"""Observer-side path discovery. Counts evidence, never estimates lumen/nit.

Reverse directions are proposals only. Every completed path is checked in
the physical light-to-observer direction, including measured BSDF support;
reciprocity of a measured table is deliberately not assumed.
"""
from __future__ import annotations

import math
import random
import time
from bisect import bisect_left

from .geometry import RayBatch, vec_add, vec_sub, vec_mul, vec_dot, vec_cross, vec_norm
from .reflection import ideal_specular_direction, orthonormal_basis, sample_cosine_weighted_hemisphere, sample_reflection_direction
from .bsdf_transport import incident_frame
from .aim_sampling import aim_sphere_rotation
from .escape_collection import DIRECTIONS

CONTRACT = "observer_path_support_v1"


def validate_settings(spec):
    if not isinstance(spec, dict):
        raise ValueError("Observer settings must be an object")
    for key, lo, hi in (("horizontal_deg", -89.9, 89.9), ("vertical_deg", -89.9, 89.9),
                        ("range_deg", 0, 10), ("step_deg", .1, 10),
                        ("resolution", 8, 256), ("samples_per_cell", 1, 16)):
        value = spec.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"Invalid observer {key}: expected {lo}..{hi}")
        if key in ("resolution", "samples_per_cell") and int(value) != value:
            raise ValueError(f"Observer {key} must be an integer")
    views = angles(spec)
    if len(views) > 121 or len(views) * spec["resolution"] ** 2 * spec["samples_per_cell"] * 2 > 500_000:
        raise ValueError("Observer budget exceeded: reduce angle range, resolution or samples")
    return views


def angles(spec):
    radius, step = spec["range_deg"], spec["step_deg"]
    # Always include the requested direction and range endpoints.
    offsets = {0., -radius, radius}
    offsets.update(i * step for i in range(-int(radius / step), int(radius / step) + 1))
    return sorted({(round(max(-89.9, min(89.9, spec["horizontal_deg"] + x)), 8),
                    round(max(-89.9, min(89.9, spec["vertical_deg"] + y)), 8)) for x in offsets for y in offsets})


def view_direction(axis, horizontal, vertical):
    # Rear -Z: positive horizontal is +X, positive vertical is +Y.
    w = DIRECTIONS[axis]
    up = (0., 1., 0.) if abs(w[1]) < .9 else (0., 0., 1.)
    right = vec_norm(vec_cross(w, up))
    up = vec_cross(right, w)
    h, v = math.radians(horizontal), math.radians(vertical)
    return vec_norm(tuple(w[i]*math.cos(h)*math.cos(v) + right[i]*math.sin(h)*math.cos(v) + up[i]*math.sin(v) for i in range(3)))


def reflection_supported(profile, normal, incoming, outgoing):
    if vec_dot(incoming, normal) > 0:
        normal = vec_mul(normal, -1)
    if vec_dot(outgoing, normal) <= 0:
        return False
    if profile.bsdf_asset_id:
        model = getattr(profile, "_bsdf_transport", None)
        if model is None:
            raise ValueError("Observer BSDF must be compiled; no analytic fallback")
        angle, x, y, n = incident_frame(incoming, normal)
        theta = math.acos(max(-1., min(1., vec_dot(outgoing, n))))
        phi = math.atan2(vec_dot(outgoing, y), vec_dot(outgoing, x))
        return model.reflectance(incoming, normal) > 0 and sum(weight * table.relative_value(theta, phi) for table, weight in model.mixture(angle)) > 0
    if profile.reflectance <= 0 or profile.scatter_model == "none":
        return False
    if profile.scatter_model == "lambertian":
        return True
    cosine = vec_dot(ideal_specular_direction(incoming, normal), outgoing)
    if profile.scatter_model == "specular":
        return cosine >= 1 - 1e-12
    if profile.scatter_model == "mixed" and profile.specular_ratio < 1:
        return True
    if profile.scatter_model == "gaussian":
        return cosine > 0
    return cosine > 0 and (profile.gaussian_sigma_deg > .01 or cosine >= 1 - 1e-12)


