from __future__ import annotations

from dataclasses import fields
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf5_gpu_event_window import cavity_case
from test_perf5_guarded_bvh import gap_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator import gpu_cuda_transfer as transfer
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator import raytracer


class ArrayCuda:
    def __init__(self):
        self.uploads = []
        self.sync_count = 0

    def to_device(self, values):
        result = np.array(values, copy=True, order='C')
        self.uploads.append(result)
        return result

    def synchronize(self):
        self.sync_count += 1


def binding_context(face_count=9, receiver_count=3):
    values = {'_device_lock': threading.Lock(), '_device_bindings': None}
    for name in resident._DeviceBindings.__dataclass_fields__:
        if name in ('context_identity', 'device_id', 'upload_sec', 'packed_transfer'):
            continue
        count = receiver_count if name.startswith('receiver_') else face_count
        dtype = np.int8 if name == 'face_scatter' else np.int64 if name in (
            'receiver_columns', 'receiver_rows') else np.float64
        shape = (count, 3) if name in ('triangle_normals', 'receiver_centers',
            'receiver_normals', 'receiver_u_axes', 'receiver_v_axes') else (count,)
        values[name] = np.arange(int(np.prod(shape)), dtype=dtype).reshape(shape)
        values[name].setflags(write=False)
    return SimpleNamespace(**values)


class StaticTransferTests(unittest.TestCase):
    def test_mixed_dtypes_preserve_bits_shapes_and_alignment_with_one_copy(self):
        fake = ArrayCuda()
        floats = np.asarray([0, 1 << 63, 0x7FF8000000000042], dtype=np.uint64).view(np.float64)
        arrays = {'flags': np.asarray([1, 0, 1], dtype=np.int8), 'floats': floats,
            'wide_ids': np.asarray([2 ** 60 + 3, -2 ** 60 - 5], dtype=np.int64),
            'empty': np.empty((0, 3), dtype=np.float64),
            'strided': np.arange(24, dtype=np.float64).reshape(4, 6)[::2]}
        snapshots = {name: values.tobytes() for name, values in arrays.items()}
        root, views = transfer.upload_aligned_arrays(fake, arrays)
        self.assertEqual(len(fake.uploads), 1)
        ranges = []
        for name, values in arrays.items():
            result = views[name]
            self.assertEqual(result.dtype, values.dtype)
            self.assertEqual(result.shape, values.shape)
            self.assertEqual(result.tobytes(), snapshots[name])
            self.assertEqual(values.tobytes(), snapshots[name])
            self.assertEqual(result.ctypes.data % values.dtype.alignment, 0)
            self.assertEqual((result.ctypes.data - root.ctypes.data) % 256, 0)
            if values.nbytes:
                self.assertTrue(np.shares_memory(root, result))
                ranges.append((result.ctypes.data, result.ctypes.data + result.nbytes))
        ranges.sort()
        self.assertTrue(all(first[1] <= second[0] for first, second in zip(ranges, ranges[1:])))

    def test_object_arrays_fail_before_any_device_upload(self):
        fake = ArrayCuda()
        with self.assertRaises(ValueError):
            transfer.upload_aligned_arrays(fake, {'invalid': np.asarray([object()], dtype=object)})
        self.assertEqual(fake.uploads, [])

    def test_bindings_use_one_upload_and_context_reuse_uses_none(self):
        context = binding_context()
        fake = ArrayCuda()
        with patch.object(gpu, '_CUDA', fake), patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'packed'}):
            bindings, reused = resident._ensure_device_bindings(context, SimpleNamespace(device_id=0))
            self.assertFalse(reused)
            self.assertEqual(len(fake.uploads), 1)
            again, reused = resident._ensure_device_bindings(context, SimpleNamespace(device_id=0))
            self.assertTrue(reused)
            self.assertIs(again, bindings)
            self.assertEqual(len(fake.uploads), 1)
        for name in vars(context):
            if not name.startswith('_'):
                self.assertEqual(getattr(bindings, name).tobytes(), getattr(context, name).tobytes())

    def test_legacy_and_packed_bindings_are_exact_and_reedited_context_is_fresh(self):
        results = []
        for mode in ('legacy', 'packed'):
            context = binding_context(receiver_count=0)
            fake = ArrayCuda()
            with patch.object(gpu, '_CUDA', fake), patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': mode}):
                bindings, _ = resident._ensure_device_bindings(context, SimpleNamespace(device_id=0))
            results.append(bindings)
            self.assertEqual(len(fake.uploads), 17 if mode == 'legacy' else 1)
        for name in vars(context):
            if not name.startswith('_'):
                self.assertEqual(getattr(results[0], name).tobytes(), getattr(results[1], name).tobytes())
        first = binding_context()
        second = binding_context()
        second.face_reflectance = np.full(9, 0.99, dtype=np.float64)
        fake = ArrayCuda()
        with patch.object(gpu, '_CUDA', fake), patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'packed'}):
            before, _ = resident._ensure_device_bindings(first, SimpleNamespace(device_id=0))
            after, _ = resident._ensure_device_bindings(second, SimpleNamespace(device_id=0))
        self.assertEqual(len(fake.uploads), 2)
        self.assertEqual(before.face_reflectance.tobytes(), first.face_reflectance.tobytes())
        self.assertEqual(after.face_reflectance.tobytes(), second.face_reflectance.tobytes())

    def test_summary_static_buffer_is_separate_and_does_not_grow_output_transfer(self):
        batch, state = reducer._empty_compile_inputs()
        request = summary.GpuSummaryAccumulatorRequest(state, batch.face_profile_slots,
            batch.profile_unassigned, batch.receiver_columns, batch.grid_offsets, batch.max_depth)
        snapshots = []
        for mode in ('legacy', 'packed'):
            fake = ArrayCuda()
            session = summary.GpuSummaryAccumulatorSession()
            with patch.object(gpu, '_CUDA', fake), patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': mode}):
                reused, _, count = summary._initialize_session(session, request)
                self.assertFalse(reused)
                self.assertEqual(len(fake.uploads), 5 if mode == 'legacy' else 2)
                self.assertEqual(count, sum(values.nbytes for values in fake.uploads))
                reused, _, count = summary._initialize_session(session, request)
                self.assertTrue(reused)
                self.assertEqual(count, 0)
            snapshots.append((session.host_transfer.tobytes(), session.transient_offset, session.transfer_layout))
            self.assertFalse(any(name.startswith('device_') for name in session.transfer_layout))
            if mode == 'packed':
                self.assertFalse(np.shares_memory(session.device_transfer, session.static_transfer))
                for name in ('device_face_profile_slots', 'device_profile_unassigned',
                    'device_receiver_columns', 'device_grid_offsets'):
                    expected = getattr(request, name.removeprefix('device_'))
                    self.assertEqual(getattr(session, name).tobytes(), expected.tobytes())
        self.assertEqual(snapshots[0], snapshots[1])

    def test_invalid_mode_and_upload_failures_keep_the_original_error_contract(self):
        fake = ArrayCuda()
        with patch.object(gpu, '_CUDA', fake), patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'invalid'}):
            with self.assertRaisesRegex(resident.GpuResidentWavefrontProviderError, 'static_transfer_mode_invalid'):
                resident._ensure_device_bindings(binding_context(), SimpleNamespace(device_id=0))
            batch, state = reducer._empty_compile_inputs()
            request = summary.GpuSummaryAccumulatorRequest(state, batch.face_profile_slots,
                batch.profile_unassigned, batch.receiver_columns, batch.grid_offsets, batch.max_depth)
            with self.assertRaisesRegex(summary.GpuSummaryAccumulatorError, 'static_transfer_mode_invalid'):
                summary._initialize_session(summary.GpuSummaryAccumulatorSession(), request)
        context = binding_context()
        with patch.object(gpu, '_CUDA', fake), patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'packed'}), patch.object(
            fake, 'to_device', side_effect=RuntimeError('injected allocation failure')
        ):
            with self.assertRaisesRegex(resident.GpuResidentWavefrontProviderError, 'bindings_upload_failed'):
                resident._ensure_device_bindings(context, SimpleNamespace(device_id=0))
        self.assertIsNone(context._device_bindings)


class StaticTransferGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')):
            raise unittest.SkipTest(preflight.reason_code or 'production CUDA unavailable')
        if preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
            raise RuntimeError('Production CUDA contract mismatch')

    def assert_parity(self, expected, actual, *, exact=False):
        report = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
            absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(expected.stored_paths, actual.stored_paths)
        if exact:
            self.assertTrue(report.semantic_exact, report.to_dict())
        performance = actual.metrics['_performance_summary']
        self.assertEqual(performance['compute_execution_state'], 'gpu_active')
        self.assertGreater(performance['gpu_cuda_gpu_success_count'], 0)
        self.assertEqual(performance['gpu_resident_wavefront_fallback_count'], 0)

    def test_real_device_views_and_summary_preserve_odd_face_counts_and_reediting(self):
        trace_input = gap_case(8193)
        original_bindings = resident._ensure_device_bindings
        original_accumulate = resident.accumulate_resident_summary_gpu_cuda
        checked = []
        def bindings(context, capability):
            result, reused = original_bindings(context, capability)
            if result.packed_transfer is not None:
                for field in fields(result):
                    if field.name in ('context_identity', 'device_id', 'upload_sec', 'packed_transfer'):
                        continue
                    view = getattr(result, field.name)
                    expected = getattr(context, field.name)
                    self.assertEqual(view.dtype, expected.dtype)
                    self.assertEqual(view.copy_to_host().tobytes(), expected.tobytes())
                    self.assertEqual(view.device_ctypes_pointer.value % expected.dtype.alignment, 0)
                    self.assertEqual(view.device_ctypes_pointer.value % 256, 0)
                checked.append('bindings')
            return result, reused
        def accumulate(events, request, session):
            result = original_accumulate(events, request, session)
            if session.static_transfer is not None:
                for name in ('face_profile_slots', 'profile_unassigned', 'receiver_columns', 'grid_offsets'):
                    self.assertEqual(getattr(session, 'device_' + name).copy_to_host().tobytes(),
                        getattr(request, name).tobytes())
                checked.append('summary')
            return result
        for reflectance in (0.14, 0.99):
            trace_input.optical_profiles[0].reflectance = reflectance
            with patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'legacy'}):
                expected = raytracer.run_direct_ray_trace(trace_input)
            with patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'packed'}), patch.object(
                resident, '_ensure_device_bindings', side_effect=bindings), patch.object(
                resident, 'accumulate_resident_summary_gpu_cuda', side_effect=accumulate
            ):
                actual = raytracer.run_direct_ray_trace(trace_input)
            self.assert_parity(expected, actual)
        self.assertIn('bindings', checked)
        self.assertIn('summary', checked)

    def test_empty_receivers_and_partial_chunks_keep_ordered_results_exact(self):
        inputs = (cavity_case(17, depth=5), WORKLOADS['stochastic_two_bounce'](8193))
        for trace_input in inputs:
            trace_input.config.compute_backend = 'gpu_cuda'
            results = []
            for mode in ('legacy', 'packed'):
                with patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': mode}):
                    results.append(raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host',
                        intersection_batch_size=4096))
            self.assert_parity(results[0], results[1], exact=True)


if __name__ == '__main__':
    unittest.main()
