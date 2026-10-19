from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_gpu_pair_production as baseline
from perf5_packed_nodes import INTEGER_FIELDS, device_variant, make_kernel, pack_nodes
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident


class PackedNodeLayoutTests(unittest.TestCase):
    def test_both_layouts_preserve_all_bound_bits_and_signed_integer_values(self):
        scene = gap_case().mesh.prepare_gpu_cuda_scene()
        count = len(scene.node_count)
        lower = np.resize(np.asarray([-0.0, 0.0, np.nextafter(0.0, 1.0), -1e200, 1e200, 1.25]), (count, 3))
        upper = np.nextafter(lower, np.inf)
        indices = np.resize(np.asarray([-2147483648, 2147483647, -1, 0, 1], dtype=np.int64), count)
        modified = replace(scene, node_bounds_min=lower, node_bounds_max=upper,
            **{name: np.roll(indices, column) for column, name in enumerate(INTEGER_FIELDS)})
        for layout in (64, 80):
            packed = pack_nodes(modified, layout)
            self.assertEqual(packed.nbytes, count * layout)
            self.assertTrue(packed.flags.c_contiguous)
            self.assertFalse(packed.flags.writeable)
            np.testing.assert_array_equal(packed[:, :3].view(np.uint64), lower.view(np.uint64))
            np.testing.assert_array_equal(packed[:, 3:6].view(np.uint64), upper.view(np.uint64))
            integers = packed[:, 6:].view(np.int32 if layout == 64 else np.int64)
            for column, name in enumerate(INTEGER_FIELDS):
                np.testing.assert_array_equal(integers[:, column].astype(np.int64), getattr(modified, name))

    def test_invalid_shapes_types_and_int32_overflow_are_rejected_without_truncation(self):
        scene = gap_case().mesh.prepare_gpu_cuda_scene()
        for value in (2 ** 31, -(2 ** 31) - 1):
            values = scene.node_left.copy()
            values[0] = value
            modified = replace(scene, node_left=values)
            with self.assertRaises(ValueError):
                pack_nodes(modified, 64)
            self.assertEqual(int(pack_nodes(modified, 80).view(np.int64)[0, 6]), value)
        for modified in (replace(scene, node_left=scene.node_left.astype(np.float64)),
                replace(scene, node_bounds_min=scene.node_bounds_min.astype(np.float32)),
                replace(scene, node_count=scene.node_count[:-1])):
            with self.assertRaises(ValueError):
                pack_nodes(modified, 64)


class Packed64GpuTests(baseline.ProductionPairGpuTests):
    layout = 64

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kernel = make_kernel(cls.layout)
        cls.compiled = False
        cls.devices = {}

    def setUp(self):
        self.enterContext(patch.object(resident, '_PAIR_KERNEL', type(self).kernel))
        self.enterContext(patch.object(resident, '_PAIR_KERNEL_COMPILED', type(self).compiled))
        self.enterContext(device_variant(type(self).layout, type(self).devices))

    def tearDown(self):
        type(self).compiled = resident._PAIR_KERNEL_COMPILED

    def test_actual_cuda_decoding_preserves_packed_nan_bits_and_signed_index_extremes(self):
        cuda = gpu._CUDA
        import importlib
        libdevice = importlib.import_module('numba.cuda.libdevice')
        scene = gap_case().mesh.prepare_gpu_cuda_scene()
        values = np.resize(np.asarray([-2147483648, 2147483647, -1, 0, 1], dtype=np.int64), len(scene.node_count))
        modified = replace(scene, **{name: np.roll(values, column) for column, name in enumerate(INTEGER_FIELDS)})
        packed = pack_nodes(modified, self.layout)
        narrow = self.layout == 64

        @cuda.jit
        def decode(nodes, output):
            index = cuda.grid(1)
            if index < output.shape[0]:
                for field in range(4):
                    if narrow:
                        bits = libdevice.double_as_longlong(nodes[index, 6 + field // 2])
                        if field & 1:
                            bits = bits >> 32
                        output[index, field] = np.int64(np.int32(bits))
                    else:
                        output[index, field] = libdevice.double_as_longlong(nodes[index, 6 + field])

        device = cuda.to_device(packed)
        output = cuda.device_array((len(values), 4), dtype=np.int64)
        decode[(len(values) + 127) // 128, 128](device, output)
        np.testing.assert_array_equal(output.copy_to_host(), np.column_stack(
            [getattr(modified, name) for name in INTEGER_FIELDS]))


class Packed80GpuTests(Packed64GpuTests):
    layout = 80


if __name__ == '__main__':
    unittest.main()
