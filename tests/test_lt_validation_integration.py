"""Synthetic production save/import/compare/retrace regression; no user data."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.lt_validation.bitsam import read_bitsam
from test_bitsam_package import project_fixture, scene_fixture, trace_request
from test_bsdf_transport import synthetic_asset


class LtProductionIntegrationTests(unittest.TestCase):
    def test_saved_results_and_reanalysis_survive_lt_validation(self):
        for use_bsdf in (False, True):
            with self.subTest(bsdf=use_bsdf), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "synthetic.step"
                source.write_bytes(b"ISO-10303-21; synthetic regression fixture")
                runtime = ApiRuntime(root / "live", scene_loader=scene_fixture)
                scene = runtime.load_scene(str(source))
                request = trace_request(scene["metadata"]["scene_token"])
                request["emitters"][0]["direction_distribution"] = "lambertian"
                request["receivers"][0].update(u_axis=[1, 0, 0], v_axis=[0, 1, 0])
                if use_bsdf:
                    asset = synthetic_asset()
                    request["bsdf_assets"] = [asset]
                    request["optical_profiles"][0].update(
                        reflectance=0, scatter_model="none", bsdf_asset_id=asset["id"])
                original_request = copy.deepcopy(request)
                before = runtime.run_raytrace_direct(request)
                self.assertGreater(before["receiver_hit_count"], 0)
                project = project_fixture()
                project["analysis_result"] = before
                project["workspace"].update(
                    emitters=before["emitters"], receivers=before["receivers"],
                    rayTraceConfig=request["config"], opticalProfiles=request["optical_profiles"])
                if use_bsdf:
                    project["schema_version"] = "bitsam-project.v1-bsdf1"
                    project["workspace"]["bsdfAssets"] = [asset]
                    project["workspace"]["materialAssignments"][0]["bsdfAssetId"] = asset["id"]
                saved = runtime.export_project({"scene_token": request["scene_token"], "project": project})
                package, _ = runtime.project_export_file(saved["download_url"].split("/")[-1])
                original_bytes = package.read_bytes()
                # Register the real production app, not a router-only mock.
                with TestClient(create_app(runtime=runtime)) as client, patch.object(
                    runtime, "import_project", side_effect=AssertionError("LT must not restore the live project")
                ), patch.object(
                    runtime, "run_raytrace_direct", side_effect=AssertionError("LT must not run ray tracing")
                ):
                    imported = client.post("/api/lt-validation/import?filename=synthetic.bitsam", content=original_bytes)
                    self.assertEqual(imported.status_code, 200, imported.text)
                    dataset = imported.json()
                    receiver = dataset["receivers"][0]
                    self.assertEqual(receiver["flux_lm"], before["receiver_grids"][0]["flux_lumen"])
                    compared = client.post("/api/lt-validation/compare", json={
                        "baseline_id": "self", "cases": [{
                            "id": "self", "name": "Synthetic identity", "sim": dataset, "lt": dataset,
                            "sim_receiver_id": receiver["id"], "lt_receiver_id": receiver["id"],
                        }],
                    })
                    self.assertEqual(compared.status_code, 200, compared.text)
                    result = compared.json()["cases"][0]
                    self.assertEqual(result["status"], "ready", result["issues"])
                    self.assertEqual(result["flux_deviation_pct"], 0)
                    self.assertAlmostEqual(result["pearson"], 1)
                    self.assertEqual(result["overlap_pct"], 100)
                    self.assertTrue(all(x == 0 for row in result["plots"]["difference_pp"] for x in row))
                self.assertEqual(package.read_bytes(), original_bytes)
                self.assertEqual(request, original_request)
                self.assertEqual(runtime.run_raytrace_direct(request)["receiver_grids"], before["receiver_grids"])
                loader = Mock(side_effect=AssertionError("Restore must reuse embedded geometry"))
                restored = ApiRuntime(root / "restored", scene_loader=loader)
                loaded = restored.import_project(package)
                self.assertEqual(loaded["project"]["analysis_result"], json.loads(json.dumps(before)))
                workspace = loaded["project"]["workspace"]
                loaded_scene = restored.load_scene(loaded["cad"]["path"])
                retrace_request = {
                    "scene_token": loaded_scene["metadata"]["scene_token"],
                    "emitters": workspace["emitters"], "receivers": workspace["receivers"],
                    "config": workspace["rayTraceConfig"], "optical_profiles": workspace["opticalProfiles"],
                    "bsdf_assets": workspace.get("bsdfAssets", []),
                }
                repeated = restored.run_raytrace_direct(retrace_request)
                self.assertEqual(repeated["receiver_grids"], before["receiver_grids"])
                self.assertEqual(repeated["stored_paths"], before["stored_paths"])
                loader.assert_not_called()

    def test_reader_explicitly_accepts_current_settings_schemas_only(self):
        from test_lt_validation_bitsam import project_fixture as saved_fixture

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "synthetic.bitsam"
            for schema in (
                "bitsam-project.v1",
                "bitsam-project.v1-bsdf1",
                "bitsam-project.v1-preview1",
                "bitsam-project.v99",
            ):
                with self.subTest(schema=schema):
                    project = saved_fixture()
                    project["schema_version"] = schema
                    if schema == "bitsam-project.v1-preview1":
                        project["leak_preview"] = {"blockers": [], "ignoreAreas": []}
                    path.write_text(json.dumps(project), encoding="utf-8")
                    if schema.endswith("v99"):
                        with self.assertRaises(ValueError):
                            read_bitsam(path)
                    else:
                        result = read_bitsam(path)
                        self.assertEqual(result["receivers"][0]["flux_lm"], [[1e-4, 2e-4], [3e-4, 4e-4]])
