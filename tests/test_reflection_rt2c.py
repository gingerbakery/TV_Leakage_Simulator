from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import TriangleMesh, vec_dot
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.reflection import (
    ideal_specular_direction,
    sample_cosine_weighted_hemisphere,
    sample_reflection_direction,
)
from leakage_simulator.types import (
    EmitterSpec,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


def add_quad(
    mesh: TriangleMesh,
    points,
    material_id: str = "reflector",
    component_id: int = 1,
) -> None:
    vertices = [mesh.add_vertex(point) for point in points]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id, metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id, metadata)


def horizontal_reflector_mesh() -> TriangleMesh:
    mesh = TriangleMesh()
    add_quad(
        mesh,
        [
            (-20.0, -20.0, 10.0),
            (20.0, -20.0, 10.0),
            (20.0, 20.0, 10.0),
            (-20.0, 20.0, 10.0),
        ],
    )
    return mesh


def reflected_input(
    profile: OpticalProfile,
    ray_count: int = 2000,
    receiver_width_mm: float = 30.0,
    max_depth: int = 1,
) -> DirectRayTraceInput:
    emitter = EmitterSpec(
        emitter_id="source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=0.2,
        height_mm=0.2,
        direction_distribution="gaussian",
        gaussian_sigma_deg=0.01,
        power_lumen=1.0,
        ray_count=ray_count,
        seed=20260717,
    )
    receiver = ReceiverSpec(
        receiver_id="observer",
        center=(0.0, 0.0, -10.0),
        normal=(0.0, 0.0, 1.0),
        width_mm=receiver_width_mm,
        height_mm=receiver_width_mm,
        resolution=(20, 20),
    )
    return DirectRayTraceInput(
        mesh=horizontal_reflector_mesh(),
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[profile],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=max_depth,
            seed=19,
            store_ray_paths=True,
            max_stored_paths=20,
        ),
    )


def angled_reflector_input(with_blocker: bool) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    add_quad(
        mesh,
        [
            (-10.0, -10.0, 0.0),
            (10.0, -10.0, 20.0),
            (10.0, 10.0, 20.0),
            (-10.0, 10.0, 0.0),
        ],
        component_id=10,
    )
    if with_blocker:
        add_quad(
            mesh,
            [
                (8.0, -5.0, 5.0),
                (8.0, 5.0, 5.0),
                (8.0, 5.0, 15.0),
                (8.0, -5.0, 15.0),
            ],
            material_id="blocker",
            component_id=20,
        )
    emitter = EmitterSpec(
        emitter_id="source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=0.1,
        height_mm=0.1,
        direction_distribution="gaussian",
        gaussian_sigma_deg=0.01,
        power_lumen=1.0,
        ray_count=1000,
        seed=77,
    )
    receiver = ReceiverSpec(
        receiver_id="side_receiver",
        center=(15.0, 0.0, 10.0),
        normal=(-1.0, 0.0, 0.0),
        width_mm=10.0,
        height_mm=10.0,
        resolution=(10, 10),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[
            OpticalProfile(
                "reflector",
                0.5,
                scatter_model="specular",
            ),
            OpticalProfile("blocker", 0.0, scatter_model="none"),
        ],
        config=RayTraceConfig(
            ray_count=1000,
            max_depth=1,
            store_ray_paths=True,
            max_stored_paths=10,
        ),
    )


