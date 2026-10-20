from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_receiver_flux as flux
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.native_cpu_ordered_reducer import TERMINAL_RECEIVER
from leakage_simulator.raytracer import run_direct_ray_trace
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_gpu_event_window import cavity_case
from verify_gpu_cpu_accuracy import build_face_direct_case


def no_hit_case(ray_count=257):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.compute_backend = 'gpu_cuda'
    trace_input.config.store_ray_paths = False
    trace_input.config.angle_dependent_reflectance = False
    trace_input.optical_profiles[0].reflectance = 0.0
    return trace_input


class ReceiverHitProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def assert_parity(self, expected, actual):
        report = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact)

    def assert_cuda(self, result):
        performance = result.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        return performance

    def test_zero_hit_chunks_do_not_allocate_or_launch_receiver_reduction(self):
        trace_input = no_hit_case()
        trace_input.config.compute_backend = 'cpu'
        expected = run_direct_ray_trace(trace_input, intersection_batch_size=64)
        trace_input.config.compute_backend = 'gpu_cuda'
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'auto'}), patch.object(flux, 'ensure_workspace') as allocate, patch.object(flux, 'accumulate_device_ordered_flux') as reduce:
            actual = run_direct_ray_trace(trace_input, intersection_batch_size=64)
        allocate.assert_not_called()
        reduce.assert_not_called()
        self.assert_parity(expected, actual)
        self.assertEqual(actual.receiver_hit_count, 0)
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_hit_hint_count'], 5)
        self.assertEqual(performance['gpu_resident_receiver_hit_hint_download_bytes'], 80)
        self.assertEqual(performance['gpu_summary_receiver_reduction_skipped_count'], 5)
        self.assertEqual(performance['gpu_summary_receiver_flux_workspace_bytes'], 0)

    def test_real_hits_use_ordered_reduction_without_an_extra_transfer(self):
        outputs = {}
        original = resident.accumulate_resident_summary_gpu_cuda
        for mode in ('off', 'auto'):
            captured = []

            def inspect(events, request, session):
                execution = original(events, request, session)
                captured.append(execution)
                return execution

            with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': mode}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=inspect), patch.object(flux, 'accumulate_device_ordered_flux', wraps=flux.accumulate_device_ordered_flux) as reduce:
                actual = run_direct_ray_trace(_build_reflected_case(8192, 7169, 'receiver_mis', backend='gpu_cuda'))
            self.assertEqual(reduce.call_count, 1)
            self.assertEqual(len(captured), 1)
            self.assertGreater(actual.receiver_hit_count, 0)
            self.assert_cuda(actual)
            outputs[mode] = (actual, captured[0])
        self.assert_parity(outputs['off'][0], outputs['auto'][0])
        self.assertEqual(outputs['auto'][1].output_bytes, outputs['off'][1].output_bytes)
        self.assertTrue(outputs['auto'][1].receiver_hit_hint_used)
        self.assertFalse(outputs['auto'][1].receiver_reduction_skipped)
        self.assertLess(outputs['auto'][1].output_bytes, 32 * 1024)

    def test_empty_late_emitter_keeps_previous_device_flux_and_owned_state(self):
        trace_input = _build_reflected_case(1024, 7169, 'receiver_mis', backend='gpu_cuda')
        trace_input.emitters.append(replace(trace_input.emitters[0], emitter_id='empty_source', center=(1000.0, 0.0, 0.0)))
        trace_input.config.ray_count = 2048
        executions = []
        original = resident.accumulate_resident_summary_gpu_cuda

        def inspect(events, request, session):
            execution = original(events, request, session)
            for name in ('receiver_flux_lumen', 'grid_flux_squared_totals_lumen2'):
                values = getattr(execution.result.state, name)
                self.assertFalse(values.flags.writeable)
                np.testing.assert_array_equal(values.view(np.uint64), session.device_state[name].copy_to_host().view(np.uint64))
            executions.append(execution)
            return execution

        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'auto'}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=inspect):
            actual = run_direct_ray_trace(trace_input)
        trace_input.config.compute_backend = 'cpu'
        expected = run_direct_ray_trace(trace_input)
        self.assert_parity(expected, actual)
        self.assertEqual(len(executions), 2)
        self.assertFalse(executions[0].receiver_reduction_skipped)
        self.assertTrue(executions[1].receiver_reduction_skipped)
        self.assertGreater(executions[1].receiver_flux_workspace_bytes, 0)
        for name in ('receiver_flux_lumen', 'grid_flux_squared_totals_lumen2'):
            np.testing.assert_array_equal(getattr(executions[0].result.state, name).view(np.uint64), getattr(executions[1].result.state, name).view(np.uint64))
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_hit_hint_count'], 2)
        self.assertEqual(performance['gpu_summary_receiver_reduction_skipped_count'], 1)

    def test_invalid_event_prevents_reduction_and_replays_without_double_commit(self):
        original = resident.accumulate_resident_summary_gpu_cuda

        def corrupt(events, request, session):
            kinds = events.terminal_kind.copy_to_host()
            slots = np.flatnonzero(kinds[:events.ray_count] == TERMINAL_RECEIVER)
            self.assertGreater(slots.size, 1)
            depths = events.terminal_depth.copy_to_host()
            depths[slots[0]] = events.depth_count
            events.terminal_depth.copy_to_device(depths)
            return original(events, request, session)

        expected = run_direct_ray_trace(_build_reflected_case(1024, 7169, 'receiver_mis'))
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'auto'}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=corrupt), patch.object(flux, 'accumulate_device_ordered_flux', wraps=flux.accumulate_device_ordered_flux) as reduce:
            actual = run_direct_ray_trace(_build_reflected_case(1024, 7169, 'receiver_mis', backend='gpu_cuda'))
        self.assertEqual(reduce.call_count, 1)
        self.assert_parity(expected, actual)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], 'gpu_summary_accumulator_invalid_event_2')

    def test_incorrect_hit_hint_fails_closed_without_losing_flux(self):
        original = resident.accumulate_resident_summary_gpu_cuda
        expected = run_direct_ray_trace(_build_reflected_case(1024, 7169, 'receiver_mis'))
        for hint, reason in ((False, 'gpu_summary_receiver_hit_hint_mismatch'), ('false', 'gpu_summary_receiver_hit_hint_invalid')):
            with self.subTest(hint=hint):
                def corrupt(events, request, session):
                    return original(replace(events, receiver_hits_present=hint), request, session)

                with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'auto'}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=corrupt):
                    actual = run_direct_ray_trace(_build_reflected_case(1024, 7169, 'receiver_mis', backend='gpu_cuda'))
                self.assert_parity(expected, actual)
                performance = actual.metrics['_performance_summary']
                self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
                self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], reason)

    def test_off_diagnostic_keeps_unconditional_reduction(self):
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'off'}), patch.object(flux, 'accumulate_device_ordered_flux', wraps=flux.accumulate_device_ordered_flux) as reduce:
            actual = run_direct_ray_trace(no_hit_case())
        self.assertEqual(reduce.call_count, 1)
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_hit_hint_count'], 0)
        self.assertEqual(performance['gpu_summary_receiver_reduction_skipped_count'], 0)

    def test_no_receivers_requires_no_probe(self):
        trace_input = cavity_case(ray_count=19, depth=10)
        trace_input.config.store_ray_paths = False
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'auto'}), patch.object(flux, 'accumulate_device_ordered_flux') as reduce:
            actual = run_direct_ray_trace(trace_input)
        reduce.assert_not_called()
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_hit_hint_count'], 0)

    def test_missing_legacy_hint_keeps_conservative_reduction(self):
        original = resident.accumulate_resident_summary_gpu_cuda

        def without_hint(events, request, session):
            return original(replace(events, receiver_hits_present=None), request, session)

        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_HIT_PROBE': 'auto'}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=without_hint), patch.object(flux, 'accumulate_device_ordered_flux', wraps=flux.accumulate_device_ordered_flux) as reduce:
            actual = run_direct_ray_trace(no_hit_case())
        self.assertEqual(reduce.call_count, 1)
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_reduction_skipped_count'], 0)

    def test_uniform_direct_packets_use_the_small_serial_kernel(self):
        trace_input = build_face_direct_case(8192)
        trace_input.config.compute_backend = 'gpu_cuda'
        actual = run_direct_ray_trace(trace_input)
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_uniform_kernel_count'], 1)
        trace_input.config.compute_backend = 'cpu'
        self.assert_parity(run_direct_ray_trace(trace_input), actual)

    def test_reflected_mis_packets_keep_the_general_kernel(self):
        actual = run_direct_ray_trace(_build_reflected_case(8192, 7169, 'receiver_mis', backend='gpu_cuda'))
        performance = self.assert_cuda(actual)
        self.assertEqual(performance['gpu_summary_receiver_uniform_kernel_count'], 0)


if __name__ == '__main__':
    unittest.main()
