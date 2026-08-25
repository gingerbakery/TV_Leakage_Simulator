from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple, List

from .geometry import (
    TriangleMesh,
    build_feature_edge_segments,
    choose_adaptive_subdivision_area_mm2,
    subdivide_flat_mesh,
)
from .materials import default_material_library
from .synth import generate_synthetic_leakage_scene
from .types import EmitterConfig

cq = None
_cadquery_checked = False
BRep_Tool = None
BRepTools = None
BRepMesh_IncrementalMesh = None
IFSelect_RetDone = None
STEPControl_Reader = None
TopAbs_FACE = None
TopAbs_SOLID = None
TopExp_Explorer = None
TopLoc_Location = None
TopoDS = None
ocp_available = None

# XCAF (product structure) reader - separate optional dependency set from the
# plain STEPControl_Reader above. NX (and most other MCAD tools) writes each
# component's name and color into the STEP AP214/AP242 product structure,
# which only the CAF (CAD Application Framework) reader exposes; the plain
# geometry-only reader used above cannot see it at all.
STEPCAFControl_Reader = None
TDocStd_Document = None
XCAFApp_Application = None
XCAFDoc_DocumentTool = None
XCAFDoc_ColorType = None
TDF_Label = None
TDF_LabelSequence = None
TDataStd_Name = None
Quantity_Color = None
TCollection_ExtendedString = None
ocp_xcaf_available = None

# STEP tessellation can leave flat panels as only one or two huge triangles.
# Subdivision improves ROI picking resolution, but it does not improve the
# underlying CAD curvature or ray-intersection accuracy. The current corner
# ROI workflow uses regions up to roughly 50 mm, so a 1.5 mm lower edge target
# avoids display-only triangle explosions while preserving useful selection.
ROI_SUBDIVISION_TARGET_DIVISIONS = 128
ROI_SUBDIVISION_MIN_EDGE_MM = 1.5
ROI_SUBDIVISION_MAX_EDGE_MM = 5.0
ROI_SUBDIVISION_MAX_FACES = 150_000
ROI_SUBDIVISION_MAX_DEPTH = 9
ROI_SUBDIVISION_AUTO_SKIP_RAW_FACES = 50_000
ROI_SUBDIVISION_SKIPPED_FAST = -1.0
ROI_SUBDIVISION_SKIPPED_DENSE_MESH = -2.0
ROI_SUBDIVISION_SKIPPED_DEFERRED = -3.0
CAD_FAST_IMPORT_ENV = "LEAKAGE_CAD_FAST_IMPORT"
CAD_FORCE_ROI_SUBDIVISION_ENV = "LEAKAGE_CAD_FORCE_ROI_SUBDIVISION"
CAD_SKIP_PRODUCT_METADATA_ENV = "LEAKAGE_CAD_SKIP_PRODUCT_METADATA"

# Keep the simulation tessellation independent from the interactive display.
# A 0.2 mm / ~9 degree display mesh remains visually smooth at TV-mechanism
# scale, while the denser 0.08 mm / ~5.7 degree trace mesh is retained for ray
# intersection. Both levels preserve the same authored B-rep face IDs.
VIEWER_LINEAR_DEFLECTION_MM = 0.20
VIEWER_ANGULAR_DEFLECTION_RAD = 0.16
TRACE_LINEAR_DEFLECTION_MM = 0.08
TRACE_ANGULAR_DEFLECTION_RAD = 0.10


def _cad_stage(
    stage: str,
    started_at: float,
    detail: str = "",
) -> float:
    elapsed = time.perf_counter() - started_at
    suffix = " | {}".format(detail) if detail else ""
    print(
        "[CAD] {:<24} {:>8.3f}s{}".format(stage, elapsed, suffix),
        flush=True,
    )
    return elapsed


def _cad_stage_start(stage: str, detail: str = "") -> None:
    suffix = " | {}".format(detail) if detail else ""
    print(
        "[CAD] {:<24} {:>8}{}".format(stage, "START", suffix),
        flush=True,
    )


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _ensure_cadquery_available() -> bool:
    global cq, _cadquery_checked
    if _cadquery_checked:
        return cq is not None
    _cadquery_checked = True
    try:
        import cadquery as cadquery_module

        cq = cadquery_module
    except Exception:  # pragma: no cover - optional dependency
        cq = None
    return cq is not None


