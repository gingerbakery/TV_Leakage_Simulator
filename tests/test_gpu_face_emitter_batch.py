from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_raytracer_rt1 import build_emitter_plane

from leakage_simulator.fast_sampling import (
    build_face_emitter_batch_geometry,
    iter_face_emitter_ray_batches,
)
from leakage_simulator.types import EmitterSpec


class GpuFaceEmitterBatchTests(unittest.TestCase):
    def test_face_sampler_batches_every_ray_with_source_face_ids(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="face-source",
            emitter_type="face",
            face_indices=[0, 1],
            direction_distribution="lambertian",
            ray_count=257,
            seed=17,
        )
        geometry = build_face_emitter_batch_geometry(mesh, emitter)
        self.assertIsNotNone(geometry)
        assert geometry is not None

        batches = list(
            iter_face_emitter_ray_batches(
                emitter,
                geometry,
                epsilon_mm=1e-4,
                seed=17,
                batch_size=31,
            )
        )
        origins = np.concatenate([batch[0] for batch in batches])
        directions = np.concatenate([batch[1] for batch in batches])
        source_faces = np.concatenate([batch[2] for batch in batches])

        self.assertEqual(len(origins), 257)
        self.assertEqual(len(batches), 9)
        self.assertEqual(set(source_faces.tolist()), {0, 1})
        np.testing.assert_allclose(origins[:, 2], 1e-4, atol=1e-12)
        np.testing.assert_allclose(
            np.linalg.norm(directions, axis=1),
            1.0,
            rtol=1e-12,
            atol=1e-12,
        )
        self.assertTrue(np.all(directions[:, 2] >= 0.0))

    def test_face_sampler_is_deterministic_across_repeated_runs(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="face-source",
            emitter_type="face",
            face_indices=[0, 1],
            direction_distribution="gaussian",
            gaussian_sigma_deg=4.0,
            ray_count=73,
            seed=19,
        )
        geometry = build_face_emitter_batch_geometry(mesh, emitter)
        assert geometry is not None

        def sample() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            batches = list(
                iter_face_emitter_ray_batches(
                    emitter,
                    geometry,
                    epsilon_mm=2e-4,
                    seed=19,
                    batch_size=16,
                )
            )
            return tuple(
                np.concatenate([batch[index] for batch in batches])
                for index in range(3)
            )  # type: ignore[return-value]

        first = sample()
        second = sample()
        for first_values, second_values in zip(first, second):
            np.testing.assert_array_equal(first_values, second_values)

    def test_lambertian_batch_matches_area_and_direction_expectations(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="face-source",
            emitter_type="face",
            face_indices=[0, 1],
            direction_distribution="lambertian",
            ray_count=50_000,
            seed=23,
        )
        geometry = build_face_emitter_batch_geometry(mesh, emitter)
        assert geometry is not None
        batches = list(
            iter_face_emitter_ray_batches(
                emitter,
                geometry,
                epsilon_mm=1e-4,
                seed=23,
                batch_size=8192,
            )
        )
        origins = np.concatenate([batch[0] for batch in batches])
        directions = np.concatenate([batch[1] for batch in batches])
        source_faces = np.concatenate([batch[2] for batch in batches])

        self.assertLess(abs(float(np.mean(origins[:, 0]))), 0.05)
        self.assertLess(abs(float(np.mean(origins[:, 1]))), 0.05)
        self.assertAlmostEqual(
            float(np.mean(source_faces == 0)),
            0.5,
            delta=0.01,
        )
        self.assertLess(abs(float(np.mean(directions[:, 0]))), 0.01)
        self.assertLess(abs(float(np.mean(directions[:, 1]))), 0.01)
        self.assertAlmostEqual(
            float(np.mean(directions[:, 2])),
            2.0 / 3.0,
            delta=0.01,
        )

    def test_invalid_or_degenerate_face_set_has_no_batch_geometry(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="face-source",
            emitter_type="face",
            face_indices=[999],
            ray_count=1,
        )
        self.assertIsNone(build_face_emitter_batch_geometry(mesh, emitter))


if __name__ == "__main__":
    unittest.main()
