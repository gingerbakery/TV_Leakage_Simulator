from __future__ import annotations

import sys
import unittest
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, _sample_polygon_point, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec


def build_emitter_plane() -> TriangleMesh:
    mesh = TriangleMesh()
    v0 = mesh.add_vertex((-5.0, -5.0, 0.0))
    v1 = mesh.add_vertex((5.0, -5.0, 0.0))
    v2 = mesh.add_vertex((5.0, 5.0, 0.0))
    v3 = mesh.add_vertex((-5.0, 5.0, 0.0))
    mesh.add_face(v0, v1, v2, "black_pc_resin")
    mesh.add_face(v0, v2, v3, "black_pc_resin")
    return mesh


class RayTracerRT1Tests(unittest.TestCase):
    def test_receiver_preserves_explicit_plane_axes(self) -> None:
        receiver = ReceiverSpec(
            receiver_id="rotated_receiver",
            placement_mode="current_view",
            center=(10.0, 20.0, 30.0),
            normal=(0.0, -1.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 0.0, 1.0),
            width_mm=40.0,
            height_mm=20.0,
            view_distance_mm=100.0,
        )

        self.assertEqual(receiver.u_axis, (1.0, 0.0, 0.0))
        self.assertEqual(receiver.v_axis, (0.0, 0.0, 1.0))
        self.assertEqual(receiver.normal, (0.0, -1.0, 0.0))
        self.assertEqual(receiver.placement_mode, "current_view")

    def test_reference_receiver_contract_round_trip(self) -> None:
        receiver = ReceiverSpec(
            receiver_id="reference_receiver",
            display_name="Corner observer",
            placement_mode="reference_plane",
            center=(5.0, 6.0, 7.0),
            normal=(0.0, 0.0, 1.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=12.0,
            height_mm=8.0,
            resolution=(24, 16),
            acceptance_angle_deg=75.0,
            reference_mode="three_vertices",
            reference_vertex_indices=[1, 4, 9, 12, 15, 18],
            base_center=(4.0, 5.0, 6.0),
            base_u_axis=(1.0, 0.0, 0.0),
            base_v_axis=(0.0, 1.0, 0.0),
            base_normal=(0.0, 0.0, 1.0),
            position_offset_mm=(1.0, 1.0, 1.0),
            tilt_xyz_deg=(2.0, -3.0, 4.0),
        )

        restored = ReceiverSpec.from_dict(receiver.to_dict())

        self.assertEqual(restored.display_name, "Corner observer")
        self.assertEqual(restored.placement_mode, "reference_plane")
        self.assertEqual(restored.reference_vertex_indices, [1, 4, 9, 12, 15, 18])
        self.assertEqual(restored.resolution, (24, 16))
        self.assertEqual(restored.base_center, (4.0, 5.0, 6.0))
        self.assertEqual(restored.position_offset_mm, (1.0, 1.0, 1.0))
        self.assertEqual(restored.tilt_xyz_deg, (2.0, -3.0, 4.0))

    def test_direct_receiver_hit_from_face_emitter(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="face_source",
            face_indices=[0, 1],
            direction_distribution="gaussian",
            gaussian_sigma_deg=2.0,
            power_lumen=1.0,
            ray_count=300,
            seed=7,
        )
        receiver = ReceiverSpec(
            receiver_id="front_receiver",
            center=(0.0, 0.0, 20.0),
            normal=(0.0, 0.0, -1.0),
            width_mm=80.0,
            height_mm=80.0,
            resolution=(8, 8),
        )
        result = run_direct_ray_trace(
            DirectRayTraceInput(
                mesh=mesh,
                emitters=[emitter],
                receivers=[receiver],
                optical_profiles=[OpticalProfile(profile_id="default", reflectance=0.08)],
                config=RayTraceConfig(ray_count=300, max_depth=0, seed=11),
            )
        )

        self.assertEqual(result.total_rays, 300)
        self.assertGreater(result.receiver_hit_count, 250)
        self.assertEqual(result.surface_hit_count, 0)
        self.assertGreater(result.metrics["front_receiver"]["peak_nit_est"], 0.0)

    def test_receiver_behind_emitter_has_no_direct_hit(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="face_source",
            face_indices=[0, 1],
            direction_distribution="gaussian",
            gaussian_sigma_deg=2.0,
            power_lumen=1.0,
            ray_count=120,
            seed=7,
        )
        receiver = ReceiverSpec(
            receiver_id="back_receiver",
            center=(0.0, 0.0, -20.0),
            normal=(0.0, 0.0, 1.0),
            width_mm=80.0,
            height_mm=80.0,
            resolution=(8, 8),
        )
        result = run_direct_ray_trace(
            DirectRayTraceInput(
                mesh=mesh,
                emitters=[emitter],
                receivers=[receiver],
                optical_profiles=[OpticalProfile(profile_id="default", reflectance=0.08)],
                config=RayTraceConfig(ray_count=120, max_depth=0, seed=11),
            )
        )

        self.assertEqual(result.total_rays, 120)
        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(result.metrics["back_receiver"]["peak_nit_est"], 0.0)

    def test_datum_plane_emitter_hits_receiver(self) -> None:
        mesh = build_emitter_plane()
        emitter = EmitterSpec(
            emitter_id="datum_source",
            emitter_type="datum_plane",
            center=(0.0, 0.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=10.0,
            height_mm=10.0,
            direction_distribution="gaussian",
            gaussian_sigma_deg=2.0,
            power_lumen=1.0,
            ray_count=200,
            seed=17,
        )
        receiver = ReceiverSpec(
            receiver_id="datum_receiver",
            center=(0.0, 0.0, 20.0),
            normal=(0.0, 0.0, -1.0),
            width_mm=80.0,
            height_mm=80.0,
            resolution=(8, 8),
        )
        result = run_direct_ray_trace(
            DirectRayTraceInput(
                mesh=mesh,
                emitters=[emitter],
                receivers=[receiver],
                optical_profiles=[],
                config=RayTraceConfig(
                    ray_count=200,
                    max_depth=0,
                    seed=19,
                    store_ray_paths=True,
                    max_stored_paths=5,
                ),
            )
        )

        self.assertGreater(result.receiver_hit_count, 170)
        self.assertEqual(result.emitters[0].emitter_type, "datum_plane")
        self.assertEqual(len(result.stored_paths), 5)
        self.assertEqual(result.stored_paths[0][0].event_type, "emitter")
        self.assertEqual(result.stored_paths[0][1].event_type, "receiver")

    def test_reference_plane_power_per_area(self) -> None:
        emitter = EmitterSpec(
            emitter_id="reference_source",
            emitter_type="reference_plane",
            center=(0.0, 0.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=20.0,
            height_mm=10.0,
            power_mode="power_per_area",
            power_density_lm_per_m2=500.0,
            reference_mode="three_vertices",
            reference_vertex_indices=[0, 1, 2, 3, 4, 5],
            ray_count=100,
        )

        self.assertAlmostEqual(emitter.effective_power_lumen(200.0), 0.1)
        self.assertEqual(emitter.reference_vertex_indices, [0, 1, 2, 3, 4, 5])

    def test_polygon_reference_emitter_uses_polygon_area(self) -> None:
        emitter = EmitterSpec(
            emitter_id="polygon_source",
            emitter_type="reference_plane",
            center=(5.0, 5.0, 0.0),
            u_axis=(1.0, 0.0, 0.0),
            v_axis=(0.0, 1.0, 0.0),
            width_mm=10.0,
            height_mm=10.0,
            surface_construction="polygon_auto",
            polygon_vertices=[(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)],
            power_mode="power_per_area",
            power_density_lm_per_m2=1000.0,
            ray_count=100,
        )

        restored = EmitterSpec.from_dict(emitter.to_dict())

        self.assertAlmostEqual(emitter.virtual_area_mm2(), 50.0)
        self.assertAlmostEqual(emitter.effective_power_lumen(emitter.virtual_area_mm2()), 0.05)
        self.assertEqual(restored.surface_construction, "polygon_auto")
        self.assertEqual(len(restored.polygon_vertices), 3)

    def test_polygon_sampling_stays_inside_triangle(self) -> None:
        vertices = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)]
        rng = random.Random(23)

        samples = [_sample_polygon_point(vertices, rng) for _ in range(200)]

        self.assertTrue(all(sample is not None for sample in samples))
        for sample in samples:
            assert sample is not None
            self.assertGreaterEqual(sample[0], 0.0)
            self.assertGreaterEqual(sample[1], 0.0)
            self.assertLessEqual(sample[0] + sample[1], 10.0 + 1e-9)
            self.assertAlmostEqual(sample[2], 0.0)


if __name__ == "__main__":
    unittest.main()
