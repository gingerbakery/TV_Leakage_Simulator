from __future__ import annotations

import tempfile
import sys
import threading
import time
import unittest
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


def _trace_runner(trace_input, progress_callback=None):
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


if __name__ == "__main__":
    unittest.main()
