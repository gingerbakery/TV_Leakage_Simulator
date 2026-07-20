from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import TriangleMesh, add_box, vec_norm
from leakage_simulator.types import RayTraceConfig


def build_box_array() -> TriangleMesh:
    mesh = TriangleMesh()
    component_id = 0
    for x_index in range(6):
        for y_index in range(5):
            x0 = x_index * 3.5
            y0 = y_index * 3.25
            z0 = (x_index + y_index) % 3
            add_box(
                mesh,
                x0,
                y0,
                z0,
                x0 + 2.0,
                y0 + 1.75,
                z0 + 1.5,
                "test",
                {"component_id": component_id},
            )
            component_id += 1
    return mesh


class FlatBvhPerf2Tests(unittest.TestCase):
    def test_bvh_matches_brute_force_for_random_rays(self) -> None:
        mesh = build_box_array()
        rng = random.Random(20260717)

        for _ in range(400):
            origin = (
                rng.uniform(-5.0, 25.0),
                rng.uniform(-5.0, 22.0),
                -8.0,
            )
            target = (
                rng.uniform(0.2, 19.0),
                rng.uniform(0.2, 14.0),
                rng.uniform(0.2, 3.2),
            )
            direction = vec_norm(
                (
                    target[0] - origin[0],
                    target[1] - origin[1],
                    target[2] - origin[2],
                )
            )
            brute_hit = mesh.intersect_ray(
                origin,
                direction,
                backend="brute_force",
            )
            bvh_hit = mesh.intersect_ray(
                origin,
                direction,
                backend="bvh",
            )

            self.assertEqual(brute_hit is None, bvh_hit is None)
            if brute_hit is None or bvh_hit is None:
                continue
            self.assertEqual(brute_hit.face_index, bvh_hit.face_index)
            self.assertTrue(math.isclose(brute_hit.t, bvh_hit.t, abs_tol=1e-9))
            for brute_value, bvh_value in zip(brute_hit.point, bvh_hit.point):
                self.assertTrue(math.isclose(brute_value, bvh_value, abs_tol=1e-9))
            for brute_value, bvh_value in zip(brute_hit.normal, bvh_hit.normal):
                self.assertTrue(math.isclose(brute_value, bvh_value, abs_tol=1e-9))

    def test_bvh_preserves_ignore_face_and_max_distance(self) -> None:
        mesh = TriangleMesh()
        add_box(mesh, -1.0, -1.0, 5.0, 1.0, 1.0, 7.0, "near")
        add_box(mesh, -1.0, -1.0, 10.0, 1.0, 1.0, 12.0, "far")
        origin = (0.23, -0.17, 0.0)
        direction = (0.0, 0.0, 1.0)

        first_hit = mesh.intersect_ray(origin, direction, backend="bvh")
        self.assertIsNotNone(first_hit)
        assert first_hit is not None
        self.assertLess(first_hit.t, 6.0)

        ignored_hit = mesh.intersect_ray(
            origin,
            direction,
            ignore_face=first_hit.face_index,
            backend="bvh",
        )
        brute_ignored_hit = mesh.intersect_ray(
            origin,
            direction,
            ignore_face=first_hit.face_index,
            backend="brute_force",
        )
        self.assertEqual(
            brute_ignored_hit.face_index if brute_ignored_hit else None,
            ignored_hit.face_index if ignored_hit else None,
        )
        self.assertIsNone(
            mesh.intersect_ray(
                origin,
                direction,
                max_t=4.9,
                backend="bvh",
            )
        )

    def test_shared_edge_tie_uses_same_lowest_face_index(self) -> None:
        mesh = TriangleMesh()
        vertices = [
            mesh.add_vertex((-1.0, -1.0, 5.0)),
            mesh.add_vertex((1.0, -1.0, 5.0)),
            mesh.add_vertex((1.0, 1.0, 5.0)),
            mesh.add_vertex((-1.0, 1.0, 5.0)),
        ]
        mesh.add_face(vertices[0], vertices[1], vertices[2], "first")
        mesh.add_face(vertices[0], vertices[2], vertices[3], "second")

        brute_hit = mesh.intersect_ray(
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
            backend="brute_force",
        )
        bvh_hit = mesh.intersect_ray(
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 1.0),
            backend="bvh",
        )
        self.assertIsNotNone(brute_hit)
        self.assertIsNotNone(bvh_hit)
        self.assertEqual(brute_hit.face_index, 0)
        self.assertEqual(bvh_hit.face_index, 0)

    def test_mesh_change_invalidates_and_rebuilds_bvh(self) -> None:
        mesh = build_box_array()
        first_info = mesh.prepare_acceleration()
        self.assertGreater(first_info["bvh_node_count"], 0)

        add_box(mesh, 40.0, 40.0, 40.0, 42.0, 42.0, 42.0, "new")
        invalidated_info = mesh.acceleration_info()
        self.assertEqual(invalidated_info["bvh_node_count"], 0)

        hit = mesh.intersect_ray(
            (41.0, 41.0, 30.0),
            (0.0, 0.0, 1.0),
            backend="bvh",
        )
        self.assertIsNotNone(hit)
        rebuilt_info = mesh.acceleration_info()
        self.assertGreater(rebuilt_info["bvh_node_count"], 0)

    def test_auto_backend_and_config_contract(self) -> None:
        small_mesh = TriangleMesh()
        add_box(small_mesh, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, "small")
        self.assertEqual(
            small_mesh.acceleration_info()["selected_backend"],
            "brute_force",
        )

        large_mesh = build_box_array()
        self.assertEqual(
            large_mesh.acceleration_info()["selected_backend"],
            "bvh",
        )
        self.assertEqual(
            RayTraceConfig(intersection_backend="bvh").intersection_backend,
            "bvh",
        )
        with self.assertRaises(ValueError):
            RayTraceConfig(intersection_backend="gpu")


if __name__ == "__main__":
    unittest.main()
