from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from leakage_simulator import raytracer
from leakage_simulator.geometry import RayBatch, RayHitBatch
from tools.perf5e_native.probe import HIT_DTYPE
from tools.perf5e_native.transport_validation import (
    OptixTransportObserver, compare_query, observe_dispatch, production_preflight_gate,
)


class FakeProbe:
    def __init__(self, scene, directory):
        self.scene = scene
        self.closed = False

    def query_coverage(self, rays, expected):
        candidate = np.zeros(len(rays), dtype=HIT_DTYPE)
        candidate["face"] = 1
        candidate["distance"] = 99.0
        return candidate, np.full(len(rays), 3, dtype=np.uint8), {}

    def close(self):
        self.closed = True


class TransportObservationTests(unittest.TestCase):
    def setUp(self):
        self.scene = SimpleNamespace(traceable_face_mask=np.array([True, True]))
        self.mesh = SimpleNamespace(prepare_native_cpu_scene=lambda: self.scene)
        self.rays = RayBatch([[0.0, 0.0, 1.0]], [[0.0, 0.0, -1.0]])
        self.reference = RayHitBatch([1.0], [0])
        self.observer = OptixTransportObserver(Path("unused"), factory=FakeProbe)
        self.addCleanup(self.observer.close)

    def test_wrong_candidate_never_replaces_reference(self):
        result = self.observer.observe(self.mesh, self.rays, self.reference)
        self.assertIs(result, self.reference)
        self.assertEqual(result.face_indices.tolist(), [0])
        summary = self.observer.summary()
        self.assertEqual(summary["candidate_results_used"], 0)
        self.assertEqual(summary["unflagged_mismatches"], 1)
        self.assertFalse(summary["production_enabled"])

    def test_same_geometry_reuses_probe(self):
        self.observer.observe(self.mesh, self.rays, self.reference)
        previous = self.observer.probe
        self.observer.observe(self.mesh, self.rays, self.reference)
        self.assertIs(previous, self.observer.probe)
        self.assertEqual(self.observer.build_count, 1)

    def test_changed_geometry_closes_old_probe(self):
        self.observer.observe(self.mesh, self.rays, self.reference)
        previous = self.observer.probe
        self.scene = SimpleNamespace(traceable_face_mask=np.array([True, False]))
        self.observer.observe(self.mesh, self.rays, self.reference)
        self.assertTrue(previous.closed)
        self.assertIsNot(previous, self.observer.probe)
        self.assertEqual(self.observer.build_count, 2)

    def test_empty_scene_not_reported_as_coverage_pass(self):
        self.scene.traceable_face_mask[:] = False
        reference = RayHitBatch([np.inf], [-1])
        self.assertIs(self.observer.observe(self.mesh, self.rays, reference), reference)
        self.assertFalse(self.observer.summary()["observed_coverage_passed"])
        self.assertEqual(self.observer.attempts, 0)

    def test_probe_errors_propagate_instead_of_passing(self):
        with patch.object(FakeProbe, "query_coverage", side_effect=RuntimeError("native failure")):
            with self.assertRaisesRegex(RuntimeError, "native failure"):
                self.observer.observe(self.mesh, self.rays, self.reference)
        self.assertEqual(self.observer.attempts, 1)
        self.assertEqual(self.observer.successes, 0)

    def test_patch_and_probe_cleanup_on_cancel_or_failure(self):
        original = raytracer._IntersectionDispatchStats.intersect_batch
        with self.assertRaises(InterruptedError):
            with observe_dispatch(self.observer):
                self.observer.observe(self.mesh, self.rays, self.reference)
                probe = self.observer.probe
                raise InterruptedError("cancel")
        self.assertIs(raytracer._IntersectionDispatchStats.intersect_batch, original)
        self.assertTrue(probe.closed)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.observer.observe(self.mesh, self.rays, self.reference)

    def test_nested_observation_rejected_without_losing_outer_patch(self):
        inner = OptixTransportObserver(Path("unused"), factory=FakeProbe)
        with observe_dispatch(self.observer):
            outer = raytracer._IntersectionDispatchStats.intersect_batch
            with self.assertRaisesRegex(RuntimeError, "nested"):
                with observe_dispatch(inner):
                    self.fail("Nested scope entered")
            self.assertIs(raytracer._IntersectionDispatchStats.intersect_batch, outer)
        inner.close()

    def test_all_guards_do_not_establish_coverage(self):
        candidate = np.zeros(1, dtype=HIT_DTYPE)
        candidate["flags"] = 4
        candidate["face"] = -1
        candidate["distance"] = np.inf
        result = compare_query(candidate, np.array([0], dtype=np.uint8), self.reference)
        self.assertEqual(result["eligible_reference_hits"], 0)
        self.assertEqual(result["guarded_reference_hits"], 1)

    def test_missing_visit_is_not_hidden_by_correct_candidate(self):
        candidate = np.zeros(1, dtype=HIT_DTYPE)
        candidate["distance"] = 1.0
        result = compare_query(candidate, np.array([0], dtype=np.uint8), self.reference)
        self.assertEqual(result["raw_face_mismatches"], 0)
        self.assertEqual(result["missing_reference_visits"], 1)

    def test_invalid_metadata_fails_closed(self):
        candidate = np.zeros(1, dtype=HIT_DTYPE)
        for visited in (np.array([4], dtype=np.uint8), np.array([1], dtype=np.uint8), np.array([3])):
            with self.subTest(visited=visited), self.assertRaises(ValueError):
                compare_query(candidate, visited, self.reference)
        candidate["flags"] = 4
        with self.assertRaises(ValueError):
            compare_query(candidate, np.array([3], dtype=np.uint8), self.reference)

    def test_preflight_requires_all_production_fields(self):
        valid = dict(available=True, strict_float64=True, kernel_executed=True,
                     kernel_verified=True, preflight_scope="production_ray_bvh",
                     provider_contract="strict_float64_bvh_v1")
        self.assertTrue(production_preflight_gate(valid))
        for key in valid:
            with self.subTest(key=key):
                invalid = valid.copy()
                del invalid[key]
                self.assertFalse(production_preflight_gate(invalid))


if __name__ == "__main__":
    unittest.main()
