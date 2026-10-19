from __future__ import annotations

from dataclasses import fields
import os
from pathlib import Path
import subprocess
import sys
import unittest
import weakref
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, semantic_hash
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from leakage_simulator import native_cpu_transport, raytracer
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator.native_cpu_intersection import probe_native_cpu
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig


class CpuTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not probe_native_cpu().available:
            raise unittest.SkipTest('Numba unavailable')

    def paired_trace(self, trace_input, *, workers=4):
        tapes = {'wavefront': [], 'fused': []}
        results = {}
        original = raytracer._reduce_wavefront_event_tape_ordered
        for mode in tapes:
            def capture(tape, *args):
                tapes[mode].append(tape)
                return original(tape, *args)
            with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': mode, 'LEAKAGE_CPU_WORKERS': str(workers)}), patch.object(raytracer, '_reduce_wavefront_event_tape_ordered', side_effect=capture):
                results[mode] = raytracer.run_direct_ray_trace(trace_input)
        performance = results['fused'].metrics['_performance_summary']
        self.assertGreater(performance.get('wavefront_cpu_transport_chunk_count', 0), 0, performance)
        self.assertEqual(performance.get('wavefront_cpu_transport_fallback_count', 0), 0)
        self.assertEqual(semantic_hash(results['fused']), semantic_hash(results['wavefront']))
        self.assertEqual(len(tapes['fused']), len(tapes['wavefront']))
        for expected, actual in zip(tapes['wavefront'], tapes['fused']):
            for field in fields(expected):
                if field.name == 'peak_bytes':
                    continue
                expected_value, actual_value = getattr(expected, field.name), getattr(actual, field.name)
                if isinstance(expected_value, np.ndarray):
                    np.testing.assert_array_equal(actual_value, expected_value, err_msg=field.name)
                    if expected_value.dtype == np.float64:
                        np.testing.assert_array_equal(actual_value.view(np.uint64), expected_value.view(np.uint64), err_msg=field.name)
                else:
                    self.assertEqual(actual_value, expected_value, field.name)
        return results

    def test_sources_transport_and_paths_are_bit_exact(self):
        for name, builder in WORKLOADS.items():
            for paths in (False, True):
                with self.subTest(scene=name, paths=paths):
                    trace_input = builder(2500)
                    trace_input.config.store_ray_paths = paths
                    trace_input.config.max_stored_paths = 23 if paths else 0
                    self.paired_trace(trace_input)

    def test_optics_termination_and_receiver_mis_are_bit_exact(self):
        for scatter in ('specular', 'lambertian', 'gaussian', 'mixed'):
            for termination in ('threshold', 'russian_roulette'):
                with self.subTest(scatter=scatter, termination=termination):
                    trace_input = build_depth_ten_case(2500)
                    for profile in trace_input.optical_profiles:
                        profile.scatter_model = scatter
                        profile.reflectance = 0.6
                    trace_input.config.min_energy = 0.0001
                    trace_input.config.termination_mode = termination
                    trace_input.config.bounce_sampling_strategy = 'receiver_mis'
                    self.paired_trace(trace_input)

    def test_high_depth_and_zero_power_keep_terminal_contract(self):
        for power in (0.0, 1.0):
            with self.subTest(power=power):
                trace_input = build_depth_ten_case(20)
                trace_input.emitters[0].power_lumen = power
                trace_input.config.min_energy = 0.0
                trace_input.config.max_depth = 1000
                trace_input.config.angle_dependent_reflectance = False
                trace_input.optical_profiles[0].reflectance = 0.999
                self.paired_trace(trace_input, workers=1)

    def test_medium_depth_uses_compact_recording_without_changing_physics(self):
        for depth in (16, 20, 32, 33):
            with self.subTest(depth=depth):
                trace_input = build_depth_ten_case(1000)
                trace_input.config.max_depth = depth
                result = self.paired_trace(trace_input)
                self.assertEqual(result['fused'].metrics['_performance_summary']['wavefront_cpu_transport_record_layout'], 'bounded_actual_event_pool_v1')

    def test_failed_fused_batch_replays_without_partial_commit(self):
        trace_input = WORKLOADS['trapped_corridor_depth10'](2500)
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'wavefront'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'fused'}), patch.object(native_cpu_transport, 'trace_native_cpu_transport', side_effect=ValueError('injected invalid tape')):
            actual = raytracer.run_direct_ray_trace(trace_input)
        self.assertEqual(semantic_hash(actual), semantic_hash(expected))
        self.assertEqual(actual.metrics['_performance_summary']['wavefront_cpu_transport_fallback_count'], 1)

    def test_parallel_event_packing_owns_only_live_rows(self):
        counts = np.asarray([0, 3, 1, 4, 0], dtype=np.int64)
        offsets = np.concatenate(([0], np.cumsum(counts)))
        row_indices = np.repeat(np.arange(len(counts)), counts)
        depth_indices = np.arange(int(offsets[-1])) - np.repeat(offsets[:-1], counts)
        dtypes = (np.int64, np.float64, np.float64, np.float64, np.uint16, np.int8, np.int8, np.float64, np.float64, np.float64, np.uint8, np.float64)
        for include_paths in (False, True):
            events = tuple(
                np.full((len(counts), 4, 3) if index in (7, 8) else (len(counts), 4), np.nan if dtype == np.float64 else 77, dtype=dtype)
                for index, dtype in enumerate(dtypes)
            )
            for values in events:
                for primary, depth in zip(row_indices, depth_indices):
                    values[primary, depth] = primary * 4 + depth
            events[2][1, 0] = -0.0
            actual, _ = native_cpu_transport._pack_events(events, counts, offsets, include_paths)
            expected = tuple(
                values[row_indices, depth_indices] if include_paths or index not in (7, 8, 9)
                else np.empty((0, 3) if index in (7, 8) else 0, dtype=values.dtype)
                for index, values in enumerate(events)
            )
            for packed, reference in zip(actual, expected):
                np.testing.assert_array_equal(packed, reference)
                if reference.dtype == np.float64:
                    np.testing.assert_array_equal(packed.view(np.uint64), reference.view(np.uint64))
                self.assertFalse(packed.flags.writeable)
                self.assertTrue(packed.flags.owndata)
            for values in events:
                values.fill(0)
            for packed, reference in zip(actual, expected):
                np.testing.assert_array_equal(packed, reference)

    def test_indexed_oracle_preserves_numpy_exact_order(self):
        rng = np.random.default_rng(42)
        for shape in ((19,), (3, 7)):
            expected = rng.random(shape)
            actual = expected.copy()
            addresses = rng.integers(0, expected.size, 5000, dtype=np.int64)
            indices = np.unravel_index(addresses, shape) if len(shape) == 2 else addresses
            values = np.exp2(rng.integers(-500, 100, len(addresses))) * rng.random(len(addresses))
            np.add.at(expected, indices, values)
            reducer._ordered_float_add(actual, indices, values)
            np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))

    def test_packet_squares_keep_cpython_power_rounding(self):
        rng = np.random.default_rng(57)
        values = np.concatenate((rng.random(10000) * 1e5, [0.0, -0.0, 1e-160, float.fromhex('0x1.9d0dc7ce2b7ecp+15')]))
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'fused'}):
            for packets in (values, np.repeat(values[-1:], 8192), np.zeros(8192)):
                expected = np.asarray([float(power) ** 2 for power in packets])
                np.testing.assert_array_equal(reducer.exact_packet_squares(packets).view(np.uint64), expected.view(np.uint64))

    def test_actual_cad_face_mapping_and_paths_are_bit_exact(self):
        from benchmark_perf5_cpu import cad_case
        trace_input, _ = cad_case(1000)
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 11
        self.paired_trace(trace_input)

    def test_seed_changes_and_primary_mis_keep_exact_packets(self):
        for seed in (41, 43):
            trace_input = WORKLOADS['stochastic_two_bounce'](2500)
            trace_input.config.seed = seed
            trace_input.config.primary_sampling_strategy = 'receiver_mis'
            for emitter in trace_input.emitters:
                emitter.seed = seed
            self.paired_trace(trace_input)

    def test_fused_cpu_fresh_process_does_not_import_cuda(self):
        code = '''
import sys, os
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'src'), str(root / 'scripts')]
class BlockCuda:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'numba.cuda' or fullname.startswith('numba.cuda.'):
            raise AssertionError('Fused CPU attempted CUDA import')
sys.meta_path.insert(0, BlockCuda())
os.environ['LEAKAGE_CPU_TRANSPORT'] = 'fused'
from benchmark_perf3b2a_multibounce import build_two_bounce_case
from leakage_simulator.raytracer import run_direct_ray_trace
result = run_direct_ray_trace(build_two_bounce_case(1000))
performance = result.metrics['_performance_summary']
assert performance['wavefront_cpu_transport_chunk_count'] > 0, performance
assert performance.get('wavefront_cpu_transport_fallback_count', 0) == 0
assert performance['compute_execution_state'] == 'cpu'
assert 'numba.cuda' not in sys.modules
'''
        process = subprocess.run(
            [sys.executable, '-c', code, str(ROOT)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_auto_uses_bounded_actual_events_for_high_depth(self):
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'auto'}):
            regular = raytracer.run_direct_ray_trace(WORKLOADS['face_direct'](1000))
            performance = regular.metrics['_performance_summary']
            self.assertEqual(performance['intersection_batch_size'], 32768)
            self.assertGreater(performance['wavefront_cpu_transport_chunk_count'], 0)
            deep_case = build_depth_ten_case(12000)
            deep_case.config.max_depth = 1000
            deep_case.optical_profiles[0].reflectance = 0.0
            deep = raytracer.run_direct_ray_trace(deep_case)
            deep_performance = deep.metrics['_performance_summary']
            self.assertTrue(deep_performance['wavefront_cpu_transport_auto_requested'])
            self.assertEqual(deep_performance.get('wavefront_cpu_transport_fallback_count', 0), 0)
            self.assertEqual(deep_performance['wavefront_cpu_transport_record_layout'], 'bounded_actual_event_pool_v1')
            self.assertLessEqual(deep_performance['wavefront_cpu_transport_event_pool_capacity'], native_cpu_transport.MAX_BATCH_EVENT_SLOTS)
            self.assertGreater(deep_performance['wavefront_cpu_transport_max_batch_size'], deep_performance['intersection_batch_size'])
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'wavefront'}):
            reference = raytracer.run_direct_ray_trace(deep_case)
        self.assertEqual(semantic_hash(deep), semantic_hash(reference))

    def test_actual_event_pool_budget_splits_without_partial_commit(self):
        trace_input = WORKLOADS['stochastic_two_bounce'](1000)
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 17
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'wavefront'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'fused', 'LEAKAGE_CPU_EVENT_LAYOUT': 'compact'}), patch.object(native_cpu_transport, 'MAX_BATCH_EVENT_SLOTS', 64):
            actual = raytracer.run_direct_ray_trace(trace_input)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(semantic_hash(actual), semantic_hash(expected))
        self.assertGreater(performance['wavefront_cpu_transport_event_budget_split_count'], 0)
        self.assertGreater(performance['wavefront_cpu_transport_abandoned_execute_sec'], 0.0)
        self.assertEqual(performance.get('wavefront_cpu_transport_fallback_count', 0), 0)
        self.assertLessEqual(performance['wavefront_cpu_transport_event_pool_capacity'], 64)

    def test_native_failure_after_growth_safely_splits_legacy_replay(self):
        trace_input = build_depth_ten_case(12000)
        trace_input.config.max_depth = 1000
        trace_input.optical_profiles[0].reflectance = 0.0
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'wavefront'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        original = native_cpu_transport.trace_native_cpu_transport

        def fail_large_batch(mesh, origins, *args, **kwargs):
            if len(origins) > 2048:
                raise ValueError('injected post-growth failure')
            return original(mesh, origins, *args, **kwargs)

        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'auto'}), patch.object(native_cpu_transport, 'trace_native_cpu_transport', side_effect=fail_large_batch):
            actual = raytracer.run_direct_ray_trace(trace_input)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(semantic_hash(actual), semantic_hash(expected))
        self.assertEqual(performance['wavefront_cpu_transport_fallback_count'], 1)
        self.assertGreater(performance['wavefront_cpu_transport_legacy_split_count'], 0)

    def test_failed_pool_is_released_before_split_replay(self):
        trace_input = build_depth_ten_case(1000)
        trace_input.config.max_depth = 1000
        trace_input.optical_profiles[0].reflectance = 0.0
        original = native_cpu_transport.trace_native_cpu_transport
        abandoned_pools = []

        def constrained_transport(mesh, origins, *args, **kwargs):
            self.assertTrue(all(reference() is None for reference in abandoned_pools))
            if len(origins) > 125:
                scratch_pool = np.empty(1024, dtype=np.float64)
                abandoned_pools.append(weakref.ref(scratch_pool))
                raise native_cpu_transport.NativeCpuEventBudgetExceeded(1024, 0.01)
            return original(mesh, origins, *args, **kwargs)

        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'auto'}), patch.object(native_cpu_transport, 'trace_native_cpu_transport', new=constrained_transport):
            actual = raytracer.run_direct_ray_trace(trace_input)
        performance = actual.metrics['_performance_summary']
        self.assertGreater(performance['wavefront_cpu_transport_event_budget_split_count'], 0)
        self.assertEqual(performance.get('wavefront_cpu_transport_fallback_count', 0), 0)
        self.assertTrue(all(reference() is None for reference in abandoned_pools))

    def test_unexpected_deep_emitter_is_split_without_reducing_depth(self):
        mesh = TriangleMesh()
        for lower_x, upper_x, height, material in ((-5, 0, 1, 'absorb'), (0, 5, 1, 'mirror'), (-5, 5, 0, 'mirror')):
            vertices = [mesh.add_vertex(point) for point in (
                (lower_x, -5, height), (upper_x, -5, height),
                (upper_x, 5, height), (lower_x, 5, height),
            )]
            mesh.add_face(vertices[0], vertices[1], vertices[2], material)
            mesh.add_face(vertices[0], vertices[2], vertices[3], material)
        trace_input = DirectRayTraceInput(
            mesh=mesh,
            emitters=[EmitterSpec(
                emitter_id=f'source_{index}', emitter_type='datum_plane',
                center=(center_x, 0.0, 0.5), u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, 1.0, 0.0),
                width_mm=0.1, height_mm=0.1, direction_distribution='gaussian',
                gaussian_sigma_deg=0.000001, power_lumen=1.0, ray_count=8, seed=42 + index,
            ) for index, center_x in enumerate((-2.0, 2.0))],
            receivers=[], optical_profiles=[
                OpticalProfile('absorb', 0.0, scatter_model='specular'),
                OpticalProfile('mirror', 0.999, scatter_model='specular'),
            ],
            config=RayTraceConfig(
                ray_count=16, max_depth=1000, min_energy=0.0,
                angle_dependent_reflectance=False, intersection_backend='bvh',
                store_ray_paths=True, max_stored_paths=16,
            ),
        )
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'wavefront', 'LEAKAGE_CPU_WORKERS': '1'}):
            expected = raytracer.run_direct_ray_trace(trace_input)
        with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'auto', 'LEAKAGE_CPU_WORKERS': '1'}), patch.object(native_cpu_transport, 'MAX_BATCH_EVENT_SLOTS', 2048):
            actual = raytracer.run_direct_ray_trace(trace_input)
        performance = actual.metrics['_performance_summary']
        self.assertEqual(semantic_hash(actual), semantic_hash(expected))
        self.assertEqual(performance['wavefront_max_observed_depth'], 1000)
        self.assertGreater(performance['wavefront_cpu_transport_event_budget_split_count'], 0)
        self.assertEqual(performance.get('wavefront_cpu_transport_fallback_count', 0), 0)
        self.assertLessEqual(performance['wavefront_cpu_transport_event_pool_capacity'], 2048)

    def test_compact_packing_does_not_read_unused_pool_slots(self):
        counts = np.asarray([0, 3, 1, 4, 0], dtype=np.int64)
        offsets = np.concatenate(([0], np.cumsum(counts)))
        source_offsets = np.asarray([0, 1, 7, 8, 12], dtype=np.int64)
        positions = np.concatenate([np.arange(start, start + count) for start, count in zip(source_offsets, counts)])
        dtypes = (np.int64, np.float64, np.float64, np.float64, np.uint16, np.int8, np.int8, np.float64, np.float64, np.float64, np.uint8, np.float64)
        for include_paths in (False, True):
            events = tuple(np.full(
                (12, 1, 3) if index in (7, 8) else (12, 1),
                np.nan if dtype == np.float64 else 77, dtype=dtype,
            ) for index, dtype in enumerate(dtypes))
            for values in events:
                for position in positions:
                    values[position, 0] = position
            events[2][1, 0] = -0.0
            actual, _ = native_cpu_transport._pack_events(events, counts, offsets, include_paths, source_offsets)
            expected = tuple(
                values[positions, 0] if include_paths or index not in (7, 8, 9)
                else np.empty((0, 3) if index in (7, 8) else 0, dtype=values.dtype)
                for index, values in enumerate(events)
            )
            for values in events:
                values.fill(0)
            for packed, reference in zip(actual, expected):
                np.testing.assert_array_equal(packed, reference)
                if reference.dtype == np.float64:
                    np.testing.assert_array_equal(packed.view(np.uint64), reference.view(np.uint64))
                self.assertTrue(packed.flags.owndata)
                self.assertFalse(packed.flags.writeable)


if __name__ == '__main__':
    unittest.main()
