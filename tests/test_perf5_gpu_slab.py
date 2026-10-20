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
from benchmark_perf5_gpu_slab import slab_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def scalar_functions(*, full=False):
    original_compile = builtins.compile
    captured = []

    def snapshot(source, filename, mode, *values, **keywords):
        if filename == '<perf5_same_order_nonparallel_slabs>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=snapshot):
        slab_factory(full=full)
    functions = []
    for tree in (ast.parse(inspect.getsource(resident._make_kernel)), captured[0]):
        definitions = [node for node in tree.body[0].body if isinstance(node, ast.FunctionDef)
            and node.name in ('general_ray_box_entry', 'ray_box_entry', 'intersect_bvh')]
        for definition in definitions:
            definition.decorator_list = []
        namespace = {'math': math}
        exec(original_compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
            '<same_order_slab_scalar_oracle>', 'exec'), namespace)
        functions.append(namespace)
    return functions


class SlabScalarTests(unittest.TestCase):
    def test_nearest_face_ties_boundaries_and_receiver_caps_keep_original_distance_bits(self):
        generator = np.random.default_rng(191020)
        for full in (False, True):
            original, candidate = scalar_functions(full=full)
            for gap in (0.0, 5e-11, 1e-10, 2e-10, 1e-6):
                scene = gap_case(gap=gap).mesh.prepare_gpu_cuda_scene()
                arrays = tuple(getattr(scene, name) for name in ('triangle_v0', 'triangle_edge1', 'triangle_edge2',
                    'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right', 'node_start',
                    'node_count', 'ordered_faces', 'traceable_face_mask'))
                for index in range(600):
                    origin = generator.uniform(-12, 12, 3)
                    direction = generator.normal(size=3)
                    direction /= np.linalg.norm(direction)
                    if index % 3 == 0:
                        origin = np.asarray([0.0, 0.0, 1.0])
                        direction = np.asarray([0.0, -0.0, -1.0])
                    elif index % 5 == 0:
                        direction = np.asarray([1.0, 1e-14, 0.0])
                    values = (0, scene.stack_width, *origin, *direction, 1e-8,
                        math.inf if index % 7 else 1.0, -1 if index % 4 else 0, *arrays)
                    expected = original['intersect_bvh'](*values, np.empty(scene.stack_width), np.empty(scene.stack_width, dtype=np.int64))
                    actual = candidate['intersect_bvh'](*values, np.empty(scene.stack_width), np.empty(scene.stack_width, dtype=np.int64))
                    self.assertEqual(actual[1:], expected[1:], (full, gap, index))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64), (full, gap, index))

    def test_box_entries_keep_zero_parallel_threshold_and_large_coordinate_behavior(self):
        generator = np.random.default_rng(191021)
        for full in (False, True):
            original, candidate = scalar_functions(full=full)
            for index in range(2000):
                scale = (1e-290, 1.0, 1e90, 1e308)[index % 4]
                lower = generator.uniform(-0.4, 0.0, 3) * scale
                upper = generator.uniform(0.0, 0.4, 3) * scale
                origin = generator.uniform(-0.5, 0.5, 3) * scale
                direction = generator.normal(size=3)
                direction /= np.linalg.norm(direction)
                if index % 3 == 0:
                    direction[0] = (-0.0, 1e-15, 1e-12, -1e-12)[index % 4]
                inverse = np.asarray([0.0 if abs(value) < 1e-15 else 1.0 / value for value in direction])
                values = (*origin, *direction, *inverse, lower[None, :], upper[None, :], 0, 0.0, math.inf)
                expected = original['ray_box_entry'](*values)
                actual = candidate['ray_box_entry'](*values, bool(np.all(np.abs(direction) >= 1e-12)))
                self.assertEqual(np.float64(actual).view(np.uint64), np.float64(expected).view(np.uint64), (full, index))


class SlabGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')
        cls.factories = {'original': resident._make_kernel, 'early': slab_factory()[0], 'full': slab_factory(full=True)[0]}
        cls.states = {mode: {} for mode in cls.factories}

    def compare(self, trace_input):
        expected = None
        for mode, factory in type(self).factories.items():
            with variant(factory, type(self).states[mode]):
                result = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
            payload = semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload)
            self.assertTrue(parity.semantic_exact, parity.to_dict())
            performance = result.metrics['_performance_summary']
            self.assertEqual(performance['compute_execution_state'], 'gpu_active')
            self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
            self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        return result

    def test_microgap_keeps_old_face_and_material_selection(self):
        self.assertGreater(self.compare(gap_case()).receiver_hit_count, 0)

    def test_cad_paths_and_reedited_properties_remain_exact(self):
        trace_input = cad_case(8193)[0]
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        for reflectance in (0.14, 0.8):
            trace_input.optical_profiles[0].reflectance = reflectance
            self.compare(trace_input)

    def test_actual_thousand_reflections_and_partial_mis_tail_are_exact(self):
        actual = self.compare(cavity_case())
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        trace_input = _build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        self.compare(trace_input)


if __name__ == '__main__':
    unittest.main()
