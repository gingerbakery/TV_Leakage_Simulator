from __future__ import annotations

import json
import math
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import ten_bounce_corridor_input, two_bounce_input

from leakage_simulator.native_cpu_wavefront import (
    CONTRACT_VERSION,
    LOBE_NONE,
    LOBE_SPECULAR,
    SCATTER_NONE,
    SCATTER_SPECULAR,
    STATUS_ATTEMPTED,
    STATUS_BELOW_ENERGY,
    STATUS_DEPTH_LIMITED,
    STATUS_DISABLED,
    STATUS_EMITTED,
    TERMINATION_THRESHOLD,
    NativeCpuWavefrontExecution,
    NativeCpuWavefrontProviderError,
    NativeCpuWavefrontUnavailable,
    WavefrontPlanInput,
    WavefrontPlanResult,
    plan_deterministic_native_cpu,
    plan_deterministic_reference,
    probe_native_cpu_wavefront,
    scatter_codes_from_names,
)
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import OpticalProfile


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _stochastic_sidecar_case(ray_count: int = 73):
    trace_input = two_bounce_input(
        max_depth=2,
        ray_count=ray_count,
        min_energy=1e-9,
        termination_mode="threshold",
        store_paths=True,
    )
    trace_input.config.intersection_backend = "bvh"
    trace_input.config.max_stored_paths = 19
    trace_input.optical_profiles = [
        OpticalProfile(
            "mirror_a",
            0.8,
            scatter_model="mixed",
            specular_ratio=0.55,
            diffuse_ratio=0.45,
            gaussian_sigma_deg=12.0,
        ),
        OpticalProfile(
            "mirror_b",
            0.5,
            scatter_model="lambertian",
        ),
    ]
    return trace_input


def _mixed_same_depth_case(ray_count: int = 101):
    trace_input = two_bounce_input(max_depth=2, ray_count=ray_count)
    trace_input.mesh.face_material[0] = "mixed_face"
    trace_input.optical_profiles.append(
        OpticalProfile(
            "mixed_face",
            0.8,
            scatter_model="mixed",
            specular_ratio=0.5,
            diffuse_ratio=0.5,
        )
    )
    return trace_input


def _invalid_native_execution(batch: WavefrontPlanInput) -> NativeCpuWavefrontExecution:
    reference = plan_deterministic_reference(batch)
    invalid_result = WavefrontPlanResult(
        supported_mask=np.zeros(len(batch), dtype=np.bool_),
        reflected_power_lumen=reference.reflected_power_lumen,
        emitted_power_lumen=reference.emitted_power_lumen,
        emitted_directions=reference.emitted_directions,
        status_flags=reference.status_flags,
        lobe_codes=reference.lobe_codes,
    )
    return NativeCpuWavefrontExecution(
        result=invalid_result,
        jit_compile_sec=0.0,
        execute_sec=0.0,
        numba_version="fake",
    )


def _malformed_native_execution(_batch: WavefrontPlanInput) -> SimpleNamespace:
    return SimpleNamespace(result=object())


