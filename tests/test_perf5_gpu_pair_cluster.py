from __future__ import annotations

import ast
import builtins
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import benchmark_perf5_gpu_isolated_cluster as cluster
import test_perf5_gpu_isolated_cluster as baseline
from benchmark_perf5_gpu_guarded_bvh import dual_scene
from benchmark_perf5_gpu_pair_cluster import pair_factory, selected_factory
from perf5_isolated_hit_cluster import build_original_topology
from test_perf5_guarded_bvh import gap_case


class PairClusterContractTests(unittest.TestCase):
    def test_pair_has_no_local_candidate_arrays_and_keeps_original_fallback_helper(self):
        captured = []
        original_compile = builtins.compile
        def capture(source, filename, mode, *values, **keywords):
            if filename == '<perf5_scalar_pair_cluster_bvh>':
                captured.append(source)
            return original_compile(source, filename, mode, *values, **keywords)
        for slab_full in (False, True):
            with patch.object(builtins, 'compile', side_effect=capture):
                pair_factory(slab_full=slab_full)
            factory = captured[-1].body[0]
            fast = next(node for node in factory.body
                if isinstance(node, ast.FunctionDef) and node.name == 'isolated_intersect_bvh')
            calls = [ast.unparse(node.func) for node in ast.walk(fast) if isinstance(node, ast.Call)]
            self.assertNotIn('cuda.local.array', calls)
            canonical = next(node for node in factory.body
                if isinstance(node, ast.FunctionDef) and node.name == 'canonical_intersect_bvh')
            canonical_calls = [ast.unparse(node.func) for node in ast.walk(canonical) if isinstance(node, ast.Call)]
            self.assertIn('ray_box_entry', canonical_calls)
            self.assertNotIn('pair_ray_box_entry', canonical_calls)
            self.assertEqual('pair_ray_box_entry' in calls, slab_full)

    def test_scalar_pair_matches_original_hits_distance_bits_and_replay_guards(self):
        generator = np.random.default_rng(191022)
        certified = 0
        replayed = 0
        for slab_full in (False, True):
            canonical, candidate = baseline.cluster_scalar_functions(4,
                factory_builder=lambda capacity: pair_factory(slab_full=slab_full),
                generated_filename='<perf5_scalar_pair_cluster_bvh>')
            for gap in (0.0, 5e-11, 1e-10, 2e-10, 5e-10, 9e-10, 1e-6):
                mesh = gap_case(gap=gap).mesh
                topology = build_original_topology(mesh.prepare_gpu_cuda_scene())
                merged, _ = dual_scene(mesh, padded=True)
                arrays = tuple(getattr(merged, name) for name in ('triangle_v0', 'triangle_edge1',
                    'triangle_edge2', 'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
                    'node_start', 'node_count', 'ordered_faces', 'traceable_face_mask'))
                for sample in range(600):
                    origin = generator.uniform(-12, 12, 3)
                    direction = generator.normal(size=3)
                    direction /= np.linalg.norm(direction)
                    if sample % 3 == 0:
                        origin = np.asarray((0.001, 0.002, 1.0))
                        direction = np.asarray((0.0, 0.0, -1.0))
                    elif sample % 5 == 0:
                        direction = np.asarray((1.0, 1e-14, 0.0))
                    values = (0, merged.stack_width, *origin, *direction, 1e-8,
                        1.0 if sample % 7 == 0 else math.inf, 0 if sample % 4 == 0 else -1, *arrays)
                    expected = canonical(*values, np.empty(merged.stack_width),
                        np.empty(merged.stack_width, dtype=np.int64))
                    counters = np.zeros(2, dtype=np.int64)
                    actual = candidate(*values, np.empty(merged.stack_width),
                        np.empty(merged.stack_width, dtype=np.int64), topology.face_leaf, topology.face_order,
                        topology.node_path, topology.node_code, counters)
                    self.assertEqual(actual[1:], expected[1:], (slab_full, gap, sample))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64),
                        (slab_full, gap, sample))
                    certified += int(counters[1])
                    replayed += int(counters[0])
        self.assertGreater(certified, 1000)
        self.assertGreater(replayed, 1000)


class PairClusterGpuTests(baseline.IsolatedClusterGpuTests):
    mode = 'pair'

    def setUp(self):
        self.enterContext(patch.object(cluster, 'isolated_factory', new=selected_factory(self.mode)))


class PairSlabClusterGpuTests(PairClusterGpuTests):
    mode = 'pair_slab'


if __name__ == '__main__':
    unittest.main()
