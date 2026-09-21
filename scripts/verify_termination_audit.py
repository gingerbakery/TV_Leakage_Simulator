from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import two_bounce_input
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalProfile
from verify_gpu_cuda_runtime import verify_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"preflight": verify_device(), "runs": [],
              "delivery_path": "source checkout; existing pinned .venv-gpu",
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "scope": "two reflections R=0.6 each, independent expectation 0.36 * source lm; no LT validation"}
    cases = [(count, power, threshold, "threshold", 42)
             for count in (4096, 8192) for power in (1, 1e-6) for threshold in (0, 1e-9)]
    cases += [(count, 1e-6, 1e-9, "russian_roulette", seed)
              for count in (4096, 8192) for seed in (42, 123, 20260921)]
    cases += [(count, 1e-5, 1e-9, "threshold", 42) for count in (2048, 4096)]
    for backend in ("cpu", "gpu_cuda"):
        for count, power, threshold, termination, seed in cases:
            trace_input = two_bounce_input(2, count, min_energy=threshold,
                                           termination_mode=termination, store_paths=False)
            trace_input.optical_profiles = [OpticalProfile("mirror_a", 0.6, scatter_model="specular"),
                                            OpticalProfile("mirror_b", 0.6, scatter_model="specular")]
            trace_input.config.angle_dependent_reflectance = False
            trace_input.config.compute_backend = backend
            trace_input.config.seed = seed
            trace_input.emitters[0].seed = seed
            trace_input.emitters[0].power_lumen = power
            result = run_direct_ray_trace(trace_input)
            flux = result.metrics["observer"]["total_flux_lumen"]
            expected = 0.36 * power
            truncated_expected = expected if expected/count >= threshold else 0
            variance = max(0, (count*result.receiver_grids[0].flux_squared_lumen2 - flux*flux)/(count-1))
            standard_error = math.sqrt(variance)
            performance = result.metrics["_performance_summary"]
            gpu_proven = backend == "cpu" or (performance.get("compute_execution_state") in ("gpu_active", "gpu_mixed")
                                               and performance.get("gpu_cuda_gpu_success_count", 0) > 0)
            if termination == "threshold":
                policy_passed = math.isclose(flux, truncated_expected, rel_tol=1e-10, abs_tol=1e-14)
                accuracy_passed = math.isclose(flux, expected, rel_tol=1e-10, abs_tol=1e-14)
            else:
                policy_passed = standard_error > 0 and abs(flux-expected) <= 6*standard_error
                accuracy_passed = policy_passed
            entry = {"backend": backend, "ray_count": count, "source_power_lm": power,
                     "min_energy_lm": threshold, "termination_mode": termination, "seed": seed,
                     "expected_untruncated_flux_lm": expected, "flux_lm": flux,
                     "standard_error_lm": standard_error, "relative_error_percent": 100*(flux/expected-1),
                     "policy_passed": policy_passed and gpu_proven, "untruncated_accuracy_passed": accuracy_passed,
                     "elapsed_sec": result.runtime_sec, "emitter_types": ["datum_plane"],
                     "performance": performance, "reflection": result.metrics["_reflection_summary"]}
            report["runs"].append(entry)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(f"{backend} N={count} power={power} threshold={threshold} {termination} seed={seed}: {flux} expected={expected}, accurate={accuracy_passed}", flush=True)
    return any(not entry["policy_passed"] for entry in report["runs"])


if __name__ == "__main__":
    raise SystemExit(main())
