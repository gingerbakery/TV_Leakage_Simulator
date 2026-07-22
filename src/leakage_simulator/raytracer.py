from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
import math
import random
import time

from .geometry import (
    TriangleMesh,
    vec_add,
    vec_cross,
    vec_dot,
    vec_len,
    vec_mul,
    vec_norm,
    vec_reflect,
    vec_sub,
    clamp01,
)
from .types import EmitterConfig, GapRule, MaterialProfile, ReceiverMetrics, RunConfig, ReceiverPatchConfig, Vec3, fresh_run_id
from .types import EmitterSpec, OpticalAssignment, OpticalProfile, RayHit, RayTraceConfig, RayTraceContributionSummary, RayTraceResult, ReceiverGrid, ReceiverSpec
from .types import SimulationOutput, RunResultSummary, random_unit_vector
from .gap import GapSample, sample_gap_profiles
from .optics import OpticalPropertyResolver, UNASSIGNED_PROFILE_ID
from .reflection import ReflectionSample, sample_reflection_direction
from .fast_sampling import (
    iter_virtual_plane_ray_batches,
    supports_fast_virtual_plane_sampling,
)


@dataclass
class EngineInput:
    source_file: Optional[str]
    mesh: TriangleMesh
    emitters: List[EmitterConfig]
    gap_rules: List[GapRule]
    receivers: List[ReceiverPatchConfig]
    materials: Dict[str, MaterialProfile]
    config: RunConfig
    project_name: str = "TV-Leakage-V1"
    source_is_synthetic: bool = False
    import_note: str = ""


@dataclass
class RayPathEvent:
    hit_face: int
    hit_pos: Vec3
    energy: float
    depth: int
    is_receiver: bool


@dataclass
class DirectRayTraceInput:
    mesh: TriangleMesh
    emitters: List[EmitterSpec]
    receivers: List[ReceiverSpec]
    optical_profiles: List[OpticalProfile]
    config: RayTraceConfig
    project_name: str = "TV-Leakage-RT2C"
    optical_assignments: List[OpticalAssignment] = field(default_factory=list)


@dataclass(slots=True)
class ReceiverFrame:
    receiver: ReceiverSpec
    u_axis: Vec3
    v_axis: Vec3
    half_width: float
    half_height: float
    inverse_width: float
    inverse_height: float
    minimum_acceptance_cosine: float
    columns: int
    rows: int


@dataclass(slots=True)
class ReceiverHitCandidate:
    grid: ReceiverGrid
    row: int
    column: int
    received_power_lumen: float
    point: Vec3
    normal: Vec3
    distance_mm: float
    incoming_power_lumen: float
    receiver_id: str
    depth: int
    ray_kind: str

    def to_ray_hit(self) -> RayHit:
        return RayHit(
            face_index=-1,
            component_id=None,
            material_id=None,
            point=self.point,
            normal=self.normal,
            distance_mm=self.distance_mm,
            incoming_energy_lumen=self.incoming_power_lumen,
            outgoing_energy_lumen=0.0,
            depth=self.depth,
            event_type="receiver",
            receiver_id=self.receiver_id,
            ray_kind=self.ray_kind,
        )


def run_simulation(engine_input: EngineInput) -> SimulationOutput:
    start_time = time.time()
    rng = random.Random(engine_input.config.seed)
    gap_samples: Dict[int, GapSample] = sample_gap_profiles(engine_input.gap_rules, rng, engine_input.mesh)
    receiver_area = _build_receiver_area(engine_input.mesh, engine_input.receivers)
    receiver_irradiance: Dict[str, float] = {r.receiver_id: 0.0 for r in engine_input.receivers}
    receiver_hits: Dict[str, int] = {r.receiver_id: 0 for r in engine_input.receivers}
    run_id = fresh_run_id("run")
    hit_count = 0
    total_rays = 0

    face_to_receiver = _build_face_to_receiver_map(engine_input.receivers)

    emitter_rays = max(1, engine_input.config.ray_count)
    power_scale = 1.0 / float(emitter_rays)

    for emitter in engine_input.emitters:
        if not emitter.enabled:
            continue
        for _ in range(emitter_rays):
            total_rays += 1
            if emitter.emitter_type == "face":
                hit = _emit_from_face(engine_input.mesh, emitter, rng)
                if hit is None:
                    continue
                origin, direction = hit
            elif emitter.emitter_type == "volume_box":
                hit = _emit_from_box(emitter, rng)
                if hit is None:
                    continue
                origin, direction = hit
            elif emitter.emitter_type == "volume_sphere":
                hit = _emit_from_sphere(emitter, rng)
                if hit is None:
                    continue
                origin, direction = hit
            else:
                continue

            path_count = _trace_path(
                mesh=engine_input.mesh,
                origin=origin,
                direction=direction,
                energy=emitter.strength * power_scale,
                max_depth=engine_input.config.max_depth,
                materials=engine_input.materials,
                rng=rng,
                gap_samples=gap_samples,
                face_to_receiver=face_to_receiver,
                receiver_area=receiver_area,
                receiver_irradiance=receiver_irradiance,
                receiver_hits=receiver_hits,
            )
            if path_count > 0:
                hit_count += path_count

    runtime = time.time() - start_time
    metrics = _build_metrics(
        receiver_area=receiver_area,
        receiver_irradiance=receiver_irradiance,
        receiver_hits=receiver_hits,
        config=engine_input.config,
    )


