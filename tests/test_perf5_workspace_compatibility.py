from __future__ import annotations

import copy
import json
import os
import unittest
from unittest.mock import Mock

from leakage_simulator.api import ApiRuntime
from tests.test_bitsam_package import trace_request
from tests import test_bitsam_workspace_package as workspace_fixtures


class Perf5WorkspaceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workspace_fixtures.BitsamWorkspacePackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_backend_settings_results_and_paths_stay_with_each_case(self):
        expected = {}
        for item, backend in zip(self.fixture.request["cases"], ("cpu", "gpu_cuda")):
            request = trace_request("not-used-during-export")
            request["config"].update(compute_backend=backend, max_depth=100, seed=123 if backend == "cpu" else 456)
            project = item["project"]
            project["workspace"].update(rayTraceConfig=request["config"], emitters=request["emitters"], receivers=request["receivers"])
            project["analysis_result"] = {
                "case_id": item["case_id"], "config": copy.deepcopy(request["config"]),
                "metrics": {"_performance_summary": {"compute_execution_state": "cpu" if backend == "cpu" else "gpu_active"}},
                "stored_paths": [[{"event_type": "surface", "face_index": 0, "energy": 0.125}]],
                "receiver_grids": [{"receiver_id": "observer", "flux_lumen": [[0.25, 0.75]]}],
            }
            expected[item["case_id"]] = json.loads(json.dumps(project))
        package = self.fixture.exported_path()
        loader = Mock(side_effect=AssertionError("CAD should restore from cache"))
        restored_runtime = ApiRuntime(self.fixture.root / "restored-settings", scene_loader=loader)
        restored = restored_runtime.import_project(package)
        self.assertEqual(restored["active_case_id"], "case-2")
        for item in restored["cases"]:
            self.assertEqual(item["project"], expected[item["case_id"]])
        loader.assert_not_called()

    def test_restored_multi_case_geometry_retraces_and_isolates_optical_edits(self):
        self.assert_restored_trace_backends(("cpu", "cpu"))

    @unittest.skipUnless(os.environ.get("PERF5_GPU_COMPATIBILITY") == "1", "Real GPU workspace check not requested")
    def test_gpu_workspace_retrace_preserves_results_and_case_settings(self):
        from leakage_simulator.gpu_cuda_intersection import PROVIDER_CONTRACT, preflight_gpu_cuda

        preflight = preflight_gpu_cuda(refresh=True)
        for field in ("available", "strict_float64", "kernel_executed", "kernel_verified"):
            self.assertIs(getattr(preflight, field), True)
        self.assertEqual(preflight.preflight_scope, "production_ray_bvh")
        self.assertEqual(PROVIDER_CONTRACT, "strict_float64_bvh_v1")
        self.assert_restored_trace_backends(("cpu", "gpu_cuda"))

    def assert_restored_trace_backends(self, backends):
        originals = {}
        for item, backend in zip(self.fixture.request["cases"], backends):
            paths = [item["cad_path"], *[entry["path"] for entry in item["accessory_paths"]]]
            scene = self.fixture.runtime.load_composite_scene(paths) if len(paths) > 1 else self.fixture.runtime.load_scene(paths[0])
            request = trace_request(scene["metadata"]["scene_token"])
            request["config"].update(compute_backend=backend, ray_count=8193)
            request["emitters"][0]["ray_count"] = 8193
            request["emitters"][0]["power_lumen"] = 1.0 if item["case_id"] == "case-1" else 0.5
            result = self.fixture.runtime.run_raytrace_direct(request)
            item["project"]["analysis_result"] = result
            item["project"]["workspace"].update(rayTraceConfig=copy.deepcopy(request["config"]), emitters=copy.deepcopy(request["emitters"]))
            originals[item["case_id"]] = (request, result)
        package = self.fixture.exported_path()
        loader = Mock(side_effect=AssertionError("CAD should not be parsed again"))
        restored_runtime = ApiRuntime(self.fixture.root / "restored-traces", scene_loader=loader)
        restored = restored_runtime.import_project(package)
        for item in restored["cases"]:
            paths = [item["cad"]["path"], *[entry["cad"]["path"] for entry in item["accessory_cads"] if entry["visible"]]]
            scene = restored_runtime.load_composite_scene(paths) if len(paths) > 1 else restored_runtime.load_scene(paths[0])
            request, original = copy.deepcopy(originals[item["case_id"]])
            request["scene_token"] = scene["metadata"]["scene_token"]
            repeated = restored_runtime.run_raytrace_direct(request)
            self.assertEqual(item["project"]["workspace"]["rayTraceConfig"], request["config"])
            self.assertEqual(item["project"]["analysis_result"], json.loads(json.dumps(original)))
            for field in ("receiver_grids", "stored_paths"):
                self.assertEqual(repeated[field], original[field])
            original_flux = original["metrics"]["observer"]["total_flux_lumen"]
            self.assertGreater(original_flux, 0)
            request["emitters"][0]["power_lumen"] *= 0.5
            edited = restored_runtime.run_raytrace_direct(request)
            self.assertAlmostEqual(edited["metrics"]["observer"]["total_flux_lumen"], original_flux * 0.5)
            self.assertEqual(item["project"]["analysis_result"], json.loads(json.dumps(original)))
            prepared = restored_runtime._build_trace_input_for_request(restored_runtime._scene_mesh_for_request(request), request)
            self.assertTrue(prepared.geometry_cache_hit)
            if request["config"]["compute_backend"] == "gpu_cuda":
                for phase, result in zip(("original", "restored", "edited"), (original, repeated, edited)):
                    performance = result["metrics"]["_performance_summary"]
                    self.assertIn(performance["compute_execution_state"], ("gpu_active", "gpu_mixed"))
                    self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0)
                    self.assertEqual(performance["intersection_fallback_count"], 0)
                    print("[PERF5 GPU WORKSPACE] " + json.dumps({
                        "phase": phase, "case_id": item["case_id"],
                        **{key: value for key, value in performance.items() if key.startswith(("compute_", "gpu_cuda_", "gpu_resident_wavefront_"))},
                    }), flush=True)
        loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
