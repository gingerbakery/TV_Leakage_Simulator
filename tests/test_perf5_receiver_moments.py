from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from leakage_simulator import gpu_cuda_receiver_moments as moments
from leakage_simulator import gpu_cuda_receiver_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace
from test_perf4e_bounce_mis import _build_reflected_case, _semantic_payload
from perf4_accuracy import compare_semantic_payloads
from diagnose_perf5_gpu_roundoff import summarize_roundoff_cases


class ReceiverMomentTests(unittest.TestCase):
    def test_candidate_roundoff_gate_is_fail_closed_and_baseline_is_separate(self):
        self.assertFalse(summarize_roundoff_cases({})['passed'])
        self.assertFalse(summarize_roundoff_cases({'candidate': [{'passed': True}, {'passed': False}]})['passed'])
        result = summarize_roundoff_cases({'candidate': [{'passed': True}], 'legacy': [{'passed': False}]})
        self.assertTrue(result['passed'])
        self.assertFalse(result['legacy_passed'])
        self.assertEqual(result['candidate_passed_count'], 1)
        self.assertEqual(result['gate_scope'], 'production_candidate')

    def test_workspace_metadata_is_bounded(self):
        for count in (0, 19, 8192, 65536):
            for receivers in (0, 1, 32, 1024):
                width, tiles = moments.workspace_layout(count, receivers)
                self.assertGreaterEqual(width, 128)
                self.assertEqual(width & (width - 1), 0)
                self.assertEqual(tiles, (count + width - 1) // width)
                self.assertLessEqual(tiles * receivers, moments.METADATA_SLOT_LIMIT)
        with self.assertRaises(ValueError):
            moments.workspace_layout(-1, 1)
        with self.assertRaises(ValueError):
            moments.workspace_layout(100, moments.METADATA_SLOT_LIMIT + 1)


class ReceiverMomentGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production preflight contract mismatch')

    def test_synthetic_packets_match_independent_order_with_constant_and_varying_tiles(self):
        cuda = gpu._CUDA
        rng = np.random.default_rng(42)
        indices = rng.integers(-1, 2, 8192, dtype=np.int32)
        powers = np.exp2(rng.integers(-80, -2, len(indices))) * rng.random(len(indices))
        powers[:128] = 0.125
        powers[indices < 0] = 0.0
        kinds = np.where(indices >= 0, moments.TERMINAL_RECEIVER, moments.TERMINAL_RECEIVER + 1).astype(np.int8)
        workspace = moments.ensure_workspace(cuda, None, len(indices), 2,
            cuda.to_device(kinds), cuda.to_device(indices), cuda.to_device(powers))
        expected = np.asarray([0.03, 0.02])
        totals = cuda.to_device(expected)
        for address, power in zip(indices, powers):
            if address >= 0:
                receiver = int(address)
                expected[receiver] += float(power) * float(power)
        moments.accumulate_device_ordered_totals(cuda, workspace, len(indices), totals)
        cuda.synchronize()
        actual = totals.copy_to_host()
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))
        self.assertGreater(np.count_nonzero(workspace.varying.copy_to_host()), 0)
        self.assertTrue(np.all(workspace.varying.copy_to_host()[:, 0] == 0))

    def test_identical_add_acceleration_is_bit_exact_at_ties_binades_and_subnormals(self):
        cuda = gpu._CUDA
        repeat_identical = moments.make_kernels(cuda)[2]

        @cuda.jit(fastmath=False)
        def evaluate(initial, increments, counts, output):
            index = cuda.grid(1)
            if index < len(counts):
                output[index] = repeat_identical(initial[index], increments[index], counts[index])

        cases = []
        for initial in (0.0, -0.0, 1.0, np.nextafter(1.0, 2.0), np.nextafter(2.0, 0.0), np.nextafter(0.0, 1.0), np.finfo(float).tiny):
            for addition in (0.0, 2.0 ** -54, 2.0 ** -53, 3.0 * 2.0 ** -53, 5.0 * 2.0 ** -53, 1e-12, np.nextafter(0.0, 1.0)):
                for count in (0, 1, 2, 31, 128, 65536):
                    cases.append((float(initial), float(addition), count))
        rng = np.random.default_rng(573)
        for index in range(400):
            initial, addition = np.ldexp(rng.random(2), rng.integers(-1074, 1000, 2))
            cases.append((float(initial), float(addition), int(rng.integers(1, 4096))))
        cases.extend(((0.0, 1e-12, 1000000), (1.0, 2.0 ** -52, 1000000), (1e308, 1e308, 10)))
        initial = np.asarray([case[0] for case in cases])
        increments = np.asarray([case[1] for case in cases])
        counts = np.asarray([case[2] for case in cases], dtype=np.int64)
        expected = []
        for start, addition, count in cases:
            total = start
            for repetition in range(count):
                total = total + addition
            expected.append(total)
        output = cuda.device_array(len(cases), dtype=np.float64)
        evaluate[(len(cases) + 127) // 128, 128](cuda.to_device(initial), cuda.to_device(increments), cuda.to_device(counts), output)
        cuda.synchronize()
        np.testing.assert_array_equal(output.copy_to_host().view(np.uint64), np.asarray(expected).view(np.uint64))

    def test_mis_repeat_preserves_receiver_moments_and_gpu_execution(self):
        cpu = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis'))
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_MOMENTS': 'ordered'}):
            for repeat in range(8):
                actual = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'))
                parity = compare_semantic_payloads(_semantic_payload(cpu), _semantic_payload(actual),
                    absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
                self.assertTrue(parity.passed, (repeat, parity.to_dict()))
                performance = actual.metrics['_performance_summary']
                self.assertEqual(performance['gpu_summary_receiver_moment_contract'], moments.CONTRACT)
                self.assertEqual(performance['gpu_summary_receiver_moment_workspace_bytes'], 0)
                self.assertGreater(performance['gpu_summary_receiver_flux_workspace_bytes'], 0)
                self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_failed_moment_kernel_replays_whole_batch_without_double_commit(self):
        cpu = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis'))
        with patch.object(flux, 'accumulate_device_ordered_flux', side_effect=ValueError('injected ordered moment kernel failure')):
            actual = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'))
        parity = compare_semantic_payloads(_semantic_payload(cpu), _semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], 'gpu_summary_ordered_receiver_flux_failed')

    def test_multiple_chunks_keep_device_state_and_owned_snapshots_synchronized(self):
        trace_input = _build_reflected_case(131091, 7171, 'receiver_mis', backend='gpu_cuda')
        original = resident.accumulate_resident_summary_gpu_cuda
        snapshots = []

        def inspect(events, request, session):
            execution = original(events, request, session)
            values = execution.result.state.grid_flux_squared_totals_lumen2
            device_values = session.device_state['grid_flux_squared_totals_lumen2'].copy_to_host()
            np.testing.assert_array_equal(device_values.view(np.uint64), values.view(np.uint64))
            self.assertFalse(values.flags.writeable)
            snapshots.append((values, values.copy()))
            return execution

        with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=inspect):
            actual = run_direct_ray_trace(trace_input)
        self.assertEqual(len(snapshots), 3)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)
        for values, copy in snapshots:
            np.testing.assert_array_equal(values.view(np.uint64), copy.view(np.uint64))

    def test_atomic_diagnostic_mode_remains_available(self):
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_MOMENTS': 'atomic'}):
            actual = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'))
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_summary_receiver_moment_contract'], 'global_fp64_atomic_receiver_moments_v1')
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


if __name__ == '__main__':
    unittest.main()
