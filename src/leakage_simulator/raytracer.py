from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
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
from .types import EmitterSpec, OpticalProfile, RayHit, RayTraceConfig, RayTraceResult, ReceiverGrid, ReceiverSpec
from .types import SimulationOutput, RunResultSummary, random_unit_vector
from .gap import GapSample, sample_gap_profiles


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
    project_name: str = "TV-Leakage-RT1"


@dataclass
class ReceiverFrame:
    receiver: ReceiverSpec
    u_axis: Vec3
    v_axis: Vec3


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


def run_direct_ray_trace(trace_input: DirectRayTraceInput) -> RayTraceResult:
    start_time = time.time()
    rng = random.Random(trace_input.config.seed)
    receiver_frames = [_build_receiver_frame(receiver) for receiver in trace_input.receivers if receiver.enabled]
    receiver_grids = {
        receiver.receiver_id: ReceiverGrid.empty(receiver)
        for receiver in trace_input.receivers
        if receiver.enabled
    }
    stored_paths: List[List[RayHit]] = []
    total_rays = 0
    receiver_hit_count = 0
    terminated_ray_count = 0

    for emitter in trace_input.emitters:
        if not emitter.enabled:
            continue
        emitter_rng = random.Random(emitter.seed if emitter.seed is not None else rng.randint(0, 2**31 - 1))
        face_weights = _build_emitter_face_weights(trace_input.mesh, emitter.face_indices)
        ray_power = emitter.power_lumen / float(emitter.ray_count)
        for _ in range(emitter.ray_count):
            total_rays += 1
            ray = _sample_face_emitter_ray(trace_input.mesh, emitter, face_weights, emitter_rng, trace_input.config.epsilon_mm)
            if ray is None:
                terminated_ray_count += 1
                continue
            origin, direction, source_face = ray
            hit = _first_receiver_hit(
                origin=origin,
                direction=direction,
                power_lumen=ray_power,
                source_face=source_face,
                receivers=receiver_frames,
                grids=receiver_grids,
                config=trace_input.config,
            )
            if hit is None:
                terminated_ray_count += 1
                continue
            receiver_hit_count += 1
            if trace_input.config.store_ray_paths and len(stored_paths) < trace_input.config.max_stored_paths:
                stored_paths.append([hit])

    grids = [receiver_grids[receiver.receiver_id] for receiver in trace_input.receivers if receiver.enabled]
    metrics = _build_direct_metrics(grids, trace_input.config)
    return RayTraceResult(
        run_id=fresh_run_id("rt1"),
        config=trace_input.config,
        emitters=trace_input.emitters,
        receivers=trace_input.receivers,
        receiver_grids=grids,
        optical_profiles=trace_input.optical_profiles,
        total_rays=total_rays,
        receiver_hit_count=receiver_hit_count,
        surface_hit_count=0,
        terminated_ray_count=terminated_ray_count,
        runtime_sec=time.time() - start_time,
        stored_paths=stored_paths,
        metrics=metrics,
    )


def _build_receiver_frame(receiver: ReceiverSpec) -> ReceiverFrame:
    normal = vec_norm(receiver.normal)
    reference = (0.0, 0.0, 1.0)
    if abs(vec_dot(normal, reference)) > 0.95:
        reference = (0.0, 1.0, 0.0)
    u_axis = vec_norm(vec_cross(reference, normal))
    v_axis = vec_norm(vec_cross(normal, u_axis))
    return ReceiverFrame(receiver=receiver, u_axis=u_axis, v_axis=v_axis)


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


def _first_receiver_hit(
    origin: Vec3,
    direction: Vec3,
    power_lumen: float,
    source_face: int,
    receivers: List[ReceiverFrame],
    grids: Dict[str, ReceiverGrid],
    config: RayTraceConfig,
) -> Optional[RayHit]:
    best_hit: Optional[RayHit] = None
    best_grid_cell: Optional[Tuple[ReceiverGrid, int, int, float]] = None
    for frame in receivers:
        receiver = frame.receiver
        denom = vec_dot(direction, receiver.normal)
        if abs(denom) < 1e-12:
            continue
        t = vec_dot(vec_sub(receiver.center, origin), receiver.normal) / denom
        if t <= config.epsilon_mm:
            continue
        point = vec_add(origin, vec_mul(direction, t))
        local = vec_sub(point, receiver.center)
        u = vec_dot(local, frame.u_axis)
        v = vec_dot(local, frame.v_axis)
        half_width = receiver.width_mm * 0.5
        half_height = receiver.height_mm * 0.5
        if u < -half_width or u > half_width or v < -half_height or v > half_height:
            continue
        cos_accept = max(0.0, -vec_dot(direction, receiver.normal))
        min_cos = math.cos(math.radians(receiver.acceptance_angle_deg))
        if cos_accept < min_cos:
            continue
        if best_hit is not None and t >= best_hit.distance_mm:
            continue
        columns, rows = receiver.resolution
        col = min(columns - 1, max(0, int(((u + half_width) / receiver.width_mm) * columns)))
        row = min(rows - 1, max(0, int(((v + half_height) / receiver.height_mm) * rows)))
        received_power = power_lumen * cos_accept
        best_hit = RayHit(
            face_index=-1,
            component_id=None,
            material_id=None,
            point=point,
            normal=receiver.normal,
            distance_mm=t,
            incoming_energy_lumen=power_lumen,
            outgoing_energy_lumen=0.0,
            depth=0,
            event_type="receiver",
            receiver_id=receiver.receiver_id,
        )
        best_grid_cell = (grids[receiver.receiver_id], row, col, received_power)

    if best_hit is None or best_grid_cell is None:
        return None
    grid, row, col, received_power = best_grid_cell
    grid.flux_lumen[row][col] += received_power
    grid.hit_count += 1
    return best_hit


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
