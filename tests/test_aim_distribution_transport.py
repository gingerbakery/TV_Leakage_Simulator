"""CPU transport and portable-project checks for selectable Aim angular laws.

The distribution references below come from integrating intensity over solid
angle, independently of the emitter sampling implementation.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np

from leakage_simulator.api import ApiRuntime
from leakage_simulator.aim_sampling import validate_emitter_aim
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import EmitterAimSpec
from test_bitsam_package import project_fixture, scene_fixture, trace_request
from test_emitter_aim import aim_scene
from test_emitter_aim_sphere import sphere_scene


def target_plane_scene(mode, distribution, *, rays=12000, sigma=12.0):
    """A nearly point source with a normal-incidence receiver 2 mm away."""
    scene = aim_scene(rays=rays, power=3.25)
    emitter = scene.emitters[0]
    emitter.width_mm = emitter.height_mm = 1e-5
    emitter.aim = EmitterAimSpec(
        enabled=True, mode=mode, distribution=distribution,
        gaussian_sigma_deg=sigma, shape="circle", radius_mm=4,
        center=(3, 0, 2), sphere_upper_deg=0, sphere_lower_deg=60,
    )
    scene.receivers[0] = replace(
        scene.receivers[0], center=(3, 0, 2), width_mm=8,
        height_mm=8, resolution=(80, 80),
    )
    scene.config.store_ray_paths = False
    return scene


def received_disk_fraction(result, radius=1.0):
    grid = result.receiver_grids[0]
    receiver = result.receivers[0]
    columns, rows = grid.resolution
    x = (np.arange(columns) + 0.5) * receiver.width_mm / columns - receiver.width_mm / 2
    y = (np.arange(rows) + 0.5) * receiver.height_mm / rows - receiver.height_mm / 2
    disk = x[None, :] ** 2 + y[:, None] ** 2 <= radius ** 2
    flux = np.asarray(grid.flux_lumen)
    return float(flux[disk].sum() / flux.sum())


def gaussian_cone_fraction(inner, outer, sigma_deg):
    # dPhi = I(theta) * 2*pi*sin(theta)*dtheta. The common 2*pi cancels.
    sigma = math.radians(sigma_deg)
    def integral(limit):
        angles = np.linspace(0, limit, 20001)
        density = np.exp(-0.5 * (angles / sigma) ** 2) * np.sin(angles)
        return float(np.trapezoid(density, angles))
    return integral(inner) / integral(outer)


class AimDistributionTransportTests(unittest.TestCase):
    def test_area_rejects_backward_source_before_sampling_but_accepts_both_sides(self):
        for source_type in ("face", "datum_plane", "reference_plane"):
            for law in ("lambertian", "gaussian"):
                scene = aim_scene(emitter_type=source_type)
                emitter = scene.emitters[0]
                emitter.aim = replace(emitter.aim, distribution=law)
                emitter.normal_flip = True
                with self.subTest(source_type=source_type, law=law):
                    with self.assertRaisesRegex(ValueError, "no power.*emission direction"):
                        validate_emitter_aim(emitter, scene.mesh, scene.config.epsilon_mm)
                    emitter.emission_direction = "both"
                    validate_emitter_aim(emitter, scene.mesh, scene.config.epsilon_mm)

    def test_aim_sigma_and_sphere_support_validation(self):
        for sigma in (0, -1, math.nan, math.inf):
            with self.subTest(sigma=sigma), self.assertRaises(ValueError):
                EmitterAimSpec(distribution="gaussian", gaussian_sigma_deg=sigma)
        with self.assertRaisesRegex(ValueError, "Upper < 90"):
            EmitterAimSpec(mode="sphere", distribution="lambertian", sphere_upper_deg=90)

    def test_area_receiver_profile_matches_point_source_radiometry(self):
        inner = math.atan(1 / 2)
        outer = math.atan(4 / 2)
        expected = {
            "uniform_target_area": 1 / 16,
            "lambertian": math.sin(inner) ** 2 / math.sin(outer) ** 2,
            "gaussian": gaussian_cone_fraction(inner, outer, 12),
        }
        for distribution, fraction in expected.items():
            with self.subTest(distribution=distribution):
                result = run_direct_ray_trace(target_plane_scene("area", distribution))
                self.assertEqual(result.receiver_hit_count, 12000)
                self.assertEqual(result.surface_hit_count, 0)
                self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 3.25, places=10)
                # Includes both Monte Carlo variation and boundary-bin rounding.
                self.assertAlmostEqual(received_disk_fraction(result), fraction, delta=0.025)

    def test_sphere_receiver_profile_and_sigma_follow_selected_intensity(self):
        inner, outer = math.atan(1 / 2), math.radians(60)
        cases = [
            ("uniform_solid_angle", 12, (1 - math.cos(inner)) / (1 - math.cos(outer))),
            ("lambertian", 12, math.sin(inner) ** 2 / math.sin(outer) ** 2),
            ("gaussian", 8, gaussian_cone_fraction(inner, outer, 8)),
            ("gaussian", 24, gaussian_cone_fraction(inner, outer, 24)),
        ]
        for distribution, sigma, fraction in cases:
            with self.subTest(distribution=distribution, sigma=sigma):
                result = run_direct_ray_trace(target_plane_scene("sphere", distribution, sigma=sigma))
                self.assertEqual(result.receiver_hit_count, 12000)
                self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 3.25, places=10)
                self.assertAlmostEqual(received_disk_fraction(result), fraction, delta=0.025)

    def test_scalar_and_batch_transport_both_use_angular_area_and_sphere(self):
        for mode in ("area", "sphere"):
            for dispatch in ("scalar", "batch"):
                with self.subTest(mode=mode, dispatch=dispatch):
                    scene = target_plane_scene(mode, "gaussian", rays=1200, sigma=8)
                    result = run_direct_ray_trace(scene, intersection_dispatch=dispatch)
                    self.assertEqual(result.receiver_hit_count, 1200)
                    self.assertGreater(received_disk_fraction(result), 0.98)
                    self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 3.25, places=10)

    def test_all_source_types_keep_power_and_respect_reflection_and_occlusion(self):
        for mode in ("area", "sphere"):
            for distribution in ("lambertian", "gaussian"):
                for source_type in ("face", "datum_plane", "reference_plane"):
                    for scenario, expected in (("direct", 1), ("reflection", 0.8), ("blocked", 0)):
                        with self.subTest(mode=mode, distribution=distribution, source=source_type, scenario=scenario):
                            if mode == "sphere":
                                scene = sphere_scene(source_type, rays=128, scenario="cone" if scenario == "direct" else scenario)
                            else:
                                scene = aim_scene(emitter_type=source_type, rays=128,
                                    reflection=scenario == "reflection", blocker=scenario == "blocked")
                            scene.emitters[0].aim = replace(scene.emitters[0].aim,
                                distribution=distribution, gaussian_sigma_deg=18)
                            scene.config.primary_sampling_strategy = "receiver_mis"
                            result = run_direct_ray_trace(scene)
                            self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], expected, places=11)
                            if scenario == "reflection":
                                self.assertEqual(result.contribution_summary.direct_receiver_hit_count, 0)
                                self.assertEqual(result.contribution_summary.reflected_receiver_hit_count, 128)

    def test_scalar_face_reflections_keep_selected_source_law_and_attenuation(self):
        for mode in ("area", "sphere"):
            for distribution in ("lambertian", "gaussian"):
                with self.subTest(mode=mode, distribution=distribution):
                    scene = (aim_scene(emitter_type="face", reflection=True, rays=128)
                             if mode == "area" else sphere_scene("face", rays=128, scenario="reflection"))
                    scene.emitters[0].aim = replace(scene.emitters[0].aim,
                        distribution=distribution, gaussian_sigma_deg=18)
                    result = run_direct_ray_trace(scene, intersection_dispatch="scalar")
                    self.assertEqual(result.receiver_hit_count, 128)
                    self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.8, places=11)

    def test_luminance_input_power_is_not_multiplied_by_aim_acceptance(self):
        for mode in ("area", "sphere"):
            for distribution in ("lambertian", "gaussian"):
                with self.subTest(mode=mode, distribution=distribution):
                    scene = aim_scene(rays=256)
                    emitter = scene.emitters[0]
                    emitter.power_mode = "set_luminance"
                    emitter.luminance_nit = 500
                    emitter.aim = replace(emitter.aim, mode=mode,
                        distribution=distribution, gaussian_sigma_deg=15, sphere_lower_deg=5)
                    result = run_direct_ray_trace(scene)
                    self.assertEqual(result.receiver_hit_count, 256)
                    self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], math.pi * 500 * 1e-6, places=12)

    def test_new_aim_distribution_and_sigma_survive_portable_save_reload_retrace(self):
        for mode in ("area", "sphere"):
            for distribution in ("lambertian", "gaussian"):
                with self.subTest(mode=mode, distribution=distribution), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    source = root / "mirror.step"
                    source.write_bytes(b"ISO-10303-21; test scene is provided by the cache")
                    first = ApiRuntime(root / "before", scene_loader=scene_fixture)
                    loaded = first.load_scene(str(source))
                    request = trace_request(loaded["metadata"]["scene_token"])
                    request["emitters"][0]["aim"] = {
                        "enabled": True, "mode": mode, "distribution": distribution,
                        "gaussian_sigma_deg": 7.25, "center": [0, 0, 10],
                        "width_mm": 2, "height_mm": 2, "sphere_lower_deg": 20,
                    }
                    initial = first.run_raytrace_direct(request)
                    project = project_fixture()
                    project["workspace"]["emitters"] = initial["emitters"]
                    project["analysis_result"] = initial
                    exported = first.export_project({"scene_token": request["scene_token"], "project": project})
                    package, _ = first.project_export_file(exported["download_url"].split("/")[-1])
                    source.unlink()
                    loader = Mock(side_effect=AssertionError("Portable Aim restore must reuse cached geometry"))
                    second = ApiRuntime(root / "after", scene_loader=loader)
                    restored = second.import_project(package)
                    cached = second.load_scene(restored["cad"]["path"])
                    request["scene_token"] = cached["metadata"]["scene_token"]
                    request["emitters"] = copy.deepcopy(restored["project"]["workspace"]["emitters"])
                    actual_aim = request["emitters"][0]["aim"]
                    self.assertEqual(actual_aim["mode"], mode)
                    self.assertEqual(actual_aim["distribution"], distribution)
                    self.assertEqual(actual_aim["gaussian_sigma_deg"], 7.25)
                    self.assertEqual(request["emitters"][0]["gaussian_sigma_deg"], 1)
                    repeated = second.run_raytrace_direct(request)
                    self.assertEqual(repeated["receiver_grids"], initial["receiver_grids"])
                    self.assertEqual(repeated["stored_paths"], initial["stored_paths"])
                    self.assertGreater(repeated["metrics"]["observer"]["total_flux_lumen"], 0)
                    loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
