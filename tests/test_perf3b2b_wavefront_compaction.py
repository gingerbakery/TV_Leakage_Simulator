from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_multibounce_rt3 import ten_bounce_corridor_input

from leakage_simulator.geometry import RayBatch, RayHitBatch, TriangleMesh
from leakage_simulator.raytracer import (
    _WavefrontStoredPathQuota,
    _store_completed_path,
    run_direct_ray_trace,
)
from leakage_simulator.types import RayHit


def _build_tilted_triangle() -> TriangleMesh:
    mesh = TriangleMesh()
    vertices = [
        mesh.add_vertex(point)
        for point in (
            (-2.0, -2.0, 5.0),
            (2.0, -2.0, 5.0),
            (0.0, 2.0, 6.0),
        )
    ]
    mesh.add_face(*vertices, "tilted")
    return mesh


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _path(tag: int, terminal_kind: str) -> list[RayHit]:
    receiver = terminal_kind == "receiver"
    return [
        RayHit(
            face_index=-1 if receiver else tag,
            component_id=None if receiver else tag,
            material_id=None if receiver else f"material_{tag}",
            point=(float(tag), 0.0, 0.0),
            normal=(0.0, 0.0, 1.0),
            distance_mm=float(tag) + 0.25,
            incoming_energy_lumen=1.0,
            outgoing_energy_lumen=1.0,
            depth=tag,
            event_type="receiver" if receiver else "surface",
            receiver_id=f"receiver_{tag}" if receiver else None,
            receiver_flux_lumen=1.0 if receiver else None,
        )
    ]


def _path_tags(paths: list[list[RayHit]]) -> list[int]:
    return [int(path[-1].point[0]) for path in paths]


def _legacy_can_store(
    paths: list[list[RayHit]],
    terminal_kind: str,
    max_paths: int,
) -> bool:
    if max_paths <= 0:
        return False
    if len(paths) < max_paths:
        return True
    if terminal_kind != "receiver":
        return False
    return any(path[-1].event_type != "receiver" for path in paths)


