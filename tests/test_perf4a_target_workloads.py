from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import benchmark_perf4a_target_workloads as perf4a


class Perf4ATargetWorkloadTests(unittest.TestCase):
    def test_workload_scene_signatures_are_stable(self) -> None:
        for name, builder in perf4a.WORKLOADS.items():
            with self.subTest(workload=name):
                first = perf4a._scene_signature(builder(17))
                second = perf4a._scene_signature(builder(17))
                different_ray_count = perf4a._scene_signature(builder(31))
                self.assertEqual(first, second)
                self.assertNotEqual(first, different_ray_count)

    def test_projection_reports_target_throughput_and_speedup(self) -> None:
        projection = perf4a._projection(
            p50_sec=10.0,
            measured_rays=1_000_000,
            target_rays=100_000_000,
            target_seconds=(300, 600),
        )
        self.assertEqual(projection["linear_projected_sec"], 1000.0)
        self.assertAlmostEqual(
            projection["target_gates"]["600"]["required_speedup"],
            1000.0 / 600.0,
        )
        self.assertFalse(projection["target_gates"]["600"]["currently_meets"])

    def test_unknown_workload_is_rejected_before_execution(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown workload"):
            perf4a.benchmark(
                workload_names=("missing",),
                ray_count=1,
                repeats=1,
                chunk_size=1,
                backend="cpu",
                target_rays=1,
            )

    def test_small_cpu_contract_has_required_evidence(self) -> None:
        summary = perf4a.benchmark(
            workload_names=("face_direct",),
            ray_count=64,
            repeats=1,
            chunk_size=32,
            backend="cpu",
            target_rays=640,
        )
        self.assertEqual(summary["contract"], perf4a.CONTRACT)
        self.assertTrue(summary["passed"])
        self.assertIsNone(summary["gpu_preflight"])
        case = summary["cases"][0]
        self.assertEqual(case["name"], "face_direct")
        self.assertEqual(case["primary_ray_count"], 64)
        self.assertEqual(len(case["scene_sha256"]), 64)
        self.assertIn("logical_intersection_rows", case["evidence"])
        self.assertIn("receiver_hit_rate", case["evidence"])
        self.assertEqual(
            case["projection"]["target_primary_rays"],
            640,
        )


if __name__ == "__main__":
    unittest.main()
