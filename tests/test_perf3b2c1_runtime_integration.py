from __future__ import annotations

import json
import math
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import two_bounce_input

from leakage_simulator.native_cpu_intersection import (
    NativeCpuExecution,
    NativeCpuProviderError,
)
from leakage_simulator.native_cpu_wavefront import (
    NativeCpuWavefrontProviderError,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _ordered_float_bits(value):
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


def _assert_semantic_bits_and_order(
    testcase: unittest.TestCase,
    actual,
    expected,
) -> None:
    actual_payload = _semantic_payload(actual)
    expected_payload = _semantic_payload(expected)
    testcase.assertEqual(
        _ordered_float_bits(actual_payload),
        _ordered_float_bits(expected_payload),
    )
    testcase.assertEqual(
        json.dumps(actual_payload, allow_nan=False, separators=(",", ":")),
        json.dumps(expected_payload, allow_nan=False, separators=(",", ":")),
    )


def _run_object(trace_input, *, chunk_size: int):
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="python_cpu",
        wavefront_planner="python_cpu",
        wavefront_pipeline="object_reference",
    )


def _run_soa(trace_input, *, chunk_size: int, **kwargs):
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider=kwargs.pop("intersection_provider", "python_cpu"),
        wavefront_planner=kwargs.pop("wavefront_planner", "python_cpu"),
        wavefront_pipeline="soa_event_tape",
        **kwargs,
    )


