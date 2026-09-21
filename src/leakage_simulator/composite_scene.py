from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


_FACE_ARRAYS = (
    "face_material_ids", "face_normals", "face_centroids", "face_areas_mm2",
)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def merge_scene_meshes(
    meshes: Iterable[dict[str, Any]],
    component_maps: list[dict[int, int]],
    source_offsets: list[int],
) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "vertices": [], "faces": [], "face_ids": [],
        "face_component_ids": [], "face_material_ids": [],
        "face_source_ids": [], "face_normals": [], "face_centroids": [],
        "face_areas_mm2": [], "feature_edge_segments": [],
    }
    for index, mesh in enumerate(meshes):
        vertex_offset = len(merged["vertices"])
        face_offset = len(merged["faces"])
        vertices = _as_list(mesh.get("vertices"))
        faces = _as_list(mesh.get("faces"))
        merged["vertices"].extend(vertices)
        merged["faces"].extend([
            [int(face[0]) + vertex_offset, int(face[1]) + vertex_offset, int(face[2]) + vertex_offset]
            for face in faces
        ])
        merged["face_ids"].extend(range(face_offset, face_offset + len(faces)))
        component_map = component_maps[index]
        merged["face_component_ids"].extend([
            component_map.get(int(value), int(value)) if value is not None else None
            for value in _as_list(mesh.get("face_component_ids"))
        ])
        source_ids = _as_list(mesh.get("face_source_ids")) or _as_list(mesh.get("face_ids"))
        merged["face_source_ids"].extend([
            int(value) + source_offsets[index] for value in source_ids
        ])
        for key in _FACE_ARRAYS:
            merged[key].extend(_as_list(mesh.get(key)))
        for edge in _as_list(mesh.get("feature_edge_segments")):
            copied = dict(edge)
            component_id = copied.get("component_id")
            if component_id is not None:
                copied["component_id"] = component_map.get(int(component_id), int(component_id))
            merged["feature_edge_segments"].append(copied)
    return merged


def merge_scene_payloads(
    payloads: list[dict[str, Any]],
    source_names: list[str],
) -> tuple[dict[str, Any], list[dict[int, int]], list[int]]:
    if not payloads:
        raise ValueError("At least one CAD scene is required")
    component_maps: list[dict[int, int]] = []
    source_offsets: list[int] = []
    next_component_id = 0
    next_source_id = 0
    for payload_index, payload in enumerate(payloads):
        component_ids = sorted({
            int(component.get("component_id", component.get("object_id", 0)))
            for component in payload.get("components", [])
        })
        if payload_index == 0:
            # Main CAD component IDs are referenced by materials, transforms,
            # emitters and receivers. Keep them unchanged when Accessory CAD is
            # attached so an existing analysis setup remains valid.
            component_maps.append({value: value for value in component_ids})
            next_component_id = max(component_ids, default=-1) + 1
        else:
            component_maps.append({value: next_component_id + offset for offset, value in enumerate(component_ids)})
            next_component_id += len(component_ids)
        source_offsets.append(next_source_id)
        source_ids = _as_list((payload.get("mesh") or {}).get("face_source_ids"))
        if not source_ids:
            source_ids = _as_list((payload.get("mesh") or {}).get("face_ids"))
        next_source_id += max([int(value) for value in source_ids], default=-1) + 1

    components: list[dict[str, Any]] = []
    for payload_index, payload in enumerate(payloads):
        face_offset = sum(len(_as_list((prior.get("mesh") or {}).get("faces"))) for prior in payloads[:payload_index])
        prefix = "" if payload_index == 0 else f"{Path(source_names[payload_index]).stem} / "
        for component in payload.get("components", []):
            copied = deepcopy(component)
            original_id = int(copied.get("component_id", copied.get("object_id", 0)))
            new_id = component_maps[payload_index][original_id]
            copied["component_id"] = new_id
            copied["object_id"] = new_id
            copied["component_name"] = prefix + str(copied.get("component_name") or f"Component {new_id}")
            copied["object_name"] = prefix + str(copied.get("object_name") or copied["component_name"])
            copied["face_indices"] = [int(value) + face_offset for value in copied.get("face_indices", [])]
            components.append(copied)

    merged = deepcopy(payloads[0])
    merged["mesh"] = merge_scene_meshes(
        [payload["mesh"] for payload in payloads], component_maps, source_offsets,
    )
    merged["components"] = components
    merged["objects"] = deepcopy(components)
    metadata = dict(payloads[0].get("metadata") or {})
    metadata.update({
        "face_count": len(merged["mesh"]["faces"]),
        "vertex_count": len(merged["mesh"]["vertices"]),
        "component_count": len(components),
        "source_file": source_names[0],
        "source_files": source_names,
        "synthetic": any(bool((payload.get("metadata") or {}).get("synthetic")) for payload in payloads),
        "import_note": f"Main + Accessory CAD scene: {len(payloads)} files",
        "receiver_face_hint": [],
    })
    merged["metadata"] = metadata
    return merged, component_maps, source_offsets
