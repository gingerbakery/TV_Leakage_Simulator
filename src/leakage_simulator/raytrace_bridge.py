from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import math

from .bsdf_guard import validate_bsdf_request
from .geometry import TriangleMesh
from .raytracer import DirectRayTraceInput
from .types import EmitterSpec, OpticalAssignment, OpticalProfile, RayTraceConfig, ReceiverSpec, Vec3


@dataclass(frozen=True)
class PreparedTraceGeometry:
    mesh: TriangleMesh
    source_to_trace_face: Dict[int, List[int]]
    roi_is_active: bool
    emission_scope: Dict = field(default_factory=dict)
    detection_boxes: List = field(default_factory=list)


def _emitter_source_faces(
    scene_mesh: Dict[str, Any],
    emitter: Dict[str, Any],
    component_cache: Optional[Dict[Tuple[int, ...], List[int]]] = None,
) -> List[int]:
    """Expand compact Preview Body sources against the server-side CAD cache."""
    explicit = [int(face_index) for face_index in emitter.get("face_indices", [])]
    component_key = tuple(sorted({
        int(component_id)
        for component_id in emitter.get("source_component_ids", [])
    }))
    if not component_key:
        return explicit
    cache = component_cache if component_cache is not None else {}
    expanded = cache.get(component_key)
    if expanded is None:
        selected = set(component_key)
        expanded = [
            face_index
            for face_index, component_id in enumerate(
                scene_mesh.get("face_component_ids") or []
            )
            if component_id is not None and int(component_id) in selected
        ]
        cache[component_key] = expanded
    return list(dict.fromkeys([*explicit, *expanded]))


def build_prepared_trace_geometry(
    scene_mesh: Dict[str, Any],
    request_payload: Dict[str, Any],
    should_stop: Optional[Callable[[], bool]] = None,
) -> PreparedTraceGeometry:
    if should_stop is not None and should_stop():
        raise InterruptedError("Ray trace preparation stopped")
    component_cache: Dict[Tuple[int, ...], List[int]] = {}
    emitter_source_faces = {
        face_index
        for item in request_payload.get("emitters", [])
        if str(item.get("emitter_type") or "face") == "face"
        for face_index in _emitter_source_faces(scene_mesh, item, component_cache)
    }
    excluded_components = {
        int(component_id)
        for component_id in request_payload.get("excluded_component_ids", [])
    }
    source_component_ids = scene_mesh.get("face_component_ids") or []
    emitter_only_source_faces = {
        face_index
        for face_index in emitter_source_faces
        if 0 <= face_index < len(source_component_ids)
        and source_component_ids[face_index] is not None
        and int(source_component_ids[face_index]) in excluded_components
    }
    mesh = build_transformed_mesh(
        scene_mesh,
        request_payload.get("transform_rules", []),
        request_payload.get("excluded_component_ids", []),
        emitter_source_face_indices=emitter_source_faces,
        preview_blockers=request_payload.get("preview_blockers", []),
        should_stop=should_stop,
    )
    original_mesh = mesh
    roi_faces = request_payload.get("roi_faces")
    analysis_roi = request_payload.get("analysis_roi")
    if analysis_roi is not None and not isinstance(analysis_roi, dict):
        raise ValueError("Invalid analysis ROI")
    scope_faces = (analysis_roi or {}).get("face_indices", roi_faces or [])
    # Resolve the unbounded axis against every participating surface. Finite
    # internal bounds keep JSON reports and numerical clipping well-defined;
    # no geometry, emission surface or receiver is cut along Z.
    raw_scope_boxes = (analysis_roi or {}).get("clip_boxes", request_payload.get("roi_clip_boxes", []))
    transport_boxes = request_payload.get("roi_clip_boxes", [])
    resolved_boxes = _resolve_roi_z_depth([*raw_scope_boxes, *transport_boxes], mesh, request_payload)
    transport_boxes = resolved_boxes[len(raw_scope_boxes):]
    raw_scope_boxes = resolved_boxes[:len(raw_scope_boxes)]
    if analysis_roi is not None and not scope_faces and not raw_scope_boxes:
        raise ValueError("ROI 해석 범위가 비어 있습니다.")
    for raw in raw_scope_boxes:
        try:
            values = [float(raw[key]) for key in ("x_min","x_max","y_min","y_max","z_min","z_max")]
            valid = all(math.isfinite(value) for value in values) and all(abs(values[2*i+1]-values[2*i]) > 1e-9 for i in range(3))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("ROI 좌표 박스가 비어 있거나 잘못되었습니다.")
    scope_boxes = _normalize_roi_clip_boxes(raw_scope_boxes)
    if raw_scope_boxes and not scope_boxes:
        raise ValueError("ROI 좌표 박스가 비어 있거나 잘못되었습니다.")
    roi_is_active = bool(roi_faces)
    if roi_is_active:
        mesh, source_to_trace_face = filter_mesh_to_roi(
            mesh,
            [int(value) for value in roi_faces],
            preserved_source_face_indices=emitter_only_source_faces,
            clip_boxes=transport_boxes,
        )
    else:
        source_to_trace_face: Dict[int, List[int]] = {}
        for face_index in range(len(mesh.faces)):
            source_face_index = int(
                mesh.metadata(face_index).get("source_face_index", face_index)
            )
            source_to_trace_face.setdefault(source_face_index, []).append(face_index)
    emission_scope = {}
    if scope_faces or scope_boxes:
        from .roi_scope import prepare_emission_scope
        emission_scope = prepare_emission_scope(mesh, original_mesh, request_payload.get("emitters", []),
            lambda item: _emitter_source_faces(scene_mesh, item, component_cache), source_to_trace_face,
            scope_boxes, set(scope_faces), context_retained=not roi_is_active)
    # Prepared geometry caches a BVH acceleration structure for both CPU and
    # CUDA consumers.  Compute-device selection remains request-local in
    # RayTraceConfig and is deliberately not encoded in this geometry cache.
    mesh.set_acceleration_structure("bvh")
    mesh.prepare_acceleration(should_stop)
    return PreparedTraceGeometry(mesh, source_to_trace_face, roi_is_active, emission_scope, scope_boxes)


