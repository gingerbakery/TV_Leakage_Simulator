from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_gpu_persistent import PersistentLaunch, persistent_factory
from benchmark_perf5_gpu_specialization import batch_values, context_values, signature_key, specialized_factory
from benchmark_perf5_gpu_triangle_bounds import triangle_auxiliary
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


class KernelProbeContractTests(unittest.TestCase):
    def test_specialization_key_preserves_float_bits_and_feature_changes(self):
        positive = signature_key({}, {'face_roughness': 0.0}, (), None)
        negative = signature_key({}, {'face_roughness': -0.0}, (), None)
        self.assertNotEqual(positive, negative)
        self.assertNotEqual(positive, signature_key({'record_geometry': 1}, {'face_roughness': 0.0}, (), None))
        self.assertNotEqual(positive, signature_key({}, {'face_roughness': 0.0}, (1, 2), None))
        self.assertNotEqual(positive, signature_key({}, {'face_roughness': 0.0}, (), 64))

    def test_nonuniform_optics_are_never_replaced_by_a_literal(self):
        context = SimpleNamespace(face_reflectance=np.asarray([0.6, 0.6]),
            face_roughness=np.asarray([-0.0, 0.0]), face_scatter=np.asarray([1, 2], dtype=np.int8),
            face_specular_ratio=np.asarray([0.3, 0.7]), face_gaussian_sigma_deg=np.asarray([12.0, 12.0]))
        optical, _ = context_values(context, 'optics')
        self.assertEqual(set(optical), {'face_reflectance', 'face_gaussian_sigma_deg'})
        self.assertEqual(context_values(context, 'models')[1], (1, 2))
        context.face_reflectance[1] = 0.8
        self.assertNotIn('face_reflectance', context_values(context, 'optics')[0])

    def test_feature_selection_and_factory_fail_closed(self):
        batch = SimpleNamespace(bounce_receiver_mis_enabled=True, angle_dependent_reflectance=False, termination_mode=1)
        selected = batch_values(batch, True, 'config')
        self.assertEqual(selected, {'record_geometry': 1, 'bounce_receiver_mis_enabled': 1,
            'angle_dependent_reflectance': 0, 'termination_mode': 1})
        self.assertEqual(batch_values(batch, True, 'regs64'), {})
        with self.assertRaises(ValueError):
            specialized_factory({'unsupported': 1}, {}, (), None)
        with self.assertRaises(ValueError):
            specialized_factory({}, {'unsupported': 1.0}, (), None)

    def test_group_bounds_preserve_normals_and_cover_each_original_ordered_group(self):
        scene = gap_case().mesh.prepare_gpu_cuda_scene()
        normals = np.arange(len(scene.triangle_v0) * 3, dtype=np.float64).reshape(-1, 3)
        for width in (1, 2, 4):
            auxiliary = triangle_auxiliary(SimpleNamespace(scene=scene, triangle_normals=normals), width)
            np.testing.assert_array_equal(auxiliary[:, :3], normals)
            for start, count in zip(scene.node_start, scene.node_count):
                for head in range(int(start), int(start + count), width):
                    faces = scene.ordered_faces[head:min(head + width, int(start + count))]
                    row = auxiliary[faces[0]]
                    vertices = np.concatenate((scene.triangle_v0[faces],
                        scene.triangle_v0[faces] + scene.triangle_edge1[faces],
                        scene.triangle_v0[faces] + scene.triangle_edge2[faces]))
                    self.assertTrue(np.all(vertices >= row[3:6]))
                    self.assertTrue(np.all(vertices <= row[6:9]))


class PersistentGpuProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def compare_persistent(self, trace_input):
        with variant(resident._make_kernel, {}):
            expected = run_direct_ray_trace(trace_input, gpu_accumulator='host')
        factory, _ = persistent_factory()
        original_launch = resident._launch_resident_kernel

        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
            blocks = 1
            queue = gpu._CUDA.to_device(np.asarray([resident.THREADS_PER_BLOCK], dtype=np.int64))
            return original_launch(PersistentLaunch(kernel, queue, blocks), context, scene,
                bindings, workspace, batch, count, depths, **keywords)

        with variant(factory, {}), patch.object(resident, '_launch_resident_kernel', new=launch):
            actual = run_direct_ray_trace(trace_input, gpu_accumulator='host')
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
        self.assertTrue(parity.semantic_exact, parity.to_dict())
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        return actual

    def test_refill_preserves_mis_rng_output_rows_and_partial_tail(self):
        trace_input = _build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        self.compare_persistent(trace_input)

    def test_actual_thousand_reflections_complete_without_losing_tail_workers(self):
        actual = self.compare_persistent(cavity_case(ray_count=17, depth=1000))
        self.assertEqual(actual.surface_hit_count, 17 * 1001)


if __name__ == '__main__':
    unittest.main()
