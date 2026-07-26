from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
import math

from .geometry import TriangleMesh
from .raytracer import DirectRayTraceInput
from .types import EmitterSpec, OpticalAssignment, OpticalProfile, RayTraceConfig, ReceiverSpec, Vec3


def build_direct_trace_input(
    scene_mesh: Dict[str, Any],
    request_payload: Dict[str, Any],
) -> DirectRayTraceInput:
    mesh = build_transformed_mesh(
        scene_mesh,
        request_payload.get("transform_rules", []),
        request_payload.get("excluded_component_ids", []),
    )
    roi_faces = request_payload.get("roi_faces")
    roi_is_active = bool(roi_faces)
    if roi_is_active:
        mesh, source_to_trace_face = filter_mesh_to_roi(
            mesh,
            [int(value) for value in roi_faces],
        )
    else:
        source_to_trace_face = {
            int(mesh.metadata(face_index).get("source_face_index", face_index)): face_index
            for face_index in range(len(mesh.faces))
        }
    emitter_payloads = []
    for item in request_payload.get("emitters", []):
        normalized = dict(item)
        if str(normalized.get("emitter_type") or "face") == "face":
            source_faces = [int(face_index) for face_index in normalized.get("face_indices", [])]
            normalized["face_indices"] = [
                source_to_trace_face[face_index]
                for face_index in source_faces
                if face_index in source_to_trace_face
            ]
            if not normalized["face_indices"]:
                if roi_is_active:
                    raise ValueError("Face emitter has no faces left inside the selected ROI")
                raise ValueError("Face emitter has no traceable faces after component exclusion")
        emitter_payloads.append(normalized)
    emitters = [EmitterSpec.from_dict(item) for item in emitter_payloads]
    receivers = [ReceiverSpec.from_dict(dict(item)) for item in request_payload.get("receivers", [])]
    if not emitters:
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
    _remap_face_optical_assignments(optical_assignments, source_to_trace_face)
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=emitters,
        receivers=receivers,
        optical_profiles=optical_profiles,
        config=config,
        project_name=str(request_payload.get("project_name") or "TV-Leakage-Direct"),
        optical_assignments=optical_assignments,
    )


def filter_mesh_to_roi(
    mesh: TriangleMesh,
    roi_face_indices: List[int],
) -> Tuple[TriangleMesh, Dict[int, int]]:
    """Trims an already-transformed direct-trace mesh down to just the ROI
    faces, agreed with the ray-trace owner as "ROI를 선택하면 그 영역만 분석한다"
    (only the selected ROI region gets analyzed, not the full model).

    Returns the trimmed mesh plus a map from original scene face index (the
    same indices ROI selection in the web UI works with, and that
    build_transformed_mesh stores as each face's "source_face_index"
    metadata) to the new, trimmed mesh's face index - callers must remap any
    face-index references (face-type emitters, face-level optical
    assignment overrides) through this before using them against the
    trimmed mesh.
    """
    roi_set = set(roi_face_indices)
    trimmed = TriangleMesh()
    remap: Dict[int, int] = {}
    for face_index in range(len(mesh.faces)):
        raw_source_face_index = mesh.metadata(face_index).get("source_face_index")
        source_face_index = (
            int(raw_source_face_index) if raw_source_face_index is not None else None
        )
        if source_face_index is None or source_face_index not in roi_set:
            continue
        v0, v1, v2 = mesh.face_vertices(face_index)
        new_v0 = trimmed.add_vertex(v0)
        new_v1 = trimmed.add_vertex(v1)
        new_v2 = trimmed.add_vertex(v2)
        new_face_index = trimmed.add_face(
            new_v0, new_v1, new_v2, mesh.material_id(face_index), dict(mesh.metadata(face_index))
        )
        remap[source_face_index] = new_face_index
    if not trimmed.faces:
        raise ValueError("ROI selection produced an empty mesh - nothing to trace")
    return trimmed, remap


def _remap_face_optical_assignments(
    optical_assignments: List[OpticalAssignment],
    face_remap: Dict[int, int],
) -> None:
    for assignment in optical_assignments:
        if assignment.target_type != "faces":
            continue
        # Unlike emitters, a face-level material override commonly spans
        # faces well outside any one ROI - dropping the out-of-ROI faces and
        # leaving the rest (even if that means an empty override) is the
        # expected behavior here, not an error.
        assignment.face_indices = [
            face_remap[index] for index in assignment.face_indices if index in face_remap
        ]


def build_transformed_mesh(
    scene_mesh: Dict[str, Any],
    transform_rules: List[Dict[str, Any]],
    excluded_component_ids: Optional[List[int]] = None,
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

    enabled_rules = {
        int(rule["object_id"]): rule
        for rule in transform_rules
        if rule.get("enabled", True)
        and rule.get("target_type", "component") == "component"
        and rule.get("object_id") is not None
    }
    component_bounds: Dict[int, List[List[float]]] = {}
    for face_index, component_id in enumerate(component_ids):
        if component_id is None:
            continue
        normalized_component_id = int(component_id)
        if normalized_component_id in excluded_components:
            continue
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

    mesh = TriangleMesh()
    for face_index, face in enumerate(faces):
        if len(face) != 3:
            raise ValueError("Direct ray tracing requires triangle faces")
        component_id = component_ids[face_index]
        if component_id is not None and int(component_id) in excluded_components:
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
            {"source_face_index": face_index, "component_id": component_id},
        )
    return mesh


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
