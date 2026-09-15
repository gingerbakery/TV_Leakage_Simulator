import unittest

import numpy as np

from leakage_simulator.fast_sampling import iter_virtual_plane_ray_batches
from leakage_simulator.types import EmitterSpec


class EmitterBothSidesTests(unittest.TestCase):
    def _emitter(self, direction: str) -> EmitterSpec:
        return EmitterSpec(
            emitter_id="two-sided-plane",
            emitter_type="datum_plane",
            face_indices=[],
            normal_mode="custom",
            normal_flip=False,
            emission_direction=direction,
            custom_normal=(0.0, 0.0, 1.0),
            direction_distribution="lambertian",
            center=(0.0, 0.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=10.0,
            height_mm=10.0,
            ray_count=20_000,
        )

    def test_both_sides_splits_one_ray_budget_between_two_hemispheres(self) -> None:
        emitter = self._emitter("both")
        batches = list(iter_virtual_plane_ray_batches(emitter, 1e-3, 42))
        origins = np.concatenate([batch[0] for batch in batches])
        directions = np.concatenate([batch[1] for batch in batches])

        self.assertEqual(len(directions), emitter.ray_count)
        forward = int(np.count_nonzero(directions[:, 2] > 0.0))
        reverse = int(np.count_nonzero(directions[:, 2] < 0.0))
        self.assertGreater(forward, emitter.ray_count * 0.45)
        self.assertGreater(reverse, emitter.ray_count * 0.45)
        self.assertTrue(np.all(origins[:, 2] * directions[:, 2] > 0.0))

    def test_explicit_reverse_direction_keeps_legacy_normal_flip_contract(self) -> None:
        emitter = self._emitter("reverse")
        self.assertTrue(emitter.normal_flip)
        _, directions = next(iter_virtual_plane_ray_batches(emitter, 1e-3, 42))
        self.assertTrue(np.all(directions[:, 2] < 0.0))


if __name__ == "__main__":
    unittest.main()
