from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

import test_perf5_emitted_flux as baseline
from benchmark_perf5_cpu import WORKLOADS
from leakage_simulator import gpu_cuda_emitted_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_summary_partitions as partitions
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator.raytracer import run_direct_ray_trace


@lru_cache(maxsize=1)
def cycle_samples():
    generator = np.random.default_rng(191920)
    sequences = [((0.125, 0.0625), 0.1, 16389), ((2.0 ** -53, 2.0 ** -52), 1.0, 16389),
        ((2.0 ** -1074, 2.0 ** -1072), 0.0, 16389), ((1e308, 1e308), 0.0, 17),
        ((0.125, 0.0625), -0.0, 0)]
    for sample in range(2000):
        length = int(generator.integers(1, 9))
        exponent = int(generator.integers(-1073, 1001))
        initial = float(np.ldexp(generator.uniform(1.0, 2.0), exponent))
        if sample % 3 == 0:
            powers = tuple(float(np.spacing(initial) * (generator.integers(0, 8) + 0.5)) for _ in range(length))
        else:
            powers = tuple(max(float(np.nextafter(0.0, 1.0)), float(np.ldexp(generator.uniform(0.5, 1.0),
                max(-1073, min(1000, exponent + int(generator.integers(-60, 61))))))) for _ in range(length))
        powers = tuple(max(float(np.nextafter(0.0, 1.0)), value) for value in powers)
        if sample % 5 == 0:
            initial = 0.0
        sequences.append((powers, initial, int(generator.integers(0, 1025))))
    metadata = np.zeros((len(sequences), 41), dtype=np.int64)
    initials = np.asarray([initial for _, initial, _ in sequences])
    lengths = np.asarray([len(powers) for powers, _, _ in sequences], dtype=np.int64)
    repetitions = np.asarray([count for _, _, count in sequences], dtype=np.int64)
    expected = initials.copy()
    for index, (powers, initial, count) in enumerate(sequences):
        for depth, value in enumerate(powers, 1):
            metadata[index, 5 + 3 * (3 + depth)] = struct.unpack('<q', struct.pack('<d', value))[0] + 1
        total = initial
        for repeat in range(count):
            for value in powers:
                total += value
        expected[index] = total
    return initials, repetitions, lengths, metadata, expected


class UniformEmissionLayoutTests(unittest.TestCase):
    def test_default_mode_uses_ordered_flux_with_bounded_uniform_metadata(self):
        with patch.dict(os.environ):
            os.environ.pop('LEAKAGE_GPU_EMITTED_FLUX', None)
            os.environ.pop('LEAKAGE_GPU_EMITTED_UNIFORM', None)
            self.assertEqual(flux.DEFAULT_MODE, 'ordered')
            self.assertEqual(flux.uniform_metadata_words(11), 42)

    def test_metadata_requires_explicit_ordered_mode_and_output_group_budget(self):
        for mode in ('atomic', 'ordered'):
            for uniform in ('off', 'auto'):
                with patch.dict(os.environ, {'LEAKAGE_GPU_EMITTED_FLUX': mode,
                        'LEAKAGE_GPU_EMITTED_UNIFORM': uniform}):
                    for depths in (1, 11, 1001):
                        expected = 3 * (3 + depths) if mode == 'ordered' and uniform == 'auto' else 0
                        self.assertEqual(flux.uniform_metadata_words(depths), expected)
        with patch.dict(os.environ, {'LEAKAGE_GPU_EMITTED_UNIFORM': 'invalid'}):
            with self.assertRaises(ValueError):
                flux.uniform_metadata_words(11)

    def test_cycle_integer_skips_match_independent_sequential_fp64(self):
        fake_cuda = SimpleNamespace(jit=lambda **options: lambda function: function)
        device = SimpleNamespace(dadd_rn=lambda first, second: float(first) + float(second),
            double_as_longlong=lambda value: struct.unpack('<q', struct.pack('<d', value))[0],
            longlong_as_double=lambda bits: struct.unpack('<d', struct.pack('<q', int(bits)))[0])
        with patch.object(flux.importlib, 'import_module', return_value=device):
            repeat_cycle = flux.make_repeat_cycle(fake_cuda)
        initials, repetitions, lengths, metadata, expected = cycle_samples()
        actual = np.asarray([repeat_cycle(float(initials[index]), int(repetitions[index]),
            int(lengths[index]), metadata[index]) for index in range(len(initials))])
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))


