from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "samples"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_rt_validation_models import definitions
from verify_rt_validation_models import gaussian_front_probability, lambertian_square_probability
from leakage_simulator.types import EmitterSpec, RayTraceConfig, ReceiverSpec


class ValidationModelDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models, cls.cases = definitions()

    def test_all_models_have_valid_named_solids(self):
        self.assertEqual(len(self.models), 10)
        for model_id, parts in self.models.items():
            with self.subTest(model=model_id):
                self.assertEqual(len({name for name, _ in parts}), len(parts))
                for name, body in parts:
                    self.assertTrue(body.val().isValid(), name)
                    self.assertEqual(len(body.val().Solids()), 1, name)
                    self.assertGreater(body.val().Volume(), 0)

    def test_cases_share_one_lumen_and_explicit_physics(self):
        self.assertEqual(len(self.cases), 25)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(set(case["component_profiles"]),
                                 {name for name, _ in self.models[Path(case["model"]).stem]})
                config = RayTraceConfig.from_dict(case["config"])
                self.assertFalse(config.angle_dependent_reflectance)
                self.assertEqual(config.primary_sampling_strategy, "source")
                for value in case["emitters"]:
                    emitter = EmitterSpec.from_dict(value)
                    self.assertEqual(emitter.power_lumen, 1)
                    self.assertEqual(emitter.power_mode, "total")
                for value in case["receivers"]:
                    receiver = ReceiverSpec.from_dict(value)
                    self.assertEqual(receiver.acceptance_angle_deg, 90)

    def test_corridors_require_the_named_reflection_count(self):
        for case in self.cases:
            if "corridor" not in case["id"]:
                continue
            reflections = case["expected"]["reflections"]
            self.assertEqual(case["receivers"][0]["center"][0], reflections * 2)
            self.assertAlmostEqual(case["expected"]["flux_lumen"], 0.999**reflections)

    def test_chamber_has_six_inward_receiver_frames(self):
        case = next(item for item in self.cases if item["id"] == "10_source_sphere_full")
        self.assertEqual(len(case["receivers"]), 6)
        for receiver in case["receivers"]:
            horizontal, vertical, normal = (receiver[key] for key in ("u_axis", "v_axis", "normal"))
            cross = [horizontal[1] * vertical[2] - horizontal[2] * vertical[1],
                     horizontal[2] * vertical[0] - horizontal[0] * vertical[2],
                     horizontal[0] * vertical[1] - horizontal[1] * vertical[0]]
            self.assertEqual(list(normal), cross)
            self.assertEqual(sum(position * direction for position, direction in zip(receiver["center"], normal)), -19)

    def test_lambertian_quadrature_matches_closed_point_formula(self):
        for half_size, distance in ((5, 10), (19, 19)):
            closed_form = 4 / math.pi * half_size / math.sqrt(half_size**2 + distance**2) * math.atan(half_size / math.sqrt(half_size**2 + distance**2))
            probability = lambertian_square_probability(half_size, distance, source_size=0)
            self.assertIs(type(probability), float)
            self.assertAlmostEqual(probability, closed_form, places=12)

    def test_gaussian_width_reduces_front_capture(self):
        values = [gaussian_front_probability(sigma) for sigma in (3, 12, 30)]
        self.assertAlmostEqual(values[0], 1)
        self.assertGreater(values[0], values[1])
        self.assertGreater(values[1], values[2])


if __name__ == "__main__":
    unittest.main()
