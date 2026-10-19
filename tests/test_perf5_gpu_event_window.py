from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_transport import semantic_payload
from benchmark_perf5_cpu import WORKLOADS
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer
from leakage_simulator import wavefront_event_tape as event_tape
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig


def cavity_case(ray_count=17, depth=1000, late_emitter=False):
    mesh = TriangleMesh()
    for lower_x, upper_x, height, material in ((-5, 0, 1, 'absorb'), (0, 5, 1, 'mirror'), (-5, 5, 0, 'mirror')):
        vertices = [mesh.add_vertex(point) for point in (
            (lower_x, -5, height), (upper_x, -5, height),
            (upper_x, 5, height), (lower_x, 5, height),
        )]
        mesh.add_face(vertices[0], vertices[1], vertices[2], material)
        mesh.add_face(vertices[0], vertices[2], vertices[3], material)
    centers = (-2.0, 2.0) if late_emitter else (2.0,)
    emitters = [EmitterSpec(
        emitter_id=f'source_{index}', emitter_type='datum_plane',
        center=(center_x, 0.0, 0.5), u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, 1.0, 0.0),
        width_mm=0.1, height_mm=0.1, direction_distribution='gaussian',
        gaussian_sigma_deg=0.000001, power_lumen=1.0, ray_count=ray_count, seed=42 + index,
    ) for index, center_x in enumerate(centers)]
    return raytracer.DirectRayTraceInput(
        mesh=mesh, emitters=emitters, receivers=[], optical_profiles=[
            OpticalProfile('absorb', 0.0, scatter_model='specular'),
            OpticalProfile('mirror', 0.999, scatter_model='specular'),
        ], config=RayTraceConfig(
            compute_backend='gpu_cuda', ray_count=ray_count * len(emitters), max_depth=depth,
            min_energy=0.0, angle_dependent_reflectance=False, intersection_backend='bvh',
            store_ray_paths=True, max_stored_paths=8,
        ),
    )


def absorber_case(ray_count):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.compute_backend = 'gpu_cuda'
    trace_input.config.max_depth = 1000
    trace_input.config.angle_dependent_reflectance = False
    trace_input.optical_profiles[0].reflectance = 0.0
    return trace_input


class GpuEventWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def tearDown(self):
        resident._reset_gpu_resident_wavefront_for_tests()

    def assert_physical_parity(self, expected, actual):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)

    def test_high_cap_shallow_paths_use_large_gpu_chunks_without_truncating_depth(self):
        trace_input = absorber_case(70019)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'off'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'auto'}), patch.object(resident, '_WORKSPACES', threading.local()):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_physical_parity(expected, actual)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(actual.total_rays, 70019)
        self.assertEqual(actual.surface_hit_count, 70019)
        self.assertEqual(actual.config.max_depth, 1000)
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 2)
        self.assertEqual(performance['gpu_event_record_depth'], resident.INITIAL_EVENT_WINDOW_DEPTH)
        self.assertEqual(performance['gpu_event_window_contract'], resident.EVENT_WINDOW_CONTRACT)
        self.assertEqual(performance['gpu_event_window_retry_count'], 0)
        self.assertEqual(performance['gpu_event_max_batch_size'], 65536)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')

    def test_actual_thousand_reflections_replay_without_shortening_paths(self):
        trace_input = cavity_case()
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'off'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(resident, '_WORKSPACES', threading.local()):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_physical_parity(expected, actual)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        self.assertEqual(len(actual.stored_paths), 8)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['wavefront_max_observed_depth'], 1000)
        self.assertEqual(performance['gpu_event_record_depth'], 1001)
        self.assertGreater(performance['gpu_event_window_retry_count'], 0)
        self.assertGreater(performance['gpu_event_window_abandoned_kernel_sec'], 0.0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)

    def test_budget_split_keeps_primary_order_and_summary_transaction(self):
        trace_input = cavity_case(257, 80)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'off'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        original = resident._ensure_workspace
        allocations = []

        def inspect(*args, **kwargs):
            workspace, reused, elapsed = original(*args, **kwargs)
            allocations.append(workspace.capacity * workspace.depth_capacity)
            return workspace, reused, elapsed

        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'auto'}), patch.object(resident, 'MAX_BATCH_EVENT_SLOTS', 2048), patch.object(event_tape, 'MAX_BATCH_EVENT_SLOTS', 2048), patch.object(resident, '_ensure_workspace', new=inspect):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_physical_parity(expected, actual)
        self.assertEqual(actual.surface_hit_count, 257 * 81)
        self.assertTrue(allocations)
        self.assertLessEqual(max(allocations), 2048)
        performance = actual.metrics['_performance_summary']
        self.assertGreater(performance['gpu_event_window_retry_count'], 0)
        self.assertGreater(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_late_deep_emitter_does_not_change_previously_committed_gpu_state(self):
        trace_input = cavity_case(8, 1000, late_emitter=True)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'off'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        original = raytracer.trace_resident_wavefront_gpu_cuda
        abandoned = []

        def inspect(context, batch, **kwargs):
            session = context._summary_session
            before = None if session.device_state is None else {
                name: session.device_state[name].copy_to_host() for name in
                ('reflection_counts', 'contribution_depth_flux_lumen', 'profile_incoming_flux_lumen')
            }
            digest = session.last_state_digest
            try:
                return original(context, batch, **kwargs)
            except resident.GpuResidentEventWindowExceeded:
                self.assertEqual(session.last_state_digest, digest)
                self.assertIsNotNone(before)
                for name, values in before.items():
                    np.testing.assert_array_equal(session.device_state[name].copy_to_host().view(np.uint64), values.view(np.uint64))
                abandoned.append(len(batch))
                raise

        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(resident, '_WORKSPACES', threading.local()), patch.object(raytracer, 'trace_resident_wavefront_gpu_cuda', new=inspect):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_physical_parity(expected, actual)
        self.assertTrue(abandoned)
        self.assertEqual(actual.surface_hit_count, 8 + 8 * 1001)
        self.assertEqual(actual.total_rays, 16)

    def test_failure_after_large_chunk_uses_budget_safe_cpu_replay_of_only_suffix(self):
        trace_input = absorber_case(70019)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'auto'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        original = raytracer.trace_resident_wavefront_gpu_cuda
        completed = []

        def fail_second(context, batch, **kwargs):
            if completed:
                raise resident.GpuResidentWavefrontProviderError('execute', 'injected_second_chunk_failure')
            result = original(context, batch, **kwargs)
            completed.append(len(batch))
            return result

        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'auto'}), patch.object(raytracer, 'trace_resident_wavefront_gpu_cuda', new=fail_second):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_physical_parity(expected, actual)
        self.assertEqual(completed, [65536])
        self.assertEqual(actual.total_rays, 70019)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')

    def test_stop_between_window_retries_does_not_commit_abandoned_events(self):
        stopped = [False]
        original = raytracer.trace_resident_wavefront_gpu_cuda

        def stop_on_overflow(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except resident.GpuResidentEventWindowExceeded:
                stopped[0] = True
                raise

        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(raytracer, 'trace_resident_wavefront_gpu_cuda', new=stop_on_overflow):
            actual = raytracer.run_direct_ray_trace(cavity_case(), should_stop=lambda: stopped[0])
        self.assertTrue(stopped[0])
        self.assertEqual(actual.total_rays, 0)
        self.assertEqual(actual.surface_hit_count, 0)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_event_window_retry_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 0)

    def test_auto_skips_replay_when_the_full_logical_record_fits_the_budget(self):
        trace_input = cavity_case()
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'auto'}), patch.object(resident, '_WORKSPACES', threading.local()):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_event_window_retry_count'], 0)
        self.assertIsNone(performance['gpu_event_window_contract'])
        self.assertEqual(performance['gpu_event_record_depth'], 1001)
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')

    def test_face_emitter_receiver_flux_and_heatmap_survive_larger_chunks(self):
        trace_input = WORKLOADS['face_direct'](70019)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.max_depth = 1000
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'off'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'auto'}), patch.object(resident, '_WORKSPACES', threading.local()):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assert_physical_parity(expected, actual)
        self.assertGreater(actual.receiver_hit_count, 0)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_event_max_batch_size'], 65536)
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 2)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


if __name__ == '__main__':
    unittest.main()
