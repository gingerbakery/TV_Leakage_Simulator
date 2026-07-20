from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, RayTraceConfig, ReceiverSpec


def add_xy_rectangle(
    mesh: TriangleMesh,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    z: float,
    component_id: int = 10,
) -> None:
    vertices = [
        mesh.add_vertex((x0, y0, z)),
        mesh.add_vertex((x1, y0, z)),
        mesh.add_vertex((x1, y1, z)),
        mesh.add_vertex((x0, y1, z)),
    ]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], "black_pc_resin", metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], "black_pc_resin", metadata)


def build_gap_mesh(gap_width_mm: float) -> TriangleMesh:
    mesh = TriangleMesh()
    half_gap = max(0.0, min(10.0, gap_width_mm * 0.5))
    if half_gap <= 0.0:
        add_xy_rectangle(mesh, -10.0, -10.0, 10.0, 10.0, 10.0)
    elif half_gap < 10.0:
        add_xy_rectangle(mesh, -10.0, -10.0, -half_gap, 10.0, 10.0)
        add_xy_rectangle(mesh, half_gap, -10.0, 10.0, 10.0, 10.0)
    return mesh


def build_input(
    mesh: TriangleMesh,
    receiver_z: float = 20.0,
    ray_count: int = 1000,
    gaussian_sigma_deg: float = 1.0,
) -> DirectRayTraceInput:
    emitter = EmitterSpec(
        emitter_id="datum_source",
        emitter_type="datum_plane",
        center=(0.0, 0.0, 0.0),
        u_axis=(1.0, 0.0, 0.0),
        v_axis=(0.0, 1.0, 0.0),
        width_mm=2.0,
        height_mm=2.0,
        direction_distribution="gaussian",
        gaussian_sigma_deg=gaussian_sigma_deg,
        power_lumen=1.0,
        ray_count=ray_count,
        seed=31,
    )
    receiver = ReceiverSpec(
        receiver_id="observer",
        center=(0.0, 0.0, receiver_z),
        normal=(0.0, 0.0, -1.0),
        width_mm=30.0,
        height_mm=30.0,
        resolution=(12, 12),
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[emitter],
        receivers=[receiver],
        optical_profiles=[],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=0,
            seed=37,
            store_ray_paths=True,
            max_stored_paths=12,
        ),
    )


class RayTracerRT2AOcclusionTests(unittest.TestCase):
    def test_opaque_plate_blocks_receiver(self) -> None:
        mesh = TriangleMesh()
        add_xy_rectangle(mesh, -10.0, -10.0, 10.0, 10.0, 10.0, component_id=42)

        result = run_direct_ray_trace(build_input(mesh))

        self.assertEqual(result.total_rays, 1000)
        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(result.surface_hit_count, 1000)
        self.assertEqual(result.terminated_ray_count, 1000)
        self.assertEqual(result.stored_paths[0][-1].event_type, "surface")
        self.assertEqual(result.stored_paths[0][-1].component_id, 42)
        self.assertEqual(result.stored_paths[0][-1].material_id, "black_pc_resin")

    def test_gap_between_plates_allows_direct_rays(self) -> None:
        mesh = TriangleMesh()
        add_xy_rectangle(mesh, -10.0, -10.0, -2.0, 10.0, 10.0)
        add_xy_rectangle(mesh, 2.0, -10.0, 10.0, 10.0, 10.0)

        result = run_direct_ray_trace(build_input(mesh, ray_count=2000))

        self.assertGreater(result.receiver_hit_count, 1900)
        self.assertLess(result.surface_hit_count, 100)
        self.assertEqual(result.receiver_hit_count + result.terminated_ray_count, result.total_rays)

    def test_receiver_before_plate_is_not_blocked(self) -> None:
        mesh = TriangleMesh()
        add_xy_rectangle(mesh, -10.0, -10.0, 10.0, 10.0, 20.0)

        result = run_direct_ray_trace(build_input(mesh, receiver_z=10.0))

        self.assertGreater(result.receiver_hit_count, 990)
        self.assertEqual(result.surface_hit_count, 0)
        self.assertEqual(result.stored_paths[0][-1].event_type, "receiver")

    def test_bvh_mesh_blocks_with_many_triangles(self) -> None:
        mesh = TriangleMesh()
        for x_index in range(8):
            for y_index in range(8):
                x0 = -8.0 + x_index * 2.0
                y0 = -8.0 + y_index * 2.0
                add_xy_rectangle(mesh, x0, y0, x0 + 2.0, y0 + 2.0, 10.0, component_id=77)

        result = run_direct_ray_trace(build_input(mesh, ray_count=500))

        self.assertEqual(len(mesh.faces), 128)
        self.assertEqual(result.surface_hit_count, 500)
        self.assertEqual(result.receiver_hit_count, 0)
        self.assertEqual(
            result.metrics["_performance_summary"]["intersection_backend"],
            "bvh",
        )
        self.assertGreater(
            result.metrics["_performance_summary"]["bvh_node_count"],
            0,
        )

    def test_gap_sweep_transmission_is_monotonic(self) -> None:
        gaps = [0.0, 1.0, 2.0, 4.0, 8.0, 20.0]
        hit_counts = [
            run_direct_ray_trace(
                build_input(
                    build_gap_mesh(gap),
                    ray_count=3000,
                    gaussian_sigma_deg=8.0,
                )
            ).receiver_hit_count
            for gap in gaps
        ]

        self.assertEqual(hit_counts, sorted(hit_counts))
        self.assertEqual(hit_counts[0], 0)
        self.assertGreater(hit_counts[-1], 2950)

    def test_partial_gap_flux_stays_between_closed_and_open(self) -> None:
        closed = run_direct_ray_trace(
            build_input(build_gap_mesh(0.0), ray_count=2000, gaussian_sigma_deg=8.0)
        )
        partial = run_direct_ray_trace(
            build_input(build_gap_mesh(4.0), ray_count=2000, gaussian_sigma_deg=8.0)
        )
        open_path = run_direct_ray_trace(
            build_input(build_gap_mesh(20.0), ray_count=2000, gaussian_sigma_deg=8.0)
        )

        closed_flux = closed.metrics["observer"]["total_flux_lumen"]
        partial_flux = partial.metrics["observer"]["total_flux_lumen"]
        open_flux = open_path.metrics["observer"]["total_flux_lumen"]
        self.assertEqual(closed_flux, 0.0)
        self.assertGreater(partial_flux, closed_flux)
        self.assertLess(partial_flux, open_flux)


if __name__ == "__main__":
    unittest.main()
