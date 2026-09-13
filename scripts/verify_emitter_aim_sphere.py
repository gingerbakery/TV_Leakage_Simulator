from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.raytracer import run_direct_ray_trace
from test_emitter_aim_sphere import sphere_scene


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rays", type=int, default=8192)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "aim-sphere-gpu-verification.json")
    args = parser.parse_args()
    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    record = {**asdict(preflight), "provider_contract": gpu_cuda.PROVIDER_CONTRACT}
    ready = (preflight.available and preflight.strict_float64 and preflight.kernel_executed
             and preflight.kernel_verified and preflight.preflight_scope == "production_ray_bvh"
             and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1")
    if not ready:
        print(json.dumps({"passed": False, "preflight": record}))
        return 1
    runs = []
    for emitter_type in ("face", "datum_plane", "reference_plane"):
        for scenario in ("full", "hemisphere", "backward", "annulus", "collimated", "cone", "blocked", "reflection"):
            scene = sphere_scene(emitter_type, args.rays, scenario)
            baseline_grids = None
            baseline_counts = None
            for backend in ("cpu", "gpu_cuda"):
                scene.config.compute_backend = backend
                for repeat in range(3):
                    result = run_direct_ray_trace(scene)
                    performance = result.metrics["_performance_summary"]
                    grids = np.concatenate([np.asarray(grid.flux_lumen).ravel() for grid in result.receiver_grids])
                    counts = [result.total_rays, result.receiver_hit_count, result.surface_hit_count]
                    if baseline_grids is None:
                        baseline_grids, baseline_counts = grids, counts
                    error = float(np.max(np.abs(grids - baseline_grids)))
                    expected_flux = {"blocked": 0, "reflection": 0.8}.get(scenario, 1)
                    discrete_exact = counts == baseline_counts
                    execution_ok = backend == "cpu" or (
                        performance["compute_execution_state"] in ("gpu_active", "gpu_mixed")
                        and performance["gpu_cuda_gpu_success_count"] > 0
                        and performance["gpu_resident_wavefront_fallback_count"] == 0
                        and performance["intersection_fallback_count"] == 0
                    )
                    passed = (execution_ok and discrete_exact and error < 1e-10
                              and abs(float(grids.sum()) - expected_flux) < 1e-10
                              and performance["monte_carlo_contract"] == "cpu_gpu_deterministic_batch_v1")
                    runs.append({
                        "emitter_type": emitter_type, "scenario": scenario, "backend": backend,
                        "repeat": repeat, "passed": passed, "discrete_exact": discrete_exact,
                        "runtime_sec": result.runtime_sec, "counts": counts,
                        "flux_lumen": float(grids.sum()), "maximum_grid_error_lumen": error,
                        "performance": {key: value for key, value in performance.items()
                                        if key.startswith(("compute_execution", "gpu_cuda_", "gpu_resident_wavefront_", "intersection_fallback", "aim_"))
                                        or key in ("monte_carlo_contract", "receiver_flux_contract", "compute_backend")},
                    })
                    print(f"{emitter_type}/{scenario}/{backend}/{repeat}: {passed} {result.runtime_sec:.3f}s", flush=True)
    report = {
        "delivery": "source_checkout", "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "working_diff_sha256": hashlib.sha256(subprocess.check_output(["git", "diff", "--", "src", "frontend"], cwd=ROOT)).hexdigest(),
        "preflight": record, "rays": args.rays, "passed": all(run["passed"] for run in runs), "runs": runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
