import math
import unittest

from leakage_simulator.raytracer import _build_direct_metrics
from leakage_simulator.types import RayTraceConfig, ReceiverGrid


class ReceiverErrorEstimateTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
