from __future__ import annotations

import math
import random
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import (
    RayBatch,
    RayHitBatch,
    TriangleMesh,
    add_box,
    vec_norm,
)


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


def build_parallel_triangles() -> TriangleMesh:
    mesh = TriangleMesh()
    for z_value, material_id in ((5.0, "near"), (10.0, "far")):
        vertices = [
            mesh.add_vertex((-1.0, -1.0, z_value)),
            mesh.add_vertex((1.0, -1.0, z_value)),
            mesh.add_vertex((0.0, 1.0, z_value)),
        ]
        mesh.add_face(*vertices, material_id)
    return mesh


def build_random_rays(
    count: int,
    seed: int = 20260717,
) -> tuple[np.ndarray, np.ndarray]:
    rng = random.Random(seed)
    origins = []
    directions = []
    for _ in range(count):
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
        origins.append(origin)
        directions.append(
            vec_norm(
                (
                    target[0] - origin[0],
                    target[1] - origin[1],
                    target[2] - origin[2],
                )
            )
        )
    return np.asarray(origins), np.asarray(directions)


class BatchIntersectionPerf3BTests(unittest.TestCase):
    def test_batch_matches_scalar_for_seeded_rays_on_both_cpu_backends(self) -> None:
        mesh = build_box_array()
        origins, directions = build_random_rays(400)
        rays = RayBatch(origins, directions)

        for backend in ("brute_force", "bvh"):
            with self.subTest(backend=backend):
                batch_hits = mesh.intersect_rays(rays, backend=backend)
                for index, (origin, direction) in enumerate(zip(origins, directions)):
                    scalar_hit = mesh.intersect_ray(
                        tuple(origin),
                        tuple(direction),
                        backend=backend,
                    )
                    if scalar_hit is None:
                        self.assertEqual(batch_hits.face_indices[index], -1)
                        self.assertTrue(math.isinf(batch_hits.t[index]))
                    else:
                        self.assertEqual(
                            batch_hits.face_indices[index],
                            scalar_hit.face_index,
                        )
                        self.assertTrue(
                            math.isclose(
                                batch_hits.t[index],
                                scalar_hit.t,
                                abs_tol=1e-9,
                            )
                        )

    def test_per_ray_limits_and_ignored_faces_preserve_boundaries(self) -> None:
        mesh = build_parallel_triangles()
        origins = np.zeros((8, 3), dtype=np.float64)
        directions = np.tile((0.0, 0.0, 1.0), (8, 1))
        rays = RayBatch(
            origins,
            directions,
            min_t=[1e-8, 1e-8, 1e-8, 1e-8, 5.0, 1e-8, 1e-8, 5.0],
            max_t=[math.inf, math.inf, 4.999, 5.0, math.inf, 9.999, 10.0, 5.0],
            ignore_faces=[-1, 0, -1, -1, -1, 0, 0, -1],
        )

        hits = mesh.intersect_rays(rays, backend="brute_force")

        np.testing.assert_array_equal(
            hits.face_indices,
            np.asarray([0, 1, -1, 0, 1, -1, 1, -1]),
        )
        np.testing.assert_allclose(
            hits.t[[0, 1, 3, 4, 6]],
            np.asarray([5.0, 10.0, 5.0, 10.0, 10.0]),
            atol=1e-12,
        )
        self.assertTrue(np.all(np.isposinf(hits.t[[2, 5, 7]])))

    def test_explicit_misses_use_inf_and_negative_one(self) -> None:
        mesh = build_parallel_triangles()
        rays = RayBatch(
            origins=[(0.0, 0.0, 0.0)] * 3,
            directions=[
                (0.0, 0.0, -1.0),
                (1.0, 0.0, 0.0),
                vec_norm((10.0, 10.0, 5.0)),
            ],
        )

        hits = mesh.intersect_rays(rays)

        np.testing.assert_array_equal(hits.face_indices, [-1, -1, -1])
        self.assertTrue(np.all(np.isposinf(hits.t)))
        np.testing.assert_array_equal(hits.hit_mask, [False, False, False])

    def test_duplicate_triangle_tie_uses_lowest_face_index(self) -> None:
        mesh = TriangleMesh()
        vertices = [
            mesh.add_vertex((-1.0, -1.0, 5.0)),
            mesh.add_vertex((1.0, -1.0, 5.0)),
            mesh.add_vertex((0.0, 1.0, 5.0)),
        ]
        mesh.add_face(*vertices, "first")
        mesh.add_face(*vertices, "second")
        rays = RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)])

        for backend in ("brute_force", "bvh"):
            with self.subTest(backend=backend):
                hits = mesh.intersect_rays(rays, backend=backend)
                self.assertEqual(hits.face_indices.tolist(), [0])

    def test_empty_batch_is_typed_and_does_not_build_bvh(self) -> None:
        mesh = build_box_array()
        rays = RayBatch([], [])

        hits = mesh.intersect_rays(rays, backend="bvh")

        self.assertEqual(len(hits), 0)
        self.assertEqual(hits.t.dtype, np.float64)
        self.assertEqual(hits.face_indices.dtype, np.int64)
        self.assertEqual(mesh.acceleration_info()["bvh_node_count"], 0)

    def test_batch_validates_shapes_values_and_result_sentinels(self) -> None:
        with self.assertRaises(ValueError):
            RayBatch([(0.0, 0.0)], [(0.0, 0.0, 1.0)])
        with self.assertRaises(ValueError):
            RayBatch([(0.0, 0.0, 0.0)], [])
        with self.assertRaises(ValueError):
            RayBatch(
                [(0.0, 0.0, 0.0)],
                [(0.0, 0.0, 1.0)],
                max_t=[1.0, 2.0],
            )
        with self.assertRaises(ValueError):
            RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 2.0)])
        with self.assertRaises(ValueError):
            RayBatch(
                [(0.0, 0.0, 0.0)],
                [(0.0, 0.0, 1.0)],
                ignore_faces=[0.5],
            )
        with self.assertRaises(ValueError):
            RayHitBatch(t=[1.0], face_indices=[-1])

    def test_trace_excluded_faces_are_transparent(self) -> None:
        mesh = TriangleMesh()
        for z_value, excluded in ((5.0, True), (10.0, False)):
            vertices = [
                mesh.add_vertex((-1.0, -1.0, z_value)),
                mesh.add_vertex((1.0, -1.0, z_value)),
                mesh.add_vertex((0.0, 1.0, z_value)),
            ]
            mesh.add_face(
                *vertices,
                "test",
                {"trace_excluded": excluded},
            )
        rays = RayBatch([(0.0, 0.0, 0.0)], [(0.0, 0.0, 1.0)])

        for backend in ("brute_force", "bvh"):
            with self.subTest(backend=backend):
                hits = mesh.intersect_rays(rays, backend=backend)
                self.assertEqual(hits.face_indices.tolist(), [1])
                self.assertEqual(hits.t.tolist(), [10.0])

    def test_chunked_batches_match_one_full_batch(self) -> None:
        mesh = build_box_array()
        origins, directions = build_random_rays(73, seed=20260818)
        full_rays = RayBatch(origins, directions)
        full_hits = mesh.intersect_rays(full_rays, backend="bvh")

        chunk_distances = []
        chunk_faces = []
        for start in range(0, len(full_rays), 11):
            end = min(len(full_rays), start + 11)
            chunk = RayBatch(
                origins[start:end],
                directions[start:end],
                min_t=full_rays.min_t[start:end],
                max_t=full_rays.max_t[start:end],
                ignore_faces=full_rays.ignore_faces[start:end],
            )
            chunk_hits = mesh.intersect_rays(chunk, backend="bvh")
            chunk_distances.append(chunk_hits.t)
            chunk_faces.append(chunk_hits.face_indices)

        np.testing.assert_array_equal(
            np.concatenate(chunk_faces),
            full_hits.face_indices,
        )
        np.testing.assert_array_equal(
            np.concatenate(chunk_distances),
            full_hits.t,
        )

    def test_materialized_hit_matches_scalar_and_inputs_are_not_mutated(self) -> None:
        mesh = build_parallel_triangles()
        origins = np.asarray([(0.0, 0.0, 0.0), (2.0, 2.0, 0.0)])
        directions = np.asarray([(0.0, 0.0, 1.0), (0.0, 0.0, 1.0)])
        origins_before = origins.copy()
        directions_before = directions.copy()
        rays = RayBatch(origins, directions)

        hits = mesh.intersect_rays(rays, backend="bvh")
        materialized = hits.materialize(mesh, rays, 0)
        scalar = mesh.intersect_ray(
            tuple(origins[0]),
            tuple(directions[0]),
            backend="bvh",
        )

        self.assertIsNotNone(materialized)
        self.assertIsNotNone(scalar)
        assert materialized is not None and scalar is not None
        self.assertEqual(materialized.face_index, scalar.face_index)
        self.assertTrue(math.isclose(materialized.t, scalar.t, abs_tol=1e-9))
        np.testing.assert_allclose(materialized.point, scalar.point, atol=1e-9)
        np.testing.assert_allclose(materialized.normal, scalar.normal, atol=1e-9)
        self.assertEqual(materialized.triangle, scalar.triangle)
        self.assertIsNone(hits.materialize(mesh, rays, 1))
        np.testing.assert_array_equal(origins, origins_before)
        np.testing.assert_array_equal(directions, directions_before)


if __name__ == "__main__":
    unittest.main()
