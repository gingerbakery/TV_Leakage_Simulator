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
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.gpu_cuda_resident_wavefront import (
    COMPACT_WORKSPACE_CONTRACT,
    FULL_WORKSPACE_CONTRACT,
    GpuResidentWavefrontProviderError,
    _reset_gpu_resident_wavefront_for_tests,
)
from leakage_simulator.raytracer import run_direct_ray_trace


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _run(workspace: str):
    trace_input = build_depth_ten_case(257)
    trace_input.config.compute_backend = "gpu_cuda"
    trace_input.config.store_ray_paths = True
    trace_input.config.max_stored_paths = 8
    return run_direct_ray_trace(
        trace_input,
        intersection_batch_size=512,
        wavefront_residency="gpu_resident",
        gpu_accumulator="gpu",
        gpu_workspace=workspace,
    )


class Perf4DCompactWorkspaceTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        _reset_gpu_resident_wavefront_for_tests()

    def test_compact_workspace_matches_full_geometry_workspace(self) -> None:
        _reset_gpu_resident_wavefront_for_tests()
        full = _run("full")
        full_performance = full.metrics["_performance_summary"]

        _reset_gpu_resident_wavefront_for_tests()
        compact = _run("compact")
        compact_performance = compact.metrics["_performance_summary"]

        report = compare_semantic_payloads(
            _semantic_payload(full),
            _semantic_payload(compact),
            absolute_tolerance=1e-9,
            relative_tolerance=1e-9,
            max_ulp_distance=1 << 48,
        )
        self.assertTrue(report.passed, report.to_dict())
        self.assertTrue(report.discrete_exact, report.to_dict())
        self.assertEqual(
            full_performance["gpu_resident_workspace_contract"],
            FULL_WORKSPACE_CONTRACT,
        )
        self.assertEqual(
            compact_performance["gpu_resident_workspace_contract"],
            COMPACT_WORKSPACE_CONTRACT,
        )
        self.assertEqual(
            compact_performance["gpu_resident_event_geometry_capacity"],
            8,
        )
        self.assertGreater(
            full_performance["gpu_resident_event_geometry_capacity"],
            compact_performance["gpu_resident_event_geometry_capacity"],
        )
        self.assertGreater(
            full_performance["gpu_resident_workspace_peak_bytes"],
            compact_performance["gpu_resident_workspace_peak_bytes"],
        )
        self.assertEqual(len(compact.stored_paths), 8)
        self.assertGreater(
            compact_performance["gpu_summary_path_retrace_sec"],
            0.0,
        )

    def test_sparse_path_retrace_failure_replays_whole_chunk(self) -> None:
        reference_input = build_depth_ten_case(257)
        reference_input.config.compute_backend = "gpu_cuda"
        reference_input.config.store_ray_paths = True
        reference_input.config.max_stored_paths = 8
        reference = run_direct_ray_trace(
            reference_input,
            intersection_batch_size=512,
            wavefront_residency="host_roundtrip",
            gpu_accumulator="host",
        )

        _reset_gpu_resident_wavefront_for_tests()
        with patch(
            "leakage_simulator.gpu_cuda_resident_wavefront."
            "_retrace_selected_path_tape",
            side_effect=GpuResidentWavefrontProviderError(
                "execute",
                "forced_sparse_path_retrace_failure",
            ),
        ):
            replayed = _run("compact")

        report = compare_semantic_payloads(
            _semantic_payload(reference),
            _semantic_payload(replayed),
        )
        performance = replayed.metrics["_performance_summary"]
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(performance["wavefront_residency"], "host_roundtrip")
        self.assertEqual(
            performance["gpu_resident_wavefront_fallback_reason"],
            "forced_sparse_path_retrace_failure",
        )


if __name__ == "__main__":
    unittest.main()