class ReflectionRT2CTests(unittest.TestCase):
    def test_specular_direction_matches_reflection_law(self) -> None:
        inverse_root_two = 1.0 / math.sqrt(2.0)
        incoming = (inverse_root_two, 0.0, -inverse_root_two)
        reflected = ideal_specular_direction(incoming, (0.0, 0.0, 1.0))

        self.assertAlmostEqual(reflected[0], inverse_root_two, places=12)
        self.assertAlmostEqual(reflected[1], 0.0, places=12)
        self.assertAlmostEqual(reflected[2], inverse_root_two, places=12)
        self.assertAlmostEqual(vec_dot(incoming, (0.0, 0.0, 1.0)), -inverse_root_two)
        self.assertAlmostEqual(vec_dot(reflected, (0.0, 0.0, 1.0)), inverse_root_two)

    def test_specular_reflection_reaches_receiver_once(self) -> None:
        result = run_direct_ray_trace(
            reflected_input(
                OpticalProfile("reflector", 0.4, scatter_model="specular"),
                ray_count=2000,
            )
        )
        summary = result.metrics["_reflection_summary"]

        self.assertEqual(result.receiver_hit_count, 2000)
        self.assertEqual(result.terminated_ray_count, 0)
        self.assertEqual(summary["reflection_receiver_hit_count"], 2000)
        self.assertEqual(summary["lobes"]["specular"]["emitted_count"], 2000)
        self.assertGreater(result.metrics["observer"]["total_flux_lumen"], 0.3999)
        self.assertEqual(len(result.stored_paths[0]), 3)
        self.assertEqual(result.stored_paths[0][-1].ray_kind, "specular")

    def test_reflected_flux_scales_with_reflectance(self) -> None:
        low = run_direct_ray_trace(
            reflected_input(OpticalProfile("reflector", 0.2, scatter_model="specular"))
        )
        high = run_direct_ray_trace(
            reflected_input(OpticalProfile("reflector", 0.8, scatter_model="specular"))
        )
        low_flux = low.metrics["observer"]["total_flux_lumen"]
        high_flux = high.metrics["observer"]["total_flux_lumen"]

        self.assertAlmostEqual(high_flux / low_flux, 4.0, places=8)

    def test_max_depth_zero_preserves_rt2a_termination(self) -> None:
        result = run_direct_ray_trace(
            reflected_input(
                OpticalProfile("reflector", 0.8, scatter_model="specular"),
                max_depth=0,
            )
        )

        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(result.terminated_ray_count, result.total_rays)
        self.assertEqual(result.metrics["_reflection_summary"]["reflection_emitted_count"], 0)

    def test_gaussian_width_reduces_small_receiver_hits(self) -> None:
        narrow = run_direct_ray_trace(
            reflected_input(
                OpticalProfile(
                    "reflector",
                    0.5,
                    scatter_model="gaussian",
                    gaussian_sigma_deg=1.0,
                ),
                ray_count=6000,
                receiver_width_mm=5.0,
            )
        )
        wide = run_direct_ray_trace(
            reflected_input(
                OpticalProfile(
                    "reflector",
                    0.5,
                    scatter_model="gaussian",
                    gaussian_sigma_deg=18.0,
                ),
                ray_count=6000,
                receiver_width_mm=5.0,
            )
        )

        self.assertGreater(narrow.receiver_hit_count, 5900)
        self.assertLess(wide.receiver_hit_count, narrow.receiver_hit_count * 0.35)

    def test_lambertian_sampling_has_cosine_weighted_mean(self) -> None:
        rng = random.Random(123)
        normal = (0.0, 0.0, 1.0)
        cosines = [
            vec_dot(sample_cosine_weighted_hemisphere(rng, normal), normal)
            for _ in range(30000)
        ]

        self.assertGreater(min(cosines), -1e-12)
        self.assertGreater(sum(cosines) / len(cosines), 0.66)
        self.assertLess(sum(cosines) / len(cosines), 0.675)

    def test_mixed_profile_uses_glossy_and_lambertian_ratios(self) -> None:
        rng = random.Random(456)
        profile = OpticalProfile(
            "mixed_surface",
            0.3,
            specular_ratio=0.25,
            diffuse_ratio=0.75,
            scatter_model="mixed",
            gaussian_sigma_deg=10.0,
        )
        counts = {"gaussian": 0, "lambertian": 0}
        for _ in range(20000):
            sample = sample_reflection_direction(
                rng,
                incoming=(0.0, 0.0, 1.0),
                normal=(0.0, 0.0, -1.0),
                profile=profile,
            )
            self.assertIsNotNone(sample)
            counts[sample.lobe] += 1

        gaussian_ratio = counts["gaussian"] / 20000.0
        self.assertGreater(gaussian_ratio, 0.235)
        self.assertLess(gaussian_ratio, 0.265)

    def test_reflected_ray_is_blocked_by_secondary_surface(self) -> None:
        open_result = run_direct_ray_trace(angled_reflector_input(with_blocker=False))
        blocked_result = run_direct_ray_trace(angled_reflector_input(with_blocker=True))
        blocked_summary = blocked_result.metrics["_reflection_summary"]

        self.assertGreater(open_result.receiver_hit_count, 990)
        self.assertEqual(blocked_result.receiver_hit_count, 0)
        self.assertGreater(blocked_summary["reflection_blocked_count"], 990)
        self.assertEqual(blocked_result.stored_paths[0][-1].component_id, 20)


if __name__ == "__main__":
    unittest.main()
