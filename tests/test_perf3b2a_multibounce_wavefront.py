from __future__ import annotations

import json
import math
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

from leakage_simulator.native_cpu_intersection import (
    NativeCpuExecution,
    NativeCpuProviderError,
)
from leakage_simulator.raytracer import (
    _build_receiver_frame,
    _find_first_receiver_hit,
    _find_first_receiver_hits_batch,
    run_direct_ray_trace,
)
from leakage_simulator.types import (
    OpticalProfile,
    RayTraceConfig,
    ReceiverGrid,
    ReceiverSpec,
)


def semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def stochastic_two_bounce_input(ray_count: int = 257):
    trace_input = two_bounce_input(
        max_depth=2,
        ray_count=ray_count,
        min_energy=0.005,
        termination_mode="russian_roulette",
        store_paths=True,
    )
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
    trace_input.config.max_stored_paths = 19
    return trace_input


def reference_native_provider(mesh):
    reference_intersect = mesh.intersect_rays

    def execute(rays, backend=None):
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

    return execute


class Perf3B2AMultiBounceWavefrontTests(unittest.TestCase):
    def test_vector_receiver_query_preserves_scalar_boundary_rules(self) -> None:
        receiver = ReceiverSpec(
            receiver_id="boundary_receiver",
            center=(0.0, 0.0, 1.0),
            normal=(0.0, 0.0, -1.0),
            width_mm=2.0,
            height_mm=2.0,
            resolution=(4, 4),
            acceptance_angle_deg=90.0,
        )
        frame = _build_receiver_frame(receiver)
        config = RayTraceConfig(epsilon_mm=1e-4)
        origins = np.asarray(
            [
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0 + 1e-12, 0.0, 0.0),
                (0.0, 0.0, 1.0 - config.epsilon_mm),
                (0.0, 0.0, 1.0 - config.epsilon_mm - 1e-12),
                (-1e12, 0.0, 0.0),
                (-1e12, 0.0, 0.0),
            ],
            dtype=np.float64,
        )
        directions = np.asarray(
            [
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (1.0, 0.0, 1e-12),
                (1.0, 0.0, np.nextafter(1e-12, 0.0)),
            ],
            dtype=np.float64,
        )
        powers = np.asarray([0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75])
        scalar_grid = ReceiverGrid.empty(receiver)
        scalar_grids = {receiver.receiver_id: scalar_grid}
        expected = [
            _find_first_receiver_hit(
                tuple(origin),
                tuple(direction),
                float(power),
                -1,
                [frame],
                scalar_grids,
                config,
                depth=3,
                ray_kind="specular",
            )
            for origin, direction, power in zip(origins, directions, powers)
        ]

        batch_grid = ReceiverGrid.empty(receiver)
        actual, maximum_t = _find_first_receiver_hits_batch(
            origins,
            directions,
            powers,
            [frame],
            {receiver.receiver_id: batch_grid},
            config,
            3,
            ["specular"] * len(origins),
        )

        self.assertEqual([item is not None for item in expected], [
            True,
            True,
            False,
            False,
            True,
            True,
            False,
        ])
        for index, (scalar_candidate, batch_candidate) in enumerate(
            zip(expected, actual)
        ):
            with self.subTest(index=index):
                if scalar_candidate is None:
                    self.assertIsNone(batch_candidate)
                    self.assertTrue(math.isinf(maximum_t[index]))
                    continue
                self.assertIsNotNone(batch_candidate)
                assert batch_candidate is not None
                self.assertEqual(
                    scalar_candidate.to_ray_hit().to_dict(),
                    batch_candidate.to_ray_hit().to_dict(),
                )
                self.assertEqual(maximum_t[index], scalar_candidate.distance_mm)

    def test_specular_depth_two_chunks_match_scalar_with_paths_and_contributions(
        self,
    ) -> None:
        ray_count = 73
        scalar = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=ray_count),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )
        expected_payload = semantic_payload(scalar)

        last_batch = None
        for chunk_size in (1, 7, 64, 4096):
            with self.subTest(chunk_size=chunk_size):
                batch = run_direct_ray_trace(
                    two_bounce_input(max_depth=2, ray_count=ray_count),
                    intersection_dispatch="batch",
                    intersection_batch_size=chunk_size,
                    intersection_provider="python_cpu",
                )
                self.assertEqual(expected_payload, semantic_payload(batch))

                performance = batch.metrics["_performance_summary"]
                chunk_count = math.ceil(ray_count / chunk_size)
                self.assertEqual(performance["execution_path"], "multi_bounce_wavefront")
                self.assertTrue(performance["multi_bounce_wavefront_used"])
                self.assertEqual(performance["intersection_dispatch"], "batch")
                self.assertEqual(performance["wavefront_chunk_count"], chunk_count)
                self.assertEqual(performance["wavefront_primary_ray_count"], ray_count)
                self.assertEqual(
                    performance["wavefront_depth_batch_count"],
                    chunk_count * 3,
                )
                self.assertEqual(performance["intersection_batch_count"], chunk_count * 3)
                self.assertEqual(performance["intersection_ray_count"], ray_count * 3)
                self.assertEqual(
                    performance["wavefront_active_ray_count_by_depth"],
                    {"0": ray_count, "1": ray_count, "2": ray_count},
                )
                self.assertEqual(performance["wavefront_max_observed_depth"], 2)
                self.assertEqual(performance["wavefront_compacted_ray_count"], ray_count)
                self.assertEqual(
                    performance["wavefront_path_materialized_count"],
                    12,
                )
                self.assertEqual(
                    performance[
                        "wavefront_path_materialization_skipped_count"
                    ],
                    ray_count - 12,
                )
                last_batch = batch

        assert last_batch is not None
        self.assertEqual(last_batch.receiver_grids, scalar.receiver_grids)
        self.assertEqual(last_batch.stored_paths, scalar.stored_paths)
        self.assertEqual(len(last_batch.stored_paths), 12)
        self.assertEqual(
            [event.event_type for event in last_batch.stored_paths[0]],
            ["emitter", "surface", "surface", "receiver"],
        )
        self.assertEqual(
            last_batch.contribution_summary.to_dict(),
            scalar.contribution_summary.to_dict(),
        )
        self.assertEqual(
            last_batch.metrics["_reflection_summary"],
            scalar.metrics["_reflection_summary"],
        )

    def test_depth_ten_corridor_matches_scalar_exactly(self) -> None:
        scalar = run_direct_ray_trace(
            ten_bounce_corridor_input(max_depth=10),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )
        wavefront = run_direct_ray_trace(
            ten_bounce_corridor_input(max_depth=10),
            intersection_dispatch="batch",
            intersection_batch_size=17,
            intersection_provider="python_cpu",
        )

        self.assertEqual(semantic_payload(scalar), semantic_payload(wavefront))
        self.assertEqual(wavefront.receiver_hit_count, 100)
        self.assertAlmostEqual(
            wavefront.metrics["corridor_observer"]["total_flux_lumen"],
            (0.95**10) / math.sqrt(2.0),
            places=6,
        )
        performance = wavefront.metrics["_performance_summary"]
        chunk_count = math.ceil(100 / 17)
        self.assertEqual(performance["wavefront_max_observed_depth"], 10)
        self.assertEqual(performance["wavefront_depth_batch_count"], chunk_count * 11)
        self.assertEqual(performance["intersection_ray_count"], 100 * 11)
        self.assertEqual(
            performance["wavefront_active_ray_count_by_depth"],
            {str(depth): 100 for depth in range(11)},
        )

    def test_explicit_multibounce_batch_uses_measured_1024_default(self) -> None:
        result = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=1100, store_paths=False),
            intersection_dispatch="batch",
            intersection_provider="python_cpu",
        )

        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_batch_size"], 1024)
        self.assertEqual(performance["wavefront_chunk_count"], 2)
        self.assertEqual(performance["intersection_batch_max_size"], 1024)
        self.assertEqual(performance["wavefront_depth_batch_count"], 6)
        self.assertEqual(performance["intersection_ray_count"], 3300)

    def test_stochastic_wavefront_is_reproducible_across_chunks_and_provider(
        self,
    ) -> None:
        expected_payload = None
        reference_result = None
        for chunk_size in (1, 7, 64, 4096):
            with self.subTest(chunk_size=chunk_size):
                result = run_direct_ray_trace(
                    stochastic_two_bounce_input(),
                    intersection_dispatch="batch",
                    intersection_batch_size=chunk_size,
                    intersection_provider="python_cpu",
                )
                payload = semantic_payload(result)
                if expected_payload is None:
                    expected_payload = payload
                    reference_result = result
                else:
                    self.assertEqual(expected_payload, payload)

                performance = result.metrics["_performance_summary"]
                self.assertEqual(
                    performance["wavefront_reflection_rng"],
                    "per_primary_seeded_v1",
                )

        assert reference_result is not None and expected_payload is not None
        reflection = reference_result.metrics["_reflection_summary"]
        self.assertGreater(reflection["roulette_survived_count"], 0)
        self.assertGreater(reflection["roulette_terminated_count"], 0)
        self.assertGreater(reflection["lobes"]["lambertian"]["emitted_count"], 0)
        self.assertGreater(reflection["lobes"]["gaussian"]["emitted_count"], 0)

        native_input = stochastic_two_bounce_input()
        with patch.object(
            native_input.mesh,
            "intersect_rays_native_cpu",
            side_effect=reference_native_provider(native_input.mesh),
        ) as native_mock:
            native_result = run_direct_ray_trace(
                native_input,
                intersection_dispatch="batch",
                intersection_batch_size=64,
                intersection_provider="numba_cpu",
            )

        self.assertEqual(expected_payload, semantic_payload(native_result))
        performance = native_result.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_provider"], "numba_cpu")
        self.assertTrue(performance["native_used"])
        self.assertEqual(
            performance["native_success_count"],
            performance["intersection_batch_count"],
        )
        self.assertEqual(performance["reference_batch_count"], 0)
        self.assertEqual(performance["intersection_fallback_count"], 0)
        self.assertEqual(native_mock.call_count, performance["intersection_batch_count"])

    def test_default_auto_multibounce_uses_cpu_gpu_parity_contract(self) -> None:
        reference = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=41),
            intersection_dispatch="batch",
            intersection_batch_size=41,
            intersection_provider="python_cpu",
            wavefront_planner="python_cpu",
            wavefront_pipeline="soa_event_tape",
            wavefront_reducer="python_cpu",
            wavefront_rng="counter_rng_v2",
            wavefront_reducer_commit="run_accumulator",
        )
        trace_input = two_bounce_input(max_depth=2, ray_count=41)

        result = run_direct_ray_trace(trace_input)

        self.assertEqual(semantic_payload(reference), semantic_payload(result))
        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["requested_intersection_dispatch"], "auto")
        self.assertEqual(
            performance["effective_intersection_dispatch_request"],
            "batch",
        )
        self.assertEqual(performance["intersection_dispatch"], "batch")
        self.assertEqual(performance["execution_path"], "multi_bounce_wavefront")
        self.assertTrue(performance["multi_bounce_wavefront_used"])
        self.assertEqual(performance["requested_intersection_provider"], "auto")
        self.assertEqual(
            performance["effective_intersection_provider_request"],
            "numba_cpu",
        )
        self.assertIn(
            performance["intersection_provider"],
            {"numba_cpu", "python_cpu", "mixed"},
        )
        self.assertEqual(performance["wavefront_pipeline"], "soa_event_tape")
        self.assertEqual(performance["wavefront_reflection_rng"], "counter_rng_v2")
        self.assertEqual(
            performance["monte_carlo_contract"],
            "cpu_gpu_deterministic_batch_v1",
        )

    def test_stop_during_depth_query_commits_started_primary_chunk_atomically(
        self,
    ) -> None:
        trace_input = two_bounce_input(max_depth=2, ray_count=48)
        stop_event = threading.Event()
        progress = []
        original_intersect = trace_input.mesh.intersect_rays
        call_count = 0

        def stop_during_second_depth(rays, backend=None):
            nonlocal call_count
            call_count += 1
            hits = original_intersect(rays, backend=backend)
            if call_count == 2:
                stop_event.set()
            return hits

        with patch.object(
            trace_input.mesh,
            "intersect_rays",
            side_effect=stop_during_second_depth,
        ):
            result = run_direct_ray_trace(
                trace_input,
                progress_callback=lambda completed, requested: progress.append(
                    (completed, requested)
                ),
                should_stop=stop_event.is_set,
                intersection_dispatch="batch",
                intersection_batch_size=16,
                intersection_provider="python_cpu",
            )

        performance = result.metrics["_performance_summary"]
        self.assertEqual(call_count, 3)
        self.assertEqual(result.total_rays, 16)
        self.assertEqual(result.receiver_hit_count, 16)
        self.assertEqual(result.surface_hit_count, 32)
        self.assertEqual(result.terminated_ray_count, 0)
        self.assertEqual(len(result.stored_paths), 12)
        self.assertTrue(
            all(path[-1].event_type == "receiver" for path in result.stored_paths)
        )
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["wavefront_chunk_count"], 1)
        self.assertEqual(performance["wavefront_depth_batch_count"], 3)
        self.assertEqual(performance["intersection_batch_count"], 3)
        self.assertEqual(performance["intersection_ray_count"], 48)
        self.assertEqual(
            performance["wavefront_active_ray_count_by_depth"],
            {"0": 16, "1": 16, "2": 16},
        )
        self.assertEqual(result.contribution_summary.reflected_receiver_hit_count, 16)
        self.assertEqual(progress[-1], (16, 48))
        self.assertEqual(progress, sorted(progress, key=lambda item: item[0]))

    def test_mid_depth_native_failure_replays_one_logical_batch_and_serializes_metrics(
        self,
    ) -> None:
        ray_count = 23
        reference = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=ray_count),
            intersection_dispatch="batch",
            intersection_batch_size=ray_count,
            intersection_provider="python_cpu",
        )
        trace_input = two_bounce_input(max_depth=2, ray_count=ray_count)
        original_reference = trace_input.mesh.intersect_rays
        native_call_count = 0
        failed_rays = None

        def fail_on_second_depth(rays, backend=None):
            nonlocal native_call_count, failed_rays
            native_call_count += 1
            if native_call_count == 2:
                failed_rays = rays
                raise NativeCpuProviderError(
                    "execute",
                    "injected_mid_depth_failure",
                )
            hits = original_reference(rays, backend=backend)
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
                wraps=original_reference,
            ) as reference_mock,
        ):
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="batch",
                intersection_batch_size=ray_count,
                intersection_provider="numba_cpu",
            )

        self.assertEqual(semantic_payload(reference), semantic_payload(result))
        self.assertEqual(native_mock.call_count, 2)
        self.assertEqual(reference_mock.call_count, 2)
        self.assertIsNotNone(failed_rays)
        self.assertIs(reference_mock.call_args_list[0].args[0], failed_rays)

        performance = result.metrics["_performance_summary"]
        self.assertEqual(performance["intersection_provider"], "mixed")
        self.assertTrue(performance["native_provider_disabled"])
        self.assertEqual(performance["native_attempt_count"], 2)
        self.assertEqual(performance["native_attempt_ray_count"], ray_count * 2)
        self.assertEqual(performance["native_success_count"], 1)
        self.assertEqual(performance["native_success_ray_count"], ray_count)
        self.assertEqual(performance["intersection_fallback_count"], 1)
        self.assertEqual(performance["intersection_fallback_ray_count"], ray_count)
        self.assertEqual(performance["intersection_fallback_phase"], "execute")
        self.assertEqual(
            performance["intersection_fallback_reason"],
            "injected_mid_depth_failure",
        )
        self.assertEqual(performance["intersection_batch_count"], 3)
        self.assertEqual(performance["intersection_ray_count"], ray_count * 3)
        self.assertEqual(
            performance["native_success_ray_count"]
            + sum(len(call.args[0]) for call in reference_mock.call_args_list),
            performance["intersection_ray_count"],
        )

        encoded = json.dumps(result.to_dict(), allow_nan=False)
        restored = json.loads(encoded)
        restored_performance = restored["metrics"]["_performance_summary"]
        self.assertEqual(restored_performance["wavefront_max_observed_depth"], 2)
        self.assertEqual(restored_performance["intersection_ray_count"], ray_count * 3)


if __name__ == "__main__":
    unittest.main()
