from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
import json
import math
import struct
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import ten_bounce_corridor_input, two_bounce_input
from test_perf3b2a_multibounce_wavefront import stochastic_two_bounce_input

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator import native_cpu_ordered_reducer as native_reducer
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, RayTraceConfig, ReceiverSpec


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _ordered_float_bits(value):
    if isinstance(value, dict):
        return tuple(
            (key, _ordered_float_bits(item)) for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return tuple(_ordered_float_bits(item) for item in value)
    if isinstance(value, (float, np.floating)):
        return ("float64", struct.pack(">d", float(value)).hex())
    return value


def _run_object(trace_input, *, chunk_size: int):
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="python_cpu",
        wavefront_planner="python_cpu",
        wavefront_pipeline="object_reference",
    )


def _run_soa(trace_input, *, chunk_size: int, reducer: str):
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="python_cpu",
        wavefront_planner="python_cpu",
        wavefront_pipeline="soa_event_tape",
        wavefront_reducer=reducer,
    )


def _summary_two_bounce(ray_count: int = 41):
    trace_input = two_bounce_input(max_depth=2, ray_count=ray_count)
    trace_input.config.contribution_mode = "summary"
    return trace_input


def _summary_stochastic(ray_count: int = 257):
    trace_input = stochastic_two_bounce_input(ray_count)
    trace_input.config.contribution_mode = "summary"
    return trace_input


def _multi_emitter_input():
    trace_input = _summary_two_bounce(31)
    second = copy.deepcopy(trace_input.emitters[0])
    second.emitter_id = "source_b"
    second.seed = int(second.seed or 0) + 1
    second.ray_count = 19
    second.power_lumen = 0.7
    trace_input.emitters.append(second)
    trace_input.config.ray_count = 50
    return trace_input


def _terminal_only_input(ray_count: int = 19):
    return DirectRayTraceInput(
        mesh=TriangleMesh(),
        emitters=[
            EmitterSpec(
                emitter_id="direct_source",
                emitter_type="datum_plane",
                center=(0.0, 0.0, 0.0),
                u_axis=(1.0, 0.0, 0.0),
                v_axis=(0.0, 1.0, 0.0),
                width_mm=0.01,
                height_mm=0.01,
                direction_distribution="gaussian",
                gaussian_sigma_deg=0.001,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=5,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="direct_receiver",
                center=(0.0, 0.0, 2.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=1.0,
                height_mm=1.0,
                resolution=(4, 4),
            )
        ],
        optical_profiles=[],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=2,
            contribution_mode="summary",
            store_ray_paths=False,
        ),
    )


def _numpy_fields(instance):
    return [
        (field.name, getattr(instance, field.name))
        for field in fields(instance)
        if isinstance(getattr(instance, field.name), np.ndarray)
    ]


def _array_snapshot(instance):
    return {
        name: (values.dtype.str, values.shape, values.tobytes())
        for name, values in _numpy_fields(instance)
    }


