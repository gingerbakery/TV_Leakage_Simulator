from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import call, patch

import numpy as np
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.geometry import RayBatch, RayHitBatch, TriangleMesh
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.types import RayTraceConfig


def _scene_mesh() -> dict:
    return {
        "vertices": [
            [-100.0, -100.0, 10.0],
            [100.0, -100.0, 10.0],
            [0.0, 100.0, 10.0],
        ],
        "faces": [[0, 1, 2]],
        "face_component_ids": [7],
        "face_material_ids": ["default"],
    }


def _request_payload(
    *,
    compute_backend: str,
    intersection_backend: str = "auto",
    ray_count: int = 8,
) -> dict:
    return {
        "scene_token": "gpu-contract-scene",
        "emitters": [
            {
                "emitter_id": "source",
                "emitter_type": "datum_plane",
                "center": [0.0, 0.0, 0.0],
                "u_axis": [1.0, 0.0, 0.0],
                "v_axis": [0.0, 1.0, 0.0],
                "width_mm": 1.0,
                "height_mm": 1.0,
                "direction_distribution": "gaussian",
                "gaussian_sigma_deg": 1.0,
                "ray_count": ray_count,
                "seed": 41,
            }
        ],
        "receivers": [
            {
                "receiver_id": "receiver",
                "center": [0.0, 0.0, 20.0],
                "normal": [0.0, 0.0, -1.0],
                "width_mm": 200.0,
                "height_mm": 200.0,
                "resolution": [2, 2],
            }
        ],
        "config": {
            "ray_count": ray_count,
            "max_depth": 0,
            "contribution_mode": "summary",
            "intersection_backend": intersection_backend,
            "compute_backend": compute_backend,
        },
    }


def _face_emitter_payload(ray_count: int = 4) -> dict:
    return {
        "emitter_id": "face-source",
        "emitter_type": "face",
        "face_indices": [0],
        "direction_distribution": "gaussian",
        "gaussian_sigma_deg": 1.0,
        "ray_count": ray_count,
        "seed": 43,
    }


def _polygon_emitter_payload(ray_count: int = 8192) -> dict:
    return {
        "emitter_id": "polygon-source",
        "emitter_type": "reference_plane",
        "center": [0.0, 0.0, 0.0],
        "u_axis": [1.0, 0.0, 0.0],
        "v_axis": [0.0, 1.0, 0.0],
        "width_mm": 6.0,
        "height_mm": 4.0,
        "surface_construction": "polygon_auto",
        "polygon_vertices": [
            [-3.0, -2.0, 0.0],
            [3.0, -2.0, 0.0],
            [1.0, 2.0, 0.0],
            [-3.0, 2.0, 0.0],
        ],
        "direction_distribution": "lambertian",
        "ray_count": ray_count,
        "seed": 47,
    }


def _owned_readonly(values, dtype) -> np.ndarray:
    result = np.array(values, dtype=dtype, copy=True, order="C")
    result.setflags(write=False)
    return result


def _fake_gpu_execution(rays: RayBatch):
    distances = _owned_readonly(
        np.full(len(rays), np.inf, dtype=np.float64),
        np.float64,
    )
    face_indices = _owned_readonly(
        np.full(len(rays), -1, dtype=np.int64),
        np.int64,
    )
    hits = RayHitBatch(t=distances, face_indices=face_indices)
    return hits, gpu_cuda.GpuCudaExecution(
        distances=distances,
        face_indices=face_indices,
        scene_build_sec=0.0,
        scene_upload_sec=0.0,
        workspace_prepare_sec=0.0,
        input_upload_sec=0.0,
        jit_compile_sec=0.0,
        kernel_sec=0.0,
        output_download_sec=0.0,
        numba_version="fake-numba",
        device_name="fake-gpu",
        compute_capability="9.9",
        device_id=3,
        toolkit_layout="fake-toolkit",
        reused_device_scene=True,
        reused_workspace=True,
    )