def build_direct_trace_input(
    scene_mesh: Dict[str, Any],
    request_payload: Dict[str, Any],
    prepared_geometry: Optional[PreparedTraceGeometry] = None,
    geometry_cache_hit: bool = False,
) -> DirectRayTraceInput:
    validate_bsdf_request(request_payload)
    geometry = prepared_geometry or build_prepared_trace_geometry(
        scene_mesh,
        request_payload,
    )
    mesh = geometry.mesh
    source_to_trace_face = geometry.source_to_trace_face
    roi_is_active = geometry.roi_is_active
    emitter_payloads = []
    component_cache: Dict[Tuple[int, ...], List[int]] = {}
    for item in request_payload.get("emitters", []):
        normalized = dict(item)
        scope = geometry.emission_scope.get(normalized["emitter_id"])
        if scope is not None and not scope["face_indices"]:
            continue
        if str(normalized.get("emitter_type") or "face") == "face":
            source_faces = _emitter_source_faces(scene_mesh, normalized, component_cache)
            normalized["face_indices"] = [
                trace_face_index
                for face_index in source_faces
                if face_index in source_to_trace_face
                for trace_face_index in source_to_trace_face[face_index]
            ]
            if not normalized["face_indices"]:
                if roi_is_active:
                    raise ValueError("Face emitter has no faces left inside the selected ROI")
                raise ValueError("Face emitter has no traceable faces after component exclusion")
        if scope is not None:
            normalized["face_indices"] = scope["face_indices"]
            if scope["virtual_normal"] is not None:
                normalized.update(emitter_type="face", normal_mode="custom", custom_normal=scope["virtual_normal"],
                    surface_construction="rectangular_fit", polygon_vertices=[])
            # Cropping must not concentrate the entire original lamp power.
            if normalized.get("power_mode", "total") == "total":
                normalized["power_lumen"] = float(normalized.get("power_lumen", 1.))*scope["fraction"]
        normalized.pop("source_component_ids", None)
        emitter_payloads.append(normalized)
    emitters = [EmitterSpec.from_dict(item) for item in emitter_payloads]
    receivers = [ReceiverSpec.from_dict(dict(item)) for item in request_payload.get("receivers", [])]
    if not emitters:
        if geometry.emission_scope:
            raise ValueError("ROI 안에 발광면이 없습니다 (no faces left inside the selected ROI). ROI 또는 Emitter 위치를 확인하세요.")
        raise ValueError("Direct ray tracing requires at least one emitter")
    if not receivers:
        raise ValueError("Direct ray tracing requires at least one receiver")
    config = RayTraceConfig.from_dict(dict(request_payload.get("config") or {}))
    optical_profiles = [
        OpticalProfile.from_dict(dict(item)) for item in request_payload.get("optical_profiles", [])
    ]
    optical_assignments = [
        OpticalAssignment.from_dict(dict(item))
        for item in request_payload.get("optical_assignments", [])
    ]
    _filter_face_optical_assignments(optical_assignments, source_to_trace_face)
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=emitters,
        receivers=receivers,
        optical_profiles=optical_profiles,
        config=config,
        project_name=str(request_payload.get("project_name") or "TV-Leakage-Direct"),
        optical_assignments=optical_assignments,
        geometry_cache_hit=geometry_cache_hit,
        bsdf_assets=request_payload.get("bsdf_assets", []),
        escape_collection=request_payload.get("escape_collection"),
        observer_probe=request_payload.get("observer_probe"),
        analysis_roi={"contract": "roi_source_detection_v1", "emission_scope": geometry.emission_scope,
                      "detection_boxes": geometry.detection_boxes, "receiver_scope": "orthogonal_projection",
                      "context_geometry_retained": not geometry.roi_is_active} if geometry.emission_scope else None,
    )


