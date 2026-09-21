import math
import unittest

from leakage_simulator.raytracer import _build_direct_metrics
from leakage_simulator.types import RayTraceConfig, ReceiverGrid


class ReceiverErrorEstimateTests(unittest.TestCase):
    def test_missing_nonpeak_moment_cannot_understate_bright_area_error(self) -> None:
        for missing_moment in (0.0, -0.001, math.nan, math.inf):
            with self.subTest(moment=missing_moment):
                grid = ReceiverGrid(
                    receiver_id="receiver", resolution=(2, 1), bin_area_mm2=1.0,
                    flux_lumen=[[1.0, 0.5]], hit_count=1500,
                    flux_squared_lumen2=0.0015,
                    flux_squared_lumen2_grid=[[0.001, missing_moment]],
                )
                metrics = _build_direct_metrics([grid], RayTraceConfig(), 10_000)["receiver"]
                self.assertEqual(metrics["total_flux_lumen"], 1.5)
                self.assertLess(metrics["peak_error_estimate_percent"], 5.0)
                self.assertIsNone(metrics["peak_area_error_estimate_percent"])

    def test_tied_peaks_use_the_larger_uncertainty(self) -> None:
        grid = ReceiverGrid(
            receiver_id="receiver", resolution=(2, 1), bin_area_mm2=1.0,
            flux_lumen=[[1.0, 1.0]], hit_count=102,
            flux_squared_lumen2=0.51, flux_squared_lumen2_grid=[[0.01, 0.5]],
        )
        metrics = _build_direct_metrics([grid], RayTraceConfig(), 1000)["receiver"]
        self.assertAlmostEqual(metrics["peak_effective_sample_count"], 2.0)
        self.assertGreater(metrics["peak_error_estimate_percent"], 70.0)
        self.assertEqual(metrics["peak_statistical_quality"], "insufficient_samples")

    def test_peak_cell_uncertainty_is_not_the_bright_area_uncertainty(self) -> None:
        grid = ReceiverGrid(
            receiver_id="receiver",
            resolution=(100, 1),
            bin_area_mm2=1.0,
            flux_lumen=[[0.01] * 100],
            hit_count=10_000,
            flux_squared_lumen2=0.0001,
            flux_squared_lumen2_grid=[[0.000001] * 100],
        )
        metrics = _build_direct_metrics([grid], RayTraceConfig(), 10_000)["receiver"]
        self.assertLess(metrics["peak_area_error_estimate_percent"], 0.0001)
        self.assertGreater(metrics["peak_error_estimate_percent"], 9.0)
        self.assertAlmostEqual(metrics["peak_effective_sample_count"], 100.0)

    def test_legacy_missing_cell_moments_do_not_claim_a_precise_peak(self) -> None:
        grid = ReceiverGrid(
            receiver_id="receiver",
            resolution=(1, 1),
            bin_area_mm2=1.0,
            flux_lumen=[[1.0]],
            hit_count=100,
        )
        metrics = _build_direct_metrics([grid], RayTraceConfig(), 100)["receiver"]
        self.assertIsNone(metrics["peak_error_estimate_percent"])
        self.assertEqual(metrics["peak_statistical_quality"], "unavailable")

    def test_reports_total_and_peak_area_monte_carlo_error(self) -> None:
        grid = ReceiverGrid(
            receiver_id="receiver",
            resolution=(2, 1),
            bin_area_mm2=1.0,
            flux_lumen=[[2.0, 0.0]],
            hit_count=2,
            flux_squared_lumen2=2.0,
            flux_squared_lumen2_grid=[[2.0, 0.0]],
        )

        metrics = _build_direct_metrics([grid], RayTraceConfig(), 4)["receiver"]

        expected = math.sqrt(1.0 / 3.0) * 100.0
        self.assertAlmostEqual(metrics["error_estimate_percent"], expected)
        self.assertAlmostEqual(metrics["peak_area_error_estimate_percent"], expected)
        self.assertEqual(metrics["statistical_quality"], "insufficient_hits")
        self.assertEqual(metrics["minimum_convergence_hits"], 30.0)
        self.assertEqual(metrics["estimated_rays_for_minimum_hits"], 60)
        self.assertEqual(metrics["heatmap_bin_count"], 2.0)
        self.assertEqual(metrics["heatmap_hits_per_bin"], 1.0)
        self.assertEqual(metrics["heatmap_quality"], "noisy")
        self.assertEqual(metrics["estimated_rays_for_usable_heatmap"], 20)

    def test_zero_hits_never_reports_zero_percent_error(self) -> None:
        grid = ReceiverGrid(
            receiver_id="receiver",
            resolution=(2, 1),
            bin_area_mm2=1.0,
            flux_lumen=[[0.0, 0.0]],
            hit_count=0,
            flux_squared_lumen2=0.0,
            flux_squared_lumen2_grid=[[0.0, 0.0]],
        )

        metrics = _build_direct_metrics([grid], RayTraceConfig(), 1_000)[
            "receiver"
        ]

        self.assertEqual(metrics["error_estimate_percent"], 100.0)
        self.assertEqual(metrics["peak_area_error_estimate_percent"], 100.0)
        self.assertIsNone(metrics["peak_error_estimate_percent"])
        self.assertEqual(metrics["peak_effective_sample_count"], 0.0)
        self.assertEqual(metrics["peak_statistical_quality"], "no_hits")
        self.assertEqual(metrics["statistical_quality"], "no_hits")
        self.assertEqual(metrics["receiver_hit_rate"], 0.0)
        self.assertIsNone(metrics["estimated_rays_for_minimum_hits"])
        self.assertEqual(metrics["heatmap_quality"], "no_hits")
        self.assertIsNone(metrics["estimated_rays_for_usable_heatmap"])


if __name__ == "__main__":
    unittest.main()
