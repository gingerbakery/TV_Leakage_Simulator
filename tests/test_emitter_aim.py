from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import random
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.aim_sampling import sample_aim_ray, sample_aim_ray_batch
from leakage_simulator.fast_sampling import (
    build_face_emitter_batch_geometry,
    iter_face_emitter_ray_batches,
    iter_virtual_plane_ray_batches,
)
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterAimSpec, EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec


def add_plane(mesh, center, width, height, material):
    vertices = [
        mesh.add_vertex((center[0] + sign_u * width / 2, center[1] + sign_v * height / 2, center[2]))
        for sign_u, sign_v in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]
    face_ids = []
    for corners in ((0, 1, 2), (0, 2, 3)):
        face_ids.append(len(mesh.faces))
        mesh.add_face(*(vertices[index] for index in corners), material)
    return face_ids


def aim_scene(*, emitter_type="datum_plane", shape="rectangle", rays=4096,
              reflection=False, blocker=False, backend="cpu", power=1.0):
    mesh = TriangleMesh()
    face_ids = add_plane(mesh, (3.0, 0.0, 0.0), 1.0, 1.0, "source") if emitter_type == "face" else []
    if reflection:
        add_plane(mesh, (0.0, 0.0, 10.0), 20.0, 20.0, "mirror")
    if blocker:
        add_plane(mesh, (0.0, 0.0, 5.0), 40.0, 40.0, "blocker")
    if not mesh.faces:
        add_plane(mesh, (0.0, 0.0, -20.0), 1.0, 1.0, "blocker")
    emitter = EmitterSpec(
        emitter_id="aim_source", emitter_type=emitter_type, face_indices=face_ids,
        center=(3.0, 0.0, 0.0), u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, 1.0, 0.0),
        width_mm=1.0, height_mm=1.0, power_lumen=power, ray_count=rays,
        seed=72017, direction_distribution="lambertian",
        aim=EmitterAimSpec(enabled=True, shape=shape, center=(0.0, 0.0, 10.0), width_mm=4.0, height_mm=2.0, radius_mm=1.0),
    )
    if emitter_type == "reference_plane":
        emitter.surface_construction = "polygon_auto"
        emitter.polygon_vertices = [(2.5, -0.5, 0.0), (3.5, -0.5, 0.0), (3.0, 0.5, 0.0)]
    receiver = ReceiverSpec(
        receiver_id="observer", center=(0.0, 0.0, -5.0 if reflection else 20.0),
        normal=(0.0, 0.0, 1.0 if reflection else -1.0),
        u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, 1.0, 0.0),
        width_mm=24.0, height_mm=24.0, resolution=(12, 12),
    )
    return DirectRayTraceInput(
        mesh=mesh, emitters=[emitter], receivers=[receiver],
        optical_profiles=[OpticalProfile("source", 0.0), OpticalProfile("blocker", 0.0), OpticalProfile("mirror", 0.8, scatter_model="specular")],
        config=RayTraceConfig(max_depth=2, min_energy=1e-15, epsilon_mm=1e-6,
            compute_backend=backend, intersection_backend="bvh", angle_dependent_reflectance=False,
            store_ray_paths=True, max_stored_paths=20),
    )


