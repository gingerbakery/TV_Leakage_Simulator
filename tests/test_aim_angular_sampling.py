from __future__ import annotations

import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from leakage_simulator.aim_angular_sampling import sample_area_angular_batch, sample_sphere_angles


def aim_spec(**changes):
    values = dict(distribution="lambertian", gaussian_sigma_deg=12.0,
                  sphere_upper_deg=0.0, sphere_lower_deg=90.0,
                  center=(0.0, 0.0, 2.0), u_axis=(1.0, 0.0, 0.0),
                  v_axis=(0.0, 1.0, 0.0), shape="circle", radius_mm=0.5,
                  width_mm=1.0, height_mm=1.0)
    values.update(changes)
    return SimpleNamespace(**values)


def fixed_source(generator, x_positions=(0.0,), normal=(0.0, 0.0, 1.0)):
    def sample(count):
        slots = generator.integers(0, len(x_positions), count)
        points = np.zeros((count, 3))
        points[:, 0] = np.asarray(x_positions)[slots]
        return points, np.tile(normal, (count, 1)), slots
    return sample


def circle_lambertian_capture(source_x):
    """Independent Gauss-Legendre polar integration of cos(source)cos(target)/pi/r²."""
    nodes, weights = np.polynomial.legendre.leggauss(48)
    radius = 0.25 * (nodes + 1.0)
    radius_weights = 0.25 * weights
    angle = (np.arange(512) + 0.5) * 2.0 * math.pi / 512
    distance_squared = (radius[:, None] * np.cos(angle) - source_x) ** 2 + (radius[:, None] * np.sin(angle)) ** 2 + 4.0
    integrand = 4.0 / (math.pi * distance_squared ** 2)
    return float(np.sum(integrand * (radius * radius_weights)[:, None]) * 2.0 * math.pi / 512)


