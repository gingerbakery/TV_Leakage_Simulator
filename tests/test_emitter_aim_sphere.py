from __future__ import annotations

from dataclasses import replace
import math
import random
import unittest

import numpy as np

from leakage_simulator.aim_sampling import aim_sphere_rotation, sample_aim_ray, sample_aim_ray_batch, validate_emitter_aim
from leakage_simulator.fast_sampling import build_face_emitter_batch_geometry, iter_face_emitter_ray_batches, iter_virtual_plane_ray_batches
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.types import EmitterAimSpec, EmitterSpec, ReceiverSpec
from test_emitter_aim import aim_scene


def sphere_aim(**kwargs):
    return EmitterAimSpec(enabled=True, mode="sphere", distribution="uniform_solid_angle", **kwargs)


def sphere_scene(emitter_type="datum_plane", rays=8192, scenario="full"):
    scene = aim_scene(emitter_type=emitter_type, rays=rays,
                      reflection=scenario == "reflection", blocker=scenario == "blocked")
    scene.emitters[0].aim = sphere_aim()
    if scenario in ("reflection", "blocked", "cone"):
        scene.emitters[0].aim = sphere_aim(sphere_lower_deg=5, sphere_beta_deg=20)
        scene.receivers[0].width_mm = 60
        scene.receivers[0].height_mm = 60
    else:
        if scenario == "hemisphere":
            scene.emitters[0].aim = sphere_aim(sphere_lower_deg=90)
        elif scenario == "backward":
            scene.emitters[0].aim = sphere_aim(sphere_upper_deg=90)
        elif scenario == "annulus":
            scene.emitters[0].aim = sphere_aim(sphere_upper_deg=30, sphere_lower_deg=60)
        elif scenario == "collimated":
            scene.emitters[0].aim = sphere_aim(sphere_lower_deg=0, sphere_beta_deg=90)
        scene.receivers = []
        for axis in range(3):
            for sign in (-1, 1):
                center = [3.0, 0.0, 0.0]
                center[axis] += sign * 10
                normal = [0.0, 0.0, 0.0]
                normal[axis] = -sign
                scene.receivers.append(ReceiverSpec(
                    receiver_id=f"axis{axis}_{sign}", center=tuple(center), normal=tuple(normal),
                    width_mm=20, height_mm=20, resolution=(8, 8),
                ))
    return scene


