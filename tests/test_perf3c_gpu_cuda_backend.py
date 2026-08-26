from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import math
import subprocess
import sys
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import two_bounce_input
from test_native_cpu_backend_perf3b2 import (
    build_parallel_triangles,
    reflected_case,
    semantic_payload,
)
from test_perf3b2a_multibounce_wavefront import stochastic_two_bounce_input

from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.geometry import RayBatch, RayHitBatch, TriangleMesh
from leakage_simulator.native_cpu_intersection import (
    NativeCpuExecution,
    NativeCpuProviderError,
)
from leakage_simulator.native_cpu_counter_wavefront import (
    CounterWavefrontPlanInput,
    counter_uniform,
    plan_counter_native_cpu,
    plan_counter_reference,
    probe_native_cpu_counter_wavefront,
)
from leakage_simulator.native_cpu_wavefront import (
    SCATTER_GAUSSIAN,
    SCATTER_LAMBERTIAN,
    SCATTER_MIXED,
    SCATTER_SPECULAR,
    TERMINATION_RUSSIAN_ROULETTE,
)
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import RayTraceConfig


FLOAT_ABS_TOLERANCE = 1e-12
FLOAT_REL_TOLERANCE = 1e-12


def _owned_readonly(values, dtype) -> np.ndarray:
    result = np.array(values, dtype=dtype, copy=True, order="C")
    result.setflags(write=False)
    return result


def _fake_execution(hits: RayHitBatch) -> gpu_cuda.GpuCudaExecution:
    return gpu_cuda.GpuCudaExecution(
        distances=_owned_readonly(hits.t, np.float64),
        face_indices=_owned_readonly(hits.face_indices, np.int64),
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
        device_id=7,
        toolkit_layout="fake-layout",
        reused_device_scene=True,
        reused_workspace=True,
    )


def _reference_gpu_adapter(mesh: TriangleMesh, calls: list[int] | None = None):
    reference = mesh.intersect_rays

    def execute(rays: RayBatch, backend=None):
        if calls is not None:
            calls.append(len(rays))
        hits = reference(rays, backend=backend)
        return hits, _fake_execution(hits)

    return execute


def _reference_native_adapter(mesh: TriangleMesh, calls: list[int] | None = None):
    reference = mesh.intersect_rays

    def execute(rays: RayBatch, backend=None):
        if calls is not None:
            calls.append(len(rays))
        hits = reference(rays, backend=backend)
        return hits, NativeCpuExecution(
            distances=_owned_readonly(hits.t, np.float64),
            face_indices=_owned_readonly(hits.face_indices, np.int64),
            scene_build_sec=0.0,
            jit_compile_sec=0.0,
            execute_sec=0.0,
            numba_version="fake-numba",
        )

    return execute


def _batch_run(trace_input, provider: str):
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=8,
        intersection_provider=provider,
        wavefront_planner="python_cpu",
        wavefront_pipeline="object_reference",
        wavefront_reducer="python_cpu",
        wavefront_rng="per_primary_seeded_v1",
    )


def _counter_wavefront_run(ray_count: int, chunk_size: int, planner: str):
    trace_input = stochastic_two_bounce_input(ray_count)
    trace_input.config.contribution_mode = "summary"
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="python_cpu",
        wavefront_planner=planner,
        wavefront_pipeline="object_reference",
        wavefront_reducer="python_cpu",
        wavefront_rng="counter_rng_v2",
    )


def _counter_batch() -> CounterWavefrontPlanInput:
    return CounterWavefrontPlanInput(
        incoming_directions=np.asarray(
            [
                (0.0, 0.0, -1.0),
                (0.2, 0.0, -1.0),
                (-0.1, 0.25, -1.0),
                (0.3, -0.2, -1.0),
            ],
            dtype=np.float64,
        ),
        surface_normals=np.tile((0.0, 0.0, 1.0), (4, 1)),
        incoming_power_lumen=[0.9, 0.004, 0.7, 0.5],
        profile_reflectance=[0.85, 0.7, 0.6, 0.95],
        profile_roughness=[0.0, 0.3, 0.5, 0.1],
        scatter_models=[
            SCATTER_SPECULAR,
            SCATTER_LAMBERTIAN,
            SCATTER_GAUSSIAN,
            SCATTER_MIXED,
        ],
        profile_specular_ratio=[1.0, 0.0, 0.0, 0.55],
        profile_gaussian_sigma_deg=[0.001, 12.0, 7.0, 9.0],
        rng_keys=np.asarray([1, 2, 2**63 + 7, 2**64 - 1], dtype=np.uint64),
        depth=1,
        max_depth=8,
        min_energy=0.01,
        termination_mode=TERMINATION_RUSSIAN_ROULETTE,
    )


