from __future__ import annotations

import ast
import builtins
import copy
import inspect
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import cad_case
from benchmark_perf5_gpu_axis_triangles import axis_triangle_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def scalar_functions():
    original_compile = builtins.compile
    captured = []

    def snapshot(source, filename, mode, *values, **keywords):
        if filename == '<perf5_same_order_axis_triangle_math>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=snapshot):
        axis_triangle_factory()
    functions = []
    for tree in (ast.parse(inspect.getsource(resident._make_kernel)), captured[0]):
        definitions = [node for node in tree.body[0].body if isinstance(node, ast.FunctionDef)
            and node.name in ('ray_box_entry', 'intersect_bvh')]
        for definition in definitions:
            definition.decorator_list = []
        namespace = {'math': math}
        exec(original_compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
            '<axis_triangle_scalar_oracle>', 'exec'), namespace)
        functions.append(namespace['intersect_bvh'])
    return functions


class AxisTriangleScalarTests(unittest.TestCase):
    def test_axis_permutations_signed_zeros_large_coordinates_and_ties_keep_original_bits(self):
        original, candidate = scalar_functions()
        generator = np.random.default_rng(191019)
        for scale in (1.0, 1e10, 1e92):
            for permutation in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
                mesh = gap_case(gap=5e-11).mesh
                mesh.vertices = [tuple(vertex[index] * scale for index in permutation) for vertex in mesh.vertices]
                scene = mesh.prepare_gpu_cuda_scene()
                arrays = tuple(getattr(scene, name) for name in ('triangle_v0', 'triangle_edge1', 'triangle_edge2',
                    'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right', 'node_start',
                    'node_count', 'ordered_faces', 'traceable_face_mask'))
                for index in range(400):
                    origin = generator.uniform(-12, 12, 3) * scale
                    direction = generator.normal(size=3)
                    direction /= np.linalg.norm(direction)
                    if index % 3 == 0:
                        origin = np.asarray([0.0, 0.0, scale])[list(permutation)]
                        direction = np.asarray([-0.0 if index % 2 else 0.0, 0.0, -1.0])[list(permutation)]
                    values = (0, scene.stack_width, *origin, *direction, 1e-8 * scale,
                        math.inf if index % 7 else scale, -1 if index % 4 else 0, *arrays)
                    expected = original(*values, np.empty(scene.stack_width), np.empty(scene.stack_width, dtype=np.int64))
                    actual = candidate(*values, np.empty(scene.stack_width), np.empty(scene.stack_width, dtype=np.int64))
                    self.assertEqual(actual[1:], expected[1:], (scale, permutation, index))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64), (scale, permutation, index))


class AxisTriangleGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')
        cls.factory, cls.fingerprint = axis_triangle_factory()
        cls.states = {mode: {} for mode in ('original', 'axis')}

    def compare(self, trace_input):
        results = []
        for mode in ('original', 'axis'):
            with variant(type(self).factory if mode == 'axis' else resident._make_kernel, type(self).states[mode]):
                results.append(raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host'))
        parity = compare_semantic_payloads(semantic_payload(results[0]), semantic_payload(results[1]))
        self.assertTrue(parity.semantic_exact, parity.to_dict())
        performance = results[1].metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        return results[1]

    def test_microgap_preserves_old_nearest_face_and_material(self):
        self.assertGreater(self.compare(gap_case()).receiver_hit_count, 0)

    def test_cad_paths_and_optical_reedits_remain_bit_exact(self):
        trace_input = cad_case(8193)[0]
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        for reflectance in (0.14, 0.8):
            trace_input.optical_profiles[0].reflectance = reflectance
            self.compare(trace_input)

    def test_actual_thousand_reflections_and_paths_remain_exact(self):
        actual = self.compare(cavity_case())
        self.assertEqual(actual.surface_hit_count, 17 * 1001)


if __name__ == '__main__':
    unittest.main()
