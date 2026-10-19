from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS
from perf5_workspace_packing import workspace_factory
import test_perf5_gpu_pair_production as production_tests
from leakage_simulator import gpu_cuda_resident_wavefront as resident


class PackedWorkspaceGpuTests(production_tests.ProductionPairGpuTests):
    workspace_mode = 'packed'

    def setUp(self):
        self.original_ensure = resident._ensure_workspace
        self.candidate_ensure = workspace_factory(self.workspace_mode)
        self.allocations = []
        self.enterContext(patch.object(resident, '_WORKSPACES', threading.local()))

    def run_mode(self, trace_input, mode, **keywords):
        def ensure(context, *values, **options):
            workspace, reused, duration = self.candidate_ensure(context, *values, **options)
            self.allocations.append((context, workspace, reused))
            return workspace, reused, duration
        with ExitStack() as stack:
            if mode == 'off':
                stack.enter_context(patch.object(resident, '_ensure_workspace', new=self.original_ensure))
                stack.enter_context(patch.object(resident, '_WORKSPACES', threading.local()))
            else:
                stack.enter_context(patch.object(resident, '_ensure_workspace', new=ensure))
            return super().run_mode(trace_input, mode, **keywords)

    def test_poisoned_previous_run_cannot_change_edited_receiver_or_material(self):
        trace_input = production_tests.add_distant_faces(WORKLOADS['stochastic_two_bounce'](8193))
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        first = self.compare(trace_input, accumulator='auto')
        first_receivers = first.to_dict()['receivers']
        first_context, first_workspace, _ = self.allocations[-1]
        for name, marker in (('event_faces', -1), ('event_status', 65535),
            ('event_incoming_power', np.nan), ('event_reflected_power', np.nan),
            ('event_emitted_power', np.nan), ('terminal_kind', 127),
            ('terminal_receiver', -7), ('terminal_received_power', np.nan),
            ('active_by_depth', -1), ('bounce_importance_counts', -1),
            ('bounce_importance_weight_stats', np.nan)):
            values = getattr(first_workspace, name)
            order = 'F' if values.ndim > 1 and values.strides[0] == values.dtype.itemsize else 'C'
            values.copy_to_device(np.full(values.shape, marker, dtype=values.dtype, order=order))
        trace_input.optical_profiles[0].reflectance = 0.14
        receiver = trace_input.receivers[0]
        receiver.center = tuple(value + (0.125 if index == 0 else 0.0)
            for index, value in enumerate(receiver.center))
        receiver.width_mm *= 0.8
        receiver.resolution = (19, 11)
        second = self.compare(trace_input, accumulator='auto')
        next_context, next_workspace, reused = self.allocations[-1]
        self.assertIsNot(first_context, next_context)
        expected_reuse = self.workspace_mode in ('reuse_context', 'packed_reuse')
        self.assertEqual(first_workspace is next_workspace, expected_reuse)
        self.assertEqual(reused, expected_reuse)
        self.assertNotEqual(first_receivers, second.to_dict()['receivers'])


class ReusedWorkspaceGpuTests(PackedWorkspaceGpuTests):
    workspace_mode = 'reuse_context'


class PackedReusedWorkspaceGpuTests(PackedWorkspaceGpuTests):
    workspace_mode = 'packed_reuse'


if __name__ == '__main__':
    unittest.main()