class Perf3B2BNativeWavefrontPlannerTests(unittest.TestCase):
    def require_native_wavefront(self) -> None:
        capability = probe_native_cpu_wavefront()
        if not capability.available:
            self.skipTest(
                capability.reason_code or "native wavefront planner unavailable"
            )

    def assertFloatBitsEqual(self, actual, expected) -> None:
        actual_array = np.ascontiguousarray(
            np.asarray(actual, dtype=np.float64)
        )
        expected_array = np.ascontiguousarray(
            np.asarray(expected, dtype=np.float64)
        )
        self.assertEqual(actual_array.shape, expected_array.shape)
        np.testing.assert_array_equal(
            actual_array.view(np.uint64),
            expected_array.view(np.uint64),
        )

    def assertPlanResultsExact(
        self,
        actual: WavefrontPlanResult,
        expected: WavefrontPlanResult,
    ) -> None:
        np.testing.assert_array_equal(
            actual.supported_mask,
            expected.supported_mask,
        )
        self.assertFloatBitsEqual(
            actual.reflected_power_lumen,
            expected.reflected_power_lumen,
        )
        self.assertFloatBitsEqual(
            actual.emitted_power_lumen,
            expected.emitted_power_lumen,
        )
        self.assertFloatBitsEqual(
            actual.emitted_directions,
            expected.emitted_directions,
        )
        np.testing.assert_array_equal(actual.status_flags, expected.status_flags)
        np.testing.assert_array_equal(actual.lobe_codes, expected.lobe_codes)

    def test_module_import_is_lazy_and_does_not_import_numba(self) -> None:
        code = f"""
import json
import sys
sys.path.insert(0, {str(ROOT / 'src')!r})
before = 'numba' in sys.modules
import leakage_simulator.native_cpu_wavefront as native_wavefront
print(json.dumps({{
    'before': before,
    'after': 'numba' in sys.modules,
    'contract': native_wavefront.CONTRACT_VERSION,
}}))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertFalse(payload["before"])
        self.assertFalse(payload["after"])
        self.assertEqual(payload["contract"], CONTRACT_VERSION)

    def test_random_and_threshold_boundaries_match_reference_bit_exactly(
        self,
    ) -> None:
        self.require_native_wavefront()
        rng = np.random.default_rng(20260819)
        row_count = 257
        directions = rng.normal(size=(row_count, 3))
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        normals = rng.normal(size=(row_count, 3))
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        powers = rng.uniform(1e-9, 2.0, size=row_count)
        reflectance = rng.uniform(0.0, 1.0, size=row_count)
        roughness = rng.uniform(0.0, 1.0, size=row_count)
        scatter = np.where(
            np.arange(row_count) % 3 == 0,
            SCATTER_NONE,
            SCATTER_SPECULAR,
        ).astype(np.int8)
        directions_snapshot = directions.copy()
        normals_snapshot = normals.copy()

        random_batch = WavefrontPlanInput(
            incoming_directions=directions,
            surface_normals=normals,
            incoming_power_lumen=powers,
            profile_reflectance=reflectance,
            profile_roughness=roughness,
            scatter_models=scatter,
            depth=1,
            max_depth=10,
            min_energy=0.123,
            termination_mode=TERMINATION_THRESHOLD,
        )
        self.assertFalse(
            np.shares_memory(random_batch.incoming_directions, directions)
        )
        self.assertFalse(np.shares_memory(random_batch.surface_normals, normals))
        directions[0, 0] = 999.0
        normals[0, 0] = 999.0
        self.assertFloatBitsEqual(
            random_batch.incoming_directions,
            directions_snapshot,
        )
        self.assertFloatBitsEqual(random_batch.surface_normals, normals_snapshot)

        root = math.sqrt(1.0 - 0.7**2)
        below_cosine = np.nextafter(0.7, 0.0)
        above_cosine = np.nextafter(0.7, 1.0)
        boundary_batch = WavefrontPlanInput(
            incoming_directions=[
                (0.0, 0.0, -1.0),
                (0.0, 0.0, 1.0),
                (root, 0.0, -0.7),
                (math.sqrt(1.0 - below_cosine**2), 0.0, -below_cosine),
                (math.sqrt(1.0 - above_cosine**2), 0.0, -above_cosine),
                (0.0, 0.0, -1.0),
                (0.0, 0.0, -1.0),
                (0.0, 0.0, -1.0),
                (0.0, 0.0, -1.0),
            ],
            surface_normals=[(0.0, 0.0, 1.0)] * 9,
            incoming_power_lumen=[
                1.0,
                1.0,
                1.0,
                1.0,
                1.0,
                1.0,
                1.0,
                0.5,
                np.nextafter(0.5, 0.0),
            ],
            profile_reflectance=[0.8] * 6 + [0.0, 0.5, 0.5],
            profile_roughness=[0.0, 1.0, 0.25, 0.5, 0.75, 0.2, 0.0, 0.0, 0.0],
            scatter_models=[SCATTER_SPECULAR] * 5
            + [SCATTER_NONE, SCATTER_SPECULAR, SCATTER_SPECULAR, SCATTER_SPECULAR],
            depth=1,
            max_depth=10,
            min_energy=0.25,
            termination_mode=TERMINATION_THRESHOLD,
        )

        for name, batch in (
            ("random", random_batch),
            ("boundary", boundary_batch),
        ):
            with self.subTest(name=name):
                reference = plan_deterministic_reference(batch)
                execution = plan_deterministic_native_cpu(batch)
                self.assertPlanResultsExact(execution.result, reference)
                self.assertEqual(execution.contract_version, CONTRACT_VERSION)
                self.assertIsInstance(execution.numba_version, str)
                self.assertTrue(math.isfinite(execution.jit_compile_sec))
                self.assertTrue(math.isfinite(execution.execute_sec))
                self.assertGreaterEqual(execution.jit_compile_sec, 0.0)
                self.assertGreaterEqual(execution.execute_sec, 0.0)
                for values in (
                    batch.incoming_directions,
                    batch.surface_normals,
                    batch.incoming_power_lumen,
                    batch.profile_reflectance,
                    batch.profile_roughness,
                    batch.scatter_models,
                    execution.result.supported_mask,
                    execution.result.reflected_power_lumen,
                    execution.result.emitted_power_lumen,
                    execution.result.emitted_directions,
                    execution.result.status_flags,
                    execution.result.lobe_codes,
                ):
                    self.assertFalse(values.flags.writeable)
                self.assertFalse(
                    np.shares_memory(
                        execution.result.reflected_power_lumen,
                        batch.incoming_power_lumen,
                    )
                )
                self.assertFalse(
                    np.shares_memory(
                        execution.result.emitted_directions,
                        batch.incoming_directions,
                    )
                )

        boundary_reference = plan_deterministic_reference(boundary_batch)
        expected_status = [
            STATUS_ATTEMPTED | STATUS_EMITTED,
            STATUS_ATTEMPTED | STATUS_EMITTED,
            STATUS_ATTEMPTED | STATUS_EMITTED,
            STATUS_ATTEMPTED | STATUS_EMITTED,
            STATUS_ATTEMPTED | STATUS_EMITTED,
            STATUS_ATTEMPTED | STATUS_DISABLED,
            STATUS_ATTEMPTED | STATUS_BELOW_ENERGY,
            STATUS_ATTEMPTED | STATUS_EMITTED,
            STATUS_ATTEMPTED | STATUS_BELOW_ENERGY,
        ]
        np.testing.assert_array_equal(
            boundary_reference.status_flags,
            expected_status,
        )
        np.testing.assert_array_equal(
            boundary_reference.lobe_codes,
            [LOBE_SPECULAR] * 5
            + [LOBE_NONE, LOBE_NONE, LOBE_SPECULAR, LOBE_NONE],
        )

        depth_limited = WavefrontPlanInput(
            incoming_directions=boundary_batch.incoming_directions,
            surface_normals=boundary_batch.surface_normals,
            incoming_power_lumen=boundary_batch.incoming_power_lumen,
            profile_reflectance=boundary_batch.profile_reflectance,
            profile_roughness=boundary_batch.profile_roughness,
            scatter_models=boundary_batch.scatter_models,
            depth=10,
            max_depth=10,
            min_energy=0.25,
            termination_mode=TERMINATION_THRESHOLD,
        )
        depth_reference = plan_deterministic_reference(depth_limited)
        depth_native = plan_deterministic_native_cpu(depth_limited).result
        self.assertPlanResultsExact(depth_native, depth_reference)
        np.testing.assert_array_equal(
            depth_native.status_flags,
            [STATUS_DEPTH_LIMITED | STATUS_DISABLED] * len(depth_limited),
        )

        with self.assertRaises(ValueError):
            boundary_reference.status_flags[0] = 0

    def test_depth_two_and_ten_native_planner_preserve_full_semantics(self) -> None:
        self.require_native_wavefront()
        cases = (
            (
                "depth_two",
                lambda: two_bounce_input(max_depth=2, ray_count=73),
                17,
                146,
                10,
            ),
            (
                "depth_ten",
                lambda: ten_bounce_corridor_input(max_depth=10),
                17,
                1000,
                60,
            ),
        )
        for name, builder, chunk_size, logical_rows, native_calls in cases:
            with self.subTest(name=name):
                reference = run_direct_ray_trace(
                    builder(),
                    intersection_dispatch="batch",
                    intersection_batch_size=chunk_size,
                    intersection_provider="python_cpu",
                    wavefront_planner="python_cpu",
                )
                native = run_direct_ray_trace(
                    builder(),
                    intersection_dispatch="batch",
                    intersection_batch_size=chunk_size,
                    intersection_provider="python_cpu",
                    wavefront_planner="numba_cpu",
                )

                self.assertEqual(
                    _semantic_payload(native),
                    _semantic_payload(reference),
                )
                performance = native.metrics["_performance_summary"]
                self.assertEqual(
                    performance["requested_wavefront_planner"],
                    "numba_cpu",
                )
                self.assertEqual(performance["wavefront_planner"], "numba_cpu")
                self.assertEqual(
                    performance["wavefront_planner_contract"],
                    CONTRACT_VERSION,
                )
                self.assertEqual(
                    performance["wavefront_planner_logical_row_count"],
                    logical_rows,
                )
                self.assertEqual(
                    performance["wavefront_planner_python_sidecar_row_count"],
                    0,
                )
                self.assertTrue(
                    performance["wavefront_planner_native_available"]
                )
                self.assertTrue(performance["wavefront_planner_native_used"])
                self.assertFalse(
                    performance["wavefront_planner_native_provider_disabled"]
                )
                self.assertEqual(
                    performance["wavefront_planner_native_attempt_count"],
                    native_calls,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_attempt_row_count"],
                    logical_rows,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_success_count"],
                    native_calls,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_success_row_count"],
                    logical_rows,
                )
                self.assertEqual(performance["wavefront_planner_fallback_count"], 0)
                self.assertEqual(
                    performance["wavefront_planner_fallback_row_count"],
                    0,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_success_row_count"]
                    + performance["wavefront_planner_python_sidecar_row_count"],
                    performance["wavefront_planner_logical_row_count"],
                )
                for timing_key in (
                    "wavefront_planner_native_input_prepare_sec",
                    "wavefront_planner_native_dispatch_sec",
                    "wavefront_planner_native_execute_sec",
                    "wavefront_planner_native_jit_compile_sec",
                ):
                    value = performance[timing_key]
                    self.assertIs(type(value), float)
                    self.assertTrue(math.isfinite(value))
                    self.assertGreaterEqual(value, 0.0)
                json.dumps(native.to_dict(), allow_nan=False)

    def test_stochastic_mixed_rows_use_python_sidecar_across_chunks_and_providers(
        self,
    ) -> None:
        reference = run_direct_ray_trace(
            _stochastic_sidecar_case(),
            intersection_dispatch="batch",
            intersection_batch_size=1,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
        )
        expected_payload = _semantic_payload(reference)

        cases = (
            (7, "python_cpu"),
            (64, "numba_cpu"),
            (4096, "python_cpu"),
        )
        for chunk_size, intersection_provider in cases:
            with self.subTest(
                chunk_size=chunk_size,
                intersection_provider=intersection_provider,
            ):
                result = run_direct_ray_trace(
                    _stochastic_sidecar_case(),
                    intersection_dispatch="batch",
                    intersection_batch_size=chunk_size,
                    intersection_provider=intersection_provider,
                    wavefront_planner="numba_cpu",
                )
                self.assertEqual(_semantic_payload(result), expected_payload)
                performance = result.metrics["_performance_summary"]
                self.assertEqual(
                    performance["wavefront_planner_logical_row_count"],
                    performance[
                        "wavefront_planner_python_sidecar_row_count"
                    ],
                )
                self.assertGreater(
                    performance["wavefront_planner_python_sidecar_row_count"],
                    0,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_attempt_count"],
                    0,
                )
                self.assertEqual(performance["wavefront_planner"], "python_cpu")
                self.assertEqual(
                    performance["wavefront_stochastic_primary_ray_count"],
                    73,
                )
                if intersection_provider == "numba_cpu":
                    self.assertEqual(
                        performance["intersection_provider"],
                        "numba_cpu",
                    )
                    self.assertTrue(performance["native_used"])

    def test_planner_failures_fallback_once_without_duplicate_logical_rows(
        self,
    ) -> None:
        ray_count = 23
        reference = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=ray_count),
            intersection_dispatch="batch",
            intersection_batch_size=ray_count,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
        )
        expected_payload = _semantic_payload(reference)
        failures = (
            (
                "unavailable",
                NativeCpuWavefrontUnavailable("injected_unavailable"),
                None,
                None,
                "injected_unavailable",
            ),
            (
                "initialize",
                NativeCpuWavefrontProviderError(
                    "initialize",
                    "injected_initialize",
                ),
                "initialize",
                "injected_initialize",
                None,
            ),
            (
                "execute",
                NativeCpuWavefrontProviderError(
                    "execute",
                    "injected_execute",
                ),
                "execute",
                "injected_execute",
                None,
            ),
            (
                "result_validation",
                NativeCpuWavefrontProviderError(
                    "result_validation",
                    "injected_result_validation",
                ),
                "result_validation",
                "injected_result_validation",
                None,
            ),
            (
                "unsupported_output",
                _invalid_native_execution,
                "result_validation",
                "native_wavefront_unsupported_output",
                None,
            ),
            (
                "malformed_output",
                _malformed_native_execution,
                "result_validation",
                "native_wavefront_invalid_output",
                None,
            ),
        )

        for name, side_effect, phase, reason, unavailable_reason in failures:
            with self.subTest(name=name):
                with patch(
                    "leakage_simulator.raytracer.plan_deterministic_native_cpu",
                    side_effect=side_effect,
                ) as native_mock:
                    result = run_direct_ray_trace(
                        two_bounce_input(max_depth=2, ray_count=ray_count),
                        intersection_dispatch="batch",
                        intersection_batch_size=ray_count,
                        intersection_provider="python_cpu",
                        wavefront_planner="numba_cpu",
                    )

                self.assertEqual(_semantic_payload(result), expected_payload)
                native_mock.assert_called_once()
                performance = result.metrics["_performance_summary"]
                logical_rows = ray_count * 2
                self.assertEqual(performance["wavefront_planner"], "python_cpu")
                self.assertTrue(
                    performance["wavefront_planner_native_provider_disabled"]
                )
                self.assertEqual(
                    performance["wavefront_planner_logical_row_count"],
                    logical_rows,
                )
                self.assertEqual(
                    performance["wavefront_planner_python_sidecar_row_count"],
                    logical_rows,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_attempt_count"],
                    1,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_attempt_row_count"],
                    ray_count,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_success_count"],
                    0,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_success_row_count"],
                    0,
                )
                self.assertEqual(
                    performance["wavefront_planner_native_success_row_count"]
                    + performance["wavefront_planner_python_sidecar_row_count"],
                    performance["wavefront_planner_logical_row_count"],
                )
                self.assertEqual(
                    performance["wavefront_planner_unavailable_reason"],
                    unavailable_reason,
                )
                if phase is None:
                    self.assertFalse(
                        performance["wavefront_planner_native_available"]
                    )
                    self.assertEqual(
                        performance["wavefront_planner_fallback_count"],
                        0,
                    )
                    self.assertEqual(
                        performance["wavefront_planner_fallback_row_count"],
                        0,
                    )
                else:
                    self.assertTrue(
                        performance["wavefront_planner_native_available"]
                    )
                    self.assertEqual(
                        performance["wavefront_planner_fallback_count"],
                        1,
                    )
                    self.assertEqual(
                        performance["wavefront_planner_fallback_row_count"],
                        ray_count,
                    )
                self.assertEqual(
                    performance["wavefront_planner_fallback_phase"],
                    phase,
                )
                self.assertEqual(
                    performance["wavefront_planner_fallback_reason"],
                    reason,
                )
                json.dumps(result.to_dict(), allow_nan=False)

    def test_input_prepare_failure_falls_back_transactionally_once(self) -> None:
        ray_count = 23
        native_candidate_counts = []

        def fail_input_prepare(*args, **_kwargs):
            native_candidate_counts.append(len(np.asarray(args[0])))
            raise RuntimeError("injected_input_prepare")

        reference = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=ray_count),
            intersection_dispatch="batch",
            intersection_batch_size=ray_count,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
        )

        with (
            patch(
                "leakage_simulator.raytracer.WavefrontPlanInput",
                side_effect=fail_input_prepare,
            ) as prepare_mock,
            patch(
                "leakage_simulator.raytracer.plan_deterministic_native_cpu",
                side_effect=AssertionError(
                    "input preparation failure must prevent provider dispatch"
                ),
            ) as native_mock,
        ):
            result = run_direct_ray_trace(
                two_bounce_input(max_depth=2, ray_count=ray_count),
                intersection_dispatch="batch",
                intersection_batch_size=ray_count,
                intersection_provider="python_cpu",
                wavefront_planner="numba_cpu",
            )

        self.assertEqual(_semantic_payload(result), _semantic_payload(reference))
        prepare_mock.assert_called_once()
        native_mock.assert_not_called()
        self.assertEqual(native_candidate_counts, [ray_count])
        performance = result.metrics["_performance_summary"]
        logical_rows = ray_count * 2
        self.assertEqual(performance["wavefront_planner"], "python_cpu")
        self.assertTrue(
            performance["wavefront_planner_native_provider_disabled"]
        )
        self.assertIsNone(performance["wavefront_planner_native_available"])
        self.assertEqual(
            performance["wavefront_planner_logical_row_count"],
            logical_rows,
        )
        self.assertEqual(
            performance["wavefront_planner_python_sidecar_row_count"],
            logical_rows,
        )
        self.assertEqual(
            performance["wavefront_planner_native_attempt_count"],
            0,
        )
        self.assertEqual(
            performance["wavefront_planner_native_attempt_row_count"],
            0,
        )
        self.assertEqual(
            performance["wavefront_planner_native_success_count"],
            0,
        )
        self.assertEqual(
            performance["wavefront_planner_native_success_row_count"],
            0,
        )
        self.assertEqual(performance["wavefront_planner_fallback_count"], 1)
        self.assertEqual(
            performance["wavefront_planner_fallback_row_count"],
            native_candidate_counts[0],
        )
        self.assertEqual(
            performance["wavefront_planner_fallback_phase"],
            "input_prepare",
        )
        self.assertEqual(
            performance["wavefront_planner_fallback_reason"],
            "native_wavefront_input_prepare_failed",
        )
        self.assertIsNone(
            performance["wavefront_planner_unavailable_reason"]
        )
        self.assertEqual(
            performance["wavefront_planner_native_success_row_count"]
            + performance["wavefront_planner_python_sidecar_row_count"],
            performance["wavefront_planner_logical_row_count"],
        )
        json.dumps(result.to_dict(), allow_nan=False)

    def test_input_prepare_fallback_excludes_stochastic_same_depth_rows(
        self,
    ) -> None:
        ray_count = 101
        native_candidate_counts = []

        def fail_input_prepare(*args, **_kwargs):
            native_candidate_counts.append(len(np.asarray(args[0])))
            raise RuntimeError("injected_mixed_input_prepare")

        reference = run_direct_ray_trace(
            _mixed_same_depth_case(ray_count),
            intersection_dispatch="batch",
            intersection_batch_size=ray_count,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
        )
        with (
            patch(
                "leakage_simulator.raytracer.WavefrontPlanInput",
                side_effect=fail_input_prepare,
            ) as prepare_mock,
            patch(
                "leakage_simulator.raytracer.plan_deterministic_native_cpu",
                side_effect=AssertionError(
                    "input preparation failure must prevent provider dispatch"
                ),
            ) as native_mock,
        ):
            result = run_direct_ray_trace(
                _mixed_same_depth_case(ray_count),
                intersection_dispatch="batch",
                intersection_batch_size=ray_count,
                intersection_provider="python_cpu",
                wavefront_planner="numba_cpu",
            )

        self.assertEqual(_semantic_payload(result), _semantic_payload(reference))
        prepare_mock.assert_called_once()
        native_mock.assert_not_called()
        self.assertEqual(len(native_candidate_counts), 1)
        native_candidate_count = native_candidate_counts[0]
        self.assertGreater(native_candidate_count, 0)
        self.assertLess(native_candidate_count, ray_count)

        performance = result.metrics["_performance_summary"]
        logical_rows = performance["wavefront_planner_logical_row_count"]
        self.assertGreater(logical_rows, ray_count)
        self.assertEqual(
            performance["wavefront_planner_python_sidecar_row_count"],
            logical_rows,
        )
        self.assertEqual(
            performance["wavefront_planner_fallback_row_count"],
            native_candidate_count,
        )
        self.assertLess(
            performance["wavefront_planner_fallback_row_count"],
            ray_count,
        )
        self.assertEqual(performance["wavefront_planner_fallback_count"], 1)
        self.assertEqual(
            performance["wavefront_planner_fallback_phase"],
            "input_prepare",
        )
        self.assertEqual(
            performance["wavefront_planner_fallback_reason"],
            "native_wavefront_input_prepare_failed",
        )
        self.assertTrue(
            performance["wavefront_planner_native_provider_disabled"]
        )
        self.assertEqual(
            performance["wavefront_planner_native_attempt_count"],
            0,
        )
        self.assertEqual(
            performance["wavefront_planner_native_attempt_row_count"],
            0,
        )
        self.assertEqual(
            performance["wavefront_planner_native_success_count"],
            0,
        )
        self.assertEqual(performance["wavefront_planner"], "python_cpu")
        json.dumps(result.to_dict(), allow_nan=False)

    def test_default_auto_and_scalar_paths_do_not_probe_native_planner(self) -> None:
        reference = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=31),
            intersection_dispatch="batch",
            intersection_batch_size=31,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
        )
        with (
            patch(
                "leakage_simulator.native_cpu_wavefront.probe_native_cpu_wavefront",
                side_effect=AssertionError("auto/scalar must not probe Numba"),
            ) as probe_mock,
            patch(
                "leakage_simulator.raytracer.plan_deterministic_native_cpu",
                side_effect=AssertionError("auto/scalar must not call native planner"),
            ) as planner_mock,
        ):
            automatic = run_direct_ray_trace(
                two_bounce_input(max_depth=2, ray_count=31),
                intersection_dispatch="batch",
                intersection_batch_size=31,
                intersection_provider="python_cpu",
            )
            scalar = run_direct_ray_trace(
                two_bounce_input(max_depth=2, ray_count=31),
                intersection_dispatch="scalar",
                intersection_provider="python_cpu",
                wavefront_planner="numba_cpu",
            )

        self.assertEqual(_semantic_payload(automatic), _semantic_payload(reference))
        probe_mock.assert_not_called()
        planner_mock.assert_not_called()
        automatic_performance = automatic.metrics["_performance_summary"]
        self.assertEqual(
            automatic_performance["requested_wavefront_planner"],
            "auto",
        )
        self.assertEqual(
            automatic_performance["wavefront_planner"],
            "python_cpu",
        )
        self.assertEqual(
            automatic_performance["wavefront_planner_native_attempt_count"],
            0,
        )
        self.assertEqual(
            automatic_performance["wavefront_planner_logical_row_count"],
            automatic_performance[
                "wavefront_planner_python_sidecar_row_count"
            ],
        )
        scalar_performance = scalar.metrics["_performance_summary"]
        self.assertEqual(
            scalar_performance["requested_wavefront_planner"],
            "numba_cpu",
        )
        self.assertEqual(scalar_performance["wavefront_planner"], "not_used")
        self.assertEqual(
            scalar_performance["wavefront_planner_logical_row_count"],
            0,
        )
        self.assertEqual(
            scalar_performance["wavefront_planner_native_attempt_count"],
            0,
        )

    def test_invalid_planner_values_raise_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "wavefront_planner"):
            run_direct_ray_trace(
                two_bounce_input(max_depth=2, ray_count=3),
                wavefront_planner="cuda",
            )
        with self.assertRaises(ValueError):
            scatter_codes_from_names(["specular", "phong"])
        with self.assertRaises(ValueError):
            WavefrontPlanInput(
                incoming_directions=[(0.0, 0.0, -1.0)],
                surface_normals=[(0.0, 0.0, 1.0)],
                incoming_power_lumen=[-1.0],
                profile_reflectance=[0.5],
                profile_roughness=[0.0],
                scatter_models=[SCATTER_SPECULAR],
                depth=0,
                max_depth=2,
                min_energy=0.0,
            )
        with self.assertRaises(ValueError):
            WavefrontPlanInput(
                incoming_directions=[(0.0, 0.0, -1.0)],
                surface_normals=[(0.0, 0.0, 1.0)],
                incoming_power_lumen=[1.0],
                profile_reflectance=[0.5],
                profile_roughness=[0.0],
                scatter_models=[99],
                depth=0,
                max_depth=2,
                min_energy=0.0,
            )
        with self.assertRaises(ValueError):
            WavefrontPlanResult(
                supported_mask=[True],
                reflected_power_lumen=[0.5],
                emitted_power_lumen=[0.5],
                emitted_directions=[(0.0, 0.0, 1.0)],
                status_flags=[STATUS_ATTEMPTED | STATUS_EMITTED],
                lobe_codes=[99],
            )


if __name__ == "__main__":
    unittest.main()