class AimAngularSamplingTests(unittest.TestCase):
    def test_sphere_lambertian_uses_cosine_weighted_solid_angle(self):
        quantiles = (np.arange(100_000) + 0.5) / 100_000
        aim = aim_spec(sphere_upper_deg=20.0, sphere_lower_deg=70.0)
        x, y, z = sample_sphere_angles(quantiles, quantiles * 0.618 % 1, aim)
        expected = (math.cos(math.radians(20)) ** 2 + math.cos(math.radians(70)) ** 2) / 2
        self.assertAlmostEqual(float(np.mean(z * z)), expected, places=12)
        np.testing.assert_allclose(x * x + y * y + z * z, 1.0, atol=1e-14)

    def test_sphere_gaussian_cdf_matches_independent_angular_integral(self):
        # Use deterministic quantiles to measure integration error, not MC noise.
        quantiles = (np.arange(120_000) + 0.5) / 120_000
        aim = aim_spec(distribution="gaussian", gaussian_sigma_deg=35.0,
                       sphere_upper_deg=15.0, sphere_lower_deg=125.0)
        _, _, z = sample_sphere_angles(quantiles, np.zeros_like(quantiles), aim)
        nodes, weights = np.polynomial.legendre.leggauss(256)
        def integral(a, b):
            theta = (nodes + 1) * (b - a) / 2 + a
            return float(np.dot(weights, np.exp(-0.5 * (theta / math.radians(35)) ** 2) * np.sin(theta)) * (b - a) / 2)
        a, b = math.radians(15), math.radians(125)
        for cutoff in (25, 50, 90):
            expected = integral(a, math.radians(cutoff)) / integral(a, b)
            actual = float(np.mean(z >= math.cos(math.radians(cutoff))))
            self.assertAlmostEqual(actual, expected, delta=1.5e-5)

    def test_sphere_gaussian_can_condition_on_extremely_small_rear_tail(self):
        aim = aim_spec(distribution="gaussian", gaussian_sigma_deg=0.1,
                       sphere_upper_deg=170.0, sphere_lower_deg=180.0)
        x, y, z = sample_sphere_angles(np.array([0.01, 0.5, 0.99]), np.zeros(3), aim)
        theta = np.degrees(np.arccos(z))
        self.assertTrue(np.all(np.isfinite(x + y + z)))
        self.assertTrue(np.all(theta >= 170.0))
        self.assertGreater(theta[-1], theta[0])
        self.assertLess(theta[-1], 170.001)

    def test_sphere_lambertian_clips_to_forward_hemisphere_and_rejects_rear_only(self):
        _, _, z = sample_sphere_angles(np.linspace(0, 1, 100), np.zeros(100), aim_spec(sphere_lower_deg=180.0))
        self.assertTrue(np.all(z >= 0.0))
        with self.assertRaisesRegex(ValueError, "forward hemisphere"):
            sample_sphere_angles(0.5, 0.5, aim_spec(sphere_upper_deg=100.0, sphere_lower_deg=180.0))

    def test_sphere_lambertian_preserves_thin_near_grazing_band(self):
        _, _, z = sample_sphere_angles(np.array([0.25, 0.75]), np.zeros(2),
                                      aim_spec(sphere_upper_deg=89.999999, sphere_lower_deg=90.0))
        self.assertGreater(z[0], z[1])
        self.assertGreater(z[1], 0.0)
        self.assertAlmostEqual(z[0] / z[1], math.sqrt(3.0), places=12)

    def test_area_joint_sampler_changes_source_share_instead_of_normalizing_each_source(self):
        f0, f1 = circle_lambertian_capture(0.0), circle_lambertian_capture(3.0)
        self.assertAlmostEqual(f0, 0.25 / 4.25, places=12)
        expected_share = f0 / (f0 + f1)
        for bounds in (((0.0, 0.0, 0.0), (3.0, 0.0, 0.0)), None):
            rng = np.random.default_rng(9876)
            points, directions, faces = sample_area_angular_batch(
                rng, 20_000, aim_spec(), 0.0, fixed_source(rng, (0.0, 3.0)), source_bounds=bounds)
            self.assertAlmostEqual(float(np.mean(faces == 0)), expected_share, delta=0.007)
            hits = points + (2.0 / directions[:, 2])[:, None] * directions
            self.assertTrue(np.all(np.sum(hits[:, :2] ** 2, axis=1) <= 0.25 + 1e-12))
            np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1.0, atol=1e-14)

    def test_area_gaussian_radial_shape_matches_angular_integral(self):
        aim = aim_spec(distribution="gaussian", gaussian_sigma_deg=15.0,
                       center=(0.0, 0.0, 1.0), radius_mm=1.0)
        rng = np.random.default_rng(38495)
        points, directions, _ = sample_area_angular_batch(rng, 30_000, aim, 0.0, fixed_source(rng))
        actual_radius_squared = np.sum((directions[:, :2] / directions[:, 2, None]) ** 2, axis=1)
        nodes, weights = np.polynomial.legendre.leggauss(256)
        theta = (nodes + 1) * math.pi / 8
        density = np.exp(-0.5 * (theta / math.radians(15)) ** 2) * np.sin(theta)
        expected = float(np.dot(weights, np.tan(theta) ** 2 * density) / np.dot(weights, density))
        self.assertAlmostEqual(float(np.mean(actual_radius_squared)), expected, delta=0.003)

    def test_far_tiny_target_uses_efficient_target_proposal(self):
        rng = np.random.default_rng(542)
        points, directions, _ = sample_area_angular_batch(
            rng, 2000, aim_spec(center=(0.0, 0.0, 1_000_000.0)), 0.0,
            fixed_source(rng), source_bounds=((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
        self.assertEqual(len(points), 2000)
        self.assertTrue(np.all(directions[:, 2] > 0.99999999))

    def test_no_support_fails_bounded_and_both_sides_can_reach_target(self):
        rng = np.random.default_rng(45)
        sampler = fixed_source(rng, normal=(0.0, 0.0, -1.0))
        bounds = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        with self.assertRaisesRegex(ValueError, "no or too little power"):
            sample_area_angular_batch(rng, 8, aim_spec(), 0.0, sampler, source_bounds=bounds)
        points, directions, _ = sample_area_angular_batch(
            rng, 3000, aim_spec(), 0.0, sampler, two_sided=True, source_bounds=bounds)
        self.assertEqual(len(points), 3000)
        self.assertTrue(np.all(directions[:, 2] > 0.0))


if __name__ == "__main__":
    unittest.main()
