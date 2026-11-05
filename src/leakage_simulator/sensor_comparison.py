"""Independent finite-area/finite-cone flux comparison using CUDA transport.

The production scene/config are never modified. Geometry and materials are
shared immutable inputs; proposal seeds/budgets and unbiased termination are
explicit validation-local settings. A valid witness is not a flux sample.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import math
import random
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .geometry import vec_add, vec_mul, vec_norm
from .bsdf_guard import bind_bsdf_profiles
from .optics import OpticalPropertyResolver
from .observer_probe import _source_geometry
from .observer_radiance import trace_observer_radiance, validate_sources
from .reflection import orthonormal_basis

CONTRACT = "finite_sensor_forward_reverse_flux_v1"
_T95 = {4: 3.182446, 8: 2.364625, 16: 2.131450}


@dataclass(frozen=True)
class ComparisonOptions:
    receiver_id: str
    batches: int = 8
    forward_samples: int = 16384
    reverse_samples: int = 1024
    tolerance_percent: float = 5.
    absolute_tolerance_lumen: float = 0.
    seed: int = 1729

    def __post_init__(self):
        if not isinstance(self.receiver_id, str) or not self.receiver_id:
            raise ValueError("비교할 Receiver를 선택하세요.")
        for key in ("batches", "forward_samples", "reverse_samples", "seed"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"{key} must be an integer")
        if self.batches not in _T95:
            raise ValueError("독립 반복 수는 4/8/16 중 선택하세요.")
        if not 32 <= self.forward_samples <= 250000 or not 32 <= self.reverse_samples <= 100000:
            raise ValueError("반복당 순방향 32..250000, 역추적 32..100000 표본이 필요합니다.")
        if self.batches*(self.forward_samples+self.reverse_samples) > 4000000:
            raise ValueError("검증 예산은 총 4,000,000 표본 이하여야 합니다.")
        if not math.isfinite(self.tolerance_percent) or not .1 <= self.tolerance_percent <= 25:
            raise ValueError("허용 차이는 0.1..25% 범위여야 합니다.")
        if not math.isfinite(self.absolute_tolerance_lumen) or self.absolute_tolerance_lumen < 0:
            raise ValueError("절대 허용 오차는 유한한 음이 아닌 값이어야 합니다.")
        if not 0 <= self.seed < 2**31:
            raise ValueError("검증 seed 범위를 확인하세요.")


class FiniteSensor:
    def __init__(self, frame, sample_count):
        self.frame, self.sample_count = frame, sample_count
        angle = frame.receiver.acceptance_angle_deg
        if not 0 < angle <= 90:
            raise ValueError("유한 광량 비교에는 0° 초과 90° 이하의 수용각이 필요합니다.")
        self.cos_min = math.cos(math.radians(angle))
        self.solid_angle = 2*math.pi*(1-self.cos_min)
        self.area_m2 = frame.receiver.width_mm*frame.receiver.height_mm*1e-6
        self.angular_u, self.angular_v, _ = orthonormal_basis(frame.normal)

    def plans(self):
        r, f = self.frame.receiver, self.frame
        return [dict(columns=1, rows=1, grid_samples=self.sample_count, horizontal=0., vertical=0.,
            outward=tuple(-v for v in f.normal), u=f.u_axis, v=f.v_axis, distance=0., width=r.width_mm,
            height=r.height_mm, min_u=-r.width_mm/2, min_v=-r.height_mm/2)]

    def launch(self, index, rng):
        f, r = self.frame, self.frame.receiver
        point = vec_add(vec_add(r.center, vec_mul(f.u_axis, (rng.random()-.5)*r.width_mm)),
                        vec_mul(f.v_axis, (rng.random()-.5)*r.height_mm))
        cosine = 1-rng.random()*(1-self.cos_min)
        sine = math.sqrt(max(0., 1-cosine*cosine))
        phi = 2*math.pi*rng.random()
        direction = vec_norm(tuple(f.normal[i]*cosine+sine*(self.angular_u[i]*math.cos(phi)+self.angular_v[i]*math.sin(phi)) for i in range(3)))
        weight = self.area_m2*self.solid_angle*cosine
        if f.roi_boxes:
            from .roi_scope import receiver_roi_mask
            if not receiver_roi_mask([point], f.normal, f.roi_boxes)[0]:
                weight = 0.
        return point, direction, weight

    def rays(self, seed, start, count):
        """Bounded host camera proposals; GPU performs the full optical walk."""
        rays = np.empty((count, 7), dtype=np.float64)
        key = (seed*0x9E3779B1) & ((1 << 64)-1)
        for row, index in enumerate(range(start, start+count)):
            point, direction, weight = self.launch(index, random.Random(key+(index+1)*0x85EBCA77))
            rays[row] = (*point, *direction, weight)
        return rays


def summarize_batches(rows, options):
    """Batch-t equivalence diagnostic; overlap is never a passing criterion."""
    if len(rows) != options.batches:
        return dict(verdict="inconclusive", reason="incomplete_batches")
    forward = np.array([r["forward_flux_lumen"] for r in rows])
    reverse = np.array([r["reverse_flux_lumen"] for r in rows])
    fm, rm = float(forward.mean()), float(reverse.mean())
    difference = fm-rm
    half = _T95[options.batches]*math.sqrt(float(forward.var(ddof=1)+reverse.var(ddof=1))/options.batches)
    tolerance = max(options.absolute_tolerance_lumen, options.tolerance_percent/100*max(fm, rm))
    hits = sum(r["forward_hits"] for r in rows)
    positives = sum(r["reverse_positive_samples"] for r in rows)
    fs = math.fsum(forward)
    fsq = math.fsum(r["forward_flux_square_sum"] for r in rows)
    rs = math.fsum(r["reverse_flux_lumen"]*r["reverse_sample_count"] for r in rows)
    rsq = math.fsum(r["reverse_second_moment"]*r["reverse_sample_count"] for r in rows)
    fess = fs*fs/fsq if fsq > 0 else 0.
    ress = rs*rs/rsq if rsq > 0 else 0.
    reason = None
    if hits == 0 or positives == 0:
        reason = "zero_samples_not_zero_light_proof"
    elif min(hits, positives, fess, ress) < 30:
        reason = "insufficient_effective_samples"
    elif min(int(np.count_nonzero(forward)), int(np.count_nonzero(reverse))) < options.batches//2:
        reason = "sparse_independent_batches"
    if reason:
        verdict = "inconclusive"
    elif difference-half >= -tolerance and difference+half <= tolerance:
        verdict, reason = "match", "difference_interval_inside_tolerance"
    elif difference+half < -tolerance or difference-half > tolerance:
        verdict, reason = "mismatch", "difference_interval_outside_tolerance"
    else:
        verdict, reason = "inconclusive", "uncertainty_too_large"
    return dict(verdict=verdict, reason=reason, forward_flux_lumen=fm, reverse_flux_lumen=rm,
        difference_lumen=difference, difference_interval_lumen=[difference-half, difference+half], tolerance_lumen=tolerance,
        forward_hits=hits, reverse_positive_samples=positives, forward_effective_samples=fess, reverse_effective_samples=ress,
        uncertainty_method="conservative_independent_batch_t95_diagnostic", formal_coverage_guaranteed=False)


def run_sensor_comparison(trace, options, should_stop=None, progress=None, *, backend="gpu_cuda"):
    from .raytracer import run_direct_ray_trace, _build_receiver_frame
    from .observer_path_validation import validate_receiver_evidence
    from .types import ReceiverGrid
    started = time.perf_counter()
    preflight_record = None
    if backend == "gpu_cuda":
        from . import gpu_cuda_intersection as cuda_backend
        preflight = cuda_backend.preflight_gpu_cuda()
        if not (preflight.available and preflight.strict_float64 and preflight.kernel_executed and preflight.kernel_verified
            and preflight.preflight_scope == "production_ray_bvh" and cuda_backend.PROVIDER_CONTRACT == "strict_float64_bvh_v1"):
            raise ValueError(f"GPU 광량 비교 불가: {preflight.reason_code or 'production_preflight_failed'}")
        preflight_record = dict(available=True, strict_float64=True, kernel_executed=True, kernel_verified=True,
            preflight_scope=preflight.preflight_scope, provider_contract=cuda_backend.PROVIDER_CONTRACT,
            device_name=preflight.device_name, compute_capability=preflight.compute_capability)
    receiver = next((r for r in trace.receivers if r.enabled and r.receiver_id == options.receiver_id), None)
    if receiver is None:
        raise ValueError("활성 Receiver를 찾을 수 없습니다.")
    frame = _build_receiver_frame(receiver)
    frame.roi_boxes = tuple((trace.analysis_roi or {}).get("detection_boxes", []))
    sensor = FiniteSensor(frame, options.reverse_samples)
    sources = _source_geometry(trace.mesh, trace.emitters)
    validate_sources(sources)
    ids = {source[0].emitter_id for source in sources}
    emitters = [e for e in trace.emitters if e.enabled and e.emitter_id in ids]
    if not emitters or len(emitters) > options.forward_samples:
        raise ValueError("양의 출력 광원 및 광원 수 이상의 검증 표본이 필요합니다.")
    profiles, _ = bind_bsdf_profiles(trace.optical_profiles, trace.bsdf_assets, trace.config)
    resolved = OpticalPropertyResolver(trace.mesh, profiles, trace.optical_assignments).resolve_all()
    witness = validate_receiver_evidence(trace, [frame], {receiver.receiver_id: ReceiverGrid.empty(receiver)}, resolved)
    for check in witness:
        check["forward_hit_count"] = None
        check["forward_energy_cutoff_count"] = None
        if check["path_valid"]:
            check["diagnosis"] = "reference_path_valid"
    packed = trace.mesh.prepare_native_cpu_scene()
    geometry_digest = hashlib.sha256()
    for name in ("triangle_v0", "triangle_edge1", "triangle_edge2", "traceable_face_mask"):
        array = np.ascontiguousarray(getattr(packed, name))
        geometry_digest.update(str((name, array.shape, array.dtype.str)).encode())
        geometry_digest.update(memoryview(array).cast("B"))
    routing_digest = hashlib.sha256()
    if hasattr(resolved, "palette_slots"):
        routing_digest.update(memoryview(np.ascontiguousarray(resolved.palette_slots)).cast("B"))
        routing_digest.update(json.dumps([p.profile.to_dict() for p in resolved.palette], sort_keys=True, allow_nan=False).encode())
    else:
        for face in resolved:
            routing_digest.update(json.dumps(face.profile.to_dict(), sort_keys=True, allow_nan=False).encode())
    snapshot = dict(geometry_sha256=geometry_digest.hexdigest(), material_routing_sha256=routing_digest.hexdigest(), receiver=receiver.to_dict(), emitters=[e.to_dict() for e in emitters], config=trace.config.to_dict(),
        profiles=[p.to_dict() for p in trace.optical_profiles], assignments=[a.to_dict() for a in trace.optical_assignments],
        bsdf_assets=trace.bsdf_assets, roi=trace.analysis_roi)
    report = dict(contract=CONTRACT, sensor=dict(receiver_id=receiver.receiver_id, center=receiver.center, normal=frame.normal,
        width_mm=receiver.width_mm, height_mm=receiver.height_mm, acceptance_angle_deg=receiver.acceptance_angle_deg,
        area_m2=sensor.area_m2, solid_angle_sr=sensor.solid_angle),
        physical_input_sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest(),
        geometry_sha256=geometry_digest.hexdigest(), execution_backend=backend, preflight=preflight_record, requested_project_backend=trace.config.compute_backend, gpu_execution_proven=False,
        witness=witness, options=options.__dict__, batches=[], complete=False, verdict="inconclusive",
        source_model_calibrated=False, comparison_unit="lumen", original_config_preserved=True,
        validation_overrides=dict(compute_backend=backend, min_energy=0., termination_mode="russian_roulette",
            primary_sampling_strategy="source", bounce_sampling_strategy="source", single_receiver=True),
        limitations=["finite_sampling", "batch_t_approximation", "reflection_only", "not_real_TV_validation"])
    report["code_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(__file__).parent.glob("*.py")}
    if witness and not witness[0]["path_valid"]:
        report.update(reason="invalid_reference_path", runtime_sec=time.perf_counter()-started)
        return report
    local_config = replace(trace.config, compute_backend=backend, intersection_backend="bvh", min_energy=0.,
        termination_mode="russian_roulette", primary_sampling_strategy="source", bounce_sampling_strategy="source",
        auto_convergence=False, store_ray_paths=False)
    collector = SimpleNamespace(minimum=receiver.center, maximum=receiver.center, direction="neg_z", regions=[])
    for batch in range(options.batches):
        if should_stop and should_stop():
            report.update(reason="stopped", runtime_sec=time.perf_counter()-started)
            return report
        fseed = (options.seed+batch*1000003+0x243F6A88) & 0x7fffffff
        rseed = (options.seed+batch*1000033+0x13198A2E) & 0x7fffffff
        base, extra = divmod(options.forward_samples, len(emitters))
        local_emitters = [replace(e, ray_count=base+int(i < extra), seed=(fseed+i*65537)&0x7fffffff) for i, e in enumerate(emitters)]
        forward_input = replace(trace, emitters=local_emitters, receivers=[receiver], config=replace(local_config, seed=fseed),
            observer_probe=None, escape_collection=None)
        phase_started = time.perf_counter()
        if progress:
            progress(batch, "forward", 0, options.forward_samples)
        result = run_direct_ray_trace(forward_input, should_stop=should_stop,
            progress_callback=(lambda n, total: progress(batch, "forward", n, total)) if progress else None)
        forward_sec = time.perf_counter()-phase_started
        forward_performance = result.metrics.get("_performance_summary", {})
        if (should_stop and should_stop()) or forward_performance.get("stopped_early"):
            report.update(reason="stopped", runtime_sec=time.perf_counter()-started)
            return report
        if backend == "gpu_cuda" and not gpu_proven(forward_performance):
            report.update(reason="forward_gpu_execution_not_proven", runtime_sec=time.perf_counter()-started,
                failed_execution=forward_performance)
            return report
        reverse_input = replace(trace, config=replace(local_config, seed=rseed), observer_probe=dict(measurement="radiance",
            resolution=8, samples_per_cell=options.reverse_samples, search_mode="preview", min_luminance_nit=0., coverage="local",
            horizontal_deg=0., vertical_deg=0., range_deg=0., step_deg=1.))
        phase_started = time.perf_counter()
        reverse = trace_observer_radiance(reverse_input, collector, resolved, should_stop, sensor=sensor,
            progress_callback=(lambda n, total: progress(batch, "reverse", n, total)) if progress else None)
        if not reverse["complete"]:
            report.update(reason="stopped", runtime_sec=time.perf_counter()-started)
            return report
        if backend == "gpu_cuda" and not gpu_proven(reverse["performance"]):
            report.update(reason="reverse_gpu_execution_not_proven", runtime_sec=time.perf_counter()-started,
                failed_execution=reverse["performance"])
            return report
        grid = result.receiver_grids[0]
        report["batches"].append(dict(index=batch+1, forward_seed=fseed, reverse_seed=rseed,
            forward_flux_lumen=math.fsum(v for row in grid.flux_lumen for v in row),
            forward_flux_square_sum=grid.flux_squared_lumen2, forward_hits=grid.hit_count,
            forward_sample_count=result.total_rays, reverse_flux_lumen=reverse["flux_lumen"],
            reverse_second_moment=reverse["sample_second_moment"], reverse_positive_samples=reverse["positive_samples"],
            reverse_sample_count=reverse["sample_count"], forward_sec=forward_sec, reverse_sec=time.perf_counter()-phase_started,
            forward_execution=forward_performance, reverse_execution=reverse["performance"]))
        if batch == 0:
            if progress:
                progress(batch, "original_policy", 0, options.forward_samples)
            original = run_direct_ray_trace(replace(forward_input, config=replace(trace.config,
                compute_backend=backend, intersection_backend="bvh", seed=fseed, auto_convergence=False,
                store_ray_paths=False, primary_sampling_strategy="source", bounce_sampling_strategy="source")),
                should_stop=should_stop)
            if should_stop and should_stop():
                report.update(reason="stopped", runtime_sec=time.perf_counter()-started)
                return report
            og = original.receiver_grids[0]
            report["original_termination_probe"] = dict(forward_flux_lumen=math.fsum(v for row in og.flux_lumen for v in row),
                forward_hits=og.hit_count, min_energy=trace.config.min_energy, termination_mode=trace.config.termination_mode,
                seed=fseed, sample_count=original.total_rays, included_in_equivalence=False,
                termination=original.metrics.get("_termination_summary", {}))
            report["original_termination_probe"]["execution"] = original.metrics.get("_performance_summary", {})
            report["original_termination_probe"]["gpu_execution_proven"] = gpu_proven(report["original_termination_probe"]["execution"])
    report.update(summarize_batches(report["batches"], options), complete=True, runtime_sec=time.perf_counter()-started,
        gpu_execution_proven=backend == "gpu_cuda")
    report["compute_execution_state"] = "gpu_mixed" if backend == "gpu_cuda" and any(row[key].get("compute_execution_state") == "gpu_mixed" for row in report["batches"] for key in ("forward_execution", "reverse_execution")) else "gpu_active" if backend == "gpu_cuda" else "cpu"
    return report


def gpu_proven(performance):
    return performance.get("compute_execution_state") in {"gpu_active", "gpu_mixed"} and performance.get("gpu_cuda_gpu_success_count", 0) > 0
