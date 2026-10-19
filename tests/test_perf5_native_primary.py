from __future__ import annotations

import os
from pathlib import Path
import sys
import subprocess
import unittest
from unittest.mock import Mock, patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, semantic_hash
from leakage_simulator import fast_sampling, native_cpu_sampling, raytracer
from leakage_simulator.native_cpu_intersection import probe_native_cpu
from leakage_simulator.types import EmitterAimSpec, EmitterSpec
from test_emitter_aim import aim_scene


class NativePrimaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not probe_native_cpu().available:
            raise unittest.SkipTest('Numba unavailable')

    def assertBits(self, expected, actual):
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))

    def test_row_directions_keep_every_bit_and_random_state(self):
        normals = np.random.default_rng(39).normal(size=(10000, 3))
        normals = fast_sampling._normalize_rows(normals)
        normals[:8] = [[0, 0, 1], [0, 0, -1], [1, 0, 0], [-1, 0, 0],
                       [0, 1, 0], [0, -1, 0], [0, -0.0, 1], [-0.0, 0, -1]]
        for distribution in ('lambertian', 'gaussian', 'isotropic'):
            emitter = EmitterSpec('probe', face_indices=[0], direction_distribution=distribution)
            results = []
            for mode in ('off', 'auto'):
                generator = np.random.default_rng(42)
                with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': mode}):
                    results.append((fast_sampling._sample_direction_rows(generator, emitter, normals), generator.random(7)))
            with self.subTest(distribution=distribution):
                self.assertBits(results[0][0], results[1][0])
                self.assertBits(results[0][1], results[1][1])

    def test_plane_directions_keep_every_bit_and_random_state(self):
        normal = fast_sampling._normalize(np.asarray([0.137, -0.7, 0.952]))
        basis_u, basis_v = fast_sampling._orthonormal_basis(normal)
        for distribution in ('lambertian', 'gaussian', 'isotropic'):
            emitter = EmitterSpec('probe', face_indices=[0], direction_distribution=distribution)
            results = []
            for mode in ('off', 'auto'):
                generator = np.random.default_rng(43)
                with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': mode}):
                    results.append((fast_sampling._sample_direction_batch(generator, emitter, normal, basis_u, basis_v, 10000), generator.random(7)))
            with self.subTest(distribution=distribution):
                self.assertBits(results[0][0], results[1][0])
                self.assertBits(results[0][1], results[1][1])

    def test_barycentric_and_plane_coordinates_are_bit_exact(self):
        generator = np.random.default_rng(44)
        triangles = generator.normal(size=(17, 3, 3)) * 500
        slots = generator.integers(0, 17, 10000, dtype=np.int64)
        root = np.sqrt(generator.random(10000))
        second = generator.random(10000)
        weights = (1.0 - root, root * (1.0 - second), root * second)
        selected = triangles[slots]
        expected = weights[0][:, None] * selected[:, 0] + weights[1][:, None] * selected[:, 1] + weights[2][:, None] * selected[:, 2]
        center, u_axis, v_axis, normal = generator.normal(size=(4, 3))
        u_offsets, v_offsets = generator.normal(size=(2, 10000))
        plane = center[None, :] + u_offsets[:, None] * u_axis + v_offsets[:, None] * v_axis
        plane = plane + 1e-4 * normal[None, :]
        with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': 'auto'}):
            self.assertBits(expected, native_cpu_sampling.face_points(triangles, slots, *weights))
            self.assertBits(plane, native_cpu_sampling.plane_origins(center, u_axis, v_axis, normal, u_offsets, v_offsets, 1e-4))

    def test_cpu_public_results_remain_exact(self):
        for name, builder in WORKLOADS.items():
            results = []
            for mode in ('off', 'auto'):
                with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': mode}):
                    results.append(raytracer.run_direct_ray_trace(builder(10000)))
            with self.subTest(scene=name):
                self.assertEqual(semantic_hash(results[0]), semantic_hash(results[1]))
                self.assertGreater(results[1].metrics['_performance_summary']['native_primary_preparation_success_batches'], 0)
                self.assertEqual(results[1].metrics['_performance_summary']['native_primary_preparation_fallback_batches'], 0)

    def test_failed_preparation_keeps_the_random_stream_and_replays_numpy(self):
        normals = np.repeat(np.asarray([[0.137, -0.7, 0.952]]), 10000, axis=0)
        normals = fast_sampling._normalize_rows(normals)
        emitter = EmitterSpec('probe', face_indices=[0], direction_distribution='gaussian')
        expected_generator = np.random.default_rng(45)
        with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': 'off'}):
            expected = fast_sampling._sample_direction_rows(expected_generator, emitter, normals)
        failing = Mock(side_effect=RuntimeError('forced preparation failure'))
        failing.signatures = []
        generator = np.random.default_rng(45)
        with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': 'auto'}), patch.object(native_cpu_sampling, '_DISABLED', False), patch.object(native_cpu_sampling, '_KERNELS', (failing, failing, failing)):
            actual = fast_sampling._sample_direction_rows(generator, emitter, normals)
            self.assertTrue(native_cpu_sampling._DISABLED)
        self.assertBits(expected, actual)
        self.assertBits(expected_generator.random(7), generator.random(7))

    def test_aim_area_sphere_and_two_sided_emitters_remain_exact(self):
        for emitter_type in ('face', 'datum_plane', 'reference_plane'):
            for mode in ('area', 'sphere', 'both'):
                scene = aim_scene(emitter_type=emitter_type, rays=10000)
                if mode == 'sphere':
                    scene.emitters[0].aim = EmitterAimSpec(enabled=True, mode='sphere', distribution='uniform_solid_angle')
                elif mode == 'both':
                    scene.emitters[0].aim = None
                    scene.emitters[0].emission_direction = 'both'
                results = []
                for preparation in ('off', 'auto'):
                    with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': preparation}):
                        results.append(raytracer.run_direct_ray_trace(scene))
                with self.subTest(emitter_type=emitter_type, mode=mode):
                    self.assertEqual(semantic_hash(results[0]), semantic_hash(results[1]))
                    self.assertEqual(results[1].metrics['_performance_summary']['native_primary_preparation_fallback_batches'], 0)

    def test_native_preparation_never_requires_cuda_in_a_fresh_cpu_process(self):
        code = '''
import sys, os
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'src'), str(root / 'scripts')]
class BlockCuda:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'numba.cuda' or fullname.startswith('numba.cuda.'):
            raise AssertionError('CPU preparation attempted CUDA import')
sys.meta_path.insert(0, BlockCuda())
os.environ['LEAKAGE_NATIVE_SAMPLING'] = 'auto'
from benchmark_perf5_cpu import WORKLOADS
from leakage_simulator.raytracer import run_direct_ray_trace
result = run_direct_ray_trace(WORKLOADS['face_direct'](10000))
performance = result.metrics['_performance_summary']
assert performance['native_primary_preparation_success_batches'] > 0
assert performance['native_primary_preparation_fallback_batches'] == 0
assert performance['compute_execution_state'] == 'cpu'
assert 'numba.cuda' not in sys.modules
'''
        process = subprocess.run([sys.executable, '-c', code, str(ROOT)], capture_output=True, text=True, timeout=120)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == '__main__':
    unittest.main()