def _reference_gpu_execution(mesh, rays: RayBatch, backend=None):
    hits = mesh.intersect_rays(rays, backend=backend)
    distances = _owned_readonly(hits.t, np.float64)
    face_indices = _owned_readonly(hits.face_indices, np.int64)
    return RayHitBatch(
        t=distances,
        face_indices=face_indices,
    ), gpu_cuda.GpuCudaExecution(
        distances=distances,
        face_indices=face_indices,
        scene_build_sec=0.0,
        scene_upload_sec=0.0,
        workspace_prepare_sec=0.0,
        input_upload_sec=0.0,
        jit_compile_sec=0.0,
        kernel_sec=0.0,
        output_download_sec=0.0,
        numba_version="fake-numba",
        device_name="fake-gpu",
        compute_capability="9.9",
        device_id=3,
        toolkit_layout="fake-toolkit",
        reused_device_scene=True,
        reused_workspace=True,
    )


def _fake_preflight_execution(
    distances=(5.0, np.inf),
    face_indices=(0, -1),
) -> gpu_cuda.GpuCudaExecution:
    return gpu_cuda.GpuCudaExecution(
        distances=_owned_readonly(distances, np.float64),
        face_indices=_owned_readonly(face_indices, np.int64),
        scene_build_sec=0.0,
        scene_upload_sec=0.01,
        workspace_prepare_sec=0.01,
        input_upload_sec=0.01,
        jit_compile_sec=0.01,
        kernel_sec=0.0,
        output_download_sec=0.01,
        numba_version="fake-numba",
        device_name="fake-gpu",
        compute_capability="9.9",
        device_id=4,
        toolkit_layout="fake-toolkit",
        reused_device_scene=False,
        reused_workspace=False,
    )


def _contract_trace_runner(
    trace_input,
    progress_callback=None,
    should_stop=None,
):
    return run_direct_ray_trace(
        trace_input,
        progress_callback=progress_callback,
        should_stop=should_stop,
        wavefront_planner="python_cpu",
        wavefront_pipeline="object_reference",
        wavefront_reducer="python_cpu",
    )


class GpuBackendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _runtime(self, *, trace_runner=run_direct_ray_trace) -> ApiRuntime:
        runtime = ApiRuntime(
            Path(self.temp_dir.name),
            trace_runner=trace_runner,
        )
        runtime._scene_mesh_cache["gpu-contract-scene"] = _scene_mesh()
        return runtime

    def test_explicit_status_endpoint_has_stable_schema_and_refresh(self) -> None:
        available_capability = gpu_cuda.GpuCudaPreflight(
            available=True,
            reason_code=None,
            numba_version="0.test",
            device_name="Contract GPU",
            compute_capability="9.1",
            device_id=2,
            strict_float64=True,
            toolkit_layout="test-layout",
            kernel_executed=True,
            kernel_verified=True,
        )
        unavailable_capability = gpu_cuda.GpuCudaPreflight(
            available=False,
            reason_code="cuda_toolkit_not_found",
            numba_version="0.test",
            device_name=None,
            compute_capability=None,
            device_id=None,
            strict_float64=False,
            toolkit_layout=None,
            kernel_executed=False,
            kernel_verified=False,
        )
        client = TestClient(create_app(self._runtime()))
        try:
            with patch.object(
                gpu_cuda,
                "preflight_gpu_cuda",
                side_effect=[available_capability, unavailable_capability],
            ) as probe:
                cached_response = client.get("/api/gpu-cuda/status")
                refreshed_response = client.get(
                    "/api/gpu-cuda/status",
                    params={"refresh": "true"},
                )
        finally:
            client.close()

        self.assertEqual(cached_response.status_code, 200)
        self.assertEqual(refreshed_response.status_code, 200)
        self.assertEqual(
            cached_response.json(),
            {
                "available": True,
                "reason_code": None,
                "device_name": "Contract GPU",
                "compute_capability": "9.1",
                "device_id": 2,
                "numba_version": "0.test",
                "toolkit_layout": "test-layout",
                "strict_float64": True,
                "kernel_executed": True,
                "kernel_verified": True,
                "preflight_scope": "production_ray_bvh",
                "provider_contract": "strict_float64_bvh_v1",
            },
        )
        self.assertEqual(
            refreshed_response.json(),
            {
                "available": False,
                "reason_code": "cuda_toolkit_not_found",
                "device_name": None,
                "compute_capability": None,
                "device_id": None,
                "numba_version": "0.test",
                "toolkit_layout": None,
                "strict_float64": False,
                "kernel_executed": False,
                "kernel_verified": False,
                "preflight_scope": "production_ray_bvh",
                "provider_contract": "strict_float64_bvh_v1",
            },
        )
        probe.assert_has_calls([call(refresh=False), call(refresh=True)])

    def test_cpu_api_trace_never_probes_or_executes_cuda(self) -> None:
        client = TestClient(create_app(self._runtime()))
        try:
            with (
                patch.object(
                    gpu_cuda,
                    "probe_gpu_cuda",
                    side_effect=AssertionError("CPU request probed CUDA"),
                ) as probe,
                patch.object(
                    TriangleMesh,
                    "intersect_rays_gpu_cuda",
                    side_effect=AssertionError("CPU request executed CUDA"),
                ) as execute,
            ):
                response = client.post(
                    "/api/raytrace/direct",
                    json=_request_payload(compute_backend="cpu"),
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, response.text)
        performance = response.json()["metrics"]["_performance_summary"]
        self.assertEqual(performance["compute_backend"], "cpu")
        self.assertEqual(performance["compute_execution_state"], "cpu")
        self.assertIsNone(performance["compute_execution_reason"])
        self.assertFalse(performance["gpu_cuda_requested"])
        self.assertFalse(performance["gpu_cuda_used"])
        probe.assert_not_called()
        execute.assert_not_called()

    def test_preflight_executes_production_bvh_and_refresh_retries(self) -> None:
        capability = gpu_cuda.GpuCudaCapability(
            available=True,
            reason_code=None,
            numba_version="fake-numba",
            device_name="fake-gpu",
            compute_capability="9.9",
            device_id=4,
            strict_float64=True,
            toolkit_layout="fake-toolkit",
        )

        def fake_intersect(
            scene,
            origins,
            directions,
            minimum_t,
            maximum_t,
            ignored_faces,
        ):
            self.assertIsInstance(scene, gpu_cuda.GpuCudaScene)
            self.assertEqual(scene.triangle_v0.shape, (1, 3))
            np.testing.assert_array_equal(scene.node_count, [1])
            np.testing.assert_array_equal(scene.ordered_faces, [0])
            np.testing.assert_array_equal(
                origins,
                [[0.25, 0.25, 0.0], [1.5, 1.5, 0.0]],
            )
            np.testing.assert_array_equal(
                directions,
                [[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]],
            )
            np.testing.assert_array_equal(minimum_t, [1e-8, 1e-8])
            np.testing.assert_array_equal(maximum_t, [10.0, 10.0])
            np.testing.assert_array_equal(ignored_faces, [-1, -1])
            return _fake_preflight_execution()

        with (
            patch.object(
                gpu_cuda,
                "probe_gpu_cuda",
                return_value=capability,
            ) as probe,
            patch.object(
                gpu_cuda,
                "intersect_gpu_cuda",
                side_effect=fake_intersect,
            ) as intersect,
            patch.object(gpu_cuda, "_PREFLIGHT", None),
        ):
            first = gpu_cuda.preflight_gpu_cuda()
            cached = gpu_cuda.preflight_gpu_cuda()
            refreshed = gpu_cuda.preflight_gpu_cuda(refresh=True)

        self.assertTrue(first.available)
        self.assertTrue(first.kernel_executed)
        self.assertTrue(first.kernel_verified)
        self.assertTrue(first.strict_float64)
        self.assertEqual(first.preflight_scope, "production_ray_bvh")
        self.assertIs(first, cached)
        self.assertTrue(refreshed.available)
        self.assertEqual(intersect.call_count, 2)
        probe.assert_has_calls([call(refresh=False), call(refresh=True)])

    def test_preflight_preserves_provider_reason_and_rejects_wrong_result(
        self,
    ) -> None:
        capability = gpu_cuda.GpuCudaCapability(
            available=True,
            reason_code=None,
            numba_version="fake-numba",
            device_name="fake-gpu",
            compute_capability="9.9",
            device_id=4,
            strict_float64=True,
            toolkit_layout="fake-toolkit",
        )
        failures = (
            ("initialize", "gpu_cuda_scene_upload_failed"),
            ("input_prepare", "gpu_cuda_input_upload_failed"),
            ("execute", "gpu_cuda_kernel_failed"),
            ("result_validation", "gpu_cuda_output_download_failed"),
        )
        for phase, reason in failures:
            with (
                self.subTest(phase=phase),
                patch.object(
                    gpu_cuda,
                    "probe_gpu_cuda",
                    return_value=capability,
                ),
                patch.object(
                    gpu_cuda,
                    "intersect_gpu_cuda",
                    side_effect=gpu_cuda.GpuCudaProviderError(
                        phase,
                        reason,
                    ),
                ),
                patch.object(gpu_cuda, "_PREFLIGHT", None),
            ):
                failure = gpu_cuda.preflight_gpu_cuda()

            self.assertFalse(failure.available)
            self.assertFalse(failure.kernel_executed)
            self.assertFalse(failure.kernel_verified)
            self.assertEqual(failure.reason_code, reason)

        with (
            patch.object(
                gpu_cuda,
                "probe_gpu_cuda",
                return_value=capability,
            ),
            patch.object(
                gpu_cuda,
                "intersect_gpu_cuda",
                return_value=_fake_preflight_execution(
                    face_indices=(1, -1),
                ),
            ),
            patch.object(gpu_cuda, "_PREFLIGHT", None),
        ):
            wrong_result = gpu_cuda.preflight_gpu_cuda()

        self.assertFalse(wrong_result.available)
        self.assertTrue(wrong_result.kernel_executed)
        self.assertFalse(wrong_result.kernel_verified)
        self.assertEqual(
            wrong_result.reason_code,
            "gpu_cuda_preflight_result_mismatch",
        )

    def test_gpu_api_preserves_config_selects_provider_and_forces_small_bvh(
        self,
    ) -> None:
        observed_backends: list[str | None] = []

        def fake_gpu(mesh, rays, backend=None):
            observed_backends.append(backend)
            return _fake_gpu_execution(rays)

        client = TestClient(
            create_app(self._runtime(trace_runner=_contract_trace_runner))
        )
        try:
            with patch.object(
                TriangleMesh,
                "intersect_rays_gpu_cuda",
                new=fake_gpu,
            ):
                response = client.post(
                    "/api/raytrace/direct",
                    json=_request_payload(
                        compute_backend="gpu_cuda",
                        intersection_backend="auto",
                        # Exactly the hybrid cutoff ensures the fake CUDA
                        # provider is selected instead of the small-wave CPU
                        # optimization.
                        ray_count=8192,
                    ),
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, response.text)
        performance = response.json()["metrics"]["_performance_summary"]
        self.assertTrue(observed_backends)
        self.assertEqual(set(observed_backends), {"bvh"})
        self.assertEqual(performance["compute_backend"], "gpu_cuda")
        self.assertEqual(performance["configured_intersection_backend"], "auto")
        self.assertEqual(performance["configured_acceleration_structure"], "auto")
        self.assertEqual(performance["intersection_backend"], "bvh")
        self.assertEqual(performance["acceleration_structure"], "bvh")
        self.assertEqual(
            performance["requested_intersection_provider"],
            "auto",
        )
        self.assertEqual(
            performance["effective_intersection_provider_request"],
            "gpu_cuda",
        )
        self.assertEqual(performance["intersection_provider"], "gpu_cuda")
        self.assertTrue(performance["gpu_cuda_requested"])
        self.assertTrue(performance["gpu_cuda_used"])
        self.assertEqual(performance["gpu_cuda_device_name"], "fake-gpu")
        self.assertEqual(
            performance["compute_execution_state"],
            "gpu_active",
        )
        self.assertIsNone(performance["compute_execution_reason"])

    def test_face_batch_request_executes_gpu_cuda_bvh(self) -> None:
        payload = _request_payload(compute_backend="gpu_cuda")
        payload["emitters"] = [_face_emitter_payload()]
        observed_ignored_faces: list[np.ndarray] = []

        def fake_gpu(mesh, rays, backend=None):
            observed_ignored_faces.append(rays.ignore_faces.copy())
            return _fake_gpu_execution(rays)

        client = TestClient(
            create_app(self._runtime(trace_runner=_contract_trace_runner))
        )
        try:
            with patch.object(
                TriangleMesh,
                "intersect_rays_gpu_cuda",
                new=fake_gpu,
            ):
                response = client.post(
                    "/api/raytrace/direct",
                    json=payload,
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, response.text)
        performance = response.json()["metrics"]["_performance_summary"]
        self.assertEqual(
            performance["compute_execution_state"],
            "gpu_active",
        )
        self.assertIsNone(performance["compute_execution_reason"])
        self.assertTrue(performance["gpu_cuda_used"])
        self.assertEqual(performance["face_batch_primary_ray_count"], 4)
        self.assertEqual(performance["scalar_primary_ray_count"], 0)
        self.assertEqual(performance["gpu_cuda_hybrid_bypass_count"], 1)
        self.assertEqual(len(observed_ignored_faces), 1)
        np.testing.assert_array_equal(observed_ignored_faces[0], [0, 0, 0, 0])

    def test_polygon_batch_request_executes_gpu_cuda_bvh(self) -> None:
        payload = _request_payload(
            compute_backend="gpu_cuda",
            ray_count=8192,
        )
        payload["emitters"] = [_polygon_emitter_payload()]
        observed_origins: list[np.ndarray] = []

        def fake_gpu(mesh, rays, backend=None):
            observed_origins.append(rays.origins.copy())
            return _fake_gpu_execution(rays)

        client = TestClient(
            create_app(self._runtime(trace_runner=_contract_trace_runner))
        )
        try:
            with patch.object(
                TriangleMesh,
                "intersect_rays_gpu_cuda",
                new=fake_gpu,
            ):
                response = client.post(
                    "/api/raytrace/direct",
                    json=payload,
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, response.text)
        performance = response.json()["metrics"]["_performance_summary"]
        self.assertEqual(performance["compute_execution_state"], "gpu_active")
        self.assertIsNone(performance["compute_execution_reason"])
        self.assertTrue(performance["gpu_cuda_used"])
        self.assertEqual(performance["fast_primary_ray_count"], 8192)
        self.assertEqual(performance["scalar_primary_ray_count"], 0)
        self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0)
        self.assertTrue(observed_origins)
        origins = np.concatenate(observed_origins)
        self.assertEqual(len(origins), 8192)
        np.testing.assert_allclose(origins[:, 2], 1e-4, atol=1e-12)

    def test_default_cpu_and_gpu_face_runs_share_exact_monte_carlo_contract(
        self,
    ) -> None:
        cpu_payload = _request_payload(compute_backend="cpu", ray_count=257)
        cpu_payload["emitters"] = [_face_emitter_payload(ray_count=257)]
        gpu_payload = _request_payload(compute_backend="gpu_cuda", ray_count=257)
        gpu_payload["emitters"] = [_face_emitter_payload(ray_count=257)]

        cpu_result = run_direct_ray_trace(
            build_direct_trace_input(_scene_mesh(), cpu_payload)
        )
        with patch.object(
            TriangleMesh,
            "intersect_rays_gpu_cuda",
            new=_reference_gpu_execution,
        ):
            gpu_result = run_direct_ray_trace(
                build_direct_trace_input(_scene_mesh(), gpu_payload)
            )

        cpu_semantic = cpu_result.to_dict()
        gpu_semantic = gpu_result.to_dict()
        for payload in (cpu_semantic, gpu_semantic):
            payload.pop("run_id", None)
            payload.pop("runtime_sec", None)
            payload["metrics"].pop("_performance_summary", None)
            payload["config"]["compute_backend"] = "normalized"

        self.assertEqual(cpu_semantic, gpu_semantic)
        cpu_performance = cpu_result.metrics["_performance_summary"]
        gpu_performance = gpu_result.metrics["_performance_summary"]
        self.assertEqual(
            cpu_performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )
        self.assertEqual(
            gpu_performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )
        self.assertEqual(cpu_performance["face_batch_primary_ray_count"], 257)
        self.assertEqual(gpu_performance["face_batch_primary_ray_count"], 257)
        self.assertEqual(cpu_performance["scalar_primary_ray_count"], 0)
        self.assertEqual(gpu_performance["scalar_primary_ray_count"], 0)

    def test_default_cpu_and_gpu_polygon_runs_share_exact_monte_carlo_contract(
        self,
    ) -> None:
        cpu_payload = _request_payload(compute_backend="cpu", ray_count=8192)
        cpu_payload["emitters"] = [_polygon_emitter_payload()]
        gpu_payload = _request_payload(
            compute_backend="gpu_cuda",
            ray_count=8192,
        )
        gpu_payload["emitters"] = [_polygon_emitter_payload()]

        cpu_result = run_direct_ray_trace(
            build_direct_trace_input(_scene_mesh(), cpu_payload)
        )
        with patch.object(
            TriangleMesh,
            "intersect_rays_gpu_cuda",
            new=_reference_gpu_execution,
        ):
            gpu_result = run_direct_ray_trace(
                build_direct_trace_input(_scene_mesh(), gpu_payload)
            )

        cpu_semantic = cpu_result.to_dict()
        gpu_semantic = gpu_result.to_dict()
        for payload in (cpu_semantic, gpu_semantic):
            payload.pop("run_id", None)
            payload.pop("runtime_sec", None)
            payload["metrics"].pop("_performance_summary", None)
            payload["config"]["compute_backend"] = "normalized"

        self.assertEqual(cpu_semantic, gpu_semantic)
        cpu_performance = cpu_result.metrics["_performance_summary"]
        gpu_performance = gpu_result.metrics["_performance_summary"]
        self.assertEqual(
            cpu_performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )
        self.assertEqual(
            gpu_performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )
        self.assertEqual(cpu_performance["scalar_primary_ray_count"], 0)
        self.assertEqual(gpu_performance["scalar_primary_ray_count"], 0)
        self.assertGreater(gpu_performance["gpu_cuda_gpu_success_count"], 0)

    def test_mixed_gpu_emitter_formats_remain_gpu_active(self) -> None:
        payload = _request_payload(
            compute_backend="gpu_cuda",
            ray_count=8192,
        )
        payload["emitters"].append(_face_emitter_payload())
        payload["emitters"].append(_polygon_emitter_payload())

        def fake_gpu(mesh, rays, backend=None):
            return _fake_gpu_execution(rays)

        client = TestClient(
            create_app(self._runtime(trace_runner=_contract_trace_runner))
        )
        try:
            with patch.object(
                TriangleMesh,
                "intersect_rays_gpu_cuda",
                new=fake_gpu,
            ):
                response = client.post(
                    "/api/raytrace/direct",
                    json=payload,
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 200, response.text)
        performance = response.json()["metrics"]["_performance_summary"]
        self.assertEqual(
            performance["compute_execution_state"],
            "gpu_active",
        )
        self.assertIsNone(performance["compute_execution_reason"])
        self.assertTrue(performance["gpu_cuda_used"])
        self.assertEqual(performance["face_batch_primary_ray_count"], 4)
        self.assertEqual(performance["scalar_primary_ray_count"], 0)
        self.assertEqual(performance["fast_primary_ray_count"], 16_388)
        self.assertGreaterEqual(performance["gpu_cuda_gpu_success_count"], 3)

    def test_face_batch_preserves_source_faces_in_soa_multibounce(self) -> None:
        payload = _request_payload(compute_backend="gpu_cuda", ray_count=16)
        payload["emitters"] = [_face_emitter_payload(ray_count=16)]
        payload["config"].update(
            {
                "max_depth": 2,
                "store_ray_paths": True,
                "max_stored_paths": 4,
            }
        )
        trace_input = build_direct_trace_input(_scene_mesh(), payload)
        observed_ignored_faces: list[np.ndarray] = []

        def fake_gpu(mesh, rays, backend=None):
            observed_ignored_faces.append(rays.ignore_faces.copy())
            return _fake_gpu_execution(rays)

        with patch.object(
            TriangleMesh,
            "intersect_rays_gpu_cuda",
            new=fake_gpu,
        ):
            result = run_direct_ray_trace(
                trace_input,
                wavefront_pipeline="soa_event_tape",
                wavefront_planner="python_cpu",
                wavefront_reducer="python_cpu",
            )

        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["compute_execution_state"], "gpu_active")
        self.assertEqual(
            performance["wavefront_event_tape_contract"],
            "ordered_primary_event_tape_v3",
        )
        self.assertTrue(observed_ignored_faces)
        np.testing.assert_array_equal(
            observed_ignored_faces[0],
            np.zeros(16, dtype=np.int64),
        )
        self.assertTrue(result.stored_paths)
        self.assertTrue(all(path[0].face_index == 0 for path in result.stored_paths))

    def test_invalid_gpu_acceleration_value_returns_actionable_api_error(
        self,
    ) -> None:
        client = TestClient(create_app(self._runtime()))
        try:
            response = client.post(
                "/api/raytrace/direct",
                json=_request_payload(
                    compute_backend="cpu",
                    intersection_backend="gpu_cuda",
                ),
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 400)
        message = response.json()["error"]
        self.assertIn("acceleration structure", message)
        self.assertIn('compute_backend="gpu_cuda"', message)

    def test_gpu_with_brute_force_is_rejected_before_api_execution(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            'compute_backend="gpu_cuda" requires intersection_backend.*auto or bvh',
        ):
            RayTraceConfig(
                compute_backend="gpu_cuda",
                intersection_backend="brute_force",
            )

        client = TestClient(create_app(self._runtime()))
        try:
            with patch.object(
                TriangleMesh,
                "intersect_rays_gpu_cuda",
                side_effect=AssertionError(
                    "invalid GPU/brute-force request reached execution"
                ),
            ) as execute:
                response = client.post(
                    "/api/raytrace/direct",
                    json=_request_payload(
                        compute_backend="gpu_cuda",
                        intersection_backend="brute_force",
                    ),
                )
        finally:
            client.close()

        self.assertEqual(response.status_code, 400)
        message = response.json()["error"]
        self.assertIn('compute_backend="gpu_cuda"', message)
        self.assertIn("auto or bvh", message)
        self.assertIn("brute_force is CPU-only", message)
        execute.assert_not_called()

    def test_clear_acceleration_structure_alias_preserves_legacy_contract(
        self,
    ) -> None:
        config = RayTraceConfig.from_dict(
            {
                "acceleration_structure": "bvh",
                "compute_backend": "gpu_cuda",
            }
        )
        self.assertEqual(config.acceleration_structure, "bvh")
        self.assertEqual(config.intersection_backend, "bvh")
        self.assertEqual(config.to_dict()["intersection_backend"], "bvh")

        mesh = TriangleMesh()
        mesh.set_acceleration_structure("bvh")
        self.assertEqual(mesh.intersection_backend, "bvh")
        mesh.set_intersection_backend("auto")
        self.assertEqual(mesh.intersection_backend, "auto")

        with self.assertRaisesRegex(ValueError, "compute_backend"):
            RayTraceConfig.from_dict(
                {"acceleration_structure": "gpu_cuda"}
            )

    def test_bridge_keeps_compute_and_acceleration_config_separate(self) -> None:
        trace_input = build_direct_trace_input(
            _scene_mesh(),
            _request_payload(
                compute_backend="gpu_cuda",
                intersection_backend="auto",
            ),
        )

        self.assertEqual(trace_input.config.compute_backend, "gpu_cuda")
        self.assertEqual(trace_input.config.intersection_backend, "auto")
        # Geometry preparation owns a reusable BVH; it does not overwrite the
        # request-local compute or configured acceleration values above.
        self.assertEqual(trace_input.mesh.intersection_backend, "bvh")


if __name__ == "__main__":
    unittest.main()
