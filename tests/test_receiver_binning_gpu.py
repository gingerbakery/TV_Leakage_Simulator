from __future__ import annotations

import ast
from contextlib import ExitStack
from dataclasses import replace
import inspect
import json
import math
import os
from pathlib import Path
import sys
import textwrap
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import native_cpu_counter_wavefront as planner
from leakage_simulator import native_cpu_transport as cpu_transport
from leakage_simulator import raytracer
from leakage_simulator.receiver_binning import CONTRACT, receiver_bin_index
from test_perf4e_bounce_mis import _build_reflected_case
from test_receiver_binning import bin_cases


def center_counter(key, depth, lane):
    if lane == planner.LANE_BOUNCE_MIS_SELECT:
        return 0.0
    return 0.5


def center_factory(module):
    source = textwrap.dedent(inspect.getsource(module._make_kernel))
    original = ast.parse(source)
    modified = ast.parse(source)
    targets = [node for node in ast.walk(modified) if isinstance(node, ast.FunctionDef) and node.name == "uniform"]
    if len(targets) != 1:
        raise AssertionError("Unexpected counter implementation")
    saved = targets[0].body
    replacement = ast.parse("if lane == LANE_BOUNCE_MIS_SELECT:\n    return 0.0\nreturn 0.5").body
    targets[0].body = replacement
    targets[0].body = saved
    if ast.dump(modified) != ast.dump(original):
        raise AssertionError("Diagnostic modified more than the counter")
    targets[0].body = replacement
    namespace = dict(vars(module))
    exec(compile(ast.fix_missing_locations(modified), "<receiver-boundary-counter-only>", "exec"), namespace)
    return namespace["_make_kernel"]


class ReceiverBinningGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capability = gpu.preflight_gpu_cuda(refresh=True)
        required = ("available", "strict_float64", "kernel_executed", "kernel_verified")
        if not all(getattr(capability, field) is True for field in required):
            raise RuntimeError(f"Production CUDA preflight required: {capability}")
        if capability.preflight_scope != "production_ray_bvh" or gpu.PROVIDER_CONTRACT != "strict_float64_bvh_v1":
            raise RuntimeError("Production CUDA contract mismatch")

    def test_cuda_bin_matches_known_cells_and_cpu(self):
        cuda = gpu._CUDA
        device_bin = cuda.jit(device=True, inline=True, fastmath=False)(receiver_bin_index)

        @cuda.jit
        def classify(values, output):
            index = cuda.grid(1)
            if index < len(output):
                output[index] = device_bin(values[index, 0], values[index, 1], values[index, 2], int(values[index, 3]), values[index, 4])

        cases = np.asarray(bin_cases(), dtype=np.float64)
        inputs = cuda.to_device(cases[:, :5].copy())
        output = cuda.device_array(len(cases), dtype=np.int64)
        classify[(len(cases) + 127) // 128, 128](inputs, output)
        np.testing.assert_array_equal(output.copy_to_host(), cases[:, 5].astype(np.int64))

    def test_forced_reflected_center_has_one_cell_and_preserves_flux(self):
        angle = math.radians(70.0)
        sine, cosine = math.sin(angle), math.cos(angle)
        with ExitStack() as context:
            context.enter_context(patch.object(planner, "counter_uniform", center_counter))
            context.enter_context(patch.object(resident, "_make_kernel", center_factory(resident)))
            context.enter_context(patch.object(planner, "_make_kernel", center_factory(planner)))
            context.enter_context(patch.object(planner, "_KERNEL", None))
            context.enter_context(patch.object(planner, "_KERNEL_COMPILED", False))
            context.enter_context(patch.object(cpu_transport, "_KERNEL", None))
            context.enter_context(patch.object(cpu_transport, "_COMPACT_KERNEL", None))
            for name, value in (("_KERNEL", None), ("_PAIR_KERNEL", None), ("_KERNEL_COMPILED", False), ("_PAIR_KERNEL_COMPILED", False)):
                context.enter_context(patch.object(resident, name, value))
            for cells in (8, 7):
                inputs = _build_reflected_case(16385, 1000, "receiver_mis", scatter_model="lambertian")
                inputs.config = replace(inputs.config, min_energy=0.0, max_depth=2)
                inputs.emitters = [replace(inputs.emitters[0], center=(-10.0 * math.tan(angle), 0.0, 0.0), u_axis=(cosine, 0.0, -sine), v_axis=(0.0, 1.0, 0.0))]
                inputs.receivers = [replace(inputs.receivers[0], center=(20.0 * sine, 0.0, 10.0 - 20.0 * cosine), normal=(-sine, 0.0, cosine), u_axis=(cosine, 0.0, sine), v_axis=(0.0, 1.0, 0.0), width_mm=2.0, height_mm=2.0, resolution=(cells, cells))]
                with patch.dict(os.environ, {"LEAKAGE_CPU_TRANSPORT": "wavefront"}):
                    cpu = raytracer.run_direct_ray_trace(
                        inputs, intersection_dispatch="batch", intersection_batch_size=65536,
                        intersection_provider="numba_cpu", wavefront_planner="python_cpu",
                        wavefront_pipeline="soa_event_tape", wavefront_rng="counter_rng_v2",
                        wavefront_reducer="numba_cpu", wavefront_reducer_commit="run_accumulator",
                    )
                with patch.dict(os.environ, {"LEAKAGE_CPU_TRANSPORT": "fused"}):
                    fused = raytracer.run_direct_ray_trace(inputs)
                fused_performance = fused.metrics["_performance_summary"]
                self.assertGreater(fused_performance.get("wavefront_cpu_transport_chunk_count", 0), 0)
                self.assertEqual(fused_performance.get("wavefront_cpu_transport_fallback_count", 0), 0)
                inputs.config = replace(inputs.config, compute_backend="gpu_cuda")
                accelerated = raytracer.run_direct_ray_trace(inputs, wavefront_residency="gpu_resident")
                performance = accelerated.metrics["_performance_summary"]
                self.assertEqual(performance["compute_execution_state"], "gpu_active")
                self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0)
                self.assertEqual(performance["gpu_cuda_gpu_success_count"], performance["gpu_cuda_gpu_attempt_count"])
                self.assertEqual(performance["gpu_cuda_hybrid_cpu_success_count"], 0)
                self.assertEqual(performance["intersection_fallback_count"], 0)
                self.assertEqual(performance["gpu_resident_wavefront_fallback_count"], 0)
                self.assertEqual(performance["receiver_bin_assignment_contract"], CONTRACT)
                cpu_grid = cpu.receiver_grids[0]
                gpu_grid = accelerated.receiver_grids[0]
                self.assertEqual(cpu_grid.hit_count, gpu_grid.hit_count)
                self.assertGreater(cpu_grid.hit_count, 0)
                for grid in (cpu_grid, fused.receiver_grids[0], gpu_grid):
                    flux = np.asarray(grid.flux_lumen)
                    np.testing.assert_array_equal(np.argwhere(flux != 0.0), [[cells // 2, cells // 2]])
                    self.assertAlmostEqual(float(flux.sum()), 0.0017502570909109678, delta=2e-15)
                np.testing.assert_allclose(cpu_grid.flux_lumen, gpu_grid.flux_lumen, rtol=1e-12, atol=0.0)
                np.testing.assert_array_equal(cpu_grid.flux_lumen, fused.receiver_grids[0].flux_lumen)
                print(json.dumps({"case": "forced_center", "cells": cells, "cpu_flux": float(np.sum(cpu_grid.flux_lumen)), "gpu_flux": float(np.sum(gpu_grid.flux_lumen)), "hit_count": cpu_grid.hit_count, "performance": performance}, allow_nan=False), flush=True)


if __name__ == "__main__":
    unittest.main()
