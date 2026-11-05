"""Independent CPU geometry checks of displayed observer representative paths.

This certifies a path and a visible surface/emitter, never invents a hole or an
external shell crossing. In particular a virtual Datum emitter is NOT a CAD
surface and a model bounding-box intersection is NOT an aperture.
"""
import math
import time

import numpy as np

from .geometry import RayBatch, vec_sub, vec_dot, vec_norm, vec_mul
from .observer_probe import _source_geometry, emission_supported
from .reflection import ideal_specular_direction


def validate_observer_paths(trace, summary, resolved=None):
    started = time.perf_counter()
    mesh = trace.mesh
    epsilon = max(trace.config.epsilon_mm, 1e-8)
    tolerance = max(epsilon*4, 1e-7)
    sources = _source_geometry(mesh, trace.emitters)
    triangles = [(triangle, normal, source, face) for source in sources for triangle, normal, face in source[1]]
    a = np.array([t[0][0] for t in triangles], dtype=float).reshape(-1, 3)
    e1 = np.array([np.subtract(t[0][1], t[0][0]) for t in triangles], dtype=float).reshape(-1, 3)
    e2 = np.array([np.subtract(t[0][2], t[0][0]) for t in triangles], dtype=float).reshape(-1, 3)
    cross = np.cross(e1, e2)
    cross_length = np.linalg.norm(cross, axis=1)
    aa, bb, ab = np.sum(e1*e1, axis=1), np.sum(e2*e2, axis=1), np.sum(e1*e2, axis=1)
    denominator = aa*bb-ab*ab

    def on_source(point, outgoing):
        if not len(a):
            return None
        delta = np.asarray(point)-a
        plane = np.abs(np.sum(delta*cross, axis=1))/np.maximum(cross_length, 1e-30)
        d1, d2 = np.sum(delta*e1, axis=1), np.sum(delta*e2, axis=1)
        u = np.divide(bb*d1-ab*d2, denominator, out=np.full_like(d1, -1.), where=denominator > 0)
        v = np.divide(aa*d2-ab*d1, denominator, out=np.full_like(d1, -1.), where=denominator > 0)
        virtual_source = None
        for i in np.flatnonzero((plane <= tolerance) & (u >= -1e-9) & (v >= -1e-9) & (u+v <= 1+1e-9)):
            _, normal, source, face = triangles[i]
            if summary.get("measure") == "luminance_cd_m2":
                from .observer_radiance import emission_radiance
                supported = emission_radiance(source, point, normal, outgoing) > 0
            else:
                supported = emission_supported(source[0], point, normal, outgoing)
            if supported:
                if face is not None and not mesh.metadata(face).get("roi_emitter_id"):
                    return ("cad_surface", face)
                virtual_source = ("virtual_emitter", None)
        return virtual_source

    checked = valid = invalid = missing = virtual = native_batches = reference_batches = 0
    reasons = {}
    cells = summary.get("cells", [])
    maximum_path = max(1, max((len(c.get("representative_path") or []) for c in cells), default=1))
    chunk_size = max(1, min(256, 8192//maximum_path))
    for start in range(0, len(cells), chunk_size):
        chunk = cells[start:start+chunk_size]
        queries, owners, lengths, endpoint_checks = [], [], [], []
        states = {}
        source_kinds = {}
        for i, cell in enumerate(chunk):
            path = cell.get("representative_path")
            if not path or len(path) < 2:
                cell["path_validation"] = dict(status="unverified", reason="missing_representative_path")
                missing += 1
                continue
            checked += 1
            if any(len(p) != 3 or not all(math.isfinite(float(x)) for x in p) for p in path):
                states[i] = "nonfinite_path"
                continue
            states[i] = None
            if len(path)-2 > trace.config.max_depth:
                states[i] = "reflection_depth_mismatch"
                continue
            if len(path) > 2 and trace.config.k_brdf <= 0:
                states[i] = "reflection_disabled"
                continue
            first_delta = vec_sub(path[1], path[0])
            if vec_dot(first_delta, first_delta) <= epsilon*epsilon:
                states[i] = "degenerate_segment"
                continue
            source_kind = on_source(path[0], vec_norm(first_delta))
            if not source_kind:
                states[i] = "source_position_or_emission_mismatch"
                continue
            source_kinds[i] = source_kind
            # Every reflection vertex must be a real first CAD hit. The last
            # camera endpoint is a sampling point, so only occlusion is tested.
            for j in range(len(path)-1):
                delta = vec_sub(path[j+1], path[j])
                distance = math.sqrt(vec_dot(delta, delta))
                if distance <= epsilon*2:
                    states[i] = "degenerate_segment"
                    break
                direction = tuple(x/distance for x in delta)
                endpoint = j < len(path)-2
                # A camera sample is not a CAD surface. Do not leave a blind
                # zone before it: even a blocker immediately in front counts.
                queries.append((path[j], direction, epsilon, distance+tolerance if endpoint else distance))
                owners.append(i); lengths.append(distance); endpoint_checks.append(j+1 if endpoint else None)
            delta = vec_sub(path[-2], path[-1])
            distance = math.sqrt(vec_dot(delta, delta))
            if distance > epsilon*2:
                queries.append((path[-1], tuple(x/distance for x in delta), epsilon, distance+tolerance))
                owners.append(i); lengths.append(distance); endpoint_checks.append("visible")
        visible_faces = {}
        if queries:
            rays = RayBatch(origins=[q[0] for q in queries], directions=[q[1] for q in queries],
                min_t=[q[2] for q in queries], max_t=[q[3] for q in queries])
            try:
                hits, _ = mesh.intersect_rays_native_cpu(rays, backend="bvh")
                native_batches += 1
            except Exception:
                hits = mesh.intersect_rays(rays, backend="bvh")
                reference_batches += 1
            for k, i in enumerate(owners):
                if states.get(i) is not None:
                    continue
                face, distance = int(hits.face_indices[k]), float(hits.t[k])
                if face >= 0 and (endpoint_checks[k] is None or distance < lengths[k]-tolerance):
                    states[i] = "occluded_segment"
                elif isinstance(endpoint_checks[k], int) and face < 0:
                    states[i] = "reflection_vertex_off_surface"
                elif isinstance(endpoint_checks[k], int) and resolved is not None:
                    j = endpoint_checks[k]
                    path = chunk[i]["representative_path"]
                    incoming = vec_norm(vec_sub(path[j], path[j-1]))
                    outgoing = vec_norm(vec_sub(path[j+1], path[j]))
                    from .observer_radiance import brdf, delta_weight
                    normal = mesh.normal(face)
                    if vec_dot(incoming, normal) > 0:
                        normal = vec_mul(normal, -1.)
                    profile = resolved[face].profile
                    wi = vec_mul(incoming, -1.)
                    continuous = brdf(profile, normal, wi, outgoing, trace.config.angle_dependent_reflectance) > 0
                    delta_supported = vec_dot(ideal_specular_direction(incoming, normal), outgoing) >= 1-1e-12 and delta_weight(profile, normal, wi, trace.config.angle_dependent_reflectance) > 0
                    if not continuous and not delta_supported:
                        states[i] = "reflection_material_mismatch"
                elif endpoint_checks[k] == "visible":
                    if face >= 0:
                        visible_faces[i] = face
                    elif len(chunk[i]["representative_path"]) > 2:
                        states[i] = "visible_endpoint_off_surface"
        for i, reason in states.items():
            cell = chunk[i]
            if reason:
                invalid += 1
                reasons[reason] = reasons.get(reason, 0)+1
                cell["path_validation"] = dict(status="invalid", reason=reason)
            else:
                valid += 1
                face = visible_faces.get(i)
                if len(cell["representative_path"]) == 2:
                    kind, source_face = source_kinds[i]
                    face = source_face if kind == "cad_surface" else None
                else:
                    kind = "cad_surface" if face is not None else "virtual_emitter"
                virtual += int(kind == "virtual_emitter")
                cell["path_validation"] = dict(status="valid", endpoint_kind=kind, face_index=face,
                    external_aperture_confirmed=False)
                # Never use a mean of several unrelated surfaces as a marker.
                cell["position"] = list(cell["representative_path"][-2])
    report = dict(contract="observer_path_geometry_audit_v1", checked=checked, valid=valid, invalid=invalid,
        missing=missing, virtual_emitter=virtual, reasons=reasons, native_cpu_batches=native_batches,
        reference_cpu_batches=reference_batches, runtime_sec=time.perf_counter()-started,
        scope="representative_geometry_not_exterior_aperture_or_radiance_calibration")
    summary["path_validation_summary"] = report
    return report


def validate_receiver_evidence(trace, frames, grids, resolved, termination=None):
    from .raytracer import _find_first_receiver_hit
    reports = []
    for frame in frames:
        receiver = frame.receiver
        path = receiver.observer_reference_path
        if not path:
            continue
        delta = vec_sub(path[-1], path[-2])
        diagnosis = "degenerate_segment"
        geometry_valid = False
        endpoint_kind = None
        if vec_dot(delta, delta) > trace.config.epsilon_mm**2:
            hit = _find_first_receiver_hit(path[-2], vec_norm(delta), 1., -1, [frame], grids, trace.config)
            if hit is None:
                diagnosis = "receiver_position_direction_acceptance_or_roi_mismatch"
            else:
                extended = [*path[:-1], hit.point]
                cell = dict(representative_path=extended)
                summary = dict(cells=[cell], measure="luminance_cd_m2")
                validate_observer_paths(trace, summary, resolved)
                audit = cell["path_validation"]
                geometry_valid = audit["status"] == "valid"
                endpoint_kind = audit.get("endpoint_kind")
                diagnosis = "reference_path_valid" if geometry_valid else audit.get("reason", "unverified")
        hit_count = grids[receiver.receiver_id].hit_count
        if geometry_valid and hit_count == 0:
            diagnosis = "valid_reference_path_zero_forward_samples"
        reports.append(dict(receiver_id=receiver.receiver_id, path_valid=geometry_valid,
            visible_endpoint_kind=endpoint_kind, external_aperture_confirmed=False,
            forward_hit_count=hit_count, diagnosis=diagnosis, reference_reflections=len(path)-2,
            injected_hits=0, scope="representative_path_not_flux_equivalence",
            forward_termination_mode=trace.config.termination_mode, min_energy=trace.config.min_energy,
            min_energy_basis=trace.config.min_energy_basis,
            forward_energy_cutoff_count=(termination or {}).get("energy_cutoff_count", 0)))
    return reports
