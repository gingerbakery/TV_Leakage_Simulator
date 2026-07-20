from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.optics import OpticalPropertyResolver, UNASSIGNED_PROFILE_ID
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    OpticalAssignment,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


def add_surface(
    mesh: TriangleMesh,
    z: float,
    material_id: str,
    component_id: int,
    source_face_index: int,
) -> int:
    vertices = [
        mesh.add_vertex((-10.0, -10.0, z)),
        mesh.add_vertex((10.0, -10.0, z)),
        mesh.add_vertex((10.0, 10.0, z)),
        mesh.add_vertex((-10.0, 10.0, z)),
    ]
    face_index = mesh.add_face(
        vertices[0],
        vertices[1],
        vertices[2],
        material_id,
        {"component_id": component_id, "source_face_index": source_face_index},
    )
    mesh.add_face(
        vertices[0],
        vertices[2],
        vertices[3],
        material_id,
        {"component_id": component_id, "source_face_index": source_face_index + 10000},
    )
    return face_index


class OpticalResolverRT2BTests(unittest.TestCase):
    def test_face_override_has_priority_over_part_and_material(self) -> None:
        mesh = TriangleMesh()
        first_face = add_surface(mesh, 10.0, "mesh_profile", 7, 101)
        second_face = add_surface(mesh, 12.0, "mesh_profile", 7, 102)
        profiles = [
            OpticalProfile("mesh_profile", 0.05),
            OpticalProfile("part_profile", 0.20),
            OpticalProfile("face_profile", 0.80),
        ]
        assignments = [
            OpticalAssignment("part", "part", 7, "part_profile"),
            OpticalAssignment("face", "faces", 7, "face_profile", [101]),
        ]
        resolver = OpticalPropertyResolver(mesh, profiles, assignments)

        face_result = resolver.resolve(first_face)
        part_result = resolver.resolve(second_face)

        self.assertEqual(face_result.profile.profile_id, "face_profile")
        self.assertEqual(face_result.source, "face_override")
        self.assertEqual(part_result.profile.profile_id, "part_profile")
        self.assertEqual(part_result.source, "part_assignment")

    def test_material_then_default_then_unassigned_fallback(self) -> None:
        mesh = TriangleMesh()
        material_face = add_surface(mesh, 10.0, "mesh_profile", 1, 1)
        default_face = add_surface(mesh, 11.0, "unknown", 2, 2)

        material_resolver = OpticalPropertyResolver(
            mesh,
            [OpticalProfile("mesh_profile", 0.15), OpticalProfile("default", 0.02)],
            [],
        )
        unassigned_resolver = OpticalPropertyResolver(mesh, [], [])

        self.assertEqual(material_resolver.resolve(material_face).source, "mesh_material")
        self.assertEqual(material_resolver.resolve(default_face).source, "default")
        self.assertEqual(
            unassigned_resolver.resolve(default_face).profile.profile_id,
            UNASSIGNED_PROFILE_ID,
        )

    def test_profile_conserves_reflected_energy(self) -> None:
        profile = OpticalProfile(
            profile_id="mixed",
            reflectance=0.24,
            absorption=0.10,
            specular_ratio=2.0,
            diffuse_ratio=6.0,
            scatter_model="mixed",
        )

        self.assertAlmostEqual(profile.absorption, 0.76)
        self.assertAlmostEqual(profile.specular_ratio, 0.25)
        self.assertAlmostEqual(profile.diffuse_ratio, 0.75)

    def test_surface_event_records_resolved_profile_and_potential_reflection(self) -> None:
        mesh = TriangleMesh()
        add_surface(mesh, 10.0, "black_test", 9, 5)
        emitter = EmitterSpec(
            emitter_id="source",
            emitter_type="datum_plane",
            center=(0.0, 0.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=1.0,
            height_mm=1.0,
            direction_distribution="gaussian",
            gaussian_sigma_deg=0.1,
            power_lumen=1.0,
            ray_count=100,
            seed=41,
        )
        receiver = ReceiverSpec(
            receiver_id="receiver",
            center=(0.0, 0.0, 20.0),
            normal=(0.0, 0.0, -1.0),
            width_mm=30.0,
            height_mm=30.0,
        )
        result = run_direct_ray_trace(
            DirectRayTraceInput(
                mesh=mesh,
                emitters=[emitter],
                receivers=[receiver],
                optical_profiles=[
                    OpticalProfile(
                        "black_test",
                        0.20,
                        specular_ratio=0.10,
                        diffuse_ratio=0.90,
                        scatter_model="gaussian",
                    )
                ],
                config=RayTraceConfig(
                    ray_count=100,
                    max_depth=0,
                    store_ray_paths=True,
                    max_stored_paths=2,
                ),
            )
        )

        surface_event = result.stored_paths[0][-1]
        optical_summary = result.metrics["_optical_summary"]
        self.assertEqual(surface_event.optical_profile_id, "black_test")
        self.assertAlmostEqual(surface_event.reflectance, 0.20)
        self.assertAlmostEqual(surface_event.outgoing_energy_lumen, 0.002)
        self.assertEqual(optical_summary["profile_hits"]["black_test"]["hit_count"], 100)
        self.assertAlmostEqual(
            optical_summary["profile_hits"]["black_test"]["potential_reflected_flux_lumen"],
            0.20,
        )


if __name__ == "__main__":
    unittest.main()
