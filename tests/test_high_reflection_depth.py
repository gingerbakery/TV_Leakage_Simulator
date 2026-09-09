from __future__ import annotations

import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator import gpu_cuda_resident_wavefront as resident_provider
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.gpu_cuda_resident_wavefront import (
    GpuResidentWavefrontBatch,
    MAX_SUPPORTED_DEPTH,
)
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    MAX_REFLECTION_DEPTH,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)
from leakage_simulator.wavefront_event_tape import (
    MAX_BATCH_EVENT_SLOTS,
    bounded_wavefront_batch_size,
)


def high_reflection_corridor(
    reflection_count: int,
    max_depth: int,
    *,
    ray_count: int = 64,
    reflectance: float = 0.999,
    backend: str = "cpu",
    store_paths: bool = False,
) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    corridor_length = 2.0 * reflection_count
    for component_id, wall_y in enumerate((-1.0, 1.0), start=1):
        vertices = [
            mesh.add_vertex(point)
            for point in (
                (-1.0, wall_y, -2.0),
                (corridor_length + 1.0, wall_y, -2.0),
                (corridor_length + 1.0, wall_y, 2.0),
                (-1.0, wall_y, 2.0),
            )
        ]
        for corners in ((0, 1, 2), (0, 2, 3)):
            mesh.add_face(
                *(vertices[index] for index in corners),
                "mirror",
                {"component_id": component_id},
            )
    inverse_root_two = 1.0 / math.sqrt(2.0)
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="source",
                emitter_type="datum_plane",
                center=(0.0, 0.0, 0.0),
                u_axis=(0.0, 0.0, 1.0),
                v_axis=(inverse_root_two, -inverse_root_two, 0.0),
                width_mm=0.001,
                height_mm=0.001,
                direction_distribution="gaussian",
                gaussian_sigma_deg=0.0001,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=20260908,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="observer",
                center=(corridor_length, 0.0, 0.0),
                normal=(-1.0, 0.0, 0.0),
                width_mm=1.5,
                height_mm=1.5,
                resolution=(8, 8),
            )
        ],
        optical_profiles=[
            OpticalProfile("mirror", reflectance, scatter_model="specular")
        ],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=max_depth,
            min_energy=1e-15,
            epsilon_mm=1e-6,
            angle_dependent_reflectance=False,
            compute_backend=backend,
            intersection_backend="bvh",
            store_ray_paths=store_paths,
            max_stored_paths=2,
        ),
    )


class HighReflectionDepthTests(unittest.TestCase):
    def test_cpu_and_gpu_share_limit_and_config_round_trips(self) -> None:
        self.assertEqual(MAX_REFLECTION_DEPTH, 1000)
        self.assertEqual(MAX_SUPPORTED_DEPTH, MAX_REFLECTION_DEPTH)
        for depth in (0, 20, 100, 300, 1000):
            with self.subTest(depth=depth):
                config = RayTraceConfig(max_depth=depth)
                self.assertEqual(RayTraceConfig(**config.to_dict()).max_depth, depth)
                batch = GpuResidentWavefrontBatch(
                    origins=[(0.0, 0.0, 0.0)],
                    directions=[(1.0, 0.0, 0.0)],
                    initial_power_lumen=[1.0],
                    source_faces=[-1],
                    reflection_seeds=[42],
                    max_depth=depth,
                    epsilon_mm=1e-6,
                    min_energy=1e-15,
                    termination_mode=0,
                )
                self.assertEqual(batch.max_depth, depth)
        for depth in (-1, 1001):
            with self.subTest(depth=depth), self.assertRaises(ValueError):
                RayTraceConfig(max_depth=depth)

    def test_batch_event_budget_includes_gpu_capacity_rounding(self) -> None:
        for depth in range(MAX_REFLECTION_DEPTH + 1):
            for requested in (1, 1024, 65536, 1_000_000):
                effective = bounded_wavefront_batch_size(requested, depth)
                rounded = 1 << (effective - 1).bit_length()
                self.assertLessEqual(rounded * (depth + 1), MAX_BATCH_EVENT_SLOTS)
                self.assertLessEqual(effective, requested)
        self.assertEqual(bounded_wavefront_batch_size(65536, 20), 65536)
        self.assertEqual(bounded_wavefront_batch_size(65536, 1000), 2048)

    def test_oversized_gpu_workspace_is_rejected_before_allocation(self) -> None:
        cuda = Mock()
        context = SimpleNamespace(scene=SimpleNamespace(stack_width=32))
        capability = SimpleNamespace(device_id=0)
        with (
            patch.object(gpu_cuda, "_CUDA", cuda),
            patch.object(resident_provider._WORKSPACES, "values", {}, create=True),
            self.assertRaisesRegex(
                resident_provider.GpuResidentWavefrontProviderError,
                "gpu_resident_event_budget_exceeded",
            ),
        ):
            resident_provider._ensure_workspace(
                context, capability, 65536, 1001, 1,
                resident_provider.COMPACT_WORKSPACE_CONTRACT,
            )
        cuda.device_array.assert_not_called()

    def test_hundred_and_thousand_reflections_match_analytic_flux(self) -> None:
        for depth in (100, 1000):
            with self.subTest(depth=depth):
                result = run_direct_ray_trace(high_reflection_corridor(depth, depth))
                self.assertEqual(result.receiver_hit_count, 64)
                self.assertEqual(result.surface_hit_count, 64 * depth)
                self.assertAlmostEqual(
                    result.metrics["observer"]["total_flux_lumen"],
                    0.999**depth,
                    places=11,
                )
                self.assertEqual(
                    result.metrics["_reflection_summary"]["max_observed_depth"],
                    depth,
                )

    def test_depth_limit_and_energy_limit_remain_independent(self) -> None:
        for depth_limit in (20, 999):
            result = run_direct_ray_trace(high_reflection_corridor(1000, depth_limit))
            self.assertEqual(result.receiver_hit_count, 0)
            self.assertEqual(result.metrics["_reflection_summary"]["depth_limit_count"], 64)
        trace_input = high_reflection_corridor(1000, 1000, reflectance=0.1)
        trace_input.config.min_energy = 1e-9
        result = run_direct_ray_trace(trace_input)
        summary = result.metrics["_reflection_summary"]
        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(summary["reflection_below_energy_count"], 64)
        self.assertEqual(summary["depth_limit_count"], 0)
        self.assertLess(summary["max_observed_depth"], 20)

    def test_scalar_thousand_reflection_path_is_not_recursive_or_truncated(self) -> None:
        result = run_direct_ray_trace(
            high_reflection_corridor(1000, 1000, ray_count=2, store_paths=True),
            intersection_dispatch="scalar",
            intersection_provider="python_cpu",
        )
        self.assertEqual(result.receiver_hit_count, 2)
        self.assertEqual(len(result.stored_paths), 2)
        self.assertEqual(len(result.stored_paths[0]), 1002)
        self.assertEqual(result.stored_paths[0][-1].depth, 1000)
        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.999**1000, places=11)

    def test_batch_size_change_preserves_primary_samples(self) -> None:
        reference = run_direct_ray_trace(high_reflection_corridor(100, 1000), intersection_batch_size=64)
        split = run_direct_ray_trace(high_reflection_corridor(100, 1000), intersection_batch_size=17)
        np.testing.assert_allclose(
            reference.receiver_grids[0].flux_lumen,
            split.receiver_grids[0].flux_lumen,
            rtol=1e-12,
            atol=1e-15,
        )
        self.assertEqual(reference.receiver_hit_count, split.receiver_hit_count)


class HighReflectionGpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
        if not (
            cls.preflight.available
            and cls.preflight.strict_float64
            and cls.preflight.kernel_executed
            and cls.preflight.kernel_verified
            and cls.preflight.preflight_scope == "production_ray_bvh"
            and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1"
        ):
            raise unittest.SkipTest(cls.preflight.reason_code or "production CUDA unavailable")

    def test_high_depth_resident_matches_cpu_without_fallback(self) -> None:
        for depth in (100, 1000):
            with self.subTest(depth=depth):
                ray_count = 64 if depth == 100 else 2500
                reference = run_direct_ray_trace(high_reflection_corridor(depth, depth, ray_count=ray_count, store_paths=True))
                resident = run_direct_ray_trace(high_reflection_corridor(depth, depth, ray_count=ray_count, backend="gpu_cuda", store_paths=True))
                performance = resident.metrics["_performance_summary"]
                self.assertEqual(resident.receiver_hit_count, reference.receiver_hit_count)
                self.assertEqual(resident.surface_hit_count, reference.surface_hit_count)
                self.assertIn(performance["compute_execution_state"], ("gpu_active", "gpu_mixed"))
                self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0)
                self.assertEqual(performance["gpu_resident_wavefront_fallback_count"], 0)
                if depth == 1000:
                    self.assertEqual(performance["intersection_batch_size"], 2048)
                    self.assertTrue(performance["wavefront_batch_memory_limited"])
                    self.assertGreaterEqual(performance["gpu_resident_wavefront_success_count"], 2)
                self.assertEqual(resident.stored_paths[0][-1].depth, depth)
                np.testing.assert_allclose(
                    resident.receiver_grids[0].flux_lumen,
                    reference.receiver_grids[0].flux_lumen,
                    rtol=1e-11,
                    atol=1e-15,
                )
                self.assertAlmostEqual(resident.metrics["observer"]["total_flux_lumen"], 0.999**depth, places=11)

    def test_full_gpu_event_tape_preserves_thousand_reflection_depth(self) -> None:
        result = run_direct_ray_trace(
            high_reflection_corridor(1000, 1000, backend="gpu_cuda", store_paths=True),
            gpu_accumulator="host",
            gpu_workspace="full",
        )
        performance = result.metrics["_performance_summary"]
        self.assertEqual(result.receiver_hit_count, 64)
        self.assertEqual(result.stored_paths[0][-1].depth, 1000)
        self.assertGreater(performance["gpu_cuda_gpu_success_count"], 0)
        self.assertEqual(performance["gpu_resident_wavefront_fallback_count"], 0)
        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.999**1000, places=11)


if __name__ == "__main__":
    unittest.main()
