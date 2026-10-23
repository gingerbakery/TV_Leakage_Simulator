from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from leakage_simulator.geometry import RayHitBatch
from tools.perf5e_native.device_replay import CONTRACT, DeviceSelectiveReplay
from tools.perf5e_native.probe import pack_rays
from tools.perf5e_native.selective_replay import assert_exact_query
from tools.perf5e_native.transport_validation import production_preflight_gate
from verify_gpu_cuda_runtime import verify_device
from verify_perf5e_selective_boundaries import boundary_cases
from verify_perf5e_transport import device_implementation_identity, identities, sha256, validate_build, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rays", type=int, default=256)
    args = parser.parse_args()
    if args.rays < 32:
        parser.error("Require at least 32 rays")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "contract": CONTRACT, "scope": "device_intersection_adapter_not_product_resident_transport",
        "delivery": "source_checkout_existing_run_web_gpu_environment",
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "production_enabled": False, "production_coverage_proven": False,
        "performance_acceptance_eligible": False, "passed": False, "records": [], "fault_checks": [],
        "rays_per_full_query": args.rays, "device_implementation": device_implementation_identity(),
        "acceptance_gates": {"faces": "exact", "distance": "bit_exact", "repeat": "bit_exact",
                             "faults": "must_raise_before_return", "ray_hit_transfer_inside_adapter": 0},
        "execution_scope": "direct_CUDA_intersection_no_transport_compute_execution_state",
    }
    path = args.output / "report.json"
    try:
        validate_build(args.native_directory)
        report["source_before"] = identities(args.native_directory)
        report["preflight"] = verify_device()
        if not production_preflight_gate(report["preflight"]):
            raise RuntimeError("Production preflight failed")
        from numba import cuda

        producer = cuda.stream()
        for name, mesh, rays, settings in boundary_cases(args.rays):
            packed = pack_rays(rays.origins, rays.directions, rays.min_t, rays.max_t, rays.ignore_faces)
            reference, execution = mesh.intersect_rays_gpu_cuda(rays, backend="bvh")
            first = None
            with DeviceSelectiveReplay(mesh, args.native_directory, args.rays) as adapter:
                pointers = {name: array.device_ctypes_pointer.value for name, array in adapter._arrays.items()}
                canonical_calls = []
                canonical_launch = adapter._launch_bvh

                def counted_launch(*arguments):
                    canonical_calls.append(arguments[0])
                    return canonical_launch(*arguments)

                adapter._launch_bvh = counted_launch
                for repeat, count in enumerate((args.rays, args.rays, args.rays, 17, 0, args.rays)):
                    device_rays = cuda.to_device(packed[:count], stream=producer)
                    calls_before = len(canonical_calls)
                    result = adapter.query_device(device_rays)
                    observed_launches = len(canonical_calls) - calls_before
                    if observed_launches != int(count > 0):
                        raise AssertionError("Expected exactly one shared canonical pass for nonempty queries")
                    actual = RayHitBatch(result.distances.copy_to_host(), result.face_indices.copy_to_host())
                    expected = RayHitBatch(reference.t[:count], reference.face_indices[:count])
                    candidate = result.candidate.copy_to_host()
                    replay = result.replay.copy_to_host()
                    rejected = adapter._arrays["rejected"][:count].copy_to_host()
                    unchanged = np.array_equal(device_rays.copy_to_host(), packed[:count])
                    workspace_reused = pointers == {name: array.device_ctypes_pointer.value for name, array in adapter._arrays.items()}
                    artifact = args.output / f"{name}-{repeat}.npz"
                    with artifact.open("xb") as stream:
                        np.savez_compressed(stream, rays=packed[:count], candidate=candidate, replay=replay, rejected=rejected,
                                            corrected_face=actual.face_indices, corrected_distance=actual.t,
                                            reference_face=expected.face_indices, reference_distance=expected.t)
                    record = {"scene": name, "repeat": repeat, "rays": count, **result.metadata,
                              "capacity": adapter.capacity, "observed_canonical_launches": observed_launches,
                              "artifact": {"path": str(artifact.resolve()), "sha256": sha256(artifact)},
                              "input_unchanged": unchanged, "workspace_reused": workspace_reused,
                              "producer_stream": producer.handle.value, "consumer_stream": adapter.stream.handle.value,
                              "cuda_provider": execution.provider_contract, "cuda_device": execution.device_name}
                    report["records"].append(record)
                    assert_exact_query(actual, expected)
                    if count == args.rays:
                        current = actual.t.tobytes(), actual.face_indices.tobytes(), replay.tobytes(), candidate.tobytes()
                        if first is None:
                            first = current
                        if first != current:
                            raise AssertionError("Repeated device query changed")
                    if not unchanged or not workspace_reused or result.metadata["hit_download_bytes"] or result.metadata["ray_upload_bytes"]:
                        raise AssertionError("Device residency or ownership gate failed")
                    write_json(path, report)
                if name == "gap_1_far_first":
                    original = adapter._probe.query_device

                    @cuda.jit
                    def corrupt(candidate, kind):
                        row = cuda.grid(1)
                        if row < len(candidate):
                            if kind == 0:
                                candidate[row].face = 0
                                candidate[row].distance = 6.0
                                candidate[row].flags = 0
                            elif kind == 1:
                                candidate[row].flags = 8
                            else:
                                candidate[row].face = -1
                                candidate[row].distance = np.inf
                                candidate[row].flags = 0

                    for kind, label in enumerate(("wrong_unflagged_far_hit", "unknown_flags", "all_candidates_missing")):
                        def injected(packed, hits, *, stream_handle):
                            timings = original(packed, hits, stream_handle=stream_handle)
                            corrupt[(len(hits) + 127) // 128, 128, adapter.stream](hits, kind)
                            return timings

                        adapter._probe.query_device = injected
                        failed = False
                        try:
                            corrected = adapter.query_device(cuda.to_device(packed, stream=producer))
                        except RuntimeError as error:
                            failed = True
                            report["fault_checks"].append({"case": label, "raised": True, "error": str(error)})
                        if kind < 2 and not failed:
                            raise AssertionError("Corrupt candidate escaped certification")
                        if kind == 2:
                            if failed:
                                raise AssertionError("Missing candidates should replay, not lose rays")
                            assert_exact_query(RayHitBatch(corrected.distances.copy_to_host(), corrected.face_indices.copy_to_host()), reference)
                            if corrected.metadata["replay_rows"] != args.rays:
                                raise AssertionError("Missing candidate did not replay")
                            report["fault_checks"].append({"case": label, "all_replayed_exact": True})
                    adapter._probe.query_device = original
                    launch = adapter._launch_bvh
                    from numba.cuda import libdevice

                    @cuda.jit
                    def corrupt_canonical(distances, overflow, replay, count, kind):
                        row = cuda.grid(1)
                        if row < count:
                            if kind == 0 and not replay[row]:
                                distances[row] = libdevice.longlong_as_double(libdevice.double_as_longlong(distances[row]) + 1)
                            elif kind == 1:
                                overflow[row] = 1

                    for kind, label in enumerate(("winner_distance_bit_corruption", "canonical_stack_overflow")):
                        def injected_launch(count, maximum, distances, faces, overflow):
                            launch(count, maximum, distances, faces, overflow)
                            corrupt_canonical[(count + 127) // 128, 128, adapter.stream](
                                distances, overflow, adapter._arrays["replay"], count, kind,
                            )

                        adapter._launch_bvh = injected_launch
                        try:
                            adapter.query_device(cuda.to_device(packed, stream=producer))
                        except RuntimeError as error:
                            if "failed closed" not in str(error):
                                raise
                            report["fault_checks"].append({"case": label, "raised": True, "error": str(error)})
                        else:
                            raise AssertionError("Corrupt canonical result escaped certification")
                        finally:
                            adapter._launch_bvh = launch
                    synchronized = []
                    synchronize = adapter.stream.synchronize

                    def record_synchronize():
                        synchronize()
                        synchronized.append(True)

                    def failed_launch(*arguments):
                        raise RuntimeError("Injected launch failure after classification")

                    adapter.stream.synchronize = record_synchronize
                    adapter._launch_bvh = failed_launch
                    try:
                        adapter.query_device(cuda.to_device(packed, stream=producer))
                    except RuntimeError as error:
                        if "Injected launch failure" not in str(error) or not synchronized:
                            raise
                    else:
                        raise AssertionError("Injected launch failure was swallowed")
                    finally:
                        adapter._launch_bvh = launch
                        adapter.stream.synchronize = synchronize
                    report["fault_checks"].append({"case": "launch_failure_cleanup", "synchronized_before_raise": True})
                    recovered = adapter.query_device(cuda.to_device(packed, stream=producer))
                    assert_exact_query(RayHitBatch(recovered.distances.copy_to_host(), recovered.face_indices.copy_to_host()), reference)
                    report["fault_checks"].append({"case": "recovery_after_failure", "exact": True})
            try:
                adapter.query_device(device_rays)
            except RuntimeError as error:
                if "closed" not in str(error):
                    raise
            else:
                raise AssertionError("Closed workspace accepted a query")
            print(json.dumps({"scene": name, "passed": True}), flush=True)
        report["passed"] = len(report["records"]) == 18 * 6 and len(report["fault_checks"]) == 7
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        report["source_after"] = identities(args.native_directory)
        report["source_unchanged"] = report.get("source_before") == report["source_after"]
        report["passed"] = report["passed"] and report["source_unchanged"]
        write_json(path, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
