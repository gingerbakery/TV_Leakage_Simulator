from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS
from benchmark_perf5_transport import semantic_payload
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import native_cpu_sampling
from leakage_simulator import raytracer
from leakage_simulator.gpu_cuda_resident_wavefront import GpuResidentWavefrontProviderError
from leakage_simulator.primary_prefetch import PrimaryPrefetchStats, managed_primary_prefetch, prefetch_primary_batches
from leakage_simulator.raytracer import run_direct_ray_trace
from perf4_accuracy import compare_semantic_payloads


class PrimaryPrefetchTests(unittest.TestCase):
    def assertNoWorkers(self):
        self.assertFalse([thread.name for thread in threading.enumerate() if thread.name.startswith('bitsam-primary')])

    def test_order_random_stream_and_metrics_are_preserved(self):
        stats = PrimaryPrefetchStats()
        baseline = native_cpu_sampling.snapshot()

        def source():
            generator = np.random.default_rng(7917)
            for index in range(23):
                native_cpu_sampling._record('success_rows', index + 1)
                native_cpu_sampling._record('success_batches', 1)
                yield generator.random(100)

        @managed_primary_prefetch
        def collect():
            return list(prefetch_primary_batches(source(), stats, enabled=True))

        actual = collect()
        generator = np.random.default_rng(7917)
        for batch in actual:
            np.testing.assert_array_equal(batch.view(np.uint64), generator.random(100).view(np.uint64))
        delta = native_cpu_sampling.delta_stats(baseline)
        self.assertEqual(delta['native_primary_preparation_success_rows'], sum(range(1, 24)))
        self.assertEqual(delta['native_primary_preparation_success_batches'], 23)
        self.assertEqual(stats.emitter_count, 1)
        self.assertEqual(stats.consumed_batches, 23)
        self.assertNoWorkers()

    def test_one_batch_ahead_and_cancellation_release_generator(self):
        prepared = threading.Event()
        closed = threading.Event()
        produced = []

        def source():
            try:
                for index in range(100):
                    produced.append(index)
                    if index == 1:
                        prepared.set()
                    yield index
            finally:
                closed.set()

        @managed_primary_prefetch
        def consume_one():
            batches = prefetch_primary_batches(source(), PrimaryPrefetchStats(), enabled=True)
            self.assertEqual(next(batches), 0)
            self.assertTrue(prepared.wait(5))
            self.assertEqual(produced, [0, 1])

        consume_one()
        self.assertTrue(closed.is_set())
        self.assertEqual(produced, [0, 1])
        self.assertNoWorkers()

    def test_worker_and_consumer_failures_are_not_swallowed(self):
        closed = []

        def source():
            try:
                yield 0
                raise ValueError('producer failure')
            finally:
                closed.append(True)

        @managed_primary_prefetch
        def fail(consumer):
            batches = prefetch_primary_batches(source(), PrimaryPrefetchStats(), enabled=True)
            self.assertEqual(next(batches), 0)
            if consumer:
                raise RuntimeError('consumer failure')
            next(batches)

        for consumer, error in ((False, ValueError), (True, RuntimeError)):
            with self.assertRaises(error):
                fail(consumer)
            self.assertNoWorkers()
        self.assertEqual(closed, [True, True])

    def test_disabled_and_unmanaged_iterators_do_not_create_workers(self):
        batches = iter(range(3))
        self.assertIs(prefetch_primary_batches(batches, PrimaryPrefetchStats(), enabled=True), batches)

        @managed_primary_prefetch
        def disabled():
            self.assertIs(prefetch_primary_batches(batches, PrimaryPrefetchStats(), enabled=False), batches)

        disabled()
        self.assertNoWorkers()


class GpuPrimaryPrefetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, key) is True for key in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('production CUDA contract mismatch')

    def test_public_gpu_outputs_paths_and_preparation_counts_match(self):
        for name, builder in WORKLOADS.items():
            with self.subTest(scene=name):
                scene = builder(70000)
                scene.config.compute_backend = 'gpu_cuda'
                scene.config.store_ray_paths = True
                scene.config.max_stored_paths = 19
                with patch.dict(os.environ, {'LEAKAGE_PRIMARY_PREFETCH': 'off'}):
                    expected = run_direct_ray_trace(scene)
                with patch.dict(os.environ, {'LEAKAGE_PRIMARY_PREFETCH': 'on'}):
                    actual = run_direct_ray_trace(scene)
                parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                    absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                self.assertTrue(parity.passed, parity.to_dict())
                before, after = (result.metrics['_performance_summary'] for result in (expected, actual))
                self.assertEqual(after['primary_prefetch_emitter_count'], 1)
                self.assertEqual(after['primary_prefetch_consumed_batches'], 2)
                self.assertEqual(after['primary_prefetch_max_pending_batches'], 1)
                self.assertEqual(after['compute_execution_state'], 'gpu_active')
                self.assertGreater(after['gpu_cuda_gpu_success_count'], 0)
                self.assertEqual(after['gpu_resident_wavefront_fallback_count'], 0)
                for field in ('success_batches', 'success_rows', 'fallback_batches'):
                    key = f'native_primary_preparation_{field}'
                    self.assertEqual(after[key], before[key])
                self.assertFalse([thread.name for thread in threading.enumerate() if thread.name.startswith('bitsam-primary')])

    def test_gpu_failure_replays_the_same_prefetched_primary_sequence(self):
        scene = WORKLOADS['face_direct'](70000)
        scene.config.compute_backend = 'gpu_cuda'
        results = []
        for mode in ('off', 'on'):
            with patch.dict(os.environ, {'LEAKAGE_PRIMARY_PREFETCH': mode}), patch.object(
                raytracer, 'trace_resident_wavefront_gpu_cuda',
                side_effect=GpuResidentWavefrontProviderError('execute', 'injected_failure'),
            ):
                results.append(run_direct_ray_trace(scene))
        parity = compare_semantic_payloads(semantic_payload(results[0]), semantic_payload(results[1]),
            absolute_tolerance=0.0, relative_tolerance=0.0, max_ulp_distance=0)
        self.assertTrue(parity.passed, parity.to_dict())
        performance = results[1].metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertIn(performance['compute_execution_state'], ('gpu_active', 'gpu_mixed'))
        self.assertFalse([thread.name for thread in threading.enumerate() if thread.name.startswith('bitsam-primary')])

    def test_cancellation_keeps_only_completed_batches_and_joins_worker(self):
        scene = WORKLOADS['face_direct'](150000)
        scene.config.compute_backend = 'gpu_cuda'
        results = []
        for mode in ('off', 'on'):
            stopped = threading.Event()

            def progress(completed, total):
                if completed >= 65536:
                    stopped.set()

            with patch.dict(os.environ, {'LEAKAGE_PRIMARY_PREFETCH': mode}):
                results.append(run_direct_ray_trace(scene, progress_callback=progress, should_stop=stopped.is_set))
            self.assertFalse([thread.name for thread in threading.enumerate() if thread.name.startswith('bitsam-primary')])
        self.assertEqual(results[0].total_rays, 65536)
        self.assertEqual(results[1].total_rays, 65536)
        parity = compare_semantic_payloads(semantic_payload(results[0]), semantic_payload(results[1]),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        performance = results[1].metrics['_performance_summary']
        self.assertEqual(performance['primary_prefetch_consumed_batches'], 1)
        self.assertEqual(performance['native_primary_preparation_success_rows'], 2 * 65536)


if __name__ == '__main__':
    unittest.main()