class UniformEmissionIntegrationTests(baseline.EmittedFluxGpuTests):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {'LEAKAGE_GPU_EMITTED_UNIFORM': 'auto'}))

    def test_real_cuda_cycles_match_independent_sequential_fp64(self):
        cuda = gpu._CUDA
        repeat_cycle = flux.make_repeat_cycle(cuda)

        @cuda.jit(fastmath=False)
        def compute(initials, repetitions, lengths, metadata, output):
            index = cuda.grid(1)
            if index < len(output):
                output[index] = repeat_cycle(initials[index], repetitions[index], lengths[index], metadata[index])

        initials, repetitions, lengths, metadata, expected = cycle_samples()
        device_inputs = [cuda.to_device(values) for values in (initials, repetitions, lengths, metadata)]
        output = cuda.device_array(len(initials), dtype=np.float64)
        compute[(len(initials) + 127) // 128, 128](*device_inputs, output)
        np.testing.assert_array_equal(output.copy_to_host().view(np.uint64), expected.view(np.uint64))

    def test_partitioned_metadata_matches_packets_and_resets_between_chunks(self):
        original = resident.accumulate_resident_summary_gpu_cuda
        observed = []

        def inspect(events, request, session):
            output = original(events, request, session)
            counts = session.chunk_counts.copy_to_host()
            status = events.event_status.copy_to_host()[:events.ray_count]
            powers = events.event_emitted_power.copy_to_host()[:events.ray_count]
            lobes = events.event_lobes.copy_to_host()[:events.ray_count]
            terminal = events.terminal_kind.copy_to_host()[:events.ray_count]
            depths = events.terminal_depth.copy_to_host()[:events.ray_count]
            visited = depths.astype(np.int64) + (terminal == reducer.TERMINAL_BLOCKED)
            columns = np.arange(status.shape[1])[None, :]
            valid = ((status & reducer.STATUS_EMITTED) != 0) & (columns < visited[:, None])
            metadata = counts[5:].reshape(-1, 3)
            for group in range(len(metadata)):
                group_mask = (lobes == group) if group < reducer.LOBE_COUNT else (
                    columns == group - reducer.LOBE_COUNT - 1)
                bits = np.asarray(powers[valid & group_mask], dtype=np.float64).view(np.int64)
                if bits.size:
                    self.assertIn(metadata[group, 0] - 1, bits)
                    self.assertEqual(metadata[group, 1], int(bits.min() != bits.max()))
                    self.assertEqual(metadata[group, 2], bits.size)
                else:
                    np.testing.assert_array_equal(metadata[group], 0)
            observed.append((events.ray_count, output.partition_count,
                session.emitted_flux_workspace.modes.copy_to_host()[:, 0].sum()))
            return output

        for requested in ('1', '128'):
            observed.clear()
            with self.subTest(partitions=requested), patch.dict(os.environ, {
                    'LEAKAGE_GPU_SUMMARY_PARTITIONS': requested}), patch.object(
                    resident, 'accumulate_resident_summary_gpu_cuda', new=inspect):
                scene = WORKLOADS['trapped_corridor_depth10'](73731)
                scene.config.compute_backend = 'gpu_cuda'
                scene.config.store_ray_paths = False
                actual = run_direct_ray_trace(scene, intersection_batch_size=65536)
            self.assertEqual(len(observed), 2, (observed, actual.metrics['_performance_summary']))
            self.assertEqual(sum(count for count, _, _ in observed), actual.total_rays)
            if requested == '128':
                self.assertGreater(max(partitions for _, partitions, _ in observed), 1)
            else:
                self.assertTrue(all(partitions == 1 for _, partitions, _ in observed))
            self.assertGreater(sum(uniform for _, _, uniform in observed), 0)
            performance = actual.metrics['_performance_summary']
            self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)
            self.assertEqual(performance['gpu_cuda_hybrid_cpu_attempt_count'], 0)

    def test_merge_detects_different_constants_in_individually_uniform_partitions(self):
        cuda = gpu._CUDA
        dtype = np.dtype(np.int64)
        layout = {name: (0, 0, dtype, (0,)) for name in partitions._TOUCH_FIELDS}
        layout['chunk_counts'] = (0, 88, dtype, (11,))
        workspace = partitions.create_workspace(cuda, {}, layout, 4)
        partial = np.zeros((4, 11), dtype=np.int64)
        first_bits = np.float64(0.125).view(np.int64) + 1
        second_bits = np.float64(0.0625).view(np.int64) + 1
        partial[0, 5:8] = (first_bits, 0, 17)
        partial[1, 5:8] = (second_bits, 0, 19)
        partial[2, 8:11] = (first_bits, 0, 23)
        partial[3, 8:11] = (first_bits, 0, 29)
        partial[2, 4] = 3
        workspace.arrays['chunk_counts'].copy_to_device(partial)
        target = cuda.to_device(np.zeros(11, dtype=np.int64))
        _, merge = partitions.make_merge_kernels(cuda)
        merge[1, 128](workspace.logical_words, 4, workspace.metadata, workspace.storage, target)
        actual = target.copy_to_host()
        np.testing.assert_array_equal(actual[5:8], (max(first_bits, second_bits), 1, 36))
        np.testing.assert_array_equal(actual[8:11], (first_bits, 0, 52))
        self.assertEqual(actual[4], 3)

    def test_single_lobe_depth_cycle_uses_exact_shortcut_with_distinct_initial_totals(self):
        cuda = gpu._CUDA
        count = 8197
        records = 5
        powers = (0.19, 0.07, 0.03, 0.11)
        status = np.zeros((count, records), dtype=np.uint16, order='F')
        status[:, :4] = reducer.STATUS_EMITTED
        emitted = np.full((count, records), np.nan, order='F')
        emitted[:, :4] = powers
        lobes = np.zeros((count, records), dtype=np.int8, order='F')
        shapes = {'reflection_lobe_flux_lumen': (3, 2), 'reflection_depth_flux_lumen': (5, 2),
            'contribution_lobe_flux_lumen': (3, 5), 'contribution_depth_flux_lumen': (5, 7)}
        expected = {name: np.full(shape, 0.01 if name.startswith('reflection') else 0.07)
            for name, shape in shapes.items()}
        state = {name: cuda.to_device(values) for name, values in expected.items()}
        metadata = np.zeros((8, 3), dtype=np.int64)
        bits = np.asarray(powers).view(np.int64)
        metadata[0] = (bits.max() + 1, int(bits.min() != bits.max()), count * 4)
        for depth, power in enumerate(powers, 1):
            metadata[3 + depth] = (bits[depth - 1] + 1, 0, count)
        for primary in range(count):
            for depth, power in enumerate(powers, 1):
                expected['reflection_lobe_flux_lumen'][0, 0] += power
                expected['contribution_lobe_flux_lumen'][0, reducer.OUTCOME_EMITTED] += power
                expected['reflection_depth_flux_lumen'][depth, 0] += power
                expected['contribution_depth_flux_lumen'][depth, reducer.CONTRIBUTION_DEPTH_EMITTED] += power
        lobe_counts = np.zeros((3, 5), dtype=np.int64)
        lobe_counts[0, reducer.OUTCOME_EMITTED] = count * 4
        depth_counts = np.zeros((5, 5), dtype=np.int64)
        depth_counts[1:, reducer.REF_DEPTH_EMITTED] = count
        state['reflection_lobe_counts'] = cuda.to_device(lobe_counts)
        state['reflection_depth_counts'] = cuda.to_device(depth_counts)
        events = SimpleNamespace(ray_count=count,
            terminal_kind=cuda.to_device(np.full(count, reducer.TERMINAL_ESCAPED, dtype=np.int8)),
            terminal_depth=cuda.to_device(np.full(count, 4, dtype=np.int16)),
            event_status=cuda.to_device(status), event_lobes=cuda.to_device(lobes),
            event_emitted_power=cuda.to_device(emitted))
        chunk_counts = np.concatenate((np.asarray((0, count * 4, count, 0, 0), dtype=np.int64), metadata.ravel()))
        workspace = flux.ensure_workspace(cuda, None, count, records, records)
        flux.accumulate_device_ordered_emissions(cuda, workspace, events, state, cuda.to_device(chunk_counts))
        cuda.synchronize()
        for name, values in expected.items():
            np.testing.assert_array_equal(state[name].copy_to_host().view(np.uint64), values.view(np.uint64), err_msg=name)
        modes = workspace.modes.copy_to_host()
        self.assertEqual(modes[0, 0], 1)
        self.assertEqual(modes[0, 1:].sum(), 0)

    def test_uniform_fast_path_keeps_exact_fp64_order_across_exponents_and_zero(self):
        cuda = gpu._CUDA
        count = 389
        records = 3
        for powers, initial in (((0.125, 0.125), 0.1), ((0.125, 0.0625), 0.1),
                ((2.0 ** -53, 2.0 ** -53), 1.0), ((2.0 ** -1074, 2.0 ** -1072), 0.0),
                ((2.0 ** 900, 2.0 ** 900), 0.0), ((2.0 ** 1023, 2.0 ** 1023), 0.0),
                ((0.0, 0.0), -0.0)):
            with self.subTest(powers=powers, initial=initial):
                shapes = {'reflection_lobe_flux_lumen': (3, 2), 'reflection_depth_flux_lumen': (3, 2),
                    'contribution_lobe_flux_lumen': (3, 5), 'contribution_depth_flux_lumen': (3, 7)}
                expected = {name: np.full(shape, initial) for name, shape in shapes.items()}
                state = {name: cuda.to_device(values) for name, values in expected.items()}
                status = np.zeros((count, records), dtype=np.uint16, order='F')
                status[:, :2] = reducer.STATUS_EMITTED
                lobes = np.broadcast_to((np.arange(count) % 3)[:, None], (count, records)).astype(np.int8).copy(order='F')
                emitted = np.full((count, records), np.nan, order='F')
                emitted[:, :2] = powers
                metadata = np.zeros((6, 3), dtype=np.int64)
                lobe_counts = np.zeros((3, 5), dtype=np.int64)
                depth_counts = np.zeros((3, 5), dtype=np.int64)
                for primary in range(count):
                    for depth in range(2):
                        lobe = int(lobes[primary, depth])
                        value = float(emitted[primary, depth])
                        expected['reflection_lobe_flux_lumen'][lobe, 0] += value
                        expected['contribution_lobe_flux_lumen'][lobe, reducer.OUTCOME_EMITTED] += value
                        expected['reflection_depth_flux_lumen'][depth + 1, 0] += value
                        expected['contribution_depth_flux_lumen'][depth + 1, reducer.CONTRIBUTION_DEPTH_EMITTED] += value
                        lobe_counts[lobe, reducer.OUTCOME_EMITTED] += 1
                        depth_counts[depth + 1, reducer.REF_DEPTH_EMITTED] += 1
                for group in range(6):
                    values = emitted[:, :2][lobes[:, :2] == group] if group < 3 else (
                        emitted[:, group - 4] if group > 3 else np.empty(0))
                    if values.size:
                        bits = np.asarray(values).view(np.int64)
                        metadata[group] = (bits.max() + 1, int(bits.min() != bits.max()), bits.size)
                state['reflection_lobe_counts'] = cuda.to_device(lobe_counts)
                state['reflection_depth_counts'] = cuda.to_device(depth_counts)
                events = SimpleNamespace(ray_count=count,
                    terminal_kind=cuda.to_device(np.full(count, reducer.TERMINAL_ESCAPED, dtype=np.int8)),
                    terminal_depth=cuda.to_device(np.full(count, 2, dtype=np.int16)),
                    event_status=cuda.to_device(status), event_lobes=cuda.to_device(lobes),
                    event_emitted_power=cuda.to_device(emitted))
                chunk_counts = np.concatenate((np.asarray((0, count * 2, count, 0, 0), dtype=np.int64), metadata.ravel()))
                workspace = flux.ensure_workspace(cuda, None, count, records, records)
                flux.accumulate_device_ordered_emissions(cuda, workspace, events, state, cuda.to_device(chunk_counts))
                cuda.synchronize()
                for name, values in expected.items():
                    np.testing.assert_array_equal(state[name].copy_to_host().view(np.uint64), values.view(np.uint64), err_msg=name)


if __name__ == '__main__':
    unittest.main()
