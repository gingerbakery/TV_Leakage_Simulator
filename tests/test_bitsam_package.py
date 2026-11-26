from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import time
import unittest
import zipfile
from functools import partial
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.bitsam_package import compare_face_bindings, extract_package, read_scene_cache, write_package
from leakage_simulator.raytracer import run_direct_ray_trace


def scene_fixture(path):
    return {
        "schema_version": "mesh-scene.v1", "units": {"length": "mm"},
        "coordinate_system": {"handedness": "right", "axes": {"x": "model_x", "y": "model_y", "z": "model_z"}},
        "mesh": {
            "vertices": [(-10.123456789012, -10, 10), (10, -10, 10), (10, 10, 10), (-10, 10, 10)],
            "faces": [(0, 1, 2), (0, 2, 3)], "face_component_ids": [4, 4],
            "face_material_ids": ["mirror", "mirror"], "face_source_ids": [11, 11],
            "face_areas_mm2": [200.0, 200.0], "feature_edge_segments": [],
        },
        "components": [{"component_id": 4, "object_id": 4, "component_name": "Mirror", "face_indices": [0, 1], "face_count": 2}],
        "metadata": {"source_file": str(path), "face_count": 2, "vertex_count": 4, "component_count": 1},
    }


def project_fixture():
    return {
        "format": "tv-leakage-simulator-project", "schema_version": "bitsam-project.v1",
        "project_name": "한글 테스트", "saved_at": "2026-09-10T00:00:00Z",
        "cad": {"display_name": "mirror.step", "fingerprint": {"face_count": 2, "vertex_count": 4, "component_count": 1}},
        "workspace": {
            "faceColorOverrides": [{"componentId": 4, "faceIds": [0, 1], "color": "#ef4444"}],
            "materialAssignments": [{"targetType": "faces", "componentId": 4, "faceIds": [0, 1], "surfaceId": "mirror"}],
            "roiScopes": [{"clipBox": {"xMin": -2, "xMax": 2, "yMin": -3, "yMax": 3}}],
            "transformRules": [{"componentId": 4, "move": {"x": 0.01, "y": 0, "z": 0}}],
        },
        "analysis_result": {"receiver_results": [{"flux_grid_lumen": [[0.123, 0.456]]}]},
    }


def trace_request(token):
    return {
        "scene_token": token,
        "emitters": [{"emitter_id": "source", "emitter_type": "datum_plane", "center": [0, 0, 1],
                      "u_axis": [1, 0, 0], "v_axis": [0, 1, 0], "width_mm": 0.2, "height_mm": 0.2,
                      "direction_distribution": "gaussian", "gaussian_sigma_deg": 1, "power_lumen": 1, "ray_count": 200, "seed": 42}],
        "receivers": [{"receiver_id": "observer", "center": [0, 0, 0], "normal": [0, 0, 1],
                       "width_mm": 20, "height_mm": 20, "resolution": [8, 8]}],
        "optical_profiles": [{"profile_id": "mirror", "reflectance": 0.8, "scatter_model": "specular"}],
        "config": {"ray_count": 200, "max_depth": 2, "seed": 42, "compute_backend": "cpu", "store_ray_paths": True, "max_stored_paths": 12},
    }


