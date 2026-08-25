from __future__ import annotations

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
from leakage_simulator.fast_sampling import (
    ReceiverImportanceGeometry,
    sample_receiver_mis_directions,
)
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    RayTraceConfig,
    ReceiverSpec,
)


def _source_mesh() -> tuple[TriangleMesh, list[int]]:
    mesh = TriangleMesh()
    vertices = [
        mesh.add_vertex(point)
        for point in (
            (-0.5, -0.5, 0.0),
            (0.5, -0.5, 0.0),
            (0.5, 0.5, 0.0),
            (-0.5, 0.5, 0.0),
        )
    ]
    faces = [
        mesh.add_face(vertices[0], vertices[1], vertices[2], "source"),
        mesh.add_face(vertices[0], vertices[2], vertices[3], "source"),
    ]
    return mesh, faces


def _build_case(
    ray_count: int,
    seed: int,
    strategy: str,
    *,
    distribution: str = "lambertian",
    max_depth: int = 0,
) -> DirectRayTraceInput:
    mesh, faces = _source_mesh()
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="source",
                emitter_type="face",
                face_indices=faces,
                direction_distribution=distribution,
                gaussian_sigma_deg=4.0,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=seed,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="observer",
                center=(0.0, 0.0, 100.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=4.0,
                height_mm=4.0,
                resolution=(8, 8),
            )
        ],
        optical_profiles=[],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=max_depth,
            seed=seed,
            min_energy=1e-12,
            contribution_mode="summary",
            intersection_backend="bvh",
            store_ray_paths=False,
            primary_sampling_strategy=strategy,
            receiver_importance_fraction=0.5,
        ),
    )


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    payload["config"]["primary_sampling_strategy"] = "normalized"
    return payload


class Perf4EReceiverMisTests(unittest.TestCase):
    def test_mis_weights_are_finite_and_bounded(self) -> None:
        emitter = EmitterSpec(
            emitter_id="datum",
            emitter_type="datum_plane",
            center=(0.0, 0.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=1.0,
            height_mm=1.0,
            direction_distribution="lambertian",
            ray_count=4096,
        )
        receivers = ReceiverImportanceGeometry(
            centers=np.asarray([(0.0, 0.0, 100.0)]),
            normals=np.asarray([(0.0, 0.0, -1.0)]),
            u_axes=np.asarray([(1.0, 0.0, 0.0)]),
            v_axes=np.asarray([(0.0, 1.0, 0.0)]),
            half_widths=np.asarray([2.0]),
            half_heights=np.asarray([2.0]),
            minimum_cosines=np.asarray([0.0]),
        )
        origins = np.zeros((4096, 3), dtype=np.float64)
        normals = np.repeat(
            np.asarray([(0.0, 0.0, 1.0)], dtype=np.float64),
            4096,
            axis=0,
        )
        directions, weights, directed_count = sample_receiver_mis_directions(
            np.random.default_rng(1234),
            emitter,
            origins,
            normals,
            receivers,
            0.5,
            1e-4,
        )

        self.assertTrue(np.all(np.isfinite(directions)))
        self.assertTrue(np.all(np.isfinite(weights)))
        self.assertGreater(directed_count, 1800)
        self.assertLess(directed_count, 2300)
        self.assertGreaterEqual(float(np.min(weights)), 0.0)
        self.assertLessEqual(float(np.max(weights)), 2.0 + 1e-12)

    def test_receiver_mis_reduces_tiny_receiver_flux_variance(self) -> None:
        source_flux = []
        mis_flux = []
        mis_hits = []
        for seed in range(100, 112):
            source = run_direct_ray_trace(_build_case(20_000, seed, "source"))
            candidate = run_direct_ray_trace(
                _build_case(20_000, seed, "receiver_mis")
            )
            source_flux.append(source.metrics["observer"]["total_flux_lumen"])
            mis_flux.append(candidate.metrics["observer"]["total_flux_lumen"])
            mis_hits.append(candidate.receiver_hit_count)

        self.assertGreater(statistics.mean(mis_hits), 9000)
        self.assertLess(
            statistics.pstdev(mis_flux),
            statistics.pstdev(source_flux) * 0.2,
        )
        expected_flux = 16.0 / (np.pi * 100.0 * 100.0)
        self.assertAlmostEqual(
            statistics.mean(mis_flux),
            expected_flux,
            delta=expected_flux * 0.03,
        )

    def test_gaussian_emitter_falls_back_without_changing_samples(self) -> None:
        source = run_direct_ray_trace(
            _build_case(4096, 333, "source", distribution="gaussian")
        )
        fallback = run_direct_ray_trace(
            _build_case(4096, 333, "receiver_mis", distribution="gaussian")
        )

        self.assertEqual(_semantic_payload(source), _semantic_payload(fallback))
        performance = fallback.metrics["_performance_summary"]
        self.assertEqual(performance["primary_sampling_strategy"], "source")
        self.assertEqual(
            performance["primary_sampling_fallback_reasons"],
            {"unsupported_emitter_distribution": 1},
        )

    def test_weighted_primary_batch_matches_gpu_when_available(self) -> None:
        preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
        if not (
            preflight.available
            and preflight.strict_float64
            and preflight.kernel_executed
            and preflight.kernel_verified
            and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
        ):
            self.skipTest(preflight.reason_code or "production CUDA unavailable")

        cpu_input = _build_case(8192, 444, "receiver_mis", max_depth=2)
        cpu_input.config.compute_backend = "cpu"
        gpu_input = _build_case(8192, 444, "receiver_mis", max_depth=2)
        gpu_input.config.compute_backend = "gpu_cuda"
        cpu = run_direct_ray_trace(cpu_input)
        gpu = run_direct_ray_trace(gpu_input)
        report = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(gpu),
            absolute_tolerance=1e-9,
            relative_tolerance=1e-9,
            max_ulp_distance=1 << 48,
        )

        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact, report.to_dict())
        performance = gpu.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_residency"], "gpu_resident")
        self.assertEqual(performance["primary_sampling_strategy"], "receiver_mis")


if __name__ == "__main__":
    unittest.main()
