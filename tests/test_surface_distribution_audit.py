from __future__ import annotations

import math
from pathlib import Path
import random
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.reflection import effective_surface_reflectance, sample_reflection_direction
from leakage_simulator.types import OpticalProfile


SAMPLE_COUNT = 16384
CDF_LIMIT = math.sqrt(math.log(2 * 128 / 0.001) / (2 * SAMPLE_COUNT))


def samples(profile, seed, incidence_deg=0):
    angle = math.radians(incidence_deg)
    incoming = (math.sin(angle), 0.0, -math.cos(angle))
    rng = random.Random(seed)
    sampled = [sample_reflection_direction(rng, incoming, (0, 0, 1), profile)
               for _ in range(SAMPLE_COUNT)]
    return np.array([sample.direction for sample in sampled]), [sample.lobe for sample in sampled]


def cdf_distance(values, expected_cdf):
    ordered = np.sort(values)
    expected = expected_cdf(ordered)
    upper = np.arange(1, len(values) + 1) / len(values)
    lower = np.arange(len(values)) / len(values)
    return float(max(np.max(upper - expected), np.max(expected - lower)))


def gaussian_reference(sigma_deg, incidence_deg):
    theta = (np.arange(65536) + 0.5) * (math.pi / 2 / 65536)
    sigma = math.radians(sigma_deg)
    incidence = math.radians(incidence_deg)
    if incidence_deg == 0:
        fraction = np.ones_like(theta)
    else:
        threshold = (1e-9 - np.cos(theta) * math.cos(incidence)) / (np.sin(theta) * math.sin(incidence))
        fraction = np.arccos(np.clip(threshold, -1, 1)) / math.pi
    mass = theta / sigma**2 * np.exp(-theta**2 / (2 * sigma**2)) * fraction * (math.pi / 2 / 65536)
    accepted = float(mass.sum())
    fallback = (1 - accepted)**32
    cumulative = np.cumsum(mass) / accepted
    return lambda values: fallback + (1 - fallback) * np.interp(values, theta, cumulative, left=0, right=1)


class SurfaceDistributionAuditTests(unittest.TestCase):
    def test_lambertian_polar_and_azimuth_cdf_for_independent_seeds(self):
        for seed in (42, 123, 20260921):
            directions, _ = samples(OpticalProfile("test", 0.6, scatter_model="lambertian"), seed)
            cosine = directions[:, 2]
            azimuth = np.mod(np.arctan2(directions[:, 1], directions[:, 0]), 2 * math.pi) / (2 * math.pi)
            with self.subTest(seed=seed):
                self.assertLess(cdf_distance(cosine, lambda values: values**2), CDF_LIMIT)
                self.assertLess(cdf_distance(azimuth, lambda values: values), CDF_LIMIT)
                self.assertLess(abs(float(cosine.mean()) - 2 / 3), 6 * math.sqrt(1 / (18 * SAMPLE_COUNT)))
                np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1, atol=1e-14)

    def test_gaussian_polar_cdf_including_oblique_hemisphere_clipping(self):
        for sigma_deg in (3, 12, 30):
            for incidence_deg in (0, 60, 89):
                reference = gaussian_reference(sigma_deg, incidence_deg)
                angle = math.radians(incidence_deg)
                axis = np.array([math.sin(angle), 0, math.cos(angle)])
                for seed in (42, 123, 20260921):
                    directions, _ = samples(OpticalProfile("test", 0.6, scatter_model="gaussian",
                                                          gaussian_sigma_deg=sigma_deg), seed, incidence_deg)
                    theta = np.arccos(np.clip(directions @ axis, -1, 1))
                    with self.subTest(sigma=sigma_deg, incidence=incidence_deg, seed=seed):
                        self.assertGreater(float(directions[:, 2].min()), 0)
                        self.assertLess(cdf_distance(theta, reference), CDF_LIMIT)
                        np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1, atol=1e-14)

    def test_mixed_lobe_probability_and_pure_endpoints(self):
        for ratio in (0, 0.25, 0.6, 1):
            for seed in (42, 123, 20260921):
                _, lobes = samples(OpticalProfile("test", 0.6, scatter_model="mixed", specular_ratio=ratio,
                                                  diffuse_ratio=1-ratio, gaussian_sigma_deg=12), seed)
                fraction = lobes.count("gaussian") / SAMPLE_COUNT
                with self.subTest(ratio=ratio, seed=seed):
                    self.assertLessEqual(abs(fraction-ratio), 6 * math.sqrt(ratio*(1-ratio)/SAMPLE_COUNT))

    def test_specular_law_on_front_back_and_rotated_normals(self):
        profile = OpticalProfile("test", 0.6, scatter_model="specular")
        for normal in (np.array([0., 0., 1.]), np.array([0., 0., -1.]), np.array([0., 0.6, 0.8])):
            tangent = np.array([1., 0., 0.])
            for incidence_deg in (0, 30, 60, 85, 89):
                angle = math.radians(incidence_deg)
                incoming = tangent * math.sin(angle) - normal * math.cos(angle)
                expected = tangent * math.sin(angle) + normal * math.cos(angle)
                sample = sample_reflection_direction(random.Random(42), tuple(incoming), tuple(normal), profile)
                np.testing.assert_allclose(sample.direction, expected, atol=1e-14)

    def test_incidence_correction_matches_declared_equation_not_constant_base(self):
        for reflectance in (0, 0.03, 0.14, 0.6, 0.95, 0.999, 1):
            for roughness in (0, 0.5, 1):
                for incidence_deg in (0, 30, 60, 85, 89):
                    angle = math.radians(incidence_deg)
                    incoming = (math.sin(angle), 0, -math.cos(angle))
                    profile = OpticalProfile("test", reflectance, roughness=roughness)
                    expected = reflectance + (1-reflectance) * max(0, 1-math.cos(angle)/0.7)**5 * (1-0.75*roughness)
                    if reflectance == 0:
                        expected = 0
                    self.assertAlmostEqual(effective_surface_reflectance(incoming, (0, 0, 1), profile), expected, places=14)
                    self.assertEqual(effective_surface_reflectance(incoming, (0, 0, 1), profile, False), reflectance)


if __name__ == "__main__":
    unittest.main()
