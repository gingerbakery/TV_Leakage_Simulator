from __future__ import annotations

import ast
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case
from benchmark_perf5_gpu_coherent import CoherentKernels, bucket_histogram, coherent_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_optical_transport_audit import clipped_override_case
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer
from leakage_simulator.raytrace_bridge import build_direct_trace_input


def scalar_bucket(direction, count):
    body = bucket_histogram(count, 'True', '0')[:3]
    namespace = {'direction_x': direction[0], 'direction_y': direction[1], 'direction_z': direction[2]}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), '<coherent_bucket_oracle>', 'exec'), namespace)
    return namespace['bucket']


class CoherentContractTests(unittest.TestCase):
    def test_direction_classes_cover_axes_signed_zeros_and_roundoff_bounds(self):
        for count in (1, 8, 64):
            keys = set()
            for direction in ((-1.0, 0.0, 1.0), (1.0, -0.0, -1.0),
                (0.0, -0.0, 0.0), (-1.0 - 1e-15, 1.0 + 1e-15, 0.5)):
                key = scalar_bucket(direction, count)
                self.assertGreaterEqual(key, 0)
                self.assertLess(key, count)
                keys.add(key)
            if count > 1:
                self.assertGreater(len(keys), 1)

    def test_invalid_classes_partial_warp_and_abi_changes_fail_closed(self):
        with self.assertRaises(ValueError):
            coherent_factory(7)
        candidate = CoherentKernels((), ('ray_count',), (), 8)
        with self.assertRaises(ValueError):
            candidate[1, 31]
        with self.assertRaises(ValueError):
            candidate[1, 32](1, 2)


class CoherentGpuTests(unittest.TestCase):
    bucket_count = 8

    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')
        cls.factory, cls.fingerprint = coherent_factory(cls.bucket_count)
        cls.candidate_state = {}
        cls.original_state = {}

    @contextmanager
    def candidate(self):
        with variant(type(self).factory, type(self).candidate_state):
            yield

    def reference(self, trace_input, **keywords):
        with variant(resident._make_kernel, type(self).original_state):
            return raytracer.run_direct_ray_trace(trace_input, **keywords)

    def assert_parity(self, expected, actual, *, exact=True):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)
        self.assertEqual(actual.stored_paths, expected.stored_paths)
        if exact:
            self.assertTrue(parity.semantic_exact, parity.to_dict())

    def compare(self, trace_input):
        expected = self.reference(trace_input, gpu_accumulator='host')
        with self.candidate():
            actual = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
        self.assert_parity(expected, actual)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        return actual

    def test_microgap_and_receiver_cap_keep_original_face_properties(self):
        self.assertGreater(self.compare(gap_case(4099)).receiver_hit_count, 0)
        for height, receives in ((3e-11, False), (7e-11, True)):
            trace_input = gap_case(4099)
            trace_input.receivers[0].center = (0.0, 0.0, height)
            trace_input.receivers[0].normal = (0.0, 0.0, 1.0)
            self.assertEqual(self.compare(trace_input).receiver_hit_count > 0, receives)

    def test_primary_histogram_and_active_counts_match_exact_source_directions(self):
        trace_input = WORKLOADS['face_direct'](8193)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        original = resident._launch_resident_kernel
        checked = []

        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
            directions = batch.directions.copy()
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords)
            histogram = kernel.last_bucket_counts[:(depths + 1) * self.bucket_count].copy_to_host().reshape(-1, self.bucket_count)
            expected = np.bincount([scalar_bucket(direction, self.bucket_count) for direction in directions], minlength=self.bucket_count)
            np.testing.assert_array_equal(histogram[0], expected)
            active = kernel.last_active_counts[:depths + 1].copy_to_host()
            np.testing.assert_array_equal(histogram.sum(axis=1), active)
            np.testing.assert_array_equal(batch.directions, directions)
            checked.append(count)
            return result

        with self.candidate(), patch.object(resident, '_launch_resident_kernel', new=launch):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assertEqual(checked, [8193])
        self.assertGreater(actual.receiver_hit_count, 0)

    def test_mis_rng_and_partial_tail_paths_remain_exact(self):
        trace_input = _build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        self.compare(trace_input)

    def test_cad_paths_and_optical_reedits_remain_exact(self):
        trace_input = cad_case(8193)[0]
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 100
        for reflectance in (0.14, 0.8):
            trace_input.optical_profiles[0].reflectance = reflectance
            self.compare(trace_input)

    def test_roi_face_overrides_and_scatter_reedits_remain_exact(self):
        scene, payload = clipped_override_case(8193)
        trace_input = build_direct_trace_input(scene, payload)
        trace_input.config.compute_backend = 'gpu_cuda'
        for index, scatter in enumerate(('specular', 'lambertian', 'gaussian', 'mixed')):
            profile = trace_input.optical_profiles[1]
            profile.reflectance = (0.03, 0.6, 0.99, 0.14)[index]
            profile.scatter_model = scatter
            profile.specular_ratio = 0.35
            profile.diffuse_ratio = 0.65
            self.compare(trace_input)

    def test_window_retry_and_sparse_path_retrace_restore_original_inputs_at_depth_thousand(self):
        trace_input = cavity_case()
        expected = self.reference(trace_input)
        with self.candidate(), patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        self.assertGreater(actual.metrics['_performance_summary']['gpu_event_window_retry_count'], 0)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)

    def test_failure_after_device_mutation_replays_only_uncommitted_suffix(self):
        trace_input = WORKLOADS['stochastic_two_bounce'](70019)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        expected = self.reference(trace_input)
        original = resident._launch_resident_kernel
        completed = []

        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
            names = ('origins', 'directions', 'initial_power_lumen', 'source_faces', 'reflection_seeds')
            snapshots = {name: getattr(batch, name).tobytes() for name in names}
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords)
            for name, saved in snapshots.items():
                self.assertEqual(getattr(batch, name).tobytes(), saved)
            completed.append(count)
            if len(completed) == 2:
                raise resident.GpuResidentWavefrontProviderError('execute', 'injected_coherent_post_mutation_failure')
            return result

        with self.candidate(), patch.object(resident, '_launch_resident_kernel', new=launch):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_parity(expected, actual, exact=False)
        self.assertEqual(completed, [65536, 4483])
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)

    def test_stop_during_recording_retry_never_commits_partial_events(self):
        stopped = [False]
        original = raytracer.trace_resident_wavefront_gpu_cuda

        def stop_on_overflow(*values, **keywords):
            try:
                return original(*values, **keywords)
            except resident.GpuResidentEventWindowExceeded:
                stopped[0] = True
                raise

        with self.candidate(), patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(
            raytracer, 'trace_resident_wavefront_gpu_cuda', new=stop_on_overflow
        ):
            actual = raytracer.run_direct_ray_trace(cavity_case(), should_stop=lambda: stopped[0])
        self.assertTrue(stopped[0])
        self.assertEqual(actual.total_rays, 0)
        self.assertEqual(actual.surface_hit_count, 0)


class Coherent64GpuTests(CoherentGpuTests):
    bucket_count = 64


if __name__ == '__main__':
    unittest.main()
