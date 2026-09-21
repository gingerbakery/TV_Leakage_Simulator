import unittest

import numpy as np

from leakage_simulator.fast_sampling import iter_virtual_plane_ray_batches
from leakage_simulator.raytrace_bridge import build_direct_trace_input
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import EmitterSpec


def two_sided_reflection_input(emitter_type="datum_plane", count=128, basis="initial_ray_fraction"):
    scene = {"vertices": [], "faces": [], "face_component_ids": [], "face_material_ids": []}
    for component_id, height in enumerate((0, 10, -10)):
        start = len(scene["vertices"])
        extent = 0.05 if component_id == 0 else 20
        scene["vertices"].extend([[-extent, -extent, height], [extent, -extent, height],
                                  [extent, extent, height], [-extent, extent, height]])
        scene["faces"].extend([[start, start + 1, start + 2], [start, start + 2, start + 3]])
        scene["face_component_ids"].extend([component_id] * 2)
        scene["face_material_ids"].extend(["default"] * 2)
    payload = {
        "excluded_component_ids": [0],
        "emitters": [{
            "emitter_id": "both", "emitter_type": emitter_type, "face_indices": [0, 1] if emitter_type == "face" else [],
            "center": [0, 0, 0], "width_mm": 0.1, "height_mm": 0.1,
            "u_axis": [1, 0, 0], "v_axis": [0, 1, 0],
            "emission_direction": "both", "direction_distribution": "gaussian", "gaussian_sigma_deg": 0.01,
            "power_lumen": 1e-12, "ray_count": count, "seed": 42,
        }],
        "receivers": [{
            "receiver_id": name, "center": [0, 0, height], "normal": [0, 0, normal],
            "width_mm": 100, "height_mm": 100, "resolution": [4, 4],
        } for name, height, normal in (("front", 2, 1), ("back", -2, -1))],
        "optical_profiles": [
            {"profile_id": "default", "reflectance": 0.1, "scatter_model": "specular"},
            {"profile_id": "front", "reflectance": 0.9, "scatter_model": "specular"},
            {"profile_id": "back", "reflectance": 0.6, "scatter_model": "specular"},
        ],
        "optical_assignments": [{
            "assignment_id": name, "target_type": "faces", "component_id": component_id,
            "face_indices": faces, "profile_id": name,
        } for name, component_id, faces in (("front", 1, [2, 3]), ("back", 2, [4, 5]))],
        "config": {"max_depth": 1, "min_energy_basis": basis, "min_energy": 1e-9,
                   "angle_dependent_reflectance": False, "store_ray_paths": False, "contribution_mode": "summary"},
    }
    return build_direct_trace_input(scene, payload)


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

    def test_both_sides_keep_face_overrides_and_relative_termination_after_exclusion(self):
        for emitter_type in ("face", "datum_plane"):
            for dispatch in ("scalar", "batch"):
                with self.subTest(emitter_type=emitter_type, dispatch=dispatch):
                    trace_input = two_sided_reflection_input(emitter_type)
                    result = run_direct_ray_trace(trace_input, intersection_dispatch=dispatch)
                    self.assertEqual(result.receiver_hit_count, 128)
                    for name, reflectance in (("front", 0.9), ("back", 0.6)):
                        metrics = result.metrics[name]
                        self.assertGreater(metrics["hit_count"], 0)
                        expected = metrics["hit_count"] * 1e-12 / 128 * reflectance
                        self.assertAlmostEqual(metrics["total_flux_lumen"] / expected, 1, places=11)
                    legacy = two_sided_reflection_input(emitter_type, basis="absolute_lumen")
                    self.assertEqual(run_direct_ray_trace(legacy, intersection_dispatch=dispatch).receiver_hit_count, 0)


if __name__ == "__main__":
    unittest.main()
