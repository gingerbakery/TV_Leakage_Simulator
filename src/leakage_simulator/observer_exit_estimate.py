"""Sampled directional exit estimates, never exterior-shell certification.

First CAD hits in opposed rings around a clear observer segment estimate a
local exterior membrane. Two scales must agree. This supports curved covers
and open seams without authored face tags; ambiguous support stays unresolved.
No estimated point contributes optical energy or changes a traced path.
"""
import math
import statistics
import time

import numpy as np

from .geometry import RayBatch, vec_dot, vec_norm, vec_sub
from .reflection import orthonormal_basis

CONTRACT = "automatic_exit_estimate_v1"
ANGLES = 16


def _ring_estimate(distances, length, radius):
    """Use the frontmost supported layer, never skip an unsupported front layer
    to label a deeper internal layer as exterior. Missing opposed sides fail.
    """
    depths = [length-t if math.isfinite(t) else None for t in distances]
    tolerance = max(.02, radius*.25)
    pairs = []
    for i in range(ANGLES//2):
        a, b = depths[i], depths[i+ANGLES//2]
        if a is not None and b is not None and abs(a-b) <= radius*2:
            pairs.append(((a+b)/2, i))
    if not pairs:
        return None
    # An isolated protrusion nearer the observer makes this scale ambiguous.
    front = max(depth for depth in depths if depth is not None)
    mids = [depth for depth, _ in pairs]
    center = max(mids)
    selected = [(depth, angle) for depth, angle in pairs if abs(depth-center) <= tolerance]
    if len(selected) < 4 or front-center > radius*1.5:
        return None
    center = statistics.median(depth for depth, _ in selected)
    if center <= max(.01, radius*.25) or center >= length-.01:
        return None
    spread = max(abs(depth-center) for depth, _ in selected)
    return dict(depth=center, spread=spread, pairs=len(selected), radius=radius)


def estimate_observer_exits(mesh, summary, *, compute_backend="cpu", budget_sec=10., max_queries=2_000_000):
    started = time.perf_counter()
    cells = summary.get("cells", [])
    bounded = [cell.pop("aperture", None) for cell in cells]
    attributed = query_count = successes = cpu_fallbacks = 0
    reason = "automatic_estimate_only"
    eligible = [i for i, cell in enumerate(cells) if cell.get("path_validation", {}).get("status") == "valid"
                and len(cell.get("representative_path", [])) >= 2]
    # Bound temporary memory; do not duplicate a multi-million-vertex mesh.
    minimum, maximum = np.full(3, math.inf), np.full(3, -math.inf)
    for offset in range(0, len(mesh.vertices) if eligible else 0, 65536):
        if time.perf_counter()-started >= budget_sec:
            reason = "exit_estimation_budget_exceeded"
            eligible = []
            break
        vertices = np.asarray(mesh.vertices[offset:offset+65536], dtype=float)
        minimum = np.minimum(minimum, vertices.min(axis=0))
        maximum = np.maximum(maximum, vertices.max(axis=0))
    extent = float(np.max(maximum-minimum)) if np.all(np.isfinite(minimum)) else 0.
    maximum_radius = min(25., max(.1, extent*.25))
    base_radius = min(.5, maximum_radius/8)
    radii = [base_radius*2**i for i in range(7) if base_radius*2**i <= maximum_radius*(1+1e-9)]
    angles = [2*math.pi*i/ANGLES for i in range(ANGLES)]

    def intersect(rays):
        nonlocal successes, cpu_fallbacks
        if compute_backend == "gpu_cuda":
            hits, execution = mesh.intersect_rays_gpu_cuda(rays, backend="bvh")
            if getattr(execution, "cpu_fallback", False):
                raise RuntimeError("exit_estimate_cuda_fallback")
        else:
            try:
                hits, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
            except Exception:
                cpu_fallbacks += 1
                hits = mesh.intersect_rays(rays, backend="bvh")
        successes += 1
        return hits

    for batch_start in range(0, len(eligible), 32):
        if time.perf_counter()-started >= budget_sec:
            reason = "exit_estimation_budget_exceeded"; break
        indices = eligible[batch_start:batch_start+32]
        origins, directions, distances, owners = [], [], [], []
        frames = {}
        for index in indices:
            start, end = cells[index]["representative_path"][-2:]
            delta = vec_sub(end, start)
            length = math.sqrt(vec_dot(delta, delta))
            if length <= .02:
                continue
            outward = vec_norm(delta)
            u, v, _ = orthonormal_basis(outward)
            frames[index] = (start, end, outward, length)
            for radius in radii:
                for angle in angles:
                    offset = tuple(radius*(u[k]*math.cos(angle)+v[k]*math.sin(angle)) for k in range(3))
                    origins.append(tuple(end[k]+offset[k] for k in range(3)))
                    directions.append(tuple(-x for x in outward))
                    distances.append(length-.001)
                    owners.append((index, radius))
        if query_count+len(origins) > max_queries:
            reason = "exit_estimation_budget_exceeded"; break
        if not origins:
            continue
        query_count += len(origins)
        try:
            hits = intersect(RayBatch(origins=origins, directions=directions, min_t=1e-5, max_t=distances))
        except Exception:
            reason = "exit_estimation_cuda_failed" if compute_backend == "gpu_cuda" else "exit_estimation_intersection_failed"
            break  # No implicit GPU-to-CPU success or invented marker.
        rings = {}
        for j, owner in enumerate(owners):
            rings.setdefault(owner, []).append(float(hits.t[j]) if hits.face_indices[j] >= 0 else math.inf)
        for index, (start, end, outward, length) in frames.items():
            estimates = [_ring_estimate(rings[index, radius], length, radius) for radius in radii]
            accepted = []
            for first, second in zip(estimates, estimates[1:]):
                if first is None or second is None:
                    continue
                change = abs(first["depth"]-second["depth"])
                if change <= max(.02, first["radius"]*.15):
                    accepted.append((first, max(.01, change, first["spread"], second["spread"])))
            if not accepted:
                continue
            best, uncertainty = max(accepted, key=lambda item: item[0]["depth"])
            # A supported larger scale closer to the observer vetoes a deeper
            # layer, even if the outer layer itself has not converged.
            contradicted = False
            for radius in radii:
                distances_at_radius = rings[index, radius]
                for angle in range(ANGLES//2):
                    a, b = distances_at_radius[angle], distances_at_radius[angle+ANGLES//2]
                    if math.isfinite(a) and math.isfinite(b) and abs(a-b) <= radius*2:
                        opposed_depth = length-(a+b)/2
                        if opposed_depth > best["depth"]+max(.02, uncertainty*2, radius*.15):
                            contradicted = True
            if contradicted:
                continue
            position = [start[k]+best["depth"]*outward[k] for k in range(3)]
            previous = bounded[index]
            planar_match = previous and math.dist(position, previous["position"]) <= max(.02, uncertainty*2)
            if planar_match:
                position = previous["position"]
                identity = previous["id"]
            else:
                # Spatial grouping is deliberately local, not a claim that an
                # entire curved seam is one topological aperture.
                step = max(.1, best["radius"])
                identity = "estimated:"+":".join(str(round(x/step)) for x in position)+f":{step:.6g}"
            cells[index]["aperture"] = dict(contract=CONTRACT, id=identity, position=position,
                uncertainty_mm=uncertainty, support_radius_mm=best["radius"], support_pairs=best["pairs"],
                method="planar_with_external_support" if planar_match else "opposed_neighborhood_multiscale",
                scope="sampled_directional_exit_not_global_shell_topology", exterior_shell_certified=False)
            attributed += 1
    summary["aperture_summary"] = dict(contract=CONTRACT, attributed=attributed, unresolved=len(cells)-attributed,
        reason=reason, runtime_sec=time.perf_counter()-started, query_count=query_count,
        compute_backend=compute_backend, successful_batches=successes, cpu_fallback_batches=cpu_fallbacks,
        gpu_successful_batches=successes if compute_backend == "gpu_cuda" else 0,
        exterior_shell_certified=False)
