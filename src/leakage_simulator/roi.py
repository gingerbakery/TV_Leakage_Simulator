from __future__ import annotations

from typing import Dict, List, Optional

from .components import build_face_groups
from .importers import import_geometry
from .types import ReceiverPatchConfig


def build_default_receivers(
    face_indices: List[int],
    name: str = "viewer_side",
) -> List[ReceiverPatchConfig]:
    return [ReceiverPatchConfig(receiver_id=name, face_indices=face_indices, weight=1.0)]


def resolve_receiver_faces(
    import_receiver_faces: List[int],
    roi_face_indices: Optional[List[int]],
) -> List[int]:
    if roi_face_indices:
        return roi_face_indices
    return import_receiver_faces


def build_scene_payload(cad_path: Optional[str]) -> Dict:
    import_result = import_geometry(cad_path)
    mesh = import_result.mesh
    objects = build_face_groups(mesh, max_faces_per_object=None)
    face_to_component: Dict[int, int] = {}
    for item in objects:
        component_id = item["object_id"]
        item["component_id"] = component_id
        item["component_name"] = item["object_name"]
        for face_index in item["face_indices"]:
            face_to_component[face_index] = component_id

    face_ids = list(range(len(mesh.faces)))
    face_component_ids = [face_to_component.get(face_index) for face_index in face_ids]
    face_material_ids = [mesh.material_id(face_index) for face_index in face_ids]
    face_normals = [
        [round(value, 6) for value in mesh.normal(face_index)]
        for face_index in face_ids
    ]
    face_centroids = [
        [round(value, 6) for value in mesh.centroid(face_index)]
        for face_index in face_ids
    ]
    face_areas = [round(mesh.area(face_index), 6) for face_index in face_ids]
    return {
        "schema_version": "mesh-scene.v1",
        "units": {
            "length": "mm",
        },
        "coordinate_system": {
            "handedness": "right",
            "axes": {
                "x": "model_x",
                "y": "model_y",
                "z": "model_z",
            },
        },
        "mesh": {
            "vertices": [[round(v[0], 6), round(v[1], 6), round(v[2], 6)] for v in mesh.vertices],
            "faces": [[face.v0, face.v1, face.v2] for face in mesh.faces],
            "face_ids": face_ids,
            "face_component_ids": face_component_ids,
            "face_material_ids": face_material_ids,
            "face_normals": face_normals,
            "face_centroids": face_centroids,
            "face_areas_mm2": face_areas,
        },
        "objects": objects,
        "components": objects,
        "metadata": {
            "face_count": len(mesh.faces),
            "vertex_count": len(mesh.vertices),
            "component_count": len(objects),
            "source_file": cad_path or "",
            "synthetic": import_result.synthetic,
            "import_note": import_result.note,
            "receiver_face_hint": import_result.receiver_face_indices[
                : min(30, len(import_result.receiver_face_indices))
            ],
        },
    }
