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

from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace
from test_emitter_both_sides import two_sided_reflection_input
from test_optical_transport_audit import clipped_override_case
from verify_gpu_cuda_runtime import verify_device


def roi_clip_input(count):
    scene, payload = clipped_override_case(count)
    payload["emitters"][0]["power_lumen"] = 1e-12
    payload["config"].update({
        "min_energy_basis": "initial_ray_fraction", "min_energy": 1e-9,
        "contribution_mode": "summary",
    })
    return build_direct_trace_input(scene, payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rays", type=int, default=8192)
    args = parser.parse_args()
    if args.rays < 8192:
        parser.error("--rays must be at least 8192 to exercise CUDA primary batches")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    preflight = verify_device()
    valid = all(preflight.get(field) is True for field in (
        "available", "strict_float64", "kernel_executed", "kernel_verified",
    )) and preflight.get("preflight_scope") == "production_ray_bvh" and preflight.get("provider_contract") == "strict_float64_bvh_v1"
    report = {
        "delivery_path": "source checkout; existing pinned .venv-gpu; no installation",
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "preflight": preflight, "preflight_passed": valid,
        "scope": "upstream two-sided source, ROI clipping, face overrides and local termination integration; not LT equivalence",
        "relative_tolerance": 1e-9, "absolute_tolerance_lumen": 1e-30,
        "runs": [], "pairs": [], "passed": False,
    }
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not valid:
        return 1
    cases = [(f"both_{emitter}_{basis}", emitter, basis)
             for emitter in ("face", "datum_plane")
             for basis in ("initial_ray_fraction", "absolute_lumen")]
    cases.append(("roi_clip_override", None, "initial_ray_fraction"))
    for label, emitter_type, basis in cases:
        reference = None
        for backend in ("cpu", "gpu_cuda"):
            scene = (two_sided_reflection_input(emitter_type, args.rays, basis)
                     if emitter_type else roi_clip_input(args.rays))
            scene.config.compute_backend = backend
            for repeat in range(3):
                result = run_direct_ray_trace(scene)
                performance = result.metrics["_performance_summary"]
                expected_hits = 0 if basis == "absolute_lumen" else args.rays
                physical_passed = result.receiver_hit_count == expected_hits
                for grid in result.receiver_grids:
                    metrics = result.metrics[grid.receiver_id]
                    reflectance = 0.6 if grid.receiver_id == "back" else 0.9
                    expected = metrics["hit_count"] * 1e-12 / args.rays * reflectance
                    physical_passed &= math.isclose(metrics["total_flux_lumen"], expected, rel_tol=1e-9, abs_tol=1e-30)
                    if expected_hits and emitter_type:
                        physical_passed &= metrics["hit_count"] > args.rays * 0.4
                device_proven = backend == "cpu" or (
                    performance.get("compute_execution_state") in ("gpu_active", "gpu_mixed")
                    and performance.get("gpu_cuda_gpu_success_count", 0) > 0
                )
                snapshot = {"hit_count": result.receiver_hit_count, "receivers": {
                    grid.receiver_id: {"hit_count": result.metrics[grid.receiver_id]["hit_count"],
                                       "flux_lumen": result.metrics[grid.receiver_id]["total_flux_lumen"],
                                       "grid": grid.flux_lumen}
                    for grid in result.receiver_grids
                }}
                if reference is None:
                    reference = snapshot
                parity_passed = snapshot["hit_count"] == reference["hit_count"]
                for name, receiver in snapshot["receivers"].items():
                    baseline = reference["receivers"][name]
                    parity_passed &= receiver["hit_count"] == baseline["hit_count"]
                    parity_passed &= math.isclose(receiver["flux_lumen"], baseline["flux_lumen"], rel_tol=1e-9, abs_tol=1e-30)
                    parity_passed &= bool(np.allclose(receiver["grid"], baseline["grid"], rtol=1e-9, atol=1e-30))
                entry = {
                    "case": label, "backend": backend, "repeat": repeat,
                    "ray_count": args.rays, "emitter_types": [item.emitter_type for item in scene.emitters],
                    "elapsed_sec": result.runtime_sec, "physical_passed": physical_passed,
                    "device_proven": device_proven, "snapshot": snapshot,
                    "performance": performance, "termination": result.metrics["_termination_summary"],
                    "passed": physical_passed and parity_passed and device_proven,
                }
                report["runs"].append(entry)
                report["pairs"].append({"case": label, "backend": backend, "repeat": repeat, "passed": parity_passed})
                args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                print(f"{label} {backend} repeat={repeat} hits={result.receiver_hit_count} pass={entry['passed']}", flush=True)
    report["passed"] = all(entry["passed"] for entry in report["runs"])
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
