from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import two_bounce_input
from test_optical_transport_audit import capture_payload, captured_flux, override_payload, override_scene
from leakage_simulator.raytrace_bridge import build_direct_trace_input, build_prepared_trace_geometry
from leakage_simulator.raytracer import run_direct_ray_trace
from verify_gpu_cuda_runtime import verify_device


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    preflight = verify_device()
    report = {
        "delivery_path": "source checkout; existing pinned .venv-gpu; official production checker",
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "preflight": preflight, "flux_absolute_tolerance_lm": 1e-10, "runs": [],
        "scope": "analytic ROI/surface capture audit, not LT equivalence or complex-cavity certification",
    }

    def record(label, trace_input, expected, backend, **options):
        trace_input.config.compute_backend = backend
        started = time.perf_counter()
        result = run_direct_ray_trace(trace_input, **options)
        performance = result.metrics["_performance_summary"]
        flux = captured_flux(result)
        gpu_proven = backend == "cpu" or (
            performance.get("compute_execution_state") in ("gpu_active", "gpu_mixed")
            and performance.get("gpu_cuda_gpu_success_count", 0) > 0
        )
        entry = {"case": label, "backend": backend, "expected_flux_lm": expected,
                 "flux_lm": flux, "absolute_error_lm": abs(flux - expected),
                 "hit_count": result.receiver_hit_count, "ray_count": result.total_rays,
                 "elapsed_sec": time.perf_counter() - started,
                 "passed": math.isclose(flux, expected, rel_tol=0, abs_tol=1e-10) and gpu_proven,
                 "emitter_types": [emitter.emitter_type for emitter in trace_input.emitters],
                 "profiles": [profile.to_dict() for profile in trace_input.optical_profiles],
                 "optical_summary": result.metrics.get("_optical_summary"),
                 "reflection_summary": result.metrics.get("_reflection_summary"),
                 "performance": performance}
        if trace_input.config.max_depth == 1:
            potential = sum(item["potential_reflected_flux_lumen"] for item in
                            result.metrics["_optical_summary"]["profile_hits"].values())
            entry["potential_reflected_flux_lm"] = potential
            entry["passed"] = entry["passed"] and math.isclose(potential, expected, rel_tol=0, abs_tol=1e-10)
        report["runs"].append(entry)
        (args.output / "transport.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"{label} {backend}: {flux:.12g} / {expected:.12g} {performance.get('compute_execution_state')} pass={entry['passed']}", flush=True)

    for backend in ("cpu", "gpu_cuda"):
        payload = capture_payload(0.6, "lambertian", 4096)
        prepared = build_prepared_trace_geometry(override_scene(), payload)
        for repeat in range(3):
            record(f"warm_repeat_{repeat}", build_direct_trace_input(override_scene(), payload, prepared, repeat > 0), 0.6, backend)
        for model in ("specular", "lambertian", "gaussian", "mixed"):
            for reflectance in (0, 0.03, 0.14, 0.6, 0.95, 0.999, 1):
                payload = capture_payload(reflectance, model, 4096)
                record(f"capture_{model}_{reflectance}", build_direct_trace_input(override_scene(), payload), reflectance, backend)
        for mode, extras in (("full", {}), ("roi", {"roi_faces": [1]}),
                             ("excluded", {"excluded_component_ids": [7]}),
                             ("roi_excluded", {"roi_faces": [1], "excluded_component_ids": [7]})):
            payload = override_payload(4096)
            payload.update(extras)
            record(f"override_{mode}", build_direct_trace_input(override_scene(), payload), 0.9, backend)
        for first, second in ((0.6, 0.6), (0.6, 0.3)):
            trace_input = two_bounce_input(2, 4096, min_energy=0, store_paths=False)
            trace_input.config.angle_dependent_reflectance = False
            trace_input.optical_profiles[0] = replace(trace_input.optical_profiles[0], reflectance=first)
            trace_input.optical_profiles[1] = replace(trace_input.optical_profiles[1], reflectance=second)
            record(f"two_bounce_{first}_{second}", trace_input, first * second, backend)
        for reflectance in (0, 0.03, 0.6, 1):
            for incidence in (0, 60, 89):
                for roughness in (0, 1):
                    angle = math.radians(incidence)
                    payload = capture_payload(reflectance, "specular", 4096)
                    payload["emitters"][0].update({
                        "center": [-math.sin(angle), 0, 10-math.cos(angle)],
                        "width_mm": 0.001, "height_mm": 0.001,
                        "aim": {"enabled": True, "mode": "sphere", "distribution": "uniform_solid_angle",
                                "sphere_upper_deg": 0, "sphere_lower_deg": 0, "sphere_beta_deg": incidence},
                    })
                    payload["config"]["angle_dependent_reflectance"] = True
                    payload["optical_profiles"][1]["roughness"] = roughness
                    expected = reflectance + (1-reflectance) * max(0, 1-math.cos(angle)/0.7)**5 * (1-0.75*roughness)
                    if reflectance == 0:
                        expected = 0
                    record(f"incidence_{reflectance}_{incidence}_{roughness}", build_direct_trace_input(override_scene(), payload), expected, backend)
        for power in (0, 0.1, 1, 2):
            payload = capture_payload(0.6, "mixed", 4096)
            payload["emitters"][0]["power_lumen"] = power
            record(f"power_scale_{power}", build_direct_trace_input(override_scene(), payload), power*0.6, backend)
        payload = capture_payload(0.6, "mixed", 4096)
        payload["emitters"].append(copy.deepcopy(payload["emitters"][0]))
        payload["emitters"][1].update({"emitter_id": "second", "power_lumen": 2, "seed": 321})
        record("two_emitters", build_direct_trace_input(override_scene(), payload), 1.8, backend)
        if args.catalog:
            for item in json.loads(args.catalog.read_text(encoding="utf-8")):
                payload = capture_payload(item["expected_reflectance"], "mixed", 4096)
                payload["optical_profiles"] = [item["profile"]]
                payload["optical_assignments"] = [item["assignment"]]
                record(f"catalog_{item['base']}_{item['surface']}", build_direct_trace_input(override_scene(), payload), item["expected_reflectance"], backend)
    failures = [entry["case"] + ":" + entry["backend"] for entry in report["runs"] if not entry["passed"]]
    print(json.dumps({"runs": len(report["runs"]), "failures": failures}))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
