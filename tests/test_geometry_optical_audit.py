from __future__ import annotations

from dataclasses import replace
import math
import unittest

import numpy as np

from test_multibounce_rt3 import two_bounce_input
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import run_direct_ray_trace


def transformed_two_bounce(scale, translation, rotated, ray_count=128):
    trace_input = two_bounce_input(2, ray_count, min_energy=0, store_paths=False)
    diagonal = math.sqrt(0.5)
    rotation = np.array([[diagonal, -diagonal, 0], [0, 0, 1], [-diagonal, -diagonal, 0]]) if rotated else np.eye(3)
    offset = np.array([translation, -translation, translation])

    def point(value):
        return tuple(rotation @ np.asarray(value) * scale + offset)

    def direction(value):
        return tuple(rotation @ np.asarray(value))

    mesh = TriangleMesh()
    for vertex in trace_input.mesh.vertices:
        mesh.add_vertex(point(vertex))
    for index, face in enumerate(trace_input.mesh.faces):
        mesh.add_face(face.v0, face.v1, face.v2, trace_input.mesh.material_id(index), dict(trace_input.mesh.metadata(index)))
    trace_input.mesh = mesh
    trace_input.emitters = [replace(emitter, center=point(emitter.center), u_axis=direction(emitter.u_axis),
                                    v_axis=direction(emitter.v_axis), width_mm=emitter.width_mm*scale,
                                    height_mm=emitter.height_mm*scale) for emitter in trace_input.emitters]
    trace_input.receivers = [replace(receiver, center=point(receiver.center), normal=direction(receiver.normal),
                                     width_mm=receiver.width_mm*scale, height_mm=receiver.height_mm*scale)
                             for receiver in trace_input.receivers]
    trace_input.config.epsilon_mm *= scale
    trace_input.config.angle_dependent_reflectance = False
    trace_input.optical_profiles = [replace(profile, reflectance=value) for profile, value in
                                    zip(trace_input.optical_profiles, (0.6, 0.3))]
    return trace_input


class GeometryOpticalAuditTests(unittest.TestCase):
    def test_rotation_translation_scale_and_bvh_preserve_two_reflection_flux(self):
        for scale in (0.01, 1, 100):
            for translation in (0, 1e6):
                for rotated in (False, True):
                    for backend in ("bvh", "brute_force"):
                        with self.subTest(scale=scale, translation=translation, rotated=rotated, backend=backend):
                            trace_input = transformed_two_bounce(scale, translation, rotated)
                            trace_input.config.intersection_backend = backend
                            result = run_direct_ray_trace(trace_input, intersection_dispatch="scalar",
                                                          intersection_provider="python_cpu")
                            self.assertEqual(result.receiver_hit_count, 128)
                            self.assertEqual(result.surface_hit_count, 256)
                            self.assertAlmostEqual(result.metrics["observer"]["total_flux_lumen"], 0.18, places=12)


if __name__ == "__main__":
    unittest.main()
