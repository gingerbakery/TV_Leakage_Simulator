from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "samples"))

from generate_rt_validation_models import DEFAULT_OUTPUT
from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.raytracer import run_direct_ray_trace


def lambertian_square_probability(half_size, distance, source_size=0.01):
    nodes, weights = np.polynomial.legendre.leggauss(48)
    source_nodes, source_weights = np.polynomial.legendre.leggauss(4)
    coordinates = nodes * half_size
    area_weights = np.outer(weights, weights) * half_size**2
    probability = 0.0
    for first_node, first_weight in zip(source_nodes, source_weights):
        for second_node, second_weight in zip(source_nodes, source_weights):
            horizontal = coordinates[:, None] - first_node * source_size / 2
            vertical = coordinates[None, :] - second_node * source_size / 2
            kernel = distance**2 / (math.pi * (horizontal**2 + vertical**2 + distance**2)**2)
            probability += float(np.sum(kernel * area_weights)) * first_weight * second_weight / 4
    return float(probability)


def gaussian_front_probability(sigma_deg):
    azimuth = np.linspace(0, 2 * math.pi, 65536, endpoint=False)
    polar_limit = np.arctan(1 / np.maximum(np.abs(np.cos(azimuth)), np.abs(np.sin(azimuth))))
    sigma = math.radians(sigma_deg)
    return float(np.mean([math.erf(float(angle) / (math.sqrt(2) * sigma)) for angle in polar_limit]))


def summary(result):
    performance = result.metrics["_performance_summary"]
    receivers = {}
    for grid, receiver in zip(result.receiver_grids, result.receivers):
        values = np.asarray(grid.flux_lumen)
        flux = float(values.sum())
        variance = max(0.0, (result.total_rays * grid.flux_squared_lumen2 - flux**2) / max(1, result.total_rays - 1))
        receivers[grid.receiver_id] = {
            "flux_lumen": flux, "flux_standard_error_lumen": math.sqrt(variance),
            "mean_lux": flux * 1e6 / (receiver.width_mm * receiver.height_mm),
            "peak_cell_lux": float(values.max()) * 1e6 / grid.bin_area_mm2,
            "hits": grid.hit_count, "mean_hits_per_cell": grid.hit_count / values.size,
        }
    return {
        "total_rays": result.total_rays, "receiver_hits": result.receiver_hit_count,
        "surface_hits": result.surface_hit_count, "runtime_sec": result.runtime_sec,
        "total_receiver_flux_lumen": sum(item["flux_lumen"] for item in receivers.values()),
        "receivers": receivers, "reflection": result.metrics.get("_reflection_summary", {}),
        "performance": {key: value for key, value in performance.items()
                        if key.startswith(("compute_", "gpu_", "intersection_fallback", "intersection_batch", "wavefront_batch"))
                        or key in ("monte_carlo_contract", "receiver_flux_contract")},
    }


def vector(result):
    return np.concatenate([np.asarray(grid.flux_lumen).ravel() for grid in result.receiver_grids])


def compare(reference, result):
    difference = np.abs(vector(reference) - vector(result))
    discrete = (reference.total_rays, reference.receiver_hit_count, reference.surface_hit_count) == (
        result.total_rays, result.receiver_hit_count, result.surface_hit_count)
    return {"passed": discrete and bool(np.allclose(vector(reference), vector(result), rtol=1e-9, atol=1e-13)),
            "discrete_exact": discrete, "maximum_grid_error_lumen": float(difference.max())}


