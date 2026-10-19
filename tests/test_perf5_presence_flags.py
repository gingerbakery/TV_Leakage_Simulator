from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


class PresenceFlagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_active_subsets_and_partial_warps_preserve_every_flag(self):
        cuda = gpu._CUDA
        masks = np.zeros((1025, 32), dtype=np.bool_)
        masks[0, 31] = True
        masks[1, 1] = True
        masks[2] = True
        masks[3, ::2] = True
        masks[4, 1::2] = True
        masks[5:] = np.random.default_rng(71920).random((1020, 32)) > 0.7
        selected = cuda.to_device(masks)
        for aggregated in (False, True):
            with self.subTest(aggregated=aggregated):
                mark = resident._make_presence_marker(cuda, aggregated)

                @cuda.jit
                def execute(masks, flags, writes):
                    address = cuda.grid(1)
                    group = address // 32
                    lane = address & 31
                    if group >= masks.shape[0]:
                        return
                    if masks[group, lane]:
                        writes[group, lane] = mark(flags, group)

                flags = cuda.to_device(np.zeros(masks.shape[0], dtype=np.int64))
                writes = cuda.to_device(np.zeros(masks.shape, dtype=np.bool_))
                execute[(masks.size + 127) // 128, 128](selected, flags, writes)
                np.testing.assert_array_equal(flags.copy_to_host(), np.any(masks, axis=1))
                attempted = writes.copy_to_host()
                self.assertFalse(np.any(attempted & ~masks))
                self.assertTrue(np.all(attempted.sum(axis=1) >= np.any(masks, axis=1)))
                if not aggregated:
                    np.testing.assert_array_equal(attempted, masks)
                else:
                    self.assertLess(int(attempted.sum()), int(masks.sum()) // 2)

    def test_partial_last_warp_can_set_the_flag_without_lane_zero(self):
        cuda = gpu._CUDA
        mark = resident._make_presence_marker(cuda, True)

        @cuda.jit
        def execute(count, flags):
            address = cuda.grid(1)
            if address >= count:
                return
            if address == count - 1:
                mark(flags, 0)

        for count in (2, 31, 33, 63, 65, 127, 131):
            with self.subTest(count=count):
                flags = cuda.to_device(np.zeros(1, dtype=np.int64))
                execute[(count + 127) // 128, 128](count, flags)
                np.testing.assert_array_equal(flags.copy_to_host(), [1])

    def test_scalar_and_warp_markers_keep_the_exact_host_reduced_trace(self):
        results = []
        for mode in ('scalar', 'warp'):
            with patch.dict(os.environ, {'LEAKAGE_GPU_PRESENCE_FLAGS': mode}), variant(resident._make_kernel, {}):
                result = run_direct_ray_trace(_build_reflected_case(8193, 7172, 'receiver_mis', backend='gpu_cuda'), gpu_accumulator='host')
            performance = result.metrics['_performance_summary']
            self.assertEqual(performance['compute_execution_state'], 'gpu_active')
            self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
            self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
            results.append(semantic_payload(result))
        parity = compare_semantic_payloads(*results)
        self.assertTrue(parity.semantic_exact, parity.to_dict())

    def test_invalid_mode_fails_closed_before_compilation(self):
        with patch.dict(os.environ, {'LEAKAGE_GPU_PRESENCE_FLAGS': 'invalid'}):
            with self.assertRaisesRegex(resident.GpuResidentWavefrontProviderError, 'gpu_resident_presence_flag_mode_invalid'):
                resident._make_kernel()


if __name__ == '__main__':
    unittest.main()
