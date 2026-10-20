from __future__ import annotations

import ast
import copy
import inspect
import math
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_gpu_bvh_kernel import early_barycentric_reject
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def traversal_functions():
    source = ast.parse(inspect.getsource(resident._make_kernel))
    definition = next(node for node in ast.walk(source)
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    box = next(node for node in ast.walk(source)
        if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry')
    functions = []
    for early in (False, True):
        candidate = copy.deepcopy(definition)
        if early:
            candidate = early_barycentric_reject(candidate)
        candidate.decorator_list = []
        helper = copy.deepcopy(box)
        helper.decorator_list = []
        namespace = {'math': math, 'np': np}
        module = ast.fix_missing_locations(ast.Module(body=[helper, candidate], type_ignores=[]))
        exec(compile(module, '<perf5_barycentric_host_oracle>', 'exec'), namespace)
        functions.append(namespace['intersect_bvh'])
    return functions


class BarycentricProbeTests(unittest.TestCase):
    def test_delayed_inverse_preserves_faces_and_distance_bits_near_boundaries(self):
        original, candidate = traversal_functions()
        generator = np.random.default_rng(711049)
        for index in range(3000):
            scale = float(np.exp2(generator.uniform(-10, 20)))
            vertices = generator.normal(size=(1, 3)) * scale
            edge1 = np.asarray([[scale, 0.0, 0.0]])
            edge2 = np.asarray([[0.0, scale, 0.0]])
            barycentric = generator.uniform(-1, 2, 2)
            if index % 3 == 0:
                boundary = (0.0, 1.0)[index % 2]
                barycentric[0] = boundary + generator.choice((-1, 1)) * 10.0 ** -generator.integers(8, 17)
            point = vertices[0] + barycentric[0] * edge1[0] + barycentric[1] * edge2[0]
            direction = np.asarray([0.0, 0.0, 1.0 if index % 2 else -1.0])
            origin = point - direction * scale
            bounds_min = vertices - 2.0 * scale
            bounds_max = vertices + 3.0 * scale
            values = (0, 4, *origin, *direction, 1e-12, math.inf, -1,
                vertices, edge1, edge2, bounds_min, bounds_max,
                np.asarray([-1]), np.asarray([-1]), np.asarray([0]), np.asarray([1]),
                np.asarray([0]), np.asarray([True]))
            expected = original(*values, np.empty(4), np.empty(4, dtype=np.int64))
            actual = candidate(*values, np.empty(4), np.empty(4, dtype=np.int64))
            self.assertEqual(actual[1:], expected[1:], index)
            self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64), index)

    def test_conservative_rejection_never_discards_accepted_rounded_coordinate(self):
        generator = np.random.default_rng(228112)
        determinants = np.exp2(generator.uniform(-26, 1023, 200000))
        determinants *= generator.choice((-1.0, 1.0), len(determinants))
        coordinates = generator.uniform(-2e-12, 1.0 + 2e-12, len(determinants))
        coordinates[:100000] = generator.choice((0.0, 1.0), 100000) + generator.normal(0, 1e-12, 100000)
        numerators = determinants * coordinates
        signed = np.where(determinants > 0.0, numerators, -numerators)
        magnitude = np.abs(determinants)
        with np.errstate(over='ignore', under='ignore'):
            rejected = (signed < -magnitude * 1e-12) | (signed > magnitude * (1.0 + 1e-12))
            original = numerators * (1.0 / determinants)
        self.assertFalse(np.any(rejected & (original >= 0.0) & (original <= 1.0)))
        self.assertGreater(np.count_nonzero(rejected), 1000)


if __name__ == '__main__':
    unittest.main()
