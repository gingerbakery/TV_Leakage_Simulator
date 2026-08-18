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

from test_contribution_rt2d import direct_input, reflected_input
from test_multibounce_rt3 import two_bounce_input
from test_raytracer_rt1 import build_emitter_plane

from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec


def semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def stored_reflection_case(ray_count: int, with_blocker: bool = False):
    trace_input = reflected_input(ray_count=ray_count, with_blocker=with_blocker)
    trace_input.config.store_ray_paths = True
    trace_input.config.max_stored_paths = 19
    return trace_input


def face_emitter_case(ray_count: int = 41) -> DirectRayTraceInput:
    mesh = build_emitter_plane()
    emitter = EmitterSpec(
        emitter_id="face_source",
        face_indices=[0, 1],
        direction_distribution="gaussian",
        gaussian_sigma_deg=2.0,
        power_lumen=1.0,
        ray_count=ray_count,
        seed=7,
    )
    receiver = ReceiverSpec(
        receiver_id="front_receiver",
        center=(0.0, 0.0, 20.0),
        normal=(0.0, 0.0, -1.0),
        width_mm=80.0,
        height_mm=80.0,
        resolution=(8, 8),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[OpticalProfile(profile_id="default", reflectance=0.08)],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=0,
            seed=11,
            store_ray_paths=True,
            max_stored_paths=8,
        ),
    )


def mixed_emitter_case() -> DirectRayTraceInput:
    trace_input = stored_reflection_case(64, with_blocker=True)
    trace_input.emitters.append(
        EmitterSpec(
            emitter_id="face_source",
            face_indices=[0, 1],
            direction_distribution="gaussian",
            gaussian_sigma_deg=2.0,
            power_lumen=0.2,
            ray_count=11,
            seed=19,
        )
    )
    return trace_input