def emission_supported(emitter, point, normal, outgoing):
    aim = emitter.aim
    if aim is not None and aim.enabled:
        if aim.mode == "sphere":
            rotation = aim_sphere_rotation(aim)
            cosine = max(-1., min(1., vec_dot(outgoing, tuple(row[2] for row in rotation))))
            theta = math.degrees(math.acos(cosine))
            if aim.sphere_upper_deg == aim.sphere_lower_deg == 0:
                return cosine >= 1 - 1e-12
            return aim.sphere_upper_deg <= theta <= aim.sphere_lower_deg and (aim.distribution != "lambertian" or cosine > 0)
        n = vec_norm(vec_cross(aim.u_axis, aim.v_axis))
        denominator = vec_dot(outgoing, n)
        if abs(denominator) < 1e-15:
            return False
        distance = vec_dot(vec_sub(aim.center, point), n) / denominator
        if distance <= 0:
            return False
        relative = vec_sub(vec_add(point, vec_mul(outgoing, distance)), aim.center)
        u, v = vec_dot(relative, aim.u_axis), vec_dot(relative, aim.v_axis)
        return (u*u + v*v <= aim.radius_mm**2 if aim.shape == "circle" else abs(u) <= aim.width_mm/2 and abs(v) <= aim.height_mm/2)
    if emitter.direction_distribution == "isotropic" or emitter.emission_direction == "both":
        return True
    if emitter.normal_mode == "custom" and emitter.custom_normal is not None:
        normal = vec_norm(emitter.custom_normal)
    if emitter.normal_flip:
        normal = vec_mul(normal, -1)
    return vec_dot(normal, outgoing) > 0


def _triangle_intersection(origin, direction, triangle, epsilon):
    a, b, c = triangle
    e1, e2 = vec_sub(b, a), vec_sub(c, a)
    p = vec_cross(direction, e2)
    det = vec_dot(e1, p)
    if abs(det) < 1e-14:
        return None
    tvec = vec_sub(origin, a)
    u = vec_dot(tvec, p) / det
    q = vec_cross(tvec, e1)
    v = vec_dot(direction, q) / det
    t = vec_dot(e2, q) / det
    return t if u >= -1e-10 and v >= -1e-10 and u+v <= 1+1e-10 and t >= epsilon else None


def _source_geometry(mesh, emitters):
    sources = []
    for emitter in emitters:
        power = emitter.power_lumen if emitter.power_mode == "total" else emitter.luminance_nit if emitter.power_mode == "set_luminance" else emitter.power_density_lm_per_m2
        if not emitter.enabled or power <= 0:
            continue
        if emitter.emitter_type == "face":
            triangles = [(mesh.face_vertices(i), mesh.normal(i), i) for i in emitter.face_indices if 0 <= i < len(mesh.faces)]
        else:
            u, v = vec_norm(emitter.u_axis), vec_norm(emitter.v_axis)
            v = vec_norm(vec_sub(v, vec_mul(u, vec_dot(u, v))))
            n = vec_norm(vec_cross(u, v))
            if emitter.surface_construction == "polygon_auto":
                points = emitter.polygon_vertices
            else:
                points = [vec_add(emitter.center, vec_add(vec_mul(u, su*emitter.width_mm/2), vec_mul(v, sv*emitter.height_mm/2))) for su, sv in ((-1,-1),(1,-1),(1,1),(-1,1))]
            triangles = [((points[0], points[i], points[i+1]), n, None) for i in range(1, len(points)-1)]
        weights, total = [], 0.
        for triangle, normal, face in triangles:
            area = math.sqrt(vec_dot(vec_cross(vec_sub(triangle[1], triangle[0]), vec_sub(triangle[2], triangle[0])), vec_cross(vec_sub(triangle[1], triangle[0]), vec_sub(triangle[2], triangle[0]))))/2
            total += area
            weights.append(total)
        if total > 0:
            sources.append((emitter, triangles, weights, total))
    return sources


