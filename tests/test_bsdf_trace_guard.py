from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.bsdf_guard import validate_bsdf_profiles
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import OpticalProfile, RayTraceConfig


class BsdfTracingGuardTests(unittest.TestCase):
    def test_analytic_profiles_and_retained_asset_metadata_remain_constructible(self):
        validate_bsdf_profiles([
            {}, {"bsdf_asset_id": None}, {"bsdf_asset_id": ""},
            OpticalProfile(profile_id="analytic", reflectance=0.3),
        ])
        profile = OpticalProfile(
            profile_id="measured", reflectance=0.3, bsdf_asset_id="legacy-asset",
        )
        self.assertEqual(OpticalProfile.from_dict(profile.to_dict()).bsdf_asset_id, "legacy-asset")
        with self.assertRaisesRegex(ValueError, "missing"):
            validate_bsdf_profiles([profile])

    def test_unresolved_or_malformed_asset_ids_never_fall_back_to_analytic_optics(self):
        for asset_id in ["legacy-unknown", "imported-valid-id", " ", 0, False]:
            with self.subTest(asset_id=asset_id):
                with self.assertRaisesRegex(ValueError, "analytic fallback"):
                    validate_bsdf_profiles([
                        {"profile_id": "custom-part", "bsdf_asset_id": asset_id},
                    ])

    def test_bridge_rejects_asset_before_geometry_build(self):
        with patch("leakage_simulator.raytrace_bridge.build_prepared_trace_geometry") as prepare:
            with self.assertRaisesRegex(ValueError, "legacy-unknown"):
                build_direct_trace_input({}, {"optical_profiles": [
                    {"profile_id": "measured", "bsdf_asset_id": "legacy-unknown"},
                ]})
            prepare.assert_not_called()

    def test_every_engine_dispatch_rejects_asset_before_native_planning(self):
        trace_input = DirectRayTraceInput(
            mesh=TriangleMesh(), emitters=[], receivers=[], config=RayTraceConfig(),
            optical_profiles=[OpticalProfile(
                profile_id="measured", reflectance=0.3, bsdf_asset_id="imported-valid-id",
            )],
        )
        with patch("leakage_simulator.raytracer.plan_counter_native_cpu") as counter, \
                patch("leakage_simulator.raytracer.plan_deterministic_native_cpu") as native:
            for dispatch, pipeline in [("scalar", "object_reference"), ("batch", "soa_event_tape"), ("auto", "auto")]:
                with self.subTest(dispatch=dispatch, pipeline=pipeline):
                    with self.assertRaisesRegex(ValueError, "missing"):
                        run_direct_ray_trace(trace_input, intersection_dispatch=dispatch, wavefront_pipeline=pipeline)
            counter.assert_not_called()
            native.assert_not_called()

    def test_api_rejects_before_preparing_scene_or_superseding_running_job(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = ApiRuntime(Path(directory))
            runtime._raytrace_jobs["existing"] = {"status": "running", "stop_requested": False}
            client = TestClient(create_app(runtime))
            with patch.object(runtime, "_scene_mesh_for_request") as prepare:
                for endpoint in ["/api/raytrace/start", "/api/raytrace/direct"]:
                    with self.subTest(endpoint=endpoint):
                        response = client.post(endpoint, json={"optical_profiles": [
                            {"profile_id": "measured", "bsdf_asset_id": "legacy-unknown"},
                        ]})
                        self.assertEqual(response.status_code, 400)
                        self.assertIn("missing", response.text)
                prepare.assert_not_called()
            self.assertEqual(list(runtime._raytrace_jobs), ["existing"])
            self.assertFalse(runtime._raytrace_jobs["existing"]["stop_requested"])


if __name__ == "__main__":
    unittest.main()
