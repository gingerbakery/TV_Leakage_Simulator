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


if __name__ == "__main__":
    unittest.main()
