from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_gpu_primary_order import MODES, ScheduledKernel, ordered_factory, primary_order
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


class PrimarySchedulingTests(unittest.TestCase):
    def test_orders_are_stable_permutations_without_mutating_source_samples(self):
        generator = np.random.default_rng(710191)
        for count in (0, 1, 31, 128, 1025):
            origins = generator.random((count, 3))
            origins[:, 2] = 0.0
            directions = generator.normal(size=(count, 3))
            directions /= np.maximum(np.linalg.norm(directions, axis=1, keepdims=True), 1e-100)
            saved_origins, saved_directions = origins.copy(), directions.copy()
            for mode in MODES:
                order = primary_order(origins, directions, mode)
                np.testing.assert_array_equal(np.sort(order), np.arange(count))
                np.testing.assert_array_equal(order, primary_order(origins, directions, mode))
                np.testing.assert_array_equal(origins, saved_origins)
                np.testing.assert_array_equal(directions, saved_directions)
        for mode in MODES:
            np.testing.assert_array_equal(primary_order(np.zeros((33, 3)), np.zeros((33, 3)), mode), np.arange(33))
        with self.assertRaises(ValueError):
            primary_order(np.zeros((1, 3)), np.zeros((1, 3)), 'invalid')


class PrimarySchedulingGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_original_output_rows_rng_and_host_summary_remain_exact(self):
        trace_input = _build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda')
        trace_input.config.store_ray_paths = True
        with variant(resident._make_kernel, {}):
            expected = run_direct_ray_trace(trace_input, gpu_accumulator='host')
        factory, _ = ordered_factory()
        original_launch = resident._launch_resident_kernel
        for mode in MODES[1:]:
            def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
                order = primary_order(batch.origins[:count], batch.directions[:count], mode)
                return original_launch(ScheduledKernel(kernel, gpu._CUDA.to_device(order)), context, scene,
                    bindings, workspace, batch, count, depths, **keywords)

            with self.subTest(mode=mode), variant(factory, {}), patch.object(resident, '_launch_resident_kernel', new=launch):
                actual = run_direct_ray_trace(trace_input, gpu_accumulator='host')
                parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual))
                self.assertTrue(parity.semantic_exact, parity.to_dict())
                performance = actual.metrics['_performance_summary']
                self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)


if __name__ == '__main__':
    unittest.main()
