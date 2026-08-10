from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.importers import import_geometry
from leakage_simulator.roi import build_scene_payload

SAMPLE_STEP = ROOT / "samples" / "tv_leakage_full_assembled_no_gap.stp"
EXPECTED_COMPONENT_COLORS = {
    "Chassis_Rear": "#07080b",
    "LCD_Cell_3T": "#02060e",
    "Frame_Middle_FMB": "#010101",
    "Cover_Deco": "#000000",
}


def _raw_step_bbox(path: Path):
    """Independent bounding box read straight off the STEP file's own
    coordinates, bypassing our importer entirely - the ground truth for
    "what NX actually authored" that the import pipeline must not shift."""
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    assert reader.ReadFile(str(path)) == IFSelect_RetDone
    reader.TransferRoots()
    shape = reader.OneShape()

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    x_min, y_min, z_min, x_max, y_max, z_max = box.Get()
    return (x_min, y_min, z_min), (x_max, y_max, z_max)


@unittest.skipUnless(SAMPLE_STEP.exists(), "sample STEP fixture not present")
class StepImportPreservesAbsoluteOriginTests(unittest.TestCase):
    """Locks in a product requirement: the mechanism drawing's own (0, 0, 0)
    - whatever NX authored it as - must remain the tool's absolute origin.
    Import must never re-center, normalize, or otherwise translate the
    model, in Full View or ROI View, for this file or any other."""

    def test_import_geometry_does_not_translate_the_model(self) -> None:
        raw_min, raw_max = _raw_step_bbox(SAMPLE_STEP)

        result = import_geometry(str(SAMPLE_STEP))
        self.assertFalse(result.synthetic)

        xs = [v[0] for v in result.mesh.vertices]
        ys = [v[1] for v in result.mesh.vertices]
        zs = [v[2] for v in result.mesh.vertices]
        mesh_min = (min(xs), min(ys), min(zs))
        mesh_max = (max(xs), max(ys), max(zs))

        # Tessellation deflection can shave a small chord height off curved
        # surfaces, but must not shift the model - a wide tolerance still
        # catches any accidental centering/normalization offset (which would
        # be on the order of the model's own half-extent, not sub-mm).
        tolerance_mm = 2.0
        for raw_value, mesh_value in zip(raw_min, mesh_min):
            self.assertAlmostEqual(raw_value, mesh_value, delta=tolerance_mm)
        for raw_value, mesh_value in zip(raw_max, mesh_max):
            self.assertAlmostEqual(raw_value, mesh_value, delta=tolerance_mm)

    def test_scene_payload_component_bboxes_use_the_same_absolute_origin(self) -> None:
        raw_min, raw_max = _raw_step_bbox(SAMPLE_STEP)

        payload = build_scene_payload(str(SAMPLE_STEP))
        components = payload["components"]
        self.assertTrue(components)

        payload_min = [
            min(component["bbox_min"][axis] for component in components)
            for axis in range(3)
        ]
        payload_max = [
            max(component["bbox_max"][axis] for component in components)
            for axis in range(3)
        ]

        tolerance_mm = 2.0
        for raw_value, payload_value in zip(raw_min, payload_min):
            self.assertAlmostEqual(raw_value, payload_value, delta=tolerance_mm)
        for raw_value, payload_value in zip(raw_max, payload_max):
            self.assertAlmostEqual(raw_value, payload_value, delta=tolerance_mm)

    def test_step_component_names_and_colors_reach_scene_payload(self) -> None:
        result = import_geometry(str(SAMPLE_STEP))
        imported_components = {}
        for face_index in range(len(result.mesh.faces)):
            metadata = result.mesh.metadata(face_index)
            component_name = metadata.get("step_component_name")
            if component_name:
                imported_components[component_name] = metadata.get(
                    "step_component_color"
                )

        self.assertEqual(imported_components, EXPECTED_COMPONENT_COLORS)

        payload = build_scene_payload(str(SAMPLE_STEP))
        payload_components = {
            component["component_name"]: component.get("color")
            for component in payload["components"]
        }
        self.assertEqual(payload_components, EXPECTED_COMPONENT_COLORS)
        self.assertIn(
            "ocp_product_structure",
            payload["metadata"]["import_timings_sec"],
        )
        self.assertTrue(payload["mesh"]["feature_edge_segments"])
        self.assertIn(
            "edges from STEP B-rep topology",
            payload["metadata"]["import_note"],
        )


if __name__ == "__main__":
    unittest.main()
