from __future__ import annotations

import math
import statistics
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.native_cpu_counter_wavefront import (
    CounterWavefrontPlanInput,
    plan_counter_native_cpu,
    plan_counter_reference,
    probe_native_cpu_counter_wavefront,
)
from leakage_simulator.native_cpu_wavefront import SCATTER_LAMBERTIAN
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


def _add_quad(
    mesh: TriangleMesh,
    points: tuple[tuple[float, float, float], ...],
    material_id: str,
) -> None:
    vertices = [mesh.add_vertex(point) for point in points]
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id)


def _build_reflected_case(
    ray_count: int,
    seed: int,
    strategy: str,
    *,
    backend: str = "cpu",
    scatter_model: str = "lambertian",
    with_blocker: bool = False,
) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    _add_quad(
        mesh,
        (
            (-20.0, -20.0, 10.0),
            (20.0, -20.0, 10.0),
            (20.0, 20.0, 10.0),
            (-20.0, 20.0, 10.0),
        ),
        "reflector",
    )
    emitter_z = 5.0 if with_blocker else 0.0
    if with_blocker:
        _add_quad(
            mesh,
            (
                (-5.0, -5.0, 0.0),
                (5.0, -5.0, 0.0),
                (5.0, 5.0, 0.0),
                (-5.0, 5.0, 0.0),
            ),
            "blocker",
        )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="source",
                emitter_type="datum_plane",
                center=(0.0, 0.0, emitter_z),
                u_axis=(1.0, 0.0, 0.0),
                v_axis=(0.0, 1.0, 0.0),
                width_mm=0.2,
                height_mm=0.2,
                direction_distribution="gaussian",
                gaussian_sigma_deg=0.01,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=seed,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="observer",
                center=(0.0, 0.0, -10.0),
                normal=(0.0, 0.0, 1.0),
                width_mm=1.0,
                height_mm=1.0,
                resolution=(4, 4),
            )
        ],
        optical_profiles=[
            OpticalProfile(
                "reflector",
                0.8,
                scatter_model=scatter_model,
                gaussian_sigma_deg=8.0,
            ),
            OpticalProfile("blocker", 0.0, scatter_model="none"),
        ],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=2,
            seed=seed,
            min_energy=1e-12,
            contribution_mode="summary",
            intersection_backend="bvh",
            compute_backend=backend,
            store_ray_paths=False,
            bounce_sampling_strategy=strategy,
            bounce_receiver_importance_fraction=0.5,
        ),
    )


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    payload["config"]["bounce_sampling_strategy"] = "normalized"
    return payload


