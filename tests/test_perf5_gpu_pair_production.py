from __future__ import annotations

import ast
import copy
from dataclasses import replace
import inspect
import math
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_optical_transport_audit import clipped_override_case
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case, scalar_functions
from test_perf5_isolated_hit_cluster import original_box_function
from verify_perf5_pair_scalar_oracle import geometry
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytrace_bridge import build_direct_trace_input


def scalar_pair_functions():
    canonical = scalar_functions(padded=True)[0]
    tree = ast.parse(inspect.getsource(pair_bvh.make_pair_intersector))
    definitions = [copy.deepcopy(node) for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef)]
    for definition in definitions:
        definition.decorator_list = []
    namespace = {'math': math, 'np': np, 'PAIR_BVH_MAGIC': pair_scene.PAIR_BVH_MAGIC,
        'canonical_intersect_bvh': canonical, 'ray_box_entry': original_box_function(),
        'libdevice': SimpleNamespace(fma_rn=math.fma,
            clzll=lambda value: 64 - (int(value) & ((1 << 64) - 1)).bit_length())}
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<production_pair_scalar_oracle>', 'exec'), namespace)
    return canonical, namespace['pair_intersect_bvh'], namespace['isolated_intersect_bvh']


def query(function, scene, origin, direction, maximum=math.inf, ignored=-1):
    arrays = tuple(getattr(scene, name) for name in ('triangle_v0', 'triangle_edge1',
        'triangle_edge2', 'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
        'node_start', 'node_count', 'ordered_faces', 'traceable_face_mask'))
    return function(0, scene.stack_width, *origin, *direction, 1e-8, maximum, ignored,
        *arrays, np.empty(scene.stack_width), np.empty(scene.stack_width, dtype=np.int64))


def add_distant_faces(trace_input, count=7):
    mesh = trace_input.mesh
    material = trace_input.optical_profiles[0].profile_id
    for index in range(count):
        vertices = [mesh.add_vertex(vertex) for vertex in ((100.0 + index, 0.0, 0.0),
            (100.0 + index, 1.0, 0.0), (100.5 + index, 0.0, 0.0))]
        mesh.add_face(*vertices, material)
    return trace_input


