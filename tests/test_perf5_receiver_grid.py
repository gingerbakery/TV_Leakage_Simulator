from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_receiver_hit_probe import no_hit_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_receiver_grid as grid
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.wavefront_event_tape import TERMINAL_RECEIVER, TERMINAL_ESCAPED


class ReceiverGridLayoutTests(unittest.TestCase):
    def test_adaptive_tile_counts_keep_the_workspace_bounded(self):
        for count in (0, 19, 65536, 1048576):
            for cells in (0, 16, 1920, 262144, 1048576):
                width, tiles = grid.workspace_layout(count, cells)
                self.assertEqual(tiles, (count + width - 1) // width)
                self.assertLessEqual(cells * (32 + 4 * tiles), grid.WORKSPACE_LIMIT_BYTES)
        for count, cells in ((-1, 1), (1, -1), (1 << 31, 1), (1, grid.WORKSPACE_LIMIT_BYTES // 36 + 1)):
            with self.assertRaises(ValueError):
                grid.workspace_layout(count, cells)


class ReceiverGridGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def setUp(self):
        self.environment = patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_GRIDS': 'ordered', 'LEAKAGE_GPU_EMITTED_FLUX': 'ordered'})
        self.environment.start()
        summary.reset_gpu_summary_accumulator_for_tests()

    def tearDown(self):
        self.environment.stop()
        summary.reset_gpu_summary_accumulator_for_tests()

    def test_ordered_cells_preserve_nonzero_prefixes_tails_and_empty_chunks(self):
        cuda = gpu._CUDA
        rng = np.random.default_rng(472915)
        columns = np.asarray([3, 2], dtype=np.int32)
        offsets = np.asarray([0, 6, 10], dtype=np.int64)
        expected_flux = rng.random(10) * 0.01
        expected_squared = rng.random(10) * 0.0001
        state = {'grid_flux_lumen': cuda.to_device(expected_flux), 'grid_flux_squared_lumen2': cuda.to_device(expected_squared)}
        workspace = None
        for count, empty in ((193, False), (19, True), (317, False), (1025, False), (1025, False), (0, True)):
            receivers = rng.integers(0, 2, count, dtype=np.int32)
            kinds = np.where(rng.random(count) < 0.7, TERMINAL_RECEIVER, TERMINAL_ESCAPED).astype(np.int8)
            if empty:
                kinds.fill(TERMINAL_ESCAPED)
            rows = rng.integers(0, 2, count, dtype=np.int32)
            coordinate_columns = np.asarray([rng.integers(0, columns[receiver]) for receiver in receivers], dtype=np.int32)
            powers = rng.random(count) * np.exp2(rng.integers(-50, -2, count))
            powers[kinds != TERMINAL_RECEIVER] = np.nan
            for primary in range(count):
                if kinds[primary] == TERMINAL_RECEIVER:
                    cell = offsets[receivers[primary]] + rows[primary] * columns[receivers[primary]] + coordinate_columns[primary]
                    power = float(powers[primary])
                    expected_flux[cell] += power
                    expected_squared[cell] += power * power
            events = SimpleNamespace(ray_count=count, terminal_kind=cuda.to_device(kinds),
                terminal_receiver=cuda.to_device(receivers), terminal_row=cuda.to_device(rows),
                terminal_column=cuda.to_device(coordinate_columns), terminal_received_power=cuda.to_device(powers))
            session = SimpleNamespace(device_state=state, device_receiver_columns=cuda.to_device(columns),
                device_grid_offsets=cuda.to_device(offsets), chunk_counts=cuda.to_device(np.zeros(5, dtype=np.int64)))
            previous = workspace
            workspace = grid.ensure_workspace(cuda, workspace, count, 10)
            if previous is not None and previous.count == count:
                self.assertIs(workspace, previous)
            self.assertLessEqual(workspace.nbytes, grid.WORKSPACE_LIMIT_BYTES)
            grid.snapshot_device_grid(cuda, workspace, state)
            grid.accumulate_device_ordered_grid(cuda, workspace, events, session)
            np.testing.assert_array_equal(state['grid_flux_lumen'].copy_to_host().view(np.uint64), expected_flux.view(np.uint64))
            np.testing.assert_array_equal(state['grid_flux_squared_lumen2'].copy_to_host().view(np.uint64), expected_squared.view(np.uint64))

    def test_existing_peak_sample_failure_and_cells_match_cpu(self):
        expected = run_direct_ray_trace(_build_reflected_case(65536, 7172, 'receiver_mis'))
        for repeat in range(4):
            actual = run_direct_ray_trace(_build_reflected_case(65536, 7172, 'receiver_mis', backend='gpu_cuda'))
            parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
            self.assertTrue(parity.passed, (repeat, parity.to_dict()))
            for field in ('flux_lumen', 'flux_squared_lumen2_grid'):
                np.testing.assert_array_equal(np.asarray(expected.to_dict()['receiver_grids'][0][field]).view(np.uint64),
                    np.asarray(actual.to_dict()['receiver_grids'][0][field]).view(np.uint64))
            performance = actual.metrics['_performance_summary']
            self.assertEqual(performance['gpu_summary_receiver_grid_contract'], grid.CONTRACT)
            self.assertGreater(performance['gpu_summary_receiver_grid_workspace_bytes'], 0)
            self.assertEqual(performance['compute_execution_state'], 'gpu_active')
            self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_identical_cell_powers_preserve_rounding_boundaries_and_signed_zero(self):
        cuda = gpu._CUDA
        count = 65539
        for power in (0.1, 0.000003, float.fromhex('0x1p-1074'), -0.0):
            with self.subTest(power=power):
                columns = np.arange(count, dtype=np.int32) % 4
                powers = np.full(count, power, dtype=np.float64)
                if power > 0.0:
                    powers[1::8] = np.nextafter(power, np.inf)
                else:
                    powers[1::8] = 0.0
                expected_flux = np.asarray([power * 1023, power * 1024, 0.0, 0.0000001])
                expected_squared = np.asarray([0.0, 0.000001, power, 0.001])
                state = {'grid_flux_lumen': cuda.to_device(expected_flux),
                    'grid_flux_squared_lumen2': cuda.to_device(expected_squared)}
                for primary in range(count):
                    cell = columns[primary]
                    value = float(powers[primary])
                    expected_flux[cell] += value
                    expected_squared[cell] += value * value
                events = SimpleNamespace(terminal_kind=cuda.to_device(np.full(count, TERMINAL_RECEIVER, dtype=np.int8)),
                    terminal_receiver=cuda.to_device(np.zeros(count, dtype=np.int32)),
                    terminal_row=cuda.to_device(np.zeros(count, dtype=np.int32)),
                    terminal_column=cuda.to_device(columns), terminal_received_power=cuda.to_device(powers))
                session = SimpleNamespace(device_state=state,
                    device_receiver_columns=cuda.to_device(np.asarray([4], dtype=np.int32)),
                    device_grid_offsets=cuda.to_device(np.asarray([0, 4], dtype=np.int64)),
                    chunk_counts=cuda.to_device(np.zeros(5, dtype=np.int64)))
                workspace = grid.ensure_workspace(cuda, None, count, 4)
                grid.snapshot_device_grid(cuda, workspace, state)
                grid.accumulate_device_ordered_grid(cuda, workspace, events, session)
                np.testing.assert_array_equal(state['grid_flux_lumen'].copy_to_host().view(np.uint64), expected_flux.view(np.uint64))
                np.testing.assert_array_equal(state['grid_flux_squared_lumen2'].copy_to_host().view(np.uint64), expected_squared.view(np.uint64))

    def test_grid_failure_replays_without_double_commit(self):
        expected = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis'))
        with patch.object(grid, 'accumulate_device_ordered_grid', side_effect=RuntimeError('injected grid failure')):
            actual = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'))
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], 'gpu_summary_ordered_receiver_grid_failed')

    def test_empty_receiver_batch_does_not_allocate_grid_workspace(self):
        actual = run_direct_ray_trace(no_hit_case())
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertEqual(performance['gpu_summary_receiver_grid_contract'], grid.CONTRACT)
        self.assertEqual(performance['gpu_summary_receiver_grid_workspace_bytes'], 0)

    def test_grid_modes_can_switch_without_reusing_the_wrong_kernel(self):
        expected = run_direct_ray_trace(_build_reflected_case(8192, 7170, 'receiver_mis'))
        for mode in ('ordered', 'atomic', 'ordered', 'atomic'):
            with self.subTest(mode=mode), patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_GRIDS': mode}):
                actual = run_direct_ray_trace(_build_reflected_case(8192, 7170, 'receiver_mis', backend='gpu_cuda'))
                parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                    absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                self.assertTrue(parity.passed, parity.to_dict())
                self.assertEqual(summary._GRID_KERNEL_MODE, mode)
                self.assertEqual(actual.metrics['_performance_summary']['compute_execution_state'], 'gpu_active')


if __name__ == '__main__':
    unittest.main()
