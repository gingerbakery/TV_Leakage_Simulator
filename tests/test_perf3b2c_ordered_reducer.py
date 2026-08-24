from __future__ import annotations

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
from test_perf3b2a_multibounce_wavefront import (
    reference_native_provider,
    stochastic_two_bounce_input,
)

from leakage_simulator.raytracer import run_direct_ray_trace


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _ordered_float_bits(value):
    """Preserve dict insertion order and compare every public float by bits."""
    if isinstance(value, dict):
        return tuple(
            (key, _ordered_float_bits(item))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return tuple(_ordered_float_bits(item) for item in value)
    if isinstance(value, (float, np.floating)):
        return ("float64", struct.pack(">d", float(value)).hex())
    return value


def _run_wavefront(trace_input, *, pipeline: str, chunk_size: int = 64, **kwargs):
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="python_cpu",
        wavefront_planner="python_cpu",
        wavefront_pipeline=pipeline,
        **kwargs,
    )


class Perf3B2COrderedReducerTests(unittest.TestCase):
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

    def assertSoAMetrics(
        self,
        result,
        *,
        event_count: int,
        path_payload: str | None = None,
    ) -> None:
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_pipeline"], "soa_event_tape")
        self.assertEqual(
            performance["wavefront_state_layout"],
            "stable_active_soa_v1",
        )
        self.assertEqual(
            performance["wavefront_event_tape_contract"],
            "ordered_primary_event_tape_v3",
        )
        self.assertEqual(
            performance["wavefront_event_tape_validation_mode"],
            "strict_v1",
        )
        self.assertEqual(
            performance["wavefront_event_tape_copy_contract"],
            "builder_owned_materialization_v1",
        )
        effective_path_payload = performance[
            "wavefront_event_tape_path_payload"
        ]
        if path_payload is not None:
            self.assertEqual(effective_path_payload, path_payload)
        else:
            self.assertIn(effective_path_payload, {"full_path_v1", "mixed_v1"})
            self.assertEqual(
                performance["wavefront_event_tape_path_payload_requested"],
                "full_path_v1",
            )
        full_chunks = performance[
            "wavefront_event_tape_path_payload_full_chunk_count"
        ]
        omitted_chunks = performance[
            "wavefront_event_tape_path_payload_omitted_chunk_count"
        ]
        self.assertEqual(
            full_chunks + omitted_chunks,
            performance["wavefront_chunk_count"],
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_full_primary_count"
            ]
            + performance[
                "wavefront_event_tape_path_payload_omitted_primary_count"
            ],
            performance["wavefront_primary_ray_count"],
        )
        self.assertEqual(
            performance[
                "wavefront_event_tape_path_payload_full_event_count"
            ]
            + performance[
                "wavefront_event_tape_path_payload_omitted_event_count"
            ],
            event_count,
        )
        if effective_path_payload == "mixed_v1":
            self.assertGreater(full_chunks, 0)
            self.assertGreater(omitted_chunks, 0)
            self.assertEqual(
                performance[
                    "wavefront_event_tape_path_payload_suppressed_chunk_count"
                ],
                omitted_chunks,
            )
        self.assertEqual(
            performance["wavefront_event_tape_peak_scope"],
            "tape_owned_ndarray_estimate_v2",
        )
        self.assertEqual(
            performance["wavefront_reducer_contract"],
            "python_ordered_v1",
        )
        self.assertEqual(performance["wavefront_event_count"], event_count)
        self.assertEqual(
            performance["wavefront_reducer_logical_event_count"],
            event_count,
        )
        self.assertIs(type(performance["wavefront_event_count"]), int)
        self.assertIs(
            type(performance["wavefront_event_tape_peak_bytes"]),
            int,
        )
        self.assertGreater(performance["wavefront_event_tape_peak_bytes"], 0)
        self.assertIs(type(performance["wavefront_event_tape_copy_bytes"]), int)
        self.assertGreater(performance["wavefront_event_tape_copy_bytes"], 0)
        timing_fields = (
            "wavefront_state_init_sec",
            "wavefront_state_advance_sec",
            "wavefront_event_tape_append_sec",
            "wavefront_event_tape_seal_sec",
            "wavefront_event_tape_validation_sec",
            "wavefront_reducer_replay_sec",
            "wavefront_reducer_hydrate_sec",
        )
        for key in timing_fields:
            value = performance[key]
            self.assertIs(type(value), float, key)
            self.assertTrue(math.isfinite(value), key)
            self.assertGreaterEqual(value, 0.0, key)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_deterministic_depth_two_is_bit_exact_for_all_chunk_boundaries(
        self,
    ) -> None:
        ray_count = 73
        reference = _run_wavefront(
            two_bounce_input(max_depth=2, ray_count=ray_count),
            pipeline="object_reference",
            chunk_size=64,
        )
        reference_performance = reference.metrics["_performance_summary"]
        self.assertEqual(
            reference_performance["wavefront_pipeline"],
            "object_reference",
        )
        self.assertEqual(
            reference_performance["wavefront_event_tape_contract"],
            "not_used",
        )
        self.assertEqual(reference_performance["wavefront_event_count"], 0)

        for chunk_size in (1, 7, 64, 1024, 4096):
            with self.subTest(chunk_size=chunk_size):
                actual = _run_wavefront(
                    two_bounce_input(max_depth=2, ray_count=ray_count),
                    pipeline="soa_event_tape",
                    chunk_size=chunk_size,
                )
                self.assertSemanticBitsAndOrderEqual(actual, reference)
                self.assertEqual(actual.receiver_grids, reference.receiver_grids)
                self.assertEqual(actual.stored_paths, reference.stored_paths)
                self.assertEqual(
                    actual.contribution_summary.to_dict(),
                    reference.contribution_summary.to_dict(),
                )
                self.assertSoAMetrics(actual, event_count=ray_count * 2)

    def test_depth_ten_summary_and_detailed_replay_preserve_bits_and_key_order(
        self,
    ) -> None:
        for contribution_mode in ("summary", "detailed"):
            with self.subTest(contribution_mode=contribution_mode):
                reference_input = ten_bounce_corridor_input(max_depth=10)
                reference_input.config.contribution_mode = contribution_mode
                actual_input = ten_bounce_corridor_input(max_depth=10)
                actual_input.config.contribution_mode = contribution_mode
                reference = _run_wavefront(
                    reference_input,
                    pipeline="object_reference",
                    chunk_size=17,
                )
                actual = _run_wavefront(
                    actual_input,
                    pipeline="soa_event_tape",
                    chunk_size=17,
                )

                self.assertSemanticBitsAndOrderEqual(actual, reference)
                self.assertSoAMetrics(
                    actual,
                    event_count=1000,
                    path_payload="omitted_v1",
                )
                contribution = actual.contribution_summary.to_dict()
                if contribution_mode == "summary":
                    self.assertEqual(list(contribution["faces"]), [])
                    self.assertEqual(list(contribution["components"]), [])
                    self.assertEqual(list(contribution["materials"]), [])
                    self.assertEqual(
                        list(contribution["depths"]),
                        [str(depth) for depth in range(1, 11)],
                    )
                else:
                    self.assertEqual(
                        list(contribution["faces"]),
                        ["1", "2", "3", "0"],
                    )
                    self.assertEqual(
                        list(contribution["components"]),
                        ["301", "302"],
                    )
                    self.assertEqual(
                        list(contribution["materials"]),
                        ["high_reflector"],
                    )
                    self.assertEqual(
                        list(contribution["depths"]),
                        [str(depth) for depth in range(11)],
                    )

    def test_mixed_gaussian_roulette_is_exact_across_chunks_and_provider(
        self,
    ) -> None:
        ray_count = 257
        reference = _run_wavefront(
            stochastic_two_bounce_input(ray_count),
            pipeline="object_reference",
            chunk_size=64,
        )
        expected_event_count = reference.surface_hit_count
        self.assertEqual(expected_event_count, 334)
        self.assertGreater(reference.receiver_hit_count, 0)
        reflection = reference.metrics["_reflection_summary"]
        self.assertGreater(reflection["roulette_survived_count"], 0)
        self.assertGreater(reflection["roulette_terminated_count"], 0)
        self.assertGreater(
            reflection["lobes"]["lambertian"]["emitted_count"],
            0,
        )
        self.assertGreater(
            reflection["lobes"]["gaussian"]["emitted_count"],
            0,
        )

        for chunk_size in (1, 7, 64, 1024):
            with self.subTest(chunk_size=chunk_size, provider="python_cpu"):
                actual = _run_wavefront(
                    stochastic_two_bounce_input(ray_count),
                    pipeline="soa_event_tape",
                    chunk_size=chunk_size,
                )
                self.assertSemanticBitsAndOrderEqual(actual, reference)
                self.assertSoAMetrics(actual, event_count=expected_event_count)
                self.assertEqual(
                    actual.metrics["_performance_summary"][
                        "wavefront_reflection_rng"
                    ],
                    "per_primary_seeded_v1",
                )

        native_input = stochastic_two_bounce_input(ray_count)
        with patch.object(
            native_input.mesh,
            "intersect_rays_native_cpu",
            side_effect=reference_native_provider(native_input.mesh),
        ) as native_mock:
            native = run_direct_ray_trace(
                native_input,
                intersection_dispatch="batch",
                intersection_batch_size=64,
                intersection_provider="numba_cpu",
                wavefront_planner="python_cpu",
                wavefront_pipeline="soa_event_tape",
            )
        self.assertSemanticBitsAndOrderEqual(native, reference)
        self.assertSoAMetrics(native, event_count=expected_event_count)
        native_performance = native.metrics["_performance_summary"]
        self.assertEqual(native_performance["intersection_provider"], "numba_cpu")
        self.assertEqual(
            native_mock.call_count,
            native_performance["intersection_batch_count"],
        )

    def test_empty_partial_and_full_path_quotas_preserve_ordered_selection(
        self,
    ) -> None:
        cases = (
            (False, 12),
            (True, 0),
            (True, 1),
            (True, 2),
            (True, 12),
        )
        for store_paths, quota in cases:
            with self.subTest(store_paths=store_paths, quota=quota):
                reference_input = two_bounce_input(
                    max_depth=2,
                    ray_count=29,
                    store_paths=store_paths,
                )
                reference_input.config.max_stored_paths = quota
                actual_input = two_bounce_input(
                    max_depth=2,
                    ray_count=29,
                    store_paths=store_paths,
                )
                actual_input.config.max_stored_paths = quota
                reference = _run_wavefront(
                    reference_input,
                    pipeline="object_reference",
                    chunk_size=7,
                )
                actual = _run_wavefront(
                    actual_input,
                    pipeline="soa_event_tape",
                    chunk_size=7,
                )

                self.assertSemanticBitsAndOrderEqual(actual, reference)
                self.assertEqual(actual.stored_paths, reference.stored_paths)
                self.assertEqual(
                    len(actual.stored_paths),
                    min(29, quota) if store_paths else 0,
                )
                performance = actual.metrics["_performance_summary"]
                if store_paths and quota > 0:
                    self.assertEqual(
                        performance["wavefront_path_materialized_count"],
                        min(29, quota),
                    )
                    self.assertEqual(
                        performance[
                            "wavefront_path_materialization_skipped_count"
                        ],
                        29 - min(29, quota),
                    )
                else:
                    self.assertEqual(
                        performance["wavefront_path_materialized_count"],
                        0,
                    )
                    self.assertEqual(
                        performance[
                            "wavefront_path_materialization_skipped_count"
                        ],
                        0,
                    )

    def test_stop_commits_one_primary_chunk_atomically_for_both_reducers(
        self,
    ) -> None:
        def stopped_run(pipeline: str):
            trace_input = two_bounce_input(max_depth=2, ray_count=48)
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
                )
            return result, calls

        reference, reference_calls = stopped_run("object_reference")
        actual, actual_calls = stopped_run("soa_event_tape")
        self.assertEqual(reference_calls, 3)
        self.assertEqual(actual_calls, 3)
        self.assertSemanticBitsAndOrderEqual(actual, reference)
        self.assertEqual(actual.total_rays, 16)
        self.assertEqual(actual.receiver_hit_count, 16)
        self.assertEqual(actual.surface_hit_count, 32)
        performance = actual.metrics["_performance_summary"]
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["wavefront_chunk_count"], 1)
        self.assertSoAMetrics(actual, event_count=32)

    def test_explicit_scalar_does_not_probe_or_build_event_tape(self) -> None:
        reference = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=31),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )
        trace_input = two_bounce_input(max_depth=2, ray_count=31)
        with (
            patch(
                "leakage_simulator.native_cpu_intersection.probe_native_cpu",
                side_effect=AssertionError("default auto must not probe Numba"),
            ) as intersection_probe,
            patch(
                "leakage_simulator.native_cpu_wavefront.probe_native_cpu_wavefront",
                side_effect=AssertionError("default auto must not probe planner"),
            ) as planner_probe,
            patch(
                "leakage_simulator.raytracer.plan_deterministic_native_cpu",
                side_effect=AssertionError("default auto must not call planner"),
            ) as planner_call,
            patch(
                "leakage_simulator.raytracer._trace_multi_bounce_wavefront_soa_batch",
                side_effect=AssertionError("scalar path must not build an event tape"),
            ) as soa_call,
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=AssertionError("default auto must not call native batch"),
            ) as native_batch,
            patch.object(
                trace_input.mesh,
                "intersect_ray_native_cpu",
                side_effect=AssertionError("default auto must not call native scalar"),
            ) as native_scalar,
        ):
            actual = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="scalar",
                intersection_provider="python_cpu",
            )

        self.assertSemanticBitsAndOrderEqual(actual, reference)
        intersection_probe.assert_not_called()
        planner_probe.assert_not_called()
        planner_call.assert_not_called()
        soa_call.assert_not_called()
        native_batch.assert_not_called()
        native_scalar.assert_not_called()
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(performance["requested_wavefront_pipeline"], "auto")
        self.assertEqual(performance["wavefront_pipeline"], "not_used")
        self.assertEqual(performance["wavefront_state_layout"], "not_used")
        self.assertEqual(performance["wavefront_event_tape_contract"], "not_used")
        self.assertEqual(
            performance["wavefront_event_tape_validation_mode"],
            "not_used",
        )
        self.assertEqual(performance["wavefront_event_tape_validation_sec"], 0.0)
        self.assertEqual(performance["wavefront_event_tape_copy_bytes"], 0)
        self.assertEqual(
            performance["wavefront_event_tape_copy_contract"],
            "not_used",
        )
        self.assertEqual(
            performance["wavefront_event_tape_path_payload"],
            "not_used",
        )
        self.assertEqual(
            performance["wavefront_event_tape_peak_scope"],
            "not_used",
        )
        self.assertEqual(performance["wavefront_event_count"], 0)
        self.assertEqual(performance["wavefront_event_tape_peak_bytes"], 0)
        self.assertEqual(performance["wavefront_reducer_contract"], "not_used")
        self.assertEqual(performance["wavefront_reducer_logical_event_count"], 0)
        json.dumps(actual.to_dict(), allow_nan=False)

    def test_auto_batch_keeps_reference_pipeline_and_invalid_value_is_rejected(
        self,
    ) -> None:
        automatic = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=17),
            intersection_dispatch="batch",
            intersection_batch_size=17,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
        )
        performance = automatic.metrics["_performance_summary"]
        self.assertEqual(performance["requested_wavefront_pipeline"], "auto")
        self.assertEqual(performance["wavefront_pipeline"], "object_reference")
        self.assertEqual(
            performance["wavefront_state_layout"],
            "python_object_graph_v1",
        )
        self.assertEqual(
            performance["wavefront_event_tape_contract"],
            "not_used",
        )
        self.assertEqual(
            performance["wavefront_event_tape_validation_mode"],
            "not_used",
        )
        self.assertEqual(performance["wavefront_event_tape_copy_bytes"], 0)
        self.assertEqual(
            performance["wavefront_event_tape_path_payload"],
            "not_used",
        )
        self.assertEqual(performance["wavefront_event_count"], 0)
        self.assertEqual(
            performance["wavefront_reducer_contract"],
            "python_object_commit_v1",
        )

        with self.assertRaisesRegex(ValueError, "wavefront_pipeline"):
            run_direct_ray_trace(
                two_bounce_input(max_depth=2, ray_count=3),
                wavefront_pipeline="cuda",
            )


if __name__ == "__main__":
    unittest.main()
