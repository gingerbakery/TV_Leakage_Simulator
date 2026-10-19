from __future__ import annotations

import ast
import builtins
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

from benchmark_perf5_cpu import WORKLOADS, cad_case
from benchmark_perf5_gpu_guarded_bvh import dual_scene
from benchmark_perf5_gpu_isolated_cluster import cluster_variant, isolated_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf5_isolated_hit_cluster import build_original_topology
from perf4_accuracy import compare_semantic_payloads
from test_optical_transport_audit import clipped_override_case
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer
from leakage_simulator.raytrace_bridge import build_direct_trace_input


def cluster_scalar_functions(capacity, *, factory_builder=isolated_factory,
    generated_filename='<perf5_isolated_hit_cluster_bvh>'):
    captured = []
    original_compile = builtins.compile
    def capture(source, filename, mode, *values, **keywords):
        if filename == generated_filename:
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)
    with patch.object(builtins, 'compile', side_effect=capture):
        factory_builder(capacity)
    names = {'ray_box_entry', 'pair_ray_box_entry', 'canonical_intersect_bvh', 'isolated_intersect_bvh', 'intersect_bvh'}
    definitions = [node for node in captured[0].body[0].body
        if isinstance(node, ast.FunctionDef) and node.name in names]
    for node in definitions:
        node.decorator_list = []
    def atomic_add(values, address, addition):
        previous = values[address]
        values[address] += addition
        return previous
    namespace = {'math': math, 'np': np, 'cuda': SimpleNamespace(
        local=SimpleNamespace(array=lambda size, dtype: np.empty(size, dtype=dtype)),
        atomic=SimpleNamespace(add=atomic_add)),
        'libdevice': SimpleNamespace(clzll=lambda value: 64 - (int(value) & ((1 << 64) - 1)).bit_length())}
    exec(original_compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<perf5_cluster_scalar_oracle>', 'exec'), namespace)
    return namespace['canonical_intersect_bvh'], namespace['intersect_bvh']


class IsolatedClusterFactoryTests(unittest.TestCase):
    def test_capacity_is_explicit_and_bounded(self):
        with self.assertRaises(ValueError):
            isolated_factory(5)

    def test_full_generated_scalar_kernel_matches_original_across_ties_and_receiver_caps(self):
        generator = np.random.default_rng(191021)
        certified = 0
        replayed = 0
        for capacity in (4, 8):
            canonical, candidate = cluster_scalar_functions(capacity)
            for gap in (0.0, 5e-11, 1e-10, 2e-10, 5e-10, 9e-10, 1e-6):
                mesh = gap_case(gap=gap).mesh
                original_scene = mesh.prepare_gpu_cuda_scene()
                topology = build_original_topology(original_scene)
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
                    self.assertEqual(actual[1:], expected[1:], (capacity, gap, sample))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64),
                        (capacity, gap, sample))
                    replayed += int(counters[0])
                    certified += int(counters[1])
        self.assertGreater(certified, 1000)
        self.assertGreater(replayed, 1000)


