from __future__ import annotations

from dataclasses import fields
import gc
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from perf5_workspace_packing import MODES, PackedWorkspaceCuda, view_strides, workspace_factory
import benchmark_perf5_gpu_workspace_packing as benchmark
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident


class HostDeviceArray(np.ndarray):
    def copy_to_device(self, values):
        np.copyto(self, values, casting='no')


class HostCuda:
    def __init__(self):
        self.allocations = []
        self.uploads = []
        self.fail_allocate = False

    def device_array(self, shape, dtype=np.float64, order='C'):
        if self.fail_allocate:
            raise MemoryError('Injected allocation failure')
        result = np.empty(shape, dtype=dtype, order=order).view(HostDeviceArray)
        self.allocations.append(result)
        return result

    def to_device(self, values):
        result = np.array(values, copy=True, order='C').view(HostDeviceArray)
        self.uploads.append(result)
        return result

    def synchronize(self):
        return None


def context_for_workspace(stack_width=4):
    return SimpleNamespace(scene=SimpleNamespace(stack_width=stack_width))


class WorkspacePackingCpuTests(unittest.TestCase):
    def setUp(self):
        self.cuda = HostCuda()
        self.capability = SimpleNamespace(device_id=0)
        self.context = context_for_workspace()
        self.functions = {mode: workspace_factory(mode) for mode in MODES}
        self.enterContext(patch.object(gpu, '_CUDA', self.cuda))
        self.enterContext(patch.object(resident, '_WORKSPACES', threading.local()))

    def ensure(self, mode='packed', *, context=None, rays=9, depths=3, geometry=2,
               contract=None, logical=11, capability=None):
        return self.functions[mode](context or self.context, capability or self.capability,
            rays, depths, geometry, contract or resident.COMPACT_WORKSPACE_CONTRACT,
            logical_depth_count=logical)

    def test_packing_matches_all_shapes_dtypes_strides_and_accounted_bytes(self):
        for contract in (resident.COMPACT_WORKSPACE_CONTRACT, resident.FULL_WORKSPACE_CONTRACT):
            with self.subTest(contract=contract), patch.object(resident, '_WORKSPACES', threading.local()):
                before = len(self.cuda.allocations)
                original, reused, _ = self.ensure('current', contract=contract)
                self.assertFalse(reused)
                self.assertEqual(len(self.cuda.allocations) - before, 27)
                before = len(self.cuda.allocations)
                packed, reused, _ = self.ensure(context=context_for_workspace(), contract=contract)
                self.assertFalse(reused)
                self.assertEqual(len(self.cuda.allocations) - before, 1)
                for field in fields(original):
                    expected, actual = getattr(original, field.name), getattr(packed, field.name)
                    if isinstance(expected, np.ndarray):
                        self.assertEqual((actual.shape, actual.dtype, actual.strides),
                            (expected.shape, expected.dtype, expected.strides), field.name)
                self.assertEqual(resident._workspace_bytes(original), resident._workspace_bytes(packed))

    def test_workspace_fields_are_disjoint_aligned_views_and_survive_factory_collection(self):
        workspace, _, _ = self.ensure()
        root = self.cuda.allocations[0]
        intervals = []
        selected = []
        for field in fields(workspace):
            values = getattr(workspace, field.name)
            if not isinstance(values, np.ndarray) or field.name in ('input_transfer', 'host_input_transfer'):
                continue
            self.assertTrue(np.shares_memory(root, values), field.name)
            start = values.ctypes.data - root.ctypes.data
            self.assertEqual(start % values.dtype.alignment, 0, field.name)
            intervals.append((start, start + values.nbytes, field.name))
            selected.append((values, len(selected) + 1))
        intervals.sort()
        self.assertTrue(all(first[1] <= second[0] for first, second in zip(intervals, intervals[1:])))
        self.assertLessEqual(intervals[-1][1], root.nbytes)
        for values, marker in selected:
            values.fill(marker)
        self.cuda.allocations.clear()
        gc.collect()
        for values, marker in selected:
            self.assertTrue(np.all(values == marker))

    def test_same_context_reuses_without_another_allocation(self):
        for mode in MODES:
            with self.subTest(mode=mode), patch.object(resident, '_WORKSPACES', threading.local()):
                first, _, _ = self.ensure(mode)
                before = len(self.cuda.allocations)
                second, reused, duration = self.ensure(mode, rays=3, geometry=1, logical=5)
                self.assertTrue(reused)
                self.assertIs(first, second)
                self.assertEqual(duration, 0.0)
                self.assertEqual(len(self.cuda.allocations), before)

    def test_reused_workspaces_do_not_rebuild_packed_metadata(self):
        for mode in ('packed', 'packed_reuse'):
            with self.subTest(mode=mode), patch.object(resident, '_WORKSPACES', threading.local()):
                factory = self.functions[mode]
                original = factory.__kwdefaults__['_packed_workspace_factory']
                calls = []
                def create(*values):
                    calls.append(values)
                    return original(*values)
                factory.__kwdefaults__['_packed_workspace_factory'] = create
                self.ensure(mode)
                self.ensure(mode)
                self.assertEqual(len(calls), 1)

    def test_context_retag_is_separate_from_packing_and_never_reuses_bindings(self):
        for mode in MODES:
            with self.subTest(mode=mode), patch.object(resident, '_WORKSPACES', threading.local()):
                first, _, _ = self.ensure(mode)
                next_context = context_for_workspace()
                before = len(self.cuda.allocations)
                second, reused, _ = self.ensure(mode, context=next_context)
                should_reuse = mode in ('reuse_context', 'packed_reuse')
                self.assertEqual(reused, should_reuse)
                self.assertEqual(second.context_identity, id(next_context))
                self.assertEqual(first is second, should_reuse)
                expected = 0 if should_reuse else 1 if mode == 'packed' else 27
                self.assertEqual(len(self.cuda.allocations) - before, expected)

    def test_every_capacity_growth_and_device_change_requires_new_storage(self):
        requests = ({'rays': 33}, {'depths': 5}, {'geometry': 8}, {'logical': 21},
            {'context': context_for_workspace(stack_width=9)},
            {'capability': SimpleNamespace(device_id=1)})
        for request in requests:
            for mode in ('packed', 'packed_reuse'):
                with self.subTest(request=request, mode=mode), patch.object(resident, '_WORKSPACES', threading.local()):
                    first, _, _ = self.ensure(mode)
                    before = len(self.cuda.allocations)
                    second, reused, _ = self.ensure(mode, **request)
                    self.assertFalse(reused)
                    self.assertIsNot(first, second)
                    self.assertEqual(len(self.cuda.allocations) - before, 1)

    def test_full_and_compact_contracts_keep_independent_storage(self):
        first, _, _ = self.ensure('packed_reuse')
        second, reused, _ = self.ensure('packed_reuse', contract=resident.FULL_WORKSPACE_CONTRACT)
        self.assertFalse(reused)
        self.assertIsNot(first, second)
        self.assertEqual(len(self.cuda.allocations), 2)
        self.assertEqual(first.event_faces.strides[0], 4)
        self.assertEqual(second.event_faces.strides[-1], 4)

    def test_actual_api_style_new_threads_still_allocate_one_root_per_job(self):
        records = []
        for _ in range(2):
            def worker():
                workspace, reused, _ = self.ensure('packed_reuse', context=context_for_workspace())
                records.append((workspace, reused))
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        self.assertEqual(len(records), 2)
        self.assertTrue(all(not reused for _, reused in records))
        self.assertEqual(len(self.cuda.allocations), 2)
        self.assertIsNot(records[0][0], records[1][0])

    def test_input_upload_replaces_active_values_and_resets_all_logical_counters(self):
        workspace, _, _ = self.ensure()
        self.cuda.allocations[0].fill(255)
        workspace.host_input_transfer.fill(255)
        count = 5
        batch = SimpleNamespace(origins=np.arange(count * 3, dtype=np.float64).reshape(count, 3),
            directions=np.ones((count, 3), dtype=np.float64), initial_power_lumen=np.full(count, 0.125),
            source_faces=np.arange(2 ** 60, 2 ** 60 + count, dtype=np.int64),
            reflection_seeds=np.arange(2 ** 63, 2 ** 63 + count, dtype=np.uint64))
        snapshots = {name: values.tobytes() for name, values in vars(batch).items()}
        resident._upload_resident_inputs(workspace, batch, count, 3)
        for name, input_name in (('origins', 'origins'), ('directions', 'directions'),
            ('powers', 'initial_power_lumen'), ('source_faces', 'source_faces'),
            ('reflection_seeds', 'reflection_seeds')):
            self.assertEqual(getattr(workspace, name)[:count].tobytes(), getattr(batch, input_name).tobytes())
            self.assertEqual(getattr(batch, input_name).tobytes(), snapshots[input_name])
        np.testing.assert_array_equal(workspace.active_by_depth, np.zeros(11, dtype=np.int64))
        np.testing.assert_array_equal(workspace.bounce_importance_counts, np.zeros(7, dtype=np.int64))
        np.testing.assert_array_equal(workspace.bounce_importance_weight_stats, (0.0, 0.0, np.inf, 0.0))
        self.assertTrue(np.all(workspace.event_faces == -1))

    def test_failed_growth_preserves_previous_cache_and_reuse(self):
        first, _, _ = self.ensure('packed_reuse')
        self.cuda.fail_allocate = True
        with self.assertRaises(resident.GpuResidentWavefrontProviderError):
            self.ensure('packed_reuse', rays=33)
        self.cuda.fail_allocate = False
        second, reused, _ = self.ensure('packed_reuse', context=context_for_workspace())
        self.assertTrue(reused)
        self.assertIs(first, second)

    def test_invalid_depth_and_event_budget_fail_before_allocating(self):
        for request in ({'depths': 0}, {'depths': 12, 'logical': 11},
            {'rays': resident.MAX_BATCH_EVENT_SLOTS + 1}):
            with self.subTest(request=request), self.assertRaises((ValueError, resident.GpuResidentWavefrontProviderError)):
                self.ensure(**request)
        self.assertEqual(len(self.cuda.allocations), 0)

    def test_allocation_sequence_mismatch_fails_closed(self):
        wrapper = PackedWorkspaceCuda(self.cuda, 9, 3, 2, resident.COMPACT_WORKSPACE_CONTRACT, 11, 4)
        with self.assertRaises(ValueError):
            wrapper.device_array(1, dtype=np.int64)
        self.assertEqual(len(self.cuda.allocations), 0)

    def test_c_and_fortran_metadata_match_numpy(self):
        for shape in ((16,), (16, 3), (2, 3, 3)):
            for dtype in (np.dtype(np.float64), np.dtype(np.int32), np.dtype(np.uint8)):
                for order in ('C', 'F'):
                    self.assertEqual(view_strides(shape, dtype, order), np.empty(shape, dtype=dtype, order=order).strides)
        with self.assertRaises(ValueError):
            view_strides((0, 3), np.dtype(np.float64), 'C')
        with self.assertRaises(ValueError):
            view_strides((3,), np.dtype(np.float64), 'invalid')

    def test_partial_batches_thousand_depth_and_zero_geometry_preserve_layouts(self):
        for rays, depths, geometry, logical, stack_width in (
            (0, 1, 0, 1, 1), (1, 1, 1, 1, 2),
            (17, 32, 3, 1001, 9), (65, 7, 0, 1001, 65),
        ):
            for contract in (resident.COMPACT_WORKSPACE_CONTRACT, resident.FULL_WORKSPACE_CONTRACT):
                with self.subTest(rays=rays, depths=depths, logical=logical, contract=contract):
                    with patch.object(resident, '_WORKSPACES', threading.local()):
                        expected, _, _ = self.ensure('current', context=context_for_workspace(stack_width),
                            rays=rays, depths=depths, geometry=geometry, logical=logical, contract=contract)
                    with patch.object(resident, '_WORKSPACES', threading.local()):
                        actual, _, _ = self.ensure('packed', context=context_for_workspace(stack_width),
                            rays=rays, depths=depths, geometry=geometry, logical=logical, contract=contract)
                    for field in fields(expected):
                        values, candidate = getattr(expected, field.name), getattr(actual, field.name)
                        if isinstance(values, np.ndarray):
                            self.assertEqual((values.shape, values.dtype, values.strides),
                                (candidate.shape, candidate.dtype, candidate.strides), field.name)

    def test_device_metadata_constructor_receives_exact_disjoint_pointer_ranges(self):
        intervals = []
        class MemoryRoot:
            def view(self, start, end):
                intervals.append((start, end))
                return SimpleNamespace(owner=self, start=start, end=end)
        root = SimpleNamespace(gpu_data=MemoryRoot(), stream=13)
        allocations = []
        def allocate(shape, dtype):
            allocations.append((shape, dtype))
            return root
        def metadata(shape, strides, dtype, stream, gpu_data):
            return SimpleNamespace(shape=shape, strides=strides, dtype=np.dtype(dtype),
                stream=stream, gpu_data=gpu_data)
        wrapper = PackedWorkspaceCuda(SimpleNamespace(device_array=allocate), 17, 3, 2,
            resident.COMPACT_WORKSPACE_CONTRACT, 1001, 9)
        with patch.dict(sys.modules, {'numba.cuda.cudadrv.devicearray': SimpleNamespace(DeviceNDArray=metadata)}):
            for shape, dtype, order in wrapper.specs:
                result = wrapper.device_array(shape, dtype, order)
                self.assertEqual((result.shape, result.dtype, result.strides, result.stream),
                    (shape, dtype, np.empty(shape, dtype=dtype, order=order).strides, 13))
                self.assertIs(result.gpu_data.owner, root.gpu_data)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(len(intervals), 27)
        self.assertTrue(all(start % 256 == 0 and start < end <= wrapper.byte_count for start, end in intervals))
        self.assertTrue(all(first[1] <= second[0] for first, second in zip(intervals, intervals[1:])))

    def test_edited_optical_and_receiver_bindings_stay_context_local(self):
        def bindings_context(marker):
            context = context_for_workspace()
            context._device_lock = threading.Lock()
            context._device_bindings = None
            for name in resident._DeviceBindings.__dataclass_fields__:
                if name in ('context_identity', 'device_id', 'upload_sec', 'packed_transfer'):
                    continue
                shape = (2, 3) if name in ('triangle_normals', 'receiver_centers', 'receiver_normals',
                    'receiver_u_axes', 'receiver_v_axes') else (2,)
                dtype = np.int8 if name == 'face_scatter' else np.int32 if name in ('receiver_columns', 'receiver_rows') else np.float64
                setattr(context, name, np.full(shape, marker, dtype=dtype))
            return context
        first_context, second_context = bindings_context(1), bindings_context(2)
        with patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER': 'packed'}):
            first_workspace, _, _ = self.ensure('packed_reuse', context=first_context)
            first_bindings, _ = resident._ensure_device_bindings(first_context, self.capability)
            second_workspace, reused, _ = self.ensure('packed_reuse', context=second_context)
            second_bindings, binding_reused = resident._ensure_device_bindings(second_context, self.capability)
        self.assertTrue(reused)
        self.assertIs(first_workspace, second_workspace)
        self.assertFalse(binding_reused)
        self.assertEqual(len(self.cuda.uploads), 2)
        self.assertIsNot(first_bindings, second_bindings)
        for name in ('face_reflectance', 'receiver_centers', 'receiver_rows'):
            self.assertTrue(np.all(getattr(first_bindings, name) == 1))
            self.assertTrue(np.all(getattr(second_bindings, name) == 2))

    def test_benchmark_thread_modes_call_the_real_runner_once_and_propagate_failures(self):
        origin_thread = threading.get_ident()
        for mode in ('same', 'fresh'):
            calls = []
            expected = object()
            trace_input = object()
            def run(selected):
                calls.append((selected, threading.get_ident()))
                return expected
            with patch.object(benchmark.raytracer, 'run_direct_ray_trace', new=run):
                self.assertIs(benchmark.execute(trace_input, mode), expected)
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0][0], trace_input)
            self.assertEqual(calls[0][1] == origin_thread, mode == 'same')
            with patch.object(benchmark.raytracer, 'run_direct_ray_trace', side_effect=RuntimeError('native failure')):
                with self.assertRaisesRegex(RuntimeError, 'native failure'):
                    benchmark.execute(trace_input, mode)


if __name__ == '__main__':
    unittest.main()
