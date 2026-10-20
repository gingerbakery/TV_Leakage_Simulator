from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_gpu_pair_leaf import fast_node_count, has_leaf_scene, leaf_builder, leaf_variant
import test_perf5_gpu_pair_production as baseline
from test_perf5_guarded_bvh import gap_case
from verify_perf5_pair_scalar_oracle import geometry


class PairLeafLayoutTests(unittest.TestCase):
    def test_every_fast_leaf_retains_all_original_faces_exactly_once(self):
        mesh, _ = geometry(191300, 5e-11, 48)
        original = mesh.prepare_gpu_cuda_scene()
        for leaf_size in (1, 2, 4, 8, 16):
            scene = leaf_builder(leaf_size)(mesh, original)
            self.assertTrue(has_leaf_scene(scene, leaf_size))
            root = len(original.node_count)
            self.assertEqual(len(scene.node_count) - root, fast_node_count(len(original.ordered_faces), leaf_size))
            found = []
            visited = set()
            pending = [root]
            while pending:
                node = pending.pop()
                self.assertNotIn(node, visited)
                self.assertGreaterEqual(node, root)
                self.assertLess(node, len(scene.node_count))
                visited.add(node)
                count = int(scene.node_count[node])
                if count:
                    self.assertLessEqual(count, leaf_size)
                    self.assertGreater(count, 0)
                    start = int(scene.node_start[node])
                    self.assertGreaterEqual(start, len(original.ordered_faces))
                    self.assertLessEqual(start + count, 2 * len(original.ordered_faces))
                    face_indices = scene.ordered_faces[start:start + count].tolist()
                    found.extend(face_indices)
                    for face_index in face_indices:
                        triangle = mesh._prepared_triangles[face_index]
                        self.assertTrue(np.all(scene.node_bounds_min[node] <= triangle.bounds_min))
                        self.assertTrue(np.all(scene.node_bounds_max[node] >= triangle.bounds_max))
                else:
                    pending.extend((int(scene.node_left[node]), int(scene.node_right[node])))
            self.assertEqual(len(visited), len(scene.node_count) - root)
            self.assertEqual(len(found), len(set(found)))
            self.assertEqual(sorted(found), sorted(original.ordered_faces.tolist()))
            self.assertIs(scene.triangle_v0, original.triangle_v0)
            self.assertIs(scene.traceable_face_mask, original.traceable_face_mask)
            self.assertFalse(scene.ordered_faces.flags.writeable)

    def test_invalid_sizes_and_wrong_leaf_layout_are_rejected(self):
        mesh = gap_case().mesh
        original = mesh.prepare_gpu_cuda_scene()
        for invalid in (0, 3, 32):
            with self.assertRaises(ValueError):
                leaf_builder(invalid)
            self.assertFalse(has_leaf_scene(original, invalid))
        scene = leaf_builder(2)(mesh, original)
        self.assertFalse(has_leaf_scene(scene, 1))

    def test_scalar_leaf_variants_keep_original_face_and_distance_bits(self):
        canonical, candidate, _ = baseline.scalar_pair_functions()
        generator = np.random.default_rng(191301)
        for gap in (0.0, 5e-11, 1.5e-10, 5e-10, 1e-6):
            mesh, targets = geometry(191100, gap, 24)
            original = mesh.prepare_gpu_cuda_scene()
            scenes = [leaf_builder(size)(mesh, original) for size in (1, 2, 4, 8, 16)]
            for sample in range(300):
                origin = generator.uniform(-18, 18, 3)
                direction = generator.normal(size=3)
                direction /= np.linalg.norm(direction)
                distance = generator.uniform(0.1, 5.0)
                if sample % 3:
                    center, normal = targets[sample % len(targets)]
                    side = 1.0 if sample % 2 else -1.0
                    origin = center + side * normal * distance
                    direction = -side * normal
                if sample % 11 == 0:
                    direction = np.asarray((1.0, 1e-14, 0.0))
                maximum = distance if sample % 7 == 0 else math.inf
                ignored = int(generator.integers(len(mesh.faces))) if sample % 5 == 0 else -1
                expected = baseline.query(canonical, original, origin, direction, maximum, ignored)
                for scene in scenes:
                    actual = baseline.query(candidate, scene, origin, direction, maximum, ignored)
                    self.assertEqual(actual[1:], expected[1:], (gap, sample))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64),
                        np.float64(expected[0]).view(np.uint64), (gap, sample))


class PairLeaf2GpuTests(baseline.ProductionPairGpuTests):
    leaf_size = 2

    def setUp(self):
        self.leaf_states = {}

    def run_mode(self, trace_input, mode, **keywords):
        with leaf_variant(self.leaf_size, self.leaf_states):
            return super().run_mode(trace_input, mode, **keywords)


class PairLeaf4GpuTests(PairLeaf2GpuTests):
    leaf_size = 4


class PairLeaf8GpuTests(PairLeaf2GpuTests):
    leaf_size = 8


if __name__ == '__main__':
    unittest.main()