class IsolatedClusterGpuTests(unittest.TestCase):
    capacity = 8

    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')
        cls.candidate_state = {}
        cls.original_state = {}

    def reference(self, trace_input, **keywords):
        with variant(resident._make_kernel, type(self).original_state):
            return raytracer.run_direct_ray_trace(trace_input, **keywords)

    def assert_parity(self, expected, actual, *, exact=True):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertEqual(expected.stored_paths, actual.stored_paths)
        if exact:
            self.assertTrue(parity.semantic_exact, parity.to_dict())

    def compare(self, trace_input):
        expected = self.reference(trace_input, gpu_accumulator='host')
        with cluster_variant(trace_input, type(self).candidate_state, self.capacity) as evidence:
            actual = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active', performance.get('gpu_resident_wavefront_fallback_reason'))
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assert_parity(expected, actual)
        return actual, evidence

    def test_microgap_and_receiver_caps_keep_exact_original_material(self):
        actual, evidence = self.compare(gap_case(4099))
        self.assertGreater(actual.receiver_hit_count, 0)
        self.assertGreater(evidence['counters'][1], 0)
        for height, receives in ((3e-11, False), (7e-11, True)):
            trace_input = gap_case(4099)
            trace_input.receivers[0].center = (0.0, 0.0, height)
            trace_input.receivers[0].normal = (0.0, 0.0, 1.0)
            self.assertEqual(self.compare(trace_input)[0].receiver_hit_count > 0, receives)

    def test_interference_band_and_candidate_overflow_replay_original_bvh(self):
        _, evidence = self.compare(gap_case(4099, gap=5e-10))
        self.assertGreater(evidence['counters'][0], 0)
        trace_input = gap_case(4099, gap=0.0)
        for _ in range(8):
            mesh = trace_input.mesh
            start = len(mesh.vertices)
            for vertex in ((-10.0, -10.0, 0.0), (10.0, -10.0, 0.0), (0.0, 10.0, 0.0)):
                mesh.add_vertex(vertex)
            mesh.add_face(start, start + 1, start + 2, 'black')
        _, evidence = self.compare(trace_input)
        self.assertGreater(evidence['counters'][0], 0)

    def test_cad_paths_and_optical_reedits_remain_exact(self):
        trace_input = cad_case(8193)[0]
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        for reflectance in (0.14, 0.8):
            trace_input.optical_profiles[0].reflectance = reflectance
            self.compare(trace_input)

    def test_mis_and_roi_custom_scatter_reedits_remain_exact(self):
        trace_input = _build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')
        self.compare(trace_input)
        scene, payload = clipped_override_case(8193)
        trace_input = build_direct_trace_input(scene, payload)
        trace_input.config.compute_backend = 'gpu_cuda'
        for index, scatter in enumerate(('specular', 'lambertian', 'gaussian', 'mixed')):
            profile = trace_input.optical_profiles[1]
            profile.scatter_model = scatter
            profile.reflectance = (0.03, 0.6, 0.99, 0.14)[index]
            profile.specular_ratio = 0.35
            profile.diffuse_ratio = 0.65
            self.compare(trace_input)

    def test_actual_thousand_reflections_retry_retrace_and_stop(self):
        trace_input = cavity_case()
        expected = self.reference(trace_input)
        with cluster_variant(trace_input, type(self).candidate_state, self.capacity), patch.dict(
            os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}
        ):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        self.assertGreater(actual.metrics['_performance_summary']['gpu_event_window_retry_count'], 0)
        stopped = [False]
        original = raytracer.trace_resident_wavefront_gpu_cuda
        def stop_on_overflow(*values, **keywords):
            try:
                return original(*values, **keywords)
            except resident.GpuResidentEventWindowExceeded:
                stopped[0] = True
                raise
        stop_state = {name: type(self).candidate_state[name] for name in ('_KERNEL', '_KERNEL_COMPILED')}
        with cluster_variant(trace_input, stop_state, self.capacity), patch.dict(
            os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(
            raytracer, 'trace_resident_wavefront_gpu_cuda', new=stop_on_overflow
        ):
            actual = raytracer.run_direct_ray_trace(trace_input, should_stop=lambda: stopped[0])
        self.assertTrue(stopped[0])
        self.assertEqual(actual.total_rays, 0)
        self.assertEqual(actual.surface_hit_count, 0)

    def test_multi_leaf_tree_certifies_actual_thousand_reflections(self):
        trace_input = cavity_case()
        for index in range(7):
            mesh = trace_input.mesh
            start = len(mesh.vertices)
            for vertex in ((100.0 + index, 0.0, 0.0), (100.0 + index, 1.0, 0.0),
                (100.5 + index, 0.0, 0.0)):
                mesh.add_vertex(vertex)
            mesh.add_face(start, start + 1, start + 2, 'absorb')
        actual, evidence = self.compare(trace_input)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        self.assertGreaterEqual(evidence['counters'][1], 17 * 1001)

    def test_post_launch_failure_replays_only_uncommitted_original_suffix(self):
        trace_input = WORKLOADS['stochastic_two_bounce'](70019)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        expected = self.reference(trace_input)
        completed = []
        original = resident._launch_resident_kernel
        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
            names = ('origins', 'directions', 'initial_power_lumen', 'source_faces', 'reflection_seeds')
            snapshots = {name: getattr(batch, name).tobytes() for name in names}
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords)
            for name, saved in snapshots.items():
                self.assertEqual(getattr(batch, name).tobytes(), saved)
            completed.append(count)
            if len(completed) == 2:
                raise resident.GpuResidentWavefrontProviderError('execute', 'injected_cluster_post_launch_failure')
            return result
        with patch.object(resident, '_launch_resident_kernel', new=launch), cluster_variant(
            trace_input, type(self).candidate_state, self.capacity
        ):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(completed, [65536, 4483])
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)


class IsolatedCluster4GpuTests(IsolatedClusterGpuTests):
    capacity = 4


if __name__ == '__main__':
    unittest.main()
