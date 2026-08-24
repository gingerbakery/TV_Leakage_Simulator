from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import two_bounce_input
from test_perf3b2a_multibounce_wavefront import stochastic_two_bounce_input
from test_perf3b2c2_native_ordered_reducer import (
    _ordered_float_bits,
    _semantic_payload,
)

from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator import native_cpu_counter_wavefront as native_counter
from leakage_simulator import native_cpu_intersection as native_intersection
from leakage_simulator import native_cpu_ordered_reducer as native_reducer
from leakage_simulator.raytracer import (
    _build_receiver_frame,
    _find_first_receiver_hit,
    _find_first_receiver_hits_numeric,
    _wavefront_reflection_seed,
    _wavefront_reflection_seeds,
    run_direct_ray_trace,
)
from leakage_simulator.types import RayTraceConfig, ReceiverGrid, ReceiverSpec
from leakage_simulator.wavefront_event_tape import (
    PATH_PAYLOAD_FULL,
    PATH_PAYLOAD_OMITTED,
    PrimaryMajorEventTapeBuilder,
)


def _summary_two_bounce(ray_count: int, *, store_paths: bool = False):
    trace_input = two_bounce_input(
        max_depth=2,
        ray_count=ray_count,
        store_paths=store_paths,
    )
    trace_input.config.contribution_mode = "summary"
    if not store_paths:
        trace_input.config.max_stored_paths = 0
    return trace_input


def _summary_stochastic(ray_count: int, *, store_paths: bool = False):
    trace_input = stochastic_two_bounce_input(ray_count)
    trace_input.config.contribution_mode = "summary"
    trace_input.config.store_ray_paths = store_paths
    if not store_paths:
        trace_input.config.max_stored_paths = 0
    return trace_input


def _run_soa(
    trace_input,
    *,
    chunk_size: int,
    commit_policy: str,
    should_stop=None,
):
    return run_direct_ray_trace(
        trace_input,
        should_stop=should_stop,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="python_cpu",
        wavefront_planner="numba_cpu",
        wavefront_pipeline="soa_event_tape",
        wavefront_reducer="numba_cpu",
        wavefront_rng="counter_rng_v2",
        wavefront_reducer_commit=commit_policy,
    )


