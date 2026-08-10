from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
except ImportError:  # pragma: no cover - optional CAD runtime
    BRepPrimAPI_MakeCylinder = None

from leakage_simulator.importers import _extract_brep_edge_segments


@unittest.skipUnless(BRepPrimAPI_MakeCylinder, "OCP runtime not installed")
class StepBrepEdgeTests(unittest.TestCase):
    def test_curved_brep_edges_are_sampled_without_cylinder_seam(self) -> None:
        cylinder = BRepPrimAPI_MakeCylinder(10.0, 20.0).Shape()

        segments = _extract_brep_edge_segments(
            cylinder,
            component_index=7,
            deflection_mm=0.25,
        )

        self.assertGreater(len(segments), 8)
        self.assertEqual({item["source"] for item in segments}, {"step_brep_edge"})
        self.assertEqual({item["step_component_id"] for item in segments}, {7})
        # The periodic cylinder seam is vertical and must not be displayed.
        self.assertTrue(
            all(
                abs(item["start"][2] - item["end"][2]) < 1e-9
                for item in segments
            )
        )


if __name__ == "__main__":
    unittest.main()
