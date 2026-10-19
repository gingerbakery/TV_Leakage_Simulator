from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from perf5_emission_aliases import alias_from_metadata, emission_aliases, make_alias_stages
from perf5_adaptive_gpu_packets import adaptive_packets
from perf5_ordered_parity_maps import receiver_parity_maps
from perf5_workspace_packing import workspace_factory
from test_perf5_emitted_flux import EmittedFluxGpuTests
from test_perf4e_bounce_mis import _build_reflected_case
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_emitted_flux as emissions
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator import raytracer


def selector_case():
    count, records, depths = 8193, 3, 5
    metadata = np.zeros(5 + 3 * (reducer.LOBE_COUNT + depths), dtype=np.int64)
    metadata[1] = count
    encoded = np.float64(0.25).view(np.int64) + 1
    for group in (1, reducer.LOBE_COUNT + 1):
        metadata[5 + 3 * group:8 + 3 * group] = encoded, 1, count
    outputs = [np.ones(shape, dtype=np.float64) for shape in ((3, 2), (depths, 2), (3, 5), (depths, 7))]
    return count, records, depths, metadata, *outputs


def selector_cases():
    cases = [(selector_case(), (1, 1))]
    for style in ('missing', 'multiple_lobes', 'multiple_depths', 'unequal_counts', 'bad_depth',
                  'negative_count', 'invalid_flag', 'missing_power', 'bad_chunk', 'empty_chunk',
                  'reflection_initial', 'contribution_initial', 'signed_zero', 'infinite_initial'):
        data = list(selector_case())
        metadata = data[3]
        if style == 'missing':
            data[3] = metadata[:5]
        elif style in ('multiple_lobes', 'multiple_depths', 'bad_depth'):
            group = {'multiple_lobes': 2, 'multiple_depths': 5, 'bad_depth': 3}[style]
            metadata[5 + group * 3:8 + group * 3] = metadata[8:11]
        elif style == 'unequal_counts':
            metadata[5 + 3 * 4 + 2] -= 1
        elif style == 'negative_count':
            metadata[10] = -1
        elif style == 'invalid_flag':
            metadata[9] = 2
        elif style == 'missing_power':
            metadata[8] = 0
        elif style == 'bad_chunk':
            metadata[4] = 1
        elif style == 'empty_chunk':
            metadata[1] = 0
        elif style == 'reflection_initial':
            data[4][1, 0] = np.nextafter(1.0, 2.0)
        elif style == 'contribution_initial':
            data[6][1, reducer.OUTCOME_EMITTED] = np.nextafter(1.0, 2.0)
        elif style == 'signed_zero':
            data[4][1, 0] = -0.0
            data[5][1, 0] = 0.0
        elif style == 'infinite_initial':
            data[4][1, 0] = data[5][1, 0] = np.inf
        cases.append((tuple(data), (-1, -1)))
    return cases


class EmissionAliasCpuTests(unittest.TestCase):
    def test_alias_requires_one_current_depth_one_lobe_and_both_initial_bit_pairs(self):
        for arguments, expected in selector_cases():
            self.assertEqual(alias_from_metadata(*arguments), expected)

    def test_equal_counts_are_not_a_proof_of_equal_streams(self):
        arguments = list(selector_case())
        metadata = arguments[3]
        metadata[5 + 3 * 2:8 + 3 * 2] = metadata[8:11]
        metadata[5 + 3 * 5:8 + 3 * 5] = metadata[8:11]
        self.assertEqual(alias_from_metadata(*arguments), (-1, -1))


