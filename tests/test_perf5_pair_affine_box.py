from __future__ import annotations

import ast
import copy
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from perf5_pair_affine_box import EXTRA_PADDING_MM, affine_pair_factory, affine_tree, expanded_scene, padded_scene_variant
from perf5_pair_masked_box import masked_pair_factory, masked_tree
import test_perf5_gpu_pair_production as baseline
from test_perf5_isolated_hit_cluster import original_box_function
from test_perf5_guarded_bvh import scalar_functions
from verify_perf5_pair_scalar_oracle import geometry
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def scalar_affine_functions(*, padded=False, masked=False):
    tree = masked_tree(affine=True, padded=padded) if masked else affine_tree(padded=padded)
    definitions = [copy.deepcopy(node) for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef)]
    for definition in definitions:
        definition.decorator_list = []
    canonical = scalar_functions(padded=True)[0]
    namespace = {'math': math, 'np': np, 'PAIR_BVH_MAGIC': pair_scene.PAIR_BVH_MAGIC,
        'canonical_intersect_bvh': canonical, 'ray_box_entry': original_box_function(),
        'libdevice': SimpleNamespace(fma_rn=math.fma,
            clzll=lambda value: 64 - (int(value) & ((1 << 64) - 1)).bit_length())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<perf5_affine_scalar_oracle>', 'exec'), namespace)
    box_name = 'masked_ray_box_entry' if masked else 'affine_ray_box_entry'
    return canonical, namespace['pair_intersect_bvh'], namespace[box_name]


class AffineBoxTests(unittest.TestCase):
    padded = False
    masked = False

    def test_interval_never_rejects_or_late_orders_an_original_finite_box(self):
        _, _, candidate = scalar_affine_functions(padded=self.padded, masked=self.masked)
        canonical = original_box_function()
        generator = np.random.default_rng(191400)
        finite = 0
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
            biases = []
            for axis in range(3):
                bound = abs(lower[0, axis]) + abs(upper[0, axis]) + 2.0
                margin = (abs(origin[axis]) + bound) * abs(inverses[axis]) * 2.842170943040401e-14
                if self.padded:
                    margin = 0.0
                center_bias = -origin[axis] * inverses[axis]
                biases.extend((center_bias - margin, center_bias + margin))
            maximum = 1.0 if sample % 3 == 0 else math.inf
            values = (*origin, *direction, *inverses, lower, upper, 0, 1e-8, maximum)
            expected = canonical(*values)
            candidate_lower = lower - EXTRA_PADDING_MM if self.padded else lower
            candidate_upper = upper + EXTRA_PADDING_MM if self.padded else upper
            masks = ()
            if self.masked:
                masks = (
                    np.int32(sum(1 << axis for axis in range(3) if abs(direction[axis]) < 1e-12)),
                    np.int32(sum(1 << axis for axis in range(3) if inverses[axis] < 0.0)),
                )
            actual = candidate(*origin, *direction, *inverses, candidate_lower,
                candidate_upper, 0, 1e-8, maximum, *biases, *masks)
            if math.isfinite(expected):
                finite += 1
                self.assertTrue(math.isfinite(actual), sample)
                self.assertLessEqual(actual, expected, sample)
        self.assertGreater(finite, 3000)

    def test_scalar_transport_keeps_exact_material_and_distance_bits(self):
        canonical, candidate, _ = scalar_affine_functions(padded=self.padded, masked=self.masked)
        generator = np.random.default_rng(191401)
        for gap in (0.0, 5e-11, 1.5e-10, 5e-10, 1e-6):
            mesh, targets = geometry(191100, gap, 48)
            original = mesh.prepare_gpu_cuda_scene()
            with patch.dict('os.environ', {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
                scene = pair_scene.prepare_pair_scene(mesh)[0]
            if self.padded:
                scene = expanded_scene(scene)
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


class AffineBoxGpuTests(baseline.ProductionPairGpuTests):
    padded = False
    masked = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        factory = masked_pair_factory(affine=True, padded=cls.padded) if cls.masked else affine_pair_factory(padded=cls.padded)
        cls.kernel = resident._make_kernel(_intersector_factory=factory)
        cls.compiled = False

    def setUp(self):
        self.enterContext(patch.object(resident, '_PAIR_KERNEL', type(self).kernel))
        self.enterContext(patch.object(resident, '_PAIR_KERNEL_COMPILED', type(self).compiled))
        if self.padded:
            self.enterContext(padded_scene_variant({}))

    def tearDown(self):
        type(self).compiled = resident._PAIR_KERNEL_COMPILED


class AffinePaddedBoxTests(AffineBoxTests):
    padded = True


class AffinePaddedBoxGpuTests(AffineBoxGpuTests):
    padded = True


class MaskedAffineBoxTests(AffineBoxTests):
    masked = True


class MaskedAffineBoxGpuTests(AffineBoxGpuTests):
    masked = True


class MaskedAffinePaddedBoxTests(MaskedAffineBoxTests):
    padded = True


class MaskedAffinePaddedBoxGpuTests(MaskedAffineBoxGpuTests):
    padded = True


if __name__ == '__main__':
    unittest.main()
