from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.components import build_face_groups
from leakage_simulator.importers import import_geometry

try:
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Pnt

    OCP_AVAILABLE = True
except ImportError:
    OCP_AVAILABLE = False


@unittest.skipUnless(OCP_AVAILABLE, "OCP STEP runtime not installed")
class StepMultiBodyComponentTests(unittest.TestCase):
    def test_one_product_item_with_six_solids_stays_six_selectable_components(
        self,
    ) -> None:
        application = XCAFApp_Application.GetApplication_s()
        document = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
        application.NewDocument(
            TCollection_ExtendedString("MDTV-XCAF"),
            document,
        )
        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())

        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        for index in range(6):
            builder.Add(
                compound,
                BRepPrimAPI_MakeBox(
                    gp_Pnt(index * 8.0, 0.0, 0.0),
                    5.0,
                    5.0,
                    5.0,
                ).Shape(),
            )

        label = shape_tool.AddShape(compound, False)
        TDataStd_Name.Set_s(
            label,
            TCollection_ExtendedString("MultiBody"),
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            step_path = Path(temporary_directory) / "one_item_six_bodies.step"
            writer = STEPCAFControl_Writer()
            writer.Transfer(document, STEPControl_AsIs)
            writer.Write(str(step_path))

            result = import_geometry(str(step_path), defer_trace_mesh=True)
            components = build_face_groups(result.mesh)
            self.assertIsNotNone(result.trace_mesh_loader)
            trace_components = build_face_groups(result.trace_mesh_loader())

        self.assertEqual(len(components), 6)
        self.assertEqual(
            [component["object_id"] for component in components],
            list(range(6)),
        )
        self.assertEqual(
            len({component["object_name"] for component in components}),
            6,
        )
        self.assertEqual(len(trace_components), 6)
        self.assertEqual(
            [component["object_name"] for component in trace_components],
            [component["object_name"] for component in components],
        )


if __name__ == "__main__":
    unittest.main()