def _resolve_roi_z_depth(raw_boxes, mesh, payload):
    if not any(box.get("z_unbounded") is True for box in raw_boxes):
        return raw_boxes
    from .roi_scope import virtual_triangles
    depths = [point[2] for point in mesh.vertices]
    for item in payload.get("emitters", []):
        if str(item.get("emitter_type") or "face") == "face":
            continue
        triangles, _ = virtual_triangles(item)
        depths.extend(point[2] for triangle in triangles for point in triangle)
    from .raytracer import _build_receiver_frame
    for item in payload.get("receivers", []):
        receiver = ReceiverSpec.from_dict(dict(item))
        frame = _build_receiver_frame(receiver)
        half_depth = abs(frame.u_axis[2])*receiver.width_mm/2 + abs(frame.v_axis[2])*receiver.height_mm/2
        depths.extend((receiver.center[2]-half_depth, receiver.center[2]+half_depth))
    low, high = min(depths, default=0.), max(depths, default=0.)
    padding = max(1., (high-low)*.01)
    return [dict(box, z_min=low-padding, z_max=high+padding)
            if box.get("z_unbounded") is True else dict(box) for box in raw_boxes]


def _normalize_roi_clip_boxes(
    raw_boxes: List[Dict[str, Any]],
) -> List[Tuple[float, float, float, float, float, float]]:
    boxes: List[Tuple[float, float, float, float, float, float]] = []
    for raw in raw_boxes:
        try:
            x0, x1 = sorted((float(raw["x_min"]), float(raw["x_max"])))
            y0, y1 = sorted((float(raw["y_min"]), float(raw["y_max"])))
            z0, z1 = sorted((float(raw["z_min"]), float(raw["z_max"])))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (x0, x1, y0, y1, z0, z1)):
            continue
        if x1 - x0 <= 1e-9 or y1 - y0 <= 1e-9 or z1 - z0 <= 1e-9:
            continue
        boxes.append((x0, x1, y0, y1, z0, z1))
    from .roi_scope import disjoint_boxes
    return disjoint_boxes(boxes)


def _clip_polygon_axis(
    polygon: List[Vec3],
    axis: int,
    boundary: float,
    keep_greater: bool,
) -> List[Vec3]:
    if not polygon:
        return []

    def inside(point: Vec3) -> bool:
        return point[axis] >= boundary - 1e-9 if keep_greater else point[axis] <= boundary + 1e-9

    result: List[Vec3] = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside != previous_inside:
            denominator = current[axis] - previous[axis]
            if abs(denominator) > 1e-15:
                ratio = (boundary - previous[axis]) / denominator
                intersection = tuple(
                    previous[index] + ratio * (current[index] - previous[index])
                    for index in range(3)
                )
                result.append(intersection)  # type: ignore[arg-type]
        if current_inside:
            result.append(current)
        previous = current
        previous_inside = current_inside
    return result


