from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from benchmark_perf4a_target_workloads import WORKLOADS
from benchmark_perf5_cpu import cad_case
from benchmark_perf5d_bvh_preparation import saved_case
from verify_gpu_cuda_runtime import verify_device
from tools.perf5e_native.transport_validation import (
    CONTRACT, OptixTransportObserver, production_preflight_gate,
    run_diagnostic_transport, semantic_payload, observe_dispatch,
)
from tools.perf5e_native.selective_replay import (
    CONTRACT as SELECTIVE_CONTRACT, SelectiveReplayObserver, selective_dispatch,
)
from tools.perf5e_native.device_transport import DeviceReplayObserver
from tools.perf5e_native.device_replay import CONTRACT as DEVICE_CONTRACT


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identities(native_directory):
    paths = set()
    for folder in ("src", "scripts", "tests"):
        paths.update((ROOT / folder).rglob("*.py"))
    paths.update(path for path in (ROOT / "tools/perf5e_native").iterdir() if path.is_file())
    paths.update(ROOT.glob("requirements*.txt"))
    paths.update(native_directory / name for name in ("optix_probe.dll", "optix_probe.ptx", "build-manifest.json"))
    return {str(path.resolve()): sha256(path) for path in sorted(paths)}


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def device_implementation_identity():
    return {"contract": DEVICE_CONTRACT,
            "adapter_sha256": sha256(ROOT / "tools/perf5e_native/device_replay.py"),
            "observer_sha256": sha256(ROOT / "tools/perf5e_native/device_transport.py")}


def execution(result):
    performance = result.metrics["_performance_summary"]
    return {key: value for key, value in performance.items() if (
        key.startswith(("compute_", "gpu_cuda_", "gpu_resident_wavefront_"))
        or key in ("intersection_provider", "intersection_fallback_count", "monte_carlo_contract")
    )}


def capture_factory(directory):
    scenes = {}

    def capture(index, rays, candidate, visited, reference, scene):
        scene_key = id(scene)
        if scene_key not in scenes:
            filename = directory / f"scene-{len(scenes)}.npz"
            with filename.open("xb") as stream:
                np.savez_compressed(stream, **{name: getattr(scene, name) for name in (
                    "triangle_v0", "triangle_edge1", "triangle_edge2", "traceable_face_mask",
                )})
            scenes[scene_key] = (scene, {"path": str(filename.resolve()), "sha256": sha256(filename)})
        filename = directory / f"query-{index:05d}.npz"
        with filename.open("xb") as stream:
            np.savez_compressed(stream, rays=rays, candidate=candidate, visited=visited,
                                reference_face=reference.face_indices, reference_distance=reference.t)
        return {"path": str(filename.resolve()), "sha256": sha256(filename), "scene": scenes[scene_key][1]}

    return capture