class Perf3B2BWavefrontCompactionTests(unittest.TestCase):
    def assertFloatBitsEqual(self, actual, expected) -> None:
        actual_array = np.ascontiguousarray(
            np.asarray(actual, dtype=np.float64)
        )
        expected_array = np.ascontiguousarray(
            np.asarray(expected, dtype=np.float64)
        )
        self.assertEqual(actual_array.shape, expected_array.shape)
        np.testing.assert_array_equal(
            actual_array.view(np.uint64),
            expected_array.view(np.uint64),
        )

    def test_surface_geometry_matches_scalar_bits_and_preserves_inputs(self) -> None:
        mesh = _build_tilted_triangle()
        origins = np.asarray(
            [
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 10.0),
                (10.0, 10.0, 0.0),
            ],
            dtype=np.float64,
        )
        directions = np.asarray(
            [
                (0.0, 0.0, 1.0),
                (0.0, 0.0, -1.0),
                (0.0, 0.0, 1.0),
            ],
            dtype=np.float64,
        )
        rays = RayBatch(origins, directions)
        hits = mesh.intersect_rays(rays, backend="bvh")
        prepared_normals = mesh.prepared_triangle_normals()

        origins_before = origins.copy()
        directions_before = directions.copy()
        ray_origins_before = rays.origins.copy()
        ray_directions_before = rays.directions.copy()
        distances_before = hits.t.copy()
        faces_before = hits.face_indices.copy()
        prepared_before = prepared_normals.copy()

        geometry = hits.materialize_surface_geometry(mesh, rays)

        self.assertEqual(geometry.points.dtype, np.float64)
        self.assertEqual(geometry.normals.dtype, np.float64)
        self.assertEqual(geometry.points.shape, (3, 3))
        self.assertEqual(geometry.normals.shape, (3, 3))
        self.assertEqual(hits.face_indices.tolist(), [0, 0, -1])
        for index in range(len(rays)):
            with self.subTest(index=index):
                scalar = hits.materialize(mesh, rays, index)
                if scalar is None:
                    self.assertEqual(int(hits.face_indices[index]), -1)
                    self.assertFloatBitsEqual(geometry.points[index], (0.0, 0.0, 0.0))
                    self.assertFloatBitsEqual(geometry.normals[index], (0.0, 0.0, 0.0))
                    continue
                self.assertEqual(int(hits.face_indices[index]), scalar.face_index)
                self.assertFloatBitsEqual(geometry.points[index], scalar.point)
                self.assertFloatBitsEqual(geometry.normals[index], scalar.normal)

        self.assertFloatBitsEqual(origins, origins_before)
        self.assertFloatBitsEqual(directions, directions_before)
        self.assertFloatBitsEqual(rays.origins, ray_origins_before)
        self.assertFloatBitsEqual(rays.directions, ray_directions_before)
        self.assertFloatBitsEqual(hits.t, distances_before)
        np.testing.assert_array_equal(hits.face_indices, faces_before)
        self.assertFloatBitsEqual(prepared_normals, prepared_before)
        self.assertIs(mesh.prepared_triangle_normals(), prepared_normals)
        self.assertFalse(prepared_normals.flags.writeable)
        self.assertFalse(np.shares_memory(geometry.points, rays.origins))
        self.assertFalse(np.shares_memory(geometry.normals, prepared_normals))
        with self.assertRaises(ValueError):
            prepared_normals[0, 0] = 123.0

    def test_prepared_normals_are_read_only_and_mesh_mutations_invalidate_cache(
        self,
    ) -> None:
        mesh = _build_tilted_triangle()
        first = mesh.prepared_triangle_normals()
        first_snapshot = first.copy()

        new_vertices = [
            mesh.add_vertex(point)
            for point in (
                (5.0, -2.0, -1.0),
                (5.0, 2.0, -1.0),
                (5.0, 0.0, 2.0),
            )
        ]
        after_vertices = mesh.prepared_triangle_normals()
        self.assertIsNot(after_vertices, first)
        self.assertFalse(after_vertices.flags.writeable)
        self.assertFloatBitsEqual(after_vertices, first_snapshot)

        second_face = mesh.add_face(*new_vertices, "vertical")
        after_face = mesh.prepared_triangle_normals()
        self.assertIsNot(after_face, after_vertices)
        self.assertEqual(after_face.shape, (2, 3))
        self.assertFalse(after_face.flags.writeable)
        self.assertFloatBitsEqual(
            after_face,
            np.asarray([mesh.normal(0), mesh.normal(1)], dtype=np.float64),
        )

        rays = RayBatch([(0.0, 0.0, 0.0)], [(1.0, 0.0, 0.0)])
        hits = mesh.intersect_rays(rays, backend="bvh")
        geometry = hits.materialize_surface_geometry(mesh, rays)
        scalar = hits.materialize(mesh, rays, 0)
        self.assertIsNotNone(scalar)
        assert scalar is not None
        self.assertEqual(scalar.face_index, second_face)
        self.assertFloatBitsEqual(geometry.points[0], scalar.point)
        self.assertFloatBitsEqual(geometry.normals[0], scalar.normal)

    def test_surface_geometry_preserves_normal_orientation_nextafter_boundary(
        self,
    ) -> None:
        mesh = TriangleMesh()
        vertices = [
            mesh.add_vertex(point)
            for point in ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0))
        ]
        mesh.add_face(*vertices, "boundary")
        below_zero = np.nextafter(0.0, -1.0)
        above_zero = np.nextafter(0.0, 1.0)
        rays = RayBatch(
            [(0.0, 0.0, 0.0)] * 3,
            [(1.0, 0.0, below_zero), (1.0, 0.0, 0.0), (1.0, 0.0, above_zero)],
        )
        hits = RayHitBatch(
            np.asarray([1.0, 1.0, 1.0], dtype=np.float64),
            np.asarray([0, 0, 0], dtype=np.int64),
        )

        geometry = hits.materialize_surface_geometry(mesh, rays)

        self.assertEqual(geometry.hit_count, 3)
        for index in range(3):
            scalar = hits.materialize(mesh, rays, index)
            self.assertIsNotNone(scalar)
            assert scalar is not None
            self.assertFloatBitsEqual(geometry.points[index], scalar.point)
            self.assertFloatBitsEqual(geometry.normals[index], scalar.normal)

    def test_stored_path_quota_matches_legacy_receiver_priority_order(self) -> None:
        stream = [
            (0, "blocked"),
            (1, "escaped"),
            (2, "receiver"),
            (3, "blocked"),
            (4, "receiver"),
            (5, "receiver"),
        ]
        expected_tags = {
            0: [],
            1: [2],
            2: [2, 4],
            12: [0, 1, 2, 3, 4, 5],
        }

        for max_paths in (0, 1, 2, 12):
            with self.subTest(max_paths=max_paths):
                legacy_paths: list[list[RayHit]] = []
                quota_paths: list[list[RayHit]] = []
                quota = _WavefrontStoredPathQuota.from_paths(
                    quota_paths,
                    max_paths,
                )
                for index, (tag, terminal_kind) in enumerate(stream):
                    candidate = _path(tag, terminal_kind)
                    self.assertEqual(
                        quota.can_store(terminal_kind),
                        _legacy_can_store(
                            legacy_paths,
                            terminal_kind,
                            max_paths,
                        ),
                    )
                    quota.store(candidate, terminal_kind)
                    _store_completed_path(
                        legacy_paths,
                        candidate,
                        max_paths,
                    )
                    self.assertEqual(quota_paths, legacy_paths)

                    # Production reconstructs this cache at each primary chunk.
                    if index in {1, 3}:
                        quota = _WavefrontStoredPathQuota.from_paths(
                            quota_paths,
                            max_paths,
                        )

                self.assertEqual(_path_tags(quota_paths), expected_tags[max_paths])
                self.assertEqual(
                    list(quota.dead_end_indices),
                    [
                        index
                        for index, path in enumerate(quota_paths)
                        if path[-1].event_type != "receiver"
                    ],
                )

    def test_depth_ten_summary_and_detailed_preserve_2a_payload_and_key_order(
        self,
    ) -> None:
        for contribution_mode in ("summary", "detailed"):
            with self.subTest(contribution_mode=contribution_mode):
                scalar_input = ten_bounce_corridor_input(max_depth=10)
                scalar_input.config.contribution_mode = contribution_mode
                wavefront_input = ten_bounce_corridor_input(max_depth=10)
                wavefront_input.config.contribution_mode = contribution_mode

                scalar = run_direct_ray_trace(
                    scalar_input,
                    intersection_dispatch="scalar",
                    intersection_provider="python_cpu",
                )
                wavefront = run_direct_ray_trace(
                    wavefront_input,
                    intersection_dispatch="batch",
                    intersection_batch_size=17,
                    intersection_provider="python_cpu",
                )

                scalar_payload = _semantic_payload(scalar)
                wavefront_payload = _semantic_payload(wavefront)
                self.assertEqual(wavefront_payload, scalar_payload)
                self.assertEqual(
                    json.dumps(
                        wavefront_payload,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                    json.dumps(
                        scalar_payload,
                        allow_nan=False,
                        separators=(",", ":"),
                    ),
                )

                contribution = wavefront.contribution_summary.to_dict()
                if contribution_mode == "summary":
                    self.assertEqual(list(contribution["faces"]), [])
                    self.assertEqual(list(contribution["components"]), [])
                    self.assertEqual(list(contribution["materials"]), [])
                    self.assertEqual(
                        list(contribution["depths"]),
                        [str(depth) for depth in range(1, 11)],
                    )
                else:
                    self.assertEqual(
                        list(contribution["faces"]),
                        ["1", "2", "3", "0"],
                    )
                    self.assertEqual(
                        list(contribution["components"]),
                        ["301", "302"],
                    )
                    self.assertEqual(
                        list(contribution["materials"]),
                        ["high_reflector"],
                    )
                    self.assertEqual(
                        list(contribution["depths"]),
                        [str(depth) for depth in range(11)],
                    )
                self.assertEqual(
                    list(wavefront.metrics["_optical_summary"]["profile_hits"]),
                    ["high_reflector"],
                )
                self.assertEqual(
                    list(wavefront.metrics["_reflection_summary"]["depths"]),
                    [str(depth) for depth in range(1, 11)],
                )

                performance = wavefront.metrics["_performance_summary"]
                self.assertIn("wavefront_geometry_sec", performance)
                timing_fields = [
                    key
                    for key in performance
                    if key.startswith("wavefront_") and key.endswith("_sec")
                ]
                self.assertGreaterEqual(len(timing_fields), 6)
                for key in timing_fields:
                    with self.subTest(
                        contribution_mode=contribution_mode,
                        timing_field=key,
                    ):
                        value = performance[key]
                        self.assertIs(type(value), float)
                        self.assertTrue(math.isfinite(value))
                        self.assertGreaterEqual(value, 0.0)
                self.assertEqual(performance["intersection_ray_count"], 1100)
                json.dumps(wavefront.to_dict(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
