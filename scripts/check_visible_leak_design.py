"""Standard-library design consistency checks; NOT production renderer validation."""

import json
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class VisibilityDesignChecks(unittest.TestCase):
    def test_contract_keeps_counts_and_luminance_separate(self):
        contract = json.loads(
            (ROOT / "docs/design/visible-leak-contract-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(contract["status"], "design_only_not_production_api")
        self.assertNotEqual(contract["measure"], contract["legacy_measure"])
        rules = contract["sampling_rules"]
        self.assertTrue(rules["explore_zero_cells"])
        self.assertTrue(rules["independent_confirmation_seed"])
        self.assertFalse(rules["zero_sample_variance_proves_absence"])
        self.assertFalse(rules["reverse_physical_emission_for_efficiency"])
        self.assertFalse(rules["legacy_counts_convert_to_nits"])
        for example in contract["examples"]:
            self.assertIn(example["execution_state"], contract["execution_states"])
            self.assertIn(example["observation_state"], contract["observation_states"])
            self.assertFalse(example["physical_validation_complete"])
            if example["execution_state"] != "completed":
                self.assertFalse(example["coverage_complete"])

    def test_nonreciprocal_forward_evaluation_and_proposal_weights(self):
        # Rows: incoming direction. Columns: outgoing direction.
        brdf = ((0.2, 0.8), (0.05, 0.3))
        incoming = (100.0, 10.0)
        integration_weights = (0.5, 0.5)
        proposal = (0.4, 0.6)
        reference = sum(brdf[i][0] * incoming[i] * integration_weights[i] for i in range(2))
        weighted = sum(
            proposal[i] * (brdf[i][0] * incoming[i] * integration_weights[i] / proposal[i])
            for i in range(2)
        )
        wrong_transpose = sum(brdf[0][i] * incoming[i] * integration_weights[i] for i in range(2))
        self.assertAlmostEqual(reference, 10.25, delta=1e-10)
        self.assertAlmostEqual(weighted, reference, delta=1e-10)
        self.assertAlmostEqual(wrong_transpose, 14.0, delta=1e-10)
        self.assertNotEqual(reference, wrong_transpose)
        # Blocking the first direction leaves only the second contribution.
        self.assertAlmostEqual(brdf[1][0] * incoming[1] * integration_weights[1], 0.25)

    def test_source_flux_contract(self):
        flux, area = 100.0, 0.01  # lm, m^2
        lambertian_luminance = flux / (math.pi * area)
        self.assertAlmostEqual(lambertian_luminance * math.pi * area, flux, delta=1e-10)
        # Uniform angular density has direction-dependent luminance.
        uniform_normal = flux / (area * 2 * math.pi)
        uniform_at_sixty = flux / (area * 2 * math.pi * math.cos(math.radians(60)))
        self.assertAlmostEqual(uniform_at_sixty / uniform_normal, 2.0, delta=1e-10)

    def test_rear_angle_roundtrip_and_unconditional_midpoint(self):
        # Rear datum: w=-Z, right=+X, up=+Y.
        h, v = math.radians(52.5), math.radians(7.5)
        x, y, z = math.sin(h) * math.cos(v), math.sin(v), -math.cos(h) * math.cos(v)
        self.assertAlmostEqual(math.degrees(math.atan2(x, -z)), 52.5, delta=1e-10)
        self.assertAlmostEqual(math.degrees(math.asin(y)), 7.5, delta=1e-10)
        self.assertEqual(((45 + 60) / 2, (0 + 15) / 2), (52.5, 7.5))
        self.assertAlmostEqual(x*x + y*y + z*z, 1.0, delta=1e-10)

    def test_finite_rejection_sampler_preserves_delta_mass(self):
        for acceptance in (0.0, 0.01, 0.5, 1.0):
            delta_mass = (1 - acceptance) ** 32
            continuous_mass = 1 - delta_mass
            self.assertAlmostEqual(continuous_mass + delta_mass, 1.0, delta=1e-10)
            if acceptance > 0:
                density_multiplier = continuous_mass / acceptance
                self.assertAlmostEqual(acceptance * density_multiplier, continuous_mass, delta=1e-10)
        self.assertGreater((1 - 0.01) ** 32, 0.7)


if __name__ == "__main__":
    print("Design math/contract checks only; no product or physical-accuracy acceptance claim.", flush=True)
    unittest.main(verbosity=2)
