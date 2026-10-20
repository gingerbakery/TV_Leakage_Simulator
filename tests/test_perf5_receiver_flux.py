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

from leakage_simulator import gpu_cuda_receiver_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator.raytracer import run_direct_ray_trace
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case, _semantic_payload


class ReceiverFluxLayoutTests(unittest.TestCase):
    def test_workspace_metadata_scales_with_outputs_not_primary_depth_product(self):
        self.assertEqual(reducer.LOBE_COUNT, 4)
        self.assertEqual(reducer.RECEIVER_FIELD_SIZE, 7)
        for count in (0, 19, 8192, 65536):
            for receivers, depths in ((1, 3), (2, 1001), (1024, 1001)):
                groups, width, tiles = flux.workspace_layout(count, receivers, depths)
                self.assertEqual(groups, receivers * (reducer.RECEIVER_FIELD_SIZE + depths) + 2 + reducer.LOBE_COUNT + depths)
                self.assertEqual(tiles, (count + width - 1) // width)
                self.assertLessEqual(groups * tiles, max(131072, groups))
        for dimensions in ((-1, 1, 1), (1, -1, 1), (1, 1, 0)):
            with self.assertRaises(ValueError):
                flux.workspace_layout(*dimensions)


class ReceiverFluxGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def test_independent_multi_receiver_depth_lobe_sums_and_nonzero_state(self):
        for receivers, zero_initial in ((2, False), (1, False), (1, True)):
            with self.subTest(receivers=receivers, zero_initial=zero_initial):
                self.assert_packet_sums(receivers, zero_initial)

    def test_bsdf_only_global_and_receiver_sums_preserve_alias_contract(self):
        for receivers, zero_initial, alias_initial in ((2, True, False), (2, False, False),
                (1, True, False), (1, False, False), (1, False, True)):
            with self.subTest(receivers=receivers, zero_initial=zero_initial, alias_initial=alias_initial):
                self.assert_packet_sums(receivers, zero_initial, bsdf_only=True, alias_initial=alias_initial)

    def assert_packet_sums(self, receiver_count, zero_initial, packet_counts=None, *, bsdf_only=False, alias_initial=False):
        cuda = gpu._CUDA
        rng = np.random.default_rng(4719)
        shapes = {
            'receiver_flux_lumen': (receiver_count, reducer.RECEIVER_FIELD_SIZE), 'receiver_depth_flux_lumen': (receiver_count, 5),
            'reflection_flux_lumen': (2,), 'contribution_receiver_flux_lumen': (2,),
            'reflection_lobe_flux_lumen': (reducer.LOBE_COUNT, 2), 'contribution_lobe_flux_lumen': (reducer.LOBE_COUNT, 5),
            'reflection_depth_flux_lumen': (5, 2), 'contribution_depth_flux_lumen': (5, 7),
            'grid_flux_squared_totals_lumen2': (receiver_count,),
        }
        expected = {name: np.zeros(shape) if zero_initial else rng.random(shape) * 0.01 for name, shape in shapes.items()}
        if alias_initial:
            for values in expected.values():
                values.fill(0.125)
        state = {name: cuda.to_device(values) for name, values in expected.items()}
        counters = {name: np.zeros(shape, dtype=np.int64) for name, shape in {
            'receiver_counts': (receiver_count, reducer.RECEIVER_FIELD_SIZE), 'receiver_depth_counts': (receiver_count, 5),
            'contribution_receiver_counts': (2,), 'reflection_lobe_counts': (reducer.LOBE_COUNT, 5),
            'contribution_depth_counts': (5, 7),
        }.items()}
        workspace = None
        for count, empty in packet_counts or ((193, False), (19, True), (317, False), (19, False)):
            kinds = np.where(rng.random(count) < 0.8, reducer.TERMINAL_RECEIVER, reducer.TERMINAL_ESCAPED).astype(np.int8)
            if empty:
                kinds.fill(reducer.TERMINAL_ESCAPED)
            receivers = rng.integers(0, receiver_count, count, dtype=np.int32)
            receivers[kinds != reducer.TERMINAL_RECEIVER] = -1
            depths = rng.integers(0, 5, count, dtype=np.int16)
            lobes = rng.integers(0, reducer.LOBE_COUNT, (count, 5), dtype=np.int8)
            if bsdf_only:
                depths.fill(1)
                lobes.fill(reducer.LOBE_BSDF)
            powers = rng.random(count) * np.exp2(rng.integers(-40, -3, count))
            powers[:128] = 0.125
            powers[kinds != reducer.TERMINAL_RECEIVER] = np.nan
            for primary in range(count):
                if kinds[primary] != reducer.TERMINAL_RECEIVER:
                    continue
                receiver = int(receivers[primary])
                depth = int(depths[primary])
                power = float(powers[primary])
                expected['grid_flux_squared_totals_lumen2'][receiver] += power * power
                category = 0 if depth == 0 else 1
                fields = [category, 2]
                if depth:
                    lobe = int(lobes[primary, depth - 1])
                    fields.append(3 + lobe)
                    expected['reflection_lobe_flux_lumen'][lobe, 1] += power
                    expected['contribution_lobe_flux_lumen'][lobe, 1] += power
                    expected['reflection_depth_flux_lumen'][depth, 1] += power
                    counters['reflection_lobe_counts'][lobe, 1] += 1
                for field in fields:
                    expected['receiver_flux_lumen'][receiver, field] += power
                    counters['receiver_counts'][receiver, field] += 1
                expected['receiver_depth_flux_lumen'][receiver, depth] += power
                expected['reflection_flux_lumen'][category] += power
                expected['contribution_receiver_flux_lumen'][category] += power
                expected['contribution_depth_flux_lumen'][depth, 2] += power
                counters['receiver_depth_counts'][receiver, depth] += 1
                counters['contribution_receiver_counts'][category] += 1
                counters['contribution_depth_counts'][depth, 2] += 1
            events = SimpleNamespace(ray_count=count, terminal_kind=cuda.to_device(kinds),
                terminal_receiver=cuda.to_device(receivers), terminal_depth=cuda.to_device(depths),
                event_lobes=cuda.to_device(lobes), terminal_received_power=cuda.to_device(powers))
            state.update({name: cuda.to_device(values) for name, values in counters.items()})
            chunk_counts = cuda.to_device(np.asarray([np.count_nonzero(kinds == reducer.TERMINAL_RECEIVER)], dtype=np.int64))
            workspace = flux.ensure_workspace(cuda, workspace, count, receiver_count, 5)
            flux.accumulate_device_ordered_flux(cuda, workspace, events, state, chunk_counts)
            cuda.synchronize()
            for name, values in expected.items():
                np.testing.assert_array_equal(state[name].copy_to_host().view(np.uint64), values.view(np.uint64), err_msg=name)

    def test_previously_failing_receiver_fields_use_ordered_flux(self):
        for seed in (7169, 7173):
            for rays in (8192, 65536):
                cpu = run_direct_ray_trace(_build_reflected_case(rays, seed, 'receiver_mis'))
                for repeat in range(4):
                    actual = run_direct_ray_trace(_build_reflected_case(rays, seed, 'receiver_mis', backend='gpu_cuda'))
                    parity = compare_semantic_payloads(cpu.contribution_summary.to_dict()['receivers'], actual.contribution_summary.to_dict()['receivers'],
                        absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
                    self.assertTrue(parity.passed, (seed, rays, repeat, parity.to_dict()))
                    performance = actual.metrics['_performance_summary']
                    self.assertEqual(performance['gpu_summary_receiver_flux_contract'], flux.CONTRACT)
                    self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                    self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
                    self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_equivalent_packet_streams_share_work_only_after_equal_initial_bits(self):
        original = flux.accumulate_device_ordered_flux
        aliases = []

        def inspect(cuda, workspace, events, state, chunk_counts):
            original(cuda, workspace, events, state, chunk_counts)
            cuda.synchronize()
            aliases.append(np.count_nonzero(workspace.varying.copy_to_host()[:, 0] == 2))

        cpu = run_direct_ray_trace(_build_reflected_case(8192, 7169, 'receiver_mis'))
        with patch.object(flux, 'accumulate_device_ordered_flux', new=inspect):
            actual = run_direct_ray_trace(_build_reflected_case(8192, 7169, 'receiver_mis', backend='gpu_cuda'))
        parity = compare_semantic_payloads(_semantic_payload(cpu), _semantic_payload(actual),
            absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
        self.assertTrue(parity.passed, parity.to_dict())
        self.assertTrue(aliases)
        self.assertGreaterEqual(max(aliases), 5)

    def test_failed_flux_kernel_replays_whole_batch_without_double_commit(self):
        cpu = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis'))
        with patch.object(flux, 'accumulate_device_ordered_flux', side_effect=RuntimeError('injected flux failure')):
            actual = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'))
        parity = compare_semantic_payloads(_semantic_payload(cpu), _semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 1)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_reason'], 'gpu_summary_ordered_receiver_flux_failed')

    def test_multi_chunk_device_state_stays_synchronized(self):
        original = resident.accumulate_resident_summary_gpu_cuda
        snapshots = []

        def inspect(events, request, session):
            execution = original(events, request, session)
            for name in ('receiver_flux_lumen', 'receiver_depth_flux_lumen', 'reflection_depth_flux_lumen'):
                values = getattr(execution.result.state, name)
                np.testing.assert_array_equal(session.device_state[name].copy_to_host().view(np.uint64), values.view(np.uint64))
                self.assertFalse(values.flags.writeable)
                snapshots.append((values, values.copy()))
            return execution

        with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=inspect):
            actual = run_direct_ray_trace(_build_reflected_case(131091, 7171, 'receiver_mis', backend='gpu_cuda'))
        self.assertEqual(len(snapshots), 9)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)
        for values, copy in snapshots:
            np.testing.assert_array_equal(values.view(np.uint64), copy.view(np.uint64))

    def test_atomic_diagnostic_reports_its_actual_flux_contract(self):
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_MOMENTS': 'atomic'}):
            actual = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'))
        self.assertEqual(actual.metrics['_performance_summary']['gpu_summary_receiver_flux_contract'], 'global_fp64_atomic_receiver_flux_v1')


if __name__ == '__main__':
    unittest.main()