def _sample_source(sources, index, rng):
    emitter, triangles, weights, total = sources[index % len(sources)]
    draw = rng.random()*total
    selected = min(bisect_left(weights, draw), len(weights)-1)
    triangle, normal, face = triangles[selected]
    root, fraction = math.sqrt(rng.random()), rng.random()
    point = tuple((1-root)*triangle[0][i]+root*(1-fraction)*triangle[1][i]+root*fraction*triangle[2][i] for i in range(3))
    return emitter, point, normal, face


def trace_observer_paths(trace_input, collector, resolved, should_stop=None, *, dispatch="batch", batch_size=8192):
    started = time.perf_counter()
    if dispatch not in ("batch", "scalar") or not isinstance(batch_size, int) or not 1 <= batch_size <= 8192:
        raise ValueError("Invalid observer dispatch/batch size")
    from .raytracer import _IntersectionDispatchStats
    requested = getattr(trace_input.config, "compute_backend", "cpu")
    if requested == "gpu_cuda" and dispatch == "scalar":
        raise ValueError("GPU observer requires batch dispatch")
    if requested == "gpu_cuda" and trace_input.config.intersection_backend == "brute_force":
        raise ValueError("GPU observer requires BVH")
    stats = _IntersectionDispatchStats(intersection_backend=trace_input.config.intersection_backend,
        requested_provider="gpu_cuda" if requested == "gpu_cuda" else "python_cpu")
    spec = trace_input.observer_probe
    views = validate_settings(spec)
    mesh, config = trace_input.mesh, trace_input.config
    epsilon = max(config.epsilon_mm, 1e-8)
    sources = _source_geometry(mesh, trace_input.emitters)
    base_samples = int(spec["resolution"])**2 * int(spec["samples_per_cell"])
    planned_count = len(views)*(base_samples + (max(base_samples, len(sources)) if sources else 0))
    if planned_count > 500_000:
        raise ValueError("Observer budget exceeded by source count")
    face_sources = {}
    virtual = []
    for source in sources:
        emitter, triangles, _, _ = source
        for triangle, normal, face in triangles:
            if face is None or mesh.metadata(face).get("trace_excluded", False):
                virtual.append((emitter, triangle, normal))
            else:
                face_sources.setdefault(face, []).append(emitter)
    corners = [tuple(collector.maximum[i] if bits & (1 << i) else collector.minimum[i] for i in range(3)) for bits in range(8)]
    center = tuple((collector.minimum[i]+collector.maximum[i])/2 for i in range(3))
    cells, summaries = [], []
    attempted = direct = reflected = excluded = depth_limited = 0
    stopped = False
    resolution, passes = int(spec["resolution"]), int(spec["samples_per_cell"])

    def supported(vertices, source_point, launch):
        for i, (point, normal, profile) in enumerate(vertices):
            lightward = source_point if i == len(vertices)-1 else vertices[i+1][0]
            observerward = launch if i == 0 else vertices[i-1][0]
            if not reflection_supported(profile, normal, vec_norm(vec_sub(point, lightward)), vec_norm(vec_sub(observerward, point))):
                return False
        return True

    for view_index, (horizontal, vertical) in enumerate(views):
        outward = view_direction(collector.direction, horizontal, vertical)
        u, v, _ = orthonormal_basis(outward)
        projected_u = [vec_dot(vec_sub(p, center), u) for p in corners]
        projected_v = [vec_dot(vec_sub(p, center), v) for p in corners]
        distance = max(vec_dot(vec_sub(p, center), outward) for p in corners) + max(1., epsilon*10)
        width, height = max(projected_u)-min(projected_u), max(projected_v)-min(projected_v)
        view_cells = {}
        counts = {"horizontal_deg": horizontal, "vertical_deg": vertical, "direct_count": 0, "reflected_count": 0,
                  "spacing_mm": [width/resolution, height/resolution]}
        grid_samples = resolution*resolution*passes
        targeted_samples = max(grid_samples, len(sources)) if sources else 0
        def probe(sample_index):
            nonlocal attempted, depth_limited
            attempted += 1
            rng = random.Random(1729 + view_index*1_000_003 + sample_index*9176)
            cell_index = sample_index // passes
            row, column = divmod(cell_index, resolution)
            # First pass covers cell centres; extra passes independently jitter.
            jx, jy = (.5, .5) if sample_index % passes == 0 else (rng.random(), rng.random())
            launch = vec_add(vec_add(vec_add(center, vec_mul(outward, distance)), vec_mul(u, min(projected_u)+(column+jx)*width/resolution)), vec_mul(v, min(projected_v)+(row+jy)*height/resolution))
            if sample_index >= grid_samples:
                # Source projections are essential at extreme angles: even a
                # large emitter becomes a sub-pixel sliver near 90 degrees.
                _, target, _, _ = _sample_source(sources, sample_index-grid_samples, rng)
                launch = vec_add(target, vec_mul(outward, distance-vec_dot(vec_sub(target, center), outward)))
                relative = vec_sub(target, center)
                column = max(0, min(resolution-1, int((vec_dot(relative, u)-min(projected_u))/max(width, 1e-12)*resolution)))
                row = max(0, min(resolution-1, int((vec_dot(relative, v)-min(projected_v))/max(height, 1e-12)*resolution)))
            origin, direction = launch, vec_mul(outward, -1)
            vertices = []
            first_point = None
            found = None
            for depth in range(config.max_depth+1):
                hit = yield (origin, direction, epsilon, math.inf)
                closest = hit.t if hit is not None else math.inf
                source_hit = None
                for emitter, triangle, normal in virtual:
                    t = _triangle_intersection(origin, direction, triangle, epsilon)
                    if t is not None and t <= closest + 1e-9:
                        point = vec_add(origin, vec_mul(direction, t))
                        if emission_supported(emitter, point, normal, vec_mul(direction, -1)):
                            closest, source_hit = t, point
                if source_hit is not None:
                    if supported(vertices, source_hit, launch):
                        found = (first_point or source_hit, depth)
                    break
                if hit is None:
                    break
                first_point = first_point or hit.point
                for emitter in face_sources.get(hit.face_index, []):
                    # Hit normals face the reverse ray; emission uses the CAD
                    # winding normal, exactly like forward Face sampling.
                    if emission_supported(emitter, hit.point, mesh.normal(hit.face_index), vec_mul(direction, -1)) and supported(vertices, hit.point, launch):
                        found = (first_point, depth)
                        break
                if found is not None:
                    break
                if depth >= config.max_depth:
                    depth_limited += 1
                    break
                profile = resolved[hit.face_index].profile
                # Source connection at every bounce supplements random continuation.
                if sources:
                    emitter, point, normal, face = _sample_source(sources, sample_index+depth, rng)
                    source_distance = math.sqrt(vec_dot(vec_sub(point, hit.point), vec_sub(point, hit.point)))
                    if source_distance <= 4*epsilon:
                        break
                    toward_source = vec_norm(vec_sub(point, hit.point))
                    block = yield (vec_add(hit.point, vec_mul(toward_source, epsilon)), toward_source, epsilon, source_distance-2*epsilon)
                    proposal = vertices + [(hit.point, hit.normal, profile)]
                    if block is None and emission_supported(emitter, point, normal, vec_mul(toward_source, -1)) and supported(proposal, point, launch):
                        found = (first_point, depth+1)
                        break
                normal = hit.normal if vec_dot(direction, hit.normal) < 0 else vec_mul(hit.normal, -1)
                if not profile.bsdf_asset_id and (profile.reflectance <= 0 or profile.scatter_model == "none"):
                    break
                # A mixture of delta and broad proposals avoids assuming BSDF reciprocity.
                if profile.scatter_model == "specular" and not profile.bsdf_asset_id:
                    new_direction = ideal_specular_direction(direction, normal)
                else:
                    proposal = rng.random()
                    if proposal < 1/3:
                        new_direction = ideal_specular_direction(direction, normal)
                    elif proposal < 2/3:
                        sample = sample_reflection_direction(rng, direction, normal, profile)
                        new_direction = sample.direction if sample is not None else sample_cosine_weighted_hemisphere(rng, normal)
                    else:
                        new_direction = sample_cosine_weighted_hemisphere(rng, normal)
                vertices.append((hit.point, hit.normal, profile))
                origin, direction = vec_add(hit.point, vec_mul(new_direction, epsilon)), new_direction
            return found, row, column, launch

        total_samples = grid_samples + targeted_samples
        for chunk_start in range(0, total_samples, batch_size):
            if should_stop is not None and should_stop():
                stopped = True
                break
            probes = [probe(i) for i in range(chunk_start, min(total_samples, chunk_start+batch_size))]
            completed, interrupted = _execute_probes(mesh, probes, stats, dispatch, should_stop)
            stopped = stopped or interrupted
            for item in completed:
                if item is None:
                    continue
                found, row, column, launch = item
                if found is None:
                    continue
                point, depth = found
                if collector._allowed(point, outward, launch):
                    excluded += 1
                    continue
                key = (row, column)
                cell = view_cells.setdefault(key, {"view_index": view_index, "row": row, "column": column, "position_sum": [0.,0.,0.], "direct_count": 0, "reflected_count": 0,
                                                   "min_depth": depth, "max_depth": depth})
                kind = "direct_count" if depth == 0 else "reflected_count"
                cell[kind] += 1
                counts[kind] += 1
                direct += int(depth == 0)
                reflected += int(depth > 0)
                cell["min_depth"], cell["max_depth"] = min(cell["min_depth"], depth), max(cell["max_depth"], depth)
                for i in range(3):
                    cell["position_sum"][i] += point[i]
            if stopped:
                break
        for cell in view_cells.values():
            cell["position"] = [x/(cell["direct_count"]+cell["reflected_count"]) for x in cell.pop("position_sum")]
            cells.append(cell)
        counts.update({"direction": outward, "u_axis": u, "v_axis": v,
                       "observer_center": vec_add(center, vec_mul(outward, distance))})
        summaries.append(counts)
        if stopped:
            break
    performance = stats.to_summary()
    state, reason = stats.compute_execution_diagnosis(gpu_compute_requested=requested == "gpu_cuda")
    performance.update(compute_execution_state=state, compute_execution_reason=reason)
    return {"contract": CONTRACT, "sample_contract": "observer_per_ray_seed_batch_v1",
            "execution_backend": "gpu_cuda" if state in ("gpu_active", "gpu_mixed") else "cpu",
            "requested_backend": requested, "performance": performance, "measure": "path_support_counts",
            "settings": spec, "attempted_count": attempted, "planned_count": planned_count,
            "direct_count": direct, "reflected_count": reflected, "excluded_count": excluded,
            "depth_limited_count": depth_limited, "stopped": stopped, "complete": not stopped,
            "runtime_sec": time.perf_counter()-started, "views": summaries, "cells": cells}


