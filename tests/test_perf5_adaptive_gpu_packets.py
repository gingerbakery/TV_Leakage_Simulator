from __future__ import annotations

from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from perf5_adaptive_gpu_packets import adaptive_batch_request, adaptive_packets
from perf5_workspace_packing import workspace_factory
import test_perf5_gpu_pair_production as production
import profile_perf5_device_stages as stage_profile
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


class AdaptivePacketsCpuTests(unittest.TestCase):
    def test_stage_profile_uses_the_requested_mis_ray_count(self):
        expected = object()
        with patch.object(stage_profile, '_build_reflected_case', return_value=expected) as build:
            self.assertIs(stage_profile.build_profile_scene('mis', 1, 2, 1000000), expected)
        build.assert_called_once_with(1000000, 7172, 'receiver_mis', backend='gpu_cuda')

    def test_requested_dispatch_size_changes_only_when_both_ray_count_and_budget_fit(self):
        trace_input = WORKLOADS['trapped_corridor_depth10'](1000000)
        self.assertEqual(adaptive_batch_request(trace_input), 131072)
        trace_input.config.max_depth = 20
        self.assertIsNone(adaptive_batch_request(trace_input))
        trace_input.config.max_depth = 1000
        self.assertIsNone(adaptive_batch_request(trace_input))
        trace_input.config.max_depth = 10
        trace_input.emitters[0].ray_count = 100000
        self.assertIsNone(adaptive_batch_request(trace_input))

    def test_small_or_budget_limited_inputs_keep_the_original_iterator(self):
        for rays, target in ((100000, 131072), (1000000, 65536)):
            trace_input = WORKLOADS['trapped_corridor_depth10'](rays)
            batches, stats = object(), object()
            result = object()
            with patch.object(raytracer, '_build_gpu_resident_wavefront_context', return_value=object()), patch.object(
                raytracer, 'prefetch_primary_batches', return_value=result
            ) as prefetch, patch.object(resident, 'event_window_batch_size', return_value=target):
                with adaptive_packets(trace_input) as decisions:
                    raytracer._build_gpu_resident_wavefront_context()
                    actual = raytracer.prefetch_primary_batches(batches, stats, enabled=True)
            self.assertIs(actual, result)
            self.assertIs(prefetch.call_args.args[0], batches)
            self.assertFalse(decisions[0]['coalesced'])

    def test_eligible_packets_preserve_every_sample_bit_and_partial_tail(self):
        generator = np.random.default_rng(42)
        batches = [(generator.normal(size=(count, 3)), generator.normal(size=(count, 3)),
            np.arange(offset, offset + count, dtype=np.int64), generator.random(count), index)
            for index, (offset, count) in enumerate(((0, 4), (4, 4), (8, 3)))]
        expected = [np.concatenate([batch[index] for batch in batches]) for index in range(4)]
        snapshots = [[array.tobytes() for array in batch[:4]] for batch in batches]
        trace_input = WORKLOADS['trapped_corridor_depth10'](1000000)
        with patch.object(raytracer, '_build_gpu_resident_wavefront_context', return_value=object()), patch.object(
            raytracer, 'prefetch_primary_batches', side_effect=lambda values, stats, enabled: values
        ), patch.object(resident, 'event_window_batch_size', return_value=8):
            with adaptive_packets(trace_input, target_rays=8) as decisions:
                raytracer._build_gpu_resident_wavefront_context()
                actual = list(raytracer.prefetch_primary_batches(iter(batches), object(), enabled=False))
        self.assertTrue(decisions[0]['coalesced'])
        self.assertEqual([len(batch[0]) for batch in actual], [8, 3])
        self.assertEqual(sum(batch[4] for batch in actual), sum(batch[4] for batch in batches))
        for index in range(4):
            self.assertEqual(np.concatenate([batch[index] for batch in actual]).tobytes(), expected[index].tobytes())
        self.assertEqual([[array.tobytes() for array in batch[:4]] for batch in batches], snapshots)

    def test_missing_resident_context_never_coalesces(self):
        trace_input = WORKLOADS['face_direct'](1000000)
        batches = object()
        with patch.object(raytracer, 'prefetch_primary_batches', side_effect=lambda values, stats, enabled: values):
            with adaptive_packets(trace_input) as decisions:
                self.assertIs(raytracer.prefetch_primary_batches(batches, object(), enabled=False), batches)
        self.assertFalse(decisions[0]['coalesced'])


class AdaptivePacketsGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        production.ProductionPairGpuTests.setUpClass.__func__(cls)

    def setUp(self):
        self.candidate = workspace_factory('packed_reuse')

    def assert_physical(self, expected, actual):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(parity.discrete_exact)

    def test_partial_tail_and_committed_prefix_failure_preserve_original_samples(self):
        trace_input = WORKLOADS['stochastic_two_bounce'](262163)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        expected = raytracer.run_direct_ray_trace(trace_input)
        launched = []
        original = resident._launch_resident_kernel
        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **options):
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **options)
            launched.append(count)
            if len(launched) == 2:
                raise resident.GpuResidentWavefrontProviderError('execute', 'injected_coalesced_second_packet_failure')
            return result
        with patch.object(resident, '_WORKSPACES', threading.local()), patch.object(
            resident, '_ensure_workspace', new=self.candidate
        ), adaptive_packets(trace_input, minimum_rays=0) as decisions:
            actual = raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=131072)
        self.assert_physical(expected, actual)
        self.assertTrue(any(decision['coalesced'] for decision in decisions))
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 3)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
        with patch.object(resident, '_WORKSPACES', threading.local()), patch.object(
            resident, '_ensure_workspace', new=self.candidate
        ), patch.object(resident, '_launch_resident_kernel', new=launch), adaptive_packets(trace_input, minimum_rays=0):
            recovered = raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=131072)
        self.assert_physical(expected, recovered)
        self.assertEqual(launched, [131072, 131072])
        performance = recovered.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_mixed')
        self.assertEqual(performance['gpu_resident_wavefront_success_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)

    def test_stop_at_same_committed_primary_boundary_keeps_identical_partial_result(self):
        trace_input = WORKLOADS['trapped_corridor_depth10'](262163)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        def execute(candidate):
            stopped = [False]
            def progress(processed, total):
                stopped[0] = processed >= 131072
            options = {'progress_callback': progress, 'should_stop': lambda: stopped[0]}
            if not candidate:
                return raytracer.run_direct_ray_trace(trace_input, **options)
            with patch.object(resident, '_WORKSPACES', threading.local()), patch.object(
                resident, '_ensure_workspace', new=self.candidate
            ), adaptive_packets(trace_input, minimum_rays=0):
                return raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=131072, **options)
        expected, actual = execute(False), execute(True)
        self.assertEqual(expected.total_rays, 131072)
        self.assertEqual(actual.total_rays, 131072)
        self.assert_physical(expected, actual)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_success_count'], 1)


if __name__ == '__main__':
    unittest.main()
