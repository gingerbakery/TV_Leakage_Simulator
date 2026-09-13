from __future__ import annotations

from dataclasses import replace
import unittest

import numpy as np

from test_emitter_aim import add_plane, aim_scene
from leakage_simulator.aim_sampling import validate_emitter_aim
from leakage_simulator.raytracer import run_direct_ray_trace


def proximity_scene(*, emitter_type="datum_plane", shape="rectangle", rays=512,
                    tilted=False, separation_mm=1e-5):
    scene = aim_scene(emitter_type=emitter_type, shape=shape, rays=rays)
    target = scene.emitters[0].aim
    target.width_mm = target.height_mm = 0.04
    target.radius_mm = 0.02
    if tilted:
        target.center = (3, 0, 0.1)
        target.u_axis, target.v_axis = (0, 1, 0), (0, 0, 1)
        receiver_height = 0.2
    else:
        target.center = (3, 0, separation_mm)
        receiver_height = 2 * separation_mm
    scene.receivers[0].center = (3, 0, receiver_height)
    return scene


class EmitterAimProximityTests(unittest.TestCase):
    def test_close_parallel_and_tilted_targets_preserve_flux(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            for shape in ("rectangle", "circle"):
                for tilted in (False, True):
                    with self.subTest(emitter=emitter_type, shape=shape, tilted=tilted):
                        scene = proximity_scene(emitter_type=emitter_type, shape=shape, tilted=tilted)
                        result = run_direct_ray_trace(scene)
                        self.assertEqual(result.receiver_hit_count, 512)
                        self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 1, places=12)

    def test_close_target_scalar_path_preserves_flux(self):
        for tilted in (False, True):
            result = run_direct_ray_trace(proximity_scene(tilted=tilted), intersection_dispatch="scalar")
            self.assertEqual(result.receiver_hit_count, 512)
            self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 1, places=12)

    def test_clearance_error_is_explicit_and_tied_to_ray_epsilon(self):
        for epsilon in (1e-6, 1e-4):
            for separation in (0, epsilon, 2 * epsilon):
                scene = proximity_scene(separation_mm=separation)
                with self.subTest(epsilon=epsilon, separation=separation):
                    with self.assertRaisesRegex(ValueError, f"within the {2 * epsilon:g} mm numerical clearance"):
                        validate_emitter_aim(scene.emitters[0], scene.mesh, epsilon)
            scene = proximity_scene(separation_mm=3 * epsilon)
            validate_emitter_aim(scene.emitters[0], scene.mesh, epsilon)

    def test_actual_tilted_intersection_and_contact_are_rejected(self):
        for emitter_type in ("face", "datum_plane", "reference_plane"):
            for shape in ("rectangle", "circle"):
                for center_height in (0, 0.02):
                    scene = proximity_scene(emitter_type=emitter_type, shape=shape, tilted=True)
                    scene.emitters[0].aim.center = (3, 0, center_height)
                    with self.subTest(emitter=emitter_type, shape=shape, height=center_height):
                        with self.assertRaisesRegex(ValueError, "Target area overlaps or touches"):
                            validate_emitter_aim(scene.emitters[0], scene.mesh, scene.config.epsilon_mm)

    def test_coplanar_disjoint_patches_are_not_infinite_planes(self):
        for shape in ("rectangle", "circle"):
            scene = proximity_scene(shape=shape)
            scene.emitters[0].aim.center = (10, 0, 0)
            validate_emitter_aim(scene.emitters[0], scene.mesh, scene.config.epsilon_mm)

    def test_separate_cad_faces_on_both_sides_do_not_create_a_filled_hull(self):
        scene = proximity_scene(emitter_type="face")
        face_ids = add_plane(scene.mesh, (3, 0, 2), 1, 1, "source")
        scene.emitters[0].face_indices.extend(face_ids)
        scene.emitters[0].aim.center = (3, 0, 1)
        validate_emitter_aim(scene.emitters[0], scene.mesh, scene.config.epsilon_mm)

    def test_circle_does_not_use_its_rectangular_bounding_box_as_the_target(self):
        scene = proximity_scene(shape="circle")
        emitter = scene.emitters[0]
        emitter.center = (0.9, 0.9, 0)
        emitter.width_mm = emitter.height_mm = 0.1
        emitter.aim.center = (0, 0, 0)
        emitter.aim.radius_mm = 1
        validate_emitter_aim(emitter, scene.mesh, scene.config.epsilon_mm)
        emitter.aim = replace(emitter.aim, shape="rectangle", width_mm=2, height_mm=2)
        with self.assertRaisesRegex(ValueError, "overlaps or touches"):
            validate_emitter_aim(emitter, scene.mesh, scene.config.epsilon_mm)

    def test_circle_inside_large_triangle_is_detected_without_edge_crossings(self):
        scene = proximity_scene(shape="circle")
        scene.emitters[0].aim.center = (3.2, -0.2, 0)
        with self.assertRaisesRegex(ValueError, "overlaps or touches"):
            validate_emitter_aim(scene.emitters[0], scene.mesh, scene.config.epsilon_mm)

    def test_rigid_rotation_and_translation_do_not_change_clearance_decisions(self):
        rotation = np.asarray(((0, 0, 1), (1, 0, 0), (0, 1, 0)), dtype=float)
        translation = np.asarray((5000, -300, 2000), dtype=float)
        for overlaps in (False, True):
            scene = proximity_scene(tilted=True, shape="circle")
            emitter = scene.emitters[0]
            if overlaps:
                emitter.aim.center = (3, 0, 0)
            for surface in (emitter, emitter.aim):
                surface.center = tuple(rotation @ surface.center + translation)
                surface.u_axis = tuple(rotation @ surface.u_axis)
                surface.v_axis = tuple(rotation @ surface.v_axis)
            if overlaps:
                with self.assertRaisesRegex(ValueError, "overlaps or touches"):
                    validate_emitter_aim(emitter, scene.mesh, scene.config.epsilon_mm)
            else:
                validate_emitter_aim(emitter, scene.mesh, scene.config.epsilon_mm)


if __name__ == "__main__":
    unittest.main()