class ProductionPairSceneTests(unittest.TestCase):
    def test_auto_keeps_small_scene_and_force_preserves_original_tables(self):
        mesh = gap_case().mesh
        original = mesh.prepare_gpu_cuda_scene()
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'auto'}):
            self.assertIs(pair_scene.prepare_pair_scene(mesh)[0], original)
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            scene, elapsed, reused = pair_scene.prepare_pair_scene(mesh)
        self.assertGreater(elapsed, 0.0)
        self.assertFalse(reused)
        self.assertTrue(pair_scene.has_pair_scene(scene))
        self.assertIs(mesh.prepare_gpu_cuda_scene(), original)
        for name in ('triangle_v0', 'triangle_edge1', 'triangle_edge2', 'traceable_face_mask'):
            self.assertIs(getattr(scene, name), getattr(original, name))
        for name in ('node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
            'node_start', 'node_count'):
            prefix = getattr(scene, name)[:len(original.node_count)].copy()
            if name == 'node_start':
                prefix[0] = original.node_start[0]
            np.testing.assert_array_equal(prefix, getattr(original, name))
            self.assertFalse(getattr(scene, name).flags.writeable)
        self.assertFalse(scene.ordered_faces.flags.writeable)
        topology = pair_scene.build_original_topology(original)
        start = int(scene.ordered_faces[-3])
        count = len(original.triangle_v0)
        np.testing.assert_array_equal(scene.ordered_faces[start:start + count], topology.face_leaf)
        np.testing.assert_array_equal(scene.ordered_faces[start + count:start + 2 * count], topology.face_order)
        codes = scene.ordered_faces[start + 2 * count:start + 2 * count + len(original.node_count)]
        np.testing.assert_array_equal(codes.view(np.uint64), topology.node_code)
        self.assertTrue(np.any(codes < 0))

    def test_cached_topology_is_reused_and_geometry_edits_invalidate_it(self):
        mesh = gap_case().mesh
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            first, _, _ = pair_scene.prepare_pair_scene(mesh)
            again, elapsed, reused = pair_scene.prepare_pair_scene(mesh)
            self.assertIs(first, again)
            self.assertEqual(elapsed, 0.0)
            self.assertTrue(reused)
            vertices = [mesh.add_vertex(vertex) for vertex in ((200, 0, 0), (201, 0, 0), (200, 1, 0))]
            mesh.add_face(*vertices, 'black')
            edited, elapsed, reused = pair_scene.prepare_pair_scene(mesh)
            self.assertIsNot(first, edited)
            self.assertGreater(elapsed, 0.0)
            self.assertFalse(reused)
            self.assertTrue(pair_scene.has_pair_scene(edited))
            self.assertEqual(len(edited.triangle_v0), len(first.triangle_v0) + 1)
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'off'}):
            self.assertIs(pair_scene.prepare_pair_scene(mesh)[0], mesh.prepare_gpu_cuda_scene())

    def test_invalid_modes_and_malformed_headers_are_rejected(self):
        mesh = gap_case().mesh
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'invalid'}):
            with self.assertRaises(ValueError):
                pair_scene.prepare_pair_scene(mesh)
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
            scene = pair_scene.prepare_pair_scene(mesh)[0]
        for address, value in ((-1, 0), (-2, 0), (-2, 64), (-3, 1), (-3, 1000)):
            faces = scene.ordered_faces.copy()
            faces[address] = value
            self.assertFalse(pair_scene.has_pair_scene(replace(scene, ordered_faces=faces)))
        starts = scene.node_start.copy()
        starts[0] = len(scene.node_count)
        self.assertFalse(pair_scene.has_pair_scene(replace(scene, node_start=starts)))

    def test_large_coordinates_skinny_triangles_and_single_leaf_keep_canonical(self):
        large = gap_case().mesh
        large.vertices = [(vertex[0] + 1e7, vertex[1], vertex[2]) for vertex in large.vertices]
        skinny = TriangleMesh()
        for index in range(10):
            vertices = [skinny.add_vertex(vertex) for vertex in ((index, 0, 0),
                (index + 1, 0, 0), (index + 1, 1e-9, 0))]
            skinny.add_face(*vertices, 'black')
        for mesh in (large, skinny, cavity_case().mesh):
            with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
                scene, _, _ = pair_scene.prepare_pair_scene(mesh)
                self.assertIs(scene, mesh.prepare_gpu_cuda_scene())
                self.assertFalse(pair_scene.has_pair_scene(scene))

    def test_native_scalar_helpers_preserve_multilevel_material_and_distance_bits(self):
        canonical, candidate, fast = scalar_pair_functions()
        certified = 0
        replayed = 0
        for scenario, gap in enumerate((0.0, 5e-11, 1.5e-10, 5e-10, 1e-6)):
            mesh, targets = geometry(191100 + scenario, gap, 48)
            original = mesh.prepare_gpu_cuda_scene()
            with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'force'}):
                scene = pair_scene.prepare_pair_scene(mesh)[0]
            self.assertTrue(pair_scene.has_pair_scene(scene))
            generator = np.random.default_rng(193100 + scenario)
            for sample in range(800):
                origin = generator.uniform(-18, 18, 3)
                direction = generator.normal(size=3)
                direction /= np.linalg.norm(direction)
                distance_to_target = generator.uniform(0.1, 5.0)
                if sample % 3:
                    center, normal = targets[sample % len(targets)]
                    side = 1.0 if sample % 2 else -1.0
                    origin = center + side * normal * distance_to_target
                    direction = -side * normal
                if sample % 11 == 0:
                    direction = np.asarray((1.0, 1e-14, 0.0))
                maximum = distance_to_target if sample % 7 == 0 else math.inf
                ignored = int(generator.integers(len(mesh.faces))) if sample % 5 == 0 else -1
                expected = query(canonical, original, origin, direction, maximum, ignored)
                actual = query(candidate, scene, origin, direction, maximum, ignored)
                self.assertEqual(actual[1:], expected[1:], (gap, sample))
                self.assertEqual(np.float64(actual[0]).view(np.uint64),
                    np.float64(expected[0]).view(np.uint64), (gap, sample))
                status = query(fast, scene, origin, direction, maximum, ignored)[2]
                certified += int(status == 0)
                replayed += int(status != 0)
        self.assertGreater(certified, 300)
        self.assertGreater(replayed, 300)


class ProductionPairGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in
            ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def run_mode(self, trace_input, mode, **keywords):
        with patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': mode}):
            return raytracer.run_direct_ray_trace(trace_input, **keywords)

    def assert_parity(self, expected, actual, *, exact=True):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertEqual(expected.stored_paths, actual.stored_paths)
        if exact:
            self.assertTrue(parity.semantic_exact, parity.to_dict())

    def compare(self, trace_input, *, mode='force', accumulator='host'):
        expected = self.run_mode(trace_input, 'off', gpu_accumulator=accumulator)
        actual = self.run_mode(trace_input, mode, gpu_accumulator=accumulator)
        self.assert_parity(expected, actual, exact=accumulator == 'host')
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active', performance)
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertGreater(performance['gpu_pair_bvh_selected_batches'], 0)
        self.assertEqual(performance['gpu_pair_bvh_contract'], pair_scene.PAIR_BVH_CONTRACT)
        return actual

    def test_microgap_and_receiver_caps_preserve_exact_material(self):
        self.assertGreater(self.compare(gap_case(4099)).receiver_hit_count, 0)
        for height, receives in ((3e-11, False), (7e-11, True)):
            trace_input = gap_case(4099)
            trace_input.receivers[0].center = (0.0, 0.0, height)
            trace_input.receivers[0].normal = (0.0, 0.0, 1.0)
            self.assertEqual(self.compare(trace_input).receiver_hit_count > 0, receives)

    def test_interference_band_and_candidate_overflow_preserve_exact_transport(self):
        self.compare(gap_case(4099, gap=5e-10))
        trace_input = gap_case(4099, gap=0.0)
        for _ in range(8):
            vertices = [trace_input.mesh.add_vertex(vertex) for vertex in
                ((-10, -10, 0), (10, -10, 0), (0, 10, 0))]
            trace_input.mesh.add_face(*vertices, 'black')
        self.compare(trace_input)

    def test_auto_cad_paths_and_optical_reedits_reuse_only_immutable_geometry(self):
        trace_input = cad_case(8193)[0]
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        for index, reflectance in enumerate((0.14, 0.8)):
            trace_input.optical_profiles[0].reflectance = reflectance
            result = self.compare(trace_input, mode='auto')
            elapsed = result.metrics['_performance_summary']['gpu_pair_scene_prepare_sec']
            if index == 0:
                self.assertGreater(elapsed, 0.0)
            else:
                self.assertEqual(elapsed, 0.0)

    def test_mis_partial_tail_and_roi_custom_surface_reedits_remain_exact(self):
        self.compare(add_distant_faces(_build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')))
        scene, payload = clipped_override_case(8193)
        trace_input = add_distant_faces(build_direct_trace_input(scene, payload))
        trace_input.config.compute_backend = 'gpu_cuda'
        for index, scatter in enumerate(('specular', 'lambertian', 'gaussian', 'mixed')):
            profile = trace_input.optical_profiles[1]
            profile.scatter_model = scatter
            profile.reflectance = (0.03, 0.6, 0.99, 0.14)[index]
            profile.specular_ratio = 0.35
            profile.diffuse_ratio = 0.65
            self.compare(trace_input)

    def test_actual_thousand_reflections_retry_retrace_and_stop(self):
        trace_input = add_distant_faces(cavity_case())
        expected = self.run_mode(trace_input, 'off')
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(
            resident, '_WORKSPACES', threading.local()
        ):
            actual = self.run_mode(trace_input, 'force')
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        performance = actual.metrics['_performance_summary']
        self.assertGreater(performance['gpu_event_window_retry_count'], 0)
        self.assertGreater(performance['gpu_pair_bvh_selected_batches'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        stopped = [False]
        original = raytracer.trace_resident_wavefront_gpu_cuda
        def stop_on_overflow(*values, **keywords):
            try:
                return original(*values, **keywords)
            except resident.GpuResidentEventWindowExceeded:
                stopped[0] = True
                raise
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(
            resident, '_WORKSPACES', threading.local()), patch.object(
            raytracer, 'trace_resident_wavefront_gpu_cuda', new=stop_on_overflow
        ):
            actual = self.run_mode(trace_input, 'force', should_stop=lambda: stopped[0])
        self.assertTrue(stopped[0])
        self.assertEqual(actual.total_rays, 0)
        self.assertEqual(actual.surface_hit_count, 0)

    def test_post_launch_failure_replays_only_uncommitted_original_suffix(self):
        trace_input = add_distant_faces(WORKLOADS['stochastic_two_bounce'](70019))
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        expected = self.run_mode(trace_input, 'off')
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
                raise resident.GpuResidentWavefrontProviderError('execute', 'injected_native_pair_post_launch_failure')
            return result
        with patch.object(resident, '_launch_resident_kernel', new=launch):
            actual = self.run_mode(trace_input, 'force')
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(completed, [65536, 4483])
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_pair_bvh_selected_batches'], 1)

    def test_small_auto_and_cpu_do_not_prepare_or_select_pair_kernel(self):
        trace_input = WORKLOADS['face_direct'](8193)
        trace_input.config.compute_backend = 'gpu_cuda'
        result = self.run_mode(trace_input, 'auto')
        performance = result.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_pair_bvh_selected_batches'], 0)
        trace_input.config.compute_backend = 'cpu'
        with patch.object(pair_scene, 'prepare_pair_scene', side_effect=AssertionError('CPU prepared a GPU scene')):
            result = raytracer.run_direct_ray_trace(trace_input)
        self.assertEqual(result.metrics['_performance_summary']['gpu_pair_bvh_selected_batches'], 0)


if __name__ == '__main__':
    unittest.main()
