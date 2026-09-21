from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_optical_transport_audit import capture_payload, captured_flux, override_scene
from test_surface_distribution_audit import cdf_distance, gaussian_reference
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace
from verify_gpu_cuda_runtime import verify_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rays", type=int, default=8192)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"preflight": verify_device(), "runs": [], "ray_count": args.rays,
              "delivery_path": "source checkout; existing pinned .venv-gpu",
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "cdf_limit": math.sqrt(math.log(2 * 512 / 0.001) / (2 * args.rays)),
              "scope": "all stored paths; no display-path subsampling; independent angular CDF"}
    cases = [("lambertian", 12, 0), ("mixed", 12, 0)]
    cases += [("gaussian", sigma, angle) for sigma in (3, 12, 30) for angle in (0, 60, 89)]
    cases += [("specular", 12, angle) for angle in (0, 60, 89)]
    for backend, dispatch in (("cpu_scalar", "scalar"), ("cpu_batch", "batch"), ("gpu_cuda", "batch")):
        for model, sigma, incidence in cases:
            for seed in (42, 123, 20260921):
                angle = math.radians(incidence)
                payload = capture_payload(0.6, model, args.rays)
                emitter = payload["emitters"][0]
                emitter.update({"center": [-math.sin(angle), 0, 10-math.cos(angle)],
                                "u_axis": [math.cos(angle), 0, -math.sin(angle)], "v_axis": [0, 1, 0],
                                "width_mm": 0.001, "height_mm": 0.001, "seed": seed,
                                "aim": {"enabled": True, "mode": "sphere", "distribution": "uniform_solid_angle",
                                        "u_axis": [math.cos(angle), 0, -math.sin(angle)], "v_axis": [0, 1, 0],
                                        "sphere_upper_deg": 0, "sphere_lower_deg": 0,
                                        "sphere_beta_deg": incidence}})
                payload["optical_profiles"][1]["gaussian_sigma_deg"] = sigma
                payload["config"].update({"store_ray_paths": True, "max_stored_paths": args.rays,
                                           "compute_backend": "gpu_cuda" if backend == "gpu_cuda" else "cpu"})
                options = {"intersection_dispatch": dispatch}
                if backend == "cpu_scalar":
                    options.update(intersection_provider="python_cpu", wavefront_planner="python_cpu")
                result = run_direct_ray_trace(build_direct_trace_input(override_scene(), payload), **options)
                directions = []
                lobes = []
                for path in result.stored_paths:
                    np.testing.assert_allclose(path[0].normal, [math.sin(angle), 0, math.cos(angle)], atol=1e-12)
                    surface = next(event for event in path if event.event_type == "surface")
                    receiver = next(event for event in path if event.event_type == "receiver")
                    direction = np.array(receiver.point) - np.array(surface.point)
                    directions.append(direction / np.linalg.norm(direction))
                    lobes.append(surface.ray_kind)
                directions = np.asarray(directions)
                errors = {}
                outgoing_axis = np.array([math.sin(angle), 0, -math.cos(angle)])
                if model == "specular":
                    errors["direction_max_absolute"] = float(np.max(np.abs(directions - outgoing_axis)))
                elif model == "lambertian":
                    errors["cosine_cdf"] = cdf_distance(-directions[:, 2], lambda values: values**2)
                    azimuth = np.mod(np.arctan2(directions[:, 1], directions[:, 0]), 2*math.pi) / (2*math.pi)
                    errors["azimuth_cdf"] = cdf_distance(azimuth, lambda values: values)
                elif model == "gaussian":
                    theta = np.arccos(np.clip(directions @ outgoing_axis, -1, 1))
                    errors["theta_cdf"] = cdf_distance(theta, gaussian_reference(sigma, incidence))
                else:
                    errors["lobe_fraction"] = abs(lobes.count("gaussian") / args.rays - 0.35)
                performance = result.metrics["_performance_summary"]
                gpu_proven = backend != "gpu_cuda" or (
                    performance.get("compute_execution_state") in ("gpu_active", "gpu_mixed")
                    and performance.get("gpu_cuda_gpu_success_count", 0) > 0
                )
                limit = 1e-5 if model == "specular" else report["cdf_limit"]
                entry = {"backend": backend, "model": model, "sigma_deg": sigma,
                         "incidence_deg": incidence, "seed": seed, "samples": len(directions),
                         "flux_lm": captured_flux(result), "errors": errors, "limit": limit,
                         "passed": len(directions) == args.rays and result.receiver_hit_count == args.rays
                         and math.isclose(captured_flux(result), 0.6, abs_tol=1e-10, rel_tol=0)
                         and all(value <= limit for value in errors.values()) and gpu_proven,
                         "performance": performance}
                entry["elapsed_sec"] = result.runtime_sec
                entry["emitter_types"] = [emitter.emitter_type for emitter in result.emitters]
                report["runs"].append(entry)
                args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(f"{backend} {model} sigma={sigma} incidence={incidence} seed={seed} errors={errors} pass={entry['passed']}", flush=True)
    return any(not entry["passed"] for entry in report["runs"])


if __name__ == "__main__":
    raise SystemExit(main())