class Perf3B2C2NativeOrderedReducerTests(unittest.TestCase):
    def requireNative(self) -> None:
        capability = native_reducer.probe_native_cpu_ordered_reducer()
        if not capability.available:
            self.skipTest(capability.reason_code or "Numba unavailable")

    def assertSemanticBitsAndOrderEqual(self, actual, expected) -> None:
        actual_payload = _semantic_payload(actual)
        expected_payload = _semantic_payload(expected)
        self.assertEqual(
            _ordered_float_bits(actual_payload),
            _ordered_float_bits(expected_payload),
        )
        self.assertEqual(
            json.dumps(actual_payload, allow_nan=False, separators=(",", ":")),
            json.dumps(expected_payload, allow_nan=False, separators=(",", ":")),
        )

    def assertNativeMetrics(self, result) -> None:
        performance = result.metrics["_performance_summary"]
        self.assertEqual(
            performance["wavefront_reducer_contract"],
            "ordered_summary_reducer_v1",
        )
        self.assertEqual(performance["requested_wavefront_reducer"], "numba_cpu")
        self.assertEqual(performance["wavefront_reducer"], "numba_cpu")
        self.assertEqual(
            performance["wavefront_reducer_selection_reason"],
            "eligible_explicit_numba",
        )
        self.assertIs(performance["wavefront_reducer_native_available"], True)
        self.assertIs(performance["wavefront_reducer_native_used"], True)
        self.assertIs(
            performance["wavefront_reducer_native_provider_disabled"],
            False,
        )
        self.assertIsInstance(
            performance["wavefront_reducer_native_provider_version"],
            str,
        )
        self.assertGreater(
            performance["wavefront_reducer_native_attempt_count"],
            0,
        )
        self.assertEqual(
            performance["wavefront_reducer_native_attempt_count"],
            performance["wavefront_reducer_native_success_count"],
        )
        self.assertEqual(
            performance["wavefront_reducer_native_attempt_primary_count"],
            performance["wavefront_reducer_logical_primary_count"],
        )
        self.assertEqual(
            performance["wavefront_reducer_native_attempt_event_count"],
            performance["wavefront_reducer_logical_event_count"],
        )
        self.assertEqual(performance["wavefront_reducer_python_tape_count"], 0)
        self.assertEqual(performance["wavefront_reducer_fallback_count"], 0)
        timing_fields = (
            "wavefront_reducer_native_prepare_sec",
            "wavefront_reducer_native_dispatch_sec",
            "wavefront_reducer_native_jit_compile_sec",
            "wavefront_reducer_native_execute_sec",
            "wavefront_reducer_native_result_validation_sec",
            "wavefront_reducer_native_apply_sec",
            "wavefront_reducer_native_path_sec",
        )
        for name in timing_fields:
            value = performance[name]
            self.assertIs(type(value), float, name)
            self.assertTrue(math.isfinite(value), name)
            self.assertGreaterEqual(value, 0.0, name)
        json.dumps(result.to_dict(), allow_nan=False)

    def assertOwnedReadonlyNoAlias(self, named_arrays) -> None:
        for name, values in named_arrays:
            self.assertTrue(values.flags.c_contiguous, name)
            self.assertTrue(values.flags.owndata, name)
            self.assertFalse(values.flags.writeable, name)
        nonempty = [item for item in named_arrays if item[1].nbytes]
        for index, (left_name, left) in enumerate(nonempty):
            for right_name, right in nonempty[index + 1 :]:
                self.assertFalse(
                    np.shares_memory(left, right),
                    f"{left_name} aliases {right_name}",
                )

    def test_object_python_and_native_are_bit_exact_across_matrix(self) -> None:
        self.requireNative()
        cases = (
            ("deterministic_chunk_1", lambda: _summary_two_bounce(41), 1),
            ("deterministic_chunk_17", lambda: _summary_two_bounce(41), 17),
            ("stochastic_chunk_7", lambda: _summary_stochastic(257), 7),
            ("stochastic_chunk_64", lambda: _summary_stochastic(257), 64),
            ("depth_ten", lambda: ten_bounce_corridor_input(10), 17),
            ("multi_emitter", _multi_emitter_input, 11),
        )
        for name, factory, chunk_size in cases:
            with self.subTest(case=name):
                reference = _run_object(factory(), chunk_size=chunk_size)
                python_soa = _run_soa(
                    factory(),
                    chunk_size=chunk_size,
                    reducer="python_cpu",
                )
                native_soa = _run_soa(
                    factory(),
                    chunk_size=chunk_size,
                    reducer="numba_cpu",
                )
                self.assertSemanticBitsAndOrderEqual(python_soa, reference)
                self.assertSemanticBitsAndOrderEqual(native_soa, reference)
                self.assertEqual(native_soa.receiver_grids, reference.receiver_grids)
                self.assertEqual(native_soa.stored_paths, reference.stored_paths)
                self.assertEqual(
                    native_soa.contribution_summary.to_dict(),
                    reference.contribution_summary.to_dict(),
                )
                self.assertNativeMetrics(native_soa)

    def test_detailed_mode_uses_python_without_native_probe(self) -> None:
        reference = _run_soa(
            two_bounce_input(max_depth=2, ray_count=29),
            chunk_size=7,
            reducer="python_cpu",
        )
        with (
            patch.object(
                native_reducer,
                "probe_native_cpu_ordered_reducer",
                side_effect=AssertionError("detailed mode must not probe Numba"),
            ) as probe_mock,
            patch.object(
                native_reducer,
                "reduce_ordered_summary_native_cpu",
                side_effect=AssertionError("detailed mode must stay on Python"),
            ) as reduce_mock,
        ):
            actual = _run_soa(
                two_bounce_input(max_depth=2, ray_count=29),
                chunk_size=7,
                reducer="numba_cpu",
            )

        self.assertSemanticBitsAndOrderEqual(actual, reference)
        probe_mock.assert_not_called()
        reduce_mock.assert_not_called()
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(performance["requested_wavefront_reducer"], "numba_cpu")
        self.assertEqual(performance["wavefront_reducer"], "python_cpu")
        self.assertEqual(
            performance["wavefront_reducer_selection_reason"],
            "detailed_contributions_unsupported",
        )
        self.assertEqual(
            performance["wavefront_reducer_contract"],
            "python_ordered_v1",
        )
        self.assertEqual(performance["wavefront_reducer_native_attempt_count"], 0)
        json.dumps(actual.to_dict(), allow_nan=False)

    def test_terminal_only_tapes_track_primary_and_event_counts_separately(self) -> None:
        self.requireNative()
        ray_count = 19
        chunk_size = 7
        reference = _run_object(
            _terminal_only_input(ray_count),
            chunk_size=chunk_size,
        )
        python_soa = _run_soa(
            _terminal_only_input(ray_count),
            chunk_size=chunk_size,
            reducer="python_cpu",
        )
        native_soa = _run_soa(
            _terminal_only_input(ray_count),
            chunk_size=chunk_size,
            reducer="numba_cpu",
        )
        self.assertSemanticBitsAndOrderEqual(python_soa, reference)
        self.assertSemanticBitsAndOrderEqual(native_soa, reference)
        self.assertEqual(native_soa.receiver_hit_count, ray_count)
        self.assertEqual(native_soa.surface_hit_count, 0)
        self.assertEqual(native_soa.terminated_ray_count, 0)
        performance = native_soa.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_event_count"], 0)
        self.assertEqual(
            performance["wavefront_reducer_logical_primary_count"],
            ray_count,
        )
        self.assertEqual(performance["wavefront_reducer_logical_event_count"], 0)
        self.assertEqual(
            performance["wavefront_reducer_native_attempt_primary_count"],
            ray_count,
        )
        self.assertEqual(
            performance["wavefront_reducer_native_attempt_event_count"],
            0,
        )
        self.assertNativeMetrics(native_soa)

    def test_native_failure_phases_fallback_atomically_and_open_circuit(self) -> None:
        self.requireNative()
        ray_count = 23
        chunk_size = 7
        reference = _run_soa(
            _summary_two_bounce(ray_count),
            chunk_size=chunk_size,
            reducer="python_cpu",
        )
        real_reduce = native_reducer.reduce_ordered_summary_native_cpu

        def unavailable(_batch, _state):
            raise native_reducer.NativeCpuOrderedReducerUnavailable(
                "injected_unavailable"
            )

        def initialize_failure(_batch, _state):
            raise native_reducer.NativeCpuOrderedReducerProviderError(
                "initialize",
                "injected_initialize_failure",
            )

        def execute_failure(_batch, state):
            # Mutate only the provider scratch input. The public summaries must
            # remain untouched when the whole tape is replayed in Python.
            state.optical_counts[0] += 999
            state.grid_hit_counts[:] = 123
            raise native_reducer.NativeCpuOrderedReducerProviderError(
                "execute",
                "injected_execute_failure",
            )

        def invalid_result(batch, state):
            execution = real_reduce(batch, state)
            return replace(execution, result_digest="0" * 64)

        cases = (
            (
                "unavailable",
                unavailable,
                0,
                None,
                None,
                "injected_unavailable",
            ),
            (
                "initialize",
                initialize_failure,
                1,
                "initialize",
                "injected_initialize_failure",
                None,
            ),
            (
                "execute",
                execute_failure,
                1,
                "execute",
                "injected_execute_failure",
                None,
            ),
            (
                "result_validation",
                invalid_result,
                1,
                "result_validation",
                "native_ordered_reducer_consumer_validation_failed",
                None,
            ),
        )
        for (
            name,
            provider,
            fallback_count,
            fallback_phase,
            fallback_reason,
            unavailable_reason,
        ) in cases:
            with self.subTest(case=name):
                with patch.object(
                    native_reducer,
                    "reduce_ordered_summary_native_cpu",
                    side_effect=provider,
                ) as provider_mock:
                    actual = _run_soa(
                        _summary_two_bounce(ray_count),
                        chunk_size=chunk_size,
                        reducer="numba_cpu",
                    )

                self.assertSemanticBitsAndOrderEqual(actual, reference)
                provider_mock.assert_called_once()
                performance = actual.metrics["_performance_summary"]
                self.assertEqual(
                    performance["wavefront_reducer_contract"],
                    "python_ordered_v1",
                )
                self.assertEqual(performance["wavefront_reducer"], "python_cpu")
                self.assertTrue(
                    performance["wavefront_reducer_native_provider_disabled"]
                )
                self.assertEqual(
                    performance["wavefront_reducer_native_attempt_count"],
                    1,
                )
                self.assertEqual(
                    performance["wavefront_reducer_native_attempt_primary_count"],
                    chunk_size,
                )
                self.assertEqual(
                    performance["wavefront_reducer_native_attempt_event_count"],
                    chunk_size * 2,
                )
                self.assertEqual(
                    performance["wavefront_reducer_python_tape_count"],
                    performance["wavefront_reducer_logical_tape_count"],
                )
                self.assertEqual(
                    performance["wavefront_reducer_python_primary_count"],
                    ray_count,
                )
                self.assertEqual(
                    performance["wavefront_reducer_python_event_count"],
                    ray_count * 2,
                )
                self.assertEqual(
                    performance["wavefront_reducer_logical_primary_count"],
                    ray_count,
                )
                self.assertEqual(
                    performance["wavefront_reducer_logical_event_count"],
                    ray_count * 2,
                )
                self.assertEqual(
                    performance["wavefront_reducer_fallback_count"],
                    fallback_count,
                )
                self.assertEqual(
                    performance["wavefront_reducer_fallback_phase"],
                    fallback_phase,
                )
                self.assertEqual(
                    performance["wavefront_reducer_fallback_reason"],
                    fallback_reason,
                )
                self.assertEqual(
                    performance["wavefront_reducer_unavailable_reason"],
                    unavailable_reason,
                )
                if fallback_count:
                    self.assertEqual(
                        performance["wavefront_reducer_fallback_primary_count"],
                        chunk_size,
                    )
                    self.assertEqual(
                        performance["wavefront_reducer_fallback_event_count"],
                        chunk_size * 2,
                    )
                else:
                    self.assertEqual(
                        performance["wavefront_reducer_fallback_primary_count"],
                        0,
                    )
                    self.assertEqual(
                        performance["wavefront_reducer_fallback_event_count"],
                        0,
                    )
                json.dumps(actual.to_dict(), allow_nan=False)

    def test_provider_inputs_are_immutable_and_outputs_owned_readonly_no_alias(self) -> None:
        self.requireNative()
        real_reduce = native_reducer.reduce_ordered_summary_native_cpu
        captured = []

        def inspect_provider(batch, state):
            batch_before = _array_snapshot(batch)
            state_before = _array_snapshot(state)
            batch_arrays = _numpy_fields(batch)
            state_arrays = _numpy_fields(state)
            for name, values in batch_arrays:
                self.assertTrue(values.flags.c_contiguous, name)
                self.assertTrue(values.flags.owndata, name)
                self.assertFalse(values.flags.writeable, name)
            for name, values in state_arrays:
                self.assertTrue(values.flags.c_contiguous, name)
                self.assertTrue(values.flags.owndata, name)
                self.assertTrue(values.flags.writeable, name)

            execution = real_reduce(batch, state)
            self.assertEqual(_array_snapshot(batch), batch_before)
            self.assertEqual(_array_snapshot(state), state_before)
            self.assertOwnedReadonlyNoAlias(_numpy_fields(execution.result.state))
            result_vectors = [
                (name, values)
                for name, values in _numpy_fields(execution.result)
            ]
            self.assertOwnedReadonlyNoAlias(result_vectors)
            output_arrays = _numpy_fields(execution.result.state) + result_vectors
            self.assertOwnedReadonlyNoAlias(output_arrays)
            for input_name, input_values in batch_arrays + state_arrays:
                for output_name, output_values in output_arrays:
                    if input_values.nbytes and output_values.nbytes:
                        self.assertFalse(
                            np.shares_memory(input_values, output_values),
                            f"{input_name} aliases output {output_name}",
                        )
            captured.append(execution)
            return execution

        reference = _run_soa(
            _summary_two_bounce(19),
            chunk_size=19,
            reducer="python_cpu",
        )
        with patch.object(
            native_reducer,
            "reduce_ordered_summary_native_cpu",
            side_effect=inspect_provider,
        ) as provider_mock:
            actual = _run_soa(
                _summary_two_bounce(19),
                chunk_size=19,
                reducer="numba_cpu",
            )

        provider_mock.assert_called_once()
        self.assertEqual(len(captured), 1)
        native_reducer.validate_ordered_summary_execution(
            provider_mock.call_args.args[0],
            captured[0],
        )
        self.assertSemanticBitsAndOrderEqual(actual, reference)
        self.assertNativeMetrics(actual)

    def test_native_path_quota_replaces_oldest_dead_end_with_receiver(self) -> None:
        self.requireNative()

        def factory():
            trace_input = _summary_stochastic(257)
            trace_input.config.max_stored_paths = 1
            return trace_input

        reference = _run_object(factory(), chunk_size=17)
        python_soa = _run_soa(factory(), chunk_size=17, reducer="python_cpu")
        native_soa = _run_soa(factory(), chunk_size=17, reducer="numba_cpu")
        self.assertSemanticBitsAndOrderEqual(python_soa, reference)
        self.assertSemanticBitsAndOrderEqual(native_soa, reference)
        self.assertEqual(len(native_soa.stored_paths), 1)
        self.assertEqual(native_soa.stored_paths[0][-1].event_type, "receiver")
        performance = native_soa.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_path_materialized_count"], 2)
        self.assertEqual(
            performance["wavefront_path_materialization_skipped_count"],
            255,
        )
        self.assertNativeMetrics(native_soa)

    def test_stop_commits_one_complete_primary_chunk_with_native_reducer(self) -> None:
        self.requireNative()

        def stopped_run(pipeline: str, reducer: str):
            trace_input = _summary_two_bounce(48)
            stop_event = threading.Event()
            original_intersect = trace_input.mesh.intersect_rays
            calls = 0

            def stop_on_second_depth(rays, backend=None):
                nonlocal calls
                calls += 1
                hits = original_intersect(rays, backend=backend)
                if calls == 2:
                    stop_event.set()
                return hits

            with patch.object(
                trace_input.mesh,
                "intersect_rays",
                side_effect=stop_on_second_depth,
            ):
                result = run_direct_ray_trace(
                    trace_input,
                    should_stop=stop_event.is_set,
                    intersection_dispatch="batch",
                    intersection_batch_size=16,
                    intersection_provider="python_cpu",
                    wavefront_planner="python_cpu",
                    wavefront_pipeline=pipeline,
                    wavefront_reducer=reducer,
                )
            return result, calls

        reference, reference_calls = stopped_run("object_reference", "auto")
        python_soa, python_calls = stopped_run("soa_event_tape", "python_cpu")
        native_soa, native_calls = stopped_run("soa_event_tape", "numba_cpu")
        self.assertEqual((reference_calls, python_calls, native_calls), (3, 3, 3))
        self.assertSemanticBitsAndOrderEqual(python_soa, reference)
        self.assertSemanticBitsAndOrderEqual(native_soa, reference)
        self.assertEqual(native_soa.total_rays, 16)
        self.assertEqual(native_soa.receiver_hit_count, 16)
        self.assertEqual(native_soa.surface_hit_count, 32)
        performance = native_soa.metrics["_performance_summary"]
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["wavefront_chunk_count"], 1)
        self.assertEqual(performance["wavefront_reducer_logical_tape_count"], 1)
        self.assertEqual(
            performance["wavefront_reducer_logical_primary_count"],
            16,
        )
        self.assertEqual(performance["wavefront_reducer_logical_event_count"], 32)
        self.assertNativeMetrics(native_soa)

    def test_default_and_auto_reducer_never_import_probe_or_execute_numba(self) -> None:
        reference = _run_soa(
            _summary_two_bounce(31),
            chunk_size=7,
            reducer="python_cpu",
        )
        with (
            patch.object(
                native_reducer.importlib,
                "import_module",
                side_effect=AssertionError("auto reducer must not import Numba"),
            ) as import_mock,
            patch.object(
                native_reducer,
                "probe_native_cpu_ordered_reducer",
                side_effect=AssertionError("auto reducer must not probe Numba"),
            ) as probe_mock,
            patch.object(
                native_reducer,
                "reduce_ordered_summary_native_cpu",
                side_effect=AssertionError("auto reducer must remain Python"),
            ) as reduce_mock,
        ):
            actual = run_direct_ray_trace(
                _summary_two_bounce(31),
                intersection_dispatch="batch",
                intersection_batch_size=7,
                intersection_provider="python_cpu",
                wavefront_planner="python_cpu",
                wavefront_pipeline="soa_event_tape",
            )

        self.assertSemanticBitsAndOrderEqual(actual, reference)
        import_mock.assert_not_called()
        probe_mock.assert_not_called()
        reduce_mock.assert_not_called()
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(performance["requested_wavefront_reducer"], "auto")
        self.assertEqual(performance["wavefront_reducer"], "python_cpu")
        self.assertEqual(
            performance["wavefront_reducer_selection_reason"],
            "auto_python_no_probe",
        )
        self.assertEqual(performance["wavefront_reducer_native_attempt_count"], 0)
        json.dumps(actual.to_dict(), allow_nan=False)

        with self.assertRaisesRegex(ValueError, "wavefront_reducer"):
            run_direct_ray_trace(
                _summary_two_bounce(3),
                wavefront_reducer="cuda",
            )

    def test_concurrent_native_runs_keep_accumulators_and_metrics_isolated(self) -> None:
        self.requireNative()
        # Compile once outside the concurrent section; this test checks run
        # state isolation, not global JIT initialization serialization.
        _run_soa(_summary_two_bounce(1), chunk_size=1, reducer="numba_cpu")

        cases = (
            (lambda: _summary_two_bounce(61), 13),
            (lambda: _summary_stochastic(257), 29),
        )
        references = [
            _run_soa(factory(), chunk_size=chunk, reducer="python_cpu")
            for factory, chunk in cases
        ]

        def execute(case):
            factory, chunk = case
            return _run_soa(
                factory(),
                chunk_size=chunk,
                reducer="numba_cpu",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            actual = list(executor.map(execute, cases))

        for index, (result, reference) in enumerate(zip(actual, references)):
            with self.subTest(run=index):
                self.assertSemanticBitsAndOrderEqual(result, reference)
                self.assertEqual(
                    result.metrics["_performance_summary"][
                        "wavefront_reducer_logical_primary_count"
                    ],
                    result.total_rays,
                )
                self.assertEqual(
                    result.metrics["_performance_summary"][
                        "wavefront_reducer_logical_event_count"
                    ],
                    result.surface_hit_count,
                )
                self.assertNativeMetrics(result)


if __name__ == "__main__":
    unittest.main()
