from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.fast_sampling import (
    iter_virtual_plane_ray_batches,
    supports_fast_virtual_plane_sampling,
)
from leakage_simulator.types import EmitterSpec


def polygon_emitter(ray_count: int = 257) -> EmitterSpec:
    return EmitterSpec(
        emitter_id="polygon-source",
        emitter_type="reference_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=6.0,
        height_mm=4.0,
        surface_construction="polygon_auto",
        polygon_vertices=[
            (-3.0, -2.0, 0.0),
            (3.0, -2.0, 0.0),
            (1.0, 2.0, 0.0),
            (-3.0, 2.0, 0.0),
        ],
        direction_distribution="lambertian",
        ray_count=ray_count,
        seed=37,
    )


class GpuPolygonEmitterBatchTests(unittest.TestCase):
    def test_polygon_sampler_is_batch_eligible_and_deterministic(self) -> None:
        emitter = polygon_emitter()
        self.assertTrue(supports_fast_virtual_plane_sampling(emitter))

        def sample() -> tuple[np.ndarray, np.ndarray]:
            batches = list(
                iter_virtual_plane_ray_batches(
                    emitter,
                    epsilon_mm=1e-4,
                    seed=37,
                    batch_size=31,
                )
            )
            return (
                np.concatenate([batch[0] for batch in batches]),
                np.concatenate([batch[1] for batch in batches]),
            )

        first_origins, first_directions = sample()
        second_origins, second_directions = sample()
        np.testing.assert_array_equal(first_origins, second_origins)
        np.testing.assert_array_equal(first_directions, second_directions)
        self.assertEqual(len(first_origins), 257)
        np.testing.assert_allclose(first_origins[:, 2], 1e-4, atol=1e-12)
        np.testing.assert_allclose(
            np.linalg.norm(first_directions, axis=1),
            1.0,
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertTrue(np.all(first_directions[:, 2] >= 0.0))

    def test_polygon_samples_stay_inside_the_authored_trapezoid(self) -> None:
        origins = np.concatenate(
            [
                batch[0]
                for batch in iter_virtual_plane_ray_batches(
                    polygon_emitter(20_000),
                    epsilon_mm=1e-4,
                    seed=43,
                    batch_size=4096,
                )
            ]
        )
        x_values = origins[:, 0]
        y_values = origins[:, 1]
        self.assertTrue(np.all(y_values >= -2.0))
        self.assertTrue(np.all(y_values <= 2.0))
        self.assertTrue(np.all(x_values >= -3.0))
        # The sloped right edge runs from (3, -2) to (1, 2).
        self.assertTrue(np.all(x_values <= 2.0 - 0.5 * y_values + 1e-12))


if __name__ == "__main__":
    unittest.main()