def _ensure_ocp_available() -> bool:
    global BRep_Tool
    global BRepTools
    global BRepMesh_IncrementalMesh
    global IFSelect_RetDone
    global STEPControl_Reader
    global TopAbs_FACE
    global TopAbs_SOLID
    global TopExp_Explorer
    global TopLoc_Location
    global TopoDS
    global ocp_available

    if ocp_available is not None:
        return ocp_available
    try:
        from OCP.BRep import BRep_Tool as ocp_brep_tool
        from OCP.BRepTools import BRepTools as ocp_brep_tools
        from OCP.BRepMesh import BRepMesh_IncrementalMesh as ocp_mesh_builder
        from OCP.IFSelect import IFSelect_RetDone as ocp_read_done
        from OCP.STEPControl import STEPControl_Reader as ocp_step_reader
        from OCP.TopAbs import TopAbs_FACE as ocp_face_type, TopAbs_SOLID as ocp_solid_type
        from OCP.TopExp import TopExp_Explorer as ocp_explorer
        from OCP.TopLoc import TopLoc_Location as ocp_location
        from OCP.TopoDS import TopoDS as ocp_topods

        BRep_Tool = ocp_brep_tool
        BRepTools = ocp_brep_tools
        BRepMesh_IncrementalMesh = ocp_mesh_builder
        IFSelect_RetDone = ocp_read_done
        STEPControl_Reader = ocp_step_reader
        TopAbs_FACE = ocp_face_type
        TopAbs_SOLID = ocp_solid_type
        TopExp_Explorer = ocp_explorer
        TopLoc_Location = ocp_location
        TopoDS = ocp_topods
        ocp_available = True
    except Exception:  # pragma: no cover - optional dependency
        ocp_available = False
    return ocp_available


def _ensure_ocp_xcaf_available() -> bool:
    global STEPCAFControl_Reader, TDocStd_Document, XCAFApp_Application
    global XCAFDoc_DocumentTool, XCAFDoc_ColorType
    global TDF_Label, TDF_LabelSequence, TDataStd_Name
    global Quantity_Color, TCollection_ExtendedString
    global ocp_xcaf_available

    if ocp_xcaf_available is not None:
        return ocp_xcaf_available
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader as ocp_cafreader
        from OCP.TDocStd import TDocStd_Document as ocp_document
        from OCP.XCAFApp import XCAFApp_Application as ocp_xcafapp
        from OCP.XCAFDoc import (
            XCAFDoc_DocumentTool as ocp_doctool,
            XCAFDoc_ColorType as ocp_colortype,
        )
        from OCP.TDF import TDF_Label as ocp_label, TDF_LabelSequence as ocp_labelseq
        from OCP.TDataStd import TDataStd_Name as ocp_name_attr
        from OCP.Quantity import Quantity_Color as ocp_color
        from OCP.TCollection import TCollection_ExtendedString as ocp_ext_string

        STEPCAFControl_Reader = ocp_cafreader
        TDocStd_Document = ocp_document
        XCAFApp_Application = ocp_xcafapp
        XCAFDoc_DocumentTool = ocp_doctool
        XCAFDoc_ColorType = ocp_colortype
        TDF_Label = ocp_label
        TDF_LabelSequence = ocp_labelseq
        TDataStd_Name = ocp_name_attr
        Quantity_Color = ocp_color
        TCollection_ExtendedString = ocp_ext_string
        ocp_xcaf_available = True
    except Exception:  # pragma: no cover - optional dependency
        ocp_xcaf_available = False
    return ocp_xcaf_available


def _quantity_color_to_hex(color) -> Optional[str]:
    try:
        r = round(max(0.0, min(1.0, color.Red())) * 255)
        g = round(max(0.0, min(1.0, color.Green())) * 255)
        b = round(max(0.0, min(1.0, color.Blue())) * 255)
    except Exception:  # pragma: no cover - defensive
        return None
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


