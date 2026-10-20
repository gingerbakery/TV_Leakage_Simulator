from __future__ import annotations

import ast
import copy
import hashlib
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from perf5_pair_masked_box import masked_pair_factory, masked_tree
from perf5_pair_source import original_pair_tree
import test_perf5_gpu_pair_production as baseline
from test_perf5_isolated_hit_cluster import original_box_function
from test_perf5_guarded_bvh import scalar_functions
from verify_perf5_pair_scalar_oracle import geometry
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def scalar_masked_functions():
    definitions = [copy.deepcopy(node) for node in masked_tree().body[0].body
        if isinstance(node, ast.FunctionDef)]
    for definition in definitions:
        definition.decorator_list = []
    canonical = scalar_functions(padded=True)[0]
    namespace = {'math': math, 'np': np, 'PAIR_BVH_MAGIC': pair_scene.PAIR_BVH_MAGIC,
        'canonical_intersect_bvh': canonical, 'ray_box_entry': original_box_function(),
        'libdevice': SimpleNamespace(clzll=lambda value: 64 - (int(value) & ((1 << 64) - 1)).bit_length())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<perf5_masked_scalar_oracle>', 'exec'), namespace)
    return canonical, namespace['pair_intersect_bvh'], namespace['masked_ray_box_entry']


class MaskedBoxTests(unittest.TestCase):
    def test_experimental_transformations_keep_the_original_pair_oracle(self):
        digest = hashlib.sha256(ast.dump(original_pair_tree()).encode()).hexdigest()
        self.assertEqual(digest, '98f68541862458b76b15d2271d57379ae447656609ab2b56b0997f307e53a2be')

    def test_cached_direction_predicates_keep_exact_box_entry_bits(self):
        _, _, candidate = scalar_masked_functions()
        canonical = original_box_function()
        generator = np.random.default_rng(191500)
        for sample in range(20000):
            scale = (1e-10, 1.0, 1e3, 9e5)[sample % 4]
            center = generator.uniform(-scale, scale, 3)
            extent = generator.uniform(0.0, 1.0, 3) * scale
            lower = np.asarray([center - extent])
            upper = np.asarray([center + extent])
            origin = center + generator.uniform(-3.0, 3.0, 3) * scale
            direction = generator.normal(size=3)
            direction /= np.linalg.norm(direction)
            if sample % 7 == 0:
                direction[sample % 3] = 0.0
            elif sample % 11 == 0:
                direction[sample % 3] = 1e-12
            if sample % 3 == 0:
                origin = center.copy()
            if sample % 5 == 0:
                origin = lower[0].copy()
            inverses = [0.0 if abs(value) < 1e-15 else 1.0 / value for value in direction]
            parallel = sum(1 << axis for axis in range(3) if abs(direction[axis]) < 1e-12)
            negative = sum(1 << axis for axis in range(3) if inverses[axis] < 0.0)
            maximum = 1.0 if sample % 3 == 0 else math.inf
            values = (*origin, *direction, *inverses, lower, upper, 0, 1e-8, maximum)
            expected = canonical(*values)
            actual = candidate(*values, np.int32(parallel), np.int32(negative))
            self.assertEqual(np.float64(actual).view(np.uint64), np.float64(expected).view(np.uint64), sample)

    def test_scalar_transport_keeps_exact_original_face_and_distance_bits(self):
        canonical, candidate, _ = scalar_masked_functions()
        generator = np.random.default_rng(191501)
        for gap in (0.0, 5e-11, 1.5e-10, 5e-10, 1e-6):
            mesh, targets = geometry(191100, gap, 48)
            original = mesh.prepare_gpu_cuda_scene()
            with patch.dict('os.environ', {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
                scene = pair_scene.prepare_pair_scene(mesh)[0]
            for sample in range(800):
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
                actual = baseline.query(candidate, scene, origin, direction, maximum, ignored)
                self.assertEqual(actual[1:], expected[1:], (gap, sample))
                self.assertEqual(np.float64(actual[0]).view(np.uint64),
                    np.float64(expected[0]).view(np.uint64), (gap, sample))


class MaskedBoxGpuTests(baseline.ProductionPairGpuTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kernel = resident._make_kernel(_intersector_factory=masked_pair_factory())
        cls.compiled = False

    def setUp(self):
        self.enterContext(patch.object(resident, '_PAIR_KERNEL', type(self).kernel))
        self.enterContext(patch.object(resident, '_PAIR_KERNEL_COMPILED', type(self).compiled))

    def tearDown(self):
        type(self).compiled = resident._PAIR_KERNEL_COMPILED


if __name__ == '__main__':
    unittest.main()
