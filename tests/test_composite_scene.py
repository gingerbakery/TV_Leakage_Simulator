from pathlib import Path
import tempfile
import unittest

from leakage_simulator.composite_scene import merge_scene_payloads
from leakage_simulator.api import ApiRuntime
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.roi import _FaceSourceIds, _MeshMaterialIds, _TriangleFaceRows

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


def _assert_merge_scene_payloads_offsets_geometry_and_preserves_accessory_identity():
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


def _assert_accessory_import_with_live_mesh_sequences(tmp_path, deferred):
    payloads = []
    trace_calls = []

    def loader(path):
        payload = _scene("Part", 7, 0)
        mesh = TriangleMesh()
        for point in payload["mesh"]["vertices"]:
            mesh.add_vertex(tuple(point))
        mesh.add_face(0, 1, 2, "default", {"face_index": 0})
        payload["mesh"]["faces"] = _TriangleFaceRows(mesh.faces)
        payload["mesh"]["face_material_ids"] = _MeshMaterialIds(mesh)
        payload["mesh"]["face_source_ids"] = _FaceSourceIds(mesh)
        if deferred:
            def trace_loader():
                trace_calls.append(path)
                return payload["mesh"]
            payload["_trace_mesh_loader"] = trace_loader
        else:
            payload["_trace_mesh"] = payload["mesh"]
        payloads.append(payload)
        return payload

    runtime = ApiRuntime(tmp_path, scene_loader=loader)
    combined = runtime.load_composite_scene(["main.step", "accessory.step"])
    assert combined["mesh"]["faces"] == [[0, 1, 2], [3, 4, 5]]
    assert combined["mesh"]["face_material_ids"] == ["default", "default"]
    assert combined["mesh"]["face_source_ids"] == [0, 1]
    assert combined["mesh"]["face_component_ids"] == [7, 8]
    assert "_trace_mesh" not in combined
    assert "_trace_mesh_loader" not in combined
    assert trace_calls == []
    token = combined["metadata"]["scene_token"]
    trace = runtime._resolve_deferred_trace_mesh(runtime._scene_mesh_cache[token])
    assert trace["faces"] == combined["mesh"]["faces"]
    assert trace["face_component_ids"] == [7, 8]
    assert len(trace_calls) == (2 if deferred else 0)
    assert list(payloads[0]["mesh"]["faces"]) == [(0, 1, 2)]
    combined["components"][0]["face_indices"].append(99)
    assert payloads[0]["components"][0]["face_indices"] == [0]


class CompositeSceneTests(unittest.TestCase):
    def test_merge_scene_payloads_offsets_geometry_and_preserves_accessory_identity(self):
        _assert_merge_scene_payloads_offsets_geometry_and_preserves_accessory_identity()

    def test_accessory_import_with_live_mesh_sequences(self):
        for deferred in (False, True):
            with self.subTest(deferred=deferred), tempfile.TemporaryDirectory() as directory:
                _assert_accessory_import_with_live_mesh_sequences(Path(directory), deferred)
