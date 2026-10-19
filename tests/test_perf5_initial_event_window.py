from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from perf5_adaptive_gpu_packets import adaptive_batch_request, adaptive_packets
from perf5_emission_aliases import emission_aliases
from perf5_initial_event_window import initial_event_window
from perf5_ordered_parity_maps import receiver_parity_maps
from perf5_workspace_packing import workspace_factory
import test_perf5_gpu_event_window as window_tests
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import raytracer


class InitialWindowCpuTests(unittest.TestCase):
    def test_factory_and_planner_use_the_same_minimum_and_restore_after_exceptions(self):
        original_depth = resident.INITIAL_EVENT_WINDOW_DEPTH
        context = SimpleNamespace(_event_depth_hint=32)
        with patch.object(raytracer, '_build_gpu_resident_wavefront_context', return_value=context):
            with self.assertRaisesRegex(RuntimeError, 'deliberate'):
                with initial_event_window(16):
                    self.assertEqual(resident.INITIAL_EVENT_WINDOW_DEPTH, 16)
                    self.assertIs(raytracer._build_gpu_resident_wavefront_context(), context)
                    self.assertEqual(context._event_depth_hint, 16)
                    raise RuntimeError('deliberate')
        self.assertEqual(resident.INITIAL_EVENT_WINDOW_DEPTH, original_depth)

    def test_absorber_cap_twenty_fits_one_coalesced_packet_without_raising_event_budget(self):
        trace_input = build_depth_ten_case(100000)
        trace_input.config.max_depth = 20
        self.assertIsNone(adaptive_batch_request(trace_input, minimum_rays=100000))
        budget = resident.MAX_BATCH_EVENT_SLOTS
        with initial_event_window(16):
            self.assertEqual(adaptive_batch_request(trace_input, minimum_rays=100000), 131072)
            self.assertLessEqual(131072 * 16, resident.MAX_BATCH_EVENT_SLOTS)
        self.assertEqual(resident.MAX_BATCH_EVENT_SLOTS, budget)

    def test_missing_context_and_invalid_policies_fail_without_mutation(self):
        with patch.object(raytracer, '_build_gpu_resident_wavefront_context', return_value=None):
            with initial_event_window(16):
                self.assertIsNone(raytracer._build_gpu_resident_wavefront_context())
        for depth in (0, 4, 1000):
            with self.assertRaises(ValueError):
                with initial_event_window(depth):
                    pass


class InitialWindowGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        window_tests.GpuEventWindowTests.setUpClass.__func__(cls)

    def run_candidate(self, trace_input, **options):
        with ExitStack() as stack:
            stack.enter_context(initial_event_window(16))
            stack.enter_context(receiver_parity_maps(gpu._CUDA))
            stack.enter_context(emission_aliases(gpu._CUDA))
            stack.enter_context(patch.object(resident, '_WORKSPACES', threading.local()))
            stack.enter_context(patch.object(resident, '_ensure_workspace', new=workspace_factory('packed_reuse')))
            stack.enter_context(adaptive_packets(trace_input, minimum_rays=0))
            return raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=131072, **options)

    def assert_physical(self, expected, actual):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)

    def test_one_hundred_thousand_absorbing_rays_use_one_gpu_commit_at_cap_twenty(self):
        trace_input = build_depth_ten_case(100000)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.max_depth = 20
        trace_input.config.angle_dependent_reflectance = False
        trace_input.optical_profiles[0].reflectance = 0.0
        expected = raytracer.run_direct_ray_trace(trace_input)
        actual = self.run_candidate(trace_input)
        self.assert_physical(expected, actual)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_actual_thousand_reflections_replay_before_commit_and_preserve_paths(self):
        trace_input = window_tests.cavity_case(17, 1000)
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 8
        expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}):
            actual = self.run_candidate(trace_input)
        self.assert_physical(expected, actual)
        self.assertEqual(expected.stored_paths, actual.stored_paths)
        self.assertEqual(actual.surface_hit_count, 17 * 1001)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertGreater(performance['gpu_event_window_retry_count'], 0)

    def test_late_deep_emitter_keeps_the_completed_earlier_emitter(self):
        trace_input = window_tests.cavity_case(1024, 80, late_emitter=True)
        expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}):
            actual = self.run_candidate(trace_input)
        self.assert_physical(expected, actual)
        self.assertEqual(expected.stored_paths, actual.stored_paths)
        performance = actual.metrics['_performance_summary']
        self.assertGreater(performance['gpu_event_window_retry_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_stop_after_overflow_discards_the_uncommitted_trial(self):
        trace_input = window_tests.cavity_case(17, 1000)
        stopped = [False]
        original = resident._launch_resident_kernel
        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **options):
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **options)
            if workspace.event_faces.shape[1] < batch.max_depth + 1:
                stopped[0] = True
            return result
        with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': 'force'}), patch.object(
            resident, '_launch_resident_kernel', new=launch
        ):
            actual = self.run_candidate(trace_input, should_stop=lambda: stopped[0])
        self.assertTrue(stopped[0])
        self.assertEqual(actual.total_rays, 0)
        self.assertEqual(actual.surface_hit_count, 0)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        self.assertEqual(performance['gpu_event_window_retry_count'], 1)

    def test_failure_preserves_the_successful_coalesced_prefix_and_replays_the_suffix(self):
        trace_input = build_depth_ten_case(262163)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.max_depth = 20
        trace_input.config.angle_dependent_reflectance = False
        trace_input.optical_profiles[0].reflectance = 0.0
        expected = raytracer.run_direct_ray_trace(trace_input)
        original = resident._launch_resident_kernel
        completed = []
        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **options):
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **options)
            completed.append(count)
            if len(completed) == 2:
                raise resident.GpuResidentWavefrontProviderError('execute', 'injected_window16_second_packet_failure')
            return result
        with patch.object(resident, '_launch_resident_kernel', new=launch):
            actual = self.run_candidate(trace_input)
        self.assertEqual(completed, [131072, 131072])
        self.assert_physical(expected, actual)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)


if __name__ == '__main__':
    unittest.main()
