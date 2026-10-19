from __future__ import annotations

import ast
import copy
from dataclasses import replace
import itertools
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from perf5_isolated_hit_cluster import build_original_topology, isolated_cluster, fold_cluster_by_original_bvh
from perf5_isolated_hit_cluster import legacy_hit_fold, order_cluster_by_original_bvh
from test_perf5_guarded_bvh import gap_case, scalar_functions
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def original_box_function():
    import inspect
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    definition = copy.deepcopy(next(node for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry'))
    definition.decorator_list = []
    namespace = {'math': math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[])),
        '<perf5_original_box_scalar>', 'exec'), namespace)
    return namespace['ray_box_entry']


class IsolatedHitClusterTests(unittest.TestCase):
    def test_nearest_only_cutoff_is_not_a_valid_legacy_certificate(self):
        ordered = ((0, 1.0 + 1.5e-10), (1, 1.0 + 0.75e-10), (2, 1.0))
        full = legacy_hit_fold(ordered, 1e-8, math.inf)
        truncated = legacy_hit_fold(ordered[1:], 1e-8, math.inf)
        self.assertEqual(full[1], 2)
        self.assertEqual(truncated[1], 1)
        cluster, cap, reason = isolated_cluster(ordered, 1e-8, math.inf)
        self.assertIsNone(reason)
        self.assertEqual(legacy_hit_fold(cluster, 1e-8, cap), full)

    def test_isolated_cluster_preserves_every_permutation_and_random_far_history(self):
        generator = np.random.default_rng(191019)
        near = ((5, 1.0), (2, 1.0 + 0.6e-10), (7, 1.0 + 1.5e-10), (3, 1.0 + 3.9e-10))
        far = ((0, 1.0 + 8.1e-10), (1, 2.0))
        for ordered in itertools.permutations((*near, *far)):
            cluster, cap, reason = isolated_cluster(ordered, 1e-8, math.inf)
            self.assertIsNone(reason)
            self.assertEqual(legacy_hit_fold(cluster, 1e-8, cap),
                legacy_hit_fold(ordered, 1e-8, math.inf))
        accepted = 0
        for _ in range(10000):
            distances = np.concatenate((1.0 + generator.uniform(0.0, 3.9e-10, 4),
                1.0 + generator.uniform(8.1e-10, 2e-9, 8)))
            ordered = [(int(face), float(distances[face])) for face in generator.permutation(12)]
            cluster, cap, reason = isolated_cluster(ordered, 1e-8, math.inf)
            if reason is None:
                accepted += 1
                self.assertEqual(legacy_hit_fold(cluster, 1e-8, cap),
                    legacy_hit_fold(ordered, 1e-8, math.inf))
        self.assertGreater(accepted, 1000)

    def test_interference_band_capacity_caps_and_nonfinite_fail_closed(self):
        self.assertEqual(isolated_cluster(((0, 1.0), (1, 1.0 + 5e-10)), 1e-8, math.inf)[2],
            'interference_band')
        self.assertEqual(isolated_cluster(tuple((face, 1.0) for face in range(9)), 1e-8, math.inf)[2],
            'candidate_overflow')
        self.assertEqual(isolated_cluster(((0, math.nan),), 1e-8, math.inf)[2], 'nonfinite_distance')
        self.assertEqual(isolated_cluster(((0, 1e5),), 1e-8, math.inf)[2], 'distance_precision_guard')
        self.assertEqual(isolated_cluster(((0, 1.0),), 1e-8, 0.5)[2], 'no_candidate')
        cluster, cap, reason = isolated_cluster(((0, 1.0), (1, 1.0 + 5e-10)), 1e-8, 1.0)
        self.assertIsNone(reason)
        self.assertEqual(cap, 1.0)
        self.assertEqual(cluster, ((0, 1.0),))

    def test_topology_is_readonly_and_rejects_invalid_geometry(self):
        scene = gap_case().mesh.prepare_gpu_cuda_scene()
        topology = build_original_topology(scene)
        self.assertTrue(np.all(topology.face_leaf[scene.ordered_faces] >= 0))
        for values in (topology.face_leaf, topology.face_order, topology.node_path, topology.node_code):
            self.assertFalse(values.flags.writeable)
        bad_bounds = scene.node_bounds_min.copy()
        bad_bounds[0, 0] = math.nan
        with self.assertRaisesRegex(ValueError, 'finite'):
            build_original_topology(replace(scene, node_bounds_min=bad_bounds))
        bad_children = scene.node_left.copy()
        bad_children[0] = 0
        with self.assertRaisesRegex(ValueError, 'acyclic'):
            build_original_topology(replace(scene, node_left=bad_children))
        bad_faces = scene.ordered_faces.copy()
        bad_faces[1] = bad_faces[0]
        with self.assertRaisesRegex(ValueError, 'unique'):
            build_original_topology(replace(scene, ordered_faces=bad_faces))

    def test_leaf_pruning_uses_entry_at_leaf_start_not_each_triangle(self):
        scene = SimpleNamespace(node_left=np.asarray([1, -1, -1]), node_right=np.asarray([2, -1, -1]))
        leaves = np.full(6, -1, dtype=np.int32)
        leaves[5], leaves[0] = 1, 2
        positions = np.zeros(6, dtype=np.int32)
        positions[0] = 1
        topology = SimpleNamespace(face_leaf=leaves, face_order=positions,
            node_code=np.asarray([0, 0, 1 << 63], dtype=np.uint64),
            node_path=np.asarray([[0, -1], [0, 1], [0, 2]]))
        candidates = ((5, 1.0 + 1.5e-10), (0, 1.0 + 0.75e-10))
        entry = lambda node: (0.0, 1.0, 1.0 + 1.6e-10)[node]
        actual = fold_cluster_by_original_bvh(candidates, topology, scene, entry, 1e-8, math.inf)
        self.assertEqual(actual, candidates[0][::-1])
        self.assertNotEqual(actual, legacy_hit_fold(candidates, 1e-8, math.inf))
        leaves[0] = 1
        actual = fold_cluster_by_original_bvh(candidates, topology, scene,
            lambda node: 1.0 + 1.6e-10, 1e-8, math.inf)
        self.assertEqual(actual, candidates[1][::-1])

    def test_lca_order_matches_original_dfs_for_both_directions(self):
        scene = gap_case().mesh.prepare_gpu_cuda_scene()
        topology = build_original_topology(scene)
        box = original_box_function()
        for direction in ((0.0, 0.0, -1.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)):
            origin = (0.0, 0.0, 1.0) if direction[2] < 0 else (0.0, 0.0, -1.0)
            inverses = tuple(0.0 if abs(value) < 1e-15 else 1.0 / value for value in direction)
            def entry(node):
                return box(*origin, *direction, *inverses, scene.node_bounds_min,
                    scene.node_bounds_max, node, 1e-8, math.inf)
            ordered = []
            def visit(node):
                if not math.isfinite(entry(node)):
                    return
                if scene.node_count[node] > 0:
                    start = int(scene.node_start[node])
                    ordered.extend(int(face) for face in scene.ordered_faces[start:start + int(scene.node_count[node])])
                    return
                children = (int(scene.node_left[node]), int(scene.node_right[node]))
                for child in sorted(children, key=entry):
                    visit(child)
            visit(0)
            candidates = tuple((face, 1000.0) for face in reversed(ordered))
            actual = order_cluster_by_original_bvh(candidates, topology, scene, entry)
            self.assertEqual([face for face, _ in actual], ordered)

    def test_raw_triangle_oracle_preserves_legacy_face_and_distance_bits(self):
        canonical, _ = scalar_functions(padded=True)
        box = original_box_function()
        generator = np.random.default_rng(191020)
        accepted = 0
        for gap in (0.0, 5e-11, 1e-10, 2e-10, 5e-10, 9e-10, 1e-6):
            scene = gap_case(gap=gap).mesh.prepare_gpu_cuda_scene()
            topology = build_original_topology(scene)
            for sample in range(400):
                origin = generator.uniform(-12, 12, 3)
                direction = generator.normal(size=3)
                direction /= np.linalg.norm(direction)
                if sample % 2 == 0:
                    origin = np.asarray((0.001, 0.002, 1.0))
                    direction = np.asarray((0.0, 0.0, -1.0))
                maximum = 1.0 if sample % 7 == 0 else math.inf
                ignored = 0 if sample % 4 == 0 else -1
                def query(current):
                    arrays = tuple(getattr(current, name) for name in ('triangle_v0', 'triangle_edge1',
                        'triangle_edge2', 'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
                        'node_start', 'node_count', 'ordered_faces', 'traceable_face_mask'))
                    return canonical(0, current.stack_width, *origin, *direction, 1e-8, maximum,
                        ignored, *arrays, np.empty(current.stack_width), np.empty(current.stack_width, dtype=np.int64))
                raw = []
                for face in scene.ordered_faces:
                    node = int(topology.face_leaf[face])
                    leaf = replace(scene, node_bounds_min=scene.node_bounds_min[[node]],
                        node_bounds_max=scene.node_bounds_max[[node]], node_left=np.asarray([-1]),
                        node_right=np.asarray([-1]), node_start=np.asarray([0]), node_count=np.asarray([1]),
                        ordered_faces=np.asarray([face]))
                    distance, found, _ = query(leaf)
                    if found >= 0:
                        raw.append((found, distance))
                cluster, cap, reason = isolated_cluster(raw, 1e-8, maximum)
                if reason is not None:
                    continue
                inverses = tuple(0.0 if abs(value) < 1e-15 else 1.0 / value for value in direction)
                def entry(node):
                    return box(*origin, *direction, *inverses, scene.node_bounds_min,
                        scene.node_bounds_max, node, 1e-8, cap)
                try:
                    actual = fold_cluster_by_original_bvh(cluster, topology, scene, entry, 1e-8, maximum)
                except ValueError:
                    continue
                expected = query(scene)
                self.assertEqual(actual[1], expected[1], (gap, sample))
                self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64))
                accepted += 1
        self.assertGreater(accepted, 800)


if __name__ == '__main__':
    unittest.main()