class Perf3DHostOverheadTests(unittest.TestCase):
    def require_native(self) -> None:
        capability = native_reducer.probe_native_cpu_ordered_reducer()
        if not capability.available:
            self.skipTest(capability.reason_code or "Numba unavailable")

    def assert_semantic_bits_and_order_equal(self, actual, expected) -> None:
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

    def test_vectorized_reflection_seeds_are_bit_exact_at_uint64_boundaries(
        self,
    ) -> None:
        cases = (
            (0, 0, 0),
            (0, 0, 1),
            (1, 7, 31),
            (-1, -3, 9),
            (2**64 - 1, 2**64 - 4, 11),
            (2**64 + 17, 2**64 + 23, 257),
        )
        for emitter_seed, primary_start, count in cases:
            with self.subTest(
                emitter_seed=emitter_seed,
                primary_start=primary_start,
                count=count,
            ):
                actual = _wavefront_reflection_seeds(
                    emitter_seed,
                    primary_start,
                    count,
                )
                expected = np.asarray(
                    [
                        _wavefront_reflection_seed(
                            emitter_seed,
                            primary_start + offset,
                        )
                        for offset in range(count)
                    ],
                    dtype=np.uint64,
                )
                self.assertEqual(actual.dtype, np.dtype(np.uint64))
                self.assertEqual(actual.shape, (count,))
                self.assertTrue(actual.flags.c_contiguous)
                self.assertTrue(actual.flags.owndata)
                np.testing.assert_array_equal(actual, expected)

    def test_vectorized_reflection_seed_count_validation_is_strict(self) -> None:
        for invalid in (True, False, -1, 1.5, "3"):
            with self.subTest(count=invalid):
                with self.assertRaises(ValueError):
                    _wavefront_reflection_seeds(1, 0, invalid)  # type: ignore[arg-type]

    def test_numeric_receiver_batch_matches_scalar_candidate_contract(self) -> None:
        receivers = [
            ReceiverSpec(
                receiver_id="far",
                center=(0.0, 0.0, 10.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=4.0,
                height_mm=4.0,
                resolution=(8, 6),
            ),
            ReceiverSpec(
                receiver_id="near",
                center=(0.0, 0.0, 5.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=2.0,
                height_mm=2.0,
                resolution=(4, 4),
            ),
        ]
        frames = [_build_receiver_frame(receiver) for receiver in receivers]
        grids = {
            receiver.receiver_id: ReceiverGrid.empty(receiver)
            for receiver in receivers
        }
        config = RayTraceConfig(epsilon_mm=1e-4)
        origins = np.asarray(
            [
                (0.0, 0.0, 0.0),
                (1.5, 0.0, 0.0),
                (3.0, 0.0, 0.0),
                (0.0, 0.0, 8.0),
                (-0.75, 0.75, 0.0),
            ],
            dtype=np.float64,
        )
        directions = np.asarray(
            [
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, -1.0),
                (0.1, -0.05, 1.0),
            ],
            dtype=np.float64,
        )
        powers = np.asarray((1.0, 0.5, 0.25, 0.75, 0.9), dtype=np.float64)
        input_snapshots = (
            origins.tobytes(),
            directions.tobytes(),
            powers.tobytes(),
        )

        actual = _find_first_receiver_hits_numeric(
            origins,
            directions,
            powers,
            frames,
            config,
        )
        expected = [
            _find_first_receiver_hit(
                tuple(origins[index]),
                tuple(directions[index]),
                float(powers[index]),
                -1,
                frames,
                grids,
                config,
            )
            for index in range(len(origins))
        ]

        self.assertEqual(
            input_snapshots,
            (origins.tobytes(), directions.tobytes(), powers.tobytes()),
        )
        for values in (
            actual.distances_mm,
            actual.receiver_indices,
            actual.rows,
            actual.columns,
            actual.received_power_lumen,
            actual.points,
        ):
            self.assertTrue(values.flags.c_contiguous)
            self.assertTrue(values.flags.owndata)

        for index, candidate in enumerate(expected):
            with self.subTest(ray=index):
                if candidate is None:
                    self.assertEqual(int(actual.receiver_indices[index]), -1)
                    self.assertTrue(np.isinf(actual.distances_mm[index]))
                    self.assertEqual(float(actual.received_power_lumen[index]), 0.0)
                    continue
                self.assertEqual(
                    int(actual.receiver_indices[index]),
                    next(
                        receiver_index
                        for receiver_index, receiver in enumerate(receivers)
                        if receiver.receiver_id == candidate.receiver_id
                    ),
                )
                self.assertEqual(int(actual.rows[index]), candidate.row)
                self.assertEqual(int(actual.columns[index]), candidate.column)
                self.assertEqual(
                    float(actual.distances_mm[index]),
                    candidate.distance_mm,
                )
                self.assertEqual(
                    float(actual.received_power_lumen[index]),
                    candidate.received_power_lumen,
                )
                self.assertEqual(
                    tuple(float(value) for value in actual.points[index]),
                    candidate.point,
                )

    def test_run_accumulator_is_bit_exact_across_counter_and_path_matrix(
        self,
    ) -> None:
        self.require_native()
        cases = (
            ("deterministic", lambda: _summary_two_bounce(41), 7),
            ("stochastic", lambda: _summary_stochastic(257), 29),
            (
                "path_quota",
                lambda: _summary_two_bounce(29, store_paths=True),
                7,
            ),
        )
        for name, factory, chunk_size in cases:
            with self.subTest(case=name):
                if name == "path_quota":
                    reference_input = factory()
                    reference_input.config.max_stored_paths = 2
                    actual_input = factory()
                    actual_input.config.max_stored_paths = 2
                else:
                    reference_input = factory()
                    actual_input = factory()
                reference = _run_soa(
                    reference_input,
                    chunk_size=chunk_size,
                    commit_policy="per_tape",
                )
                actual = _run_soa(
                    actual_input,
                    chunk_size=chunk_size,
                    commit_policy="run_accumulator",
                )

                self.assert_semantic_bits_and_order_equal(actual, reference)
                self.assertEqual(actual.receiver_grids, reference.receiver_grids)
                self.assertEqual(actual.stored_paths, reference.stored_paths)
                performance = actual.metrics["_performance_summary"]
                self.assertEqual(
                    performance["wavefront_reducer_commit_policy"],
                    "run_accumulator",
                )
                self.assertEqual(
                    performance["wavefront_reducer_retained_tape_count"],
                    performance["wavefront_chunk_count"],
                )
                self.assertEqual(
                    performance["wavefront_reducer_retained_primary_count"],
                    actual.total_rays,
                )
                self.assertEqual(
                    performance["wavefront_reducer_retained_event_count"],
                    actual.surface_hit_count,
                )
                self.assertEqual(
                    performance["wavefront_reducer_final_flush_count"],
                    1,
                )
                self.assertEqual(
                    performance["wavefront_reducer_fallback_flush_count"],
                    0,
                )
                self.assertEqual(
                    performance["wavefront_reflection_seed_dispatch"],
                    "numpy_splitmix64_batch_v1",
                )
                self.assertEqual(
                    performance["wavefront_receiver_dispatch"],
                    "numpy_numeric_batch_v2",
                )
                self.assertEqual(
                    performance["wavefront_reflection_rng"],
                    "counter_rng_v2",
                )
                self.assertEqual(
                    performance["wavefront_reducer_native_attempt_count"],
                    performance["wavefront_reducer_native_success_count"],
                )
                self.assertEqual(
                    performance["wavefront_reducer_fallback_count"],
                    0,
                )
                self.assertIs(type(performance["wavefront_reducer_final_flush_sec"]), float)
                self.assertGreaterEqual(
                    performance["wavefront_reducer_final_flush_sec"],
                    0.0,
                )
                json.dumps(actual.to_dict(), allow_nan=False)

    def test_retained_accumulator_clone_is_owned_mutable_and_disjoint(self) -> None:
        self.require_native()
        real_clone = native_reducer.clone_ordered_summary_accumulator
        clone_count = 0

        def inspect_clone(state):
            nonlocal clone_count
            clone_count += 1
            cloned = real_clone(state)
            for field in fields(state):
                source = getattr(state, field.name)
                target = getattr(cloned, field.name)
                if not isinstance(source, np.ndarray):
                    continue
                self.assertTrue(source.flags.owndata, field.name)
                self.assertFalse(source.flags.writeable, field.name)
                self.assertTrue(target.flags.c_contiguous, field.name)
                self.assertTrue(target.flags.owndata, field.name)
                self.assertTrue(target.flags.writeable, field.name)
                self.assertFalse(np.shares_memory(source, target), field.name)
                np.testing.assert_array_equal(target, source)
            return cloned

        with patch.object(
            native_reducer,
            "clone_ordered_summary_accumulator",
            side_effect=inspect_clone,
        ):
            result = _run_soa(
                _summary_two_bounce(41),
                chunk_size=7,
                commit_policy="run_accumulator",
            )
        performance = result.metrics["_performance_summary"]
        self.assertEqual(
            clone_count,
            performance["wavefront_reducer_retained_tape_count"] - 1,
        )

    def test_second_native_failure_flushes_prior_state_then_replays_once(self) -> None:
        self.require_native()
        ray_count = 23
        chunk_size = 7
        reference = _run_soa(
            _summary_two_bounce(ray_count),
            chunk_size=chunk_size,
            commit_policy="per_tape",
        )
        real_reduce = native_reducer.reduce_ordered_summary_native_cpu
        call_count = 0

        def fail_second(batch, state):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                state.optical_counts[0] += 999
                raise native_reducer.NativeCpuOrderedReducerProviderError(
                    "execute",
                    "injected_perf3d_second_tape_failure",
                )
            return real_reduce(batch, state)

        with patch.object(
            native_reducer,
            "reduce_ordered_summary_native_cpu",
            side_effect=fail_second,
        ):
            actual = _run_soa(
                _summary_two_bounce(ray_count),
                chunk_size=chunk_size,
                commit_policy="run_accumulator",
            )

        self.assert_semantic_bits_and_order_equal(actual, reference)
        self.assertEqual(call_count, 2)
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_reducer"], "mixed")
        self.assertEqual(performance["wavefront_reducer_native_attempt_count"], 2)
        self.assertEqual(performance["wavefront_reducer_native_success_count"], 1)
        self.assertEqual(performance["wavefront_reducer_fallback_count"], 1)
        self.assertEqual(
            performance["wavefront_reducer_fallback_reason"],
            "injected_perf3d_second_tape_failure",
        )
        self.assertEqual(performance["wavefront_reducer_retained_tape_count"], 1)
        self.assertEqual(performance["wavefront_reducer_final_flush_count"], 1)
        self.assertEqual(performance["wavefront_reducer_fallback_flush_count"], 1)
        self.assertEqual(
            performance["wavefront_reducer_native_success_primary_count"]
            + performance["wavefront_reducer_python_primary_count"],
            ray_count,
        )
        self.assertEqual(
            performance["wavefront_reducer_native_success_event_count"]
            + performance["wavefront_reducer_python_event_count"],
            actual.surface_hit_count,
        )

    def test_stop_flushes_one_complete_retained_chunk(self) -> None:
        self.require_native()

        def stopped_run(commit_policy: str):
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
                result = _run_soa(
                    trace_input,
                    chunk_size=16,
                    commit_policy=commit_policy,
                    should_stop=stop_event.is_set,
                )
            return result, calls

        reference, reference_calls = stopped_run("per_tape")
        actual, actual_calls = stopped_run("run_accumulator")
        self.assertEqual((reference_calls, actual_calls), (3, 3))
        self.assert_semantic_bits_and_order_equal(actual, reference)
        self.assertEqual(actual.total_rays, 16)
        performance = actual.metrics["_performance_summary"]
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["wavefront_chunk_count"], 1)
        self.assertEqual(performance["wavefront_reducer_retained_tape_count"], 1)
        self.assertEqual(performance["wavefront_reducer_final_flush_count"], 1)
        self.assertEqual(performance["wavefront_reducer_fallback_flush_count"], 0)

    def test_concurrent_run_accumulators_are_isolated(self) -> None:
        self.require_native()
        _run_soa(
            _summary_two_bounce(1),
            chunk_size=1,
            commit_policy="run_accumulator",
        )
        cases = (
            (lambda: _summary_two_bounce(61), 13),
            (lambda: _summary_stochastic(257), 29),
        )
        references = [
            _run_soa(
                factory(),
                chunk_size=chunk_size,
                commit_policy="per_tape",
            )
            for factory, chunk_size in cases
        ]

        def execute(case):
            factory, chunk_size = case
            return _run_soa(
                factory(),
                chunk_size=chunk_size,
                commit_policy="run_accumulator",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            actual = list(executor.map(execute, cases))

        for index, (result, reference) in enumerate(zip(actual, references)):
            with self.subTest(run=index):
                self.assert_semantic_bits_and_order_equal(result, reference)
                performance = result.metrics["_performance_summary"]
                self.assertEqual(
                    performance["wavefront_reducer_retained_primary_count"],
                    result.total_rays,
                )
                self.assertEqual(
                    performance["wavefront_reducer_retained_event_count"],
                    result.surface_hit_count,
                )
                self.assertEqual(
                    performance["wavefront_reducer_final_flush_count"],
                    1,
                )
                self.assertEqual(
                    performance["wavefront_reducer_fallback_flush_count"],
                    0,
                )

    def test_cpu_default_uses_native_parity_stack_without_cuda_probe(self) -> None:
        trace_input = _summary_two_bounce(17)
        with patch.object(
            gpu_cuda,
            "probe_gpu_cuda",
            side_effect=AssertionError("CPU default must not probe CUDA"),
        ) as gpu_probe:
            result = run_direct_ray_trace(trace_input)

        gpu_probe.assert_not_called()
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["compute_backend"], "cpu")
        self.assertEqual(
            performance["wavefront_reducer_commit_policy"],
            "run_accumulator",
        )
        self.assertEqual(
            performance["wavefront_reflection_rng"],
            "counter_rng_v2",
        )
        self.assertEqual(
            performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )

    def test_gpu_auto_selects_run_accumulator_without_forcing_gpu_in_test(self) -> None:
        self.require_native()
        trace_input = _summary_two_bounce(41)
        trace_input.config.compute_backend = "gpu_cuda"
        result = run_direct_ray_trace(
            trace_input,
            intersection_provider="python_cpu",
        )
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["compute_backend"], "gpu_cuda")
        self.assertEqual(
            performance["wavefront_reducer_commit_policy"],
            "run_accumulator",
        )
        self.assertGreater(
            performance["wavefront_reducer_retained_tape_count"],
            0,
        )
        self.assertEqual(performance["wavefront_reducer_final_flush_count"], 1)
        self.assertEqual(
            performance["wavefront_reflection_seed_dispatch"],
            "numpy_splitmix64_batch_v1",
        )
        self.assertEqual(
            performance["wavefront_receiver_dispatch"],
            "numpy_numeric_batch_v2",
        )

    def test_path_payload_suppression_is_monotonic_and_preserves_dead_end_gate(
        self,
    ) -> None:
        self.require_native()

        def execute(ray_count: int):
            trace_input = _summary_stochastic(ray_count, store_paths=True)
            trace_input.config.max_stored_paths = 1
            payloads: list[str] = []
            real_seal = PrimaryMajorEventTapeBuilder.seal

            def record_seal(builder):
                tape = real_seal(builder)
                payloads.append(tape.path_payload)
                return tape

            with patch.object(
                PrimaryMajorEventTapeBuilder,
                "seal",
                new=record_seal,
            ):
                result = _run_soa(
                    trace_input,
                    chunk_size=17,
                    commit_policy="run_accumulator",
                )
            return result, payloads

        dead_end_result, dead_end_payloads = execute(97)
        self.assertTrue(dead_end_payloads)
        self.assertEqual(set(dead_end_payloads), {PATH_PAYLOAD_FULL})
        self.assertEqual(dead_end_result.stored_paths[0][-1].event_type, "surface")
        dead_end_performance = dead_end_result.metrics["_performance_summary"]
        self.assertEqual(
            dead_end_performance[
                "wavefront_event_tape_path_payload_suppressed_chunk_count"
            ],
            0,
        )

        reference_input = _summary_stochastic(257, store_paths=True)
        reference_input.config.max_stored_paths = 1
        reference = _run_soa(
            reference_input,
            chunk_size=17,
            commit_policy="per_tape",
        )
        actual, payloads = execute(257)
        self.assert_semantic_bits_and_order_equal(actual, reference)
        self.assertEqual(actual.stored_paths, reference.stored_paths)
        self.assertEqual(actual.stored_paths[0][-1].event_type, "receiver")
        first_omitted = payloads.index(PATH_PAYLOAD_OMITTED)
        self.assertGreater(first_omitted, 0)
        self.assertEqual(
            payloads[:first_omitted],
            [PATH_PAYLOAD_FULL] * first_omitted,
        )
        self.assertEqual(
            payloads[first_omitted:],
            [PATH_PAYLOAD_OMITTED] * (len(payloads) - first_omitted),
        )
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(
            performance["wavefront_event_tape_path_payload"],
            "mixed_v1",
        )
        self.assertEqual(
            performance["wavefront_event_tape_path_payload_requested"],
            PATH_PAYLOAD_FULL,
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_full_chunk_count"
            ],
            first_omitted,
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_omitted_chunk_count"
            ],
            len(payloads) - first_omitted,
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_suppressed_chunk_count"
            ],
            len(payloads) - first_omitted,
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_full_primary_count"
            ]
            + performance[
                "wavefront_event_tape_path_payload_omitted_primary_count"
            ],
            actual.total_rays,
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_full_event_count"
            ]
            + performance[
                "wavefront_event_tape_path_payload_omitted_event_count"
            ],
            actual.surface_hit_count,
        )


if __name__ == "__main__":
    unittest.main()
