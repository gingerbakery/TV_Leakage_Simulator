from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import (
    TriangleMesh,
    build_feature_edge_segments,
    choose_adaptive_subdivision_area_mm2,
    estimate_subdivided_face_count,
    subdivide_flat_mesh,
)


class FeatureEdgeSegmentTests(unittest.TestCase):
    def test_coplanar_quad_diagonal_is_not_a_feature_edge(self) -> None:
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((10.0, 0.0, 0.0))
        v2 = mesh.add_vertex((10.0, 10.0, 0.0))
        v3 = mesh.add_vertex((0.0, 10.0, 0.0))
        mesh.add_face(v0, v1, v2, "mat", {})
        mesh.add_face(v0, v2, v3, "mat", {})

        segments = build_feature_edge_segments(mesh)

        self.assertEqual(len(segments), 4)
        endpoints = {
            frozenset((tuple(segment["start"]), tuple(segment["end"])))
            for segment in segments
        }
        self.assertNotIn(frozenset(((0.0, 0.0, 0.0), (10.0, 10.0, 0.0))), endpoints)

    def test_folded_triangles_keep_their_shared_feature_edge(self) -> None:
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((10.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 10.0, 0.0))
        v3 = mesh.add_vertex((0.0, 0.0, 10.0))
        mesh.add_face(v0, v1, v2, "mat", {})
        mesh.add_face(v0, v3, v1, "mat", {})

        segments = build_feature_edge_segments(mesh)

        self.assertEqual(len(segments), 5)


class SubdivideFlatMeshTests(unittest.TestCase):
    def test_small_face_is_left_untouched(self) -> None:
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((1.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 1.0, 0.0))
        mesh.add_face(v0, v1, v2, "mat", {"component_id": 1})

        result = subdivide_flat_mesh(mesh, max_area_mm2=100.0)

        self.assertEqual(len(result.faces), 1)
        self.assertAlmostEqual(result.area(0), mesh.area(0), places=6)
        self.assertEqual(result.metadata(0)["component_id"], 1)

    def test_large_face_is_split_below_threshold(self) -> None:
        # A 100x100 right triangle, area 5000 mm^2 - well above any
        # reasonable threshold, matching a STEP-tessellated flat panel
        # that comes out as one or two huge triangles.
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((100.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 100.0, 0.0))
        mesh.add_face(v0, v1, v2, "black_pc_resin", {"step_component_id": 7})

        result = subdivide_flat_mesh(mesh, max_area_mm2=50.0)

        self.assertGreater(len(result.faces), 1)
        for face_index in range(len(result.faces)):
            self.assertLessEqual(result.area(face_index), 50.0 + 1e-6)
            # Metadata/material must carry over to every sub-face.
            self.assertEqual(result.material_id(face_index), "black_pc_resin")
            self.assertEqual(result.metadata(face_index)["step_component_id"], 7)

    def test_total_area_is_preserved(self) -> None:
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((60.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 40.0, 0.0))
        mesh.add_face(v0, v1, v2, "mat", {})
        original_area = mesh.area(0)

        result = subdivide_flat_mesh(mesh, max_area_mm2=10.0)

        total_area = sum(result.area(i) for i in range(len(result.faces)))
        self.assertAlmostEqual(total_area, original_area, places=6)

    def test_shared_edge_vertices_are_deduplicated(self) -> None:
        # Two large triangles sharing the edge (10,0)-(0,10) - after
        # subdivision, the midpoint of that shared edge must be the same
        # vertex in both results, not a duplicate created independently
        # per triangle (which would leave a visible seam/crack and bloat
        # vertex count for no reason).
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((10.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 10.0, 0.0))
        v3 = mesh.add_vertex((10.0, 10.0, 0.0))
        mesh.add_face(v0, v1, v3, "mat", {})  # shares edge v1-v3... use v1,v3 diag differently
        mesh.add_face(v0, v3, v2, "mat", {})

        result = subdivide_flat_mesh(mesh, max_area_mm2=5.0)

        midpoint_key_count = 0
        seen = set()
        for vertex in result.vertices:
            key = (round(vertex[0], 3), round(vertex[1], 3), round(vertex[2], 3))
            if key in seen:
                midpoint_key_count += 1
            seen.add(key)
        # No duplicated coordinates at all - every unique position appears once.
        self.assertEqual(midpoint_key_count, 0)

    def test_max_depth_bounds_the_subdivision(self) -> None:
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((1000.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 1000.0, 0.0))
        mesh.add_face(v0, v1, v2, "mat", {})

        result = subdivide_flat_mesh(mesh, max_area_mm2=0.001, max_depth=2)

        # 1-to-4 split twice -> at most 4^2 = 16 faces, even though the
        # area threshold alone would ask for far more subdivision.
        self.assertLessEqual(len(result.faces), 16)

    def test_adaptive_target_scales_with_cad_size(self) -> None:
        def triangle(size_mm: float) -> TriangleMesh:
            mesh = TriangleMesh()
            v0 = mesh.add_vertex((0.0, 0.0, 0.0))
            v1 = mesh.add_vertex((size_mm, 0.0, 0.0))
            v2 = mesh.add_vertex((0.0, size_mm, 0.0))
            mesh.add_face(v0, v1, v2, "mat", {})
            return mesh

        small_target = choose_adaptive_subdivision_area_mm2(
            triangle(100.0),
            max_output_faces=1_000_000,
        )
        large_target = choose_adaptive_subdivision_area_mm2(
            triangle(1000.0),
            max_output_faces=1_000_000,
        )

        self.assertGreater(large_target, small_target)
        self.assertNotEqual(small_target, 10.0)
        self.assertNotEqual(large_target, 10.0)

    def test_adaptive_target_stays_within_face_budget(self) -> None:
        mesh = TriangleMesh()
        v0 = mesh.add_vertex((0.0, 0.0, 0.0))
        v1 = mesh.add_vertex((1000.0, 0.0, 0.0))
        v2 = mesh.add_vertex((0.0, 1000.0, 0.0))
        mesh.add_face(v0, v1, v2, "mat", {})

        target = choose_adaptive_subdivision_area_mm2(
            mesh,
            max_output_faces=64,
            max_depth=9,
        )

        self.assertLessEqual(
            estimate_subdivided_face_count(mesh, target, max_depth=9),
            64,
        )
        result = subdivide_flat_mesh(mesh, target, max_depth=9)
        self.assertLessEqual(len(result.faces), 64)


if __name__ == "__main__":
    unittest.main()
