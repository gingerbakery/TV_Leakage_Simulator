from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_rt2c_reflection_report import build_model_input
from leakage_simulator.raytracer import run_direct_ray_trace


def build_case(contribution_mode: str, max_depth: int = 1):
    trace_input = build_model_input("gaussian")
    trace_input.emitters[0].ray_count = 1000
    trace_input.config.ray_count = 1000
    trace_input.config.max_depth = max_depth
    trace_input.config.contribution_mode = contribution_mode
    trace_input.config.store_ray_paths = True
    trace_input.config.max_stored_paths = 5
    return trace_input


class Perf3ASingleBounceFastPathTests(unittest.TestCase):
    def test_fast_summary_preserves_optical_result(self) -> None:
        fast = run_direct_ray_trace(build_case("summary"))
        detailed = run_direct_ray_trace(build_case("detailed"))

        self.assertEqual(fast.receiver_hit_count, detailed.receiver_hit_count)
        self.assertEqual(fast.surface_hit_count, detailed.surface_hit_count)
        self.assertAlmostEqual(
            fast.metrics["observer"]["total_flux_lumen"],
            detailed.metrics["observer"]["total_flux_lumen"],
            places=12,
        )
        self.assertEqual(
            fast.metrics["_reflection_summary"],
            detailed.metrics["_reflection_summary"],
        )
        self.assertEqual(fast.stored_paths, detailed.stored_paths)

    def test_summary_skips_only_surface_contribution_breakdown(self) -> None:
        fast = run_direct_ray_trace(build_case("summary"))
        detailed = run_direct_ray_trace(build_case("detailed"))

        self.assertEqual(fast.contribution_summary.components, {})
        self.assertEqual(fast.contribution_summary.faces, {})
        self.assertEqual(fast.contribution_summary.materials, {})
        self.assertTrue(detailed.contribution_summary.components)
        self.assertTrue(detailed.contribution_summary.faces)
        self.assertTrue(detailed.contribution_summary.materials)
        self.assertEqual(
            fast.contribution_summary.reflected_receiver_flux_lumen,
            detailed.contribution_summary.reflected_receiver_flux_lumen,
        )

    def test_production_counter_rng_uses_wavefront_at_low_depth(self) -> None:
        one_bounce = run_direct_ray_trace(build_case("summary", max_depth=1))
        multi_bounce = run_direct_ray_trace(build_case("summary", max_depth=2))

        self.assertEqual(
            one_bounce.metrics["_performance_summary"]["execution_path"],
            "single_bounce_wavefront",
        )
        self.assertEqual(
            multi_bounce.metrics["_performance_summary"]["execution_path"],
            "multi_bounce_wavefront",
        )


if __name__ == "__main__":
    unittest.main()
