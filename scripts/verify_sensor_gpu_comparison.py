"""Actual CUDA finite-sensor physical gate; never substitutes a CPU run."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput
from leakage_simulator.sensor_comparison import ComparisonOptions, run_sensor_comparison
from leakage_simulator.types import OpticalProfile, RayTraceConfig, ReceiverSpec, EmitterAimSpec
from validate_observer_detection import plate, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = dict(scope="synthetic_finite_sensor_not_TV_calibration", delivery="source_checkout",
        gpu_execution_proven=False, passed=False, runs=[])
    try:
        for mode in ("uniform", "lambertian", "mirror", "blocked"):
            mesh = TriangleMesh()
            mirror = mode == "mirror"
            plate(mesh, 0 if mirror else 9, 20)
            if mode == "blocked":
                plate(mesh, -7, 20)
            lamp = source((0, 0, -5), 40, 100., reverse=mirror)
            lamp.aim = EmitterAimSpec(enabled=True, mode="sphere",
                distribution="uniform_solid_angle" if mode == "uniform" else "lambertian",
                sphere_upper_deg=0., sphere_lower_deg=90., sphere_beta_deg=0. if mirror else 180.)
            angle = 10 if mirror else 30
            receiver = ReceiverSpec("common", center=(0,0,-10), normal=(0,0,1), width_mm=20, height_mm=20,
                acceptance_angle_deg=angle, resolution=(1,1))
            trace = DirectRayTraceInput(mesh=mesh, emitters=[lamp], receivers=[receiver],
                optical_profiles=[OpticalProfile(profile_id="surface", reflectance=.9 if mirror else 0.,
                    scatter_model="specular" if mirror else "none")],
                config=RayTraceConfig(compute_backend="gpu_cuda", intersection_backend="bvh",
                    max_depth=1 if mirror else 0, angle_dependent_reflectance=False))
            alpha = math.radians(angle)
            expected = 0. if mode == "blocked" else 100*math.pi*400e-6*(1-math.cos(alpha) if mode == "uniform" else math.sin(alpha)**2)*(.9 if mirror else 1.)
            for repeat in range(3):
                result = run_sensor_comparison(trace, ComparisonOptions("common", batches=8,
                    forward_samples=65536, reverse_samples=1024))
                if mode == "blocked":
                    physical = result.get("forward_flux_lumen") == result.get("reverse_flux_lumen") == 0 and result["verdict"] == "inconclusive"
                else:
                    physical = (abs(result.get("forward_flux_lumen", math.inf)-expected) <= expected*.05
                        and abs(result.get("reverse_flux_lumen", math.inf)-expected) <= expected*.02)
                passed = (result.get("gpu_execution_proven", False) and result.get("complete", False) and physical
                    and (mode == "blocked" or result["verdict"] == "match"))
                report["runs"].append(dict(mode=mode, repeat=repeat+1, timing_scope="first" if repeat == 0 else "warm",
                    emitter_types=[lamp.emitter_type], expected_lumen=expected, physical_gate_passed=physical,
                    passed=passed, result=result))
                if not passed:
                    raise RuntimeError(f"finite_sensor_gpu_gate_failed:{mode}:{repeat+1}")
        report.update(passed=True, gpu_execution_proven=True)
    except Exception as exc:
        report["error"] = str(exc)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