class BitsamPackageTests(unittest.TestCase):
    def test_completed_trace_reuses_result_and_geometry_across_scene_tokens(self):
        precision_loader = Mock(return_value=self.scene["mesh"])

        def deferred(path):
            payload = scene_fixture(path)
            payload["_trace_mesh_loader"] = precision_loader
            return payload

        runtime = ApiRuntime(
            self.root / "repeat", scene_loader=deferred,
            trace_runner=partial(run_direct_ray_trace, intersection_dispatch="scalar"),
        )

        def run(request):
            job = runtime.start_raytrace_job(request)
            deadline = time.monotonic() + 5
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(0.01)
                job = runtime.raytrace_job_snapshot(job["job_id"])
            self.assertEqual(job["status"], "completed", job.get("error"))
            with runtime._state_lock:
                worker = runtime._raytrace_workers.get(job["job_id"])
            if worker is not None:
                self.assertTrue(worker.wait(5))
            return job

        scene = runtime.load_scene(str(self.source))
        request = trace_request(scene["metadata"]["scene_token"])
        first = run(request)
        self.assertFalse(first["geometry_cache_hit"])
        self.assertEqual(first["_trace_mesh_cache"], "resolved_or_waited")
        self.assertEqual(first["_prepared_geometry_cache"], "miss")
        refreshed = runtime.load_scene(str(self.source))
        request["scene_token"] = refreshed["metadata"]["scene_token"]
        request["resume_scope"] = "second-case"
        reused = run(request)
        self.assertEqual(reused["result_reused_from_job_id"], first["job_id"])
        self.assertEqual(reused["_result_cache_decision"], "hit")
        self.assertEqual(reused["result"], first["result"])
        self.assertEqual(reused["trace_elapsed_sec"], 0.0)

        forced = run(dict(request, restart_from_beginning=True))
        self.assertNotIn("result_reused_from_job_id", forced)
        self.assertEqual(forced["_result_cache_decision"], "forced_recompute")
        self.assertEqual(forced["_prepared_geometry_cache"], "hit")
        self.assertTrue(forced["geometry_cache_hit"])
        self.assertNotEqual(forced["result"]["run_id"], first["result"]["run_id"])
        request["optical_profiles"][0]["reflectance"] = 0.2
        changed = run(request)
        self.assertNotIn("result_reused_from_job_id", changed)
        self.assertEqual(changed["_result_cache_decision"], "miss")
        self.assertTrue(changed["geometry_cache_hit"])
        precision_loader.assert_called_once()

        preview = dict(request, resume_scope="preview:second-case", geometry_mode="preview")
        first_preview = run(preview)
        self.assertNotIn("result_reused_from_job_id", first_preview)
        self.assertEqual(run(preview)["result_reused_from_job_id"], first_preview["job_id"])

    def test_preview_evidence_round_trips_without_precision_result_or_live_job(self):
        self.project.pop("analysis_result")
        self.project["preview_result"] = {
            "format": "leak-preview-result.v1",
            "result": {"run_id": "preview-1", "stored_paths": [],
                       "metrics": {"_observer_probe_summary": {"complete": False, "stopped": True}}},
            "points": [{"position": [1.25, 2.5, -3.75], "relativeStrength": .5, "candidateId": "leak-2"}],
            "candidates": [{"id": "leak-2", "label": "Candidate 02"}],
            "selectedCandidateId": "leak-2", "highlight": None, "inputSignature": None,
        }
        write_package(self.package, self.project, self.source, self.scene, self.scene["mesh"])
        restored = ApiRuntime(self.root / "preview-evidence", scene_loader=scene_fixture).import_project(self.package)
        self.assertEqual(restored["project"]["preview_result"], self.project["preview_result"])
        self.assertNotIn("analysis_result", restored["project"])

    def test_full_detail_settings_round_trip_and_require_their_version_tag(self):
        self.project["schema_version"] = "bitsam-project.v1-detail1"
        self.project["leak_preview"] = {"detectionMode": "escape_directions", "observer": {
            "enabled": True, "search_mode": "full_detail", "spacing_mm": .5}}
        write_package(self.package, self.project, self.source, self.scene, self.scene["mesh"])
        with zipfile.ZipFile(self.package) as archive:
            settings = json.loads(archive.read("project.json"))
        self.assertEqual(settings["leak_preview"]["observer"]["spacing_mm"], .5)
        self.project["schema_version"] = "bitsam-project.v1-escape1"
        with self.assertRaisesRegex(ValueError, "Full detail"):
            write_package(self.root / "stale.bitsam", self.project, self.source, self.scene, self.scene["mesh"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "mirror.step"
        self.source.write_bytes(b"ISO-10303-21; original CAD bytes\x00\xff")
        self.scene = scene_fixture(self.source)
        self.project = project_fixture()
        self.package = self.root / "project.bitsam"

    def test_lossless_round_trip_without_source_file(self):
        write_package(self.package, self.project, self.source, self.scene, self.scene["mesh"])
        original = self.source.read_bytes()
        self.source.unlink()
        destination = self.root / "restored"
        manifest = extract_package(self.package, destination)
        scene = read_scene_cache(destination / "scene.bin")
        trace = read_scene_cache(destination / "trace.bin")
        self.assertTrue(manifest["trace_cached"])
        self.assertEqual((destination / manifest["source"]).read_bytes(), original)
        self.assertEqual(list(scene["mesh"]["vertices"]), self.scene["mesh"]["vertices"])
        self.assertEqual(list(trace["mesh"]["vertices"]), self.scene["mesh"]["vertices"])
        self.assertEqual(scene["components"], self.scene["components"])
        self.assertEqual(json.loads((destination / "project.json").read_text(encoding="utf-8")), self.project)

    def test_directional_escape_schema_is_preserved_and_cannot_be_downgraded(self):
        project = copy.deepcopy(self.project)
        project["schema_version"] = "bitsam-project.v1-escape1"
        project["leak_preview"] = {"detectionMode": "escape_directions"}
        write_package(self.package, project, self.source, self.scene, self.scene["mesh"])
        restored = self.root / "escape-restored"
        extract_package(self.package, restored)
        saved = json.loads((restored / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["leak_preview"]["detectionMode"], "escape_directions")
        project["schema_version"] = "bitsam-project.v1-preview1"
        with self.assertRaisesRegex(ValueError, "Directional escape"):
            write_package(self.package, project, self.source, self.scene, self.scene["mesh"])

    def test_preview_settings_schema_round_trips_and_fails_closed(self):
        preview = copy.deepcopy(self.project)
        preview["schema_version"] = "bitsam-project.v1-preview1"
        preview["leak_preview"] = {
            "sourceMode": "face",
            "sourceFaceIds": [],
            "sourceComponentIds": [],
            "sourceBodyFaceCount": 0,
            "quality": "balanced",
            "directions": ["pos_z"],
            "visualizationVisible": True,
            "ignoreAreasVisible": True,
            "blockersVisible": True,
            "ignoreAreas": [],
            "blockers": [{
                "id": "rib", "label": "Rib", "enabled": True,
                "baseCenter": [0, 0, 0], "uAxis": [1, 0, 0],
                "vAxis": [0, 1, 0], "normal": [0, 0, 1],
                "widthMm": 10, "heightMm": 5, "offsetMm": 0,
                "depthMm": 1, "reverse": False,
            }],
        }
        write_package(
            self.package, preview, self.source, self.scene, self.scene["mesh"],
        )
        restored = ApiRuntime(
            self.root / "preview-restore", scene_loader=scene_fixture,
        ).import_project(self.package)
        self.assertEqual(restored["project"]["leak_preview"], preview["leak_preview"])

        missing_payload = copy.deepcopy(preview)
        missing_payload.pop("leak_preview")
        with self.assertRaisesRegex(ValueError, "Leak Preview"):
            write_package(
                self.root / "missing-preview.bitsam",
                missing_payload,
                self.source,
                self.scene,
                self.scene["mesh"],
            )
        wrong_version = copy.deepcopy(preview)
        wrong_version["schema_version"] = "bitsam-project.v1"
        with self.assertRaisesRegex(ValueError, "Leak Preview"):
            write_package(
                self.root / "wrong-preview-version.bitsam",
                wrong_version,
                self.source,
                self.scene,
                self.scene["mesh"],
            )

    def test_cached_restore_trace_repeat_and_settings_change(self):
        first = ApiRuntime(self.root / "before", scene_loader=scene_fixture)
        loaded = first.load_scene(str(self.source))
        request = trace_request(loaded["metadata"]["scene_token"])
        baseline = first.run_raytrace_direct(request)
        self.project["analysis_result"] = baseline
        exported = first.export_project({"scene_token": request["scene_token"], "project": self.project})
        export_id = exported["download_url"].split("/")[-1]
        package, _ = first.project_export_file(export_id)
        self.source.unlink()
        loader = Mock(side_effect=AssertionError("CAD must not be re-imported"))
        second = ApiRuntime(self.root / "after", scene_loader=loader)
        restored = second.import_project(package)
        scene = second.load_scene(restored["cad"]["path"])
        request["scene_token"] = scene["metadata"]["scene_token"]
        repeated = second.run_raytrace_direct(request)
        self.assertEqual(restored["project"]["analysis_result"], json.loads(json.dumps(baseline)))
        self.assertEqual(repeated["receiver_grids"], baseline["receiver_grids"])
        self.assertEqual(repeated["stored_paths"], baseline["stored_paths"])
        self.assertEqual(repeated["metrics"]["observer"], baseline["metrics"]["observer"])
        self.assertGreater(baseline["metrics"]["observer"]["total_flux_lumen"], 0)
        changed = copy.deepcopy(request)
        changed["emitters"][0]["power_lumen"] = 0.5
        result = second.run_raytrace_direct(changed)
        self.assertAlmostEqual(result["metrics"]["observer"]["total_flux_lumen"], baseline["metrics"]["observer"]["total_flux_lumen"] * 0.5)
        self.assertTrue(second._build_trace_input_for_request(second._scene_mesh_for_request(request), request).geometry_cache_hit)
        changed_surface = copy.deepcopy(request)
        changed_surface["optical_profiles"].append({"profile_id": "face-matte", "reflectance": 0.4, "scatter_model": "specular"})
        changed_surface["optical_assignments"] = [{"assignment_id": "face-test", "target_type": "faces", "component_id": 4, "face_indices": [0], "profile_id": "face-matte"}]
        surface_result = second.run_raytrace_direct(changed_surface)
        self.assertAlmostEqual(surface_result["metrics"]["observer"]["total_flux_lumen"], baseline["metrics"]["observer"]["total_flux_lumen"] * 0.5)
        changed_transform = copy.deepcopy(request)
        changed_transform["transform_rules"] = [{"target_type": "component", "object_id": 4, "enabled": True, "move": {"x": 0, "y": 0, "z": 1}, "tilt": {"x": 0, "y": 0, "z": 0}}]
        prepared = second._build_trace_input_for_request(second._scene_mesh_for_request(request), changed_transform)
        self.assertFalse(prepared.geometry_cache_hit)
        self.assertEqual(prepared.mesh.face_vertices(0)[0][2], 11)
        self.assertEqual(second._scene_mesh_for_request(request)["vertices"][0][2], 10)
        loader.assert_not_called()

    def test_sphere_settings_results_and_retrace_survive_portable_restore(self):
        first = ApiRuntime(self.root / "sphere-before", scene_loader=scene_fixture)
        loaded = first.load_scene(str(self.source))
        request = trace_request(loaded["metadata"]["scene_token"])
        request["emitters"][0]["aim"] = {"enabled": True, "mode": "sphere", "distribution": "uniform_solid_angle"}
        baseline = first.run_raytrace_direct(request)
        self.project["workspace"]["emitters"] = baseline["emitters"]
        self.project["analysis_result"] = baseline
        exported = first.export_project({"scene_token": request["scene_token"], "project": self.project})
        package, _ = first.project_export_file(exported["download_url"].split("/")[-1])
        self.source.unlink()
        loader = Mock(side_effect=AssertionError("Sphere restore must reuse cached geometry"))
        second = ApiRuntime(self.root / "sphere-after", scene_loader=loader)
        restored = second.import_project(package)
        scene = second.load_scene(restored["cad"]["path"])
        request["scene_token"] = scene["metadata"]["scene_token"]
        request["emitters"] = copy.deepcopy(restored["project"]["workspace"]["emitters"])
        repeated = second.run_raytrace_direct(request)
        self.assertEqual(restored["project"]["analysis_result"], json.loads(json.dumps(baseline)))
        self.assertEqual(repeated["receiver_grids"], baseline["receiver_grids"])
        self.assertEqual(repeated["stored_paths"], baseline["stored_paths"])
        self.assertEqual(request["emitters"][0]["aim"]["sphere_lower_deg"], 180)
        request["emitters"][0]["aim"].update(sphere_lower_deg=5, sphere_beta_deg=180)
        changed = second.run_raytrace_direct(request)
        self.assertAlmostEqual(changed["metrics"]["observer"]["total_flux_lumen"], 1.0)
        self.assertNotEqual(changed["receiver_grids"], baseline["receiver_grids"])
        loader.assert_not_called()

    def test_resave_loaded_package_keeps_edited_setup_and_new_run(self):
        first = ApiRuntime(self.root / "initial", scene_loader=scene_fixture)
        scene = first.load_scene(str(self.source))
        request = trace_request(scene["metadata"]["scene_token"])
        baseline = first.run_raytrace_direct(request)
        self.project["workspace"].update(emitters=baseline["emitters"], receivers=baseline["receivers"])
        self.project["analysis_result"] = baseline
        exported = first.export_project({"scene_token": request["scene_token"], "project": self.project})
        original, _ = first.project_export_file(exported["download_url"].split("/")[-1])

        loader = Mock(side_effect=AssertionError("Re-save must reuse the embedded CAD"))
        editing = ApiRuntime(self.root / "editing", scene_loader=loader)
        imported = editing.import_project(original)
        scene = editing.load_scene(imported["cad"]["path"])
        request["scene_token"] = scene["metadata"]["scene_token"]
        request["emitters"][0]["center"] = [0.25, 0, 1]
        request["receivers"][0]["center"] = [0.25, 0, 0]
        request["receivers"][0]["acceptance_angle_deg"] = 60
        request["optical_profiles"][0]["reflectance"] = 0.4
        changed = editing.run_raytrace_direct(request)
        self.assertNotEqual(changed["run_id"], baseline["run_id"])
        self.assertLess(changed["metrics"]["observer"]["total_flux_lumen"], baseline["metrics"]["observer"]["total_flux_lumen"])
        edited_project = copy.deepcopy(imported["project"])
        edited_project["project_name"] = "manually-edited"
        edited_project["workspace"]["emitters"] = changed["emitters"]
        edited_project["workspace"]["receivers"] = changed["receivers"]
        edited_project["workspace"]["materialAssignments"][0]["opticalOverride"] = {
            "reflectance": 0.4, "loss": 0.6, "specularRatio": 1, "diffuseRatio": 0,
        }
        edited_project["analysis_result"] = changed
        saved = editing.export_project({"scene_token": request["scene_token"], "project": edited_project})
        renamed, _ = editing.project_export_file(saved["download_url"].split("/")[-1])
        reopened = ApiRuntime(self.root / "reopened", scene_loader=loader).import_project(renamed)
        self.assertEqual(reopened["project"], json.loads(json.dumps(edited_project)))
        self.assertNotEqual(reopened["project"]["workspace"], imported["project"]["workspace"])
        loader.assert_not_called()

    def test_save_embeds_deferred_trace_and_reopen_does_not_import_cad(self):
        trace_loader = Mock(return_value=self.scene["mesh"])
        def deferred(path):
            payload = scene_fixture(path)
            payload["_trace_mesh_loader"] = trace_loader
            return payload
        first = ApiRuntime(self.root / "before", scene_loader=deferred)
        scene = first.load_scene(str(self.source))
        exported = first.export_project({"scene_token": scene["metadata"]["scene_token"], "project": self.project})
        self.assertTrue(exported["trace_cached"])
        trace_loader.assert_called_once()
        package, _ = first.project_export_file(exported["download_url"].split("/")[-1])
        forbidden = Mock(side_effect=AssertionError("CAD unexpectedly imported"))
        second = ApiRuntime(self.root / "after", scene_loader=forbidden)
        restored = second.import_project(package)
        scene = second.load_scene(restored["cad"]["path"])
        second.run_raytrace_direct(trace_request(scene["metadata"]["scene_token"]))
        forbidden.assert_not_called()

    def test_export_can_explicitly_omit_the_precision_mesh(self):
        trace_loader = Mock(side_effect=AssertionError("Trace mesh unexpectedly built"))

        def deferred(path):
            payload = scene_fixture(path)
            payload["_trace_mesh_loader"] = trace_loader
            return payload

        runtime = ApiRuntime(self.root / "before", scene_loader=deferred)
        scene = runtime.load_scene(str(self.source))
        exported = runtime.export_project({
            "scene_token": scene["metadata"]["scene_token"],
            "project": self.project,
            "include_trace_mesh": False,
        })
        self.assertFalse(exported["trace_cached"])
        trace_loader.assert_not_called()

    def test_legacy_load_checks_viewer_and_reuses_checked_trace_loader(self):
        trace_loader = Mock(return_value=self.scene["mesh"])
        write_package(self.package, self.project, self.source, self.scene, None)

        def rebuild_with_different_feature_edges(path):
            payload = scene_fixture(path)
            payload["_trace_mesh_loader"] = trace_loader
            payload["mesh"]["feature_edge_segments"] = [
                {"start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0], "component_id": None},
            ]
            return payload

        second_loader = Mock(side_effect=rebuild_with_different_feature_edges)
        second = ApiRuntime(self.root / "after", scene_loader=second_loader)
        restored = second.import_project(self.package)
        self.assertIsNone(restored["trace_binding_error"])
        self.source.unlink()
        loaded = second.load_scene(restored["cad"]["path"])
        second_loader.assert_called_once()
        trace_loader.assert_not_called()
        request = trace_request(loaded["metadata"]["scene_token"])
        second.run_raytrace_direct(request)
        second.run_raytrace_direct(request)
        self.assertEqual(second_loader.call_count, 1)
        self.assertEqual(trace_loader.call_count, 1)
        exported_again = second.export_project({"scene_token": request["scene_token"], "project": self.project})
        self.assertTrue(exported_again["trace_cached"])

    def test_deferred_rebuild_rejects_changed_face_identity(self):
        write_package(self.package, self.project, self.source, self.scene, None)
        def changed(path):
            payload = scene_fixture(path)
            payload["mesh"]["face_source_ids"] = [22, 22]
            return payload
        runtime = ApiRuntime(self.root / "after", scene_loader=changed)
        with patch("leakage_simulator.api.runtime.log_event") as log:
            restored = runtime.import_project(self.package)
        event = next(call.kwargs for call in log.call_args_list if call.args[0] == "cad.portable_binding_check")
        self.assertEqual(event["triangle_topology_diff_count"], 0)
        self.assertEqual(event["face_source_ids_diff_count"], 2)
        self.assertEqual(event["mismatch_reasons"], ["face_source_ids"])
        self.assertIn("면 할당을 확인하세요", restored["trace_binding_error"])
        self.assertEqual(restored["project"]["analysis_result"], self.project["analysis_result"])
        scene = runtime.load_scene(restored["cad"]["path"])
        with self.assertRaisesRegex(ValueError, "면 할당을 확인하세요"):
            runtime.run_raytrace_direct(trace_request(scene["metadata"]["scene_token"]))
        for evict in (False, True):
            with self.subTest(evict=evict):
                if evict:
                    runtime._scene_payload_cache.clear()
                    scene = runtime.load_scene(restored["cad"]["path"])
                exported = runtime.export_project({
                    "scene_token": scene["metadata"]["scene_token"], "project": restored["project"],
                })
                self.assertFalse(exported["trace_cached"])
                saved, _ = runtime.project_export_file(exported["download_url"].split("/")[-1])
                with zipfile.ZipFile(saved) as archive:
                    self.assertIn("scene.bin", archive.namelist())
                    self.assertNotIn("trace.bin", archive.namelist())
                    self.assertEqual(archive.read("cad/original.step"), self.source.read_bytes())
                    self.assertEqual(json.loads(archive.read("project.json")), restored["project"])
                reimported = ApiRuntime(self.root / "reopened", scene_loader=changed).import_project(saved)
                self.assertEqual(reimported["project"], restored["project"])

    def test_binding_comparison_reports_winding_and_label_changes(self):
        original = self.scene["mesh"]
        modified = copy.deepcopy(original)
        modified["faces"][0] = (0, 2, 1)
        modified["face_component_ids"][1] = 8
        modified["face_material_ids"][1] = "glass"
        report = compare_face_bindings(original, modified)
        self.assertFalse(report["equivalent"])
        self.assertEqual(report["triangle_topology_diff_count"], 1)
        self.assertEqual(report["face_component_ids_diff_count"], 1)
        self.assertEqual(report["face_material_ids_diff_count"], 1)

    def test_binding_comparison_rejects_coordinate_movement_outside_tolerance(self):
        original = self.scene["mesh"]
        modified = copy.deepcopy(original)
        x, y, z = modified["vertices"][0]
        modified["vertices"][0] = (x + 0.001, y, z)
        report = compare_face_bindings(original, modified)
        self.assertFalse(report["equivalent"])
        self.assertEqual(report["mismatch_reasons"], ["coordinate_delta"])
        self.assertAlmostEqual(report["max_sorted_coordinate_delta"], 0.001)

    def test_deferred_rebuild_accepts_reordered_face_bindings_with_small_coordinate_drift(self):
        self.scene["mesh"]["face_source_ids"] = [11, 12]
        write_package(self.package, self.project, self.source, self.scene, None)

        def reordered(path):
            payload = scene_fixture(path)
            mesh = payload["mesh"]
            mesh["vertices"].reverse()
            x, y, z = mesh["vertices"][0]
            mesh["vertices"][0] = (x - 1e-10, y, z)
            mesh["faces"] = [(1, 0, 3), (2, 1, 3)]
            mesh["face_source_ids"] = [12, 11]
            return payload

        runtime = ApiRuntime(self.root / "after", scene_loader=reordered)
        restored = runtime.import_project(self.package)
        scene = runtime.load_scene(restored["cad"]["path"])
        token = scene["metadata"]["scene_token"]
        runtime.run_raytrace_direct(trace_request(token))
        request = {"emitters": [{"emitter_type": "face", "face_indices": [0]}]}
        mapped = runtime._map_viewer_faces_to_trace(runtime._scene_mesh_cache[token], request)
        self.assertEqual(mapped["emitters"][0]["face_indices"], [1])

    def test_cache_eviction_reloads_package_not_cad(self):
        write_package(self.package, self.project, self.source, self.scene, self.scene["mesh"])
        loader = Mock(side_effect=AssertionError("CAD unexpectedly parsed"))
        runtime = ApiRuntime(self.root / "after", scene_loader=loader, max_cached_scenes=1)
        restored = runtime.import_project(self.package)
        initial = runtime.load_scene(restored["cad"]["path"])
        runtime._scene_payload_cache.clear()
        loaded = runtime.load_scene(restored["cad"]["path"])
        self.assertNotEqual(initial["metadata"]["scene_token"], loaded["metadata"]["scene_token"])
        runtime.run_raytrace_direct(trace_request(loaded["metadata"]["scene_token"]))
        loader.assert_not_called()

    def test_corruption_path_traversal_and_unknown_version_rejected(self):
        write_package(self.package, self.project, self.source, self.scene, None)
        with zipfile.ZipFile(self.package) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        for kind in ("checksum", "path", "version", "precision", "indices"):
            with self.subTest(kind=kind):
                changed = dict(entries)
                manifest = json.loads(changed["manifest.json"])
                if kind == "checksum":
                    changed["cad/original.step"] += b"corrupt"
                elif kind == "path":
                    changed["../escape"] = b"forbidden"
                elif kind == "version":
                    manifest["cache_contract"] = "future-cache"
                elif kind in {"precision", "indices"}:
                    bad_scene = copy.deepcopy(self.scene)
                    if kind == "indices":
                        bad_scene["mesh"]["faces"] = [(0, 1, 900), (0, 2, 3)]
                    from leakage_simulator.scene_binary import prepare_scene_binary, iter_scene_binary
                    header, blocks = prepare_scene_binary(bad_scene, coordinate_dtype="float32" if kind == "precision" else "float64")
                    changed["scene.bin"] = b"".join(iter_scene_binary(header, blocks))
                    manifest["entries"]["scene.bin"] = {"size": len(changed["scene.bin"]), "sha256": hashlib.sha256(changed["scene.bin"]).hexdigest()}
                changed["manifest.json"] = json.dumps(manifest).encode()
                path = self.root / (kind + ".bitsam")
                with zipfile.ZipFile(path, "w") as archive:
                    for name, data in changed.items():
                        archive.writestr(name, data)
                with self.assertRaises(ValueError):
                    ApiRuntime(self.root / kind).import_project(path)
        self.assertFalse((self.root / "escape").exists())

    def test_http_export_download_import_and_scene_binary(self):
        runtime = ApiRuntime(self.root / "api", scene_loader=scene_fixture)
        with TestClient(create_app(runtime)) as client:
            loaded = client.get("/api/scene", params={"cad": str(self.source)}).json()
            exported = client.post("/api/projects/export", json={"scene_token": loaded["metadata"]["scene_token"], "project": self.project})
            self.assertEqual(exported.status_code, 200, exported.text)
            downloaded = client.get(exported.json()["download_url"])
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(len(downloaded.content), exported.json()["size_bytes"])
            self.assertEqual(client.get(exported.json()["download_url"]).status_code, 404)
            self.source.unlink()
            restored = client.post("/api/projects/import", content=downloaded.content)
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()["project"], self.project)
            scene = client.get("/api/scene", params={"cad": restored.json()["cad"]["path"], "format": "binary"})
            self.assertEqual(scene.status_code, 200)
            self.assertTrue(scene.content.startswith(b"BITSAMSC"))

    def test_missing_source_expired_session_or_wrong_model_save_rejected(self):
        runtime = ApiRuntime(self.root, scene_loader=scene_fixture)
        scene = runtime.load_scene(str(self.source))
        token = scene["metadata"]["scene_token"]
        wrong = copy.deepcopy(self.project)
        wrong["cad"]["fingerprint"]["face_count"] = 7
        with self.assertRaisesRegex(ValueError, "do not match"):
            runtime.export_project({"scene_token": token, "project": wrong})
        with self.assertRaisesRegex(ValueError, "expired"):
            runtime.export_project({"scene_token": "invalid", "project": self.project})
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "Original CAD"):
            runtime.export_project({"scene_token": token, "project": self.project})
        self.assertEqual(list(runtime.upload_dir.glob("*.writing")), [])


if __name__ == "__main__":
    unittest.main()
