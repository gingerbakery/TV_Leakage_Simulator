from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_unit_norm import candidate_factory, normalized_helper
from leakage_simulator import gpu_cuda_intersection as gpu


@lru_cache(maxsize=1)
def vectors():
    generator = np.random.default_rng(191921)
    random = generator.normal(size=(10000, 3))
    random /= np.linalg.norm(random, axis=1)[:, None]
    rows = [[sign if axis == coordinate else zero for coordinate in range(3)]
        for axis in range(3) for sign in (-1.0, 1.0) for zero in (-0.0, 0.0)]
    rows.extend([[value, 0.0, -0.0] for value in (
        0.0, -0.0, np.nextafter(1.0, 0.0), np.nextafter(1.0, 2.0),
        np.nextafter(0.0, 1.0), 1e-16, 1e-15, 1e-14, 1e-100,
        1e100, 1e200, np.inf, -np.inf, np.nan)])
    base = np.concatenate((np.asarray(rows, dtype=np.float64), random,
        np.nextafter(random, 0.0), np.nextafter(random, np.copysign(np.inf, random))))
    return np.ascontiguousarray(base)


class ExactUnitNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preflight = gpu.preflight_gpu_cuda(refresh=True)
        for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified'):
            if getattr(cls.preflight, name) is not True:
                raise RuntimeError(f'Production CUDA preflight failed: {name}')
        if cls.preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_factory_preserves_original_keyword_only_signature(self):
        import inspect
        from leakage_simulator import gpu_cuda_resident_wavefront as resident
        self.assertEqual(inspect.signature(candidate_factory()), inspect.signature(resident._make_kernel))

    def test_real_cuda_normalization_matches_original_finite_bits_and_nan_mask(self):
        cuda = gpu._CUDA
        original = normalized_helper(False)
        shortcut = normalized_helper(True)

        @cuda.jit
        def compute(inputs, before, after):
            index = cuda.grid(1)
            if index < inputs.shape[0]:
                original_x, original_y, original_z = original(inputs[index, 0], inputs[index, 1], inputs[index, 2])
                shortcut_x, shortcut_y, shortcut_z = shortcut(inputs[index, 0], inputs[index, 1], inputs[index, 2])
                before[index, 0], before[index, 1], before[index, 2] = original_x, original_y, original_z
                after[index, 0], after[index, 1], after[index, 2] = shortcut_x, shortcut_y, shortcut_z

        inputs = vectors()
        device = cuda.to_device(inputs)
        before = cuda.device_array(inputs.shape, dtype=np.float64)
        after = cuda.device_array(inputs.shape, dtype=np.float64)
        compute[(len(inputs) + 127) // 128, 128](device, before, after)
        original_result, shortcut_result = before.copy_to_host(), after.copy_to_host()
        np.testing.assert_array_equal(np.isnan(original_result), np.isnan(shortcut_result))
        valid = ~np.isnan(original_result)
        np.testing.assert_array_equal(original_result.view(np.uint64)[valid], shortcut_result.view(np.uint64)[valid])


if __name__ == '__main__':
    unittest.main()
