"""Real-device observer gate. Run after run_web_gpu production preflight.

Synthetic CPU/GPU sample parity and first/warm timings; not TV calibration.
Never accepts CPU fallback as a successful GPU run.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from leakage_simulator import gpu_cuda_intersection as backend
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.gpu_cuda_observer import ResidentObserver, reference_chunk, CONTRACT, SAMPLE_CONTRACT
from leakage_simulator.observer_probe import _source_geometry, sampling_plan
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.types import OpticalProfile, RayTraceConfig
from validate_observer_detection import plate, source


def run_case(name, rays, repeats):
    mesh = TriangleMesh()
    mirror = name in ("mirror", "direct_mirror")
    reflected = name in ("mirror", "direct_mirror", "diffuse", "gaussian", "mixed")
    plate(mesh, -5 if name.startswith("coplanar") else 0 if reflected else 9, 20)
    if name in ("blocked", "coplanar_blocked"):
        plate(mesh, -7, 20)
    profile = OpticalProfile(profile_id="black", reflectance=.9 if reflected else 0.,
        scatter_model="specular" if mirror else "lambertian" if name == "diffuse" else name if reflected else "none",
        gaussian_sigma_deg=18., specular_ratio=.3)
    trace = SimpleNamespace(mesh=mesh, emitters=[source((0, 0, -5), 40, 100., reverse=reflected)],
        config=RayTraceConfig(max_depth=4, seed=42, angle_dependent_reflectance=False, compute_backend="gpu_cuda"),
        observer_probe=dict(measurement="radiance", horizontal_deg=0., vertical_deg=0., range_deg=0., step_deg=1.,
            resolution=64, samples_per_cell=2, coverage="local", search_mode="preview"))
    sources = _source_geometry(mesh, trace.emitters)
    if name == "direct_mirror":
        trace.emitters.append(source((0, 0, -4), 40, 1., reverse=False))
        sources = _source_geometry(mesh, trace.emitters)
    resolved = OpticalPropertyResolver(mesh, [profile], []).resolve_all()
    setup_started = time.perf_counter()
    session = ResidentObserver(trace, sources, resolved)
    setup_sec = time.perf_counter()-setup_started
    plan = sampling_plan(trace.observer_probe, (-10, -10, -10), (10, 10, 10), "neg_z", 0)[0]
    rays = min(rays, plan["grid_samples"])
    cpu_started = time.perf_counter()
    expected, expected_paths = reference_chunk(trace, session.host, plan, (0, 0, 0), 0, 0, rays)
    cpu_sec = time.perf_counter()-cpu_started
    timings, passed, successes, reason = [], True, 0, None
    max_error = 0.
    for _ in range(repeats):
        began = time.perf_counter()
        results = []
        for start in range(0, rays, session.batch_size(8192)):
            completed, execution, _ = session.run(plan, (0, 0, 0), 0, start, min(session.batch_size(8192), rays-start))
            if getattr(execution, "cpu_fallback", False):
                passed, reason = False, execution.reason
            else:
                successes += 1
            results.extend(completed)
        timings.append(time.perf_counter()-began)
        for i, item in enumerate(results):
            index, value, path, depth, _, best = item[:6]
            max_error = max(max_error, abs(value-expected[i, 0]))
            if index != i or depth != int(expected[i, 2]) or (len(path) if path else 0) != int(expected[i, 3]):
                passed = False
            if not np.allclose([value, best], expected[i, :2], rtol=1e-9, atol=1e-10):
                passed = False
            if not np.isclose(item[6], expected[i, 7], rtol=1e-9, atol=1e-10):
                passed = False
            if path and not np.allclose(path, expected_paths[i, :len(path)], rtol=1e-9, atol=1e-8):
                passed = False
        analytic = {"direct": 100., "coplanar": 100., "mirror": 90., "direct_mirror": 91., "blocked": 0., "coplanar_blocked": 0.}.get(name)
        if analytic is not None and not np.allclose([item[1] for item in results], analytic, rtol=1e-9, atol=1e-10):
            passed = False
    return dict(case=name, rays=rays, emitter_types=[e.emitter_type for e in trace.emitters],
        passed=passed and successes > 0 and reason is None, cuda_success_count=successes,
        cpu_fallback_reason=reason, max_luminance_error=max_error, cpu_equation_oracle_sec=cpu_sec,
        estimator_timing_sec=timings, initialization_and_optical_preflight_sec=setup_sec,
        first_run_with_initialization_sec=setup_sec+timings[0], warm_timing_sec=timings[1:],
        first_run_includes_possible_jit=True,
        timing_comparison_scope="observer_estimator_only_not_CAD_BVH_or_whole_UI")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rays", type=int, default=8192)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 32 <= args.rays <= 8192 or not 3 <= args.repeats <= 4:
        parser.error("--rays must be 32..8192; --repeats must be 3..4")
    report = dict(delivery="source_checkout", provider_contract=CONTRACT, sample_contract=SAMPLE_CONTRACT,
        physical_tv_validation=False, passed=False)
    report["commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report["tracked_dirty"] = bool(subprocess.check_output(["git", "diff", "--name-only", "HEAD"], cwd=ROOT, text=True).strip())
    report["source_sha256"] = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ("src/leakage_simulator/gpu_cuda_observer.py", "src/leakage_simulator/observer_radiance.py",
         "src/leakage_simulator/gpu_cuda_resident_wavefront.py", "src/leakage_simulator/gpu_cuda_intersection.py", "scripts/verify_observer_resident_gpu.py")}
    preflight = backend.preflight_gpu_cuda(refresh=True)
    report["preflight"] = dict(asdict(preflight), provider_contract=backend.PROVIDER_CONTRACT)
    if preflight.available and preflight.kernel_verified and preflight.strict_float64 and preflight.kernel_executed and preflight.preflight_scope == "production_ray_bvh" and backend.PROVIDER_CONTRACT == "strict_float64_bvh_v1":
        try:
            report["cases"] = [run_case(name, args.rays, args.repeats) for name in ("direct", "coplanar", "coplanar_blocked", "mirror", "direct_mirror", "blocked", "diffuse", "gaussian", "mixed")]
            report["passed"] = all(c["passed"] for c in report["cases"])
        except Exception as exc:
            report["error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