def physical_checks(case, result):
    record = summary(result)
    total = record["total_receiver_flux_lumen"]
    expected = case.get("expected")
    checks = [{"name": "finite_nonnegative_and_total_flux_bounded",
               "passed": bool(np.all(np.isfinite(vector(result))) and np.all(vector(result) >= 0) and total <= 1 + 1e-10)}]
    if not expected:
        return checks
    if expected["kind"] in ("exact", "source_capture"):
        checks.append({"name": "analytic_flux", "expected_lumen": expected["flux_lumen"],
                       "actual_lumen": total, "passed": abs(total - expected["flux_lumen"]) < 1e-10})
    if expected["kind"] == "exact":
        checks.append({"name": "exact_bounce_count", "passed": result.surface_hit_count == result.total_rays * expected["reflections"]
                       and result.receiver_hit_count == result.total_rays})
    probability_checks = []
    if expected["kind"] == "lambertian_quadrature":
        probability_checks.append(("observer", lambertian_square_probability(5, 10), 0.8))
    if expected["kind"] == "source_capture":
        variant = expected["variant"]
        if variant in ("isotropic", "sphere_full"):
            probability_checks.extend((receiver.receiver_id, 1 / 6, 1) for receiver in result.receivers)
        elif variant == "lambertian":
            probability_checks.extend((("positive_Z", lambertian_square_probability(19, 19), 1), ("negative_Z", 0, 1)))
        elif variant.startswith("gaussian"):
            probability_checks.extend((("positive_Z", gaussian_front_probability(case["emitters"][0]["gaussian_sigma_deg"]), 1),
                                       ("negative_Z", 0, 1)))
        elif variant == "sphere_hemisphere":
            probability_checks.append(("negative_Z", 0, 1))
        elif variant == "sphere_backward":
            probability_checks.append(("negative_Z", 1, 1))
        else:
            probability_checks.append(("positive_Z", 1, 1))
    for receiver_id, probability, reflectance in probability_checks:
        expected_flux = probability * reflectance
        tolerance = 6 * reflectance * math.sqrt(probability * (1 - probability) / result.total_rays) + 1e-6
        actual = record["receivers"][receiver_id]["flux_lumen"]
        checks.append({"name": "distribution_" + receiver_id, "expected_lumen": expected_flux,
                       "actual_lumen": actual, "tolerance_lumen": tolerance,
                       "passed": bool(abs(actual - expected_flux) <= tolerance)})
    return checks


def request_for(case, scene, ray_count, backend, seed=20260920, depth=None):
    components = {item["object_name"]: item["object_id"] for item in scene["objects"]}
    if set(components) != set(case["component_profiles"]):
        raise ValueError(f"STEP component names do not match: {case['id']} {components}")
    request = copy.deepcopy({key: case[key] for key in ("emitters", "receivers", "optical_profiles", "config")})
    request["scene_token"] = scene["metadata"]["scene_token"]
    request["project_name"] = case["id"]
    request["config"].update(ray_count=ray_count, seed=seed, compute_backend=backend)
    if depth is not None:
        request["config"]["max_depth"] = depth
    for emitter in request["emitters"]:
        emitter.update(ray_count=ray_count, seed=seed)
    request["optical_assignments"] = [
        {"assignment_id": "part_" + name, "target_type": "part", "component_id": components[name],
         "profile_id": profile_id}
        for name, profile_id in case["component_profiles"].items()
    ]
    return request