def _read_step_named_colored_solids(path: Path) -> Optional[List[Tuple[object, str, Optional[str]]]]:
    """Walks a STEP file's product structure (via XCAF) to recover each
    component's authored name and display color, e.g. the "Component Name"
    and body color set in NX before STEP export. Returns a flat list of
    (solid_shape, name, hex_color) tuples in the file's assembled (global)
    coordinate frame, or None if the file has no usable product structure
    (falls back to the plain geometry-only reader in that case)."""
    if not _ensure_ocp_xcaf_available():
        return None

    application = XCAFApp_Application.GetApplication_s()
    document = TDocStd_Document(TCollection_ExtendedString("XmlXCAF"))
    application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), document)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        return None
    if not reader.Transfer(document):
        return None

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())

    def get_name(label) -> Optional[str]:
        attr = TDataStd_Name()
        if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
            text = attr.Get().ToExtString()
            return text if text else None
        return None

    def get_color(item) -> Optional[str]:
        """Read XCAF colors from labels, shapes, or assembly instances.

        STEP exporters do not all attach presentation data at the same level:
        some use the product label, some the component instance, and others
        only the represented shape.  Query every XCAF overload defensively.
        """
        if item is None:
            return None
        color = Quantity_Color()
        for color_type in (
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
            XCAFDoc_ColorType.XCAFDoc_ColorGen,
            XCAFDoc_ColorType.XCAFDoc_ColorCurv,
        ):
            try:
                if color_tool.GetColor(item, color_type, color):
                    return _quantity_color_to_hex(color)
            except (AttributeError, TypeError):
                pass
            try:
                if color_tool.GetInstanceColor(item, color_type, color):
                    return _quantity_color_to_hex(color)
            except (AttributeError, TypeError):
                pass
        return None

    def preferred_name(*labels) -> Optional[str]:
        names = [get_name(label) for label in labels if label is not None]
        names = [name.strip() for name in names if name and name.strip()]
        if not names:
            return None
        # NAUO names are automatically generated assembly-occurrence IDs in
        # many STEP files, not the NX/SolidWorks component name users expect.
        for name in names:
            if not re.match(r"^(NAUO|NEXT_ASSEMBLY_USAGE_OCCURRENCE)[ _-]*\d*$", name, re.I):
                return name
        return names[0]

    label_color_cache: Dict[Tuple[int, ...], Optional[str]] = {}

    def label_key(label) -> Tuple[int, ...]:
        tags = []
        current = label
        while current is not None and not current.IsNull():
            tags.append(int(current.Tag()))
            if current.IsRoot():
                break
            current = current.Father()
        return tuple(reversed(tags))

    def get_label_or_subshape_color(label, shape) -> Optional[str]:
        cache_key = label_key(label)
        if cache_key in label_color_cache:
            return label_color_cache[cache_key]
        direct = get_color(label) or get_color(shape)
        if direct:
            label_color_cache[cache_key] = direct
            return direct
        # Some NX/AP214 exports attach presentation colors to XCAF subshape
        # labels only; querying TopoDS faces directly does not find them.
        subshape_colors: Dict[str, int] = {}
        subshape_labels = TDF_LabelSequence()
        try:
            shape_tool.GetSubShapes_s(label, subshape_labels)
        except Exception:
            subshape_labels = TDF_LabelSequence()
        for index in range(1, subshape_labels.Length() + 1):
            subshape_label = subshape_labels.Value(index)
            color = get_color(subshape_label)
            if not color:
                try:
                    color = get_color(shape_tool.GetShape_s(subshape_label))
                except Exception:
                    color = None
            if color:
                subshape_colors[color] = subshape_colors.get(color, 0) + 1
        resolved = (
            max(subshape_colors, key=subshape_colors.get)
            if subshape_colors
            else None
        )
        label_color_cache[cache_key] = resolved
        return resolved

    results: List[Tuple[object, str, Optional[str]]] = []
    part_counter = 0

    def walk(label, accumulated_location, inherited_name, inherited_color) -> None:
        nonlocal part_counter
        if shape_tool.IsAssembly_s(label):
            components = TDF_LabelSequence()
            shape_tool.GetComponents_s(label, components)
            for i in range(1, components.Length() + 1):
                component_label = components.Value(i)
                referred_label = TDF_Label()
                is_reference = shape_tool.GetReferredShape_s(component_label, referred_label)
                target_label = referred_label if is_reference else component_label
                component_shape = shape_tool.GetShape_s(component_label)
                if component_shape is None or component_shape.IsNull():
                    continue
                combined_location = accumulated_location.Multiplied(component_shape.Location())
                walk(
                    target_label,
                    combined_location,
                    preferred_name(component_label, target_label),
                    get_color(component_label)
                    or get_label_or_subshape_color(target_label, component_shape),
                )
            return

        prototype_shape = shape_tool.GetShape_s(label)
        if prototype_shape is None or prototype_shape.IsNull():
            return
        located_shape = prototype_shape.Moved(accumulated_location)

        part_counter += 1
        name = inherited_name or preferred_name(label) or "STEP Part {}".format(part_counter)
        color = (
            inherited_color
            or get_label_or_subshape_color(label, prototype_shape)
        )
        # Preserve one component per product/assembly occurrence. Exploding a
        # multi-body ITEM into separate solids loses its authored component
        # identity and can duplicate the same name in the UI. Shell/surface
        # STEP components are also valid even when they contain no solid.
        results.append((located_shape, name, color))

    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    identity_location = TopLoc_Location()
    for i in range(1, free_shapes.Length() + 1):
        walk(free_shapes.Value(i), identity_location, None, None)

    return results if results else None


def _subdivide_step_mesh(mesh: TriangleMesh) -> Tuple[TriangleMesh, float]:
    if _env_enabled(CAD_FAST_IMPORT_ENV):
        print(
            "[CAD] ROI mesh subdivision skipped | fast import diagnostic",
            flush=True,
        )
        return mesh, ROI_SUBDIVISION_SKIPPED_FAST
    if (
        len(mesh.faces) >= ROI_SUBDIVISION_AUTO_SKIP_RAW_FACES
        and not _env_enabled(CAD_FORCE_ROI_SUBDIVISION_ENV)
    ):
        print(
            "[CAD] ROI mesh subdivision skipped | native mesh already dense "
            "({} faces)".format(len(mesh.faces)),
            flush=True,
        )
        return mesh, ROI_SUBDIVISION_SKIPPED_DENSE_MESH
    target_area = choose_adaptive_subdivision_area_mm2(
        mesh,
        target_divisions_across_diagonal=ROI_SUBDIVISION_TARGET_DIVISIONS,
        min_target_edge_mm=ROI_SUBDIVISION_MIN_EDGE_MM,
        max_target_edge_mm=ROI_SUBDIVISION_MAX_EDGE_MM,
        max_output_faces=ROI_SUBDIVISION_MAX_FACES,
        max_depth=ROI_SUBDIVISION_MAX_DEPTH,
    )
    return (
        subdivide_flat_mesh(mesh, target_area, max_depth=ROI_SUBDIVISION_MAX_DEPTH),
        target_area,
    )