def run_direct_ray_trace(
    trace_input: DirectRayTraceInput,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> RayTraceResult:
    start_time = time.time()
    rng = random.Random(trace_input.config.seed)
    trace_input.mesh.set_intersection_backend(
        trace_input.config.intersection_backend
    )
    receiver_frames = [_build_receiver_frame(receiver) for receiver in trace_input.receivers if receiver.enabled]
    receiver_grids = {
        receiver.receiver_id: ReceiverGrid.empty(receiver)
        for receiver in trace_input.receivers
        if receiver.enabled
    }
    stored_paths: List[List[RayHit]] = []
    total_rays = 0
    fast_primary_ray_count = 0
    scalar_primary_ray_count = 0
    receiver_hit_count = 0
    surface_hit_count = 0
    terminated_ray_count = 0
    optical_resolver = OpticalPropertyResolver(
        trace_input.mesh,
        trace_input.optical_profiles,
        trace_input.optical_assignments,
    )
    resolved_optical_by_face = [
        optical_resolver.resolve(face_index)
        for face_index in range(len(trace_input.mesh.faces))
    ]
    optical_summary = {
        "surface_hit_count": 0,
        "unassigned_surface_hit_count": 0,
        "profile_hits": {},
    }
    reflection_summary = _empty_reflection_summary(trace_input.config)
    contribution_summary = _empty_contribution_summary(trace_input.receivers)
    detailed_contributions = trace_input.config.contribution_mode == "detailed"
    face_contribution_cache: List[Optional[Dict]] = (
        [None for _ in trace_input.mesh.faces]
        if detailed_contributions
        else []
    )
    execution_path = (
        "single_bounce_fast"
        if trace_input.config.max_depth <= 1
        else "multi_bounce"
    )
    expected_ray_count = sum(
        emitter.ray_count for emitter in trace_input.emitters if emitter.enabled
    )
    progress_interval = max(1, expected_ray_count // 400)
    last_progress_count = -1
    if progress_callback is not None:
        progress_callback(0, expected_ray_count)

    for emitter in trace_input.emitters:
        if not emitter.enabled:
            continue
        emitter_seed = (
            emitter.seed
            if emitter.seed is not None
            else rng.randint(0, 2**31 - 1)
        )
        emitter_rng = random.Random(emitter_seed ^ 0x5DEECE66D)
        if supports_fast_virtual_plane_sampling(emitter):
            fast_primary_ray_count += emitter.ray_count
        else:
            scalar_primary_ray_count += emitter.ray_count
        face_weights = _build_emitter_face_weights(trace_input.mesh, emitter.face_indices) if emitter.emitter_type == "face" else []
        if emitter.emitter_type == "face":
            emitter_area_mm2 = sum(
                trace_input.mesh.area(face_index)
                for face_index in emitter.face_indices
                if 0 <= face_index < len(trace_input.mesh.faces)
            )
        else:
            emitter_area_mm2 = emitter.virtual_area_mm2()
        ray_power = emitter.effective_power_lumen(emitter_area_mm2) / float(emitter.ray_count)
        for ray in _iter_primary_emitter_rays(
            trace_input.mesh,
            emitter,
            face_weights,
            emitter_rng,
            emitter_seed,
            trace_input.config.epsilon_mm,
        ):
            total_rays += 1
            if progress_callback is not None:
                processed_ray_count = max(0, total_rays - 1)
                if processed_ray_count - last_progress_count >= progress_interval:
                    progress_callback(processed_ray_count, expected_ray_count)
                    last_progress_count = processed_ray_count
            if ray is None:
                terminated_ray_count += 1
                continue
            origin, direction, source_face = ray
            store_path = (
                trace_input.config.store_ray_paths
                and len(stored_paths) < trace_input.config.max_stored_paths
            )
            emitter_event = (
                _emitter_ray_hit(source_face, origin, direction, ray_power)
                if store_path
                else None
            )
            current_origin = origin
            current_direction = direction
            current_power = ray_power
            current_source_face = source_face
            current_depth = 0
            current_ray_kind = "direct"
            previous_surface_contribution: Optional[Dict] = None
            previous_lobe: Optional[str] = None
            path_events: List[RayHit] = [emitter_event] if emitter_event is not None else []

            if trace_input.config.max_depth <= 1:
                fast_receiver_hits, fast_surface_hits, fast_terminated = _trace_single_bounce_fast(
                    trace_input.mesh,
                    origin,
                    direction,
                    ray_power,
                    source_face,
                    receiver_frames,
                    receiver_grids,
                    trace_input.config,
                    resolved_optical_by_face,
                    emitter_rng,
                    optical_summary,
                    reflection_summary,
                    contribution_summary,
                    face_contribution_cache,
                    detailed_contributions,
                    store_path,
                    path_events,
                    stored_paths,
                )
                receiver_hit_count += fast_receiver_hits
                surface_hit_count += fast_surface_hits
                terminated_ray_count += fast_terminated
                continue

            while True:
                reflection_summary["max_observed_depth"] = max(
                    reflection_summary["max_observed_depth"],
                    current_depth,
                )
                receiver_candidate = _find_first_receiver_hit(
                    origin=current_origin,
                    direction=current_direction,
                    power_lumen=current_power,
                    source_face=current_source_face,
                    receivers=receiver_frames,
                    grids=receiver_grids,
                    config=trace_input.config,
                    depth=current_depth,
                    ray_kind=current_ray_kind,
                )
                receiver_distance = (
                    receiver_candidate.distance_mm
                    if receiver_candidate is not None
                    else None
                )
                surface_hit = trace_input.mesh.intersect_ray(
                    current_origin,
                    current_direction,
                    ignore_face=current_source_face if current_source_face >= 0 else None,
                    min_t=trace_input.config.epsilon_mm,
                    max_t=receiver_distance,
                )

                if surface_hit is None:
                    if receiver_candidate is not None:
                        _record_receiver_hit(receiver_candidate)
                        receiver_hit_count += 1
                        if current_depth == 0:
                            reflection_summary["direct_receiver_hit_count"] += 1
                            reflection_summary["direct_receiver_flux_lumen"] += receiver_candidate.received_power_lumen
                            _record_direct_receiver_contribution(
                                contribution_summary,
                                receiver_candidate,
                            )
                        else:
                            _record_reflection_outcome(
                                reflection_summary,
                                previous_lobe,
                                "receiver",
                                current_depth,
                                receiver_candidate.received_power_lumen,
                            )
                            _record_reflected_receiver_contribution(
                                contribution_summary,
                                receiver_candidate,
                                previous_lobe,
                                current_depth,
                            )
                            _record_surface_reflection_outcome(
                                contribution_summary,
                                previous_surface_contribution,
                                previous_lobe,
                                "receiver",
                                current_power,
                                current_depth,
                                received_flux_lumen=receiver_candidate.received_power_lumen,
                            )
                        if store_path:
                            path_events.append(receiver_candidate.to_ray_hit())
                            stored_paths.append(path_events)
                    else:
                        terminated_ray_count += 1
                        if current_depth > 0:
                            _record_reflection_outcome(
                                reflection_summary,
                                previous_lobe,
                                "escaped",
                                current_depth,
                            )
                            _record_surface_reflection_outcome(
                                contribution_summary,
                                previous_surface_contribution,
                                previous_lobe,
                                "escaped",
                                current_power,
                                current_depth,
                            )
                        if store_path:
                            stored_paths.append(path_events)
                    break

                surface_hit_count += 1
                resolved_optical = resolved_optical_by_face[surface_hit.face_index]
                reflected_power = current_power * resolved_optical.profile.reflectance
                surface_contribution = (
                    _surface_contribution_for_face(
                        contribution_summary,
                        trace_input.mesh,
                        surface_hit.face_index,
                        face_contribution_cache,
                    )
                    if detailed_contributions
                    else None
                )
                if current_depth == 0:
                    reflection_summary["primary_surface_hit_count"] += 1
                reflection_summary["surface_hit_count"] += 1
                reflection_summary["max_observed_depth"] = max(
                    reflection_summary["max_observed_depth"],
                    current_depth,
                )
                if detailed_contributions:
                    _record_surface_hit_contribution(
                        contribution_summary,
                        surface_contribution,
                        current_depth,
                        current_power,
                        reflected_power,
                    )
                _record_optical_summary(
                    optical_summary,
                    resolved_optical.profile,
                    resolved_optical.source,
                    current_power,
                    reflected_power,
                )
                reflection_emission = _prepare_reflection_emission(
                    emitter_rng,
                    current_direction,
                    surface_hit.normal,
                    reflected_power,
                    resolved_optical.profile,
                    trace_input.config,
                    reflection_summary,
                    current_depth,
                )

                if reflection_emission is None:
                    if current_depth > 0:
                        _record_reflection_outcome(
                            reflection_summary,
                            previous_lobe,
                            "blocked",
                            current_depth,
                        )
                        _record_surface_reflection_outcome(
                            contribution_summary,
                            previous_surface_contribution,
                            previous_lobe,
                            "blocked",
                            current_power,
                            current_depth,
                        )
                        if detailed_contributions:
                            _record_secondary_blocker_contribution(
                                contribution_summary,
                                surface_contribution,
                                previous_lobe,
                                current_power,
                                current_depth,
                            )
                    terminated_ray_count += 1
                    if store_path:
                        path_events.append(
                            _surface_ray_hit(
                                trace_input.mesh,
                                surface_hit.face_index,
                                surface_hit.point,
                                surface_hit.normal,
                                surface_hit.t,
                                current_power,
                                reflected_power,
                                depth=current_depth,
                                optical_profile=resolved_optical.profile,
                                optical_source=resolved_optical.source,
                                ray_kind=current_ray_kind if current_depth > 0 else None,
                            )
                        )
                        stored_paths.append(path_events)
                    break

                reflection_sample, emitted_power = reflection_emission
                next_depth = current_depth + 1
                if current_depth > 0:
                    _record_reflection_outcome(
                        reflection_summary,
                        previous_lobe,
                        "continued",
                        current_depth,
                    )
                    _record_surface_reflection_outcome(
                        contribution_summary,
                        previous_surface_contribution,
                        previous_lobe,
                        "continued",
                        current_power,
                        current_depth,
                    )
                _record_reflection_emission(
                    reflection_summary,
                    reflection_sample,
                    emitted_power,
                    next_depth,
                )
                _record_surface_reflection_emission(
                    contribution_summary,
                    surface_contribution,
                    reflection_sample.lobe,
                    emitted_power,
                    next_depth,
                )
                if store_path:
                    path_events.append(
                        _surface_ray_hit(
                            trace_input.mesh,
                            surface_hit.face_index,
                            surface_hit.point,
                            surface_hit.normal,
                            surface_hit.t,
                            current_power,
                            emitted_power,
                            depth=current_depth,
                            optical_profile=resolved_optical.profile,
                            optical_source=resolved_optical.source,
                            ray_kind=reflection_sample.lobe,
                        )
                    )

                previous_surface_contribution = surface_contribution
                previous_lobe = reflection_sample.lobe
                current_origin = vec_add(
                    surface_hit.point,
                    vec_mul(reflection_sample.direction, trace_input.config.epsilon_mm),
                )
                current_direction = reflection_sample.direction
                current_power = emitted_power
                current_source_face = surface_hit.face_index
                current_depth = next_depth
                current_ray_kind = reflection_sample.lobe

    if progress_callback is not None:
        progress_callback(total_rays, expected_ray_count)
    _finalize_surface_contributions(contribution_summary)
    grids = [receiver_grids[receiver.receiver_id] for receiver in trace_input.receivers if receiver.enabled]
    metrics = _build_direct_metrics(grids, trace_input.config)
    metrics["_optical_summary"] = optical_summary
    metrics["_reflection_summary"] = reflection_summary
    metrics["_contribution_summary"] = contribution_summary.to_dict()
    runtime_sec = time.time() - start_time
    acceleration_info = trace_input.mesh.acceleration_info()
    metrics["_performance_summary"] = {
        "backend": "python_numpy_cpu",
        "execution_path": execution_path,
        "contribution_mode": trace_input.config.contribution_mode,
        "intersection_backend": acceleration_info["selected_backend"],
        "configured_intersection_backend": acceleration_info["configured_backend"],
        "bvh_node_count": acceleration_info["bvh_node_count"],
        "bvh_leaf_count": acceleration_info["bvh_leaf_count"],
        "bvh_build_sec": acceleration_info["bvh_build_sec"],
        "fast_primary_ray_count": fast_primary_ray_count,
        "scalar_primary_ray_count": scalar_primary_ray_count,
        "resolved_optical_face_cache_count": len(resolved_optical_by_face),
        "stored_path_count": len(stored_paths),
        "rays_per_sec": total_rays / runtime_sec if runtime_sec > 0.0 else 0.0,
    }
    return RayTraceResult(
        run_id=fresh_run_id("rt3"),
        config=trace_input.config,
        emitters=trace_input.emitters,
        receivers=trace_input.receivers,
        receiver_grids=grids,
        optical_profiles=trace_input.optical_profiles,
        total_rays=total_rays,
        receiver_hit_count=receiver_hit_count,
        surface_hit_count=surface_hit_count,
        terminated_ray_count=terminated_ray_count,
        contribution_summary=contribution_summary,
        runtime_sec=runtime_sec,
        stored_paths=stored_paths,
        metrics=metrics,
    )


def _trace_single_bounce_fast(
    mesh: TriangleMesh,
    origin: Vec3,
    direction: Vec3,
    ray_power: float,
    source_face: int,
    receivers: List[ReceiverFrame],
    receiver_grids: Dict[str, ReceiverGrid],
    config: RayTraceConfig,
    resolved_optical_by_face: List,
    rng: random.Random,
    optical_summary: Dict,
    reflection_summary: Dict,
    contribution_summary: RayTraceContributionSummary,
    face_contribution_cache: List[Optional[Dict]],
    detailed_contributions: bool,
    store_path: bool,
    path_events: List[RayHit],
    stored_paths: List[List[RayHit]],
) -> Tuple[int, int, int]:
    receiver_candidate = _find_first_receiver_hit(
        origin=origin,
        direction=direction,
        power_lumen=ray_power,
        source_face=source_face,
        receivers=receivers,
        grids=receiver_grids,
        config=config,
        depth=0,
        ray_kind="direct",
    )
    receiver_distance = (
        receiver_candidate.distance_mm
        if receiver_candidate is not None
        else None
    )
    surface_hit = mesh.intersect_ray(
        origin,
        direction,
        ignore_face=source_face if source_face >= 0 else None,
        min_t=config.epsilon_mm,
        max_t=receiver_distance,
    )

    if surface_hit is None:
        if receiver_candidate is None:
            if store_path:
                stored_paths.append(path_events)
            return 0, 0, 1
        _record_receiver_hit(receiver_candidate)
        reflection_summary["direct_receiver_hit_count"] += 1
        reflection_summary["direct_receiver_flux_lumen"] += receiver_candidate.received_power_lumen
        _record_direct_receiver_contribution(
            contribution_summary,
            receiver_candidate,
        )
        if store_path:
            path_events.append(receiver_candidate.to_ray_hit())
            stored_paths.append(path_events)
        return 1, 0, 0

    reflection_summary["surface_hit_count"] += 1
    reflection_summary["primary_surface_hit_count"] += 1
    resolved_optical = resolved_optical_by_face[surface_hit.face_index]
    reflected_power = ray_power * resolved_optical.profile.reflectance
    surface_contribution = (
        _surface_contribution_for_face(
            contribution_summary,
            mesh,
            surface_hit.face_index,
            face_contribution_cache,
        )
        if detailed_contributions
        else None
    )
    if detailed_contributions:
        _record_surface_hit_contribution(
            contribution_summary,
            surface_contribution,
            0,
            ray_power,
            reflected_power,
        )
    _record_optical_summary(
        optical_summary,
        resolved_optical.profile,
        resolved_optical.source,
        ray_power,
        reflected_power,
    )
    reflection_emission = _prepare_reflection_emission(
        rng,
        direction,
        surface_hit.normal,
        reflected_power,
        resolved_optical.profile,
        config,
        reflection_summary,
        0,
    )
    if reflection_emission is None:
        if store_path:
            path_events.append(
                _surface_ray_hit(
                    mesh,
                    surface_hit.face_index,
                    surface_hit.point,
                    surface_hit.normal,
                    surface_hit.t,
                    ray_power,
                    reflected_power,
                    depth=0,
                    optical_profile=resolved_optical.profile,
                    optical_source=resolved_optical.source,
                )
            )
            stored_paths.append(path_events)
        return 0, 1, 1

    reflection_sample, emitted_power = reflection_emission
    _record_reflection_emission(
        reflection_summary,
        reflection_sample,
        emitted_power,
        1,
    )
    _record_surface_reflection_emission(
        contribution_summary,
        surface_contribution,
        reflection_sample.lobe,
        emitted_power,
        1,
    )
    if store_path:
        path_events.append(
            _surface_ray_hit(
                mesh,
                surface_hit.face_index,
                surface_hit.point,
                surface_hit.normal,
                surface_hit.t,
                ray_power,
                emitted_power,
                depth=0,
                optical_profile=resolved_optical.profile,
                optical_source=resolved_optical.source,
                ray_kind=reflection_sample.lobe,
            )
        )

    reflected_origin = vec_add(
        surface_hit.point,
        vec_mul(reflection_sample.direction, config.epsilon_mm),
    )
    reflected_receiver = _find_first_receiver_hit(
        origin=reflected_origin,
        direction=reflection_sample.direction,
        power_lumen=emitted_power,
        source_face=surface_hit.face_index,
        receivers=receivers,
        grids=receiver_grids,
        config=config,
        depth=1,
        ray_kind=reflection_sample.lobe,
    )
    reflected_receiver_distance = (
        reflected_receiver.distance_mm
        if reflected_receiver is not None
        else None
    )
    secondary_surface_hit = mesh.intersect_ray(
        reflected_origin,
        reflection_sample.direction,
        ignore_face=surface_hit.face_index,
        min_t=config.epsilon_mm,
        max_t=reflected_receiver_distance,
    )
    reflection_summary["max_observed_depth"] = 1

    if secondary_surface_hit is not None:
        reflection_summary["surface_hit_count"] += 1
        secondary_optical = resolved_optical_by_face[secondary_surface_hit.face_index]
        secondary_reflected_power = emitted_power * secondary_optical.profile.reflectance
        secondary_contribution = (
            _surface_contribution_for_face(
                contribution_summary,
                mesh,
                secondary_surface_hit.face_index,
                face_contribution_cache,
            )
            if detailed_contributions
            else None
        )
        if detailed_contributions:
            _record_surface_hit_contribution(
                contribution_summary,
                secondary_contribution,
                1,
                emitted_power,
                secondary_reflected_power,
            )
        _record_optical_summary(
            optical_summary,
            secondary_optical.profile,
            secondary_optical.source,
            emitted_power,
            secondary_reflected_power,
        )
        _prepare_reflection_emission(
            rng,
            reflection_sample.direction,
            secondary_surface_hit.normal,
            secondary_reflected_power,
            secondary_optical.profile,
            config,
            reflection_summary,
            1,
        )
        _record_reflection_outcome(
            reflection_summary,
            reflection_sample.lobe,
            "blocked",
            1,
        )
        _record_surface_reflection_outcome(
            contribution_summary,
            surface_contribution,
            reflection_sample.lobe,
            "blocked",
            emitted_power,
            1,
        )
        if detailed_contributions:
            _record_secondary_blocker_contribution(
                contribution_summary,
                secondary_contribution,
                reflection_sample.lobe,
                emitted_power,
                1,
            )
        if store_path:
            path_events.append(
                _surface_ray_hit(
                    mesh,
                    secondary_surface_hit.face_index,
                    secondary_surface_hit.point,
                    secondary_surface_hit.normal,
                    secondary_surface_hit.t,
                    emitted_power,
                    secondary_reflected_power,
                    depth=1,
                    optical_profile=secondary_optical.profile,
                    optical_source=secondary_optical.source,
                    ray_kind=reflection_sample.lobe,
                )
            )
            stored_paths.append(path_events)
        return 0, 2, 1

    if reflected_receiver is not None:
        _record_receiver_hit(reflected_receiver)
        _record_reflection_outcome(
            reflection_summary,
            reflection_sample.lobe,
            "receiver",
            1,
            reflected_receiver.received_power_lumen,
        )
        _record_reflected_receiver_contribution(
            contribution_summary,
            reflected_receiver,
            reflection_sample.lobe,
            1,
        )
        _record_surface_reflection_outcome(
            contribution_summary,
            surface_contribution,
            reflection_sample.lobe,
            "receiver",
            emitted_power,
            1,
            received_flux_lumen=reflected_receiver.received_power_lumen,
        )
        if store_path:
            path_events.append(reflected_receiver.to_ray_hit())
            stored_paths.append(path_events)
        return 1, 1, 0

    _record_reflection_outcome(
        reflection_summary,
        reflection_sample.lobe,
        "escaped",
        1,
    )
    _record_surface_reflection_outcome(
        contribution_summary,
        surface_contribution,
        reflection_sample.lobe,
        "escaped",
        emitted_power,
        1,
    )
    if store_path:
        stored_paths.append(path_events)
    return 0, 1, 1


def _iter_primary_emitter_rays(
    mesh: TriangleMesh,
    emitter: EmitterSpec,
    face_weights: List[Tuple[int, float]],
    rng: random.Random,
    seed: int,
    epsilon_mm: float,
):
    if supports_fast_virtual_plane_sampling(emitter):
        for origin_batch, direction_batch in iter_virtual_plane_ray_batches(
            emitter,
            epsilon_mm,
            seed,
        ):
            for index in range(len(origin_batch)):
                origin_values = origin_batch[index]
                direction_values = direction_batch[index]
                yield (
                    (
                        float(origin_values[0]),
                        float(origin_values[1]),
                        float(origin_values[2]),
                    ),
                    (
                        float(direction_values[0]),
                        float(direction_values[1]),
                        float(direction_values[2]),
                    ),
                    -1,
                )
        return
    for _ in range(emitter.ray_count):
        if emitter.emitter_type == "face":
            yield _sample_face_emitter_ray(
                mesh,
                emitter,
                face_weights,
                rng,
                epsilon_mm,
            )
        else:
            yield _sample_virtual_plane_emitter_ray(
                emitter,
                rng,
                epsilon_mm,
            )


def _empty_reflection_summary(config: RayTraceConfig) -> Dict:
    return {
        "enabled": config.max_depth >= 1,
        "implemented_max_depth": config.max_depth,
        "termination_mode": config.termination_mode,
        "min_energy_lumen": config.min_energy,
        "max_observed_depth": 0,
        "surface_hit_count": 0,
        "primary_surface_hit_count": 0,
        "reflection_attempt_count": 0,
        "reflection_emitted_count": 0,
        "reflection_receiver_hit_count": 0,
        "reflection_blocked_count": 0,
        "reflection_continued_count": 0,
        "reflection_escaped_count": 0,
        "reflection_below_energy_count": 0,
        "reflection_disabled_count": 0,
        "depth_limit_count": 0,
        "roulette_terminated_count": 0,
        "roulette_survived_count": 0,
        "direct_receiver_hit_count": 0,
        "direct_receiver_flux_lumen": 0.0,
        "reflected_receiver_flux_lumen": 0.0,
        "depths": {},
        "lobes": {
            lobe: {
                "emitted_count": 0,
                "emitted_flux_lumen": 0.0,
                "receiver_hit_count": 0,
                "receiver_flux_lumen": 0.0,
                "blocked_count": 0,
                "continued_count": 0,
                "escaped_count": 0,
            }
            for lobe in ("specular", "lambertian", "gaussian")
        },
    }


def _empty_count_flux() -> Dict[str, float]:
    return {"hit_count": 0, "flux_lumen": 0.0}


def _empty_lobe_contribution() -> Dict[str, float]:
    return {
        "emitted_count": 0,
        "emitted_flux_lumen": 0.0,
        "receiver_hit_count": 0,
        "receiver_flux_lumen": 0.0,
        "blocked_count": 0,
        "blocked_flux_lumen": 0.0,
        "continued_count": 0,
        "continued_flux_lumen": 0.0,
        "escaped_count": 0,
        "escaped_flux_lumen": 0.0,
    }


def _empty_depth_contribution() -> Dict[str, float]:
    return {
        "surface_hit_count": 0,
        "surface_incident_flux_lumen": 0.0,
        "reflection_emitted_count": 0,
        "reflection_emitted_flux_lumen": 0.0,
        "receiver_hit_count": 0,
        "receiver_flux_lumen": 0.0,
        "blocked_count": 0,
        "blocked_flux_lumen": 0.0,
        "continued_count": 0,
        "continued_flux_lumen": 0.0,
        "escaped_count": 0,
        "escaped_flux_lumen": 0.0,
        "secondary_block_count": 0,
        "secondary_blocked_flux_lumen": 0.0,
    }


def _depth_contribution(entries: Dict[str, Dict], depth: int) -> Dict:
    depth_key = str(depth)
    entry = entries.get(depth_key)
    if entry is None:
        entry = _empty_depth_contribution()
        entries[depth_key] = entry
    return entry


def _receiver_depth_contribution(entries: Dict[str, Dict], depth: int) -> Dict:
    depth_key = str(depth)
    entry = entries.get(depth_key)
    if entry is None:
        entry = _empty_count_flux()
        entries[depth_key] = entry
    return entry


def _reflection_depth_summary(summary: Dict, depth: int) -> Dict:
    depth_key = str(depth)
    entry = summary["depths"].get(depth_key)
    if entry is None:
        entry = {
            "emitted_count": 0,
            "emitted_flux_lumen": 0.0,
            "receiver_hit_count": 0,
            "receiver_flux_lumen": 0.0,
            "blocked_count": 0,
            "continued_count": 0,
            "escaped_count": 0,
        }
        summary["depths"][depth_key] = entry
    return entry


def _empty_receiver_contribution(receiver_id: str) -> Dict:
    return {
        "receiver_id": receiver_id,
        "direct": _empty_count_flux(),
        "reflected": _empty_count_flux(),
        "total": _empty_count_flux(),
        "lobes": {
            lobe: _empty_count_flux()
            for lobe in ("specular", "lambertian", "gaussian")
        },
        "depths": {},
    }


def _empty_surface_contribution(
    target_id: str,
    component_id: Optional[str] = None,
    material_id: Optional[str] = None,
) -> Dict:
    contribution = {
        "target_id": target_id,
        "surface_hit_count": 0,
        "surface_incident_flux_lumen": 0.0,
        "surface_reflectable_flux_lumen": 0.0,
        "primary_hit_count": 0,
        "incident_flux_lumen": 0.0,
        "reflectable_flux_lumen": 0.0,
        "reflection_emitted_count": 0,
        "reflection_emitted_flux_lumen": 0.0,
        "receiver_hit_count": 0,
        "receiver_flux_lumen": 0.0,
        "reflection_blocked_count": 0,
        "reflection_blocked_flux_lumen": 0.0,
        "continued_count": 0,
        "continued_flux_lumen": 0.0,
        "secondary_block_count": 0,
        "secondary_blocked_flux_lumen": 0.0,
        "escaped_count": 0,
        "escaped_flux_lumen": 0.0,
        "lobes": {
            lobe: _empty_lobe_contribution()
            for lobe in ("specular", "lambertian", "gaussian")
        },
        "depths": {},
    }
    if component_id is not None:
        contribution["component_id"] = component_id
    if material_id is not None:
        contribution["material_id"] = material_id
    return contribution


def _empty_contribution_summary(
    receivers: List[ReceiverSpec],
) -> RayTraceContributionSummary:
    return RayTraceContributionSummary(
        receivers={
            receiver.receiver_id: _empty_receiver_contribution(receiver.receiver_id)
            for receiver in receivers
            if receiver.enabled
        },
        lobes={
            lobe: _empty_lobe_contribution()
            for lobe in ("specular", "lambertian", "gaussian")
        },
    )


def _surface_contribution_for_face(
    summary: RayTraceContributionSummary,
    mesh: TriangleMesh,
    face_index: int,
    cache: List[Optional[Dict]],
) -> Dict:
    cached = cache[face_index]
    if cached is not None:
        return cached
    metadata = mesh.metadata(face_index)
    component_id = metadata.get("component_id")
    if component_id is None:
        component_id = metadata.get("step_component_id")
    material_id = mesh.material_id(face_index) or "unassigned"
    component_key = str(component_id) if component_id is not None else "unassigned"
    face_key = str(face_index)
    contribution = _empty_surface_contribution(
        face_key,
        component_id=component_key,
        material_id=str(material_id),
    )
    summary.faces[face_key] = contribution
    cache[face_index] = contribution
    return contribution


def _record_surface_hit_contribution(
    summary: RayTraceContributionSummary,
    contribution: Dict,
    depth: int,
    incident_flux_lumen: float,
    reflectable_flux_lumen: float,
) -> None:
    contribution["surface_hit_count"] += 1
    contribution["surface_incident_flux_lumen"] += incident_flux_lumen
    contribution["surface_reflectable_flux_lumen"] += reflectable_flux_lumen
    if depth == 0:
        contribution["primary_hit_count"] += 1
        contribution["incident_flux_lumen"] += incident_flux_lumen
        contribution["reflectable_flux_lumen"] += reflectable_flux_lumen
    surface_depth = _depth_contribution(contribution["depths"], depth)
    surface_depth["surface_hit_count"] += 1
    surface_depth["surface_incident_flux_lumen"] += incident_flux_lumen


def _record_surface_reflection_emission(
    summary: RayTraceContributionSummary,
    contribution: Optional[Dict],
    lobe: str,
    emitted_flux_lumen: float,
    depth: int,
) -> None:
    global_lobe = summary.lobes[lobe]
    global_lobe["emitted_count"] += 1
    global_lobe["emitted_flux_lumen"] += emitted_flux_lumen
    if contribution is None:
        depth_entry = _depth_contribution(summary.depths, depth)
        depth_entry["reflection_emitted_count"] += 1
        depth_entry["reflection_emitted_flux_lumen"] += emitted_flux_lumen
        return
    contribution["reflection_emitted_count"] += 1
    contribution["reflection_emitted_flux_lumen"] += emitted_flux_lumen
    lobe_contribution = contribution["lobes"][lobe]
    lobe_contribution["emitted_count"] += 1
    lobe_contribution["emitted_flux_lumen"] += emitted_flux_lumen
    depth_entry = _depth_contribution(contribution["depths"], depth)
    depth_entry["reflection_emitted_count"] += 1
    depth_entry["reflection_emitted_flux_lumen"] += emitted_flux_lumen


def _record_surface_reflection_outcome(
    summary: RayTraceContributionSummary,
    contribution: Optional[Dict],
    lobe: str,
    outcome: str,
    flux_lumen: float,
    depth: int,
    received_flux_lumen: Optional[float] = None,
) -> None:
    global_lobe = summary.lobes[lobe]
    outcome_flux_lumen = (
        received_flux_lumen
        if outcome == "receiver" and received_flux_lumen is not None
        else flux_lumen
    )
    if outcome == "receiver":
        global_lobe["receiver_hit_count"] += 1
        global_lobe["receiver_flux_lumen"] += outcome_flux_lumen
    elif outcome == "blocked":
        global_lobe["blocked_count"] += 1
        global_lobe["blocked_flux_lumen"] += flux_lumen
    elif outcome == "continued":
        global_lobe["continued_count"] += 1
        global_lobe["continued_flux_lumen"] += flux_lumen
    else:
        global_lobe["escaped_count"] += 1
        global_lobe["escaped_flux_lumen"] += flux_lumen
    if contribution is None:
        depth_entry = _depth_contribution(summary.depths, depth)
        if outcome == "receiver":
            depth_entry["receiver_hit_count"] += 1
            depth_entry["receiver_flux_lumen"] += outcome_flux_lumen
        elif outcome == "blocked":
            depth_entry["blocked_count"] += 1
            depth_entry["blocked_flux_lumen"] += flux_lumen
        elif outcome == "continued":
            depth_entry["continued_count"] += 1
            depth_entry["continued_flux_lumen"] += flux_lumen
        else:
            depth_entry["escaped_count"] += 1
            depth_entry["escaped_flux_lumen"] += flux_lumen
        return
    lobe_contribution = contribution["lobes"][lobe]
    if outcome == "receiver":
        contribution["receiver_hit_count"] += 1
        contribution["receiver_flux_lumen"] += outcome_flux_lumen
        lobe_contribution["receiver_hit_count"] += 1
        lobe_contribution["receiver_flux_lumen"] += outcome_flux_lumen
    elif outcome == "blocked":
        contribution["reflection_blocked_count"] += 1
        contribution["reflection_blocked_flux_lumen"] += flux_lumen
        lobe_contribution["blocked_count"] += 1
        lobe_contribution["blocked_flux_lumen"] += flux_lumen
    elif outcome == "continued":
        contribution["continued_count"] += 1
        contribution["continued_flux_lumen"] += flux_lumen
        lobe_contribution["continued_count"] += 1
        lobe_contribution["continued_flux_lumen"] += flux_lumen
    else:
        contribution["escaped_count"] += 1
        contribution["escaped_flux_lumen"] += flux_lumen
        lobe_contribution["escaped_count"] += 1
        lobe_contribution["escaped_flux_lumen"] += flux_lumen
    depth_entry = _depth_contribution(contribution["depths"], depth)
    if outcome == "receiver":
        depth_entry["receiver_hit_count"] += 1
        depth_entry["receiver_flux_lumen"] += outcome_flux_lumen
    elif outcome == "blocked":
        depth_entry["blocked_count"] += 1
        depth_entry["blocked_flux_lumen"] += flux_lumen
    elif outcome == "continued":
        depth_entry["continued_count"] += 1
        depth_entry["continued_flux_lumen"] += flux_lumen
    else:
        depth_entry["escaped_count"] += 1
        depth_entry["escaped_flux_lumen"] += flux_lumen


def _record_secondary_blocker_contribution(
    summary: RayTraceContributionSummary,
    contribution: Dict,
    lobe: str,
    blocked_flux_lumen: float,
    depth: int,
) -> None:
    contribution["secondary_block_count"] += 1
    contribution["secondary_blocked_flux_lumen"] += blocked_flux_lumen
    lobe_contribution = contribution["lobes"][lobe]
    lobe_contribution["blocked_count"] += 1
    lobe_contribution["blocked_flux_lumen"] += blocked_flux_lumen
    depth_entry = _depth_contribution(contribution["depths"], depth)
    depth_entry["secondary_block_count"] += 1
    depth_entry["secondary_blocked_flux_lumen"] += blocked_flux_lumen


def _finalize_surface_contributions(
    summary: RayTraceContributionSummary,
) -> None:
    summary.components = {}
    summary.materials = {}
    for face_contribution in summary.faces.values():
        _merge_depth_contributions(summary.depths, face_contribution["depths"])
        component_id = face_contribution["component_id"]
        material_id = face_contribution["material_id"]
        component = summary.components.setdefault(
            component_id,
            _empty_surface_contribution(component_id),
        )
        material = summary.materials.setdefault(
            material_id,
            _empty_surface_contribution(material_id),
        )
        _merge_surface_contribution(component, face_contribution)
        _merge_surface_contribution(material, face_contribution)


def _merge_depth_contributions(target: Dict[str, Dict], source: Dict[str, Dict]) -> None:
    for depth, source_depth in source.items():
        target_depth = _depth_contribution(target, int(depth))
        for field_name, value in source_depth.items():
            target_depth[field_name] += value


def _merge_surface_contribution(target: Dict, source: Dict) -> None:
    for field_name in (
        "surface_hit_count",
        "surface_incident_flux_lumen",
        "surface_reflectable_flux_lumen",
        "primary_hit_count",
        "incident_flux_lumen",
        "reflectable_flux_lumen",
        "reflection_emitted_count",
        "reflection_emitted_flux_lumen",
        "receiver_hit_count",
        "receiver_flux_lumen",
        "reflection_blocked_count",
        "reflection_blocked_flux_lumen",
        "continued_count",
        "continued_flux_lumen",
        "secondary_block_count",
        "secondary_blocked_flux_lumen",
        "escaped_count",
        "escaped_flux_lumen",
    ):
        target[field_name] += source[field_name]
    for lobe, source_lobe in source["lobes"].items():
        target_lobe = target["lobes"][lobe]
        for field_name, value in source_lobe.items():
            target_lobe[field_name] += value
    for depth, source_depth in source["depths"].items():
        target_depth = target["depths"].setdefault(depth, _empty_depth_contribution())
        for field_name, value in source_depth.items():
            target_depth[field_name] += value


def _record_direct_receiver_contribution(
    summary: RayTraceContributionSummary,
    candidate: ReceiverHitCandidate,
) -> None:
    receiver = summary.receivers[candidate.receiver_id]
    flux_lumen = candidate.received_power_lumen
    summary.direct_receiver_hit_count += 1
    summary.direct_receiver_flux_lumen += flux_lumen
    receiver["direct"]["hit_count"] += 1
    receiver["direct"]["flux_lumen"] += flux_lumen
    receiver["total"]["hit_count"] += 1
    receiver["total"]["flux_lumen"] += flux_lumen
    receiver_depth = _receiver_depth_contribution(receiver["depths"], 0)
    receiver_depth["hit_count"] += 1
    receiver_depth["flux_lumen"] += flux_lumen
    depth_entry = _depth_contribution(summary.depths, 0)
    depth_entry["receiver_hit_count"] += 1
    depth_entry["receiver_flux_lumen"] += flux_lumen


def _record_reflected_receiver_contribution(
    summary: RayTraceContributionSummary,
    candidate: ReceiverHitCandidate,
    lobe: str,
    depth: int,
) -> None:
    receiver = summary.receivers[candidate.receiver_id]
    flux_lumen = candidate.received_power_lumen
    summary.reflected_receiver_hit_count += 1
    summary.reflected_receiver_flux_lumen += flux_lumen
    receiver["reflected"]["hit_count"] += 1
    receiver["reflected"]["flux_lumen"] += flux_lumen
    receiver["total"]["hit_count"] += 1
    receiver["total"]["flux_lumen"] += flux_lumen
    receiver["lobes"][lobe]["hit_count"] += 1
    receiver["lobes"][lobe]["flux_lumen"] += flux_lumen
    receiver_depth = _receiver_depth_contribution(receiver["depths"], depth)
    receiver_depth["hit_count"] += 1
    receiver_depth["flux_lumen"] += flux_lumen


def _prepare_reflection_emission(
    rng: random.Random,
    incoming: Vec3,
    normal: Vec3,
    reflected_power_lumen: float,
    profile: OpticalProfile,
    config: RayTraceConfig,
    summary: Dict,
    depth: int,
) -> Optional[Tuple[ReflectionSample, float]]:
    if depth >= config.max_depth:
        summary["depth_limit_count"] += 1
        summary["reflection_disabled_count"] += 1
        return None
    summary["reflection_attempt_count"] += 1
    emitted_power_lumen = reflected_power_lumen
    if config.min_energy > 0.0 and reflected_power_lumen < config.min_energy:
        if config.termination_mode == "threshold":
            summary["reflection_below_energy_count"] += 1
            return None
        survival_probability = max(
            0.0,
            min(1.0, reflected_power_lumen / config.min_energy),
        )
        if rng.random() >= survival_probability:
            summary["reflection_below_energy_count"] += 1
            summary["roulette_terminated_count"] += 1
            return None
        summary["roulette_survived_count"] += 1
        emitted_power_lumen = config.min_energy
    reflection_sample = sample_reflection_direction(rng, incoming, normal, profile)
    if reflection_sample is None:
        summary["reflection_disabled_count"] += 1
        return None
    return reflection_sample, emitted_power_lumen


def _record_reflection_emission(
    summary: Dict,
    reflection_sample: ReflectionSample,
    reflected_power_lumen: float,
    depth: int,
) -> None:
    summary["reflection_emitted_count"] += 1
    lobe_summary = summary["lobes"][reflection_sample.lobe]
    lobe_summary["emitted_count"] += 1
    lobe_summary["emitted_flux_lumen"] += reflected_power_lumen
    depth_entry = _reflection_depth_summary(summary, depth)
    depth_entry["emitted_count"] += 1
    depth_entry["emitted_flux_lumen"] += reflected_power_lumen


def _record_reflection_outcome(
    summary: Dict,
    lobe: str,
    outcome: str,
    depth: int,
    received_power_lumen: float = 0.0,
) -> None:
    lobe_summary = summary["lobes"][lobe]
    if outcome == "receiver":
        summary["reflection_receiver_hit_count"] += 1
        summary["reflected_receiver_flux_lumen"] += received_power_lumen
        lobe_summary["receiver_hit_count"] += 1
        lobe_summary["receiver_flux_lumen"] += received_power_lumen
    elif outcome == "blocked":
        summary["reflection_blocked_count"] += 1
        lobe_summary["blocked_count"] += 1
    elif outcome == "continued":
        summary["reflection_continued_count"] += 1
        lobe_summary["continued_count"] += 1
    else:
        summary["reflection_escaped_count"] += 1
        lobe_summary["escaped_count"] += 1
    depth_entry = _reflection_depth_summary(summary, depth)
    if outcome == "receiver":
        depth_entry["receiver_hit_count"] += 1
        depth_entry["receiver_flux_lumen"] += received_power_lumen
    elif outcome == "blocked":
        depth_entry["blocked_count"] += 1
    elif outcome == "continued":
        depth_entry["continued_count"] += 1
    else:
        depth_entry["escaped_count"] += 1


def _surface_ray_hit(
    mesh: TriangleMesh,
    face_index: int,
    point: Vec3,
    normal: Vec3,
    distance_mm: float,
    incoming_power_lumen: float,
    outgoing_power_lumen: float,
    depth: int,
    optical_profile: Optional[OpticalProfile] = None,
    optical_source: Optional[str] = None,
    ray_kind: Optional[str] = None,
) -> RayHit:
    metadata = mesh.metadata(face_index)
    component_id = metadata.get("component_id")
    return RayHit(
        face_index=face_index,
        component_id=int(component_id) if component_id is not None else None,
        material_id=mesh.material_id(face_index) or None,
        point=point,
        normal=normal,
        distance_mm=distance_mm,
        incoming_energy_lumen=incoming_power_lumen,
        outgoing_energy_lumen=outgoing_power_lumen,
        depth=depth,
        event_type="surface",
        optical_profile_id=optical_profile.profile_id if optical_profile is not None else None,
        reflectance=optical_profile.reflectance if optical_profile is not None else None,
        scatter_model=optical_profile.scatter_model if optical_profile is not None else None,
        optical_assignment_source=optical_source,
        ray_kind=ray_kind,
    )


def _record_optical_summary(
    summary: Dict,
    profile: OpticalProfile,
    source: str,
    incoming_power_lumen: float,
    reflected_power_lumen: float,
) -> None:
    summary["surface_hit_count"] += 1
    if profile.profile_id == UNASSIGNED_PROFILE_ID:
        summary["unassigned_surface_hit_count"] += 1
    profile_hits = summary["profile_hits"]
    entry = profile_hits.setdefault(
        profile.profile_id,
        {
            "profile_id": profile.profile_id,
            "source": source,
            "hit_count": 0,
            "reflectance": profile.reflectance,
            "specular_ratio": profile.specular_ratio,
            "diffuse_ratio": profile.diffuse_ratio,
            "scatter_model": profile.scatter_model,
            "incoming_flux_lumen": 0.0,
            "potential_reflected_flux_lumen": 0.0,
        },
    )
    entry["hit_count"] += 1
    entry["incoming_flux_lumen"] += incoming_power_lumen
    entry["potential_reflected_flux_lumen"] += reflected_power_lumen


def _build_receiver_frame(receiver: ReceiverSpec) -> ReceiverFrame:
    columns, rows = receiver.resolution
    frame_fields = {
        "half_width": receiver.width_mm * 0.5,
        "half_height": receiver.height_mm * 0.5,
        "inverse_width": 1.0 / receiver.width_mm,
        "inverse_height": 1.0 / receiver.height_mm,
        "minimum_acceptance_cosine": math.cos(
            math.radians(receiver.acceptance_angle_deg)
        ),
        "columns": columns,
        "rows": rows,
    }
    if receiver.u_axis is not None and receiver.v_axis is not None:
        return ReceiverFrame(
            receiver=receiver,
            u_axis=vec_norm(receiver.u_axis),
            v_axis=vec_norm(receiver.v_axis),
            **frame_fields,
        )
    normal = vec_norm(receiver.normal)
    reference = (0.0, 0.0, 1.0)
    if abs(vec_dot(normal, reference)) > 0.95:
        reference = (0.0, 1.0, 0.0)
    u_axis = vec_norm(vec_cross(reference, normal))
    v_axis = vec_norm(vec_cross(normal, u_axis))
    return ReceiverFrame(
        receiver=receiver,
        u_axis=u_axis,
        v_axis=v_axis,
        **frame_fields,
    )


def _build_emitter_face_weights(mesh: TriangleMesh, face_indices: List[int]) -> List[Tuple[int, float]]:
    weighted: List[Tuple[int, float]] = []
    total_area = 0.0
    for face_index in face_indices:
        if face_index < 0 or face_index >= len(mesh.faces):
            continue
        area = max(0.0, mesh.area(face_index))
        if area <= 0.0:
            continue
        total_area += area
        weighted.append((face_index, total_area))
    if total_area <= 0.0:
        return []
    return [(face_index, cumulative / total_area) for face_index, cumulative in weighted]


def _choose_weighted_face(face_weights: List[Tuple[int, float]], rng: random.Random) -> Optional[int]:
    if not face_weights:
        return None
    value = rng.random()
    for face_index, cumulative in face_weights:
        if value <= cumulative:
            return face_index
    return face_weights[-1][0]


def _sample_face_emitter_ray(
    mesh: TriangleMesh,
    emitter: EmitterSpec,
    face_weights: List[Tuple[int, float]],
    rng: random.Random,
    epsilon_mm: float,
) -> Optional[Tuple[Vec3, Vec3, int]]:
    face_index = _choose_weighted_face(face_weights, rng)
    if face_index is None:
        return None
    a, b, c = mesh.face_vertices(face_index)
    r1 = rng.random()
    r2 = rng.random()
    sqrt_r1 = math.sqrt(r1)
    point = (
        (1.0 - sqrt_r1) * a[0] + sqrt_r1 * (1.0 - r2) * b[0] + sqrt_r1 * r2 * c[0],
        (1.0 - sqrt_r1) * a[1] + sqrt_r1 * (1.0 - r2) * b[1] + sqrt_r1 * r2 * c[1],
        (1.0 - sqrt_r1) * a[2] + sqrt_r1 * (1.0 - r2) * b[2] + sqrt_r1 * r2 * c[2],
    )
    normal = emitter.custom_normal if emitter.normal_mode == "custom" and emitter.custom_normal is not None else mesh.normal(face_index)
    normal = vec_norm(normal)
    if emitter.normal_flip:
        normal = vec_mul(normal, -1.0)
    direction = _sample_emitter_direction(rng, emitter, normal)
    origin = vec_add(point, vec_mul(normal, epsilon_mm))
    return origin, direction, face_index


def _sample_virtual_plane_emitter_ray(
    emitter: EmitterSpec,
    rng: random.Random,
    epsilon_mm: float,
) -> Optional[Tuple[Vec3, Vec3, int]]:
    if (
        emitter.center is None
        or emitter.u_axis is None
        or emitter.v_axis is None
        or emitter.width_mm is None
        or emitter.height_mm is None
    ):
        return None
    u_axis = vec_norm(emitter.u_axis)
    raw_v = vec_add(emitter.v_axis, vec_mul(u_axis, -vec_dot(emitter.v_axis, u_axis)))
    if math.sqrt(vec_dot(raw_v, raw_v)) <= 1e-12:
        return None
    v_axis = vec_norm(raw_v)
    normal = vec_norm(vec_cross(u_axis, v_axis))
    if emitter.normal_flip:
        normal = vec_mul(normal, -1.0)
    if emitter.surface_construction == "polygon_auto" and len(emitter.polygon_vertices) >= 3:
        point = _sample_polygon_point(emitter.polygon_vertices, rng)
        if point is None:
            return None
    else:
        u_offset = (rng.random() - 0.5) * emitter.width_mm
        v_offset = (rng.random() - 0.5) * emitter.height_mm
        point = vec_add(
            emitter.center,
            vec_add(vec_mul(u_axis, u_offset), vec_mul(v_axis, v_offset)),
        )
    direction = _sample_emitter_direction(rng, emitter, normal)
    origin = vec_add(point, vec_mul(normal, epsilon_mm))
    return origin, direction, -1


def _sample_polygon_point(vertices: List[Vec3], rng: random.Random) -> Optional[Vec3]:
    origin = vertices[0]
    weighted_triangles: List[Tuple[Vec3, Vec3, float]] = []
    total_area = 0.0
    for index in range(1, len(vertices) - 1):
        first = vertices[index]
        second = vertices[index + 1]
        cross = vec_cross(vec_add(first, vec_mul(origin, -1.0)), vec_add(second, vec_mul(origin, -1.0)))
        area = 0.5 * math.sqrt(vec_dot(cross, cross))
        if area <= 1e-12:
            continue
        total_area += area
        weighted_triangles.append((first, second, total_area))
    if total_area <= 1e-12:
        return None
    target = rng.random() * total_area
    first, second, _ = weighted_triangles[-1]
    for triangle_first, triangle_second, cumulative_area in weighted_triangles:
        if target <= cumulative_area:
            first, second = triangle_first, triangle_second
            break
    root = math.sqrt(rng.random())
    second_weight = root * rng.random()
    first_weight = root - second_weight
    origin_weight = 1.0 - root
    return (
        origin_weight * origin[0] + first_weight * first[0] + second_weight * second[0],
        origin_weight * origin[1] + first_weight * first[1] + second_weight * second[1],
        origin_weight * origin[2] + first_weight * first[2] + second_weight * second[2],
    )


def _sample_emitter_direction(rng: random.Random, emitter: EmitterSpec, normal: Vec3) -> Vec3:
    if emitter.direction_distribution == "isotropic":
        return random_unit_vector(rng)
    if emitter.direction_distribution == "gaussian":
        return _sample_gaussian_cone(rng, normal, emitter.gaussian_sigma_deg)
    return _sample_cosine_weighted_hemisphere(rng, normal)


def _orthonormal_basis(normal: Vec3) -> Tuple[Vec3, Vec3, Vec3]:
    w = vec_norm(normal)
    helper = (0.0, 0.0, 1.0)
    if abs(vec_dot(w, helper)) > 0.95:
        helper = (0.0, 1.0, 0.0)
    u = vec_norm(vec_cross(helper, w))
    v = vec_norm(vec_cross(w, u))
    return u, v, w


def _sample_cosine_weighted_hemisphere(rng: random.Random, normal: Vec3) -> Vec3:
    u_axis, v_axis, w_axis = _orthonormal_basis(normal)
    r1 = rng.random()
    r2 = rng.random()
    radius = math.sqrt(r1)
    phi = 2.0 * math.pi * r2
    x = radius * math.cos(phi)
    y = radius * math.sin(phi)
    z = math.sqrt(max(0.0, 1.0 - r1))
    return vec_norm(
        vec_add(
            vec_add(vec_mul(u_axis, x), vec_mul(v_axis, y)),
            vec_mul(w_axis, z),
        )
    )


def _sample_gaussian_cone(rng: random.Random, normal: Vec3, sigma_deg: float) -> Vec3:
    u_axis, v_axis, w_axis = _orthonormal_basis(normal)
    sigma_rad = math.radians(max(1e-6, sigma_deg))
    theta = abs(rng.gauss(0.0, sigma_rad))
    theta = min(theta, math.pi * 0.5)
    phi = rng.uniform(0.0, 2.0 * math.pi)
    sin_t = math.sin(theta)
    direction = vec_add(
        vec_add(vec_mul(u_axis, sin_t * math.cos(phi)), vec_mul(v_axis, sin_t * math.sin(phi))),
        vec_mul(w_axis, math.cos(theta)),
    )
    return vec_norm(direction)


def _emitter_ray_hit(source_face: int, origin: Vec3, direction: Vec3, power_lumen: float) -> RayHit:
    return RayHit(
        face_index=source_face,
        component_id=None,
        material_id=None,
        point=origin,
        normal=direction,
        distance_mm=0.0,
        incoming_energy_lumen=power_lumen,
        outgoing_energy_lumen=power_lumen,
        depth=0,
        event_type="emitter",
        ray_kind="direct",
    )


def _find_first_receiver_hit(
    origin: Vec3,
    direction: Vec3,
    power_lumen: float,
    source_face: int,
    receivers: List[ReceiverFrame],
    grids: Dict[str, ReceiverGrid],
    config: RayTraceConfig,
    depth: int = 0,
    ray_kind: str = "direct",
) -> Optional[ReceiverHitCandidate]:
    best_candidate: Optional[ReceiverHitCandidate] = None
    best_distance = float("inf")
    origin_x, origin_y, origin_z = origin
    direction_x, direction_y, direction_z = direction
    for frame in receivers:
        receiver = frame.receiver
        normal_x, normal_y, normal_z = receiver.normal
        denom = (
            direction_x * normal_x
            + direction_y * normal_y
            + direction_z * normal_z
        )
        if abs(denom) < 1e-12:
            continue
        center_x, center_y, center_z = receiver.center
        t = (
            (center_x - origin_x) * normal_x
            + (center_y - origin_y) * normal_y
            + (center_z - origin_z) * normal_z
        ) / denom
        if t <= config.epsilon_mm:
            continue
        if t >= best_distance:
            continue
        point_x = origin_x + direction_x * t
        point_y = origin_y + direction_y * t
        point_z = origin_z + direction_z * t
        local_x = point_x - center_x
        local_y = point_y - center_y
        local_z = point_z - center_z
        u_axis_x, u_axis_y, u_axis_z = frame.u_axis
        v_axis_x, v_axis_y, v_axis_z = frame.v_axis
        u = local_x * u_axis_x + local_y * u_axis_y + local_z * u_axis_z
        v = local_x * v_axis_x + local_y * v_axis_y + local_z * v_axis_z
        if (
            u < -frame.half_width
            or u > frame.half_width
            or v < -frame.half_height
            or v > frame.half_height
        ):
            continue
        cos_accept = max(
            0.0,
            -(
                direction_x * normal_x
                + direction_y * normal_y
                + direction_z * normal_z
            ),
        )
        if cos_accept < frame.minimum_acceptance_cosine:
            continue
        col = min(
            frame.columns - 1,
            max(
                0,
                int((u + frame.half_width) * frame.inverse_width * frame.columns),
            ),
        )
        row = min(
            frame.rows - 1,
            max(
                0,
                int((v + frame.half_height) * frame.inverse_height * frame.rows),
            ),
        )
        received_power = power_lumen * cos_accept
        best_distance = t
        best_candidate = ReceiverHitCandidate(
            grid=grids[receiver.receiver_id],
            row=row,
            column=col,
            received_power_lumen=received_power,
            point=(point_x, point_y, point_z),
            normal=receiver.normal,
            distance_mm=t,
            incoming_power_lumen=power_lumen,
            receiver_id=receiver.receiver_id,
            depth=depth,
            ray_kind=ray_kind,
        )

    return best_candidate


def _record_receiver_hit(candidate: ReceiverHitCandidate) -> None:
    candidate.grid.flux_lumen[candidate.row][candidate.column] += candidate.received_power_lumen
    candidate.grid.hit_count += 1


def _build_direct_metrics(grids: List[ReceiverGrid], config: RayTraceConfig) -> Dict[str, Dict[str, float]]:
    metrics: Dict[str, Dict[str, float]] = {}
    for grid in grids:
        values = [value for row in grid.flux_lumen for value in row]
        bin_area_m2 = grid.bin_area_mm2 * 1e-6
        nit_values = [
            config.k_abs * config.k_brdf * (flux / max(bin_area_m2, 1e-18)) / math.pi
            for flux in values
        ]
        sorted_nits = sorted(nit_values)
        peak = max(nit_values) if nit_values else 0.0
        mean = sum(nit_values) / float(len(nit_values)) if nit_values else 0.0
        if sorted_nits:
            p95_index = min(len(sorted_nits) - 1, int(math.ceil(0.95 * len(sorted_nits))) - 1)
            p95 = sorted_nits[p95_index]
        else:
            p95 = 0.0
        area_above_zero = sum(1 for value in values if value > 0.0) * grid.bin_area_mm2
        metrics[grid.receiver_id] = {
            "peak_nit_est": peak,
            "mean_nit_est": mean,
            "p95_nit_est": p95,
            "total_flux_lumen": sum(values),
            "hit_count": float(grid.hit_count),
            "area_above_zero_mm2": area_above_zero,
        }
    return metrics
    summary = RunResultSummary(
        run_id=run_id,
        total_rays=total_rays,
        hit_count=hit_count,
        max_depth=engine_input.config.max_depth,
        runtime_sec=runtime,
        metadata={
            "source_file": engine_input.source_file,
            "project": engine_input.project_name,
            "k_abs": engine_input.config.k_abs,
            "k_brdf": engine_input.config.k_brdf,
            "seed": engine_input.config.seed,
            "gap_rules": len(engine_input.gap_rules),
            "synthetic_geometry": engine_input.source_is_synthetic,
            "import_note": engine_input.import_note,
        },
    )
    return SimulationOutput(
        run_id=run_id,
        project_name=engine_input.project_name,
        source_file=engine_input.source_file,
        summary=summary,
        receiver_metrics=metrics,
        mesh_info={
            "face_count": len(engine_input.mesh.faces),
            "vertex_count": len(engine_input.mesh.vertices),
            "receiver_count": len(engine_input.receivers),
            "emitter_count": len(engine_input.emitters),
            "gap_applied": len(gap_samples),
        },
        emitter_count=len(engine_input.emitters),
        gap_rule_count=len(engine_input.gap_rules),
    )


def _trace_path(
    mesh: TriangleMesh,
    origin: Vec3,
    direction: Vec3,
    energy: float,
    max_depth: int,
    materials: Dict[str, MaterialProfile],
    rng: random.Random,
    gap_samples: Dict[int, GapSample],
    face_to_receiver: Dict[int, str],
    receiver_area: Dict[str, float],
    receiver_irradiance: Dict[str, float],
    receiver_hits: Dict[str, int],
) -> int:
    cur_origin = origin
    cur_dir = vec_norm(direction)
    cur_energy = energy
    hit_count = 0
    for depth in range(max_depth + 1):
        hit = mesh.intersect_ray(cur_origin, cur_dir)
        if hit is None:
            break
        face_idx = hit.face_index
        normal = hit.normal
        material_id = mesh.material_id(face_idx)
        material = materials.get(material_id)
        if material is None:
            break

        if face_idx in face_to_receiver:
            receiver_id = face_to_receiver[face_idx]
            dist2 = max(1e-6, hit.t * hit.t)
            cos_theta = clamp01(max(0.0, -vec_dot(cur_dir, normal)))
            area = max(1e-6, receiver_area.get(receiver_id, 1.0))
            irradiance = cur_energy * cos_theta / dist2 / area
            receiver_irradiance[receiver_id] += irradiance
            receiver_hits[receiver_id] += 1
            hit_count += 1
            return hit_count

        if face_idx in gap_samples:
            gap = gap_samples[face_idx]
            if rng.random() < gap.transmissive:
                cur_origin = vec_add(hit.point, vec_mul(cur_dir, 1e-4))
                cur_energy *= (gap.transmissive * 0.95 + 0.02)
                continue

        if depth >= max_depth:
            break

        reflect_ratio = max(0.0, material.reflectance_total - material.absorption_ratio)
        if reflect_ratio <= 0.0:
            break

        reflected = vec_reflect(cur_dir, normal)
        if material.roughness > 0.001:
            jitter_axis = _random_unit_on_hemisphere(rng, normal)
            reflected = vec_norm(vec_add(reflected, vec_mul(jitter_axis, material.roughness)))
        cur_origin = vec_add(hit.point, vec_mul(normal, 1e-4))
        cur_dir = vec_norm(reflected)
        cur_energy *= reflect_ratio
    return hit_count


def _emit_from_face(mesh: TriangleMesh, emitter: EmitterConfig, rng: random.Random):
    if emitter.face_index is None or emitter.face_index >= len(mesh.faces):
        return None
    a, b, c = mesh.face_vertices(emitter.face_index)
    u = math.sqrt(rng.random())
    v = rng.random() * (1.0 - u)
    p = (
        a[0] + (b[0] - a[0]) * u + (c[0] - a[0]) * v,
        a[1] + (b[1] - a[1]) * u + (c[1] - a[1]) * v,
        a[2] + (b[2] - a[2]) * u + (c[2] - a[2]) * v,
    )
    n = mesh.normal(emitter.face_index)
    d = _sample_direction(rng, emitter.direction_distribution, n, emitter.direction_mode)
    return p, d


def _emit_from_box(emitter: EmitterConfig, rng: random.Random):
    if emitter.box_min is None or emitter.box_max is None:
        return None
    xmin, ymin, zmin = emitter.box_min
    xmax, ymax, zmax = emitter.box_max
    p = (
        rng.uniform(xmin, xmax),
        rng.uniform(ymin, ymax),
        rng.uniform(zmin, zmax),
    )
    n_hint = emitter.normal_hint if emitter.normal_hint is not None else (0.0, 0.0, 1.0)
    d = _sample_direction(rng, emitter.direction_distribution, n_hint, emitter.direction_mode)
    return p, d


def _emit_from_sphere(emitter: EmitterConfig, rng: random.Random):
    if emitter.sphere_center is None or emitter.sphere_radius is None:
        return None
    center = emitter.sphere_center
    r = emitter.sphere_radius
    x, y, z = random_unit_vector(rng)
    p = (center[0] + x * r, center[1] + y * r, center[2] + z * r)
    n_hint = emitter.normal_hint if emitter.normal_hint is not None else (0.0, 1.0, 0.0)
    d = _sample_direction(rng, emitter.direction_distribution, n_hint, emitter.direction_mode)
    return p, d


def _sample_direction(rng: random.Random, distribution: str, normal: Vec3, mode: str) -> Vec3:
    if distribution == "uniform_toward_normal":
        return _random_unit_on_hemisphere(rng, normal)
    if distribution == "random_cosine":
        return _random_unit_on_hemisphere(rng, normal)
    if mode == "toward_receiver":
        return _random_unit_on_hemisphere(rng, normal)
    return random_unit_vector(rng)


def _random_unit_on_hemisphere(rng: random.Random, normal: Vec3) -> Vec3:
    vec = random_unit_vector(rng)
    if vec_dot(vec, normal) < 0.0:
        vec = (-vec[0], -vec[1], -vec[2])
    return vec


def _build_receiver_area(mesh: TriangleMesh, receivers: List[ReceiverPatchConfig]) -> Dict[str, float]:
    area: Dict[str, float] = {r.receiver_id: 0.0 for r in receivers}
    for receiver in receivers:
        total = 0.0
        for face_idx in receiver.face_indices:
            total += mesh.area(face_idx)
        area[receiver.receiver_id] = max(1e-6, total)
    return area


def _build_face_to_receiver_map(receivers: List[ReceiverPatchConfig]) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for receiver in receivers:
        for face_idx in receiver.face_indices:
            mapping[face_idx] = receiver.receiver_id
    return mapping


def _build_metrics(
    receiver_area: Dict[str, float],
    receiver_irradiance: Dict[str, float],
    receiver_hits: Dict[str, int],
    config: RunConfig,
) -> List[ReceiverMetrics]:
    metrics: List[ReceiverMetrics] = []
    p95_ratio = 0.95
    for receiver_id in sorted(receiver_area.keys()):
        area = receiver_area[receiver_id]
        irradiance = receiver_irradiance[receiver_id]
        hit_count = receiver_hits[receiver_id]
        luminance_rel = irradiance * config.k_brdf
        nits = luminance_rel * config.k_abs
        metrics.append(
            ReceiverMetrics(
                receiver_id=receiver_id,
                irradiance_sum=irradiance,
                peak_nit=nits,
                mean_nit=nits,
                p95_nit=nits * p95_ratio,
                area_mm2=area,
                area_above_threshold=max(0.0, min(area, area * clamp01(irradiance))),
                rays_hit=hit_count,
            )
        )
    return metrics
