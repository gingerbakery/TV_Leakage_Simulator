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
from verify_gpu_cpu_accuracy import build_stochastic_two_bounce_case
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.gpu_cuda_summary_accumulator import (
    GpuSummaryAccumulatorError,
    PROVIDER_CONTRACT,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def _run(builder, ray_count: int, accumulator: str, *, chunk_size: int = 65536):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = "gpu_cuda"
    return run_direct_ray_trace(
        trace_input,
        intersection_batch_size=chunk_size,
        wavefront_residency="gpu_resident",
        gpu_accumulator=accumulator,
    )


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


class Perf4CGpuSummaryAccumulatorTests(unittest.TestCase):
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

    def assertAccumulatorParity(self, reference, candidate) -> None:
        report = compare_semantic_payloads(
            _semantic_payload(reference),
            _semantic_payload(candidate),
            absolute_tolerance=1e-9,
            relative_tolerance=1e-9,
            max_ulp_distance=1 << 48,
        )
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact, report.to_dict())
        self.assertLessEqual(report.max_absolute_error, 1e-9)

    def test_accumulator_matches_ordered_host_reducer(self) -> None:
        reference = _run(build_depth_ten_case, 8192, "host")
        candidate = _run(build_depth_ten_case, 8192, "gpu")

        self.assertAccumulatorParity(reference, candidate)
        performance = candidate.metrics["_performance_summary"]
        self.assertEqual(performance["wavefront_residency"], "gpu_resident")
        self.assertEqual(
            performance["gpu_summary_accumulator_contract"],
            PROVIDER_CONTRACT,
        )
        self.assertGreater(
            performance["gpu_summary_accumulator_success_count"],
            0,
        )
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_count"],
            0,
        )
        self.assertLess(
            performance["wavefront_event_tape_copy_bytes"],
            32 * 1024,
        )

    def test_accumulator_reuses_state_across_chunks(self) -> None:
        reference = _run(
            build_stochastic_two_bounce_case,
            4096,
            "host",
            chunk_size=512,
        )
        candidate = _run(
            build_stochastic_two_bounce_case,
            4096,
            "gpu",
            chunk_size=512,
        )

        self.assertAccumulatorParity(reference, candidate)
        performance = candidate.metrics["_performance_summary"]
        self.assertEqual(performance["gpu_summary_accumulator_success_count"], 8)
        self.assertEqual(
            performance["gpu_summary_accumulator_reused_state_count"],
            7,
        )

    def test_only_quota_selected_paths_are_downloaded(self) -> None:
        trace_input = build_depth_ten_case(257)
        trace_input.config.compute_backend = "gpu_cuda"
        trace_input.config.store_ray_paths = True
        trace_input.config.max_stored_paths = 8

        result = run_direct_ray_trace(
            trace_input,
            intersection_batch_size=64,
            wavefront_residency="gpu_resident",
            gpu_accumulator="gpu",
        )

        performance = result.metrics["_performance_summary"]
        self.assertEqual(len(result.stored_paths), 8)
        self.assertEqual(performance["gpu_summary_selected_path_count"], 8)
        self.assertEqual(performance["gpu_summary_skipped_path_count"], 249)
        self.assertEqual(
            performance["wavefront_path_materialized_count"],
            8,
        )

    def test_accumulator_failure_replays_the_same_chunk(self) -> None:
        reference = _run(build_depth_ten_case, 512, "host")
        with patch(
            "leakage_simulator.gpu_cuda_resident_wavefront."
            "accumulate_resident_summary_gpu_cuda",
            side_effect=GpuSummaryAccumulatorError(
                "execute",
                "forced_gpu_accumulator_failure",
            ),
        ):
            candidate = _run(build_depth_ten_case, 512, "gpu")

        report = compare_semantic_payloads(
            _semantic_payload(reference),
            _semantic_payload(candidate),
        )
        performance = candidate.metrics["_performance_summary"]
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(performance["wavefront_residency"], "host_roundtrip")
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_reason"],
            "forced_gpu_accumulator_failure",
        )


if __name__ == "__main__":
    unittest.main()
