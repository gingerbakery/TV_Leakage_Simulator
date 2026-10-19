from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf4a_target_workloads import WORKLOADS
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as cuda_backend
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


class Perf5GpuWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = cuda_backend.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in (
            'available', 'strict_float64', 'kernel_executed', 'kernel_verified',
        )):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or cuda_backend.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('production CUDA contract mismatch')

    def assertParity(self, expected, actual):
        report = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(report.passed, report.to_dict())

    def test_terminal_histogram_recovers_every_visited_depth(self):
        histogram = np.asarray([3, 0, 2, 1, 0], dtype=np.int64)
        actual = resident._active_counts_from_terminal_histogram(histogram, 6)
        np.testing.assert_array_equal(actual, [6, 3, 3, 1, 0])
        for invalid in (np.asarray([3, -1, 4]), np.asarray([3, 2])):
            with self.assertRaises(ValueError):
                resident._active_counts_from_terminal_histogram(invalid, 6)

    def test_packed_session_reuses_disjoint_views_and_keeps_snapshots_immutable(self):
        trace_input = build_depth_ten_case(1025)
        expected = run_direct_ray_trace(trace_input)
        trace_input.config.compute_backend = 'gpu_cuda'
        accumulate = resident.accumulate_resident_summary_gpu_cuda
        snapshots = []
        sessions = []

        def inspect(events, request, session):
            self.assertEqual(events.event_faces.strides[0], np.dtype(np.int32).itemsize)
            execution = accumulate(events, request, session)
            sessions.append((id(session), id(session.device_transfer)))
            intervals = sorted((start, end) for start, end, dtype, shape in session.transfer_layout.values() if end > start)
            self.assertEqual(intervals[0][0], 0)
            self.assertEqual(intervals[-1][1], session.host_transfer.nbytes)
            for previous, current in zip(intervals, intervals[1:]):
                self.assertEqual(previous[1], current[0])
                self.assertEqual(current[0] % 8, 0)
            state = execution.result.state
            snapshot = {field.name: getattr(state, field.name).tobytes() for field in fields(state)}
            snapshots.append((state, snapshot))
            return execution

        with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', side_effect=inspect):
            actual = run_direct_ray_trace(trace_input, intersection_batch_size=512)
        self.assertParity(expected, actual)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(len(set(sessions)), 1)
        for state, snapshot in snapshots:
            for field in fields(state):
                values = getattr(state, field.name)
                self.assertTrue(values.flags.owndata)
                self.assertFalse(values.flags.writeable)
                self.assertEqual(values.tobytes(), snapshot[field.name])
        performance = actual.metrics['_performance_summary']
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_unused_depth_slots_can_contain_stale_faces_and_nan_power(self):
        absorber = build_depth_ten_case(257)
        absorber.config.max_depth = 1000
        absorber.config.angle_dependent_reflectance = False
        absorber.optical_profiles[0].reflectance = 0.0
        for name, trace_input in (('absorbed', absorber), ('direct', WORKLOADS['face_direct'](257))):
            with self.subTest(name=name):
                expected = run_direct_ray_trace(trace_input)
                trace_input.config.compute_backend = 'gpu_cuda'
                accumulate = resident.accumulate_resident_summary_gpu_cuda
                poisoned = []

                def poison(events, request, session):
                    depths = events.terminal_depth[:events.ray_count].copy_to_host()
                    kinds = events.terminal_kind[:events.ray_count].copy_to_host()
                    counts = depths.astype(np.int64) + (kinds == 3)
                    self.assertTrue(np.all(counts <= events.event_faces.shape[1]))
                    unused = np.arange(events.event_faces.shape[1])[None, :] >= counts[:, None]
                    poisoned.append(int(np.count_nonzero(unused)))
                    for field_name, value in (
                        ('event_faces', 0), ('event_status', 65535),
                        ('event_incoming_power', np.nan), ('event_reflected_power', np.nan),
                        ('event_emitted_power', np.nan), ('event_lobes', 127),
                    ):
                        device_values = getattr(events, field_name)
                        host = device_values.copy_to_host()
                        host[:events.ray_count][unused] = value
                        device_values.copy_to_device(host)
                    return accumulate(events, request, session)

                with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', side_effect=poison):
                    actual = run_direct_ray_trace(trace_input, intersection_batch_size=512)
                self.assertGreater(sum(poisoned), 0)
                self.assertParity(expected, actual)
                self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 0)

    def test_missing_live_event_fails_closed_and_replays_the_chunk(self):
        trace_input = build_depth_ten_case(257)
        expected = run_direct_ray_trace(trace_input)
        trace_input.config.compute_backend = 'gpu_cuda'
        accumulate = resident.accumulate_resident_summary_gpu_cuda

        def corrupt(events, request, session):
            faces = events.event_faces.copy_to_host()
            faces[0, 0] = -1
            events.event_faces.copy_to_device(faces)
            return accumulate(events, request, session)

        with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', side_effect=corrupt):
            actual = run_direct_ray_trace(trace_input, intersection_batch_size=512)
        self.assertParity(expected, actual)
        self.assertEqual(actual.metrics['_performance_summary']['gpu_resident_wavefront_fallback_count'], 1)


if __name__ == '__main__':
    unittest.main()
