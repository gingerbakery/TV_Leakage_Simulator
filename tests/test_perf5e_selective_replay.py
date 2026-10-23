from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from leakage_simulator import raytracer
from leakage_simulator.geometry import RayBatch, RayHitBatch, TriangleMesh
from tools.perf5e_native.probe import HIT_DTYPE
from tools.perf5e_native.selective_replay import (
    SelectiveReplayObserver, assert_exact_query, replay_mask, selective_dispatch, selective_query,
)
from tools.perf5e_native.transport_validation import observe_dispatch


class SelectiveReplayTests(unittest.TestCase):
    def setUp(self):
        self.mesh = TriangleMesh()
        for height in (5.0, 5.0, 8.0):
            vertices = [self.mesh.add_vertex(point) for point in (
                (-1.0, -1.0, height), (1.0, -1.0, height), (0.0, 1.0, height),
            )]
            self.mesh.add_face(*vertices, "test")
        self.rays = RayBatch([[0, 0, 0]] * 4, [[0, 0, 1]] * 4)
        self.candidate = np.zeros(4, dtype=HIT_DTYPE)
        self.candidate["face"] = [1, 1, 1, -1]
        self.candidate["distance"] = [5, 5, 5, np.inf]
        self.candidate["flags"] = [1, 2, 4, 0]

    def test_ambiguous_and_miss_rows_replay_original_tie_order(self):
        expected, _ = self.mesh.intersect_rays_native_cpu(self.rays, backend="bvh")
        actual, replay, rejected = selective_query(self.mesh, self.rays, self.candidate, "cpu")
        assert_exact_query(actual, expected)
        self.assertTrue(replay.all())
        self.assertFalse(rejected.any())

    def test_winner_refinement_preserves_cpu_bits_without_bvh(self):
        self.candidate["face"] = 0
        self.candidate["flags"] = 0
        self.candidate["distance"] = np.nextafter(5.0, 10.0)
        with patch.object(self.mesh, "intersect_rays_native_cpu", side_effect=AssertionError("Unexpected replay")):
            actual, replay, _ = selective_query(self.mesh, self.rays, self.candidate, "cpu")
        assert_exact_query(actual, RayHitBatch(np.full(4, 5.0), np.zeros(4, dtype=np.int64)))
        self.assertFalse(replay.any())

    def test_invalid_winner_refinement_replays_instead_of_dropping_ray(self):
        self.candidate["face"] = 0
        self.candidate["distance"] = 5
        self.candidate["flags"] = 0
        refine = Mock(return_value=np.array([np.inf, np.nan, 1e-9, 5.0]))
        actual, replay, rejected = selective_query(self.mesh, self.rays, self.candidate, "cpu", refine=refine)
        self.assertEqual(replay.tolist(), [True, True, True, False])
        self.assertTrue(np.array_equal(replay, rejected))
        self.assertEqual(actual.t.tolist(), [5.0] * 4)

    def test_small_gpu_subset_never_silently_switches_to_cpu(self):
        self.candidate["face"] = 0
        self.candidate["distance"] = 5
        self.candidate["flags"] = [1, 0, 0, 0]
        gpu = Mock(return_value=(RayHitBatch([5.0], [0]), None))
        with patch.object(self.mesh, "intersect_rays_gpu_cuda", gpu), patch.object(
            self.mesh, "intersect_rays_native_cpu", side_effect=AssertionError("CPU substitution"),
        ):
            selective_query(self.mesh, self.rays, self.candidate, "gpu_cuda",
                            refine=Mock(return_value=np.full(3, 5.0)))
        self.assertEqual(len(gpu.call_args.args[0]), 1)

    def test_unknown_flags_fail_closed(self):
        self.candidate["flags"][0] = 8
        with self.assertRaisesRegex(ValueError, "flags"):
            replay_mask(self.candidate, np.ones(3, bool), self.rays)

    def test_invalid_face_fails_closed(self):
        self.candidate["face"][0] = 3
        with self.assertRaisesRegex(ValueError, "face"):
            replay_mask(self.candidate, np.ones(3, bool), self.rays)

    def test_excluded_face_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "excluded"):
            replay_mask(self.candidate, np.array([True, False, True]), self.rays)

    def test_ignored_face_fails_closed(self):
        self.rays.ignore_faces[0] = 1
        with self.assertRaisesRegex(ValueError, "ignored"):
            replay_mask(self.candidate, np.ones(3, bool), self.rays)

    def test_out_of_range_distance_fails_closed(self):
        self.rays.max_t[0] = 4.0
        with self.assertRaisesRegex(ValueError, "distance"):
            replay_mask(self.candidate, np.ones(3, bool), self.rays)

    def test_malformed_miss_fails_closed(self):
        self.candidate["distance"][-1] = 5.0
        with self.assertRaisesRegex(ValueError, "miss"):
            replay_mask(self.candidate, np.ones(3, bool), self.rays)

    def test_wrong_backend_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "backend"):
            selective_query(self.mesh, self.rays, self.candidate, "auto")

    def test_one_ulp_difference_is_not_hidden(self):
        with self.assertRaisesRegex(AssertionError, "distance_bits=1"):
            assert_exact_query(RayHitBatch([np.nextafter(5.0, 10.0)], [0]), RayHitBatch([5.0], [0]))

    def test_cardinality_mismatch_cannot_broadcast_into_success(self):
        single = RayHitBatch([5.0], [0])
        multiple = RayHitBatch([5.0, 5.0], [0, 0])
        for actual, expected in ((single, multiple), (multiple, single)):
            with self.subTest(actual=len(actual), expected=len(expected)):
                with self.assertRaisesRegex(AssertionError, "cardinality"):
                    assert_exact_query(actual, expected)

    def test_wrong_unflagged_nearest_face_is_not_certified(self):
        self.candidate["face"] = 2
        self.candidate["distance"] = 8
        self.candidate["flags"] = 0
        actual, replay, _ = selective_query(self.mesh, self.rays, self.candidate, "cpu")
        self.assertFalse(replay.any())
        expected, _ = self.mesh.intersect_rays_native_cpu(self.rays, backend="bvh")
        with self.assertRaisesRegex(AssertionError, "faces=4"):
            assert_exact_query(actual, expected)

    def test_empty_rows(self):
        rays = RayBatch(np.empty((0, 3)), np.empty((0, 3)))
        actual, replay, _ = selective_query(self.mesh, rays, self.candidate[:0], "cpu")
        self.assertEqual(len(actual), 0)
        self.assertEqual(len(replay), 0)

    def test_isolated_patch_restored_on_failure_and_nested_call(self):
        observer = SelectiveReplayObserver(Path("unused"))
        original = raytracer._IntersectionDispatchStats.intersect_batch
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            with selective_dispatch(observer):
                with self.assertRaisesRegex(RuntimeError, "nested"):
                    with observe_dispatch(observer):
                        self.fail("Nested context entered")
                raise RuntimeError("cancel")
        self.assertIs(original, raytracer._IntersectionDispatchStats.intersect_batch)
        self.assertTrue(observer.closed)

    def test_provider_identity_comes_from_executed_batch(self):
        observer = SelectiveReplayObserver(Path("unused"))
        observer.observe_with_backend = Mock(return_value=RayHitBatch([5.0], [0]))
        dispatch = SimpleNamespace(gpu_cuda_gpu_success_count=0, native_success_count=0)

        def original(state, mesh, rays, **kwargs):
            state.native_success_count += 1
            state.gpu_cuda_gpu_success_count += 1
            return RayHitBatch([5.0], [0])

        with patch.object(raytracer._IntersectionDispatchStats, "intersect_batch", original):
            with selective_dispatch(observer):
                raytracer._IntersectionDispatchStats.intersect_batch(dispatch, self.mesh, self.rays)
        self.assertEqual(observer.observe_with_backend.call_args.args[-1], "gpu_cuda")

    def test_hybrid_cpu_batch_stays_cpu(self):
        observer = SelectiveReplayObserver(Path("unused"))
        observer.observe_with_backend = Mock(return_value=RayHitBatch([5.0], [0]))
        dispatch = SimpleNamespace(gpu_cuda_gpu_success_count=12, native_success_count=12)

        def original(state, mesh, rays, **kwargs):
            state.native_success_count += 1
            return RayHitBatch([5.0], [0])

        with patch.object(raytracer._IntersectionDispatchStats, "intersect_batch", original):
            with selective_dispatch(observer):
                raytracer._IntersectionDispatchStats.intersect_batch(dispatch, self.mesh, self.rays)
        self.assertEqual(observer.observe_with_backend.call_args.args[-1], "cpu")

    def test_canonical_failure_cannot_be_certified(self):
        observer = SelectiveReplayObserver(Path("unused"))
        observer.observe_with_backend = Mock()
        dispatch = SimpleNamespace(gpu_cuda_gpu_success_count=0, native_success_count=0)
        with patch.object(raytracer._IntersectionDispatchStats, "intersect_batch", return_value=RayHitBatch([5.0], [0])):
            with selective_dispatch(observer):
                with self.assertRaisesRegex(RuntimeError, "Canonical native dispatch failed"):
                    raytracer._IntersectionDispatchStats.intersect_batch(dispatch, self.mesh, self.rays)
        observer.observe_with_backend.assert_not_called()

    def test_other_thread_cannot_use_global_hook(self):
        observer = SelectiveReplayObserver(Path("unused"))
        errors = []

        def other_thread():
            try:
                raytracer._IntersectionDispatchStats.intersect_batch(None, self.mesh, self.rays)
            except RuntimeError as error:
                errors.append(str(error))

        with selective_dispatch(observer):
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ["Selective replay requires an isolated process"])

    def test_observer_mismatch_propagates_without_oracle_substitution(self):
        observer = SelectiveReplayObserver(Path("unused"))
        observer.probe = Mock()
        observer.probe.query.return_value = (self.candidate, {})
        observer.records.append({})
        self.addCleanup(observer.close)
        with patch("tools.perf5e_native.selective_replay.OptixTransportObserver.observe"), patch(
            "tools.perf5e_native.selective_replay.selective_query",
            return_value=(RayHitBatch([8.0] * 4, [2] * 4), np.zeros(4, bool), np.zeros(4, bool)),
        ):
            with self.assertRaisesRegex(AssertionError, "faces=4"):
                observer.observe_with_backend(self.mesh, self.rays, RayHitBatch([5.0] * 4, [0] * 4), "cpu")


if __name__ == "__main__":
    unittest.main()