def _execute_probes(mesh, probes, stats, dispatch, should_stop):
    """Wavefront scheduler for primary, continuation AND source-shadow rays.

    Per-probe RNG is private and results reduce in primary order, so batch
    grouping, GPU scheduling and early path termination never change samples.
    """
    completed = [None] * len(probes)
    active = []
    try:
        for index, probe in enumerate(probes):
            try:
                active.append((index, probe, next(probe)))
            except StopIteration as done:
                completed[index] = done.value
        while active:
            if should_stop is not None and should_stop():
                return completed, True
            if dispatch == "scalar":
                hits = [stats.intersect_scalar(mesh, query[0], query[1], min_t=query[2], max_t=query[3]) for _, _, query in active]
            else:
                queries = [query for _, _, query in active]
                rays = RayBatch(origins=[q[0] for q in queries], directions=[q[1] for q in queries],
                    min_t=[q[2] for q in queries], max_t=[q[3] for q in queries])
                batch = stats.intersect_batch(mesh, rays, allow_hybrid_cpu=False)
                hits = [batch.materialize(mesh, rays, i) for i in range(len(active))]
            following = []
            for (index, probe, _), hit in zip(active, hits):
                try:
                    following.append((index, probe, probe.send(hit)))
                except StopIteration as done:
                    completed[index] = done.value
            active = following
        return completed, False
    finally:
        for probe in probes:
            probe.close()