class Perf3B2C1RuntimeIntegrationTests(unittest.TestCase):
    def assertStrictV2Metrics(
        self,
        result,
        *,
        path_payload: str,
        path_payload_requested: str | None = None,
    ) -> None:
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_pipeline"], "soa_event_tape")
        self.assertEqual(
            performance["wavefront_event_tape_contract"],
            "ordered_primary_event_tape_v2",
        )
        self.assertEqual(
            performance["wavefront_event_tape_validation_mode"],
            "strict_v1",
        )
        self.assertEqual(
            performance["wavefront_event_tape_copy_contract"],
            "builder_owned_materialization_v1",
        )
        self.assertEqual(
            performance["wavefront_event_tape_path_payload"],
            path_payload,
        )
        if path_payload_requested is None:
            path_payload_requested = path_payload
        self.assertEqual(
            performance["wavefront_event_tape_path_payload_requested"],
            path_payload_requested,
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
            performance["wavefront_event_count"],
        )
        self.assertEqual(
            performance["wavefront_event_tape_peak_scope"],
            "tape_owned_ndarray_estimate_v2",
        )
        self.assertIs(
            type(performance["wavefront_event_tape_validation_sec"]),
            float,
        )
        self.assertTrue(
            math.isfinite(performance["wavefront_event_tape_validation_sec"])
        )
        self.assertGreaterEqual(
            performance["wavefront_event_tape_validation_sec"],
            0.0,
        )
        self.assertIs(type(performance["wavefront_event_tape_copy_bytes"]), int)
        self.assertGreater(performance["wavefront_event_tape_copy_bytes"], 0)
        self.assertIs(
            type(performance["wavefront_event_tape_peak_bytes"]),
            int,
        )
        self.assertGreater(performance["wavefront_event_tape_peak_bytes"], 0)
        json.dumps(result.to_dict(), allow_nan=False)

    def test_optional_path_payload_modes_preserve_exact_runtime_semantics(self) -> None:
        ray_count = 29
        chunk_size = 7
        cases = (
            (False, 12, "omitted_v1", "omitted_v1", 0),
            (True, 0, "omitted_v1", "omitted_v1", 0),
            (True, 2, "mixed_v1", "full_path_v1", 2),
        )
        omitted_memory = []
        path_enabled_memory = None
        for (
            store_paths,
            quota,
            expected_payload,
            expected_requested_payload,
            expected_paths,
        ) in cases:
            with self.subTest(store_paths=store_paths, quota=quota):
                reference_input = two_bounce_input(
                    max_depth=2,
                    ray_count=ray_count,
                    store_paths=store_paths,
                )
                reference_input.config.max_stored_paths = quota
                actual_input = two_bounce_input(
                    max_depth=2,
                    ray_count=ray_count,
                    store_paths=store_paths,
                )
                actual_input.config.max_stored_paths = quota
                reference = _run_object(reference_input, chunk_size=chunk_size)

                if expected_payload == "omitted_v1":
                    materialize_patch = patch(
                        "leakage_simulator.raytracer._materialize_stored_path_from_tape",
                        side_effect=AssertionError(
                            "omitted payload must not be hydrated"
                        ),
                    )
                else:
                    from leakage_simulator import raytracer

                    materialize_patch = patch(
                        "leakage_simulator.raytracer._materialize_stored_path_from_tape",
                        wraps=raytracer._materialize_stored_path_from_tape,
                    )
                with materialize_patch as materialize_mock:
                    actual = _run_soa(actual_input, chunk_size=chunk_size)

                _assert_semantic_bits_and_order(self, actual, reference)
                self.assertEqual(actual.receiver_grids, reference.receiver_grids)
                self.assertEqual(actual.contribution_summary, reference.contribution_summary)
                self.assertEqual(actual.stored_paths, reference.stored_paths)
                self.assertEqual(len(actual.stored_paths), expected_paths)
                self.assertStrictV2Metrics(
                    actual,
                    path_payload=expected_payload,
                    path_payload_requested=expected_requested_payload,
                )
                performance = actual.metrics["_performance_summary"]
                if expected_payload == "omitted_v1":
                    materialize_mock.assert_not_called()
                    self.assertEqual(
                        performance[
                            "wavefront_event_tape_path_payload_full_chunk_count"
                        ],
                        0,
                    )
                    self.assertEqual(
                        performance[
                            "wavefront_event_tape_path_payload_suppressed_chunk_count"
                        ],
                        0,
                    )
                    omitted_memory.append(
                        (
                            performance["wavefront_event_tape_copy_bytes"],
                            performance["wavefront_event_tape_peak_bytes"],
                        )
                    )
                else:
                    self.assertEqual(materialize_mock.call_count, expected_paths)
                    self.assertEqual(
                        performance[
                            "wavefront_event_tape_path_payload_full_chunk_count"
                        ],
                        1,
                    )
                    self.assertEqual(
                        performance[
                            "wavefront_event_tape_path_payload_omitted_chunk_count"
                        ],
                        4,
                    )
                    self.assertEqual(
                        performance[
                            "wavefront_event_tape_path_payload_suppressed_chunk_count"
                        ],
                        4,
                    )
                    path_enabled_memory = (
                        performance["wavefront_event_tape_copy_bytes"],
                        performance["wavefront_event_tape_peak_bytes"],
                    )

        self.assertEqual(omitted_memory[0], omitted_memory[1])
        self.assertIsNotNone(path_enabled_memory)
        assert path_enabled_memory is not None
        self.assertLess(omitted_memory[0][0], path_enabled_memory[0])
        self.assertLess(omitted_memory[0][1], path_enabled_memory[1])

    def test_mid_depth_intersection_failure_keeps_one_logical_query(self) -> None:
        ray_count = 23
        reference_input = two_bounce_input(
            max_depth=2,
            ray_count=ray_count,
            store_paths=False,
        )
        reference = _run_object(reference_input, chunk_size=ray_count)
        trace_input = two_bounce_input(
            max_depth=2,
            ray_count=ray_count,
            store_paths=False,
        )
        reference_intersect = trace_input.mesh.intersect_rays
        native_call_count = 0
        failed_rays = None

        def fail_on_second_depth(rays, backend=None):
            nonlocal native_call_count, failed_rays
            native_call_count += 1
            if native_call_count == 2:
                failed_rays = rays
                raise NativeCpuProviderError(
                    "execute",
                    "injected_perf3b2c1_mid_depth_failure",
                )
            hits = reference_intersect(rays, backend=backend)
            return (
                hits,
                NativeCpuExecution(
                    distances=np.array(hits.t, dtype=np.float64, copy=True),
                    face_indices=np.array(
                        hits.face_indices,
                        dtype=np.int64,
                        copy=True,
                    ),
                    scene_build_sec=0.0,
                    jit_compile_sec=0.0,
                    execute_sec=0.0,
                    numba_version="test",
                ),
            )

        with (
            patch.object(
                trace_input.mesh,
                "intersect_rays_native_cpu",
                side_effect=fail_on_second_depth,
            ) as native_mock,
            patch.object(
                trace_input.mesh,
                "intersect_rays",
                wraps=reference_intersect,
            ) as python_mock,
        ):
            actual = _run_soa(
                trace_input,
                chunk_size=ray_count,
                intersection_provider="numba_cpu",
            )

        _assert_semantic_bits_and_order(self, actual, reference)
        native_mock.assert_called()
        self.assertEqual(native_mock.call_count, 2)
        self.assertEqual(python_mock.call_count, 2)
        self.assertIsNotNone(failed_rays)
        self.assertIs(python_mock.call_args_list[0].args[0], failed_rays)
        performance = actual.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_provider"], "mixed")
        self.assertTrue(performance["native_provider_disabled"])
        self.assertEqual(performance["native_attempt_count"], 2)
        self.assertEqual(performance["native_success_count"], 1)
        self.assertEqual(performance["intersection_fallback_count"], 1)
        self.assertEqual(performance["intersection_fallback_ray_count"], ray_count)
        self.assertEqual(performance["intersection_fallback_phase"], "execute")
        self.assertEqual(
            performance["intersection_fallback_reason"],
            "injected_perf3b2c1_mid_depth_failure",
        )
        self.assertEqual(performance["intersection_ray_count"], ray_count * 3)
        self.assertEqual(
            performance["native_success_ray_count"]
            + sum(len(call.args[0]) for call in python_mock.call_args_list),
            performance["intersection_ray_count"],
        )
        self.assertStrictV2Metrics(actual, path_payload="omitted_v1")

    def test_native_planner_failure_keeps_one_python_sidecar_row_per_event(self) -> None:
        ray_count = 23
        reference = _run_object(
            two_bounce_input(
                max_depth=2,
                ray_count=ray_count,
                store_paths=False,
            ),
            chunk_size=ray_count,
        )
        with patch(
            "leakage_simulator.raytracer.plan_deterministic_native_cpu",
            side_effect=NativeCpuWavefrontProviderError(
                "execute",
                "injected_perf3b2c1_planner_failure",
            ),
        ) as native_planner:
            actual = _run_soa(
                two_bounce_input(
                    max_depth=2,
                    ray_count=ray_count,
                    store_paths=False,
                ),
                chunk_size=ray_count,
                wavefront_planner="numba_cpu",
            )

        _assert_semantic_bits_and_order(self, actual, reference)
        native_planner.assert_called_once()
        performance = actual.metrics["_performance_summary"]
        logical_rows = ray_count * 2
        self.assertEqual(performance["wavefront_planner"], "python_cpu")
        self.assertTrue(performance["wavefront_planner_native_provider_disabled"])
        self.assertEqual(performance["wavefront_planner_logical_row_count"], logical_rows)
        self.assertEqual(
            performance["wavefront_planner_python_sidecar_row_count"],
            logical_rows,
        )
        self.assertEqual(performance["wavefront_planner_native_attempt_count"], 1)
        self.assertEqual(
            performance["wavefront_planner_native_attempt_row_count"],
            ray_count,
        )
        self.assertEqual(performance["wavefront_planner_native_success_row_count"], 0)
        self.assertEqual(performance["wavefront_planner_fallback_count"], 1)
        self.assertEqual(performance["wavefront_planner_fallback_row_count"], ray_count)
        self.assertEqual(performance["wavefront_planner_fallback_phase"], "execute")
        self.assertEqual(
            performance["wavefront_planner_fallback_reason"],
            "injected_perf3b2c1_planner_failure",
        )
        self.assertEqual(
            performance["wavefront_planner_native_success_row_count"]
            + performance["wavefront_planner_python_sidecar_row_count"],
            performance["wavefront_planner_logical_row_count"],
        )
        self.assertStrictV2Metrics(actual, path_payload="omitted_v1")

    def test_default_scalar_and_batch_auto_do_not_probe_or_build_tape(self) -> None:
        with (
            patch(
                "leakage_simulator.native_cpu_intersection.probe_native_cpu",
                side_effect=AssertionError("default scalar must not probe Numba"),
            ) as intersection_probe,
            patch(
                "leakage_simulator.native_cpu_wavefront.probe_native_cpu_wavefront",
                side_effect=AssertionError("default scalar must not probe planner"),
            ) as planner_probe,
            patch(
                "leakage_simulator.raytracer.PrimaryMajorEventTapeBuilder",
                side_effect=AssertionError("non-SoA paths must not build a tape"),
            ) as tape_builder,
        ):
            scalar = run_direct_ray_trace(
                two_bounce_input(
                    max_depth=2,
                    ray_count=17,
                    store_paths=False,
                )
            )
            automatic_batch = run_direct_ray_trace(
                two_bounce_input(
                    max_depth=2,
                    ray_count=17,
                    store_paths=False,
                ),
                intersection_dispatch="batch",
                intersection_batch_size=17,
                intersection_provider="python_cpu",
                wavefront_planner="python_cpu",
            )

        intersection_probe.assert_not_called()
        planner_probe.assert_not_called()
        tape_builder.assert_not_called()
        scalar_performance = scalar.metrics["_performance_summary"]
        batch_performance = automatic_batch.metrics["_performance_summary"]
        self.assertEqual(scalar_performance["wavefront_pipeline"], "not_used")
        self.assertEqual(batch_performance["wavefront_pipeline"], "object_reference")
        for performance in (scalar_performance, batch_performance):
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
        json.dumps(scalar.to_dict(), allow_nan=False)
        json.dumps(automatic_batch.to_dict(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
