from __future__ import annotations

import json
import struct

from leakage_simulator.scene_binary import HEADER, MAGIC, iter_scene_binary, prepare_scene_binary
from leakage_simulator.roi import materialize_scene_derived_geometry


def test_scene_binary_preserves_geometry_metadata_and_alignment() -> None:
    component = {
        "object_id": 7,
        "component_id": 7,
        "object_name": "Cover",
        "component_name": "Cover",
        "face_indices": [0],
        "face_count": 1,
        "area_mm2": 0.5,
        "bbox_min": [0.0, 0.0, 0.0],
        "bbox_max": [1.0, 1.0, 0.0],
        "is_truncated": False,
        "color": "#123456",
    }
    payload = {
        "schema_version": "mesh-scene.v1",
        "units": {"length": "mm"},
        "coordinate_system": {"handedness": "right", "axes": {}},
        "mesh": {
            "vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "faces": [[0, 1, 2]],
            "face_ids": [0],
            "face_component_ids": [7],
            "face_material_ids": ["PC · Black"],
            "face_source_ids": [42],
            "face_normals": [[0.0, 0.0, 1.0]],
            "face_centroids": [[1.0 / 3.0, 1.0 / 3.0, 0.0]],
            "face_areas_mm2": [0.5],
            "feature_edge_segments": [
                {"start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0], "component_id": 7}
            ],
        },
        "objects": [component],
        "components": [component],
        "metadata": {"scene_token": "scene-test"},
    }

    manifest_bytes, blocks = prepare_scene_binary(payload)
    body = b"".join(iter_scene_binary(manifest_bytes, blocks))
    magic, version, manifest_length = HEADER.unpack(body[: HEADER.size])
    assert magic == MAGIC
    assert version == 1
    assert (HEADER.size + manifest_length) % 8 == 0

    manifest = json.loads(body[HEADER.size : HEADER.size + manifest_length])
    assert manifest["components"][0]["component_name"] == "Cover"
    assert manifest["components"][0]["color"] == "#123456"
    assert "face_indices" not in manifest["components"][0]
    arrays = manifest["binary"]["arrays"]
    assert arrays["vertices"]["dtype"] == "float32"
    assert arrays["face_areas_mm2"]["dtype"] == "float32"
    assert arrays["feature_edge_points"]["dtype"] == "float32"
    data_start = HEADER.size + manifest_length
    source = arrays["face_source_ids"]
    assert struct.unpack_from("<I", body, data_start + source["byte_offset"])[0] == 42
    assert "face_ids" not in arrays
    assert "face_centroids" not in arrays
    assert "face_normals" not in arrays
    assert "face_material_codes" not in arrays
    assert manifest["components"][0]["binary_face_encoding"] == "range"
    component_faces = arrays["component_face_indices"]
    assert component_faces["count"] == 0
    assert len(body) == data_start + manifest["binary"]["byte_length"]


def test_json_fallback_materializes_binary_only_derived_geometry() -> None:
    payload = {
        "mesh": {
            "vertices": [(0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0)],
            "faces": [(0, 1, 2)],
            "face_ids": [],
            "face_component_ids": [3],
            "face_material_ids": ["default"],
            "face_source_ids": [12],
            "face_normals": [],
            "face_centroids": [],
            "face_areas_mm2": [2.0],
        }
    }

    response = materialize_scene_derived_geometry(payload)

    assert response["mesh"]["face_ids"] == [0]
    assert response["mesh"]["face_normals"] == [[0.0, 0.0, 1.0]]
    assert response["mesh"]["face_centroids"] == [
        [2.0 / 3.0, 2.0 / 3.0, 0.0]
    ]
    assert payload["mesh"]["face_normals"] == []