def _extract_brep_edge_segments(
    shape,
    component_index: int,
    deflection_mm: float = VIEWER_LINEAR_DEFLECTION_MM,
) -> List[Dict]:
    """Discretize true STEP B-rep topology edges for Viewer display.

    The render mesh is deliberately not consulted here: triangle boundaries
    on round faces are tessellation artifacts, not authored CAD edges.
    Periodic-surface seams and degenerated edges are omitted.
    """
    from OCP.BRep import BRep_Tool as ocp_brep_tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_EDGE as ocp_edge_type, TopAbs_FACE as ocp_face_type
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
    from OCP.TopoDS import TopoDS as ocp_topods

    edge_faces = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(
        shape,
        ocp_edge_type,
        ocp_face_type,
        edge_faces,
    )
    segments: List[Dict] = []
    for edge_index in range(1, edge_faces.Extent() + 1):
        edge = ocp_topods.Edge_s(edge_faces.FindKey(edge_index))
        if ocp_brep_tool.Degenerated_s(edge):
            continue
        ancestor_faces = edge_faces.FindFromIndex(edge_index)
        is_seam = any(
            ocp_brep_tool.IsClosed_s(edge, ocp_topods.Face_s(face))
            for face in ancestor_faces
        )
        if is_seam:
            continue

        try:
            curve = BRepAdaptor_Curve(edge)
            sampler = GCPnts_QuasiUniformDeflection()
            sampler.Initialize(curve, max(1e-4, float(deflection_mm)))
            if not sampler.IsDone() or sampler.NbPoints() < 2:
                continue
            points = [sampler.Value(i) for i in range(1, sampler.NbPoints() + 1)]
        except Exception:
            continue

        for start, end in zip(points, points[1:]):
            start_xyz = (start.X(), start.Y(), start.Z())
            end_xyz = (end.X(), end.Y(), end.Z())
            if sum((a - b) ** 2 for a, b in zip(start_xyz, end_xyz)) <= 1e-18:
                continue
            segments.append(
                {
                    "start": start_xyz,
                    "end": end_xyz,
                    "adjacent_face_indices": [],
                    "step_component_id": component_index,
                    "source": "step_brep_edge",
                }
            )
    return segments


def _step_import_note(
    engine: str,
    target_area: float,
    face_count: int,
    detail: str = "",
) -> str:
    detail_suffix = ", {}".format(detail) if detail else ""
    if target_area == ROI_SUBDIVISION_SKIPPED_DENSE_MESH:
        return (
            "STEP parsed with {} using its native dense tessellation "
            "({} faces{})."
        ).format(engine, face_count, detail_suffix)
    if target_area == ROI_SUBDIVISION_SKIPPED_FAST:
        return (
            "STEP parsed with {} without ROI subdivision "
            "(fast import diagnostic, {} faces{})."
        ).format(engine, face_count, detail_suffix)
    if target_area == ROI_SUBDIVISION_SKIPPED_DEFERRED:
        return (
            "STEP parsed with {} using a display tessellation; precision "
            "trace tessellation will be prepared on the first ray run "
            "({} viewer faces{})."
        ).format(engine, face_count, detail_suffix)
    return (
        "STEP parsed with {} and adaptively tessellated "
        "(target area {:.4g} mm^2, {} faces{})."
    ).format(engine, target_area, face_count, detail_suffix)


@dataclass
class ImportResult:
    mesh: TriangleMesh
    emitters: List[EmitterConfig]
    receiver_face_indices: List[int]
    synthetic: bool
    note: str
    feature_edge_segments: Optional[List[Dict]] = None
    timings_sec: Optional[Dict[str, float]] = None
    viewer_mesh: Optional[TriangleMesh] = None
    trace_mesh_loader: Optional[Callable[[], TriangleMesh]] = None


def import_geometry(
    file_path: Optional[str],
    *,
    defer_trace_mesh: bool = False,
) -> ImportResult:
    if not file_path:
        mesh, emitters, receiver = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=mesh,
            emitters=emitters,
            receiver_face_indices=receiver,
            synthetic=True,
            note="No input CAD file. Synthetic test geometry generated.",
        )
    path = Path(file_path)
    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    is_xt = lower_name.endswith(".x_t")
    if is_xt:
        raise ValueError(
            "Parasolid X_T import is not supported by the current CAD "
            "runtime. Export the model as STEP AP214 (recommended for "
            "component names/colors) or AP242 and import that file."
        )
    if suffix in {".stl", ".obj", ".step", ".stp"} or is_xt:
        try:
            if suffix == ".obj":
                return _import_obj(path)
            if suffix == ".stl":
                return _import_stl_ascii(path)
            if suffix in {".step", ".stp"}:
                return _import_step(path, defer_trace_mesh=defer_trace_mesh)
        except Exception as exc:
            mesh, emitters, receiver = generate_synthetic_leakage_scene()
            return ImportResult(
                mesh=mesh,
                emitters=emitters,
                receiver_face_indices=receiver,
                synthetic=True,
                note=f"Import failed: {exc}. Synthetic geometry used.",
            )
    mesh, emitters, receiver = generate_synthetic_leakage_scene()
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver,
        synthetic=True,
        note="Unsupported format in V1. Synthetic geometry used.",
    )


