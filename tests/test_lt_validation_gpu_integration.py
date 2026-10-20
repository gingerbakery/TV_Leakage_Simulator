from __future__ import annotations

import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT / "tests"), str(ROOT / "tools")]

from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.api import ApiRuntime, create_app
from perf5d_evidence import transport_accuracy_gates
from test_bitsam_package import project_fixture, scene_fixture, trace_request
from test_bsdf_transport import synthetic_asset
from validate_bsdf_gpu import physical_float_parity


def semantic_payload(result):
    payload = copy.deepcopy(result)
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    return payload


class LtGpuProductionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capability = gpu.preflight_gpu_cuda(refresh=True)
        required = ("available", "strict_float64", "kernel_executed", "kernel_verified")
        if not all(getattr(capability, field) is True for field in required):
            raise RuntimeError(f"Production CUDA required: {asdict(capability)}")
        if (capability.preflight_scope != "production_ray_bvh"
                or gpu.PROVIDER_CONTRACT != "strict_float64_bvh_v1"):
            raise RuntimeError("Production CUDA contract mismatch")

    def assert_gpu_execution(self, result, label):
        performance = result["metrics"]["_performance_summary"]
        self.assertEqual(performance["compute_execution_state"], "gpu_active", performance)
        self.assertIsNone(performance["compute_execution_reason"], performance)
        self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0, performance)
        self.assertEqual(performance["gpu_cuda_gpu_success_count"],
                         performance["gpu_cuda_gpu_attempt_count"], performance)
        self.assertGreater(performance["gpu_resident_wavefront_success_count"], 0, performance)
        self.assertEqual(performance["monte_carlo_contract"], "cpu_gpu_deterministic_batch_v1")
        for field in ("gpu_cuda_hybrid_cpu_success_count", "intersection_fallback_count",
                      "gpu_resident_wavefront_fallback_count", "wavefront_planner_fallback_count",
                      "wavefront_reducer_fallback_count"):
            self.assertEqual(performance[field], 0, performance)
        print(json.dumps({"case": self.id(), "stage": label, "runtime_sec": result["runtime_sec"],
                          "emitter_types": [emitter["emitter_type"] for emitter in result["emitters"]],
                          "performance": performance}, allow_nan=False), flush=True)

    def assert_results_match(self, reference, candidate):
        gates = transport_accuracy_gates(
            semantic_payload(reference), semantic_payload(reference), semantic_payload(candidate))
        for name in ("parity", "physical_repeatability", "receiver_parity", "receiver_repeatability"):
            self.assertTrue(gates[name]["passed"], (name, gates[name]))

    def assert_cpu_receiver_parity(self, reference, candidate):
        expected = semantic_payload(reference)
        actual = semantic_payload(candidate)
        gates = transport_accuracy_gates(expected, expected, actual)
        physical_pass = physical_float_parity(expected, actual)
        print(json.dumps({"case": self.id(), "comparison": "CPU/GPU",
                          "gates": gates, "physical_float_parity": physical_pass},
                         allow_nan=False), flush=True)
        self.assertTrue(gates["strict_all_fields"]["discrete_exact"], gates["strict_all_fields"])
        self.assertTrue(physical_pass, gates["strict_all_fields"])
        self.assertTrue(gates["receiver_parity"]["passed"], gates["receiver_parity"])

    def test_analytic_gpu_save_compare_restore_and_retrace(self):
        self.check_roundtrip(False)

    def test_bsdf_gpu_save_compare_restore_and_retrace(self):
        self.check_roundtrip(True)

    def check_roundtrip(self, use_bsdf):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.step"
            source.write_bytes(b"ISO-10303-21; synthetic GPU integration fixture")
            runtime = ApiRuntime(root / "live", scene_loader=scene_fixture)
            scene = runtime.load_scene(str(source))
            request = trace_request(scene["metadata"]["scene_token"])
            request["emitters"][0].update(ray_count=8193, direction_distribution="lambertian")
            request["receivers"][0].update(u_axis=[1, 0, 0], v_axis=[0, 1, 0])
            request["config"].update(ray_count=8193, contribution_mode="summary")
            if use_bsdf:
                asset = synthetic_asset()
                request["bsdf_assets"] = [asset]
                request["optical_profiles"][0].update(
                    reflectance=0, scatter_model="none", bsdf_asset_id=asset["id"])
            reference = runtime.run_raytrace_direct(request)
            self.assertGreater(reference["receiver_hit_count"], 0)
            request["config"]["compute_backend"] = "gpu_cuda"
            original_request = copy.deepcopy(request)
            first = None
            timings = []
            for iteration in range(3):
                started = time.perf_counter()
                result = runtime.run_raytrace_direct(request)
                timings.append(time.perf_counter() - started)
                self.assert_gpu_execution(result, f"repeat-{iteration}")
                self.assert_cpu_receiver_parity(reference, result)
                if first is None:
                    first = result
                self.assert_results_match(first, result)
            print(json.dumps({"case": self.id(), "first_wall_sec": timings[0],
                              "warm_wall_sec": timings[1:]}), flush=True)
            project = project_fixture()
            project["schema_version"] = "bitsam-project.v1-bsdf1" if use_bsdf else "bitsam-project.v1"
            project["workspace"].update(
                emitters=result["emitters"], receivers=result["receivers"],
                rayTraceConfig=request["config"], opticalProfiles=request["optical_profiles"],
                bsdfAssets=request.get("bsdf_assets", []), roiScopes=[], transformRules=[])
            if use_bsdf:
                project["workspace"]["materialAssignments"][0]["bsdfAssetId"] = asset["id"]
            packages = {}
            for backend, saved_result in (("cpu", reference), ("gpu_cuda", result)):
                saved_project = copy.deepcopy(project)
                saved_project["workspace"]["rayTraceConfig"]["compute_backend"] = backend
                saved_project["analysis_result"] = saved_result
                exported = runtime.export_project(
                    {"scene_token": request["scene_token"], "project": saved_project})
                package, _ = runtime.project_export_file(exported["download_url"].split("/")[-1])
                packages[backend] = (package, package.read_bytes())
            datasets = {}
            with TestClient(create_app(runtime=runtime)) as client, patch.object(
                runtime, "import_project", side_effect=AssertionError("LT must not restore a live project")
            ), patch.object(
                runtime, "run_raytrace_direct", side_effect=AssertionError("LT must not trace a live scene")
            ):
                for backend, (_, content) in packages.items():
                    response = client.post(
                        f"/api/lt-validation/import?filename={backend}.bitsam", content=content)
                    self.assertEqual(response.status_code, 200, response.text)
                    datasets[backend] = response.json()
                receiver = datasets["gpu_cuda"]["receivers"][0]
                self.assertEqual(receiver["flux_lm"], result["receiver_grids"][0]["flux_lumen"])
                response = client.post("/api/lt-validation/compare", json={
                    "baseline_id": "parity", "cases": [{
                        "id": "parity", "name": "GPU vs synthetic CPU reference",
                        "sim": datasets["gpu_cuda"], "lt": datasets["cpu"],
                        "sim_receiver_id": receiver["id"],
                        "lt_receiver_id": datasets["cpu"]["receivers"][0]["id"],
                    }],
                })
                self.assertEqual(response.status_code, 200, response.text)
                comparison = response.json()["cases"][0]
                self.assertEqual(comparison["status"], "ready", comparison["issues"])
                self.assertAlmostEqual(comparison["flux_deviation_pct"], 0, places=10)
                self.assertAlmostEqual(comparison["pearson"], 1, places=12)
                self.assertAlmostEqual(comparison["overlap_pct"], 100, places=10)
                np.testing.assert_allclose(comparison["plots"]["difference_pp"], 0, atol=1e-10)
            self.assertEqual(request, original_request)
            for package, content in packages.values():
                self.assertEqual(package.read_bytes(), content)
            loader = Mock(side_effect=AssertionError("Restore must reuse embedded geometry"))
            restored = ApiRuntime(root / "restored", scene_loader=loader)
            loaded = restored.import_project(packages["gpu_cuda"][0])
            self.assertEqual(loaded["project"]["analysis_result"], json.loads(json.dumps(result)))
            workspace = loaded["project"]["workspace"]
            loaded_scene = restored.load_scene(loaded["cad"]["path"])
            retrace = {
                "scene_token": loaded_scene["metadata"]["scene_token"],
                "emitters": workspace["emitters"], "receivers": workspace["receivers"],
                "config": workspace["rayTraceConfig"], "optical_profiles": workspace["opticalProfiles"],
                "bsdf_assets": workspace.get("bsdfAssets", []),
            }
            repeated = restored.run_raytrace_direct(retrace)
            self.assert_gpu_execution(repeated, "restored")
            self.assert_results_match(result, repeated)
            retrace["emitters"][0]["power_lumen"] *= 0.5
            changed = restored.run_raytrace_direct(retrace)
            self.assert_gpu_execution(changed, "edited-source")
            self.assertEqual(changed["receiver_hit_count"], repeated["receiver_hit_count"])
            np.testing.assert_allclose(
                changed["receiver_grids"][0]["flux_lumen"],
                np.asarray(repeated["receiver_grids"][0]["flux_lumen"]) * 0.5,
                atol=1e-12, rtol=1e-12)
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
