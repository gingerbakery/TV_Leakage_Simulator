from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.raytracer import run_direct_ray_trace
from test_emitter_aim import aim_scene
from test_emitter_aim_proximity import proximity_scene


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ray-count", type=int, default=8192)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "emitter-aim-verification.json")
    args = parser.parse_args()
    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    preflight_record = {**asdict(preflight), "provider_contract": gpu_cuda.PROVIDER_CONTRACT}
    ready = (
        preflight.available and preflight.strict_float64
        and preflight.kernel_executed and preflight.kernel_verified
        and preflight.preflight_scope == "production_ray_bvh"
        and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1"
    )
    if not ready:
        print(json.dumps({"passed": False, "preflight": preflight_record}))
        return 1
    rows = []
    passed = True
    for emitter_type in ("face", "datum_plane", "reference_plane"):
        for shape in ("rectangle", "circle"):
            for scenario in ("direct", "specular", "blocked", "close_parallel", "close_tilted"):
                if scenario.startswith("close_"):
                    trace_input = proximity_scene(
                        emitter_type=emitter_type, shape=shape, rays=args.ray_count,
                        tilted=scenario == "close_tilted",
                    )
                else:
                    trace_input = aim_scene(
                        emitter_type=emitter_type, shape=shape, rays=args.ray_count,
                        reflection=scenario == "specular", blocker=scenario == "blocked",
                    )
                expected_flux = {"specular": 0.8, "blocked": 0.0}.get(scenario, 1.0)
                expected_hits = 0 if scenario == "blocked" else args.ray_count
                reference = None
                for backend in ("cpu", "gpu_cuda"):
                    trace_input.config.compute_backend = backend
                    for repeat in range(3):
                        result = run_direct_ray_trace(trace_input)
                        performance = result.metrics["_performance_summary"]
                        flux = result.metrics["observer"]["total_flux_lumen"]
                        grid = np.asarray(result.receiver_grids[0].flux_lumen)
                        if reference is None:
                            reference = grid
                        grid_error = float(np.max(np.abs(grid - reference)))
                        execution_ok = backend == "cpu" or (
                            performance["compute_execution_state"] in ("gpu_active", "gpu_mixed")
                            and performance["gpu_cuda_gpu_success_count"] > 0
                            and performance["gpu_resident_wavefront_fallback_count"] == 0
                            and performance["intersection_fallback_count"] == 0
                        )
                        run_passed = (
                            result.receiver_hit_count == expected_hits
                            and abs(flux - expected_flux) < 1e-10
                            and grid_error < 1e-10 and execution_ok
                        )
                        passed = passed and run_passed
                        row = {
                            "emitter_type": emitter_type, "shape": shape, "scenario": scenario,
                            "backend": backend, "repeat": repeat, "passed": run_passed,
                            "runtime_sec": result.runtime_sec, "receiver_hits": result.receiver_hit_count,
                            "surface_hits": result.surface_hit_count, "flux_lumen": flux,
                            "expected_flux_lumen": expected_flux,
                            "maximum_grid_absolute_error_lumen": grid_error,
                            "performance": {
                                key: value for key, value in performance.items()
                                if key.startswith(("compute_execution", "gpu_cuda_", "gpu_resident_wavefront_", "aim_"))
                                or key.startswith("intersection_fallback")
                                or key in ("compute_backend", "intersection_batch_size", "receiver_flux_contract")
                            },
                        }
                        rows.append(row)
                        print(f"{emitter_type}/{shape}/{scenario}/{backend}/{repeat}: {run_passed} {result.runtime_sec:.3f}s", flush=True)
    report = {
        "passed": passed, "delivery": "source_checkout", "preflight": preflight_record,
        "ray_count": args.ray_count, "store_ray_paths": True, "max_stored_paths": 20,
        "input_flux_lumen": 1.0, "mirror_reflectance": 0.8,
        "angle_dependent_reflectance": False, "runs": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Report: {args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