class EmitterAimTests(unittest.TestCase):
    def test_contract_round_trip_and_legacy_off(self):
        emitter = aim_scene().emitters[0]
        restored = EmitterSpec.from_dict(emitter.to_dict())
        self.assertEqual(restored, emitter)
        legacy = emitter.to_dict()
        del legacy["aim"]
        self.assertIsNone(EmitterSpec.from_dict(legacy).aim)

    def test_contract_rejects_invalid_fields(self):
        for invalid in (
            {"shape": "triangle"}, {"width_mm": 0}, {"radius_mm": -1},
            {"height_mm": math.inf}, {"center": (math.nan, 0, 0)},
            {"v_axis": (1, 0, 0)}, {"u_axis": (0, 0, 0)},
            {"enabled": "false"}, {"distribution": "lambertian"},
            {"power_reference": "whole_sphere"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                EmitterAimSpec(**invalid)

    def test_tilted_target_uniform_area_and_normalized_directions(self):
        for shape in ("rectangle", "circle"):
            aim = EmitterAimSpec(enabled=True, shape=shape, center=(10, 3, 5),
                u_axis=(0, 1, 0), v_axis=(0, 0, 1), width_mm=4, height_mm=2, radius_mm=2)
            origins, directions = sample_aim_ray_batch(np.random.default_rng(17), np.zeros((60000, 3)), aim, 1e-6)
            distances = (10 - origins[:, 0]) / directions[:, 0]
            targets = origins + distances[:, None] * directions
            offsets = targets - aim.center
            self.assertTrue(np.all(distances > 0))
            np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1, atol=1e-14)
            np.testing.assert_allclose(offsets[:, 0], 0, atol=1e-12)
            self.assertLess(abs(float(np.mean(offsets[:, 1]))), 0.02)
            if shape == "rectangle":
                self.assertLessEqual(np.max(np.abs(offsets[:, 1])), 2)
                self.assertLessEqual(np.max(np.abs(offsets[:, 2])), 1)
                self.assertAlmostEqual(float(np.mean(offsets[:, 1] ** 2)), 4 / 3, delta=0.03)
            else:
                radius_squared = offsets[:, 1] ** 2 + offsets[:, 2] ** 2
                self.assertLessEqual(float(np.max(radius_squared)), 4 + 1e-12)
                self.assertAlmostEqual(float(np.mean(radius_squared)), 2, delta=0.03)

    def test_scalar_targets_are_inside_circle(self):
        aim = EmitterAimSpec(enabled=True, shape="circle", radius_mm=2)
        generator = random.Random(7)
        for _ in range(100):
            origin, direction = sample_aim_ray(generator, (0, 0, 0), aim, 1e-6)
            distance = (aim.center[2] - origin[2]) / direction[2]
            point = np.asarray(origin) + distance * np.asarray(direction)
            self.assertLessEqual(np.linalg.norm(point[:2]), 2 + 1e-12)

    def test_aim_off_preserves_existing_random_stream(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            scene = aim_scene(emitter_type=emitter_type, rays=20)
            emitter = replace(scene.emitters[0], aim=None)
            disabled = replace(emitter, aim=EmitterAimSpec(enabled=False))
            if emitter_type == "face":
                geometry = build_face_emitter_batch_geometry(scene.mesh, emitter)
                first = list(iter_face_emitter_ray_batches(emitter, geometry, 1e-6, 1))
                second = list(iter_face_emitter_ray_batches(disabled, geometry, 1e-6, 1))
            else:
                first = list(iter_virtual_plane_ray_batches(emitter, 1e-6, 1))
                second = list(iter_virtual_plane_ray_batches(disabled, 1e-6, 1))
            for batch_first, batch_second in zip(first, second):
                for values_first, values_second in zip(batch_first, batch_second):
                    np.testing.assert_array_equal(values_first, values_second)

    def test_target_does_not_act_as_a_receiver_or_blocker(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            for shape in ("rectangle", "circle"):
                with self.subTest(emitter_type=emitter_type, shape=shape):
                    result = run_direct_ray_trace(aim_scene(emitter_type=emitter_type, shape=shape, rays=512))
                    self.assertEqual(result.receiver_hit_count, 512)
                    self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 1.0, places=12)
                    self.assertEqual(result.metrics["_performance_summary"]["aim_emitter_count"], 1)

    def test_specular_reflection_attenuates_without_reaiming(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            result = run_direct_ray_trace(aim_scene(emitter_type=emitter_type, reflection=True, rays=512))
            self.assertEqual(result.receiver_hit_count, 512)
            self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.8, places=11)

    def test_intermediate_occluder_is_not_bypassed(self):
        result = run_direct_ray_trace(aim_scene(blocker=True, rays=512))
        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(result.metrics["observer"]["total_flux_lumen"], 0.0)

    def test_aim_power_is_conserved_and_set_luminance_is_not_rescaled(self):
        scene = aim_scene(rays=512)
        scene.emitters[0].power_mode = "set_luminance"
        scene.emitters[0].luminance_nit = 500
        expected = math.pi * 500 * 1e-6
        for width in (2, 4, 8):
            scene.emitters[0].aim.width_mm = width
            result = run_direct_ray_trace(scene)
            self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], expected, places=12)

    def test_primary_receiver_mis_cannot_redirect_aim_rays(self):
        scene = aim_scene(reflection=True, rays=512)
        scene.config.primary_sampling_strategy = "receiver_mis"
        result = run_direct_ray_trace(scene)
        self.assertEqual(result.receiver_hit_count, 512)
        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.8, places=11)
        self.assertIn("aim_area_overrides_receiver_mis", str(result.metrics["_performance_summary"]))

    def test_overlapping_coplanar_target_is_rejected(self):
        scene = aim_scene()
        scene.emitters[0].aim.center = (3, 0, 0)
        with self.assertRaisesRegex(ValueError, "Target area overlaps or touches"):
            run_direct_ray_trace(scene)

    def test_scalar_trace_obeys_target(self):
        scene = aim_scene(emitter_type="face", reflection=True, rays=100)
        result = run_direct_ray_trace(scene, intersection_dispatch="scalar")
        self.assertEqual(result.receiver_hit_count, 100)
        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.8, places=11)


if __name__ == "__main__":
    unittest.main()
