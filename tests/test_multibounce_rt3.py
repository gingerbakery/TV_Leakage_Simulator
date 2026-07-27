from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    MAX_REFLECTION_DEPTH,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


def add_mirror(
    mesh: TriangleMesh,
    center,
    material_id: str,
    component_id: int,
    half_extent: float = 4.0,
) -> None:
    inverse_root_two = 1.0 / math.sqrt(2.0)
    tangent = (inverse_root_two, 0.0, inverse_root_two)
    vertical = (0.0, 1.0, 0.0)

    def point(tangent_scale: float, vertical_scale: float):
        return (
            center[0] + tangent[0] * tangent_scale + vertical[0] * vertical_scale,
            center[1] + tangent[1] * tangent_scale + vertical[1] * vertical_scale,
            center[2] + tangent[2] * tangent_scale + vertical[2] * vertical_scale,
        )

    points = [
        point(-half_extent, -half_extent),
        point(half_extent, -half_extent),
        point(half_extent, half_extent),
        point(-half_extent, half_extent),
    ]
    vertices = [mesh.add_vertex(value) for value in points]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id, metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id, metadata)


def two_bounce_input(
    max_depth: int,
    ray_count: int = 1000,
    min_energy: float = 1e-9,
    termination_mode: str = "threshold",
    store_paths: bool = True,
) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    add_mirror(mesh, (0.0, 0.0, 10.0), "mirror_a", 101)
    add_mirror(mesh, (10.0, 0.0, 10.0), "mirror_b", 202)
    emitter = EmitterSpec(
        emitter_id="source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=0.02,
        height_mm=0.02,
        direction_distribution="gaussian",
        gaussian_sigma_deg=0.001,
        power_lumen=1.0,
        ray_count=ray_count,
        seed=20260721,
    )
    receiver = ReceiverSpec(
        receiver_id="observer",
        center=(10.0, 0.0, 20.0),
        normal=(0.0, 0.0, -1.0),
        width_mm=4.0,
        height_mm=4.0,
        resolution=(12, 12),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[
            OpticalProfile("mirror_a", 0.8, scatter_model="specular"),
            OpticalProfile("mirror_b", 0.5, scatter_model="specular"),
        ],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=max_depth,
            seed=31,
            min_energy=min_energy,
            termination_mode=termination_mode,
            contribution_mode="detailed",
            store_ray_paths=store_paths,
            max_stored_paths=12,
        ),
    )


def add_corridor_wall(
    mesh: TriangleMesh,
    y_position: float,
    material_id: str,
    component_id: int,
) -> None:
    if y_position > 0.0:
        points = [
            (0.0, y_position, -2.0),
            (21.0, y_position, -2.0),
            (21.0, y_position, 2.0),
            (0.0, y_position, 2.0),
        ]
    else:
        points = [
            (0.0, y_position, -2.0),
            (0.0, y_position, 2.0),
            (21.0, y_position, 2.0),
            (21.0, y_position, -2.0),
        ]
    vertices = [mesh.add_vertex(value) for value in points]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id, metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id, metadata)


def ten_bounce_corridor_input(max_depth: int) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    add_corridor_wall(mesh, 1.0, "high_reflector", 301)
    add_corridor_wall(mesh, -1.0, "high_reflector", 302)
    inverse_root_two = 1.0 / math.sqrt(2.0)
    emitter = EmitterSpec(
        emitter_id="corridor_source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(0.0, 0.0, 1.0),
        v_axis=(inverse_root_two, -inverse_root_two, 0.0),
        width_mm=0.001,
        height_mm=0.001,
        direction_distribution="gaussian",
        gaussian_sigma_deg=0.001,
        power_lumen=1.0,
        ray_count=100,
        seed=20260727,
    )
    receiver = ReceiverSpec(
        receiver_id="corridor_observer",
        center=(20.0, 0.0, 0.0),
        normal=(-1.0, 0.0, 0.0),
        width_mm=1.5,
        height_mm=1.5,
        resolution=(10, 10),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[
            OpticalProfile(
                "high_reflector",
                0.95,
                scatter_model="specular",
            ),
        ],
        config=RayTraceConfig(
            ray_count=100,
            max_depth=max_depth,
            seed=73,
            min_energy=1e-12,
            contribution_mode="summary",
            store_ray_paths=False,
        ),
    )