def _import_obj(path: Path) -> ImportResult:
    mesh = TriangleMesh()
    emitters = []
    receiver_faces: List[int] = []
    material_library = default_material_library()
    default_material = material_library["black_pc_resin"].material_id
    with path.open("r", encoding="utf-8") as file:
        for raw in file:
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("v "):
                parts = text.split()
                mesh.add_vertex((float(parts[1]), float(parts[2]), float(parts[3])))
            elif text.startswith("f "):
                parts = text.split()
                idx = [int(p.split("/")[0]) - 1 for p in parts[1:]]
                if len(idx) >= 3:
                    for j in range(1, len(idx) - 1):
                        face_id = mesh.add_face(
                            idx[0],
                            idx[j],
                            idx[j + 1],
                            default_material,
                            {},
                        )
                        if face_id % 7 == 0:
                            receiver_faces.append(face_id)
    if not mesh.faces:
        mesh, emitters, receiver_faces = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=mesh,
            emitters=emitters,
            receiver_face_indices=receiver_faces,
            synthetic=True,
            note="OBJ parsed but no triangles found; synthetic fallback.",
        )
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver_faces[: max(1, len(receiver_faces) // 10) ],
        synthetic=False,
        note="OBJ parsed. No explicit materials; fallback profile applied.",
    )


def _import_stl_ascii(path: Path) -> ImportResult:
    mesh = TriangleMesh()
    emitters = []
    receiver_faces: List[int] = []
    material_library = default_material_library()
    default_material = material_library["black_pc_resin"].material_id
    tri: List[Tuple[float, float, float]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as file:
        for raw in file:
            text = raw.strip().lower()
            if text.startswith("vertex"):
                parts = text.split()
                tri.append((float(parts[1]), float(parts[2]), float(parts[3])))
                if len(tri) == 3:
                    i0 = mesh.add_vertex(tri[0])
                    i1 = mesh.add_vertex(tri[1])
                    i2 = mesh.add_vertex(tri[2])
                    face_id = mesh.add_face(i0, i1, i2, default_material, {})
                    if face_id % 11 == 0:
                        receiver_faces.append(face_id)
                    tri = []
    if not mesh.faces:
        mesh, emitters, receiver_faces = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=mesh,
            emitters=emitters,
            receiver_face_indices=receiver_faces,
            synthetic=True,
            note="STL parsed but no triangles found; synthetic fallback.",
        )
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver_faces[: max(1, len(receiver_faces) // 5)],
        synthetic=False,
        note="STL ASCII parsed. Material mapping uses default profile.",
    )


def _import_step(
    path: Path,
    *,
    defer_trace_mesh: bool = False,
) -> ImportResult:
    total_started_at = time.perf_counter()
    print(
        "[CAD] STEP import start       | {} ({:.2f} MB)".format(
            path.name,
            path.stat().st_size / (1024.0 * 1024.0),
        ),
        flush=True,
    )
    runtime_started_at = time.perf_counter()
    _cad_stage_start("OCP runtime load")
    has_ocp = _ensure_ocp_available()
    runtime_sec = _cad_stage(
        "OCP runtime load",
        runtime_started_at,
        "available={}".format(has_ocp),
    )
    if has_ocp:
        try:
            result = _import_step_ocp(
                path,
                defer_trace_mesh=defer_trace_mesh,
            )
            timings = dict(result.timings_sec or {})
            timings["ocp_runtime_load"] = runtime_sec
            timings["step_import_total"] = time.perf_counter() - total_started_at
            result.timings_sec = timings
            _cad_stage(
                "STEP import complete",
                total_started_at,
                "{} faces".format(len(result.mesh.faces)),
            )
            return result
        except Exception as exc:
            _cad_stage(
                "OCP import failed",
                total_started_at,
                "{}: {}".format(type(exc).__name__, exc),
            )

    cadquery_started_at = time.perf_counter()
    has_cadquery = _ensure_cadquery_available()
    cadquery_runtime_sec = _cad_stage(
        "CadQuery runtime load",
        cadquery_started_at,
        "available={}".format(has_cadquery),
    )
    if not has_cadquery:
        mesh, emitters, receiver = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=mesh,
            emitters=emitters,
            receiver_face_indices=receiver,
            synthetic=True,
            note="CadQuery is not installed, so STEP import fell back to synthetic geometry.",
            timings_sec={
                "ocp_runtime_load": runtime_sec,
                "cadquery_runtime_load": cadquery_runtime_sec,
                "step_import_total": time.perf_counter() - total_started_at,
            },
        )

    mesh = TriangleMesh()
    emitters: List[EmitterConfig] = []
    material_library = default_material_library()
    default_material = material_library["black_pc_resin"].material_id

    cadquery_import_started_at = time.perf_counter()
    workplane = cq.importers.importStep(str(path))
    shape = workplane.val()
    # Keep the CadQuery fallback visually consistent with the primary OCP
    # path, including small fillets and compound R-surfaces.
    vertices, triangles = shape.tessellate(
        VIEWER_LINEAR_DEFLECTION_MM,
        VIEWER_ANGULAR_DEFLECTION_RAD,
    )
    cadquery_import_sec = _cad_stage(
        "CadQuery STEP+tessellate",
        cadquery_import_started_at,
        "{} raw triangles".format(len(triangles)),
    )

    vertex_index: List[int] = []
    for vertex in vertices:
        vertex_index.append(mesh.add_vertex(vertex.toTuple()))

    for tri in triangles:
        mesh.add_face(
            vertex_index[tri[0]],
            vertex_index[tri[1]],
            vertex_index[tri[2]],
            default_material,
            {"source": "step"},
        )

    if not mesh.faces:
        fallback_mesh, fallback_emitters, receiver_faces = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=fallback_mesh,
            emitters=fallback_emitters,
            receiver_face_indices=receiver_faces,
            synthetic=True,
            note="STEP parsed but tessellation produced no triangles; synthetic fallback used.",
        )

    feature_started_at = time.perf_counter()
    feature_edge_segments = build_feature_edge_segments(mesh)
    feature_sec = _cad_stage(
        "feature edges",
        feature_started_at,
        "{} segments".format(len(feature_edge_segments)),
    )
    subdivision_started_at = time.perf_counter()
    mesh, target_area = _subdivide_step_mesh(mesh)
    subdivision_sec = _cad_stage(
        "ROI mesh subdivision",
        subdivision_started_at,
        "{} faces".format(len(mesh.faces)),
    )
    receiver_faces = _guess_receiver_faces(mesh)
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver_faces,
        synthetic=False,
        note=_step_import_note(
            "CadQuery",
            target_area,
            len(mesh.faces),
        ),
        feature_edge_segments=feature_edge_segments,
        timings_sec={
            "ocp_runtime_load": runtime_sec,
            "cadquery_runtime_load": cadquery_runtime_sec,
            "cadquery_step_tessellate": cadquery_import_sec,
            "feature_edges": feature_sec,
            "roi_mesh_subdivision": subdivision_sec,
            "step_import_total": time.perf_counter() - total_started_at,
        },
    )


