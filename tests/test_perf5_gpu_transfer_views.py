from __future__ import annotations

import gc
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from test_perf5_gpu_static_transfer import ArrayCuda
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_transfer as transfer


def arrays_to_upload():
    return {'flags': np.asarray([1, 0, 1], dtype=np.int8),
        'float_bits': np.asarray([0, 1 << 63, 0x7FF8000000000042], dtype=np.uint64).view(np.float64),
        'wide_ids': np.asarray([2 ** 60 + 3, -2 ** 60 - 5], dtype=np.int64),
        'empty': np.empty((0, 3), dtype=np.float64),
        'strided': np.arange(24, dtype=np.float64).reshape(4, 6)[::2],
        'scalar': np.asarray(2 ** 60 + 1, dtype=np.int64)}


class TransferViewTests(unittest.TestCase):
    def test_fake_arrays_keep_exact_values_in_both_modes_without_importing_cuda(self):
        source = arrays_to_upload()
        expected = {name: values.tobytes() for name, values in source.items()}
        for mode in ('sliced', 'direct'):
            with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': mode}):
                fake = ArrayCuda()
                root, views = transfer.upload_aligned_arrays(fake, source)
            self.assertEqual(len(fake.uploads), 1)
            for name, values in views.items():
                self.assertEqual(values.shape, source[name].shape)
                self.assertEqual(values.dtype, source[name].dtype)
                self.assertEqual(values.tobytes(), expected[name])
                self.assertEqual(source[name].tobytes(), expected[name])
                self.assertEqual((values.ctypes.data - root.ctypes.data) % 256, 0)
        script = "import sys,numpy as np; from leakage_simulator.gpu_cuda_transfer import create_device_views; "
        script += "create_device_views(np.zeros(8,dtype=np.uint8),{'value':(0,8,np.dtype('float64'),(1,))}); "
        script += "assert 'numba.cuda' not in sys.modules"
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
            env={**os.environ, 'PYTHONPATH': str(ROOT / 'src'), 'LEAKAGE_GPU_TRANSFER_VIEWS': 'direct'},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_ranges_shapes_alignment_and_modes_fail_closed(self):
        device = np.zeros(32, dtype=np.uint8)
        for mode in ('sliced', 'direct'):
            with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': mode}):
                for start, end, dtype, shape in ((-1, 7, 'float64', (1,)),
                    (0, 33, 'uint8', (33,)), (9, 8, 'uint8', (0,)),
                    (0, 8, 'float64', (2,)), (1, 9, 'float64', (1,)),
                    (0, 8, 'float64', (-1, -1)), (0, 8, 'object', (1,))):
                    with self.assertRaises(ValueError):
                        transfer.create_device_views(device, {'bad': (start, end, np.dtype(dtype), shape)})
        with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': 'invalid'}):
            with self.assertRaises(ValueError):
                transfer.create_device_views(device, {})


class TransferViewGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in
            ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_direct_views_preserve_bits_alignment_independence_and_owner_lifetime(self):
        source = arrays_to_upload()
        expected = {name: values.tobytes() for name, values in source.items()}
        with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': 'direct'}):
            root, views = transfer.upload_aligned_arrays(gpu._CUDA, source)
        ranges = []
        for name, values in views.items():
            self.assertEqual(values.shape, source[name].shape)
            self.assertEqual(values.dtype, source[name].dtype)
            self.assertEqual(values.copy_to_host().tobytes(), expected[name])
            if values.nbytes:
                start = values.device_ctypes_pointer.value
                self.assertEqual(start % 256, 0)
                ranges.append((start, start + values.nbytes))
        ranges.sort()
        self.assertTrue(all(first[1] <= second[0] for first, second in zip(ranges, ranges[1:])))
        del root
        gc.collect()
        for name, values in views.items():
            self.assertEqual(values.copy_to_host().tobytes(), expected[name])
        views['flags'].copy_to_device(np.asarray([0, 1, 0], dtype=np.int8))
        for name in source.keys() - {'flags'}:
            self.assertEqual(views[name].copy_to_host().tobytes(), expected[name])

    def test_stream_binding_and_empty_buffer_do_not_allocate_per_field(self):
        cuda = gpu._CUDA
        stream = cuda.stream()
        host = np.arange(32, dtype=np.uint8)
        root = cuda.to_device(host, stream=stream)
        layout = {'first': (0, 16, np.dtype('int64'), (2,)),
            'second': (16, 32, np.dtype('float64'), (2,)),
            'empty': (32, 32, np.dtype('float64'), (0, 3))}
        with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': 'direct'}), patch.object(
            cuda.current_context(), 'memalloc', side_effect=AssertionError('A view allocated CUDA memory')
        ):
            views = transfer.create_device_views(root, layout)
            empty = transfer.create_device_views(cuda.to_device(np.empty(0, dtype=np.uint8)),
                {'empty': (0, 0, np.dtype('int64'), (0,))})
        stream.synchronize()
        for name, values in views.items():
            self.assertIs(values.stream, stream)
            self.assertEqual(values.copy_to_host().tobytes(), host[layout[name][0]:layout[name][1]].tobytes())
        self.assertEqual(empty['empty'].size, 0)


if __name__ == '__main__':
    unittest.main()
