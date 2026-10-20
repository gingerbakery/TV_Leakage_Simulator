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
from perf5_pair_stack import WidthGuard, make_kernel, stack_tree
from verify_perf5_pair_scalar_oracle import geometry
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def scalar_functions(prefix, immediate):
    canonical, _, _ = baseline.scalar_pair_functions()
    tree = stack_tree(prefix, immediate=immediate)
    definitions = [copy.deepcopy(node) for node in tree.body[0].body if isinstance(node, ast.FunctionDef)]
    for definition in definitions:
        definition.decorator_list = []
    namespace = {'math': math, 'np': np, 'PAIR_BVH_MAGIC': pair_scene.PAIR_BVH_MAGIC,
        'canonical_intersect_bvh': canonical, 'ray_box_entry': baseline.original_box_function(),
        'cuda': SimpleNamespace(threadIdx=SimpleNamespace(x=0),
            shared=SimpleNamespace(array=lambda shape, dtype: np.empty(shape, dtype=dtype))),
        'libdevice': SimpleNamespace(fma_rn=math.fma,
            clzll=lambda value: 64 - (int(value) & ((1 << 64) - 1)).bit_length())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<scalar_pair_stack_oracle>', 'exec'), namespace)
    return namespace['pair_intersect_bvh'], namespace['isolated_intersect_bvh']


class PairStackScalarTests(unittest.TestCase):
    def test_all_candidates_keep_original_hit_distance_face_and_replay_status(self):
        _, reference, reference_fast = baseline.scalar_pair_functions()
        candidates = [scalar_functions(4, False), scalar_functions(8, False), scalar_functions(0, True)]
        generator = np.random.default_rng(191922)
        for scenario, gap in enumerate((0.0, 5e-11, 1.5e-10, 5e-10, 1e-6)):
            mesh, targets = geometry(192000 + scenario, gap, 48)
            with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
                scene = pair_scene.prepare_pair_scene(mesh)[0]
            self.assertTrue(pair_scene.has_pair_scene(scene))
            for sample in range(96):
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
                maximum = math.inf if sample % 3 else float(generator.uniform(1.0, 8.0))
                ignored = int(generator.integers(len(mesh.faces))) if sample % 5 == 0 else -1
                for expected_function, position in ((reference, 0), (reference_fast, 1)):
                    expected = baseline.query(expected_function, scene, origin, direction, maximum, ignored)
                    for functions in candidates:
                        actual = baseline.query(functions[position], scene, origin, direction, maximum, ignored)
                        self.assertEqual(actual[1:], expected[1:])
                        self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64))

    def test_invalid_prefix_and_oversized_blocks_fail_closed(self):
        with self.assertRaises(ValueError):
            stack_tree(3)
        guard = WidthGuard(None, 128)
        for shape in ((1, 256), (1, (8, 16)), (1, 0)):
            with self.assertRaises(ValueError):
                guard[shape]


class PairShared4GpuTests(baseline.ProductionPairGpuTests):
    prefix = 4
    immediate = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kernel = make_kernel(cls.prefix, immediate=cls.immediate)
        cls.compiled = False

    def setUp(self):
        self.enterContext(patch.object(resident, '_PAIR_KERNEL', type(self).kernel))
        self.enterContext(patch.object(resident, '_PAIR_KERNEL_COMPILED', type(self).compiled))

    def tearDown(self):
        type(self).compiled = resident._PAIR_KERNEL_COMPILED


class PairShared8GpuTests(PairShared4GpuTests):
    prefix = 8


class PairImmediateGpuTests(PairShared4GpuTests):
    prefix = 0
    immediate = True


if __name__ == '__main__':
    unittest.main()