def _numpy_fields(instance):
    return [
        (field.name, getattr(instance, field.name))
        for field in fields(instance)
        if isinstance(getattr(instance, field.name), np.ndarray)
    ]


class Perf3CGpuCudaBackendTests(unittest.TestCase):
    def assertCounterResultsEqual(self, actual, expected) -> None:
        np.testing.assert_array_equal(actual.supported_mask, expected.supported_mask)
        np.testing.assert_array_equal(actual.status_flags, expected.status_flags)
        np.testing.assert_array_equal(actual.lobe_codes, expected.lobe_codes)
        np.testing.assert_array_equal(actual.rng_draw_counts, expected.rng_draw_counts)
        for name in (
            "reflected_power_lumen",
            "emitted_power_lumen",
            "emitted_directions",
        ):
            np.testing.assert_allclose(
                getattr(actual, name),
                getattr(expected, name),
                atol=FLOAT_ABS_TOLERANCE,
                rtol=FLOAT_REL_TOLERANCE,
            )

    def test_gpu_module_and_default_cpu_import_are_cuda_lazy(self) -> None:
        code = f"""
import json, sys
sys.path.insert(0, {str(ROOT / 'src')!r})
before = {{name: name in sys.modules for name in ('numba', 'numba.cuda')}}
import leakage_simulator.gpu_cuda_intersection as gpu
import leakage_simulator.raytracer
after = {{name: name in sys.modules for name in ('numba', 'numba.cuda')}}
print(json.dumps({{'before': before, 'after': after, 'probed': gpu._CAPABILITY is not None}}))
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code],
            check=True,
            capture_output=True,
            text=True,
        )
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["before"], {"numba": False, "numba.cuda": False})
        self.assertEqual(evidence["after"], {"numba": False, "numba.cuda": False})
        self.assertFalse(evidence["probed"])

    def test_compute_backend_schema_round_trip_and_validation(self) -> None:
        cpu = RayTraceConfig.from_dict(RayTraceConfig().to_dict())
        gpu = RayTraceConfig.from_dict(
            RayTraceConfig(compute_backend="gpu_cuda").to_dict()
        )
        self.assertEqual(cpu.compute_backend, "cpu")
        self.assertEqual(gpu.compute_backend, "gpu_cuda")
        with self.assertRaisesRegex(ValueError, "compute_backend"):
            RayTraceConfig(compute_backend="unknown")

    def test_default_cpu_never_probes_or_executes_gpu(self) -> None:
        trace_input = reflected_case(23)
        reference = _batch_run(reflected_case(23), "python_cpu")
        with (
            patch.object(
                trace_input.mesh,
                "intersect_rays_gpu_cuda",
                side_effect=AssertionError("CPU default must not execute CUDA"),
            ) as execute_mock,
            patch.object(
                gpu_cuda,
                "probe_gpu_cuda",
                side_effect=AssertionError("CPU default must not probe CUDA"),
            ) as probe_mock,
        ):
            result = _batch_run(trace_input, "auto")

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        execute_mock.assert_not_called()
        probe_mock.assert_not_called()
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["requested_intersection_provider"], "auto")
        self.assertEqual(performance["intersection_provider"], "python_cpu")
        self.assertIs(performance["gpu_cuda_available"], None)
        self.assertFalse(performance["gpu_cuda_used"])
        self.assertEqual(performance["native_attempt_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 0)

    def test_project_gpu_auto_stack_uses_65536_batch_and_strict_metrics(self) -> None:
        trace_input = two_bounce_input(max_depth=2, ray_count=8192)
        trace_input.config.compute_backend = "gpu_cuda"
        trace_input.config.contribution_mode = "summary"
        reference_input = two_bounce_input(max_depth=2, ray_count=8192)
        reference_input.config.compute_backend = "gpu_cuda"
        reference_input.config.contribution_mode = "summary"
        reference = run_direct_ray_trace(
            reference_input,
            intersection_dispatch="batch",
            intersection_batch_size=65536,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
            wavefront_pipeline="soa_event_tape",
            wavefront_reducer="python_cpu",
            wavefront_rng="counter_rng_v2",
        )
        gpu_calls: list[int] = []
        cpu_calls: list[int] = []
        with (
            patch.object(
                trace_input.mesh,
                "intersect_rays_gpu_cuda",
                side_effect=_reference_gpu_adapter(trace_input.mesh, gpu_calls),
            ),
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=_reference_native_adapter(trace_input.mesh, cpu_calls),
            ),
        ):
            result = run_direct_ray_trace(
                trace_input,
                wavefront_planner="python_cpu",
                wavefront_reducer="python_cpu",
            )

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        self.assertGreater(len(gpu_calls), 0)
        self.assertEqual(cpu_calls, [])
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["compute_backend"], "gpu_cuda")
        self.assertEqual(performance["intersection_batch_size"], 65536)
        self.assertEqual(performance["requested_intersection_provider"], "auto")
        self.assertEqual(
            performance["effective_intersection_provider_request"],
            "gpu_cuda",
        )
        self.assertEqual(performance["intersection_provider"], "gpu_cuda")
        self.assertTrue(performance["gpu_cuda_available"])
        self.assertTrue(performance["gpu_cuda_used"])
        self.assertTrue(performance["gpu_cuda_strict_float64"])
        self.assertEqual(performance["gpu_cuda_contract"], "strict_float64_bvh_v1")
        self.assertEqual(performance["gpu_cuda_device_name"], "fake-gpu")
        self.assertEqual(performance["gpu_cuda_compute_capability"], "9.9")
        self.assertEqual(performance["gpu_cuda_device_id"], 7)
        self.assertEqual(
            performance["gpu_cuda_execution_policy"],
            "hybrid_numba_cpu_small_wave_v1",
        )
        self.assertEqual(performance["gpu_cuda_hybrid_cpu_below_rays"], 8192)
        self.assertEqual(performance["gpu_cuda_hybrid_cpu_success_count"], 0)
        self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0)
        self.assertEqual(performance["wavefront_reflection_rng"], "counter_rng_v2")
        self.assertEqual(performance["wavefront_pipeline"], "soa_event_tape")
        self.assertEqual(performance["intersection_fallback_count"], 0)
        for name in (
            "gpu_cuda_scene_upload_sec",
            "gpu_cuda_workspace_prepare_sec",
            "gpu_cuda_input_upload_sec",
            "gpu_cuda_kernel_sec",
            "gpu_cuda_output_download_sec",
        ):
            value = performance[name]
            self.assertIs(type(value), float, name)
            self.assertTrue(math.isfinite(value), name)
            self.assertGreaterEqual(value, 0.0, name)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_project_gpu_routes_small_waves_to_numba_without_gpu_probe(self) -> None:
        trace_input = two_bounce_input(max_depth=2, ray_count=37)
        trace_input.config.compute_backend = "gpu_cuda"
        trace_input.config.contribution_mode = "summary"
        cpu_calls: list[int] = []
        with (
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=_reference_native_adapter(trace_input.mesh, cpu_calls),
            ),
            patch.object(
                trace_input.mesh,
                "intersect_rays_gpu_cuda",
                side_effect=AssertionError("small waves must not launch CUDA"),
            ) as gpu_mock,
            patch.object(
                gpu_cuda,
                "probe_gpu_cuda",
                side_effect=AssertionError("small waves must not probe CUDA"),
            ) as probe_mock,
        ):
            result = run_direct_ray_trace(
                trace_input,
                wavefront_planner="python_cpu",
                wavefront_reducer="python_cpu",
            )

        self.assertGreater(len(cpu_calls), 0)
        gpu_mock.assert_not_called()
        probe_mock.assert_not_called()
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_provider"], "numba_cpu")
        self.assertIs(performance["gpu_cuda_available"], None)
        self.assertFalse(performance["gpu_cuda_used"])
        self.assertEqual(performance["gpu_cuda_gpu_attempt_count"], 0)
        self.assertEqual(performance["gpu_cuda_hybrid_cpu_failure_count"], 0)
        self.assertEqual(
            performance["compute_execution_state"],
            "gpu_requested_cpu_only",
        )
        self.assertEqual(
            performance["compute_execution_reason"],
            "gpu_cuda_below_hybrid_threshold",
        )
        self.assertEqual(
            performance["gpu_cuda_hybrid_cpu_attempt_count"],
            performance["gpu_cuda_hybrid_cpu_success_count"],
        )
        self.assertEqual(
            performance["gpu_cuda_hybrid_cpu_attempt_ray_count"],
            performance["intersection_ray_count"],
        )
        json.dumps(result.to_dict(), allow_nan=False)

    def test_small_wave_hybrid_failure_opens_run_local_hybrid_circuit(self) -> None:
        trace_input = two_bounce_input(max_depth=2, ray_count=37)
        trace_input.config.compute_backend = "gpu_cuda"
        trace_input.config.contribution_mode = "summary"
        reference_input = two_bounce_input(max_depth=2, ray_count=37)
        reference_input.config.compute_backend = "gpu_cuda"
        reference_input.config.contribution_mode = "summary"
        reference = run_direct_ray_trace(
            reference_input,
            intersection_dispatch="batch",
            intersection_batch_size=65536,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
            wavefront_pipeline="soa_event_tape",
            wavefront_reducer="python_cpu",
            wavefront_rng="counter_rng_v2",
        )
        gpu_calls: list[int] = []
        with (
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=NativeCpuProviderError(
                    "execute",
                    "injected_hybrid_failure",
                ),
            ) as native_mock,
            patch.object(
                trace_input.mesh,
                "intersect_rays_gpu_cuda",
                side_effect=_reference_gpu_adapter(trace_input.mesh, gpu_calls),
            ),
        ):
            result = run_direct_ray_trace(
                trace_input,
                wavefront_planner="python_cpu",
                wavefront_reducer="python_cpu",
            )

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        self.assertEqual(native_mock.call_count, 1)
        self.assertGreater(len(gpu_calls), 1)
        performance = result.metrics["_performance_summary"]
        self.assertTrue(performance["gpu_cuda_hybrid_cpu_disabled"])
        self.assertEqual(performance["gpu_cuda_hybrid_cpu_attempt_count"], 1)
        self.assertEqual(performance["gpu_cuda_hybrid_cpu_failure_count"], 1)
        self.assertEqual(
            performance["gpu_cuda_hybrid_cpu_failure_reason"],
            "injected_hybrid_failure",
        )
        self.assertEqual(performance["gpu_cuda_gpu_attempt_count"], len(gpu_calls))
        self.assertEqual(performance["gpu_cuda_gpu_success_count"], len(gpu_calls))
        self.assertEqual(performance["intersection_fallback_count"], 0)
        self.assertEqual(performance["intersection_provider"], "gpu_cuda")
        json.dumps(result.to_dict(), allow_nan=False)

    def test_unavailable_gpu_is_normal_cpu_selection_not_hard_fallback(self) -> None:
        reference = _batch_run(reflected_case(41), "python_cpu")
        trace_input = reflected_case(41)
        with patch.object(
            trace_input.mesh,
            "intersect_rays_gpu_cuda",
            side_effect=gpu_cuda.GpuCudaUnavailable("injected_no_gpu"),
        ) as gpu_mock:
            result = _batch_run(trace_input, "gpu_cuda")

        self.assertEqual(semantic_payload(result), semantic_payload(reference))
        gpu_mock.assert_called_once()
        performance = result.metrics["_performance_summary"]
        self.assertIs(performance["gpu_cuda_available"], False)
        self.assertFalse(performance["gpu_cuda_used"])
        self.assertEqual(performance["intersection_provider"], "python_cpu")
        self.assertEqual(performance["native_attempt_count"], 1)
        self.assertEqual(performance["native_success_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 0)
        self.assertEqual(
            performance["intersection_provider_unavailable_reason"],
            "injected_no_gpu",
        )
        self.assertEqual(
            performance["compute_execution_state"],
            "gpu_requested_cpu_only",
        )
        self.assertEqual(
            performance["compute_execution_reason"],
            "injected_no_gpu",
        )

    def test_gpu_hard_failures_replay_whole_batch_once_and_open_circuit(self) -> None:
        reference = _batch_run(reflected_case(41), "python_cpu")
        for phase in ("input_prepare", "initialize", "execute", "result_validation"):
            with self.subTest(phase=phase):
                trace_input = reflected_case(41)
                original_reference = trace_input.mesh.intersect_rays
                reason = f"injected_{phase}_failure"
                with (
                    patch.object(
                        trace_input.mesh,
                        "intersect_rays_gpu_cuda",
                        side_effect=gpu_cuda.GpuCudaProviderError(phase, reason),
                    ) as gpu_mock,
                    patch.object(
                        trace_input.mesh,
                        "intersect_rays",
                        wraps=original_reference,
                    ) as reference_mock,
                ):
                    result = _batch_run(trace_input, "gpu_cuda")

                self.assertEqual(semantic_payload(result), semantic_payload(reference))
                gpu_mock.assert_called_once()
                failed_rays = gpu_mock.call_args.args[0]
                first_replay = reference_mock.call_args_list[0].args[0]
                self.assertIs(failed_rays, first_replay)
                performance = result.metrics["_performance_summary"]
                self.assertTrue(performance["native_provider_disabled"])
                self.assertEqual(performance["native_attempt_count"], 1)
                self.assertEqual(performance["native_success_count"], 0)
                self.assertEqual(performance["intersection_fallback_count"], 1)
                self.assertEqual(
                    performance["intersection_fallback_ray_count"],
                    len(failed_rays),
                )
                self.assertEqual(performance["intersection_fallback_phase"], phase)
                self.assertEqual(performance["intersection_fallback_reason"], reason)
                self.assertEqual(
                    performance["compute_execution_state"],
                    "gpu_requested_cpu_only",
                )
                self.assertEqual(
                    performance["compute_execution_reason"],
                    reason,
                )
                self.assertEqual(
                    sum(len(call.args[0]) for call in reference_mock.call_args_list),
                    performance["intersection_ray_count"],
                )

    def test_gpu_breaker_is_isolated_between_concurrent_runs(self) -> None:
        failed_input = reflected_case(31)
        successful_input = reflected_case(31)
        failed_mesh = failed_input.mesh
        calls = {"failed": 0, "successful": 0}
        original_gpu = TriangleMesh.intersect_rays_gpu_cuda

        def dispatch(mesh, rays, backend=None):
            if mesh is failed_mesh:
                calls["failed"] += 1
                raise gpu_cuda.GpuCudaProviderError("execute", "thread_failure")
            calls["successful"] += 1
            hits = TriangleMesh.intersect_rays(mesh, rays, backend=backend)
            return hits, _fake_execution(hits)

        with patch.object(TriangleMesh, "intersect_rays_gpu_cuda", new=dispatch):
            with ThreadPoolExecutor(max_workers=2) as executor:
                failed_future = executor.submit(_batch_run, failed_input, "gpu_cuda")
                successful_future = executor.submit(
                    _batch_run,
                    successful_input,
                    "gpu_cuda",
                )
                failed = failed_future.result()
                successful = successful_future.result()

        self.assertIsNotNone(original_gpu)
        self.assertEqual(calls["failed"], 1)
        self.assertGreater(calls["successful"], 1)
        failed_metrics = failed.metrics["_performance_summary"]
        successful_metrics = successful.metrics["_performance_summary"]
        self.assertEqual(failed_metrics["intersection_fallback_count"], 1)
        self.assertFalse(failed_metrics["gpu_cuda_used"])
        self.assertEqual(successful_metrics["intersection_fallback_count"], 0)
        self.assertTrue(successful_metrics["gpu_cuda_used"])
        self.assertEqual(
            successful_metrics["native_attempt_count"],
            successful_metrics["native_success_count"],
        )

    def test_gpu_consumer_validation_rejects_invalid_or_mutable_results(self) -> None:
        mesh = build_parallel_triangles()
        capability = gpu_cuda.GpuCudaCapability(
            True,
            None,
            "test",
            "fake-gpu",
            "9.9",
            0,
            True,
            "test-layout",
        )
        rays = RayBatch(
            [(0.0, 0.0, 0.0)],
            [(0.0, 0.0, 1.0)],
            max_t=[20.0],
            ignore_faces=[-1],
        )

        def execution(distance, face, *, readonly=True):
            distances = np.asarray([distance], dtype=np.float64)
            faces = np.asarray([face], dtype=np.int64)
            if readonly:
                distances.setflags(write=False)
                faces.setflags(write=False)
            return gpu_cuda.GpuCudaExecution(
                distances=distances,
                face_indices=faces,
                scene_build_sec=0.0,
                scene_upload_sec=0.0,
                workspace_prepare_sec=0.0,
                input_upload_sec=0.0,
                jit_compile_sec=0.0,
                kernel_sec=0.0,
                output_download_sec=0.0,
                numba_version="test",
                device_name="fake-gpu",
                compute_capability="9.9",
                device_id=0,
                toolkit_layout="fake-layout",
                reused_device_scene=False,
                reused_workspace=False,
            )

        cases = (
            ("face_range", execution(5.0, 99), "face_out_of_range"),
            ("distance_bound", execution(25.0, 0), "distance_out_of_bounds"),
            ("finite_miss", execution(5.0, -1), "invalid_miss"),
            ("mutable", execution(5.0, 0, readonly=False), "ownership_invalid"),
            (
                "not_strict_float64",
                replace(execution(5.0, 0), strict_float64=False),
                "execution_contract_invalid",
            ),
            (
                "wrong_contract",
                replace(execution(5.0, 0), provider_contract="wrong"),
                "execution_contract_invalid",
            ),
            (
                "negative_timing",
                replace(execution(5.0, 0), kernel_sec=-1.0),
                "execution_contract_invalid",
            ),
            (
                "invalid_device",
                replace(execution(5.0, 0), device_id=-1),
                "execution_contract_invalid",
            ),
        )
        for name, injected, reason in cases:
            with self.subTest(name=name), patch.object(
                gpu_cuda,
                "probe_gpu_cuda",
                return_value=capability,
            ), patch.object(
                gpu_cuda,
                "intersect_gpu_cuda",
                return_value=injected,
            ):
                with self.assertRaisesRegex(
                    gpu_cuda.GpuCudaProviderError,
                    reason,
                ) as caught:
                    mesh.intersect_rays_gpu_cuda(rays, backend="bvh")
                self.assertEqual(caught.exception.phase, "result_validation")

    def test_empty_gpu_batch_is_owned_readonly_and_does_not_probe(self) -> None:
        mesh = build_parallel_triangles()
        rays = RayBatch(
            np.empty((0, 3), dtype=np.float64),
            np.empty((0, 3), dtype=np.float64),
        )
        with patch.object(
            gpu_cuda,
            "probe_gpu_cuda",
            side_effect=AssertionError("empty GPU batches must not probe CUDA"),
        ) as probe_mock:
            hits, execution = mesh.intersect_rays_gpu_cuda(rays, backend="bvh")

        probe_mock.assert_not_called()
        self.assertEqual(len(hits), 0)
        for values in (hits.t, hits.face_indices):
            self.assertTrue(values.flags.owndata)
            self.assertTrue(values.flags.c_contiguous)
            self.assertFalse(values.flags.writeable)
        for values in (execution.distances, execution.face_indices):
            self.assertTrue(values.flags.owndata)
            self.assertTrue(values.flags.c_contiguous)
            self.assertFalse(values.flags.writeable)
        self.assertFalse(
            np.shares_memory(execution.distances, execution.face_indices)
        )
        self.assertEqual(execution.device_name, "not_probed")
        self.assertEqual(execution.device_id, -1)

    def test_gpu_host_scene_is_readonly_cached_and_invalidated(self) -> None:
        mesh = build_parallel_triangles()
        first = mesh.prepare_gpu_cuda_scene()
        self.assertIs(first, mesh.prepare_gpu_cuda_scene())
        arrays = _numpy_fields(first)
        self.assertGreater(len(arrays), 0)
        for name, values in arrays:
            self.assertTrue(values.flags.c_contiguous, name)
            self.assertFalse(values.flags.writeable, name)

        vertices = [
            mesh.add_vertex((-1.0, -1.0, 2.0)),
            mesh.add_vertex((1.0, -1.0, 2.0)),
            mesh.add_vertex((0.0, 1.0, 2.0)),
        ]
        mesh.add_face(*vertices, "new-nearest")
        second = mesh.prepare_gpu_cuda_scene()
        self.assertIsNot(first, second)
        self.assertEqual(len(second.triangle_v0), 3)

    def test_counter_rng_known_vectors_and_row_reordering_are_stable(self) -> None:
        vectors = [
            (0, 0, 0),
            (1, 0, 1),
            (2**63 + 7, 3, 17),
            (2**64 - 1, 9, 95),
        ]
        first = [counter_uniform(*vector) for vector in vectors]
        second = [counter_uniform(*vector) for vector in vectors]
        self.assertEqual(first, second)
        self.assertTrue(all(0.0 <= value < 1.0 for value in first))
        self.assertEqual(len(set(first)), len(first))

        batch = _counter_batch()
        expected = plan_counter_reference(batch)
        permutation = np.asarray([3, 1, 0, 2])
        permuted = CounterWavefrontPlanInput(
            incoming_directions=batch.incoming_directions[permutation],
            surface_normals=batch.surface_normals[permutation],
            incoming_power_lumen=batch.incoming_power_lumen[permutation],
            profile_reflectance=batch.profile_reflectance[permutation],
            profile_roughness=batch.profile_roughness[permutation],
            scatter_models=batch.scatter_models[permutation],
            profile_specular_ratio=batch.profile_specular_ratio[permutation],
            profile_gaussian_sigma_deg=batch.profile_gaussian_sigma_deg[permutation],
            rng_keys=batch.rng_keys[permutation],
            depth=batch.depth,
            max_depth=batch.max_depth,
            min_energy=batch.min_energy,
            termination_mode=batch.termination_mode,
        )
        actual = plan_counter_reference(permuted)
        inverse = np.argsort(permutation)
        for name, values in _numpy_fields(actual):
            restored = values[inverse]
            expected_values = getattr(expected, name)
            if np.issubdtype(values.dtype, np.floating):
                np.testing.assert_allclose(
                    restored,
                    expected_values,
                    atol=FLOAT_ABS_TOLERANCE,
                    rtol=FLOAT_REL_TOLERANCE,
                )
            else:
                np.testing.assert_array_equal(restored, expected_values)

    def test_counter_rng_inputs_and_outputs_are_owned_readonly_and_disjoint(self) -> None:
        batch = _counter_batch()
        result = plan_counter_reference(batch)
        for scope, arrays in (
            ("input", _numpy_fields(batch)),
            ("output", _numpy_fields(result)),
        ):
            for name, values in arrays:
                self.assertTrue(values.flags.c_contiguous, f"{scope}:{name}")
                self.assertTrue(values.flags.owndata, f"{scope}:{name}")
                self.assertFalse(values.flags.writeable, f"{scope}:{name}")
            for index, (left_name, left) in enumerate(arrays):
                for right_name, right in arrays[index + 1 :]:
                    self.assertFalse(
                        np.shares_memory(left, right),
                        f"{scope}:{left_name} aliases {right_name}",
                    )

    def test_counter_rng_native_matches_reference_with_documented_tolerance(self) -> None:
        capability = probe_native_cpu_counter_wavefront()
        if not capability.available:
            self.skipTest(capability.reason_code or "Numba unavailable")
        batch = _counter_batch()
        expected = plan_counter_reference(batch)
        execution = plan_counter_native_cpu(batch)
        self.assertCounterResultsEqual(execution.result, expected)
        self.assertEqual(execution.numba_version, capability.numba_version)
        for value in (
            execution.jit_compile_sec,
            execution.execute_sec,
            execution.result_validation_sec,
        ):
            self.assertIs(type(value), float)
            self.assertTrue(math.isfinite(value))
            self.assertGreaterEqual(value, 0.0)

    def test_counter_rng_is_exact_across_chunks_and_python_numba_planners(self) -> None:
        baseline = _counter_wavefront_run(257, 17, "python_cpu")
        variants = [
            _counter_wavefront_run(257, 64, "python_cpu"),
            _counter_wavefront_run(257, 17, "numba_cpu"),
            _counter_wavefront_run(257, 64, "numba_cpu"),
        ]
        for index, result in enumerate(variants):
            with self.subTest(index=index):
                self.assertEqual(
                    semantic_payload(result),
                    semantic_payload(baseline),
                )
                performance = result.metrics["_performance_summary"]
                self.assertEqual(
                    performance["wavefront_reflection_rng"],
                    "counter_rng_v2",
                )
                self.assertEqual(
                    performance["wavefront_planner_contract"],
                    "counter_rng_v2",
                )

    def test_counter_rng_has_statistical_parity_with_v1_stream(self) -> None:
        aggregates = {}
        for contract in ("per_primary_seeded_v1", "counter_rng_v2"):
            gaussian_count = 0
            lambertian_count = 0
            emitted_flux = 0.0
            for seed_offset in range(8):
                trace_input = stochastic_two_bounce_input(512)
                trace_input.config.contribution_mode = "summary"
                trace_input.emitters[0].seed = 1000 + seed_offset
                result = run_direct_ray_trace(
                    trace_input,
                    intersection_dispatch="batch",
                    intersection_batch_size=64,
                    intersection_provider="python_cpu",
                    wavefront_planner="python_cpu",
                    wavefront_pipeline="object_reference",
                    wavefront_reducer="python_cpu",
                    wavefront_rng=contract,
                )
                lobes = result.to_dict()["contribution_summary"]["lobes"]
                gaussian_count += lobes["gaussian"]["emitted_count"]
                lambertian_count += lobes["lambertian"]["emitted_count"]
                emitted_flux += lobes["gaussian"]["emitted_flux_lumen"]
                emitted_flux += lobes["lambertian"]["emitted_flux_lumen"]
            total = gaussian_count + lambertian_count
            self.assertGreater(total, 1000)
            aggregates[contract] = {
                "gaussian_fraction": gaussian_count / total,
                "emitted_flux_lumen": emitted_flux,
            }

        legacy = aggregates["per_primary_seeded_v1"]
        counter = aggregates["counter_rng_v2"]
        self.assertLessEqual(
            abs(counter["gaussian_fraction"] - legacy["gaussian_fraction"]),
            0.05,
        )
        self.assertLessEqual(
            abs(counter["emitted_flux_lumen"] - legacy["emitted_flux_lumen"])
            / legacy["emitted_flux_lumen"],
            0.10,
        )

    def test_actual_gpu_intersection_matches_cpu_when_capability_is_available(self) -> None:
        capability = gpu_cuda.probe_gpu_cuda()
        if not capability.available:
            self.skipTest(capability.reason_code or "CUDA unavailable")
        mesh = build_parallel_triangles()
        rays = RayBatch(
            origins=np.zeros((6, 3), dtype=np.float64),
            directions=np.tile((0.0, 0.0, 1.0), (6, 1)),
            min_t=[1e-8, 1e-8, 1e-8, 5.0, 1e-8, 5.0],
            max_t=[math.inf, 4.999, 5.0, math.inf, 10.0, 5.0],
            ignore_faces=[-1, -1, -1, -1, 0, -1],
        )
        expected = mesh.intersect_rays(rays, backend="bvh")
        actual, execution = mesh.intersect_rays_gpu_cuda(rays, backend="bvh")
        np.testing.assert_array_equal(actual.face_indices, expected.face_indices)
        np.testing.assert_allclose(
            actual.t,
            expected.t,
            atol=FLOAT_ABS_TOLERANCE,
            rtol=FLOAT_REL_TOLERANCE,
        )
        self.assertTrue(execution.strict_float64)
        self.assertEqual(execution.provider_contract, "strict_float64_bvh_v1")

        excluded_mesh = TriangleMesh()
        for z_value, excluded in ((5.0, True), (10.0, False)):
            vertices = [
                excluded_mesh.add_vertex((-1.0, -1.0, z_value)),
                excluded_mesh.add_vertex((1.0, -1.0, z_value)),
                excluded_mesh.add_vertex((0.0, 1.0, z_value)),
            ]
            excluded_mesh.add_face(
                *vertices,
                "excluded-boundary",
                {"trace_excluded": excluded},
            )
        excluded_rays = RayBatch(
            [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)],
            [(0.0, 0.0, 1.0), (0.0, 0.0, 1.0)],
            ignore_faces=[-1, 1],
        )
        excluded_expected = excluded_mesh.intersect_rays(
            excluded_rays,
            backend="bvh",
        )
        excluded_actual, _ = excluded_mesh.intersect_rays_gpu_cuda(
            excluded_rays,
            backend="bvh",
        )
        np.testing.assert_array_equal(
            excluded_actual.face_indices,
            excluded_expected.face_indices,
        )
        np.testing.assert_allclose(
            excluded_actual.t,
            excluded_expected.t,
            atol=FLOAT_ABS_TOLERANCE,
            rtol=FLOAT_REL_TOLERANCE,
        )

    def test_actual_gpu_e2e_preserves_exact_summaries_and_tolerant_paths(self) -> None:
        capability = gpu_cuda.probe_gpu_cuda()
        if not capability.available:
            self.skipTest(capability.reason_code or "CUDA unavailable")

        def run(provider: str):
            trace_input = stochastic_two_bounce_input(257)
            trace_input.config.contribution_mode = "summary"
            trace_input.config.intersection_backend = "bvh"
            return run_direct_ray_trace(
                trace_input,
                intersection_dispatch="batch",
                intersection_batch_size=64,
                intersection_provider=provider,
                wavefront_planner="python_cpu",
                wavefront_pipeline="object_reference",
                wavefront_reducer="python_cpu",
                wavefront_rng="counter_rng_v2",
            )

        expected = run("python_cpu")
        actual = run("gpu_cuda")
        expected_payload = semantic_payload(expected)
        actual_payload = semantic_payload(actual)
        expected_paths = expected_payload.pop("stored_paths")
        actual_paths = actual_payload.pop("stored_paths")
        self.assertEqual(actual_payload, expected_payload)
        self.assertEqual(len(actual_paths), len(expected_paths))
        for path_index, (actual_path, expected_path) in enumerate(
            zip(actual_paths, expected_paths)
        ):
            self.assertEqual(len(actual_path), len(expected_path))
            for event_index, (actual_event, expected_event) in enumerate(
                zip(actual_path, expected_path)
            ):
                with self.subTest(path=path_index, event=event_index):
                    for key in actual_event:
                        if key in {"point", "normal"}:
                            np.testing.assert_allclose(
                                actual_event[key],
                                expected_event[key],
                                atol=FLOAT_ABS_TOLERANCE,
                                rtol=FLOAT_REL_TOLERANCE,
                            )
                        elif key == "distance_mm":
                            self.assertTrue(
                                math.isclose(
                                    actual_event[key],
                                    expected_event[key],
                                    abs_tol=FLOAT_ABS_TOLERANCE,
                                    rel_tol=FLOAT_REL_TOLERANCE,
                                )
                            )
                        else:
                            self.assertEqual(actual_event[key], expected_event[key])
        performance = actual.metrics["_performance_summary"]
        self.assertTrue(performance["gpu_cuda_used"])
        self.assertEqual(performance["intersection_fallback_count"], 0)
        json.dumps(actual.to_dict(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
