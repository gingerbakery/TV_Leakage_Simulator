from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import weakref

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.gpu_cuda_pair_sah import SahSplitter
from leakage_simulator.gpu_cuda_pair_scene import _build_pair_scene, has_pair_scene, prepare_pair_scene
from perf5_pair_sah import SahSplitter as ReferenceSplitter


def make_mesh(seed, count):
    generator = np.random.default_rng(seed)
    mesh = TriangleMesh()
    for index in range(count):
        center = generator.uniform(-100, 100, 3)
        if index % 5 == 0:
            center = np.array([0.0, -0.0, 0.0])
        points = (center, center + (1, 0, 0), center + (0, 1, 1))
        vertices = [mesh.add_vertex(tuple(point)) for point in points]
        mesh.add_face(*vertices, 'default', {'trace_excluded': index % 11 == 0})
    return mesh


class BvhPreparationTests(unittest.TestCase):
    def test_auto_boundary_uses_traceable_face_count(self):
        for count in (4095, 4096, 4097):
            mesh = make_mesh(5041, count)
            for metadata in mesh.face_metadata:
                metadata['trace_excluded'] = False
            with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'auto'}):
                scene, _, _ = prepare_pair_scene(mesh)
                self.assertEqual(has_pair_scene(scene), count >= 4096)
                mesh.face_metadata[-1]['trace_excluded'] = True
                mesh._invalidate_acceleration()
                scene, _, _ = prepare_pair_scene(mesh)
                self.assertEqual(has_pair_scene(scene), count - 1 >= 4096)

    def test_splitter_preserves_stable_partition_for_all_sizes_and_cutoffs(self):
        mesh = make_mesh(5030, 128)
        mesh.prepare_acceleration()
        generator = np.random.default_rng(5031)
        for bins in (8, 16, 32):
            for cutoff in (2, 8, 32, 64):
                candidate = SahSplitter(mesh._prepared_triangles, bins, cutoff)
                reference = ReferenceSplitter(mesh._prepared_triangles, bins, cutoff)
                for count in (2, 3, 4, 8, 16, 31, 32, 64, 128):
                    indices = generator.permutation(128)[:count].tolist()
                    for depth in (1, 47, 48, 64):
                        with self.subTest(bins=bins, cutoff=cutoff, count=count, depth=depth):
                            self.assertEqual(candidate(indices, depth), reference(indices, depth))

    def test_splitter_preserves_tied_centers_and_input_order(self):
        mesh = make_mesh(5032, 16)
        mesh.prepare_acceleration()
        candidate = SahSplitter(mesh._prepared_triangles)
        reference = ReferenceSplitter(mesh._prepared_triangles, 16, 32)
        for indices in ([0, 5], [5, 0], [10, 5], [5, 10], [15, 0, 5, 10]):
            self.assertEqual(candidate(indices, 1), reference(indices, 1))

    def test_every_auxiliary_bound_matches_full_descendant_scan(self):
        for seed in range(5033, 5037):
            mesh = make_mesh(seed, 257)
            original = mesh.prepare_gpu_cuda_scene()
            for splitter in (SahSplitter, None):
                scene = _build_pair_scene(mesh, original, splitter_factory=splitter)
                self.assertTrue(has_pair_scene(scene))
                root = len(original.node_count)

                def descendants(node):
                    count = int(scene.node_count[node])
                    if count:
                        start = int(scene.node_start[node])
                        return scene.ordered_faces[start:start + count].tolist()
                    return descendants(scene.node_left[node]) + descendants(scene.node_right[node])

                for node in range(root, len(scene.node_count)):
                    faces = descendants(node)
                    lower = np.asarray([min(mesh._prepared_triangles[face].bounds_min[axis]
                        for face in faces) for axis in range(3)])
                    upper = np.asarray([max(mesh._prepared_triangles[face].bounds_max[axis]
                        for face in faces) for axis in range(3)])
                    padding = 1e-7 * (np.abs(lower) + np.abs(upper) + 1.0)
                    np.testing.assert_array_equal(scene.node_bounds_min[node].view(np.uint64),
                        (lower - padding).view(np.uint64))
                    np.testing.assert_array_equal(scene.node_bounds_max[node].view(np.uint64),
                        (upper + padding).view(np.uint64))

    def test_geometry_invalidation_releases_stale_pair_scene(self):
        mesh = make_mesh(5038, 48)
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            first, _, _ = prepare_pair_scene(mesh)
            cached, elapsed, reused = prepare_pair_scene(mesh)
            self.assertIs(cached, first)
            self.assertTrue(reused)
            self.assertEqual(elapsed, 0.0)
            mesh.add_vertex((200.0, 200.0, 200.0))
            self.assertIsNone(mesh._gpu_pair_scene_cache)
            mesh.face_metadata[1]['trace_excluded'] = True
            mesh._invalidate_acceleration()
            replacement, _, reused = prepare_pair_scene(mesh)
            self.assertFalse(reused)
            self.assertIsNot(replacement, first)
            self.assertFalse(replacement.traceable_face_mask[1])
            mesh._invalidate_acceleration()
            self.assertIsNone(mesh._gpu_pair_scene_cache)

    def test_optical_edit_reuses_geometry_but_not_optical_values(self):
        mesh = make_mesh(5039, 48)
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            first, _, _ = prepare_pair_scene(mesh)
            mesh.face_material[1] = 'different_material'
            mesh.face_metadata[1]['optical_profile_id'] = 'different_surface'
            current, elapsed, reused = prepare_pair_scene(mesh)
            self.assertIs(current, first)
            self.assertTrue(reused)
            self.assertEqual(elapsed, 0.0)
            self.assertEqual(mesh.material_id(1), 'different_material')
            self.assertEqual(mesh.metadata(1)['optical_profile_id'], 'different_surface')

    def test_invalidated_auxiliary_arrays_are_not_retained_by_mesh(self):
        mesh = make_mesh(5040, 48)
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            scene, _, _ = prepare_pair_scene(mesh)
            bounds = weakref.ref(scene.node_bounds_min)
            del scene
            self.assertIsNotNone(bounds())
            mesh._invalidate_acceleration()
            self.assertIsNone(bounds())


if __name__ == '__main__':
    unittest.main()
