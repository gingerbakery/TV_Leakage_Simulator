from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_perf3b2a_multibounce_wavefront import (
    semantic_payload,
    stochastic_two_bounce_input,
)

from leakage_simulator.native_cpu_counter_wavefront import (
    CONTRACT_VERSION,
    RNG_ALGORITHM,
    CounterWavefrontPlanInput,
    NativeCpuCounterWavefrontProviderError,
    counter_uniform,
    plan_counter_native_cpu,
    plan_counter_reference,
    probe_native_cpu_counter_wavefront,
)
from leakage_simulator.native_cpu_wavefront import (
    SCATTER_GAUSSIAN,
    SCATTER_LAMBERTIAN,
    SCATTER_MIXED,
    SCATTER_NONE,
    SCATTER_SPECULAR,
    TERMINATION_RUSSIAN_ROULETTE,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def _counter_batch(row_count: int = 513) -> CounterWavefrontPlanInput:
    generator = np.random.default_rng(90210)
    directions = generator.normal(size=(row_count, 3))
    directions[:, 2] = -np.maximum(np.abs(directions[:, 2]), 0.05)
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    scatter_pattern = np.asarray(
        [
            SCATTER_NONE,
            SCATTER_SPECULAR,
            SCATTER_LAMBERTIAN,
            SCATTER_GAUSSIAN,
            SCATTER_MIXED,
        ],
        dtype=np.int8,
    )
    return CounterWavefrontPlanInput(
        directions,
        np.tile(np.asarray([0.0, 0.0, 1.0]), (row_count, 1)),
        generator.uniform(0.0005, 0.02, row_count),
        generator.uniform(0.2, 0.95, row_count),
        generator.uniform(0.0, 0.8, row_count),
        np.resize(scatter_pattern, row_count),
        generator.uniform(0.1, 0.9, row_count),
        generator.uniform(2.0, 35.0, row_count),
        np.arange(1000, 1000 + row_count, dtype=np.uint64),
        depth=2,
        max_depth=10,
        min_energy=0.005,
        termination_mode=TERMINATION_RUSSIAN_ROULETTE,
    )


class Perf3CCounterWavefrontPlannerTests(unittest.TestCase):
    def test_counter_uniform_has_stable_golden_values_and_semantic_lanes(self) -> None:
        self.assertEqual(CONTRACT_VERSION, "counter_rng_v2")
        self.assertEqual(RNG_ALGORITHM, "splitmix64_semantic_lane_v1")
        values = [counter_uniform(42, 3, lane) for lane in range(5)]
        self.assertEqual(
            values,
            [
                0.016977973414202263,
                0.1266563695488261,
                0.6345498919963009,
                0.2981262591383902,
                0.5207256389010231,
            ],
        )
        self.assertEqual(values, [counter_uniform(42, 3, lane) for lane in range(5)])
        self.assertEqual(len(set(values)), len(values))

    def test_input_and_result_are_owned_readonly_contiguous_and_disjoint(self) -> None:
        source = _counter_batch(17)
        input_arrays = (
            source.incoming_directions,
            source.surface_normals,
            source.incoming_power_lumen,
            source.profile_reflectance,
            source.profile_roughness,
            source.scatter_models,
            source.profile_specular_ratio,
            source.profile_gaussian_sigma_deg,
            source.rng_keys,
        )
        result = plan_counter_reference(source)
        output_arrays = (
            result.supported_mask,
            result.reflected_power_lumen,
            result.emitted_power_lumen,
            result.emitted_directions,
            result.status_flags,
            result.lobe_codes,
            result.rng_draw_counts,
        )
        for values in (*input_arrays, *output_arrays):
            self.assertTrue(values.flags.owndata)
            self.assertTrue(values.flags.c_contiguous)
            self.assertFalse(values.flags.writeable)
        for arrays in (input_arrays, output_arrays):
            for index, first in enumerate(arrays):
                for second in arrays[index + 1 :]:
                    self.assertFalse(np.shares_memory(first, second))

    def test_native_matches_reference_and_is_row_order_independent(self) -> None:
        capability = probe_native_cpu_counter_wavefront()
        if not capability.available:
            self.skipTest(capability.reason_code or "numba unavailable")
        batch = _counter_batch()
        reference = plan_counter_reference(batch)
        native = plan_counter_native_cpu(batch).result
        for field in (
            "supported_mask",
            "status_flags",
            "lobe_codes",
            "rng_draw_counts",
        ):
            self.assertTrue(
                np.array_equal(getattr(reference, field), getattr(native, field)),
                field,
            )
        for field in (
            "reflected_power_lumen",
            "emitted_power_lumen",
            "emitted_directions",
        ):
            self.assertTrue(
                np.array_equal(getattr(reference, field), getattr(native, field)),
                field,
            )

        permutation = np.random.default_rng(7).permutation(len(batch))
        permuted = CounterWavefrontPlanInput(
            batch.incoming_directions[permutation],
            batch.surface_normals[permutation],
            batch.incoming_power_lumen[permutation],
            batch.profile_reflectance[permutation],
            batch.profile_roughness[permutation],
            batch.scatter_models[permutation],
            batch.profile_specular_ratio[permutation],
            batch.profile_gaussian_sigma_deg[permutation],
            batch.rng_keys[permutation],
            depth=batch.depth,
            max_depth=batch.max_depth,
            min_energy=batch.min_energy,
            termination_mode=batch.termination_mode,
        )
        permuted_result = plan_counter_native_cpu(permuted).result
        inverse = np.argsort(permutation)
        for field in (
            "status_flags",
            "lobe_codes",
            "rng_draw_counts",
            "reflected_power_lumen",
            "emitted_power_lumen",
            "emitted_directions",
        ):
            self.assertTrue(
                np.array_equal(
                    getattr(native, field),
                    getattr(permuted_result, field)[inverse],
                ),
                field,
            )

    def test_raytracer_counter_contract_is_exact_across_pipeline_chunk_and_provider(self) -> None:
        records = []
        for planner in ("python_cpu", "numba_cpu"):
            for pipeline in ("object_reference", "soa_event_tape"):
                for chunk_size in (7, 64):
                    with self.subTest(
                        planner=planner,
                        pipeline=pipeline,
                        chunk_size=chunk_size,
                    ):
                        result = run_direct_ray_trace(
                            stochastic_two_bounce_input(257),
                            intersection_dispatch="batch",
                            intersection_batch_size=chunk_size,
                            intersection_provider="python_cpu",
                            wavefront_planner=planner,
                            wavefront_pipeline=pipeline,
                            wavefront_reducer="python_cpu",
                            wavefront_rng="counter_rng_v2",
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
                        self.assertEqual(
                            performance["wavefront_planner_rng_algorithm"],
                            RNG_ALGORITHM,
                        )
                        if pipeline == "soa_event_tape":
                            self.assertEqual(
                                performance["wavefront_counter_apply_dispatch"],
                                "numpy_vectorized_v1",
                            )
                        self.assertEqual(
                            performance["wavefront_planner_fallback_count"],
                            0,
                        )
                        records.append(semantic_payload(result))
        self.assertTrue(all(payload == records[0] for payload in records[1:]))

    def test_vectorized_counter_apply_is_exact_for_summary_detailed_and_paths(self) -> None:
        for contribution_mode in ("summary", "detailed"):
            for store_paths in (False, True):
                with self.subTest(
                    contribution_mode=contribution_mode,
                    store_paths=store_paths,
                ):
                    def build_input():
                        trace_input = stochastic_two_bounce_input(257)
                        trace_input.config.contribution_mode = contribution_mode
                        trace_input.config.store_ray_paths = store_paths
                        trace_input.config.max_stored_paths = 19 if store_paths else 0
                        return trace_input

                    reference = run_direct_ray_trace(
                        build_input(),
                        intersection_dispatch="batch",
                        intersection_batch_size=29,
                        intersection_provider="python_cpu",
                        wavefront_planner="python_cpu",
                        wavefront_pipeline="soa_event_tape",
                        wavefront_reducer="python_cpu",
                        wavefront_rng="counter_rng_v2",
                    )
                    native = run_direct_ray_trace(
                        build_input(),
                        intersection_dispatch="batch",
                        intersection_batch_size=64,
                        intersection_provider="python_cpu",
                        wavefront_planner="numba_cpu",
                        wavefront_pipeline="soa_event_tape",
                        wavefront_reducer="python_cpu",
                        wavefront_rng="counter_rng_v2",
                    )
                    self.assertEqual(
                        semantic_payload(reference),
                        semantic_payload(native),
                    )
                    self.assertEqual(
                        native.metrics["_performance_summary"][
                            "wavefront_counter_apply_dispatch"
                        ],
                        "numpy_vectorized_v1",
                    )

    def test_counter_chunk_8192_and_65536_are_exact_with_path_quota(self) -> None:
        payloads = []
        for chunk_size in (8192, 65536):
            trace_input = stochastic_two_bounce_input(20_001)
            trace_input.config.store_ray_paths = True
            trace_input.config.max_stored_paths = 19
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="batch",
                intersection_batch_size=chunk_size,
                intersection_provider="python_cpu",
                wavefront_planner="numba_cpu",
                wavefront_pipeline="soa_event_tape",
                wavefront_reducer="numba_cpu",
                wavefront_rng="counter_rng_v2",
            )
            payloads.append(semantic_payload(result))
            performance = result.metrics["_performance_summary"]
            self.assertEqual(performance["wavefront_planner_fallback_count"], 0)
            self.assertEqual(
                performance["wavefront_counter_apply_dispatch"],
                "numpy_vectorized_v1",
            )
        self.assertEqual(payloads[0], payloads[1])

    def test_native_failure_replays_whole_counter_batch_once_and_opens_circuit(self) -> None:
        expected = run_direct_ray_trace(
            stochastic_two_bounce_input(257),
            intersection_dispatch="batch",
            intersection_batch_size=64,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
            wavefront_pipeline="soa_event_tape",
            wavefront_reducer="python_cpu",
            wavefront_rng="counter_rng_v2",
        )
        with patch(
            "leakage_simulator.raytracer.plan_counter_native_cpu",
            side_effect=NativeCpuCounterWavefrontProviderError(
                "execute",
                "injected_counter_failure",
            ),
        ) as native_mock:
            actual = run_direct_ray_trace(
                stochastic_two_bounce_input(257),
                intersection_dispatch="batch",
                intersection_batch_size=64,
                intersection_provider="python_cpu",
                wavefront_planner="numba_cpu",
                wavefront_pipeline="soa_event_tape",
                wavefront_reducer="python_cpu",
                wavefront_rng="counter_rng_v2",
            )
        self.assertEqual(semantic_payload(expected), semantic_payload(actual))
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(native_mock.call_count, 1)
        self.assertEqual(performance["wavefront_planner_fallback_count"], 1)
        self.assertEqual(
            performance["wavefront_planner_fallback_phase"],
            "execute",
        )
        self.assertEqual(
            performance["wavefront_planner_python_sidecar_row_count"],
            performance["wavefront_planner_logical_row_count"],
        )

    def test_face_table_prepare_failure_replays_counter_rows_without_ending_run(self) -> None:
        expected = run_direct_ray_trace(
            stochastic_two_bounce_input(257),
            intersection_dispatch="batch",
            intersection_batch_size=64,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
            wavefront_pipeline="soa_event_tape",
            wavefront_reducer="python_cpu",
            wavefront_rng="counter_rng_v2",
        )
        with patch(
            "leakage_simulator.raytracer._WavefrontPlannerStats.prepare_counter_face_tables",
            return_value=None,
        ):
            actual = run_direct_ray_trace(
                stochastic_two_bounce_input(257),
                intersection_dispatch="batch",
                intersection_batch_size=64,
                intersection_provider="python_cpu",
                wavefront_planner="numba_cpu",
                wavefront_pipeline="soa_event_tape",
                wavefront_reducer="python_cpu",
                wavefront_rng="counter_rng_v2",
            )
        self.assertEqual(semantic_payload(expected), semantic_payload(actual))
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_planner_fallback_count"], 1)
        self.assertEqual(
            performance["wavefront_planner_fallback_phase"],
            "input_prepare",
        )
        self.assertEqual(
            performance["wavefront_planner_python_sidecar_row_count"],
            performance["wavefront_planner_logical_row_count"],
        )

    def test_cpu_default_enters_counter_parity_path(self) -> None:
        result = run_direct_ray_trace(stochastic_two_bounce_input(257))
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["compute_backend"], "cpu")
        self.assertEqual(
            performance["wavefront_reflection_rng"],
            "counter_rng_v2",
        )
        self.assertEqual(performance["wavefront_pipeline"], "soa_event_tape")
        self.assertEqual(
            performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )
        json.dumps(result.to_dict(), allow_nan=False)

    def test_gpu_config_promotes_full_batch_policy_while_concrete_kwargs_win(self) -> None:
        trace_input = stochastic_two_bounce_input(257)
        trace_input.config.compute_backend = "gpu_cuda"
        # Use a CPU intersection override so this selection test is independent
        # of the CI machine's GPU. All other auto knobs must be promoted.
        result = run_direct_ray_trace(
            trace_input,
            intersection_provider="python_cpu",
        )
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["compute_backend"], "gpu_cuda")
        self.assertEqual(performance["requested_intersection_dispatch"], "auto")
        self.assertEqual(
            performance["effective_intersection_dispatch_request"],
            "batch",
        )
        self.assertEqual(performance["intersection_batch_size"], 65536)
        self.assertEqual(performance["wavefront_pipeline"], "soa_event_tape")
        self.assertEqual(performance["requested_wavefront_planner"], "auto")
        self.assertEqual(
            performance["effective_wavefront_planner_request"],
            "numba_cpu",
        )
        self.assertEqual(performance["requested_wavefront_reducer"], "auto")
        self.assertEqual(
            performance["effective_wavefront_reducer_request"],
            "numba_cpu",
        )
        self.assertEqual(performance["wavefront_reflection_rng"], "counter_rng_v2")

        overridden = run_direct_ray_trace(
            trace_input,
            intersection_dispatch="batch",
            intersection_batch_size=31,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
            wavefront_pipeline="object_reference",
            wavefront_reducer="python_cpu",
            wavefront_rng="per_primary_seeded_v1",
        )
        override_performance = overridden.metrics["_performance_summary"]
        self.assertEqual(override_performance["intersection_batch_size"], 31)
        self.assertEqual(
            override_performance["wavefront_pipeline"],
            "object_reference",
        )
        self.assertEqual(
            override_performance["wavefront_reflection_rng"],
            "per_primary_seeded_v1",
        )


if __name__ == "__main__":
    unittest.main()