def _import_step_ocp(
    path: Path,
    *,
    defer_trace_mesh: bool = False,
) -> ImportResult:
    timings: Dict[str, float] = {}
    structure_started_at = time.perf_counter()
    named_colored_solids = None
    if _env_enabled(CAD_SKIP_PRODUCT_METADATA_ENV):
        print(
            "[CAD] OCP product structure skipped | metadata diagnostic",
            flush=True,
        )
    else:
        _cad_stage_start("OCP product structure")
        try:
            named_colored_solids = _read_step_named_colored_solids(path)
        except Exception:
            named_colored_solids = None
    timings["ocp_product_structure"] = _cad_stage(
        "OCP product structure",
        structure_started_at,
        "components={}".format(len(named_colored_solids or [])),
    )

    shape = None

    def component_leaf_shapes(component_shape) -> List[object]:
        """Return solids for fast meshing/extraction but preserve shells.

        The caller retains the product component ID, name and color while
        expensive OCP operations run on smaller leaf shapes.
        """
        solids: List[object] = []
        solid_explorer = TopExp_Explorer(component_shape, TopAbs_SOLID)
        while solid_explorer.More():
            solids.append(TopoDS.Solid_s(solid_explorer.Current()))
            solid_explorer.Next()
        return solids or [component_shape]
    if named_colored_solids:
        # Each solid was parsed independently via the XCAF document, so its
        # triangulation has to be computed on that same solid - meshing the
        # plain-reader shape below would not populate these faces at all.
        tessellation_started_at = time.perf_counter()
        _cad_stage_start(
            "OCP viewer tessellation",
            "components={}".format(len(named_colored_solids)),
        )
        for component_shape, _name, _color in named_colored_solids:
            for solid in component_leaf_shapes(component_shape):
                try:
                    BRepMesh_IncrementalMesh(
                        solid,
                        VIEWER_LINEAR_DEFLECTION_MM,
                        False,
                        VIEWER_ANGULAR_DEFLECTION_RAD,
                        True,
                    ).Perform()
                except Exception:
                    pass
        timings["ocp_viewer_tessellation"] = _cad_stage(
            "OCP viewer tessellation",
            tessellation_started_at,
        )
    else:
        read_started_at = time.perf_counter()
        _cad_stage_start("OCP STEP read")
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(path))
        timings["ocp_step_read"] = _cad_stage(
            "OCP STEP read",
            read_started_at,
        )
        if status != IFSelect_RetDone:
            raise RuntimeError("OCP STEP reader could not open file")
        transfer_started_at = time.perf_counter()
        _cad_stage_start("OCP transfer roots")
        reader.TransferRoots()
        shape = reader.OneShape()
        timings["ocp_transfer_roots"] = _cad_stage(
            "OCP transfer roots",
            transfer_started_at,
        )

        tessellation_started_at = time.perf_counter()
        _cad_stage_start("OCP viewer tessellation")
        mesh_builder = BRepMesh_IncrementalMesh(
            shape,
            VIEWER_LINEAR_DEFLECTION_MM,
            False,
            VIEWER_ANGULAR_DEFLECTION_RAD,
            True,
        )
        try:
            mesh_builder.Perform()
        except Exception:
            pass
        timings["ocp_viewer_tessellation"] = _cad_stage(
            "OCP viewer tessellation",
            tessellation_started_at,
        )

    emitters: List[EmitterConfig] = []
    material_library = default_material_library()
    default_material = material_library["black_pc_resin"].material_id
    def extract_current_triangulation() -> Tuple[TriangleMesh, List[int], int]:
        extracted_mesh = TriangleMesh()
        receiver_faces: List[int] = []
        global_vertex_map: Dict[Tuple[int, int, int], int] = {}
        face_counter = 0

        def add_deduped_vertex(x: float, y: float, z: float) -> int:
            key = (round(x * 1000000), round(y * 1000000), round(z * 1000000))
            existing = global_vertex_map.get(key)
            if existing is not None:
                return existing
            vertex_index = extracted_mesh.add_vertex((x, y, z))
            global_vertex_map[key] = vertex_index
            return vertex_index

        def import_face(
            face,
            component_index: int,
            component_name: str,
            component_color: Optional[str],
        ) -> None:
            nonlocal face_counter
            location = TopLoc_Location()
            triangulation = BRep_Tool.Triangulation_s(face, location)
            if (
                triangulation is not None
                and triangulation.NbNodes() > 0
                and triangulation.NbTriangles() > 0
            ):
                transform = location.Transformation()
                vertex_map = {}
                for node_index in range(1, triangulation.NbNodes() + 1):
                    point = triangulation.Node(node_index).Transformed(transform)
                    vertex_map[node_index] = add_deduped_vertex(
                        point.X(), point.Y(), point.Z()
                    )

                metadata = {
                    "source": "step_ocp",
                    "face_index": face_counter,
                    "step_component_id": component_index,
                    "step_component_name": component_name,
                }
                if component_color:
                    metadata["step_component_color"] = component_color

                for tri_index in range(1, triangulation.NbTriangles() + 1):
                    a, b, c = triangulation.Triangle(tri_index).Get()
                    face_id = extracted_mesh.add_face(
                        vertex_map[a],
                        vertex_map[b],
                        vertex_map[c],
                        default_material,
                        dict(metadata),
                    )
                    if face_id % 13 == 0:
                        receiver_faces.append(face_id)
            # This counter is the stable bridge between display and trace
            # tessellations: one ID per authored B-rep face, independent of
            # how many triangles that face produces at each resolution.
            face_counter += 1

        solid_counter = 0
        if named_colored_solids:
            for component_shape, component_name, component_color in named_colored_solids:
                solid_counter += 1
                for solid in component_leaf_shapes(component_shape):
                    face_explorer = TopExp_Explorer(solid, TopAbs_FACE)
                    while face_explorer.More():
                        import_face(
                            TopoDS.Face_s(face_explorer.Current()),
                            solid_counter - 1,
                            component_name,
                            component_color,
                        )
                        face_explorer.Next()
        else:
            solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
            while solid_explorer.More():
                solid_counter += 1
                solid = solid_explorer.Current()
                component_name = "STEP Solid {}".format(solid_counter)
                face_explorer = TopExp_Explorer(solid, TopAbs_FACE)
                while face_explorer.More():
                    import_face(
                        TopoDS.Face_s(face_explorer.Current()),
                        solid_counter - 1,
                        component_name,
                        None,
                    )
                    face_explorer.Next()
                solid_explorer.Next()

        if solid_counter == 0:
            explorer = TopExp_Explorer(shape, TopAbs_FACE)
            while explorer.More():
                import_face(TopoDS.Face_s(explorer.Current()), 0, "STEP Body", None)
                explorer.Next()
        return extracted_mesh, receiver_faces, solid_counter

    extraction_started_at = time.perf_counter()
    _cad_stage_start("viewer triangle extract")
    viewer_mesh, _viewer_receiver_faces, solid_counter = extract_current_triangulation()
    timings["viewer_triangle_extraction"] = _cad_stage(
        "viewer triangle extract",
        extraction_started_at,
        "{} faces".format(len(viewer_mesh.faces)),
    )

    if not viewer_mesh.faces:
        fallback_mesh, fallback_emitters, fallback_receivers = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=fallback_mesh,
            emitters=fallback_emitters,
            receiver_face_indices=fallback_receivers,
            synthetic=True,
            note="STEP parsed with OCP but tessellation produced no triangles; synthetic fallback used.",
        )

    trace_state: Dict[str, float] = {}

    def build_trace_mesh() -> TriangleMesh:
        # Remove only cached triangulations, never B-rep topology or XCAF
        # product metadata, then remesh the same shapes at trace accuracy.
        trace_tessellation_started_at = time.perf_counter()
        _cad_stage_start("OCP trace tessellation")
        trace_shapes = (
            [component_shape for component_shape, _name, _color in named_colored_solids]
            if named_colored_solids
            else [shape]
        )
        for trace_shape in trace_shapes:
            try:
                BRepTools.Clean_s(trace_shape)
                BRepMesh_IncrementalMesh(
                    trace_shape,
                    TRACE_LINEAR_DEFLECTION_MM,
                    False,
                    TRACE_ANGULAR_DEFLECTION_RAD,
                    True,
                ).Perform()
            except Exception:
                pass
        timings["ocp_trace_tessellation"] = _cad_stage(
            "OCP trace tessellation",
            trace_tessellation_started_at,
        )

        trace_extraction_started_at = time.perf_counter()
        _cad_stage_start("trace triangle extract")
        trace_mesh, _trace_receiver_faces, _trace_solid_count = (
            extract_current_triangulation()
        )
        timings["trace_triangle_extraction"] = _cad_stage(
            "trace triangle extract",
            trace_extraction_started_at,
            "{} faces".format(len(trace_mesh.faces)),
        )
        if not trace_mesh.faces:
            raise RuntimeError("Trace tessellation produced no triangles")

        subdivision_started_at = time.perf_counter()
        _cad_stage_start("ROI mesh subdivision")
        trace_mesh, target_area = _subdivide_step_mesh(trace_mesh)
        timings["roi_mesh_subdivision"] = _cad_stage(
            "ROI mesh subdivision",
            subdivision_started_at,
            "{} faces".format(len(trace_mesh.faces)),
        )
        trace_state["target_area"] = target_area
        return trace_mesh

    feature_started_at = time.perf_counter()
    _cad_stage_start("B-rep feature edges")
    feature_edge_segments: List[Dict] = []
    if named_colored_solids:
        for component_index, (component_shape, _name, _color) in enumerate(
            named_colored_solids
        ):
            for solid in component_leaf_shapes(component_shape):
                feature_edge_segments.extend(
                    _extract_brep_edge_segments(solid, component_index)
                )
    elif shape is not None:
        edge_solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        edge_component_index = 0
        while edge_solid_explorer.More():
            feature_edge_segments.extend(
                _extract_brep_edge_segments(
                    edge_solid_explorer.Current(),
                    edge_component_index,
                )
            )
            edge_component_index += 1
            edge_solid_explorer.Next()
        if edge_component_index == 0:
            feature_edge_segments = _extract_brep_edge_segments(shape, 0)

    edge_source = "STEP B-rep topology"
    if not feature_edge_segments:
        # Defensive fallback for malformed/non-B-rep STEP representations.
        feature_edge_segments = build_feature_edge_segments(viewer_mesh)
        edge_source = "mesh-angle fallback"
    timings["feature_edges"] = _cad_stage(
        "B-rep feature edges",
        feature_started_at,
        "{} segments | {}".format(len(feature_edge_segments), edge_source),
    )
    mesh = viewer_mesh if defer_trace_mesh else build_trace_mesh()
    target_area = (
        ROI_SUBDIVISION_SKIPPED_DEFERRED
        if defer_trace_mesh
        else trace_state.get("target_area", ROI_SUBDIVISION_SKIPPED_DENSE_MESH)
    )
    receiver_started_at = time.perf_counter()
    guessed_receivers = _guess_receiver_faces(mesh)
    timings["receiver_hint"] = _cad_stage(
        "receiver hint",
        receiver_started_at,
    )
    if guessed_receivers:
        receiver_faces = guessed_receivers
    naming_note = (
        "component names/colors read from STEP product structure"
        if named_colored_solids
        else "no STEP product structure found; generic solid names used"
    )
    naming_note = "{}, edges from {}".format(naming_note, edge_source)
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver_faces,
        synthetic=False,
        note=_step_import_note(
            "OCP",
            target_area,
            len(mesh.faces),
            naming_note,
        ),
        feature_edge_segments=feature_edge_segments,
        timings_sec=timings,
        viewer_mesh=viewer_mesh,
        trace_mesh_loader=build_trace_mesh if defer_trace_mesh else None,
    )


def _guess_receiver_faces(mesh: TriangleMesh) -> List[int]:
    if not mesh.faces:
        return []
    centroids = [mesh.centroid(idx) for idx in range(len(mesh.faces))]
    max_y = max(center[1] for center in centroids)
    min_y = min(center[1] for center in centroids)
    span_y = max(1e-6, max_y - min_y)
    threshold = max_y - span_y * 0.05
    candidates = [idx for idx, center in enumerate(centroids) if center[1] >= threshold]
    if not candidates:
        step = max(1, len(mesh.faces) // 32)
        return list(range(0, len(mesh.faces), step))[:64]
    if len(candidates) > 256:
        step = max(1, len(candidates) // 128)
        candidates = candidates[::step]
    return candidates
