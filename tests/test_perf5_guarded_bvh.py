from __future__ import annotations

import ast
import builtins
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

from benchmark_perf5_cpu import cad_case
from benchmark_perf5_gpu_guarded_bvh import GuardedLaunch, dual_scene, guarded_factory, unsafe_leaf_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf5_gpu_event_window import cavity_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec


def gap_case(rays=4096, gap=5e-11):
    mesh = TriangleMesh()
    for height, material in ((0.0, 'mirror'), (gap, 'black')):
        start = len(mesh.vertices)
        for vertex in ((-10.0, -10.0, height), (10.0, -10.0, height), (0.0, 10.0, height)):
            mesh.add_vertex(vertex)
        mesh.add_face(start, start + 1, start + 2, material)
    for index in range(7):
        start = len(mesh.vertices)
        for vertex in ((100.0 + index, 0.0, 0.0), (100.0 + index, 1.0, 0.0), (100.5 + index, 0.0, 0.0)):
            mesh.add_vertex(vertex)
        mesh.add_face(start, start + 1, start + 2, 'black')
    return raytracer.DirectRayTraceInput(mesh=mesh,
        emitters=[EmitterSpec(emitter_id='source', emitter_type='datum_plane', center=(0.0, 0.0, 1.0),
            u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, -1.0, 0.0), width_mm=0.01, height_mm=0.01,
            direction_distribution='gaussian', gaussian_sigma_deg=1e-5, power_lumen=1.0, ray_count=rays, seed=9191)],
        receivers=[ReceiverSpec(receiver_id='observer', center=(0.0, 0.0, 2.0), normal=(0.0, 0.0, -1.0),
            width_mm=1.0, height_mm=1.0, resolution=(4, 4))],
        optical_profiles=[OpticalProfile('mirror', 0.8, scatter_model='specular'),
            OpticalProfile('black', 0.0, scatter_model='none')],
        config=RayTraceConfig(ray_count=rays, max_depth=1, epsilon_mm=1e-6, min_energy=0.0,
            angle_dependent_reflectance=False, compute_backend='gpu_cuda', intersection_backend='bvh',
            store_ray_paths=True, max_stored_paths=100))


def scalar_functions(*, padded=False):
    captured = []
    original_compile = builtins.compile

    def compile_snapshot(source, filename, mode, *values, **keywords):
        if filename == '<perf5_guarded_dual_bvh>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=compile_snapshot):
        guarded_factory(padded=padded)
    names = {'ray_box_entry', 'guarded_ray_box_entry', 'canonical_intersect_bvh', 'fast_intersect_bvh', 'intersect_bvh'}
    definitions = [node for node in captured[0].body[0].body if isinstance(node, ast.FunctionDef) and node.name in names]
    for definition in definitions:
        definition.decorator_list = []

    def atomic_add(values, address, addition):
        previous = values[address]
        values[address] += addition
        return previous

    namespace = {'math': math, 'np': np, 'cuda': SimpleNamespace(atomic=SimpleNamespace(add=atomic_add))}
    exec(original_compile(ast.fix_missing_locations(ast.Module(body=definitions, type_ignores=[])),
        '<guarded_bvh_scalar_oracle>', 'exec'), namespace)
    return namespace['canonical_intersect_bvh'], namespace['intersect_bvh']


class GuardedBvhLayoutTests(unittest.TestCase):
    def test_dual_scene_preserves_legacy_tables_and_original_cached_scene(self):
        mesh = gap_case().mesh
        original = mesh.prepare_gpu_cuda_scene()
        snapshot = {name: getattr(original, name).copy() for name in ('node_bounds_min', 'node_bounds_max',
            'node_left', 'node_right', 'node_start', 'node_count', 'ordered_faces')}
        merged, _ = dual_scene(mesh)
        self.assertIs(mesh.prepare_gpu_cuda_scene(), original)
        self.assertEqual(merged.node_start[0], len(original.node_count))
        self.assertGreater(merged.stack_width, original.stack_width)
        for name, values in snapshot.items():
            np.testing.assert_array_equal(getattr(original, name), values)
            prefix = getattr(merged, name)[:len(values)].copy()
            if name == 'node_start':
                prefix[0] = values[0]
            np.testing.assert_array_equal(prefix, values)
            self.assertFalse(getattr(merged, name).flags.writeable)
        np.testing.assert_array_equal(merged.traceable_face_mask, original.traceable_face_mask)

    def test_large_coordinate_and_ill_conditioned_geometry_keep_legacy_scene(self):
        mesh = gap_case().mesh
        mesh.vertices = [(vertex[0] + 1e7, vertex[1], vertex[2]) for vertex in mesh.vertices]
        original = mesh.prepare_gpu_cuda_scene()
        self.assertIs(dual_scene(mesh)[0], original)
        skinny = TriangleMesh()
        for index in range(10):
            start = len(skinny.vertices)
            for vertex in ((float(index), 0.0, 0.0), (index + 1.0, 0.0, 0.0), (index + 1.0, 1e-9, 0.0)):
                skinny.add_vertex(vertex)
            skinny.add_face(start, start + 1, start + 2, 'black')
        original = skinny.prepare_gpu_cuda_scene()
        self.assertIs(dual_scene(skinny)[0], original)

    def test_scalar_oracle_preserves_ties_boundaries_misses_and_ignored_faces(self):
        generator = np.random.default_rng(191016)
        total_replays = 0
        for padded in (False, True):
            canonical, guarded = scalar_functions(padded=padded)
            for gap in (0.0, 5e-11, 1e-10, 2e-10, 1e-6):
                merged, _ = dual_scene(gap_case(gap=gap).mesh, padded=padded)
                arrays = tuple(getattr(merged, name) for name in ('triangle_v0', 'triangle_edge1', 'triangle_edge2',
                    'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right', 'node_start',
                    'node_count', 'ordered_faces', 'traceable_face_mask'))
                for index in range(600):
                    origin = generator.uniform(-12, 12, 3)
                    direction = generator.normal(size=3)
                    direction /= np.linalg.norm(direction)
                    if index % 3 == 0:
                        origin = np.asarray([0.0, 0.0, 1.0])
                        direction = np.asarray([0.0, 0.0, -1.0])
                    elif index % 5 == 0:
                        direction = np.asarray([1.0, 1e-14, 0.0])
                    values = (0, merged.stack_width, *origin, *direction, 1e-8,
                        math.inf if index % 7 else 1.0, -1 if index % 4 else 0, *arrays)
                    counters = np.zeros(1, dtype=np.int64)
                    expected = canonical(*values, np.empty(merged.stack_width), np.empty(merged.stack_width, dtype=np.int64))
                    actual = guarded(*values, np.empty(merged.stack_width), np.empty(merged.stack_width, dtype=np.int64), counters)
                    self.assertEqual(actual[1:], expected[1:], (padded, gap, index))
                    self.assertEqual(np.float64(actual[0]).view(np.uint64), np.float64(expected[0]).view(np.uint64), (padded, gap, index))
                    total_replays += int(counters[0])
        self.assertGreater(total_replays, 1000)


class GuardedBvhGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def guarded_result(self, trace_input, *, factory_builder=guarded_factory, padded=False, canonical_inline=False):
        merged, _ = dual_scene(trace_input.mesh, padded=padded)
        factory, _ = factory_builder(padded=padded, canonical_inline=canonical_inline) if factory_builder is guarded_factory else factory_builder()
        original_context = raytracer._build_gpu_resident_wavefront_context
        original_launch = resident._launch_resident_kernel
        replay_count = 0

        def context(*values, **keywords):
            prepared = original_context(*values, **keywords)
            prepared.scene = merged
            return prepared

        def launch(kernel, *values, **keywords):
            nonlocal replay_count
            replays = gpu._CUDA.to_device(np.zeros(1, dtype=np.int64))
            result = original_launch(GuardedLaunch(kernel, replays), *values, **keywords)
            replay_count += int(replays.copy_to_host()[0])
            return result

        with variant(factory, {}), patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=context), patch.object(resident, '_launch_resident_kernel', new=launch):
            result = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        performance = result.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        return result, replay_count

    def test_old_microgap_face_choice_and_material_are_preserved_by_gpu_replay(self):
        trace_input = gap_case()
        with variant(resident._make_kernel, {}):
            expected = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        actual, replays = self.guarded_result(trace_input)
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
        self.assertTrue(parity.semantic_exact, parity.to_dict())
        self.assertGreater(replays, 0)
        self.assertGreater(actual.receiver_hit_count, 0)

    def test_unsafe_leaf_control_reproduces_the_material_error(self):
        trace_input = gap_case()
        with variant(resident._make_kernel, {}):
            expected = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        actual, _ = self.guarded_result(trace_input, factory_builder=unsafe_leaf_factory)
        self.assertGreater(expected.receiver_hit_count, 0)
        self.assertEqual(actual.receiver_hit_count, 0)
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
        self.assertFalse(parity.passed)

    def test_padded_and_inlined_guards_preserve_the_original_microgap_result(self):
        trace_input = gap_case()
        with variant(resident._make_kernel, {}):
            expected = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        for inlined in (False, True):
            actual, replays = self.guarded_result(trace_input, padded=True, canonical_inline=inlined)
            parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
            self.assertTrue(parity.semantic_exact, parity.to_dict())
            self.assertGreater(replays, 0)

    def test_padded_guard_preserves_actual_thousand_reflections(self):
        trace_input = cavity_case()
        for index in range(7):
            mesh = trace_input.mesh
            start = len(mesh.vertices)
            for vertex in ((100.0 + index, 0.0, 0.0), (100.0 + index, 1.0, 0.0), (100.5 + index, 0.0, 0.0)):
                mesh.add_vertex(vertex)
            mesh.add_face(start, start + 1, start + 2, 'absorb')
        with variant(resident._make_kernel, {}):
            expected = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        actual, _ = self.guarded_result(trace_input, padded=True, canonical_inline=True)
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
        self.assertTrue(parity.semantic_exact, parity.to_dict())
        self.assertEqual(actual.surface_hit_count, 17 * 1001)

    def test_cad_event_paths_and_reedited_properties_are_bit_exact(self):
        trace_input = cad_case(8193)[0]
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        for reflectance in (0.14, 0.8):
            trace_input.optical_profiles[0].reflectance = reflectance
            with variant(resident._make_kernel, {}):
                expected = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
            actual, _ = self.guarded_result(trace_input)
            parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
            self.assertTrue(parity.semantic_exact, parity.to_dict())


if __name__ == '__main__':
    unittest.main()
