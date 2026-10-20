from __future__ import annotations

import ast
import copy
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_gpu_pair_production as baseline
from benchmark_perf5_cpu import cad_case
from perf5_pair_traversal_profile import CountingKernel, counts_valid, make_kernel, profile_trees
from verify_perf5_pair_scalar_oracle import geometry
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def scalar_function():
    resident_tree, pair_tree, fields, _ = profile_trees()
    canonical = copy.deepcopy(next(node for node in resident_tree.body[0].body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh'))
    canonical.name = 'canonical_intersect_bvh'
    definitions = [canonical] + [copy.deepcopy(node) for node in pair_tree.body[0].body
        if isinstance(node, ast.FunctionDef)]
    for definition in definitions:
        definition.decorator_list = []
    namespace = {'math': math, 'np': np, 'PAIR_BVH_MAGIC': pair_scene.PAIR_BVH_MAGIC,
        'ray_box_entry': baseline.original_box_function(),
        'cuda': SimpleNamespace(threadIdx=SimpleNamespace(x=0), activemask=lambda: 1),
        'libdevice': SimpleNamespace(fma_rn=math.fma, ffs=lambda value: (int(value) & -int(value)).bit_length(),
            clzll=lambda value: 64 - (int(value) & ((1 << 64) - 1)).bit_length())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<scalar_current_pair_counts>', 'exec'), namespace)
    return namespace['pair_intersect_bvh'], fields


class PairTraversalScalarTests(unittest.TestCase):
    def test_counters_preserve_exact_hit_and_account_for_every_query(self):
        candidate, fields = scalar_function()
        original = baseline.scalar_pair_functions()[1]
        generator = np.random.default_rng(194900)
        totals = np.zeros((len(fields), 1), dtype=np.int64)
        for scenario, gap in enumerate((0.0, 1.5e-10, 5e-10, 1e-6)):
            mesh, targets = geometry(194800 + scenario, gap, 48)
            with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
                scene = pair_scene.prepare_pair_scene(mesh)[0]
            for sample in range(96):
                center, normal = targets[sample % len(targets)]
                side = 1.0 if sample % 2 else -1.0
                origin = center + side * normal * generator.uniform(0.1, 8.0)
                direction = -side * normal
                if sample % 5 == 0:
                    direction = generator.normal(size=3)
                    direction /= np.linalg.norm(direction)
                maximum = generator.uniform(0.1, 8.0) if sample % 3 == 0 else math.inf
                expected = baseline.query(original, scene, origin, direction, maximum)
                actual = baseline.query(lambda *values: candidate(*values, totals), scene, origin, direction, maximum)
                self.assertEqual(actual[1:], expected[1:])
                self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64))
        counts = {name: int(value) for name, value in zip(fields, totals[:, 0], strict=True)}
        self.assertTrue(counts_valid(counts), counts)
        self.assertEqual(counts['pair_queries'], 384)
        self.assertGreater(counts['fast_replays'], 0)
        self.assertGreater(counts['certified_queries'], 0)
        self.assertGreater(counts['canonical_triangle_tests'], 0)

    def test_counter_invariants_reject_missing_or_negative_results(self):
        _, _, fields, _ = profile_trees()
        counts = {name: 0 for name in fields}
        self.assertTrue(counts_valid(counts))
        counts['pair_queries'] = 1
        self.assertFalse(counts_valid(counts))
        counts['pair_queries'] = 0
        counts['fast_triangle_tests'] = -1
        self.assertFalse(counts_valid(counts))


class PairTraversalGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if (not all(getattr(preflight, name) is True for name in
            ('available', 'strict_float64', 'kernel_executed', 'kernel_verified'))
            or preflight.preflight_scope != 'production_ray_bvh'
            or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'):
            raise RuntimeError('Production CUDA preflight failed')
        cls.kernel, cls.fields, _, _ = make_kernel()
        cls.compiled = False

    def test_current_cad_counts_and_output_stay_consistent(self):
        scene = cad_case(8193)[0]
        scene.config.compute_backend = 'gpu_cuda'
        expected = raytracer.run_direct_ray_trace(scene, gpu_accumulator='host')
        collector = CountingKernel(type(self).kernel, gpu._CUDA, type(self).fields)
        with patch.object(resident, '_PAIR_KERNEL', collector), patch.object(resident, '_PAIR_KERNEL_COMPILED', type(self).compiled):
            actual = raytracer.run_direct_ray_trace(scene, gpu_accumulator='host')
            type(self).compiled = resident._PAIR_KERNEL_COMPILED
        baseline.ProductionPairGpuTests.assert_parity(self, expected, actual, exact=True)
        counts = collector.totals()
        performance = actual.metrics['_performance_summary']
        self.assertGreater(len(collector.samples), 0, performance)
        self.assertTrue(counts_valid(counts), counts)
        self.assertEqual(counts['pair_queries'], performance['gpu_resident_wavefront_logical_intersection_rows'])
        self.assertGreater(counts['fast_triangle_tests'], 0)
        self.assertGreater(counts['certified_queries'], 0)
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertEqual(performance['gpu_cuda_hybrid_cpu_attempt_count'], 0)


if __name__ == '__main__':
    unittest.main()
