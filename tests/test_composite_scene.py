from leakage_simulator.composite_scene import merge_scene_payloads


def _scene(name: str, component_id: int, x: float) -> dict:
    mesh = {
        "vertices": [[x, 0, 0], [x + 1, 0, 0], [x, 1, 0]],
        "faces": [[0, 1, 2]],
        "face_ids": [0],
        "face_source_ids": [0],
        "face_component_ids": [component_id],
        "face_material_ids": ["default"],
        "face_normals": [[0, 0, 1]],
        "face_centroids": [[x + 1 / 3, 1 / 3, 0]],
        "face_areas_mm2": [0.5],
        "feature_edge_segments": [{"start": [x, 0, 0], "end": [x + 1, 0, 0], "component_id": component_id}],
    }
    component = {
        "object_id": component_id, "component_id": component_id,
        "object_name": name, "component_name": name, "face_indices": [0],
        "face_count": 1, "area_mm2": 0.5, "bbox_min": [x, 0, 0],
        "bbox_max": [x + 1, 1, 0], "is_truncated": False, "color": "#123456",
    }
    return {
        "schema_version": "mesh-scene.v1", "units": {"length": "mm"},
        "coordinate_system": {"handedness": "right", "axes": {"x": "model_x", "y": "model_y", "z": "model_z"}},
        "mesh": mesh, "objects": [component], "components": [component],
        "metadata": {"face_count": 1, "vertex_count": 3, "component_count": 1,
                     "source_file": name, "synthetic": False, "import_note": "",
                     "receiver_face_hint": [], "scene_token": "ignored"},
    }


def test_merge_scene_payloads_offsets_geometry_and_preserves_accessory_identity():
    merged, component_maps, source_offsets = merge_scene_payloads(
        [_scene("Main", 7, 0), _scene("Board", 7, 10)],
        ["main.step", "power-board.step"],
    )
    assert merged["mesh"]["faces"] == [[0, 1, 2], [3, 4, 5]]
    assert merged["mesh"]["face_component_ids"] == [7, 8]
    assert merged["mesh"]["face_source_ids"] == [0, 1]
    assert merged["components"][0]["component_name"] == "Main"
    assert merged["components"][1]["component_name"] == "power-board / Board"
    assert merged["components"][1]["face_indices"] == [1]
    assert merged["components"][1]["color"] == "#123456"
    assert merged["metadata"]["source_files"] == ["main.step", "power-board.step"]
    assert merged["metadata"]["component_count"] == 2
    assert component_maps == [{7: 7}, {7: 8}]
    assert source_offsets == [0, 1]
