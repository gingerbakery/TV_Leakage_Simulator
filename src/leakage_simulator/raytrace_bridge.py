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
                raise ValueError(
                    "Face emitter has no traceable faces after component deletion or Traceability Off"
                )
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
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=emitters,
        receivers=receivers,
        optical_profiles=optical_profiles,
        config=config,
        project_name=str(request_payload.get("project_name") or "TV-Leakage-Direct"),
        optical_assignments=optical_assignments,
    )


def build_transformed_mesh(
    scene_mesh: Dict[str, Any],
    transform_rules: List[Dict[str, Any]],
    excluded_component_ids: Optional[List[int]] = None,
) -> TriangleMesh:
    vertices = scene_mesh.get("vertices") or []
    faces = scene_mesh.get("faces") or []
    component_ids = scene_mesh.get("face_component_ids") or [None] * len(faces)
    material_ids = scene_mesh.get("face_material_ids") or ["default"] * len(faces)
    face_centroids = scene_mesh.get("face_centroids") or [
        _triangle_centroid(vertices, face) for face in faces
    ]
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
    component_face_indices: Dict[int, List[int]] = {}
    for face_index, component_id in enumerate(component_ids):
        if component_id is None:
            continue
        normalized_component_id = int(component_id)
        if normalized_component_id in excluded_components:
            continue
        component_face_indices.setdefault(normalized_component_id, []).append(face_index)
    pivots = {
        component_id: _average_points([face_centroids[index] for index in indices])
        for component_id, indices in component_face_indices.items()
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


def _triangle_centroid(vertices: List[List[float]], face: List[int]) -> Vec3:
    points = [vertices[int(index)] for index in face]
    return tuple(sum(float(point[axis]) for point in points) / 3.0 for axis in range(3))  # type: ignore[return-value]


def _average_points(points: List[List[float]]) -> Vec3:
    if not points:
        return (0.0, 0.0, 0.0)
    return tuple(sum(float(point[axis]) for point in points) / len(points) for axis in range(3))  # type: ignore[return-value]


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
