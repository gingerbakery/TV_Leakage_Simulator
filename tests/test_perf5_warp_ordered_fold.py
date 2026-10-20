from __future__ import annotations

from pathlib import Path
import importlib
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src')]

from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.gpu_cuda_receiver_moments import make_warp_ordered_fold


def cases():
    rng = np.random.default_rng(917273)
    samples = []
    for exponent in (-1022, -1000, -100, -10, 0, 10, 100, 1000):
        base = np.ldexp(1.0, exponent)
        unit = np.spacing(base)
        for offset in (0, 1, 2, 17, (1 << 51) - 17, (1 << 52) - 17):
            initial = np.asarray(base).view(np.uint64) + np.uint64(offset)
            initial = initial.view(np.float64).item()
            for style in range(8):
                if style < 4:
                    multipliers = np.arange(32) * 0.5 + 0.5 if style == 0 else rng.integers(0, 20, 32) + 0.5
                    values = unit * multipliers
                elif style == 4:
                    values = rng.random(32) * unit * 1e5
                elif style == 5:
                    values = rng.random(32) * initial * 0.25
                elif style == 6:
                    values = np.full(32, unit * 0.5)
                else:
                    values = np.zeros(32)
                masks = rng.random(32) > 0.25
                samples.append((initial, values, masks))
    for initial in (0.0, -0.0, np.nextafter(0.0, 1.0), np.finfo(np.float64).max / 2):
        for scale in (np.nextafter(0.0, 1.0), 1e-300, 1.0, 1e300):
            samples.append((initial, rng.random(32) * scale, rng.random(32) > 0.25))
    for repeat in range(2000):
        initial = np.ldexp(rng.uniform(1.0, 2.0), int(rng.integers(-1000, 1000)))
        values = np.ldexp(rng.random(32), rng.integers(-1074, 1000, 32))
        samples.append((initial, values, rng.random(32) > 0.25))
    for repeat in range(1000):
        initial = np.ldexp(rng.uniform(1.0, 2.0), int(rng.integers(-1000, 1000)))
        values = np.spacing(initial) * (rng.integers(0, 1000000, 32) + 0.5)
        samples.append((initial, values, rng.random(32) > 0.25))
    samples.append((np.finfo(np.float64).max, np.full(32, np.finfo(np.float64).max), np.ones(32, dtype=np.bool_)))
    for initial in (-1.0, 1.0, -0.0):
        samples.append((initial, rng.uniform(-1.0, 1.0, 32), np.ones(32, dtype=np.bool_)))
        samples.append((initial, np.ones(32), np.zeros(32, dtype=np.bool_)))
    return samples


class WarpOrderedFoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_ieee_rounding_ties_binades_masks_subnormals_and_overflow(self):
        cuda = gpu._CUDA
        fold = make_warp_ordered_fold(cuda)
        samples = cases()
        initial = np.asarray([sample[0] for sample in samples])
        values = np.asarray([sample[1] for sample in samples])
        valid = np.asarray([sample[2] for sample in samples])
        expected = initial.copy()
        with np.errstate(over='ignore'):
            for index in range(len(samples)):
                for lane in range(32):
                    if valid[index, lane]:
                        expected[index] = expected[index] + values[index, lane]

        @cuda.jit(fastmath=False)
        def execute(initial, values, valid, output, fast):
            address = cuda.grid(1)
            case = address // 32
            lane = address & 31
            if case >= initial.size:
                return
            total, accelerated = fold(initial[case], values[case, lane], valid[case, lane])
            if lane == 0:
                output[case] = total
                fast[case] = accelerated

        actual = cuda.device_array(len(samples), dtype=np.float64)
        fast = cuda.device_array(len(samples), dtype=np.bool_)
        execute[(len(samples) + 3) // 4, 128](cuda.to_device(initial), cuda.to_device(values), cuda.to_device(valid), actual, fast)
        cuda.synchronize()
        np.testing.assert_array_equal(actual.copy_to_host().view(np.uint64), expected.view(np.uint64))
        accelerated = int(np.count_nonzero(fast.copy_to_host()))
        self.assertGreater(accelerated, 500)
        self.assertLess(accelerated, len(samples) - 100)

    def test_squared_packets_round_before_the_accumulation(self):
        cuda = gpu._CUDA
        libdevice = importlib.import_module('numba.cuda.libdevice')
        fold = make_warp_ordered_fold(cuda)
        exponents = (-500, -200, 0, 200, 480)
        powers = np.asarray([[np.ldexp(1.0 + 2.0 ** -27, exponent)] * 32 for exponent in exponents])
        initial = np.asarray([np.ldexp(1.0, 27 + 2 * exponent) for exponent in exponents])
        expected = initial.copy()
        for index in range(len(initial)):
            for lane in range(32):
                square = powers[index, lane] * powers[index, lane]
                expected[index] = expected[index] + square

        @cuda.jit(fastmath=False)
        def execute(initial, powers, output):
            case = cuda.grid(1) // 32
            lane = cuda.threadIdx.x & 31
            if case >= initial.size:
                return
            square = libdevice.dmul_rn(powers[case, lane], powers[case, lane])
            total, _ = fold(initial[case], square, True)
            if lane == 0:
                output[case] = total

        output = cuda.device_array(initial.size, dtype=np.float64)
        execute[2, 128](cuda.to_device(initial), cuda.to_device(powers), output)
        np.testing.assert_array_equal(output.copy_to_host().view(np.uint64), expected.view(np.uint64))


if __name__ == '__main__':
    unittest.main()