class MultiBounceRT3Tests(unittest.TestCase):
    def test_second_reflection_requires_depth_two(self) -> None:
        depth_one = run_direct_ray_trace(two_bounce_input(max_depth=1))
        depth_two = run_direct_ray_trace(two_bounce_input(max_depth=2))

        self.assertEqual(depth_one.receiver_hit_count, 0)
        self.assertEqual(depth_two.receiver_hit_count, 1000)
        self.assertGreater(depth_two.metrics["observer"]["total_flux_lumen"], 0.399)
        self.assertLess(depth_two.metrics["observer"]["total_flux_lumen"], 0.401)
        self.assertEqual(depth_two.metrics["_reflection_summary"]["implemented_max_depth"], 2)
        self.assertEqual(depth_two.metrics["_reflection_summary"]["max_observed_depth"], 2)

    def test_stored_path_records_two_surface_bounces(self) -> None:
        result = run_direct_ray_trace(two_bounce_input(max_depth=2, ray_count=20))
        path = result.stored_paths[0]

        self.assertEqual([event.event_type for event in path], ["emitter", "surface", "surface", "receiver"])
        self.assertEqual([event.depth for event in path], [0, 0, 1, 2])
        self.assertAlmostEqual(path[1].outgoing_energy_lumen, 0.04, places=8)
        self.assertAlmostEqual(path[2].outgoing_energy_lumen, 0.02, places=8)

    def test_threshold_terminates_low_energy_second_bounce(self) -> None:
        result = run_direct_ray_trace(
            two_bounce_input(max_depth=2, min_energy=0.0005)
        )
        summary = result.metrics["_reflection_summary"]

        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(summary["reflection_below_energy_count"], 1000)
        self.assertEqual(summary["reflection_emitted_count"], 1000)

    def test_russian_roulette_is_reproducible_and_unbiased(self) -> None:
        first = run_direct_ray_trace(
            two_bounce_input(
                max_depth=2,
                min_energy=0.0005,
                termination_mode="russian_roulette",
                store_paths=False,
            )
        )
        second = run_direct_ray_trace(
            two_bounce_input(
                max_depth=2,
                min_energy=0.0005,
                termination_mode="russian_roulette",
                store_paths=False,
            )
        )
        summary = first.metrics["_reflection_summary"]

        self.assertEqual(first.receiver_hit_count, second.receiver_hit_count)
        self.assertEqual(first.metrics["observer"]["total_flux_lumen"], second.metrics["observer"]["total_flux_lumen"])
        self.assertGreater(first.receiver_hit_count, 740)
        self.assertLess(first.receiver_hit_count, 860)
        self.assertGreater(first.metrics["observer"]["total_flux_lumen"], 0.37)
        self.assertLess(first.metrics["observer"]["total_flux_lumen"], 0.43)
        self.assertEqual(summary["roulette_survived_count"], first.receiver_hit_count)
        self.assertEqual(summary["roulette_terminated_count"], 1000 - first.receiver_hit_count)

    def test_contribution_summary_is_split_by_bounce_depth(self) -> None:
        result = run_direct_ray_trace(two_bounce_input(max_depth=2))
        summary = result.contribution_summary

        self.assertEqual(summary.depths["0"]["surface_hit_count"], 1000)
        self.assertEqual(summary.depths["1"]["surface_hit_count"], 1000)
        self.assertEqual(summary.depths["2"]["receiver_hit_count"], 1000)
        self.assertEqual(summary.components["101"]["continued_count"], 1000)
        self.assertEqual(summary.components["202"]["receiver_hit_count"], 1000)
        self.assertAlmostEqual(summary.reflected_receiver_flux_lumen, 0.4, places=6)

    def test_ten_reflections_reach_receiver_in_high_reflectance_corridor(self) -> None:
        depth_nine = run_direct_ray_trace(ten_bounce_corridor_input(max_depth=9))
        depth_ten = run_direct_ray_trace(ten_bounce_corridor_input(max_depth=10))

        self.assertEqual(depth_nine.receiver_hit_count, 0)
        self.assertEqual(depth_ten.receiver_hit_count, 100)
        self.assertAlmostEqual(
            depth_ten.metrics["corridor_observer"]["total_flux_lumen"],
            (0.95**10) / math.sqrt(2.0),
            places=6,
        )
        self.assertEqual(
            depth_ten.metrics["_reflection_summary"]["max_observed_depth"],
            10,
        )

    def test_v1_reflection_depth_contract_accepts_twenty_and_rejects_more(self) -> None:
        config = RayTraceConfig(max_depth=MAX_REFLECTION_DEPTH)

        self.assertEqual(config.max_depth, 20)
        with self.assertRaisesRegex(ValueError, "must not exceed 20"):
            RayTraceConfig(max_depth=MAX_REFLECTION_DEPTH + 1)


if __name__ == "__main__":
    unittest.main()