def _clip_triangle_to_box_union(
    triangle: Tuple[Vec3, Vec3, Vec3],
    boxes: List[Tuple[float, float, float, float, float, float]],
) -> List[Tuple[Vec3, Vec3, Vec3]]:
    """Clip one tessellated triangle to the union of ROI AABBs.

    The viewer already clips the visible ROI surface. Applying the same
    volume on the trace mesh prevents a boundary triangle from emitting rays
    outside the yellow ROI highlight. Rounded triangle keys suppress overlap
    duplicates when two active ROI boxes touch or overlap.
    """
    result: List[Tuple[Vec3, Vec3, Vec3]] = []
    seen: Set[Tuple[Tuple[float, float, float], ...]] = set()
    for x0, x1, y0, y1, z0, z1 in boxes:
        polygon: List[Vec3] = list(triangle)
        for axis, boundary, keep_greater in (
            (0, x0, True), (0, x1, False),
            (1, y0, True), (1, y1, False),
            (2, z0, True), (2, z1, False),
        ):
            polygon = _clip_polygon_axis(polygon, axis, boundary, keep_greater)
            if len(polygon) < 3:
                break
        if len(polygon) < 3:
            continue
        origin = polygon[0]
        for index in range(1, len(polygon) - 1):
            clipped = (origin, polygon[index], polygon[index + 1])
            key = tuple(sorted(
                tuple(round(value, 9) for value in point)
                for point in clipped
            ))
            if key in seen:
                continue
            seen.add(key)
            result.append(clipped)
    return result