class Perf4EBounceMisTests(unittest.TestCase):
    def test_counter_planner_mis_matches_reference_and_bounds_weights(self) -> None:
        capability = probe_native_cpu_counter_wavefront()
        if not capability.available:
            self.skipTest(capability.reason_code or "Numba CPU unavailable")
        row_count = 2048
        batch = CounterWavefrontPlanInput(
            incoming_directions=np.tile((0.0, 0.0, 1.0), (row_count, 1)),
            surface_normals=np.tile((0.0, 0.0, 1.0), (row_count, 1)),
            incoming_power_lumen=np.ones(row_count),
            profile_reflectance=np.full(row_count, 0.8),
            profile_roughness=np.zeros(row_count),
            scatter_models=np.full(row_count, SCATTER_LAMBERTIAN, dtype=np.int8),
            profile_specular_ratio=np.zeros(row_count),
            profile_gaussian_sigma_deg=np.full(row_count, 8.0),
            rng_keys=np.arange(1000, 1000 + row_count, dtype=np.uint64),
            depth=0,
            max_depth=2,
            min_energy=0.0,
            surface_points=np.tile((0.0, 0.0, 10.0), (row_count, 1)),
            receiver_centers=np.asarray([(0.0, 0.0, -10.0)]),
            receiver_normals=np.asarray([(0.0, 0.0, 1.0)]),
            receiver_u_axes=np.asarray([(1.0, 0.0, 0.0)]),
            receiver_v_axes=np.asarray([(0.0, 1.0, 0.0)]),
            receiver_half_widths=np.asarray([0.5]),
            receiver_half_heights=np.asarray([0.5]),
            receiver_minimum_cosines=np.asarray([0.0]),
            receiver_importance_fraction=0.5,
        )

        reference = plan_counter_reference(batch)
        native = plan_counter_native_cpu(batch).result

        for field in (
            "supported_mask",
            "status_flags",
            "lobe_codes",
            "rng_draw_counts",
            "importance_eligible_mask",
            "importance_directed_mask",
            "importance_zero_weight_mask",
            "importance_unsupported_mask",
            "reflected_power_lumen",
            "emitted_power_lumen",
            "emitted_directions",
            "importance_weights",
        ):
            self.assertTrue(
                np.array_equal(getattr(reference, field), getattr(native, field)),
                field,
            )
        directed_count = int(np.count_nonzero(native.importance_directed_mask))
        self.assertEqual(int(np.count_nonzero(native.importance_eligible_mask)), row_count)
        self.assertGreater(directed_count, row_count * 0.45)
        self.assertLess(directed_count, row_count * 0.55)
        self.assertGreaterEqual(float(np.min(native.importance_weights)), 0.0)
        self.assertLessEqual(float(np.max(native.importance_weights)), 2.0)

    def test_bounce_mis_reduces_hidden_receiver_flux_variance(self) -> None:
        source_flux = []
        mis_flux = []
        mis_hits = []
        for seed in range(200, 208):
            source = run_direct_ray_trace(
                _build_reflected_case(10_000, seed, "source")
            )
            candidate = run_direct_ray_trace(
                _build_reflected_case(10_000, seed, "receiver_mis")
            )
            source_flux.append(source.metrics["observer"]["total_flux_lumen"])
            mis_flux.append(candidate.metrics["observer"]["total_flux_lumen"])
            mis_hits.append(candidate.receiver_hit_count)

        self.assertGreater(statistics.mean(mis_hits), 4_500)
        self.assertLess(
            statistics.pstdev(mis_flux),
            statistics.pstdev(source_flux) * 0.2,
        )
        expected_flux = 0.8 / (math.pi * 20.0 * 20.0)
        self.assertAlmostEqual(
            statistics.mean(mis_flux),
            expected_flux,
            delta=expected_flux * 0.03,
        )

    def test_scene_intersection_blocks_receiver_directed_bounce(self) -> None:
        result = run_direct_ray_trace(
            _build_reflected_case(
                4096,
                5150,
                "receiver_mis",
                with_blocker=True,
            )
        )

        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(result.metrics["observer"]["total_flux_lumen"], 0.0)
        self.assertGreater(result.surface_hit_count, 4096)

    def test_gaussian_surface_falls_back_without_changing_trace(self) -> None:
        source = run_direct_ray_trace(
            _build_reflected_case(
                4096,
                6161,
                "source",
                scatter_model="gaussian",
            )
        )
        fallback = run_direct_ray_trace(
            _build_reflected_case(
                4096,
                6161,
                "receiver_mis",
                scatter_model="gaussian",
            )
        )

        self.assertEqual(_semantic_payload(source), _semantic_payload(fallback))
        performance = fallback.metrics["_performance_summary"]
        self.assertEqual(performance["bounce_sampling_strategy"], "source")
        self.assertEqual(
            performance["bounce_sampling_fallback_reasons"],
            {"unsupported_scatter_model": 4096},
        )

    def test_bounce_mis_matches_gpu_when_available(self) -> None:
        preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
        if not (
            preflight.available
            and preflight.strict_float64
            and preflight.kernel_executed
            and preflight.kernel_verified
            and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
        ):
            self.skipTest(preflight.reason_code or "production CUDA unavailable")

        cpu = run_direct_ray_trace(
            _build_reflected_case(8192, 7171, "receiver_mis", backend="cpu"),
            wavefront_residency="host_roundtrip",
        )
        gpu = run_direct_ray_trace(
            _build_reflected_case(
                8192,
                7171,
                "receiver_mis",
                backend="gpu_cuda",
            ),
            wavefront_residency="gpu_resident",
        )
        report = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(gpu),
            absolute_tolerance=1e-12,
            relative_tolerance=1e-12,
            max_ulp_distance=32,
        )

        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact, report.to_dict())
        self.assertEqual(cpu.receiver_hit_count, gpu.receiver_hit_count)
        cpu_performance = cpu.metrics["_performance_summary"]
        gpu_performance = gpu.metrics["_performance_summary"]
        self.assertEqual(
            cpu_performance["bounce_sampling_eligible_surface_count"],
            gpu_performance["bounce_sampling_eligible_surface_count"],
        )
        self.assertEqual(
            cpu_performance["bounce_sampling_receiver_directed_ray_count"],
            gpu_performance["bounce_sampling_receiver_directed_ray_count"],
        )
        self.assertEqual(gpu_performance["wavefront_residency"], "gpu_resident")


if __name__ == "__main__":
    unittest.main()