def validate_build(native_directory):
    manifest = json.loads((native_directory / "build-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("production_enabled") is not False or manifest.get("test_faults_enabled") is not False:
        raise ValueError("Require an experimental normal build, not a fault-injection build")
    for artifact in manifest["artifacts"]:
        actual = native_directory / Path(artifact["path"]).name
        if sha256(actual) != artifact["sha256"].lower():
            raise ValueError("Native artifact hash changed")
    for source in manifest["sources"]:
        actual = ROOT / "tools/perf5e_native" / Path(source["path"]).name
        if sha256(actual) != source["sha256"].lower():
            raise ValueError("Native source/build identity changed")


def selective_capture_factory(directory):
    def capture(index, rays, candidate, corrected, replay, rejected, reference, backend):
        filename = directory / f"selective-{index:05d}.npz"
        with filename.open("xb") as stream:
            np.savez_compressed(stream, rays=rays, candidate=candidate,
                                corrected_face=corrected.face_indices, corrected_distance=corrected.t,
                                replay=replay, rejected=rejected, reference_face=reference.face_indices,
                                reference_distance=reference.t, backend=np.array(backend))
        return {"path": str(filename.resolve()), "sha256": sha256(filename)}
    return capture


def main():
    parser = argparse.ArgumentParser(description="Isolated OptiX observation of canonical multi-bounce transport; no production promotion")
    parser.add_argument("--native-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rays", type=int, default=8193)
    parser.add_argument("--warm-runs", type=int, default=3)
    parser.add_argument("--saved-case", type=Path, action="append", default=[])
    parser.add_argument("--skip-cad", action="store_true")
    parser.add_argument("--selective-replay", action="store_true")
    parser.add_argument("--device-replay", action="store_true")
    args = parser.parse_args()
    args.selective_replay = args.selective_replay or args.device_replay
    if args.rays < 8193 or args.warm_runs < 2:
        parser.error("Require at least 8193 rays and two warm runs")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "contract": SELECTIVE_CONTRACT if args.selective_replay else CONTRACT,
        "production_enabled": False, "performance_acceptance_eligible": False,
        "scope": "oracle_checked_selective_host_roundtrip_not_fused_provider" if args.selective_replay
        else "canonical_host_roundtrip_transport_with_optix_shadow_not_fused_provider",
        "production_coverage_proven": False,
        "acceptance_gates": {"face_id": "exact", "distance": "bit_exact" if args.selective_replay else "canonical_unchanged",
                             "transport_semantics": "exact", "candidate_promotion": False},
        "delivery": "source_checkout_existing_run_web_gpu_environment",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "records": [], "passed": False,
        "original_projects": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in args.saved_case],
    }
    report_path = args.output / "report.json"
    if args.device_replay:
        report["contract"] = DEVICE_CONTRACT
        report["scope"] = "device_query_inside_oracle_checked_host_roundtrip_not_fused_transport"
        report["device_implementation"] = device_implementation_identity()
    try:
        validate_build(args.native_directory)
        report["source_before"] = identities(args.native_directory)
        report["preflight"] = verify_device()
        if not production_preflight_gate(report["preflight"]):
            raise RuntimeError("Production preflight failed")
        write_json(report_path, report)
        scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items()]
        if not args.skip_cad:
            scenes.append(("tv_corner", cad_case(args.rays)[0]))
        scenes.extend((f"saved_{index}", saved_case(path, args.rays)[0]) for index, path in enumerate(args.saved_case))
        for name, trace_input in scenes:
            trace_input.config.store_ray_paths = True
            trace_input.config.max_stored_paths = 32
            for backend in ("cpu", "gpu_cuda"):
                trace_input.config.compute_backend = backend
                first_payload = None
                for repeat in range(args.warm_runs + 1):
                    directory = args.output / f"{name}-{backend}-{repeat}"
                    directory.mkdir()
                    observer_type = DeviceReplayObserver if args.device_replay else SelectiveReplayObserver
                    observer = (observer_type(
                        args.native_directory, capture=capture_factory(directory),
                        selective_capture=selective_capture_factory(directory),
                    ) if args.selective_replay else OptixTransportObserver(args.native_directory, capture=capture_factory(directory)))
                    results = {}
                    timings = {}
                    for mode in (("baseline", "shadow") if repeat % 2 == 0 else ("shadow", "baseline")):
                        started = time.perf_counter()
                        try:
                            result = run_diagnostic_transport(
                                trace_input, observer=observer if mode == "shadow" else None,
                                observation_context=selective_dispatch if args.selective_replay else observe_dispatch,
                            )
                        except Exception:
                            report["failed_observation"] = observer.summary()
                            raise
                        timings[mode] = time.perf_counter() - started
                        results[mode] = result
                    baseline = semantic_payload(results["baseline"])
                    candidate = semantic_payload(results["shadow"])
                    if first_payload is None:
                        first_payload = baseline
                    observation = observer.summary()
                    record = {
                        "scene": name, "backend": backend, "repeat": repeat, "timing_seconds": timings,
                        "emitter_types": [emitter.emitter_type for emitter in trace_input.emitters],
                        "semantic_exact": baseline == candidate,
                        "same_mode_repeat_exact": first_payload == baseline == candidate,
                        "execution": {mode: execution(result) for mode, result in results.items()},
                        "observation": observation,
                        "results": {mode: write_json(directory / f"{mode}.json", result.to_dict()) for mode, result in results.items()},
                    }
                    report["records"].append(record)
                    write_json(report_path, report)
                    if not record["semantic_exact"] or not record["same_mode_repeat_exact"]:
                        raise AssertionError("Canonical results changed under observation")
                    if observation["successes"] <= 0 or observation["attempts"] != observation["successes"]:
                        raise AssertionError("No complete OptiX observation")
                    if backend == "gpu_cuda":
                        for evidence in record["execution"].values():
                            if evidence.get("compute_execution_state") not in ("gpu_active", "gpu_mixed") or evidence.get("gpu_cuda_gpu_success_count", 0) <= 0:
                                raise AssertionError("Canonical GPU execution was not proven")
                    print(json.dumps({key: record[key] for key in ("scene", "backend", "repeat", "semantic_exact")}), flush=True)
        report["source_after"] = identities(args.native_directory)
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["original_projects_unchanged"] = all(sha256(item["path"]) == item["sha256"] for item in report["original_projects"])
        report["observed_candidate_coverage_passed"] = all(record["observation"]["observed_coverage_passed"] for record in report["records"])
        report["unflagged_candidate_mismatches"] = sum(record["observation"]["unflagged_mismatches"] for record in report["records"])
        report["passed"] = bool(report["records"]) and report["source_unchanged"] and report["original_projects_unchanged"]
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        write_json(report_path, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