class EmitterAimSphereTests(unittest.TestCase):
    def test_contract_round_trip_and_legacy_area(self):
        scene = sphere_scene()
        emitter = scene.emitters[0]
        self.assertEqual(EmitterSpec.from_dict(emitter.to_dict()), emitter)
        old = aim_scene().emitters[0].to_dict()
        for key in ("mode", "sphere_upper_deg", "sphere_lower_deg", "sphere_alpha_deg", "sphere_beta_deg"):
            old["aim"].pop(key)
        self.assertEqual(EmitterSpec.from_dict(old).aim.mode, "area")
        self.assertEqual(sphere_aim().sphere_lower_deg, 180)
        self.assertEqual(sphere_aim(width_mm=0).mode, "sphere")
        self.assertEqual(EmitterAimSpec(sphere_upper_deg=100, sphere_lower_deg=10).mode, "area")

    def test_rejects_invalid_bounds_and_mode_distribution_mismatch(self):
        for change in ({"sphere_upper_deg": -1}, {"sphere_lower_deg": 181},
                       {"sphere_upper_deg": 60, "sphere_lower_deg": 30},
                       {"sphere_upper_deg": 30, "sphere_lower_deg": 30},
                       {"sphere_alpha_deg": math.nan}, {"sphere_beta_deg": math.inf}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                sphere_aim(**change)
        with self.assertRaises(ValueError):
            EmitterAimSpec(mode="sphere")
        with self.assertRaises(ValueError):
            EmitterAimSpec(distribution="uniform_solid_angle")

    def test_full_sphere_is_uniform_in_solid_angle_and_bidirectional(self):
        points = np.tile([3.0, 2.0, 1.0], (100000, 1))
        origins, directions = sample_aim_ray_batch(np.random.default_rng(18), points, sphere_aim(), 1e-6)
        np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1, atol=1e-14)
        np.testing.assert_allclose(origins - 1e-6 * directions, points, atol=1e-14)
        np.testing.assert_allclose(np.mean(directions, axis=0), 0, atol=0.006)
        np.testing.assert_allclose(np.mean(directions ** 2, axis=0), 1 / 3, atol=0.004)
        self.assertAlmostEqual(float(np.mean(directions[:, 2] > 0)), 0.5, delta=0.006)
        histogram, _ = np.histogram(directions[:, 2], bins=np.linspace(-1, 1, 11))
        self.assertTrue(np.all(np.abs(histogram - 10000) < 400))

    def test_annulus_rotated_bounds_and_scalar_batch_formula_agree(self):
        aim = sphere_aim(sphere_upper_deg=20, sphere_lower_deg=50, sphere_alpha_deg=35, sphere_beta_deg=-60)
        rotation = np.asarray(aim_sphere_rotation(aim))
        _, directions = sample_aim_ray_batch(np.random.default_rng(41), np.zeros((20000, 3)), aim, 0)
        local = directions @ rotation
        polar = np.degrees(np.arccos(np.clip(local[:, 2], -1, 1)))
        self.assertGreaterEqual(float(polar.min()), 20 - 1e-10)
        self.assertLessEqual(float(polar.max()), 50 + 1e-10)
        self.assertAlmostEqual(float(local[:, 2].mean()), (math.cos(math.radians(20)) + math.cos(math.radians(50))) / 2, delta=0.002)
        class FixedRandom:
            def __init__(self):
                self.values = iter((0.3, 0.7))

            def random(self, count=None):
                value = next(self.values)
                return value if count is None else np.full(count, value)
        scalar = sample_aim_ray(FixedRandom(), (2, 3, 4), aim, 1e-6)
        batch = sample_aim_ray_batch(FixedRandom(), np.asarray([[2, 3, 4]]), aim, 1e-6)
        for single, array in zip(scalar, batch):
            np.testing.assert_allclose(single, array[0], atol=1e-14)

    def test_collimated_and_backward_sphere_do_not_need_target_separation(self):
        scene = sphere_scene()
        scene.emitters[0].aim = sphere_aim(center=scene.emitters[0].center, sphere_lower_deg=0, sphere_beta_deg=90)
        validate_emitter_aim(scene.emitters[0], scene.mesh, 1e-6)
        _, directions = sample_aim_ray_batch(np.random.default_rng(2), np.zeros((100, 3)), scene.emitters[0].aim, 1e-6)
        np.testing.assert_allclose(directions, np.tile([1, 0, 0], (100, 1)), atol=1e-14)
        for sample_index in range(100):
            _, direction = sample_aim_ray(random.Random(sample_index), (0, 0, 0), sphere_aim(sphere_upper_deg=90), 1e-6)
            self.assertLessEqual(direction[2], 1e-14)

    def test_all_existing_emitter_positions_and_disabled_stream_are_preserved(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            scene = sphere_scene(emitter_type, rays=2048)
            emitter = scene.emitters[0]
            def batches(source):
                if emitter_type == "face":
                    return list(iter_face_emitter_ray_batches(source, build_face_emitter_batch_geometry(scene.mesh, source), 1e-6, 42))
                return list(iter_virtual_plane_ray_batches(source, 1e-6, 42))
            for batch in batches(emitter):
                origins, directions = batch[:2]
                points = origins - directions * 1e-6
                np.testing.assert_allclose(points[:, 2], 0, atol=1e-14)
                self.assertTrue(np.all((points[:, 0] >= 2.5) & (points[:, 0] <= 3.5)))
                self.assertTrue(np.any(directions[:, 2] > 0))
                self.assertTrue(np.any(directions[:, 2] < 0))
            for expected, actual in zip(batches(replace(emitter, aim=None)), batches(replace(emitter, aim=replace(emitter.aim, enabled=False)))):
                for left, right in zip(expected, actual):
                    np.testing.assert_array_equal(left, right)

    def test_enclosing_receivers_conserve_total_flux_and_do_not_double_power(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            for scenario in ("full", "hemisphere", "backward", "annulus", "collimated"):
                with self.subTest(emitter_type=emitter_type, scenario=scenario):
                    scene = sphere_scene(emitter_type, rays=1024, scenario=scenario)
                    result = run_direct_ray_trace(scene)
                    self.assertEqual(result.receiver_hit_count, 1024)
                    self.assertAlmostEqual(sum(result.metrics[receiver.receiver_id]["total_flux_lumen"] for receiver in scene.receivers), 1, places=11)
                    if scenario == "full":
                        self.assertGreater(result.metrics["axis2_-1"]["total_flux_lumen"], 0.1)
                        self.assertGreater(result.metrics["axis2_1"]["total_flux_lumen"], 0.1)
                    if scenario == "hemisphere":
                        self.assertEqual(result.metrics["axis2_-1"]["total_flux_lumen"], 0)
                    if scenario == "backward":
                        self.assertEqual(result.metrics["axis2_1"]["total_flux_lumen"], 0)
                    self.assertEqual(result.metrics["_performance_summary"]["aim_sphere_emitter_count"], 1)

    def test_sphere_reflection_occlusion_and_mis_respect_source_distribution(self):
        for scenario, expected in (("cone", 1), ("blocked", 0), ("reflection", 0.8)):
            for emitter_type in ("face", "datum_plane", "reference_plane"):
                scene = sphere_scene(emitter_type, rays=512, scenario=scenario)
                scene.config.primary_sampling_strategy = "receiver_mis"
                result = run_direct_ray_trace(scene)
                self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], expected, places=11)
                self.assertIn("aim_sphere_overrides_receiver_mis", str(result.metrics["_performance_summary"]))

    def test_scalar_path_and_set_luminance_keep_total_flux(self):
        for dispatch in ("scalar", "batch"):
            scene = sphere_scene("face", rays=100)
            scene.emitters[0].power_mode = "set_luminance"
            scene.emitters[0].luminance_nit = 500
            result = run_direct_ray_trace(scene, intersection_dispatch=dispatch)
            self.assertEqual(result.receiver_hit_count, 100)
            self.assertAlmostEqual(sum(result.metrics[receiver.receiver_id]["total_flux_lumen"] for receiver in scene.receivers), math.pi * 500 * 1e-6, places=12)


if __name__ == "__main__":
    unittest.main()
