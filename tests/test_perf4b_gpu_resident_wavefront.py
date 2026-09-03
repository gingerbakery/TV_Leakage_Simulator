from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from perf4_accuracy import compare_semantic_payloads
from verify_gpu_cpu_accuracy import (
    build_face_direct_case,
    build_stochastic_two_bounce_case,
)
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.gpu_cuda_resident_wavefront import (
    GpuResidentWavefrontBatch,
    GpuResidentWavefrontProviderError,
    PROVIDER_CONTRACT,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    return payload


def _run(builder, ray_count: int, backend: str, residency: str):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = backend
    return run_direct_ray_trace(
        trace_input,
        wavefront_residency=residency,
        gpu_accumulator="host",
    )


class Perf4AccuracyContractTests(unittest.TestCase):
    def test_float64_ulp_noise_passes_without_hiding_discrete_changes(self) -> None:
        reference = {
            "count": 7,
            "flux": 0.019691785314741937,
            "nested": [1715.2316318490728],
        }
        candidate = {
            "count": 7,
            "flux": 0.01969178531474194,
            "nested": [1715.231631849073],
        }

        report = compare_semantic_payloads(reference, candidate)

        self.assertTrue(report.passed)
        self.assertFalse(report.semantic_exact)
        self.assertTrue(report.discrete_exact)
        self.assertTrue(report.float64_tolerance_passed)
        self.assertEqual(report.discrete_difference_count, 0)
        self.assertEqual(report.numeric_difference_count, 2)
        self.assertLessEqual(report.max_ulp_distance, 8)

        failed = compare_semantic_payloads(
            reference,
            {**candidate, "count": 8},
        )
        self.assertFalse(failed.passed)
        self.assertFalse(failed.discrete_exact)
        self.assertEqual(failed.discrete_difference_count, 1)

    def test_resident_batch_rejects_invalid_direction_and_depth(self) -> None:
        common = {
            "origins": [(0.0, 0.0, 0.0)],
            "initial_power_lumen": [1.0],
            "source_faces": [-1],
            "reflection_seeds": [1],
            "epsilon_mm": 1e-6,
            "min_energy": 1e-9,
            "termination_mode": 0,
        }
        with self.assertRaisesRegex(ValueError, "directions must be normalized"):
            GpuResidentWavefrontBatch(
                **common,
                directions=[(0.0, 0.0, 2.0)],
                max_depth=2,
            )
        with self.assertRaisesRegex(ValueError, "max_depth"):
            GpuResidentWavefrontBatch(
                **common,
                directions=[(0.0, 0.0, 1.0)],
                max_depth=33,
            )


class Perf4BGpuResidentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
        if not (
            cls.preflight.available
            and cls.preflight.strict_float64
            and cls.preflight.kernel_executed
            and cls.preflight.kernel_verified
            and cls.preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
        ):
            raise unittest.SkipTest(
                cls.preflight.reason_code or "production CUDA unavailable"
            )

    def test_depth_ten_resident_matches_cpu_exactly(self) -> None:
        cpu = _run(build_depth_ten_case, 512, "cpu", "host_roundtrip")
        resident = _run(
            build_depth_ten_case,
            512,
            "gpu_cuda",
            "gpu_resident",
        )

        report = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(resident),
        )
        performance = resident.metrics["_performance_summary"]
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.semantic_exact)
        self.assertEqual(performance["wavefront_residency"], "gpu_resident")
        self.assertEqual(
            performance["gpu_resident_wavefront_contract"],
            PROVIDER_CONTRACT,
        )
        self.assertGreater(
            performance["gpu_resident_wavefront_success_count"],
            0,
        )
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_count"],
            0,
        )

    def test_zero_reflection_resident_matches_cpu_exactly(self) -> None:
        def build_case(ray_count: int):
            trace_input = build_face_direct_case(ray_count)
            trace_input.config.max_depth = 0
            return trace_input

        cpu = _run(build_case, 512, "cpu", "host_roundtrip")
        resident = _run(build_case, 512, "gpu_cuda", "gpu_resident")

        report = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(resident),
        )
        performance = resident.metrics["_performance_summary"]
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.semantic_exact)
        self.assertEqual(performance["execution_path"], "single_bounce_wavefront")
        self.assertEqual(performance["wavefront_residency"], "gpu_resident")
        self.assertGreater(
            performance["gpu_resident_wavefront_success_count"],
            0,
        )

    def test_one_reflection_resident_preserves_cpu_contract(self) -> None:
        def build_case(ray_count: int):
            trace_input = build_stochastic_two_bounce_case(ray_count)
            trace_input.config.max_depth = 1
            trace_input.config.angle_dependent_reflectance = False
            return trace_input

        cpu = _run(build_case, 8192, "cpu", "host_roundtrip")
        resident = _run(build_case, 8192, "gpu_cuda", "gpu_resident")

        report = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(resident),
        )
        performance = resident.metrics["_performance_summary"]
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact)
        self.assertTrue(report.float64_tolerance_passed)
        self.assertLessEqual(report.max_ulp_distance, 8)
        self.assertEqual(performance["execution_path"], "single_bounce_wavefront")
        self.assertEqual(performance["wavefront_residency"], "gpu_resident")
        self.assertGreater(
            performance["gpu_resident_wavefront_success_count"],
            0,
        )

    def test_stochastic_resident_preserves_discrete_trace_and_float64_tolerance(
        self,
    ) -> None:
        cpu = _run(
            build_stochastic_two_bounce_case,
            8192,
            "cpu",
            "host_roundtrip",
        )
        resident = _run(
            build_stochastic_two_bounce_case,
            8192,
            "gpu_cuda",
            "gpu_resident",
        )

        report = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(resident),
        )
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact)
        self.assertTrue(report.float64_tolerance_passed)
        self.assertLessEqual(report.max_ulp_distance, 8)
        self.assertEqual(cpu.receiver_hit_count, resident.receiver_hit_count)
        self.assertEqual(cpu.surface_hit_count, resident.surface_hit_count)

    def test_resident_failure_replays_chunk_through_host_roundtrip(self) -> None:
        reference = _run(
            build_depth_ten_case,
            8192,
            "gpu_cuda",
            "host_roundtrip",
        )
        with patch(
            "leakage_simulator.raytracer.trace_resident_wavefront_gpu_cuda",
            side_effect=GpuResidentWavefrontProviderError(
                "execute",
                "forced_resident_failure",
            ),
        ):
            replayed = _run(
                build_depth_ten_case,
                8192,
                "gpu_cuda",
                "gpu_resident",
            )

        self.assertEqual(
            _semantic_payload(replayed),
            _semantic_payload(reference),
        )
        performance = replayed.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_residency"], "host_roundtrip")
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_count"],
            1,
        )
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_phase"],
            "execute",
        )
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_reason"],
            "forced_resident_failure",
        )


if __name__ == "__main__":
    unittest.main()
