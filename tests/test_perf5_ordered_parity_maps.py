from __future__ import annotations

import math
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from perf5_ordered_parity_maps import (
    addition_map, bits_float, compose_maps, float_bits, make_block_ordered_fold,
    ordered_map_fold, receiver_parity_maps, emission_parity_maps,
)
from test_perf5_warp_ordered_fold import cases
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_receiver_flux import ReceiverFluxGpuTests
from test_perf5_emitted_flux import EmittedFluxGpuTests
from perf5_adaptive_gpu_packets import adaptive_packets
from perf5_workspace_packing import workspace_factory
from benchmark_perf5_cpu import WORKLOADS
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import raytracer
from leakage_simulator import gpu_cuda_receiver_flux as receiver_flux
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def serial(initial, values, valid, square=False):
    total = float(initial)
    for value, selected in zip(values, valid, strict=True):
        if selected:
            addition = float(value) * float(value) if square else float(value)
            total += addition
    return total


class OrderedParityMapCpuTests(unittest.TestCase):
    def test_half_ulp_ties_use_incoming_mantissa_parity(self):
        initial = 1.0
        unit = math.ulp(initial)
        self.assertEqual(addition_map(initial, unit / 2), (0, 1))
        self.assertEqual(addition_map(initial, unit * 1.5), (2, 1))
        for offset in range(2):
            for values in ([unit / 2] * 2048, [unit * 1.5, unit / 2] * 1024):
                start = bits_float(float_bits(initial) + offset)
                actual, fast = ordered_map_fold(start, values, [True] * len(values))
                self.assertTrue(fast)
                self.assertEqual(float_bits(actual), float_bits(serial(start, values, [True] * len(values))))

    def test_integer_maps_compose_associatively_without_changing_order(self):
        generator = np.random.default_rng(91813)
        for _ in range(10000):
            maps = [tuple(int(value) for value in generator.integers(0, 1000000, 2)) for _ in range(3)]
            self.assertEqual(compose_maps(compose_maps(maps[0], maps[1]), maps[2]),
                compose_maps(maps[0], compose_maps(maps[1], maps[2])))
            for parity in (0, 1):
                expected = parity
                for mapping in maps:
                    expected += mapping[expected & 1]
                self.assertEqual(expected, parity + compose_maps(compose_maps(maps[0], maps[1]), maps[2])[parity])

    def test_ieee_oracle_cases_masks_squares_and_partial_tiles(self):
        accelerated = 0
        for initial, values, valid in cases():
            actual, fast = ordered_map_fold(initial, values, valid)
            expected = serial(initial, values, valid)
            self.assertEqual(float_bits(actual), float_bits(expected))
            accelerated += int(fast)
        self.assertGreater(accelerated, 500)
        generator = np.random.default_rng(8473)
        for length in (0, 1, 17, 127, 128, 129, 2047, 2048):
            for square in (False, True):
                values = generator.random(length) * 1e-3
                valid = generator.random(length) > 0.25
                for initial in (0.0, -0.0, 1e-300, 1.0, 1e100):
                    actual, _ = ordered_map_fold(initial, values, valid, square=square)
                    self.assertEqual(float_bits(actual), float_bits(serial(initial, values, valid, square)))

    def test_invalid_packets_and_nonfinite_values_are_not_silently_accepted(self):
        for values, valid in (([1.0] * 2049, [True] * 2049), ([1.0], [])):
            with self.assertRaises(ValueError):
                ordered_map_fold(1.0, values, valid)
        for value in (-1.0, math.inf, math.nan):
            self.assertIsNone(addition_map(1.0, value))
        actual, fast = ordered_map_fold(1.0, [math.inf], [True])
        self.assertFalse(fast)
        self.assertEqual(actual, math.inf)


class OrderedParityMapGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in
            ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise RuntimeError(f'Production CUDA unavailable: {preflight.reason_code}')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_block_helper_preserves_ordered_double_bits_with_ties_and_fallbacks(self):
        cuda = gpu._CUDA
        fold = make_block_ordered_fold(cuda)
        samples = cases()[::7]
        generator = np.random.default_rng(81723)
        for length in (1, 17, 127, 128, 129, 2047, 2048):
            for initial in (0.0, -0.0, 1.0, np.nextafter(2.0, 0.0), 1e300):
                values = np.spacing(initial if initial > 0 else 1.0) * (
                    generator.integers(0, 20, length) + 0.5)
                samples.append((initial, values, generator.random(length) > 0.25))
        initial = np.asarray([sample[0] for sample in samples])
        counts = np.asarray([len(sample[1]) for sample in samples], dtype=np.int64)
        values = np.zeros((len(samples), 2048), dtype=np.float64)
        valid = np.zeros(values.shape, dtype=np.uint8)
        for index, sample in enumerate(samples):
            values[index, :counts[index]] = sample[1]
            valid[index, :counts[index]] = sample[2]

        @cuda.jit(fastmath=False)
        def execute(initial, values, valid, counts, square, output, fast):
            case = cuda.blockIdx.x
            total, accepted = fold(initial[case], values[case], valid[case], counts[case], square)
            if cuda.threadIdx.x == 0:
                output[case] = total
                fast[case] = int(accepted)

        output = cuda.device_array(initial.size, dtype=np.float64)
        fast = cuda.device_array(initial.size, dtype=np.uint8)
        device_initial, device_values, device_valid, device_counts = [cuda.to_device(array)
            for array in (initial, values, valid, counts)]
        accelerated = 0
        for square in (False, True):
            execute[initial.size, 128](device_initial, device_values, device_valid, device_counts, square, output, fast)
            expected = np.asarray([serial(sample[0], sample[1], sample[2], square) for sample in samples])
            np.testing.assert_array_equal(output.copy_to_host().view(np.uint64), expected.view(np.uint64))
            accelerated += int(np.count_nonzero(fast.copy_to_host()))
        self.assertGreater(accelerated, 200)

    def test_receiver_mis_summary_keeps_discrete_and_strict_physical_results(self):
        for rays in (8193, 131089):
            trace_input = _build_reflected_case(rays, 7172, 'receiver_mis', backend='gpu_cuda')
            expected = raytracer.run_direct_ray_trace(trace_input)
            with receiver_parity_maps(gpu._CUDA):
                actual = raytracer.run_direct_ray_trace(trace_input)
            parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
            self.assertTrue(parity.passed, parity.to_dict())
            self.assertTrue(parity.discrete_exact)
            performance = actual.metrics['_performance_summary']
            self.assertEqual(performance['compute_execution_state'], 'gpu_active')
            self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
            self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_emission_and_receiver_maps_keep_strict_multi_chunk_mis_results(self):
        trace_input = _build_reflected_case(131089, 7173, 'receiver_mis', backend='gpu_cuda')
        expected = raytracer.run_direct_ray_trace(trace_input)
        with receiver_parity_maps(gpu._CUDA), emission_parity_maps(gpu._CUDA):
            actual = raytracer.run_direct_ray_trace(trace_input)
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)

    def test_coalesced_face_and_mis_preserve_stored_paths_and_partial_tail(self):
        for trace_input in (WORKLOADS['face_direct'](262163),
                _build_reflected_case(262163, 7173, 'receiver_mis', backend='gpu_cuda')):
            trace_input.config.compute_backend = 'gpu_cuda'
            trace_input.config.store_ray_paths = True
            trace_input.config.max_stored_paths = 17
            expected = raytracer.run_direct_ray_trace(trace_input)
            with receiver_parity_maps(gpu._CUDA), emission_parity_maps(gpu._CUDA), patch.object(
                resident, '_WORKSPACES', threading.local()
            ), patch.object(resident, '_ensure_workspace', new=workspace_factory('packed_reuse')), adaptive_packets(
                trace_input, minimum_rays=0
            ) as decisions:
                actual = raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=131072)
            parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
            self.assertTrue(parity.passed, parity.to_dict())
            self.assertTrue(any(decision['coalesced'] for decision in decisions))
            self.assertEqual(expected.stored_paths, actual.stored_paths)
            self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)


class ParityMapReceiverContractTests(ReceiverFluxGpuTests):
    def setUp(self):
        context = receiver_parity_maps(gpu._CUDA)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_large_partial_tiles_multi_receiver_and_different_companion_initial_bits(self):
        for receivers, zero_initial in ((2, False), (1, False), (1, True)):
            self.assert_packet_sums(receivers, zero_initial,
                ((4099, False), (19, True), (8193, False), (19, False)))

    def test_failure_after_mutating_candidate_device_state_replays_without_double_commit(self):
        trace_input = _build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')
        expected = raytracer.run_direct_ray_trace(trace_input)
        original = receiver_flux.accumulate_device_ordered_flux
        def fail_after_launch(*arguments):
            original(*arguments)
            gpu._CUDA.synchronize()
            raise RuntimeError('injected parity-map failure after device mutation')
        with patch.object(receiver_flux, 'accumulate_device_ordered_flux', new=fail_after_launch):
            actual = raytracer.run_direct_ray_trace(trace_input)
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], 'gpu_summary_ordered_receiver_flux_failed')


class ParityMapEmissionContractTests(EmittedFluxGpuTests):
    def setUp(self):
        super().setUp()
        context = emission_parity_maps(gpu._CUDA)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def assert_recorded_mode(self, workspace, mode, expected_mode):
        values = workspace.modes.copy_to_host()
        if mode in ('quantized', 'ties'):
            self.assertGreater(values[:, 1].sum(), 0)
        else:
            self.assertGreater(values.sum(), 0)

    def test_large_partial_primary_major_packets_preserve_all_sum_bits(self):
        self.assert_packets(5, 5, False, ((4099, False), (19, True), (8193, False)))


if __name__ == '__main__':
    unittest.main()
