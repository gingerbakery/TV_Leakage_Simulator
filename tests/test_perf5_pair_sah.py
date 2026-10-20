from __future__ import annotations

import math
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_gpu_pair_production as baseline
from perf5_pair_sah import SahSplitter, scene_builder, scene_variant
from verify_perf5_pair_scalar_oracle import geometry
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.gpu_cuda_pair_sah import SahSplitter as ProductSahSplitter


CONFIGURATIONS = ((8, 32), (16, 32), (16, 64))


class PairSahSceneTests(unittest.TestCase):
    def test_static_production_default_matches_the_experimental_layout(self):
        for scenario, gap in enumerate((0.0, 1.5e-10, 5e-10, 1e-6)):
            mesh, _ = geometry(195600 + scenario, gap, 48)
            original = mesh.prepare_gpu_cuda_scene()
            expected = scene_builder(16, 32)(mesh, original)
            actual = pair_scene._build_pair_scene(mesh, original)
            self.assertEqual(actual.stack_width, expected.stack_width)
            for name in ('node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
                'node_start', 'node_count', 'ordered_faces'):
                np.testing.assert_array_equal(getattr(actual, name), getattr(expected, name))
            self.assertIsInstance(ProductSahSplitter(mesh._prepared_triangles), ProductSahSplitter)

    def test_every_face_and_canonical_prefix_are_preserved(self):
        mesh, _ = geometry(195001, 1.5e-10, 48)
        original = mesh.prepare_gpu_cuda_scene()
        for bins, cutoff in CONFIGURATIONS:
            with self.subTest(bins=bins, cutoff=cutoff):
                scene = scene_builder(bins, cutoff)(mesh, original)
                self.assertTrue(pair_scene.has_pair_scene(scene))
                root = len(original.node_count)
                for name in ('node_bounds_min', 'node_bounds_max', 'node_left', 'node_right', 'node_start', 'node_count'):
                    values = getattr(scene, name)[:root].copy()
                    if name == 'node_start':
                        values[0] = original.node_start[0]
                    np.testing.assert_array_equal(values, getattr(original, name))
                    self.assertFalse(getattr(scene, name).flags.writeable)
                for name in ('triangle_v0', 'triangle_edge1', 'triangle_edge2', 'traceable_face_mask'):
                    self.assertIs(getattr(scene, name), getattr(original, name))
                face_count = len(original.triangle_v0)
                self.assertEqual(len(scene.node_count) - root, 2 * face_count - 1)
                np.testing.assert_array_equal(np.sort(scene.ordered_faces[face_count:2 * face_count]), np.arange(face_count))
                leaves = np.flatnonzero(scene.node_count[root:] > 0) + root
                np.testing.assert_array_equal(scene.node_count[leaves], np.ones(face_count, dtype=np.int64))
                np.testing.assert_array_equal(scene.ordered_faces[:face_count], original.ordered_faces)

    def test_cache_reuses_only_unchanged_geometry_and_cpu_off_does_not_build(self):
        mesh, _ = geometry(195002, 0.0, 16)
        states = {}
        with scene_variant(16, 32, states), patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            first, elapsed, reused = pair_scene.prepare_pair_scene(mesh)
            self.assertGreater(elapsed, 0.0)
            self.assertFalse(reused)
            second, elapsed, reused = pair_scene.prepare_pair_scene(mesh)
            self.assertIs(second, first)
            self.assertEqual(elapsed, 0.0)
            self.assertTrue(reused)
            vertices = [mesh.add_vertex(point) for point in ((200, 0, 0), (201, 0, 0), (200, 1, 0))]
            mesh.add_face(*vertices, 'opaque')
            edited = pair_scene.prepare_pair_scene(mesh)[0]
            self.assertIsNot(edited, first)
            self.assertEqual(len(edited.triangle_v0), len(first.triangle_v0) + 1)
            with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'off'}):
                self.assertIs(pair_scene.prepare_pair_scene(mesh)[0], mesh.prepare_gpu_cuda_scene())
        for bins, cutoff in ((3, 32), (16, 1)):
            with self.assertRaises(ValueError):
                SahSplitter(mesh._prepared_triangles, bins, cutoff)

    def test_trace_off_faces_stay_excluded_without_renumbering(self):
        mesh, _ = geometry(195004, 5e-10, 48)
        for face in range(0, len(mesh.faces), 7):
            mesh.face_metadata[face]['trace_excluded'] = True
        original = mesh.prepare_gpu_cuda_scene()
        canonical, candidate, _ = baseline.scalar_pair_functions()
        generator = np.random.default_rng(195005)
        for bins, cutoff in CONFIGURATIONS:
            scene = scene_builder(bins, cutoff)(mesh, original)
            self.assertTrue(pair_scene.has_pair_scene(scene))
            face_offset = len(original.ordered_faces)
            active = scene.ordered_faces[face_offset:2 * face_offset]
            np.testing.assert_array_equal(np.sort(active), np.sort(original.ordered_faces))
            self.assertTrue(np.all(scene.traceable_face_mask[active]))
            self.assertEqual(len(scene.triangle_v0), len(mesh.faces))
            for _ in range(96):
                origin = generator.uniform(-18, 18, 3)
                direction = generator.normal(size=3)
                direction /= np.linalg.norm(direction)
                expected = baseline.query(canonical, original, origin, direction)
                actual = baseline.query(candidate, scene, origin, direction)
                self.assertEqual(actual[1:], expected[1:])
                self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64))

    def test_scalar_transport_matches_original_face_and_distance_bits(self):
        canonical, candidate, _ = baseline.scalar_pair_functions()
        generator = np.random.default_rng(195300)
        for scenario, gap in enumerate((0.0, 5e-11, 1.5e-10, 5e-10, 1e-6)):
            mesh, targets = geometry(195100 + scenario, gap, 48)
            original = mesh.prepare_gpu_cuda_scene()
            for bins, cutoff in CONFIGURATIONS:
                scene = scene_builder(bins, cutoff)(mesh, original)
                for sample in range(192):
                    origin = generator.uniform(-18, 18, 3)
                    direction = generator.normal(size=3)
                    direction /= np.linalg.norm(direction)
                    if sample % 3:
                        center, normal = targets[sample % len(targets)]
                        side = 1.0 if sample % 2 else -1.0
                        origin = center + side * normal * generator.uniform(0.1, 5.0)
                        direction = -side * normal
                    if sample % 11 == 0:
                        direction = np.asarray((1.0, 1e-14, 0.0))
                    maximum = math.inf if sample % 7 else generator.uniform(0.1, 5.0)
                    ignored = int(generator.integers(len(mesh.faces))) if sample % 5 == 0 else -1
                    expected = baseline.query(canonical, original, origin, direction, maximum, ignored)
                    actual = baseline.query(candidate, scene, origin, direction, maximum, ignored)
                    self.assertEqual(actual[1:], expected[1:], (scenario, bins, cutoff, sample))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64),
                        (scenario, bins, cutoff, sample))


class PairSah8GpuTests(baseline.ProductionPairGpuTests):
    bins = 8
    cutoff = 32

    def setUp(self):
        self.enterContext(scene_variant(self.bins, self.cutoff, {}))

    def test_sah_partitioned_cavity_preserves_actual_thousand_reflections(self):
        scene = baseline.add_distant_faces(baseline.cavity_case(), count=96)
        scene.config.compute_backend = 'gpu_cuda'
        expected = self.run_mode(scene, 'off')
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(
            resident, '_WORKSPACES', threading.local()
        ):
            actual = self.run_mode(scene, 'force')
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        performance = actual.metrics['_performance_summary']
        self.assertGreater(performance['gpu_event_window_retry_count'], 0)
        self.assertGreater(performance['gpu_pair_bvh_selected_batches'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


class PairSah16GpuTests(PairSah8GpuTests):
    bins = 16


class PairSah16Cutoff64GpuTests(PairSah16GpuTests):
    cutoff = 64


if __name__ == '__main__':
    unittest.main()