class EmissionAliasGpuTests(EmittedFluxGpuTests):
    def setUp(self):
        super().setUp()
        context = emission_aliases(gpu._CUDA)
        self.alias_kernel = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_device_selector_matches_independent_host_guard(self):
        cuda = gpu._CUDA
        classify, _ = make_alias_stages(cuda)
        alias = cuda.device_array(2, dtype=np.int64)
        for arguments, expected in selector_cases():
            count, records, depths, metadata, *outputs = arguments
            classify[1, 32](count, records, depths, cuda.to_device(metadata),
                *(cuda.to_device(values) for values in outputs), alias)
            self.assertEqual(tuple(alias.copy_to_host()), expected)

    def test_variable_sole_depth_packets_preserve_every_output_bit_and_reject_changed_initial(self):
        cuda = gpu._CUDA
        generator = np.random.default_rng(71811)
        count, records, depths = 8193, 3, 5
        for mode in ('varying', 'ties', 'different_reflection', 'different_contribution', 'metadata_off'):
            for initial in (0.0, 1.0):
                metadata = selector_case()[3]
                outputs = [generator.random(shape) for shape in ((3, 2), (depths, 2), (3, 5), (depths, 7))]
                outputs[0][1, 0] = outputs[1][1, 0] = initial
                outputs[2][1, reducer.OUTCOME_EMITTED] = outputs[3][1, reducer.CONTRIBUTION_DEPTH_EMITTED] = initial
                if mode == 'different_reflection':
                    outputs[0][1, 0] = np.nextafter(initial, 2.0)
                if mode == 'different_contribution':
                    outputs[2][1, reducer.OUTCOME_EMITTED] = np.nextafter(initial, 2.0)
                if mode == 'metadata_off':
                    metadata = metadata[:5]
                power = generator.random(count) * 0.001
                if mode == 'ties':
                    power = np.spacing(1.0) * (generator.integers(0, 12, count) + 0.5)
                expected = [values.copy() for values in outputs]
                for value in power:
                    expected[0][1, 0] += value
                    expected[1][1, 0] += value
                    expected[2][1, reducer.OUTCOME_EMITTED] += value
                    expected[3][1, reducer.CONTRIBUTION_DEPTH_EMITTED] += value
                status = np.zeros((count, records), dtype=np.uint16, order='F')
                status[:, 0] = reducer.STATUS_EMITTED
                emitted = np.full((count, records), np.nan, order='F')
                emitted[:, 0] = power
                lobe_counts = np.zeros((3, 5), dtype=np.int64)
                depth_counts = np.zeros((depths, 5), dtype=np.int64)
                lobe_counts[1, reducer.OUTCOME_EMITTED] = count
                depth_counts[1, reducer.REF_DEPTH_EMITTED] = count
                device_outputs = [cuda.to_device(values) for values in outputs]
                workspace = emissions.ensure_workspace(cuda, None, count, records, depths)
                events = SimpleNamespace(ray_count=count, terminal_kind=cuda.to_device(
                    np.full(count, reducer.TERMINAL_ESCAPED, dtype=np.int8)),
                    terminal_depth=cuda.to_device(np.ones(count, dtype=np.int16)),
                    event_status=cuda.to_device(status), event_lobes=cuda.to_device(np.ones((count, records), dtype=np.int8)),
                    event_emitted_power=cuda.to_device(emitted))
                state = dict(zip(('reflection_lobe_flux_lumen', 'reflection_depth_flux_lumen',
                    'contribution_lobe_flux_lumen', 'contribution_depth_flux_lumen'), device_outputs, strict=True))
                state.update(reflection_lobe_counts=cuda.to_device(lobe_counts), reflection_depth_counts=cuda.to_device(depth_counts))
                emissions.accumulate_device_ordered_emissions(cuda, workspace, events, state, cuda.to_device(metadata))
                for actual, reference in zip(device_outputs, expected, strict=True):
                    np.testing.assert_array_equal(actual.copy_to_host().view(np.uint64), reference.view(np.uint64))
                alias = getattr(self.alias_kernel.scratch, 'last_alias', None)
                if mode == 'metadata_off':
                    self.assertIsNone(alias)
                else:
                    self.assertEqual(tuple(alias.copy_to_host()), (1, 1) if mode in ('varying', 'ties') else (-1, -1))

    def test_real_mis_selects_alias_and_matches_strict_cpu_receiver_and_emission_results(self):
        for seed in (7169, 7173):
            cpu = raytracer.run_direct_ray_trace(_build_reflected_case(65553, seed, 'receiver_mis'))
            actual = raytracer.run_direct_ray_trace(_build_reflected_case(65553, seed, 'receiver_mis', backend='gpu_cuda'))
            parity = compare_semantic_payloads(semantic_payload(cpu), semantic_payload(actual),
                absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
            self.assertTrue(parity.passed, parity.to_dict())
            self.assertTrue(parity.discrete_exact)
            self.assertEqual(tuple(self.alias_kernel.scratch.last_alias.copy_to_host()), (1, 1))
            performance = actual.metrics['_performance_summary']
            self.assertEqual(performance['compute_execution_state'], 'gpu_active')
            self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
            self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


class EmissionAliasTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        EmittedFluxGpuTests.setUpClass.__func__(cls)

    def run_candidate(self, trace_input, *, fail_second=False, **options):
        with ExitStack() as stack:
            stack.enter_context(receiver_parity_maps(gpu._CUDA))
            aliased = stack.enter_context(emission_aliases(gpu._CUDA))
            stack.enter_context(patch.object(resident, '_WORKSPACES', threading.local()))
            stack.enter_context(patch.object(resident, '_ensure_workspace', new=workspace_factory('packed_reuse')))
            decisions = stack.enter_context(adaptive_packets(trace_input, minimum_rays=0))
            original = emissions.accumulate_device_ordered_emissions
            completed = []
            def inject(*arguments):
                original(*arguments)
                completed.append(arguments[2].ray_count)
                if len(completed) == 2:
                    gpu._CUDA.synchronize()
                    raise RuntimeError('injected second aliased packet failure after device mutation')
            if fail_second:
                stack.enter_context(patch.object(emissions, 'accumulate_device_ordered_emissions', new=inject))
            result = raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=131072, **options)
            self.assertTrue(any(decision['coalesced'] for decision in decisions))
            alias = getattr(aliased.scratch, 'last_alias', None)
            selected = tuple(alias.copy_to_host()) if alias is not None else None
        return result, selected, completed

    def assert_physical(self, expected, actual):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)

    def test_coalesced_partial_tail_preserves_receiver_maps_and_display_paths(self):
        trace_input = _build_reflected_case(262163, 7173, 'receiver_mis', backend='gpu_cuda')
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 17
        expected = raytracer.run_direct_ray_trace(trace_input)
        actual, selected, _ = self.run_candidate(trace_input)
        self.assert_physical(expected, actual)
        self.assertEqual(expected.stored_paths, actual.stored_paths)
        self.assertEqual(selected, (1, 1))
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 3)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_stop_retains_the_same_committed_primary_prefix(self):
        trace_input = _build_reflected_case(262163, 7172, 'receiver_mis', backend='gpu_cuda')
        def execute(candidate):
            stopped = [False]
            def progress(processed, total):
                stopped[0] = processed >= 131072
            options = {'progress_callback': progress, 'should_stop': lambda: stopped[0]}
            return self.run_candidate(trace_input, **options)[0] if candidate else raytracer.run_direct_ray_trace(trace_input, **options)
        expected, actual = execute(False), execute(True)
        self.assertEqual(expected.total_rays, 131072)
        self.assertEqual(actual.total_rays, 131072)
        self.assert_physical(expected, actual)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_success_count'], 1)

    def test_failed_second_aliased_packet_replays_only_the_uncommitted_suffix(self):
        trace_input = _build_reflected_case(262163, 7171, 'receiver_mis', backend='gpu_cuda')
        expected = raytracer.run_direct_ray_trace(trace_input)
        actual, _, completed = self.run_candidate(trace_input, fail_second=True)
        self.assertEqual(completed, [131072, 131072])
        self.assert_physical(expected, actual)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)


if __name__ == '__main__':
    unittest.main()
