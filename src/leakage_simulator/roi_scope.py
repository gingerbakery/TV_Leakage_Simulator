"""Independent ROI contracts for emission, detection and context geometry."""
from __future__ import annotations

import math
import numpy as np
from .geometry import vec_add, vec_sub, vec_mul, vec_cross, vec_dot, vec_norm

CONTRACT = "roi_source_detection_v1"


def disjoint_boxes(boxes):
    result = []
    for box in boxes:
        pending = [box]
        for existing in result:
            following = []
            for part in pending:
                lo = [max(part[2*i], existing[2*i]) for i in range(3)]
                hi = [min(part[2*i+1], existing[2*i+1]) for i in range(3)]
                if any(hi[i] <= lo[i] for i in range(3)):
                    following.append(part)
                    continue
                core = list(part)
                for i in range(3):
                    if core[2*i] < lo[i]:
                        slab = core.copy(); slab[2*i+1] = lo[i]
                        following.append(tuple(slab)); core[2*i] = lo[i]
                    if core[2*i+1] > hi[i]:
                        slab = core.copy(); slab[2*i] = hi[i]
                        following.append(tuple(slab)); core[2*i+1] = hi[i]
            pending = following
        result.extend(pending)
        if len(result) > 4096:
            raise ValueError("ROI union is too complex")
    return result


def receiver_roi_mask(points, normal, boxes):
    """Exact orthogonal projection of an AABB union onto a Receiver plane.

    Receivers can sit outside the CAD/ROI at their configured offset. A hit
    is accepted only if the line through it along the Receiver normal crosses
    the ROI, rather than accepting the whole original rectangular sensor.
    """
    points = np.asarray(points, dtype=np.float64).reshape((-1, 3))
    if not boxes:
        return np.ones(len(points), dtype=bool)
    accepted = np.zeros(len(points), dtype=bool)
    for box in boxes:
        enter, leave = np.full(len(points), -np.inf), np.full(len(points), np.inf)
        valid = np.ones(len(points), dtype=bool)
        for i in range(3):
            lo, hi = box[2*i:2*i+2]
            if abs(normal[i]) < 1e-15:
                valid &= (points[:, i] >= lo-1e-9) & (points[:, i] <= hi+1e-9)
            else:
                a, b = (lo-points[:, i])/normal[i], (hi-points[:, i])/normal[i]
                enter, leave = np.maximum(enter, np.minimum(a,b)), np.minimum(leave, np.maximum(a,b))
        accepted |= valid & (enter <= leave+1e-9)
    return accepted


def triangle_area(triangle):
    cross = vec_cross(vec_sub(triangle[1], triangle[0]), vec_sub(triangle[2], triangle[0]))
    return math.sqrt(vec_dot(cross, cross))/2


def virtual_triangles(item):
    u = vec_norm(item["u_axis"])
    v = vec_norm(vec_sub(item["v_axis"], vec_mul(u, vec_dot(item["v_axis"], u))))
    normal = vec_norm(vec_cross(u, v))
    if item.get("surface_construction") == "polygon_auto":
        points = item.get("polygon_vertices", [])
    else:
        points = [vec_add(item["center"], vec_add(vec_mul(u, su*float(item["width_mm"])/2), vec_mul(v, sv*float(item["height_mm"])/2)))
                  for su, sv in ((-1,-1), (1,-1), (1,1), (-1,1))]
    return [(points[0], points[i], points[i+1]) for i in range(1, len(points)-1)], normal


def prepare_emission_scope(mesh, original_mesh, items, source_faces, transport_map, boxes, face_ids, context_retained):
    """Build transparent sampling patches; never turn a cut into an absorber.

    Face sources already physically trimmed by legacy mesh ROI reuse those
    faces. Virtual planes and retained-context Face sources get independently
    clipped, non-colliding sampling triangles in the prepared mesh.
    """
    from .raytrace_bridge import _clip_triangle_to_box_union
    result = {}
    original_by_source = {}
    for i in range(len(original_mesh.faces)):
        source = original_mesh.metadata(i).get("source_face_index")
        if source is not None:
            original_by_source.setdefault(source, []).append(i)
    for item in items:
        emitter_id = item["emitter_id"]
        face_source = item.get("emitter_type", "face") == "face"
        normal = None
        if face_source:
            indices = [i for source in source_faces(item) for i in original_by_source.get(source, [])]
            triangles = [original_mesh.face_vertices(i) for i in indices]
        else:
            if not boxes:
                raise ValueError("ROI에 Datum/Reference Emitter를 제한하려면 좌표 박스 ROI가 필요합니다.")
            triangles, normal = virtual_triangles(item)
        original_area = sum(triangle_area(t) for t in triangles)
        if face_source and not context_retained:
            selected = [i for source in source_faces(item) for i in transport_map.get(source, [])]
        else:
            selected = []
            for index, triangle in enumerate(triangles):
                if not boxes and face_source and original_mesh.metadata(indices[index]).get("source_face_index") not in face_ids:
                    continue
                patches = _clip_triangle_to_box_union(triangle, boxes) if boxes else [triangle]
                for patch in patches:
                    if triangle_area(patch) <= 1e-16:
                        continue
                    vertices = [mesh.add_vertex(point) for point in patch]
                    selected.append(mesh.add_face(*vertices, "default", {"trace_excluded": True, "roi_emitter_id": emitter_id}))
        selected = [i for i in selected if mesh.area(i) > 1e-16]
        clipped_area = sum(mesh.area(i) for i in selected)
        result[emitter_id] = {"face_indices": selected, "fraction": min(1., clipped_area/original_area) if original_area > 0 else 0., "virtual_normal": normal,
                              "original_area_mm2": original_area, "clipped_area_mm2": clipped_area}
    return result
