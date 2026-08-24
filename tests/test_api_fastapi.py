from __future__ import annotations

import tempfile
import sys
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.api import API_VERSION, ApiRuntime, create_app


class _FakeTraceResult:
    def to_dict(self):
        return {
            "run_id": "run_test",
            "total_rays": 2,
        }


def _scene_loader(cad_path: str):
    return {
        "schema_version": "mesh-scene.v1",
        "mesh": {
            "vertices": [],
            "faces": [],
        },
        "metadata": {
            "source_file": cad_path,
        },
    }


def _trace_input_builder(scene_mesh, request_payload):
    return SimpleNamespace(
        scene_mesh=scene_mesh,
        request_payload=request_payload,
        emitters=[
            SimpleNamespace(
                ray_count=2,
                enabled=True,
            )
        ],
    )


def _trace_runner(trace_input, progress_callback=None, should_stop=None):
    if progress_callback is not None:
        progress_callback(1, 2)
        progress_callback(2, 2)
    return _FakeTraceResult()


class FastApiLayerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime = ApiRuntime(
            Path(self.temp_dir.name),
            scene_loader=_scene_loader,
            trace_input_builder=_trace_input_builder,
            trace_runner=_trace_runner,
        )
        self.client = TestClient(create_app(self.runtime))

    def test_default_runtime_reuses_prepared_bvh_for_non_geometry_changes(self):
        runtime = ApiRuntime(Path(self.temp_dir.name))
        scene_mesh = {
            "vertices": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            "faces": [[0, 1, 2]],
            "face_component_ids": [1],
            "face_material_ids": ["default"],
        }
        payload = {
            "scene_token": "scene-cache-test",
            "emitters": [{
                "emitter_id": "emitter_001",
                "emitter_type": "datum_plane",
                "center": [0, 0, 1],
                "u_axis": [1, 0, 0],
                "v_axis": [0, 1, 0],
                "width_mm": 1,
                "height_mm": 1,
            }],
            "receivers": [{
                "receiver_id": "receiver_001",
                "center": [0, 0, 2],
                "normal": [0, 0, -1],
                "width_mm": 1,
                "height_mm": 1,
            }],
        }

        first = runtime._build_trace_input_for_request(scene_mesh, payload)
        changed_receiver = dict(payload)
        changed_receiver["receivers"] = [
            {**payload["receivers"][0], "width_mm": 2}
        ]
        second = runtime._build_trace_input_for_request(scene_mesh, changed_receiver)

        self.assertFalse(first.geometry_cache_hit)
        self.assertTrue(second.geometry_cache_hit)
        self.assertIs(first.mesh, second.mesh)

        changed_geometry = dict(payload)
        changed_geometry["excluded_component_ids"] = [1]
        third = runtime._build_trace_input_for_request(scene_mesh, changed_geometry)
        self.assertFalse(third.geometry_cache_hit)
        self.assertIsNot(first.mesh, third.mesh)

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_system_endpoints_expose_api_identity(self):
        root_response = self.client.get("/")
        health_response = self.client.get("/health")
        dev_response = self.client.get("/dev-status")
        ping_response = self.client.get("/_ping")

        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(
            root_response.json()["api_version"],
            API_VERSION,
        )
        self.assertEqual(
            health_response.text,
            "ok api_version={}".format(API_VERSION),
        )
        self.assertTrue(dev_response.json()["ok"])
        self.assertIn("boot_token", dev_response.json())
        self.assertEqual(ping_response.text, "pong")

    def test_dev_status_exposes_explicit_environment_boot_token(self):
        expected_token = "gpu-source-launch-0123456789abcdef"
        with patch.dict(
            "os.environ",
            {"LEAKAGE_BOOT_TOKEN": expected_token},
        ):
            client = TestClient(create_app(self.runtime))
        try:
            response = client.get("/dev-status")
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["boot_token"], expected_token)

    def test_dev_status_keeps_generated_time_token_without_environment_value(self):
        with patch.dict("os.environ", {}, clear=True):
            client = TestClient(create_app(self.runtime))
        try:
            response = client.get("/dev-status")
        finally:
            client.close()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["boot_token"].isdigit())

    def test_production_frontend_is_served_from_same_origin(self):
        frontend_dist = (
            Path(self.temp_dir.name) / "frontend" / "dist"
        )
        assets_dir = frontend_dist / "assets"
        assets_dir.mkdir(parents=True)
        (frontend_dist / "index.html").write_text(
            "<html><body>React production shell</body></html>",
            encoding="utf-8",
        )
        (assets_dir / "app.js").write_text(
            "window.__APP_READY__ = true",
            encoding="utf-8",
        )
        client = TestClient(create_app(self.runtime))
        try:
            root_response = client.get("/")
            asset_response = client.get("/assets/app.js")
            route_response = client.get("/workspace/result")
            unknown_api_response = client.get("/api/unknown")
        finally:
            client.close()

        self.assertEqual(root_response.status_code, 200)
        self.assertIn("React production shell", root_response.text)
        self.assertEqual(asset_response.status_code, 200)
        self.assertIn("__APP_READY__", asset_response.text)
        self.assertEqual(route_response.status_code, 200)
        self.assertIn("React production shell", route_response.text)
        self.assertEqual(unknown_api_response.status_code, 404)

    def test_scene_endpoint_caches_mesh_and_returns_token(self):
        response = self.client.get(
            "/api/scene",
            params={"cad": "fixture.step"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["metadata"]["source_file"],
            "fixture.step",
        )
        self.assertTrue(
            payload["metadata"]["scene_token"].startswith("scene_")
        )

    def test_duplicate_scene_loads_share_one_active_import(self):
        loader_started = threading.Event()
        release_loader = threading.Event()
        load_count = 0

        def slow_loader(cad_path: str):
            nonlocal load_count
            load_count += 1
            loader_started.set()
            release_loader.wait(timeout=2.0)
            return _scene_loader(cad_path)

        runtime = ApiRuntime(
            Path(self.temp_dir.name),
            scene_loader=slow_loader,
            trace_input_builder=_trace_input_builder,
            trace_runner=_trace_runner,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(runtime.load_scene, "same.step")
            self.assertTrue(loader_started.wait(timeout=1.0))
            second = executor.submit(runtime.load_scene, "same.step")
            time.sleep(0.05)
            release_loader.set()
            first_payload = first.result(timeout=2.0)
            second_payload = second.result(timeout=2.0)

        self.assertEqual(load_count, 1)
        self.assertNotEqual(
            first_payload["metadata"]["scene_token"],
            second_payload["metadata"]["scene_token"],
        )
        runtime.load_scene("same.step")
        self.assertEqual(load_count, 1)

    def test_upload_preserves_binary_contract_and_validates_suffix(self):
        response = self.client.post(
            "/api/upload",
            params={"filename": "TV frame.step"},
            content=b"STEP-DATA",
            headers={"Content-Type": "application/octet-stream"},
        )
        invalid_response = self.client.post(
            "/api/upload",
            params={"filename": "notes.txt"},
            content=b"not-cad",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["display_name"], "TV_frame.step")
        self.assertEqual(
            Path(payload["path"]).read_bytes(),
            b"STEP-DATA",
        )
        self.assertEqual(invalid_response.status_code, 400)
        self.assertIn(
            "Supported CAD formats",
            invalid_response.text,
        )

    def test_xt_upload_is_rejected_instead_of_showing_synthetic_body(self):
        response = self.client.post(
            "/api/upload",
            params={"filename": "assembly.x_t"},
            content=b"PARASOLID-DATA",
            headers={"Content-Type": "application/octet-stream"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(".x_t is not supported", response.text)
        self.assertIn("STEP AP214 or AP242", response.text)

    def test_direct_and_async_raytrace_share_scene_cache(self):
        scene_response = self.client.get(
            "/api/scene",
            params={"cad": "fixture.step"},
        )
        scene_token = scene_response.json()["metadata"]["scene_token"]
        request_payload = {
            "scene_token": scene_token,
            "emitters": [
                {
                    "enabled": True,
                    "ray_count": 2,
                }
            ],
        }

        direct_response = self.client.post(
            "/api/raytrace/direct",
            json=request_payload,
        )
        start_response = self.client.post(
            "/api/raytrace/start",
            json=request_payload,
        )

        self.assertEqual(direct_response.status_code, 200)
        self.assertEqual(
            direct_response.json()["run_id"],
            "run_test",
        )
        self.assertEqual(start_response.status_code, 202)
        job_id = start_response.json()["job_id"]

        job = None
        for _ in range(50):
            status_response = self.client.get(
                "/api/raytrace/status",
                params={"job_id": job_id},
            )
            job = status_response.json()
            if job["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["processed_rays"], 2)
        self.assertEqual(job["result"]["run_id"], "run_test")

    def test_expired_scene_and_unknown_job_keep_error_contract(self):
        direct_response = self.client.post(
            "/api/raytrace/direct",
            json={
                "scene_token": "expired",
                "emitters": [],
            },
        )
        status_response = self.client.get(
            "/api/raytrace/status",
            params={"job_id": "missing"},
        )

        self.assertEqual(direct_response.status_code, 400)
        self.assertIn("error", direct_response.json())
        self.assertEqual(status_response.status_code, 404)
        self.assertEqual(
            status_response.json()["error"],
            "Ray tracing job was not found",
        )

    def test_stop_finishes_job_with_partial_result(self):
        class PartialResult:
            def __init__(self, total_rays):
                self.total_rays = total_rays

            def to_dict(self):
                return {"run_id": "run_partial", "total_rays": self.total_rays}

        def input_builder(scene_mesh, request_payload):
            return SimpleNamespace(
                emitters=[SimpleNamespace(ray_count=200, enabled=True)],
            )

        def slow_runner(trace_input, progress_callback=None, should_stop=None):
            processed = 0
            for processed in range(1, 201):
                if should_stop is not None and should_stop():
                    processed -= 1
                    break
                if progress_callback is not None:
                    progress_callback(processed, 200)
                time.sleep(0.002)
            return PartialResult(processed)

        runtime = ApiRuntime(
            Path(self.temp_dir.name) / "partial",
            scene_loader=_scene_loader,
            trace_input_builder=input_builder,
            trace_runner=slow_runner,
        )
        client = TestClient(create_app(runtime))
        try:
            scene = runtime.load_scene("partial.step")
            job = client.post(
                "/api/raytrace/start",
                json={
                    "scene_token": scene["metadata"]["scene_token"],
                    "emitters": [{"enabled": True, "ray_count": 200}],
                },
            ).json()
            time.sleep(0.02)
            stop_response = client.post(
                "/api/raytrace/stop",
                params={"job_id": job["job_id"]},
            )
            self.assertEqual(stop_response.status_code, 200)

            snapshot = None
            for _ in range(100):
                snapshot = client.get(
                    "/api/raytrace/status",
                    params={"job_id": job["job_id"]},
                ).json()
                if snapshot["status"] == "completed":
                    break
                time.sleep(0.005)

            self.assertEqual(snapshot["phase"], "stopped")
            self.assertTrue(snapshot["stopped_early"])
            self.assertGreater(snapshot["processed_rays"], 0)
            self.assertLess(snapshot["processed_rays"], 200)
            self.assertEqual(
                snapshot["result"]["total_rays"],
                snapshot["processed_rays"],
            )
        finally:
            client.close()

    def test_starting_a_new_job_stops_the_previous_active_job(self):
        class PartialResult:
            def __init__(self, total_rays):
                self.total_rays = total_rays

            def to_dict(self):
                return {"run_id": "run_replaced", "total_rays": self.total_rays}

        first_started = threading.Event()

        def input_builder(scene_mesh, request_payload):
            return SimpleNamespace(
                emitters=[SimpleNamespace(ray_count=200, enabled=True)],
            )

        def slow_runner(trace_input, progress_callback=None, should_stop=None):
            processed = 0
            first_started.set()
            for processed in range(1, 201):
                if should_stop is not None and should_stop():
                    processed -= 1
                    break
                if progress_callback is not None:
                    progress_callback(processed, 200)
                time.sleep(0.002)
            return PartialResult(processed)

        runtime = ApiRuntime(
            Path(self.temp_dir.name) / "replace-active",
            scene_loader=_scene_loader,
            trace_input_builder=input_builder,
            trace_runner=slow_runner,
        )
        scene = runtime.load_scene("replace-active.step")
        payload = {
            "scene_token": scene["metadata"]["scene_token"],
            "emitters": [{"enabled": True, "ray_count": 200}],
        }

        first = runtime.start_raytrace_job(payload)
        self.assertTrue(first_started.wait(timeout=1.0))
        second = runtime.start_raytrace_job(payload)

        first_snapshot = runtime.raytrace_job_snapshot(first["job_id"])
        self.assertIsNotNone(first_snapshot)
        self.assertTrue(first_snapshot["stop_requested"])
        self.assertEqual(first_snapshot["phase"], "stopping")
        self.assertFalse(second["stop_requested"])

        for _ in range(100):
            first_snapshot = runtime.raytrace_job_snapshot(first["job_id"])
            if first_snapshot and first_snapshot["status"] == "completed":
                break
            time.sleep(0.005)

        self.assertIsNotNone(first_snapshot)
        self.assertEqual(first_snapshot["phase"], "stopped")
        self.assertTrue(first_snapshot["stopped_early"])
        runtime.stop_raytrace_job(second["job_id"])


if __name__ == "__main__":
    unittest.main()
