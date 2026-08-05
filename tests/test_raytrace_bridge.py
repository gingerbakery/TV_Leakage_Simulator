from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.raytrace_bridge import build_direct_trace_input, build_transformed_mesh


class RayTraceBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scene_mesh = {
            "vertices": [[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 3.0, 0.0]],
            "faces": [[0, 1, 2]],
            "face_component_ids": [7],
            "face_material_ids": ["black_pc_resin"],
            "face_centroids": [[1.0, 1.0, 0.0]],
        }

    def test_component_transform_is_applied_to_direct_mesh(self) -> None:
        mesh = build_transformed_mesh(
            self.scene_mesh,
            [{
                "target_type": "component",
                "object_id": 7,
                "enabled": True,
                "move": {"x": 2.0, "y": -1.0, "z": 0.5},
                "tilt": {"x": 0.0, "y": 0.0, "z": 0.0},
            }],
        )

        self.assertEqual(mesh.face_vertices(0)[0], (2.0, -1.0, 0.5))
        self.assertEqual(mesh.metadata(0)["source_face_index"], 0)
        self.assertEqual(mesh.metadata(0)["component_id"], 7)

    def test_component_rotation_uses_viewer_bbox_center_pivot(self) -> None:
        scene_mesh = {
            "vertices": [
                [0.0, 0.0, 0.0],
                [4.0, 0.0, 0.0],
                [0.0, 2.0, 0.0],
            ],
            "faces": [[0, 1, 2]],
            "face_component_ids": [7],
            "face_material_ids": ["default"],
        }

        mesh = build_transformed_mesh(
            scene_mesh,
            [{
                "target_type": "component",
                "object_id": 7,
                "enabled": True,
                "move": {"x": 0.0, "y": 0.0, "z": 0.0},
                "tilt": {"x": 0.0, "y": 0.0, "z": 180.0},
            }],
        )

        self.assertAlmostEqual(mesh.face_vertices(0)[0][0], 4.0)
        self.assertAlmostEqual(mesh.face_vertices(0)[0][1], 2.0)

    def test_component_rotation_honors_pivot_override(self) -> None:
        scene_mesh = {
            "vertices": [
                [0.0, 0.0, 0.0],
                [4.0, 0.0, 0.0],
                [0.0, 2.0, 0.0],
            ],
            "faces": [[0, 1, 2]],
            "face_component_ids": [7],
            "face_material_ids": ["default"],
        }

        mesh = build_transformed_mesh(
            scene_mesh,
            [{
                "target_type": "component",
                "object_id": 7,
                "enabled": True,
                "move": {"x": 0.0, "y": 0.0, "z": 0.0},
                "tilt": {"x": 0.0, "y": 0.0, "z": 180.0},
                # Pivot at the origin instead of the bbox center (2, 1, 0) -
                # must change the result from the bbox-center-pivot test
                # above, and stay in sync with the frontend viewer's same
                # override (three-viewer-canvas.tsx resolveTransformPivot).
                "pivot": {"x": 0.0, "y": 0.0, "z": 0.0},
            }],
        )

        # Vertex (0, 0, 0) sits exactly on the pivot, so it must stay fixed.
        self.assertAlmostEqual(mesh.face_vertices(0)[0][0], 0.0)
        self.assertAlmostEqual(mesh.face_vertices(0)[0][1], 0.0)
        # Vertex (4, 0, 0) rotates 180 degrees around the origin, not the
        # bbox center - so it lands at (-4, 0, 0), not (0, 2, 0).
        self.assertAlmostEqual(mesh.face_vertices(0)[1][0], -4.0)
        self.assertAlmostEqual(mesh.face_vertices(0)[1][1], 0.0)

    def test_direct_input_requires_emitter_and_receiver(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one emitter"):
            build_direct_trace_input(self.scene_mesh, {"emitters": [], "receivers": []})

    def test_direct_input_parses_optical_assignments(self) -> None:
        trace_input = build_direct_trace_input(
            self.scene_mesh,
            {
                "emitters": [{
                    "emitter_id": "source",
                    "emitter_type": "datum_plane",
                    "center": [0, 0, 0],
                    "u_axis": [1, 0, 0],
                    "v_axis": [0, 1, 0],
                    "width_mm": 1,
                    "height_mm": 1,
                }],
                "receivers": [{
                    "receiver_id": "receiver",
                    "center": [0, 0, 10],
                    "normal": [0, 0, -1],
                    "width_mm": 10,
                    "height_mm": 10,
                }],
                "optical_profiles": [{
                    "profile_id": "part_profile",
                    "reflectance": 0.2,
                }],
                "optical_assignments": [{
                    "assignment_id": "part_7",
                    "target_type": "part",
                    "component_id": 7,
                    "profile_id": "part_profile",
                }],
            },
        )

        self.assertEqual(trace_input.optical_profiles[0].profile_id, "part_profile")
        self.assertEqual(trace_input.optical_assignments[0].component_id, 7)

    def test_excluded_component_is_removed_from_direct_mesh(self) -> None:
        scene_mesh = {
            "vertices": [
                [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                [0.0, 0.0, 2.0], [1.0, 0.0, 2.0], [0.0, 1.0, 2.0],
            ],
            "faces": [[0, 1, 2], [3, 4, 5]],
            "face_component_ids": [7, 8],
            "face_material_ids": ["deleted", "kept"],
            "face_centroids": [[1.0 / 3.0, 1.0 / 3.0, 0.0], [1.0 / 3.0, 1.0 / 3.0, 2.0]],
        }

        mesh = build_transformed_mesh(scene_mesh, [], excluded_component_ids=[7])

        self.assertEqual(len(mesh.faces), 1)
        self.assertEqual(mesh.metadata(0)["source_face_index"], 1)
        self.assertEqual(mesh.metadata(0)["component_id"], 8)
        self.assertEqual(mesh.material_id(0), "kept")

    def test_face_emitter_is_remapped_after_component_deletion(self) -> None:
        scene_mesh = {
            "vertices": [
                [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                [0.0, 0.0, 2.0], [1.0, 0.0, 2.0], [0.0, 1.0, 2.0],
            ],
            "faces": [[0, 1, 2], [3, 4, 5]],
            "face_component_ids": [7, 8],
            "face_material_ids": ["deleted", "kept"],
            "face_centroids": [[1.0 / 3.0, 1.0 / 3.0, 0.0], [1.0 / 3.0, 1.0 / 3.0, 2.0]],
        }
        trace_input = build_direct_trace_input(
            scene_mesh,
            {
                "excluded_component_ids": [7],
                "emitters": [{
                    "emitter_id": "source",
                    "emitter_type": "face",
                    "face_indices": [1],
                }],
                "receivers": [{
                    "receiver_id": "receiver",
                    "center": [0, 0, 10],
                    "normal": [0, 0, -1],
                    "width_mm": 10,
                    "height_mm": 10,
                }],
            },
        )

        self.assertEqual(len(trace_input.mesh.faces), 1)
        self.assertEqual(trace_input.emitters[0].face_indices, [0])
        self.assertEqual(trace_input.mesh.metadata(0)["source_face_index"], 1)


class RoiFilteringTests(unittest.TestCase):
    """ROI 담당자와 협의된 컨셉: ROI를 지정하면 그 영역만 분석한다 (raytrace_bridge.py
    가 build_direct_trace_input 안에서 필터링을 담당 - docs/changes/2026-07-20_roi-native-selection.md
    참고)."""

    def setUp(self) -> None:
        # Two well-separated faces so a single-face ROI unambiguously
        # keeps one and drops the other.
        self.scene_mesh = {
            "vertices": [
                [0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 3.0, 0.0],
                [10.0, 0.0, 0.0], [13.0, 0.0, 0.0], [10.0, 3.0, 0.0],
            ],
            "faces": [[0, 1, 2], [3, 4, 5]],
            "face_component_ids": [7, 8],
            "face_material_ids": ["black_pc_resin", "gray_pc_resin"],
            "face_centroids": [[1.0, 1.0, 0.0], [11.0, 1.0, 0.0]],
        }

    def _payload(self, roi_faces=None, extra_assignments=None):
        payload = {
            "emitters": [{
                "emitter_id": "e1",
                "emitter_type": "face",
                "face_indices": [0],
            }],
            "receivers": [{
                "receiver_id": "r1",
                "center": [0, 0, 10],
                "normal": [0, 0, -1],
                "width_mm": 10,
                "height_mm": 10,
            }],
            "optical_assignments": extra_assignments or [],
        }
        if roi_faces is not None:
            payload["roi_faces"] = roi_faces
        return payload

    def test_no_roi_faces_keeps_full_mesh(self) -> None:
        trace_input = build_direct_trace_input(self.scene_mesh, self._payload())
        self.assertEqual(len(trace_input.mesh.faces), 2)
        self.assertEqual(trace_input.emitters[0].face_indices, [0])

    def test_roi_faces_trims_mesh_and_remaps_face_emitter(self) -> None:
        trace_input = build_direct_trace_input(self.scene_mesh, self._payload(roi_faces=[0]))
        self.assertEqual(len(trace_input.mesh.faces), 1)
        # Original face 0 is the only survivor, so it must land at the
        # trimmed mesh's face 0 regardless of the remap's internal detail.
        self.assertEqual(trace_input.emitters[0].face_indices, [0])
        self.assertEqual(trace_input.mesh.metadata(0)["source_face_index"], 0)

    def test_roi_transform_material_emitter_receiver_pipeline_stays_aligned(self) -> None:
        payload = self._payload(
            roi_faces=[0],
            extra_assignments=[{
                "assignment_id": "part_7",
                "target_type": "part",
                "component_id": 7,
                "profile_id": "black_profile",
            }],
        )
        payload["optical_profiles"] = [{
            "profile_id": "black_profile",
            "reflectance": 0.1,
        }]
        payload["transform_rules"] = [{
            "rule_id": "move_7",
            "target_type": "component",
            "object_id": 7,
            "enabled": True,
            "move": {"x": 1.5, "y": 0.0, "z": 0.0},
            "tilt": {"x": 0.0, "y": 0.0, "z": 0.0},
        }]

        trace_input = build_direct_trace_input(self.scene_mesh, payload)

        self.assertEqual(len(trace_input.mesh.faces), 1)
        self.assertEqual(trace_input.mesh.face_vertices(0)[0], (1.5, 0.0, 0.0))
        self.assertEqual(trace_input.mesh.metadata(0)["source_face_index"], 0)
        self.assertEqual(trace_input.emitters[0].face_indices, [0])
        self.assertEqual(trace_input.receivers[0].receiver_id, "r1")
        self.assertEqual(trace_input.optical_assignments[0].component_id, 7)

    def test_roi_faces_excluding_all_emitter_faces_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "no faces left inside the selected ROI"):
            build_direct_trace_input(self.scene_mesh, self._payload(roi_faces=[1]))

    def test_roi_faces_partially_trims_face_optical_assignment(self) -> None:
        assignments = [{
            "assignment_id": "faces_override",
            "target_type": "faces",
            "component_id": 7,
            "face_indices": [0, 1],
            "profile_id": "part_profile",
        }]
        trace_input = build_direct_trace_input(
            self.scene_mesh, self._payload(roi_faces=[0], extra_assignments=assignments)
        )
        # Face 1 is outside the ROI and silently dropped (material overrides
        # commonly span faces outside any one ROI - see raytrace_bridge.py's
        # _remap_face_optical_assignments), leaving just remapped face 0.
        self.assertEqual(trace_input.optical_assignments[0].face_indices, [0])

    def test_component_exclusion_and_roi_share_one_face_remap(self) -> None:
        payload = self._payload(
            roi_faces=[1],
            extra_assignments=[{
                "assignment_id": "faces_override",
                "target_type": "faces",
                "component_id": 8,
                "face_indices": [1],
                "profile_id": "part_profile",
            }],
        )
        payload["excluded_component_ids"] = [7]
        payload["emitters"][0]["face_indices"] = [1]

        trace_input = build_direct_trace_input(self.scene_mesh, payload)

        self.assertEqual(len(trace_input.mesh.faces), 1)
        self.assertEqual(trace_input.mesh.metadata(0)["source_face_index"], 1)
        self.assertEqual(trace_input.emitters[0].face_indices, [0])
        self.assertEqual(trace_input.optical_assignments[0].face_indices, [0])

    def test_face_optical_assignment_is_remapped_without_roi(self) -> None:
        payload = self._payload(
            extra_assignments=[{
                "assignment_id": "faces_override",
                "target_type": "faces",
                "component_id": 8,
                "face_indices": [1],
                "profile_id": "part_profile",
            }],
        )
        payload["excluded_component_ids"] = [7]
        payload["emitters"][0]["face_indices"] = [1]

        trace_input = build_direct_trace_input(self.scene_mesh, payload)

        self.assertEqual(trace_input.optical_assignments[0].face_indices, [0])

    def test_empty_roi_faces_list_is_treated_as_no_filter(self) -> None:
        trace_input = build_direct_trace_input(self.scene_mesh, self._payload(roi_faces=[]))
        self.assertEqual(len(trace_input.mesh.faces), 2)


if __name__ == "__main__":
    unittest.main()
