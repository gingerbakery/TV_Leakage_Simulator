from __future__ import annotations

import unittest

from leakage_simulator.section_cap import build_section_cap_contours


def cube_mesh() -> dict[str, object]:
    vertices = [
        [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    ]
    faces = [
        [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
    ]
    return {
        "vertices": vertices,
        "faces": faces,
        "face_component_ids": [7] * len(faces),
    }


class SectionCapTests(unittest.TestCase):
    def test_cube_center_cut_returns_one_closed_contour(self) -> None:
        result = build_section_cap_contours(
            cube_mesh(), axis="x", position=0.0
        )

        self.assertEqual(result["open_chain_count"], 0)
        self.assertEqual(len(result["contours"]), 1)
        contour = result["contours"][0]
        self.assertEqual(contour["component_id"], 7)
        self.assertGreaterEqual(len(contour["points"]), 4)
        self.assertTrue(
            all(abs(point[0]) < 1.0e-8 for point in contour["points"])
        )

    def test_hidden_component_is_not_capped(self) -> None:
        result = build_section_cap_contours(
            cube_mesh(),
            axis="z",
            position=0.0,
            hidden_component_ids=[7],
        )
        self.assertEqual(result["contours"], [])

    def test_component_transform_moves_the_cut_geometry(self) -> None:
        result = build_section_cap_contours(
            cube_mesh(),
            axis="x",
            position=5.0,
            transform_rules=[{
                "enabled": True,
                "targetType": "component",
                "componentId": 7,
                "move": {"x": 5, "y": 0, "z": 0},
                "tilt": {"x": 0, "y": 0, "z": 0},
            }],
        )
        self.assertEqual(result["open_chain_count"], 0)
        self.assertEqual(len(result["contours"]), 1)
        self.assertTrue(
            all(abs(point[0] - 5.0) < 1.0e-8 for point in result["contours"][0]["points"])
        )


if __name__ == "__main__":
    unittest.main()