def filter_mesh_to_roi(
    mesh: TriangleMesh,
    roi_face_indices: List[int],
    preserved_source_face_indices: Optional[Set[int]] = None,
    clip_boxes: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[TriangleMesh, Dict[int, List[int]]]:
    """Trims an already-transformed direct-trace mesh down to just the ROI
    faces, agreed with the ray-trace owner as "ROI를 선택하면 그 영역만 분석한다"
    (only the selected ROI region gets analyzed, not the full model).

    Returns the trimmed mesh plus a map from original scene face index (the
    same indices ROI selection in the web UI works with, and that
    build_transformed_mesh stores as each face's "source_face_index"
    metadata) to the new, trimmed mesh's face indices. Face emitters use the
    remapped geometry indices. Optical assignments retain original scene
    indices because OpticalPropertyResolver resolves source_face_index.
    """
    roi_set = set(roi_face_indices)
    preserved_set = preserved_source_face_indices or set()
    trimmed = TriangleMesh()
    remap: Dict[int, List[int]] = {}
    normalized_boxes = _normalize_roi_clip_boxes(clip_boxes or [])
    for face_index in range(len(mesh.faces)):
        metadata = mesh.metadata(face_index)
        raw_source_face_index = metadata.get("source_face_index")
        source_face_index = (
            int(raw_source_face_index) if raw_source_face_index is not None else None
        )
        is_auxiliary_absorber = metadata.get("preview_blocker_id") is not None
        if not is_auxiliary_absorber and (source_face_index is None or (
            source_face_index not in roi_set and source_face_index not in preserved_set
        )):
            continue
        triangles = [mesh.face_vertices(face_index)]
        if normalized_boxes and (source_face_index in roi_set or source_face_index in preserved_set):
            triangles = _clip_triangle_to_box_union(triangles[0], normalized_boxes)
        for v0, v1, v2 in triangles:
            new_v0 = trimmed.add_vertex(v0)
            new_v1 = trimmed.add_vertex(v1)
            new_v2 = trimmed.add_vertex(v2)
            new_face_index = trimmed.add_face(
                new_v0, new_v1, new_v2, mesh.material_id(face_index), dict(mesh.metadata(face_index))
            )
            remap.setdefault(source_face_index, []).append(new_face_index)
    if not trimmed.faces:
        raise ValueError("ROI selection produced an empty mesh - nothing to trace")
    return trimmed, remap


def _filter_face_optical_assignments(
    optical_assignments: List[OpticalAssignment],
    face_remap: Dict[int, List[int]],
) -> None:
    for assignment in optical_assignments:
        if assignment.target_type != "faces":
            continue
        assignment.face_indices = [
            index for index in assignment.face_indices if index in face_remap
        ]


def build_transformed_mesh(
    scene_mesh: Dict[str, Any],
    transform_rules: List[Dict[str, Any]],
    excluded_component_ids: Optional[List[int]] = None,
    emitter_source_face_indices: Optional[Set[int]] = None,
    preview_blockers: Optional[List[Dict[str, Any]]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> TriangleMesh:
    vertices = scene_mesh.get("vertices") or []
    faces = scene_mesh.get("faces") or []
    component_ids = scene_mesh.get("face_component_ids") or [None] * len(faces)
    material_ids = scene_mesh.get("face_material_ids") or ["default"] * len(faces)
    if len(component_ids) != len(faces):
        raise ValueError("face_component_ids must match face count")
    excluded_components: Set[int] = {
        int(component_id) for component_id in (excluded_component_ids or [])
    }
    emitter_source_faces = emitter_source_face_indices or set()

    enabled_rules = {
        int(rule["object_id"]): rule
        for rule in transform_rules
        if rule.get("enabled", True)
        and rule.get("target_type", "component") == "component"
        and rule.get("object_id") is not None
    }
    component_bounds: Dict[int, List[List[float]]] = {}
    for face_index, component_id in enumerate(component_ids):
        if face_index % 4096 == 0 and should_stop is not None and should_stop():
            raise InterruptedError("Ray trace preparation stopped")
        if component_id is None:
            continue
        normalized_component_id = int(component_id)
        bounds = component_bounds.setdefault(
            normalized_component_id,
            [
                [math.inf, math.inf, math.inf],
                [-math.inf, -math.inf, -math.inf],
            ],
        )
        for vertex_index in faces[face_index]:
            point = vertices[int(vertex_index)]
            for axis in range(3):
                coordinate = float(point[axis])
                bounds[0][axis] = min(bounds[0][axis], coordinate)
                bounds[1][axis] = max(bounds[1][axis], coordinate)
    pivots = {
        component_id: tuple(
            (bounds[0][axis] + bounds[1][axis]) / 2.0
            for axis in range(3)
        )
        for component_id, bounds in component_bounds.items()
    }
    for component_id, rule in enabled_rules.items():
        # A rule-specified pivot overrides the bounding-box center default -
        # must stay in sync with the same override in the frontend viewer
        # (three-viewer-canvas.tsx resolveTransformPivot) or the ray-traced
        # result will diverge from what the user sees in the preview.
        pivot_override = rule.get("pivot")
        if pivot_override:
            pivots[component_id] = (
                float(pivot_override.get("x", 0.0) or 0.0),
                float(pivot_override.get("y", 0.0) or 0.0),
                float(pivot_override.get("z", 0.0) or 0.0),
            )

    mesh = TriangleMesh()
    for face_index, face in enumerate(faces):
        if face_index % 4096 == 0 and should_stop is not None and should_stop():
            raise InterruptedError("Ray trace preparation stopped")
        if len(face) != 3:
            raise ValueError("Direct ray tracing requires triangle faces")
        component_id = component_ids[face_index]
        component_is_excluded = (
            component_id is not None and int(component_id) in excluded_components
        )
        if component_is_excluded and face_index not in emitter_source_faces:
            continue
        rule = enabled_rules.get(int(component_id)) if component_id is not None else None
        pivot = pivots.get(int(component_id), (0.0, 0.0, 0.0)) if component_id is not None else (0.0, 0.0, 0.0)
        transformed_indices: List[int] = []
        for vertex_index in face:
            point = tuple(float(value) for value in vertices[int(vertex_index)])
            transformed = _transform_point(point, pivot, rule) if rule else point
            transformed_indices.append(mesh.add_vertex(transformed))
        material_id = str(material_ids[face_index]) if face_index < len(material_ids) else "default"
        mesh.add_face(
            transformed_indices[0],
            transformed_indices[1],
            transformed_indices[2],
            material_id,
            {
                "source_face_index": face_index,
                "component_id": component_id,
                "trace_excluded": component_is_excluded,
            },
        )
    _append_preview_blockers(mesh, preview_blockers or [])
    return mesh


def _append_preview_blockers(
    mesh: TriangleMesh,
    blockers: List[Dict[str, Any]],
) -> None:
    """Append lightweight, unassigned rectangular absorber solids.

    Preview uses these as approximate assembly blockers. Precision tracing may
    also opt into the same geometry for Preview Blockers and Allowed-Area
    evaluation masks. Unassigned faces resolve to the safe zero-reflectance
    profile, so these volumes remove intercepted light without adding a
    synthetic reflection.
    """
    triangles = (
        (0, 2, 1), (0, 3, 2),
        (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4),
        (3, 7, 6), (3, 6, 2),
        (0, 4, 7), (0, 7, 3),
        (1, 2, 6), (1, 6, 5),
    )
    for blocker_index, blocker in enumerate(blockers):
        if not blocker.get("enabled", True):
            continue
        center = tuple(float(value) for value in blocker.get("center", (0, 0, 0)))
        u_axis = tuple(float(value) for value in blocker.get("u_axis", (1, 0, 0)))
        v_axis = tuple(float(value) for value in blocker.get("v_axis", (0, 1, 0)))
        normal = tuple(float(value) for value in blocker.get("normal", (0, 0, 1)))
        half_width = max(float(blocker.get("width_mm", 0.0)), 1e-6) / 2.0
        half_height = max(float(blocker.get("height_mm", 0.0)), 1e-6) / 2.0
        half_depth = max(float(blocker.get("depth_mm", 0.0)), 1e-6) / 2.0
        corners: List[Vec3] = []
        for depth_sign in (-1.0, 1.0):
            for u_sign, v_sign in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
                corners.append((
                    center[0] + u_axis[0] * half_width * u_sign + v_axis[0] * half_height * v_sign + normal[0] * half_depth * depth_sign,
                    center[1] + u_axis[1] * half_width * u_sign + v_axis[1] * half_height * v_sign + normal[1] * half_depth * depth_sign,
                    center[2] + u_axis[2] * half_width * u_sign + v_axis[2] * half_height * v_sign + normal[2] * half_depth * depth_sign,
                ))
        local_triangles = triangles
        if blocker.get("shape", "box") == "ellipse":
            count = 48
            corners = []
            for depth_sign in (-1., 1.):
                for index in range(count):
                    angle = 2*math.pi*index/count
                    u, v = half_width*math.cos(angle), half_height*math.sin(angle)
                    corners.append(tuple(center[axis] + u_axis[axis]*u + v_axis[axis]*v +
                                         normal[axis]*half_depth*depth_sign for axis in range(3)))
            corners.extend(tuple(center[axis] + normal[axis]*half_depth*sign for axis in range(3)) for sign in (-1., 1.))
            local_triangles = []
            for index in range(count):
                following = (index+1) % count
                local_triangles.extend(((2*count, following, index), (2*count+1, count+index, count+following),
                                        (index, following, count+following), (index, count+following, count+index)))
        elif blocker.get("shape", "box") != "box":
            raise ValueError("Unknown blocker shape")
        vertex_indices = [mesh.add_vertex(point) for point in corners]
        component_id = -1_000_000 - blocker_index
        blocker_id = str(blocker.get("blocker_id") or "preview-blocker")
        for triangle in local_triangles:
            mesh.add_face(
                vertex_indices[triangle[0]],
                vertex_indices[triangle[1]],
                vertex_indices[triangle[2]],
                "__auxiliary_absorber__",
                {
                    "source_face_index": -1,
                    "component_id": component_id,
                    "preview_blocker_id": blocker_id,
                    "trace_excluded": False,
                },
            )


def _transform_point(point: Vec3, pivot: Vec3, rule: Dict[str, Any]) -> Vec3:
    move = rule.get("move") or {}
    tilt = rule.get("tilt") or {}
    x = point[0] - pivot[0]
    y = point[1] - pivot[1]
    z = point[2] - pivot[2]
    rotation_x = math.radians(float(tilt.get("x", 0.0) or 0.0))
    rotation_y = math.radians(float(tilt.get("y", 0.0) or 0.0))
    rotation_z = math.radians(float(tilt.get("z", 0.0) or 0.0))
    if abs(rotation_x) > 1e-12:
        next_y = y * math.cos(rotation_x) - z * math.sin(rotation_x)
        next_z = y * math.sin(rotation_x) + z * math.cos(rotation_x)
        y, z = next_y, next_z
    if abs(rotation_y) > 1e-12:
        next_x = x * math.cos(rotation_y) + z * math.sin(rotation_y)
        next_z = -x * math.sin(rotation_y) + z * math.cos(rotation_y)
        x, z = next_x, next_z
    if abs(rotation_z) > 1e-12:
        next_x = x * math.cos(rotation_z) - y * math.sin(rotation_z)
        next_y = x * math.sin(rotation_z) + y * math.cos(rotation_z)
        x, y = next_x, next_y
    return (
        x + pivot[0] + float(move.get("x", 0.0) or 0.0),
        y + pivot[1] + float(move.get("y", 0.0) or 0.0),
        z + pivot[2] + float(move.get("z", 0.0) or 0.0),
    )
