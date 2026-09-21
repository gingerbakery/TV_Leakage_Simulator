from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalProfile
from test_high_reflection_depth import high_reflection_corridor
from test_termination_policy import termination_scene
from verify_gpu_cuda_runtime import verify_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    preflight = verify_device()
    if not all(preflight.get(field) is True for field in ("available", "strict_float64", "kernel_executed", "kernel_verified")) or preflight.get("preflight_scope") != "production_ray_bvh" or preflight.get("provider_contract") != "strict_float64_bvh_v1":
        raise RuntimeError(f"Production GPU preflight failed: {preflight}")
    report = {"preflight": preflight, "delivery_path": "source checkout, existing pinned .venv-gpu; no installation",
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "tolerance": "deterministic relative 1e-9; roulette 4.5 analytic standard errors, 32 independent seeds",
              "runs": [], "roulette_groups": [], "paired_device_checks": []}

    def record(scene, case, expected, policy_expected=None, runtime=None, **metadata):
        result = run_direct_ray_trace(scene, **(runtime or {}))
        flux = result.metrics["observer"]["total_flux_lumen"]
        performance = result.metrics["_performance_summary"]
        gpu = scene.config.compute_backend == "gpu_cuda"
        proven = not gpu or (performance.get("compute_execution_state") in ("gpu_active", "gpu_mixed") and performance.get("gpu_cuda_gpu_success_count", 0) > 0)
        accuracy = None if expected is None else math.isclose(flux, expected, rel_tol=1e-9, abs_tol=1e-30)
        policy = policy_expected is None or math.isclose(flux, policy_expected, rel_tol=1e-9, abs_tol=1e-30)
        entry = {"case": case, "backend": scene.config.compute_backend, "ray_count": scene.emitters[0].ray_count,
                 "source_power_lumen": scene.emitters[0].power_lumen, "min_energy_basis": scene.config.min_energy_basis,
                 "min_energy": scene.config.min_energy, "termination_mode": scene.config.termination_mode,
                 "max_depth": scene.config.max_depth, "seed": scene.emitters[0].seed,
                 "expected_untruncated_flux_lumen": expected, "flux_lumen": flux,
                 "accuracy_passed": accuracy, "policy_passed": policy, "device_proven": proven,
                 "elapsed_sec": result.runtime_sec, "emitter_types": [entry.emitter_type for entry in scene.emitters],
                 "termination": result.metrics["_termination_summary"], "reflection": result.metrics["_reflection_summary"],
                 "performance": performance, **metadata}
        report["runs"].append(entry)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"{entry['backend']} {case} N={entry['ray_count']} flux={flux:.10g} policy={policy} gpu={proven}", flush=True)
        return entry

    for backend in ("cpu", "gpu_cuda"):
        warm_scene = termination_scene(8192, 1e-5)
        warm_scene.config.compute_backend = backend
        warm_scene.config.contribution_mode = "summary"
        for repetition in range(3):
            record(warm_scene, "first_and_warm", 3.6e-6, 3.6e-6, repetition=repetition)
        for count in (2048, 4096, 8192):
            for power in (1.0, 1e-5, 1e-12):
                for basis, threshold in (("absolute_lumen", 1e-9), ("initial_ray_fraction", 1e-9), ("initial_ray_fraction", 0)):
                    scene = termination_scene(count, power, basis, threshold)
                    scene.config.compute_backend = backend
                    scene.config.contribution_mode = "summary"
                    effective_threshold = threshold if basis == "absolute_lumen" else threshold * power / count
                    expected = 0.36 * power
                    policy_expected = expected if expected / count >= effective_threshold else 0
                    record(scene, "power_ray_sweep", expected, policy_expected)
        for reflectance, bounces, depths in ((0.6, 20, (0, 10, 20, 50)), (0.95, 100, (20, 50, 100, 300)), (0.999, 300, (20, 100, 300, 1000)), (0.999, 1000, (1000,))):
            for depth in depths:
                scene = high_reflection_corridor(bounces, depth, ray_count=256, reflectance=reflectance, backend=backend)
                scene.config.min_energy_basis = "initial_ray_fraction"
                scene.config.min_energy = 1e-9
                expected = reflectance ** bounces
                entry = record(scene, "depth_sweep", expected, expected if depth >= bounces else 0, reflectance=reflectance, required_bounces=bounces)
                expected_loss = reflectance ** (depth + 1) if depth < bounces else 0.0
                entry["loss_ledger_passed"] = math.isclose(entry["termination"]["unpropagated_surface_flux_lumen"], expected_loss, rel_tol=1e-8, abs_tol=1e-9)
        for batch in (128, 2048, 8192):
            scene = termination_scene(8192, 1e-12)
            scene.config.compute_backend = backend
            scene.config.contribution_mode = "summary"
            record(scene, "batch_partition", 3.6e-13, 3.6e-13, runtime={"intersection_batch_size": batch}, batch=batch)
        for termination in ("threshold", "russian_roulette"):
            for contribution in ("summary", "detailed"):
                scene = termination_scene(256, 1.0, threshold=0.5 if termination == "threshold" else 0.8)
                scene.config.compute_backend = backend
                scene.config.termination_mode = termination
                scene.config.contribution_mode = contribution
                scene.config.bounce_sampling_strategy = "receiver_mis"
                scene.optical_profiles = [OpticalProfile(name, 0.6, scatter_model="lambertian") for name in ("mirror_a", "mirror_b")]
                entry = record(scene, "zero_weight_mis_termination", None, contribution=contribution)
                entry["policy_passed"] = entry["performance"]["bounce_sampling_zero_weight_count"] > 0 and entry["termination"]["unpropagated_surface_flux_lumen"] is None
        for basis, power, threshold in (("initial_ray_fraction", 1e-6, 0.5), ("absolute_lumen", 1e-6, 1e-9)):
            group = []
            for index in range(32):
                scene = termination_scene(4096, power, basis, threshold)
                scene.config.compute_backend = backend
                scene.config.contribution_mode = "summary"
                scene.config.termination_mode = "russian_roulette"
                scene.emitters[0].seed = 42 + index * 1000003
                group.append(record(scene, "roulette", power * 0.36, seed_index=index))
            expected = power * 0.36
            packet = threshold if basis == "absolute_lumen" else threshold * power / 4096
            analytic_se = math.sqrt((expected * packet - expected ** 2 / 4096) / len(group))
            mean = statistics.mean(entry["flux_lumen"] for entry in group)
            report["roulette_groups"].append({"backend": backend, "basis": basis, "mean_flux_lumen": mean,
                "expected_flux_lumen": expected, "analytic_mean_standard_error_lumen": analytic_se,
                "z_score": (mean - expected) / analytic_se, "passed": abs(mean - expected) <= 4.5 * analytic_se,
                "seeds": len(group), "relative_mean_error_percent": 100 * (mean / expected - 1)})
    cpu_runs = [entry for entry in report["runs"] if entry["backend"] == "cpu"]
    gpu_runs = [entry for entry in report["runs"] if entry["backend"] == "gpu_cuda"]
    for cpu, gpu in zip(cpu_runs, gpu_runs, strict=True):
        report["paired_device_checks"].append({"case": cpu["case"], "passed": math.isclose(cpu["flux_lumen"], gpu["flux_lumen"], rel_tol=1e-9, abs_tol=1e-30) and cpu["reflection"]["reflection_receiver_hit_count"] == gpu["reflection"]["reflection_receiver_hit_count"]})
    report["passed"] = (all(entry["policy_passed"] and entry["device_proven"] and entry.get("loss_ledger_passed", True) for entry in report["runs"])
        and all(entry["passed"] for entry in report["roulette_groups"] + report["paired_device_checks"]))
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return not report["passed"]


if __name__ == "__main__":
    raise SystemExit(main())
