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
from test_high_reflection_depth import high_reflection_corridor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ray-count", type=int, default=4096)
    args = parser.parse_args()
    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    preflight_record = {
        **asdict(preflight),
        "provider_contract": gpu_cuda.PROVIDER_CONTRACT,
    }
    if not (
        preflight.available
        and preflight.strict_float64
        and preflight.kernel_executed
        and preflight.kernel_verified
        and preflight.preflight_scope == "production_ray_bvh"
        and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1"
    ):
        print(json.dumps({"passed": False, "preflight": preflight_record}))
        return 1
    rows = []
    passed = True
    for required_depth in (100, 1000):
        trace_input = high_reflection_corridor(
            required_depth, required_depth, ray_count=args.ray_count
        )
        reference_grid = None
        for backend in ("cpu", "gpu_cuda"):
            trace_input.config.compute_backend = backend
            for repeat in range(3):
                result = run_direct_ray_trace(trace_input)
                performance = result.metrics["_performance_summary"]
                flux = result.metrics["observer"]["total_flux_lumen"]
                expected_flux = 0.999**required_depth
                relative_error = abs(flux - expected_flux) / expected_flux
                if reference_grid is None:
                    reference_grid = np.asarray(result.receiver_grids[0].flux_lumen)
                grid_error = float(np.max(np.abs(
                    np.asarray(result.receiver_grids[0].flux_lumen) - reference_grid
                )))
                execution_ok = backend == "cpu" or (
                    performance["compute_execution_state"] == "gpu_active"
                    and performance["gpu_cuda_gpu_success_count"] > 0
                    and performance["gpu_resident_wavefront_fallback_count"] == 0
                )
                passed = passed and (
                    result.receiver_hit_count == args.ray_count
                    and relative_error < 1e-10
                    and grid_error < 1e-10
                    and execution_ok
                )
                rows.append({
                    "required_reflections": required_depth,
                    "backend": backend,
                    "repeat": repeat,
                    "runtime_sec": result.runtime_sec,
                    "receiver_hits": result.receiver_hit_count,
                    "surface_hits": result.surface_hit_count,
                    "flux_lumen": flux,
                    "expected_flux_lumen": expected_flux,
                    "relative_flux_error": relative_error,
                    "maximum_grid_absolute_error_lumen": grid_error,
                    "max_observed_depth": result.metrics["_reflection_summary"]["max_observed_depth"],
                    "performance": {
                        key: value for key, value in performance.items()
                        if key.startswith(("compute_execution", "gpu_cuda_", "gpu_resident_wavefront_"))
                        or key in (
                            "intersection_batch_size",
                            "requested_intersection_batch_size",
                            "wavefront_batch_event_slot_limit",
                            "wavefront_batch_memory_limited",
                            "receiver_flux_contract",
                        )
                    },
                })
    print(json.dumps({
        "passed": passed,
        "delivery": "source_checkout",
        "preflight": preflight_record,
        "emitter_type": "datum_plane",
        "ray_count": args.ray_count,
        "reflectance": 0.999,
        "angle_dependent_reflectance": False,
        "store_ray_paths": False,
        "runs": rows,
    }, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
