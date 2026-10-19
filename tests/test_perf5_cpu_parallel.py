from __future__ import annotations

from dataclasses import fields
from importlib import util
import os
from pathlib import Path
import sys
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))

from test_native_cpu_backend_perf3b2 import build_box_array, build_random_rays, semantic_payload
from test_perf3c_counter_wavefront_planner import _counter_batch
from test_perf3b2a_multibounce_wavefront import stochastic_two_bounce_input
from leakage_simulator.cpu_parallel import cpu_worker_limit, run_cpu_ranges
from leakage_simulator.geometry import RayBatch
from leakage_simulator.native_cpu_counter_wavefront import plan_counter_native_cpu
from leakage_simulator.native_cpu_intersection import probe_native_cpu
from leakage_simulator.raytracer import run_direct_ray_trace


class CpuParallelTests(unittest.TestCase):
    def test_ranges_cover_every_row_once_and_propagate_errors_after_join(self):
        output = np.zeros(10003, dtype=np.int64)
        completed = threading.Event()

        def write(start, end):
            output[start:end] += 1

        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '4'}):
            self.assertEqual(run_cpu_ranges(len(output), write, minimum_rows=512), 4)
        np.testing.assert_array_equal(output, np.ones_like(output))

        def failing(start, end):
            if start == 0:
                raise RuntimeError('parallel failure')
            time.sleep(0.02)
            completed.set()

        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '2'}):
            with self.assertRaisesRegex(RuntimeError, 'parallel failure'):
                run_cpu_ranges(1024, failing, minimum_rows=512)
        self.assertTrue(completed.is_set())

    def test_small_nested_and_disabled_ranges_remain_serial(self):
        nested = []

        def outer(start, end):
            nested.append(run_cpu_ranges(2048, lambda first, last: None, minimum_rows=512))

        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '4'}):
            self.assertEqual(run_cpu_ranges(7, lambda first, last: None, minimum_rows=512), 1)
            self.assertEqual(run_cpu_ranges(2048, outer, minimum_rows=512), 4)
            self.assertEqual(run_cpu_ranges(2048, lambda first, last: None, minimum_rows=512, enabled=False), 1)
        self.assertEqual(nested, [1, 1, 1, 1])

    def test_invalid_worker_setting_is_safe(self):
        for setting in ('nonsense', '-1', '0'):
            with self.subTest(setting=setting), patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': setting}):
                self.assertEqual(cpu_worker_limit(), 1)

    def test_parallel_intersections_are_exact_and_repeatable(self):
        if not probe_native_cpu().available:
            self.skipTest('Numba unavailable')
        mesh = build_box_array()
        origins, directions = build_random_rays(10003)
        rays = RayBatch(origins, directions, min_t=1e-8, max_t=80.0)
        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '1'}):
            reference, _ = mesh.intersect_rays_native_cpu(rays, backend='bvh')
        for workers in (2, 4, 8):
            with self.subTest(workers=workers), patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': str(workers)}):
                for repeat in range(3):
                    actual, execution = mesh.intersect_rays_native_cpu(rays, backend='bvh')
                    np.testing.assert_array_equal(actual.face_indices, reference.face_indices)
                    np.testing.assert_array_equal(actual.t, reference.t)
                    self.assertEqual(execution.parallel_workers, min(workers, os.cpu_count() or 1))

    def test_parallel_reflections_keep_rng_and_every_output_exact(self):
        if not probe_native_cpu().available:
            self.skipTest('Numba unavailable')
        batch = _counter_batch(17003)
        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '1'}):
            reference = plan_counter_native_cpu(batch).result
        with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': '4'}):
            for repeat in range(3):
                execution = plan_counter_native_cpu(batch)
                self.assertGreater(execution.parallel_workers, 1)
                for field in fields(reference):
                    np.testing.assert_array_equal(getattr(execution.result, field.name), getattr(reference, field.name))

    def test_end_to_end_keeps_heatmap_contributions_and_stored_paths(self):
        if not probe_native_cpu().available:
            self.skipTest('Numba unavailable')
        expected = None
        for workers in (1, 4):
            trace_input = stochastic_two_bounce_input(12000)
            trace_input.config.store_ray_paths = True
            trace_input.config.max_stored_paths = 31
            with patch.dict(os.environ, {'LEAKAGE_CPU_WORKERS': str(workers)}):
                actual = run_direct_ray_trace(trace_input)
            payload = semantic_payload(actual)
            if expected is None:
                expected = payload
            self.assertEqual(payload, expected)
            self.assertEqual(actual.metrics['_performance_summary']['intersection_fallback_count'], 0)
            self.assertEqual(actual.metrics['_performance_summary']['intersection_batch_size'], 32768)

    def test_cpu_requirements_are_pinned_and_do_not_need_cuda(self):
        spec = util.spec_from_file_location('perf5_source_verifier', ROOT / 'scripts' / 'verify_source_requirements.py')
        verifier = util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        pins = verifier.read_exact_pins([ROOT / 'requirements-dev.txt', ROOT / 'requirements-cpu-accelerated.txt'])
        self.assertEqual(pins['numba'][1], '0.66.0')
        self.assertEqual(pins['llvmlite'][1], '0.48.0')
        self.assertFalse(any('cuda' in name for name in pins))
        launcher = (ROOT / 'run_web.bat').read_text(encoding='utf-8')
        self.assertLess(launcher.index('requirements-cpu-accelerated.txt'), launcher.index('run_web.py'))
        self.assertIn('requirements-cpu-accelerated.txt', verifier.format_human({
            'python': '3.13.3', 'architecture_bits': 64, 'verified_pin_count': 2,
            'requirements': ['requirements-cpu-accelerated.txt'],
        }))

    def test_fresh_cpu_process_never_imports_cuda(self):
        if not probe_native_cpu().available:
            self.skipTest('Numba unavailable')
        code = '''
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'src'), str(root / 'scripts')]
class BlockCuda:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'numba.cuda' or fullname.startswith('numba.cuda.'):
            raise AssertionError('CPU attempted CUDA import')
sys.meta_path.insert(0, BlockCuda())
from benchmark_perf3b2a_multibounce import build_two_bounce_case
from leakage_simulator.raytracer import run_direct_ray_trace
result = run_direct_ray_trace(build_two_bounce_case(1000))
performance = result.metrics['_performance_summary']
assert performance['native_used']
assert performance['intersection_fallback_count'] == 0
assert performance['compute_execution_state'] == 'cpu'
assert 'numba.cuda' not in sys.modules
'''
        process = subprocess.run([sys.executable, '-c', code, str(ROOT)],
                                 capture_output=True, text=True, timeout=90)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == '__main__':
    unittest.main()
