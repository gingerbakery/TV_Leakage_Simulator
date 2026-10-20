from __future__ import annotations

import ast
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS
from benchmark_perf5_gpu_spatial import PrefixKernel, spatial_classification, spatial_factory
import test_perf5_gpu_coherent as coherent_tests
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


class SpatialContractTests(unittest.TestCase):
    def test_spatial_classes_clamp_degenerate_extreme_and_boundary_values(self):
        for cells in (4, 8):
            body = ast.Module(body=spatial_classification(cells), type_ignores=[])
            program = compile(ast.fix_missing_locations(body), '<spatial_bucket_oracle>', 'exec')
            for origin in ((0.0, 0.5, 1.0), (-1.0, 2.0, 0.1), (math.inf, math.nan, -math.inf)):
                for scale in (0.0, 1.0):
                    namespace = {'origin_x': origin[0], 'origin_y': origin[1], 'origin_z': origin[2],
                        'direction_x': -0.0, 'direction_y': -1.0, 'direction_z': 1.0,
                        'grid_basis': np.asarray([0.0, 0.0, 0.0, scale, scale, scale]), 'math': math}
                    with np.errstate(invalid='ignore'):
                        exec(program, namespace)
                    self.assertGreaterEqual(namespace['bucket'], 0)
                    self.assertLess(namespace['bucket'], cells ** 3 * 8)
            with self.assertRaises(ValueError):
                spatial_factory(cells + 1)

    def test_prefix_adapter_always_supplies_complete_shared_scan_block(self):
        class FakeKernel:
            def __getitem__(self, dimensions):
                return dimensions

        self.assertEqual(PrefixKernel(FakeKernel())[1, 32], (1, 256))


class SpatialGpuTests(coherent_tests.CoherentGpuTests):
    cells = 4
    bucket_count = 512

    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')
        cls.factory, cls.fingerprint = spatial_factory(cls.cells)
        cls.candidate_state = {}
        cls.original_state = {}

    def test_primary_histogram_and_active_counts_match_exact_source_directions(self):
        trace_input = WORKLOADS['face_direct'](8193)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        original = resident._launch_resident_kernel
        checked = []

        def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
            directions = batch.directions.copy()
            origins = batch.origins.copy()
            lower = context.scene.node_bounds_min[0]
            spans = context.scene.node_bounds_max[0] - lower
            scales = np.divide(self.cells, spans, out=np.zeros(3), where=np.isfinite(spans) & (spans > 0.0))
            cells = np.clip((origins - lower) * scales, 0, self.cells - 1).astype(np.int64)
            octants = (directions[:, 0] < 0).astype(np.int64) + 2 * (directions[:, 1] < 0) + 4 * (directions[:, 2] < 0)
            buckets = octants + 8 * (cells[:, 0] + self.cells * (cells[:, 1] + self.cells * cells[:, 2]))
            expected = np.bincount(buckets, minlength=self.bucket_count)
            result = original(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords)
            histogram = kernel.last_bucket_counts[:(depths + 1) * self.bucket_count].copy_to_host().reshape(-1, self.bucket_count)
            np.testing.assert_array_equal(histogram[0], expected)
            active = kernel.last_active_counts[:depths + 1].copy_to_host()
            np.testing.assert_array_equal(histogram.sum(axis=1), active)
            np.testing.assert_array_equal(batch.directions, directions)
            np.testing.assert_array_equal(batch.origins, origins)
            checked.append(count)
            return result

        with self.candidate(), patch.object(resident, '_launch_resident_kernel', new=launch):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assertEqual(checked, [8193])
        self.assertGreater(actual.receiver_hit_count, 0)


class Spatial8GpuTests(SpatialGpuTests):
    cells = 8
    bucket_count = 4096


if __name__ == '__main__':
    unittest.main()