class Perf3B1BatchRayTracerTests(unittest.TestCase):
    def test_default_auto_keeps_cpu_scalar_until_native_backend_exists(self) -> None:
        default_result = run_direct_ray_trace(stored_reflection_case(127, True))
        scalar_result = run_direct_ray_trace(
            stored_reflection_case(127, True),
            intersection_dispatch="scalar",
        )

        self.assertEqual(
            semantic_payload(default_result),
            semantic_payload(scalar_result),
        )
        performance = default_result.metrics["_performance_summary"]
        self.assertEqual(performance["requested_intersection_dispatch"], "auto")
        self.assertEqual(performance["intersection_dispatch"], "scalar")

    def test_batch_dispatch_matches_scalar_for_receiver_and_blocker(self) -> None:
        for with_blocker in (False, True):
            with self.subTest(with_blocker=with_blocker):
                scalar = run_direct_ray_trace(
                    stored_reflection_case(257, with_blocker),
                    intersection_dispatch="scalar",
                )
                batch = run_direct_ray_trace(
                    stored_reflection_case(257, with_blocker),
                    intersection_dispatch="batch",
                    intersection_batch_size=17,
                )

                self.assertEqual(semantic_payload(scalar), semantic_payload(batch))
                self.assertEqual(
                    batch.metrics["_performance_summary"]["intersection_dispatch"],
                    "batch",
                )

    def test_intersection_chunk_size_preserves_semantics_and_primary_ray_stream(self) -> None:
        expected_payload = None
        expected_origins = None
        expected_directions = None
        for chunk_size in (1, 7, 64, 1024):
            trace_input = stored_reflection_case(257, with_blocker=True)
            captured_origins = []
            captured_directions = []
            original_intersect_rays = trace_input.mesh.intersect_rays

            def capture_primary(rays, backend=None):
                if np.all(rays.ignore_faces == -1):
                    captured_origins.append(rays.origins.copy())
                    captured_directions.append(rays.directions.copy())
                return original_intersect_rays(rays, backend=backend)

            trace_input.mesh.intersect_rays = capture_primary
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch="batch",
                intersection_batch_size=chunk_size,
            )
            payload = semantic_payload(result)
            origins = np.concatenate(captured_origins)
            directions = np.concatenate(captured_directions)

            if expected_payload is None:
                expected_payload = payload
                expected_origins = origins
                expected_directions = directions
            else:
                self.assertEqual(expected_payload, payload)
                np.testing.assert_array_equal(expected_origins, origins)
                np.testing.assert_array_equal(expected_directions, directions)

    def test_batch_preserves_russian_roulette_rng_order(self) -> None:
        def roulette_case():
            trace_input = stored_reflection_case(257, with_blocker=True)
            trace_input.config.min_energy = 0.003
            trace_input.config.termination_mode = "russian_roulette"
            return trace_input

        scalar = run_direct_ray_trace(
            roulette_case(),
            intersection_dispatch="scalar",
        )
        batch = run_direct_ray_trace(
            roulette_case(),
            intersection_dispatch="batch",
            intersection_batch_size=23,
        )

        self.assertEqual(semantic_payload(scalar), semantic_payload(batch))
        summary = batch.metrics["_reflection_summary"]
        self.assertGreater(summary["roulette_survived_count"], 0)
        self.assertGreater(summary["roulette_terminated_count"], 0)

    def test_unsupported_emitters_and_multibounce_stay_scalar(self) -> None:
        face_scalar = run_direct_ray_trace(
            face_emitter_case(),
            intersection_dispatch="scalar",
        )
        face_requested_batch = run_direct_ray_trace(
            face_emitter_case(),
            intersection_dispatch="batch",
            intersection_batch_size=7,
        )
        self.assertEqual(
            semantic_payload(face_scalar),
            semantic_payload(face_requested_batch),
        )
        self.assertEqual(
            face_requested_batch.metrics["_performance_summary"]["intersection_dispatch"],
            "scalar",
        )

        multibounce = run_direct_ray_trace(
            two_bounce_input(max_depth=2, ray_count=73),
            intersection_dispatch="batch",
            intersection_batch_size=7,
        )
        self.assertEqual(
            multibounce.metrics["_performance_summary"]["intersection_dispatch"],
            "scalar",
        )
        self.assertEqual(multibounce.receiver_hit_count, 73)

    def test_mixed_emitters_report_mixed_dispatch_without_changing_result(self) -> None:
        scalar = run_direct_ray_trace(
            mixed_emitter_case(),
            intersection_dispatch="scalar",
        )
        mixed = run_direct_ray_trace(
            mixed_emitter_case(),
            intersection_dispatch="batch",
            intersection_batch_size=17,
        )
        performance = mixed.metrics["_performance_summary"]

        self.assertEqual(semantic_payload(scalar), semantic_payload(mixed))
        self.assertEqual(performance["intersection_dispatch"], "mixed")
        self.assertGreater(performance["intersection_batch_count"], 0)
        self.assertGreater(performance["intersection_scalar_query_count"], 0)

    def test_stop_commits_the_started_chunk_atomically(self) -> None:
        trace_input = direct_input(ray_count=96)
        stop_event = threading.Event()
        progress = []
        original_intersect_rays = trace_input.mesh.intersect_rays
        call_count = 0

        def stop_during_fourth_batch(rays, backend=None):
            nonlocal call_count
            call_count += 1
            hits = original_intersect_rays(rays, backend=backend)
            if call_count == 4:
                stop_event.set()
            return hits

        trace_input.mesh.intersect_rays = stop_during_fourth_batch
        result = run_direct_ray_trace(
            trace_input,
            progress_callback=lambda completed, requested: progress.append(
                (completed, requested)
            ),
            should_stop=stop_event.is_set,
            intersection_dispatch="batch",
            intersection_batch_size=16,
        )
        performance = result.metrics["_performance_summary"]

        self.assertEqual(call_count, 4)
        self.assertEqual(result.total_rays, 64)
        self.assertEqual(result.receiver_hit_count, 64)
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["intersection_batch_count"], 4)
        self.assertEqual(performance["intersection_ray_count"], 64)
        self.assertEqual(progress[-1], (64, 96))
        self.assertEqual(progress, sorted(progress, key=lambda value: value[0]))

    def test_stop_before_first_batch_returns_valid_zero_ray_result(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        with patch(
            "leakage_simulator.raytracer.iter_virtual_plane_ray_batches",
            side_effect=AssertionError("sampler must not run after a pending stop"),
        ):
            result = run_direct_ray_trace(
                direct_input(ray_count=32),
                should_stop=stop_event.is_set,
                intersection_dispatch="batch",
                intersection_batch_size=8,
            )
        performance = result.metrics["_performance_summary"]

        self.assertEqual(result.total_rays, 0)
        self.assertEqual(result.receiver_hit_count, 0)
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["intersection_batch_count"], 0)
        self.assertEqual(performance["intersection_ray_count"], 0)

    def test_stop_during_primary_query_still_commits_secondary_batch(self) -> None:
        trace_input = stored_reflection_case(96, with_blocker=True)
        stop_event = threading.Event()
        original_intersect_rays = trace_input.mesh.intersect_rays
        call_count = 0

        def stop_during_fourth_primary_batch(rays, backend=None):
            nonlocal call_count
            call_count += 1
            hits = original_intersect_rays(rays, backend=backend)
            if call_count == 7:
                self.assertTrue(np.all(rays.ignore_faces == -1))
                stop_event.set()
            return hits

        trace_input.mesh.intersect_rays = stop_during_fourth_primary_batch
        result = run_direct_ray_trace(
            trace_input,
            should_stop=stop_event.is_set,
            intersection_dispatch="batch",
            intersection_batch_size=16,
        )
        performance = result.metrics["_performance_summary"]

        self.assertEqual(call_count, 8)
        self.assertEqual(result.total_rays, 64)
        self.assertEqual(result.surface_hit_count, 128)
        self.assertEqual(result.terminated_ray_count, 64)
        self.assertTrue(performance["stopped_early"])
        self.assertEqual(performance["intersection_batch_count"], 8)
        self.assertEqual(performance["intersection_ray_count"], 128)

    def test_metrics_and_result_are_strict_json_serializable(self) -> None:
        result = run_direct_ray_trace(
            direct_input(ray_count=257),
            intersection_dispatch="batch",
            intersection_batch_size=32,
        )
        performance = result.metrics["_performance_summary"]

        self.assertEqual(performance["intersection_dispatch"], "batch")
        self.assertEqual(performance["intersection_batch_count"], math.ceil(257 / 32))
        self.assertEqual(performance["intersection_batch_max_size"], 32)
        self.assertEqual(performance["intersection_ray_count"], 257)
        self.assertEqual(performance["intersection_scalar_query_count"], 0)
        self.assertEqual(
            performance["intersection_timing_scope"],
            "batch_dispatch_only",
        )
        self.assertIs(performance["native_batch"], False)
        self.assertIsInstance(performance["intersection_sec"], float)
        self.assertGreaterEqual(performance["intersection_sec"], 0.0)

        encoded = json.dumps(result.to_dict(), allow_nan=False)
        restored = json.loads(encoded)
        self.assertEqual(
            restored["metrics"]["_performance_summary"]["intersection_batch_count"],
            math.ceil(257 / 32),
        )

    def test_dispatch_arguments_are_runtime_only_and_validated(self) -> None:
        with self.assertRaises(ValueError):
            run_direct_ray_trace(direct_input(1), intersection_dispatch="cuda")
        with self.assertRaises(ValueError):
            run_direct_ray_trace(
                direct_input(1),
                intersection_dispatch="batch",
                intersection_batch_size=0,
            )
        for invalid_size in (1.5, "8", True):
            with self.subTest(invalid_size=invalid_size):
                with self.assertRaises(ValueError):
                    run_direct_ray_trace(
                        direct_input(1),
                        intersection_dispatch="batch",
                        intersection_batch_size=invalid_size,
                    )
        self.assertNotIn("intersection_dispatch", direct_input(1).config.to_dict())
        self.assertNotIn("intersection_batch_size", direct_input(1).config.to_dict())


if __name__ == "__main__":
    unittest.main()
