from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from leakage_simulator.api import ApiRuntime
from test_bitsam_package import project_fixture, scene_fixture
from test_optical_transport_audit import override_payload, override_scene


def split_resolution_scene(path):
    scene = scene_fixture(path)
    viewer = override_scene()
    viewer["face_source_ids"] = [7, 19]
    scene["mesh"] = viewer
    trace = copy.deepcopy(viewer)
    trace["vertices"].append([0, -10 / 3, 10])
    trace["faces"] = [[0, 1, 2], [3, 4, 6], [4, 5, 6], [5, 3, 6]]
    trace["face_component_ids"] = [7, 8, 8, 8]
    trace["face_material_ids"] = ["default"] * 4
    trace["face_source_ids"] = [7, 19, 19, 19]
    scene["_trace_mesh"] = trace
    scene["components"] = [
        {"component_id": 7, "object_id": 7, "face_indices": [0], "face_count": 1},
        {"component_id": 8, "object_id": 8, "face_indices": [1], "face_count": 1},
    ]
    scene["metadata"].update({"face_count": 2, "vertex_count": 6, "component_count": 2})
    return scene


class OpticalPortableAuditTests(unittest.TestCase):
    def test_precision_mapping_roi_and_override_survive_bitsam_restore_and_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.step"
            source.write_bytes(b"ISO-10303-21; audit fixture")
            runtime = ApiRuntime(root / "before", scene_loader=split_resolution_scene)
            loaded = runtime.load_scene(str(source))
            request = override_payload()
            request.update({"scene_token": loaded["metadata"]["scene_token"], "roi_faces": [1]})
            baseline = runtime.run_raytrace_direct(request)
            self.assertAlmostEqual(baseline["metrics"]["receiver"]["total_flux_lumen"], 0.9, places=12)
            prepared = runtime._build_trace_input_for_request(runtime._scene_mesh_for_request(request), request)
            self.assertTrue(prepared.geometry_cache_hit)
            self.assertEqual(prepared.optical_assignments[0].face_indices, [1, 2, 3])
            self.assertEqual([prepared.mesh.metadata(index)["source_face_index"] for index in range(3)], [1, 2, 3])
            project = project_fixture()
            project["cad"]["fingerprint"] = {"face_count": 2, "vertex_count": 6, "component_count": 2}
            project["analysis_result"] = baseline
            project["workspace"]["audit_request"] = request
            exported = runtime.export_project({"scene_token": request["scene_token"], "project": project})
            package, _ = runtime.project_export_file(exported["download_url"].split("/")[-1])
            source.unlink()
            loader = Mock(side_effect=AssertionError("Restoring cached geometry must not reimport CAD"))
            restored_runtime = ApiRuntime(root / "after", scene_loader=loader)
            restored = restored_runtime.import_project(package)
            scene = restored_runtime.load_scene(restored["cad"]["path"])
            restored_request = restored["project"]["workspace"]["audit_request"]
            restored_request["scene_token"] = scene["metadata"]["scene_token"]
            repeated = restored_runtime.run_raytrace_direct(restored_request)
            self.assertEqual(restored["project"]["analysis_result"], json.loads(json.dumps(baseline)))
            self.assertEqual(repeated["receiver_grids"], baseline["receiver_grids"])
            restored_request["optical_profiles"][1]["reflectance"] = 0.6
            edited = restored_runtime.run_raytrace_direct(restored_request)
            self.assertAlmostEqual(edited["metrics"]["receiver"]["total_flux_lumen"], 0.6, places=12)
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
