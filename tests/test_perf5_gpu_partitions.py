from __future__ import annotations

from dataclasses import fields
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS
from benchmark_perf5_transport import semantic_payload
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator import gpu_cuda_summary_partitions as partitions
from leakage_simulator.raytracer import run_direct_ray_trace
from perf4_accuracy import compare_semantic_payloads


class GpuPartitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, key) is True for key in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('production CUDA contract mismatch')

    def assertParity(self, expected, actual):
        parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(parity.passed, parity.to_dict())

    def test_partitioned_workloads_match_cpu_and_global_accumulation(self):
        for name, builder in WORKLOADS.items():
            with self.subTest(scene=name):
                scene = builder(10000)
                scene.config.store_ray_paths = True
                scene.config.max_stored_paths = 29
                cpu = run_direct_ray_trace(scene)
                scene.config.compute_backend = 'gpu_cuda'
                with patch.dict(os.environ, {'LEAKAGE_GPU_SUMMARY_PARTITIONS': '1'}):
                    global_result = run_direct_ray_trace(scene)
                with patch.dict(os.environ, {'LEAKAGE_GPU_SUMMARY_PARTITIONS': '128'}):
                    actual = run_direct_ray_trace(scene)
                self.assertParity(cpu, actual)
                self.assertParity(global_result, actual)
                performance = actual.metrics['_performance_summary']
                self.assertGreater(performance['gpu_summary_partition_count'], 1)
                self.assertEqual(performance['gpu_summary_aggregation_contract'], partitions.CONTRACT)
                self.assertLessEqual(performance['gpu_summary_partition_workspace_bytes'], partitions.WORKSPACE_LIMIT_BYTES)
                self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
                self.assertEqual(performance['compute_execution_state'], 'gpu_active')
                self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)

    def test_merge_matches_independent_reference_for_all_typed_fields(self):
        original = resident.accumulate_resident_summary_gpu_cuda
        examined = []

        def inspect(events, request, session):
            isolated = summary.GpuSummaryAccumulatorSession()
            summary._initialize_session(isolated, request)
            workspace = partitions.create_workspace(gpu._CUDA, isolated.device_state, isolated.transfer_layout, 4)
            reset, merge = partitions.make_merge_kernels(gpu._CUDA)
            blocks = (workspace.storage.size + 127) // 128
            workspace.storage.copy_to_device(np.full(workspace.storage.size, -77, dtype=np.int64))
            reset[blocks, 128](workspace.storage, workspace.sentinel_ranges)
            cleared = workspace.storage.copy_to_host()
            expected_cleared = np.zeros_like(cleared)
            for start, end in workspace.sentinel_ranges.copy_to_host():
                expected_cleared[start:end] = np.iinfo(np.int64).max
            np.testing.assert_array_equal(cleared, expected_cleared)
            baseline = isolated.device_transfer.copy_to_host()
            expected = baseline.copy()
            expected_int = expected.view(np.int64)
            partial = cleared.copy()
            for logical, start, target, length, mode in workspace.metadata.copy_to_host():
                for partition in range(workspace.count):
                    addresses = slice(start + partition * length, start + (partition + 1) * length)
                    self.assertNotEqual(mode, 1)
                    values = np.arange(length, dtype=np.int64) + partition + 1
                    partial[addresses] = values
                    if mode == 2:
                        expected_int[target:target + length] = np.minimum(expected_int[target:target + length], values)
                    else:
                        prior = expected_int[target:target + length].copy()
                        expected_int[target:target + length] += values
                        special = 0 if mode == 3 else 4 if mode == 4 else -1
                        if special >= 0:
                            expected_int[target + special] = max(prior[special], values[special])
                        if mode == 4:
                            for position in range(5, length):
                                if (position - 5) % 3 < 2:
                                    expected_int[target + position] = max(prior[position], values[position])
            workspace.storage.copy_to_device(partial)
            merge[(workspace.logical_words + 127) // 128, 128](
                workspace.logical_words, workspace.count, workspace.metadata,
                workspace.storage, isolated.device_transfer.view(np.int64),
            )
            np.testing.assert_array_equal(isolated.device_transfer.copy_to_host(), expected)
            examined.append(workspace.nbytes)
            return original(events, request, session)

        scene = WORKLOADS['trapped_corridor_depth10'](1024)
        scene.config.compute_backend = 'gpu_cuda'
        with patch.dict(os.environ, {'LEAKAGE_GPU_SUMMARY_PARTITIONS': '4'}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', side_effect=inspect):
            result = run_direct_ray_trace(scene)
        self.assertTrue(examined)
        self.assertEqual(result.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)

    def test_partition_selection_honors_budget_and_invalid_settings(self):
        original = resident.accumulate_resident_summary_gpu_cuda
        examined = []

        def inspect(events, request, session):
            state = {field.name: getattr(request.state, field.name) for field in fields(request.state)
                     if field.name in summary._NUMERIC_STATE_FIELDS}
            self.assertEqual(partitions.choose_count(state, 64, requested='auto'), 1)
            for invalid in ('no', '0', '-1', '3', '256'):
                with self.assertRaises(ValueError):
                    partitions.choose_count(state, 65536, requested=invalid)
            count = partitions.choose_count(state, 65536, requested='auto')
            examined.append(count)
            execution = original(events, request, session)
            workspace = session.partition_workspace
            self.assertNotIn('grid_flux_lumen', workspace.arrays)
            self.assertNotIn('grid_flux_squared_lumen2', workspace.arrays)
            self.assertNotIn('grid_flux_squared_totals_lumen2', workspace.arrays)
            for name, values in state.items():
                if values.dtype == np.dtype(np.float64):
                    self.assertNotIn(name, workspace.arrays)
            self.assertLessEqual(workspace.nbytes, partitions.WORKSPACE_LIMIT_BYTES)
            return execution

        scene = WORKLOADS['face_direct'](1024)
        scene.config.compute_backend = 'gpu_cuda'
        with patch.dict(os.environ, {'LEAKAGE_GPU_SUMMARY_PARTITIONS': '4'}), patch.object(resident, 'accumulate_resident_summary_gpu_cuda', side_effect=inspect):
            actual = run_direct_ray_trace(scene)
        self.assertTrue(examined)
        self.assertGreater(actual.metrics['_performance_summary']['gpu_summary_partition_count'], 1)

    def test_sparse_selection_keeps_replacements_for_new_dead_ends(self):
        cuda = gpu._CUDA
        select, _ = resident._ensure_path_kernels()
        for existing_count, existing_dead, quota in ((0, 0, 8), (5, 3, 8)):
            selection = resident.GpuResidentPathSelection(existing_count, existing_dead, quota)
            capacity = resident._path_candidate_capacity(selection, 64)
            free = quota - existing_count
            self.assertEqual(capacity, 2 * free + existing_dead)
            kinds = np.ones(64, dtype=np.int8)
            kinds[:free] = 3
            slots = cuda.device_array(capacity, dtype=np.int64)
            count = cuda.to_device(np.zeros(1, dtype=np.int64))
            select[1, 1](64, existing_count, existing_dead, quota, capacity, cuda.to_device(kinds), slots, count)
            self.assertEqual(int(count.copy_to_host()[0]), capacity)
            np.testing.assert_array_equal(slots.copy_to_host(), np.arange(capacity, dtype=np.int64))


if __name__ == '__main__':
    unittest.main()