def cuda_execution_passed(result):
    performance = result.metrics["_performance_summary"]
    return (performance.get("compute_execution_state") in ("gpu_active", "gpu_mixed")
            and performance.get("gpu_cuda_gpu_success_count", 0) > 0
            and performance.get("gpu_resident_wavefront_fallback_count", 0) == 0
            and performance.get("intersection_fallback_count", 0) == 0
            and performance.get("monte_carlo_contract") == "cpu_gpu_deterministic_batch_v1")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kit", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rays", type=int, default=8192)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--convergence", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case-prefix", default="")
    arguments = parser.parse_args()
    manifest = json.loads((arguments.kit / "models_manifest.json").read_text(encoding="utf-8"))
    results_dir = arguments.kit / "results"
    results_dir.mkdir(exist_ok=True)
    preflight_record = None
    if arguments.gpu:
        preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
        preflight_record = {**asdict(preflight), "provider_contract": gpu_cuda.PROVIDER_CONTRACT}
        if not (preflight.available and preflight.strict_float64 and preflight.kernel_executed
                and preflight.kernel_verified and preflight.preflight_scope == "production_ray_bvh"
                and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1"):
            (results_dir / "preflight_failed.json").write_text(json.dumps(preflight_record, indent=2), encoding="utf-8")
            raise SystemExit("GPU preflight failed; no CPU substitution.")
    report = {"delivery": "source_checkout", "base_commit": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "preflight": preflight_record, "ray_count": arguments.rays,
              "emitter_types": ["datum_plane"], "cases": [], "convergence": [],
              "limits": ["No LT result comparison yet", "No company TV geometry used", "Not an exhaustive qualification"]}
    report_path = results_dir / ("verification" + ("_" + arguments.case_prefix if arguments.case_prefix else "") + ".json")
    if arguments.resume and report_path.exists():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        if previous["base_commit"] != report["base_commit"] or previous["ray_count"] != arguments.rays:
            raise ValueError("Resume requires the same commit and ray count")
        if bool(previous["preflight"]) != arguments.gpu:
            raise ValueError("Resume requires the same CPU/GPU verification mode")
        report = previous
        report.setdefault("resume_preflight", []).append(preflight_record)

    def checkpoint():
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="bitsam_rt_kit_") as temporary:
        runtime = ApiRuntime(Path(temporary))
        for case in manifest["cases"]:
            if not case["id"].startswith(arguments.case_prefix):
                continue
            path = arguments.kit / case["model"]
            model_record = next(item for item in manifest["models"] if item["file"] == case["model"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != model_record["sha256"]:
                raise ValueError("STEP hash mismatch: " + str(path))
            existing = next((item for item in report["cases"] if item["id"] == case["id"]), None)
            if existing and existing["passed"] and existing["step_sha256"] == model_record["sha256"]:
                print(f"Resume verified case: {case['id']}", flush=True)
                continue
            report["cases"] = [item for item in report["cases"] if item["id"] != case["id"]]
            import_started = time.perf_counter()
            scene = runtime.load_scene(str(path.resolve()))
            record = {"id": case["id"], "model": case["model"], "step_sha256": model_record["sha256"],
                      "import_sec": time.perf_counter() - import_started, "component_count": len(scene["objects"]),
                      "runs": [], "additional_checks": []}
            reference = None
            for backend in (("cpu", "gpu_cuda") if arguments.gpu else ("cpu",)):
                request = request_for(case, scene, arguments.rays, backend)
                trace = runtime._build_trace_input_for_request(runtime._scene_mesh_for_request(request), request)
                record["trace_face_count"] = len(trace.mesh.faces)
                for repeat in range(3):
                    result = run_direct_ray_trace(trace)
                    if reference is None:
                        reference = result
                        (results_dir / (case["id"] + "_cpu_result.json")).write_text(json.dumps(result.to_dict()), encoding="utf-8")
                        (results_dir / (case["id"] + "_request.json")).write_text(json.dumps(request, indent=2), encoding="utf-8")
                    parity = compare(reference, result)
                    checks = physical_checks(case, result)
                    execution = backend == "cpu" or cuda_execution_passed(result)
                    passed = parity["passed"] and all(item["passed"] for item in checks) and execution
                    record["runs"].append({"backend": backend, "repeat": repeat, "passed": passed,
                                           "gpu_execution_proven": execution if backend == "gpu_cuda" else None,
                                           "parity": parity, "checks": checks, **summary(result)})
                    print(f"{case['id']} {backend} #{repeat}: {passed} flux={summary(result)['total_receiver_flux_lumen']:.12g} ({result.runtime_sec:.3f}s)", flush=True)
            request = request_for(case, scene, arguments.rays, "cpu")
            trace = runtime._build_trace_input_for_request(runtime._scene_mesh_for_request(request), request)
            split = run_direct_ray_trace(trace, intersection_batch_size=257)
            record["additional_checks"].append({"name": "batch_257_parity", **compare(reference, split)})
            if case.get("expected", {}) and case["expected"]["kind"] == "exact":
                trace.config.max_depth = case["expected"]["reflections"] - 1
                limited = run_direct_ray_trace(trace)
                record["additional_checks"].append({"name": "depth_below_required_blocks_all", "passed": limited.receiver_hit_count == 0,
                                                    "depth_limit": trace.config.max_depth, "receiver_hits": limited.receiver_hit_count})
            record["passed"] = all(item["passed"] for item in record["runs"] + record["additional_checks"])
            report["cases"].append(record)
            checkpoint()

        if arguments.convergence:
            case = next(item for item in manifest["cases"] if item["id"] == "08_cavity_gap_1p0")
            scene = runtime.load_scene(str((arguments.kit / case["model"]).resolve()))
            schedules = [(arguments.rays, depth) for depth in (20, 50, 100, 300, 1000)]
            schedules.append((arguments.rays * 4, 1000))
            for ray_count, depth in schedules:
                for seed in (11, 22, 33, 44, 55):
                    if any(item["ray_count"] == ray_count and item["max_depth"] == depth and item["seed"] == seed
                           for item in report["convergence"]):
                        continue
                    request = request_for(case, scene, ray_count, "gpu_cuda" if arguments.gpu else "cpu", seed=seed, depth=depth)
                    trace = runtime._build_trace_input_for_request(runtime._scene_mesh_for_request(request), request)
                    result = run_direct_ray_trace(trace)
                    record = {"case": case["id"], "ray_count": ray_count, "max_depth": depth, "seed": seed,
                              "gpu_execution_proven": cuda_execution_passed(result) if arguments.gpu else None,
                              **summary(result)}
                    report["convergence"].append(record)
                    print(f"Sweep rays={ray_count} depth={depth} seed={seed}: {record['total_receiver_flux_lumen']:.9g}", flush=True)
                    checkpoint()
    report["passed"] = bool(report["cases"]) and all(case["passed"] for case in report["cases"])
    if arguments.gpu:
        report["passed"] = report["passed"] and all(item["gpu_execution_proven"] for item in report["convergence"])
    checkpoint()
    print(f"Report: {report_path} passed={report['passed']}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
