from __future__ import annotations

from dataclasses import replace
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
from leakage_simulator import gpu_cuda_emitted_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator.raytracer import run_direct_ray_trace
from test_perf4e_bounce_mis import _build_reflected_case
from test_perf5_receiver_hit_probe import no_hit_case


class EmittedFluxLayoutTests(unittest.TestCase):
    def test_metadata_is_bounded_by_output_groups_not_the_full_tape(self):
        for count, records, depths in ((0, 1, 1), (65536, 11, 11), (2048, 1001, 1001), (65536, 32, 1001)):
            groups, width, tiles = flux.workspace_layout(count, records, depths)
            self.assertEqual(groups, 3 + depths)
            self.assertEqual(groups, flux.workspace_layout(count * 16, records, depths)[0])
            self.assertEqual(tiles, (count * records + width - 1) // width)
        for dimensions in ((-1, 1, 1), (1, 0, 1), (1, 3, 2)):
            with self.assertRaises(ValueError):
                flux.workspace_layout(*dimensions)


class EmittedFluxGpuTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.dict(os.environ, {'LEAKAGE_GPU_EMITTED_FLUX': 'ordered'})
        self.mode.start()
        self.addCleanup(self.mode.stop)

    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_independent_primary_major_oracle_lobes_depths_and_nonzero_state(self):
        for records, depths, zero_initial in ((5, 5, False), (3, 1001, True)):
            with self.subTest(records=records, depths=depths, zero_initial=zero_initial):
                self.assert_packets(records, depths, zero_initial)

    def assert_packets(self, records, depths, zero_initial, packet_counts=None):
        cuda = gpu._CUDA
        rng = np.random.default_rng(4871)
        shapes = {'reflection_lobe_flux_lumen': (3, 2), 'reflection_depth_flux_lumen': (depths, 2),
            'contribution_lobe_flux_lumen': (3, 5), 'contribution_depth_flux_lumen': (depths, 7)}
        expected = {name: np.zeros(shape) if zero_initial else rng.random(shape) * 0.01 for name, shape in shapes.items()}
        state = {name: cuda.to_device(values) for name, values in expected.items()}
        lobe_counts = np.zeros((3, 5), dtype=np.int64)
        depth_counts = np.zeros((depths, 5), dtype=np.int64)
        workspace = None
        for count, empty in packet_counts or ((193, False), (19, True), (317, False), (19, False)):
            kinds = np.full(count, reducer.TERMINAL_ESCAPED, dtype=np.int8)
            terminal_depths = rng.integers(0, records, count, dtype=np.int16)
            status = np.zeros((count, records), dtype=np.uint16, order='F')
            lobes = rng.integers(0, 3, (count, records), dtype=np.int8)
            emitted = np.full((count, records), np.nan, order='F')
            if empty:
                terminal_depths.fill(0)
            surfaces = 0
            for primary in range(count):
                visited = int(terminal_depths[primary])
                if primary % 5 == 0 and not empty:
                    kinds[primary] = reducer.TERMINAL_BLOCKED
                    surfaces += 1
                surfaces += visited
                for depth in range(visited):
                    status[primary, depth] = reducer.STATUS_EMITTED
                    lobe = int(lobes[primary, depth])
                    power = 0.125 if primary < 128 else float(rng.random() * np.exp2(rng.integers(-40, -3)))
                    emitted[primary, depth] = power
                    expected['reflection_lobe_flux_lumen'][lobe, 0] += power
                    expected['contribution_lobe_flux_lumen'][lobe, reducer.OUTCOME_EMITTED] += power
                    expected['reflection_depth_flux_lumen'][depth + 1, 0] += power
                    expected['contribution_depth_flux_lumen'][depth + 1, reducer.CONTRIBUTION_DEPTH_EMITTED] += power
                    lobe_counts[lobe, reducer.OUTCOME_EMITTED] += 1
                    depth_counts[depth + 1, reducer.REF_DEPTH_EMITTED] += 1
            state['reflection_lobe_counts'] = cuda.to_device(lobe_counts)
            state['reflection_depth_counts'] = cuda.to_device(depth_counts)
            events = SimpleNamespace(ray_count=count, terminal_kind=cuda.to_device(kinds),
                terminal_depth=cuda.to_device(terminal_depths), event_status=cuda.to_device(status),
                event_lobes=cuda.to_device(lobes), event_emitted_power=cuda.to_device(emitted))
            workspace = flux.ensure_workspace(cuda, workspace, count, records, depths)
            self.assertEqual(workspace.nbytes, (3 + depths) * 24)
            flux.accumulate_device_ordered_emissions(cuda, workspace, events, state,
                cuda.to_device(np.asarray([0, surfaces, count, 0, 0], dtype=np.int64)))
            cuda.synchronize()
            for name, values in expected.items():
                np.testing.assert_array_equal(state[name].copy_to_host().view(np.uint64), values.view(np.uint64), err_msg=name)

    @patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_GRIDS': 'ordered'})
    def test_existing_failing_emission_fields_follow_cpu_order(self):
        for rays in (8192, 65536):
            expected = run_direct_ray_trace(_build_reflected_case(rays, 7172, 'receiver_mis'))
            for repeat in range(4):
                actual = run_direct_ray_trace(_build_reflected_case(rays, 7172, 'receiver_mis', backend='gpu_cuda'))
                parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                    absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
                self.assertTrue(parity.passed, (rays, repeat, parity.to_dict()))
                performance = actual.metrics['_performance_summary']
                self.assertEqual(performance['gpu_summary_emitted_flux_contract'], flux.CONTRACT)
                self.assertEqual(performance['gpu_summary_receiver_grid_contract'], 'device_primary_ordered_receiver_grid_v1')
                self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_block_modes_match_scalar_order_across_tiles_and_exponents(self):
        cuda = gpu._CUDA
        rng = np.random.default_rng(19731)
        count = 8197
        records = 3
        for mode, exponent, expected_mode in (('quantized', 0, 1), ('quantized', -1000, 1),
                ('quantized', 1000, 1), ('ties', 0, 2), ('uniform', 0, 0)):
            with self.subTest(mode=mode, exponent=exponent):
                scale = np.ldexp(1.0, exponent)
                shapes = {'reflection_lobe_flux_lumen': (3, 2), 'reflection_depth_flux_lumen': (3, 2),
                    'contribution_lobe_flux_lumen': (3, 5), 'contribution_depth_flux_lumen': (3, 7)}
                expected = {name: np.full(shape, scale) if mode == 'ties' else scale * rng.uniform(1.0, 1.5, shape)
                    for name, shape in shapes.items()}
                state = {name: cuda.to_device(values) for name, values in expected.items()}
                status = np.zeros((count, records), dtype=np.uint16, order='F')
                status[:, :2] = reducer.STATUS_EMITTED
                lobes = (np.arange(count * records).reshape(count, records) + np.arange(count)[:, None]) % 3
                lobes = lobes.astype(np.int8)
                emitted = np.full((count, records), np.nan, order='F')
                if mode == 'quantized':
                    emitted[:, :2] = np.ldexp(rng.uniform(0.5, 1.0, (count, 2)), exponent - 25)
                elif mode == 'ties':
                    emitted[:, :2] = np.spacing(scale) * (rng.integers(0, 8, (count, 2)) + 0.5)
                else:
                    emitted[:, :2] = scale / 64
                lobe_counts = np.zeros((3, 5), dtype=np.int64)
                depth_counts = np.zeros((3, 5), dtype=np.int64)
                for primary in range(count):
                    for depth in range(2):
                        lobe = int(lobes[primary, depth])
                        power = float(emitted[primary, depth])
                        expected['reflection_lobe_flux_lumen'][lobe, 0] += power
                        expected['contribution_lobe_flux_lumen'][lobe, reducer.OUTCOME_EMITTED] += power
                        expected['reflection_depth_flux_lumen'][depth + 1, 0] += power
                        expected['contribution_depth_flux_lumen'][depth + 1, reducer.CONTRIBUTION_DEPTH_EMITTED] += power
                        lobe_counts[lobe, reducer.OUTCOME_EMITTED] += 1
                        depth_counts[depth + 1, reducer.REF_DEPTH_EMITTED] += 1
                state['reflection_lobe_counts'] = cuda.to_device(lobe_counts)
                state['reflection_depth_counts'] = cuda.to_device(depth_counts)
                events = SimpleNamespace(ray_count=count, terminal_kind=cuda.to_device(np.full(count, reducer.TERMINAL_ESCAPED, dtype=np.int8)),
                    terminal_depth=cuda.to_device(np.full(count, 2, dtype=np.int16)), event_status=cuda.to_device(status),
                    event_lobes=cuda.to_device(lobes), event_emitted_power=cuda.to_device(emitted))
                workspace = flux.ensure_workspace(cuda, None, count, records, records)
                flux.accumulate_device_ordered_emissions(cuda, workspace, events, state,
                    cuda.to_device(np.asarray([0, count * 2, count, 0, 0], dtype=np.int64)))
                cuda.synchronize()
                for name, values in expected.items():
                    np.testing.assert_array_equal(state[name].copy_to_host().view(np.uint64), values.view(np.uint64), err_msg=name)
                self.assert_recorded_mode(workspace, mode, expected_mode)

    def assert_recorded_mode(self, workspace, mode, expected_mode):
        self.assertGreater(workspace.modes.copy_to_host()[:, expected_mode].sum(), 0)

    def test_nan_emission_is_rejected_instead_of_hidden_as_a_constant_tile(self):
        original = resident.accumulate_resident_summary_gpu_cuda

        def corrupt(events, request, session):
            values = events.event_emitted_power.copy_to_host()
            values[0, 0] = np.nan
            events.event_emitted_power.copy_to_device(values)
            return original(events, request, session)

        with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=corrupt):
            actual = run_direct_ray_trace(_build_reflected_case(1024, 7172, 'receiver_mis', backend='gpu_cuda'))
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], 'gpu_summary_accumulator_result_invalid')

    def test_no_emissions_skip_workspace_and_stage(self):
        with patch.object(flux, 'ensure_workspace') as allocate, patch.object(flux, 'accumulate_device_ordered_emissions') as accumulate:
            actual = run_direct_ray_trace(no_hit_case())
        allocate.assert_not_called()
        accumulate.assert_not_called()
        self.assertEqual(actual.metrics['_performance_summary']['gpu_summary_emitted_flux_workspace_bytes'], 0)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)

    def test_failed_stage_or_bad_hint_replays_without_double_commit(self):
        expected = run_direct_ray_trace(_build_reflected_case(1024, 7172, 'receiver_mis'))
        original = resident.accumulate_resident_summary_gpu_cuda
        for kind, reason in (('kernel', 'gpu_summary_ordered_emitted_flux_failed'), ('hint', 'gpu_summary_emission_hint_mismatch')):
            with self.subTest(kind=kind):
                def corrupt(events, request, session):
                    return original(replace(events, emissions_present=False), request, session)

                target = patch.object(flux, 'accumulate_device_ordered_emissions', side_effect=RuntimeError('injected emission failure')) if kind == 'kernel' else patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=corrupt)
                with target:
                    actual = run_direct_ray_trace(_build_reflected_case(1024, 7172, 'receiver_mis', backend='gpu_cuda'))
                parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                    absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                self.assertTrue(parity.passed, parity.to_dict())
                performance = actual.metrics['_performance_summary']
                self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
                self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], reason)

    def test_atomic_diagnostic_is_reported_as_atomic(self):
        with patch.dict(os.environ, {'LEAKAGE_GPU_EMITTED_FLUX': 'atomic'}):
            actual = run_direct_ray_trace(_build_reflected_case(1024, 7172, 'receiver_mis', backend='gpu_cuda'))
        self.assertEqual(actual.metrics['_performance_summary']['gpu_summary_emitted_flux_contract'], 'global_fp64_atomic_emitted_flux_v1')


if __name__ == '__main__':
    unittest.main()
