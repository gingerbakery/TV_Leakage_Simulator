from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List

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
BRepMesh_IncrementalMesh = None
IFSelect_RetDone = None
STEPControl_Reader = None
TopAbs_FACE = None
TopAbs_SOLID = None
TopExp_Explorer = None
TopLoc_Location = None
TopoDS = None
ocp_available = None

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
        from OCP.BRepMesh import BRepMesh_IncrementalMesh as ocp_mesh_builder
        from OCP.IFSelect import IFSelect_RetDone as ocp_read_done
        from OCP.STEPControl import STEPControl_Reader as ocp_step_reader
        from OCP.TopAbs import TopAbs_FACE as ocp_face_type, TopAbs_SOLID as ocp_solid_type
        from OCP.TopExp import TopExp_Explorer as ocp_explorer
        from OCP.TopLoc import TopLoc_Location as ocp_location
        from OCP.TopoDS import TopoDS as ocp_topods

        BRep_Tool = ocp_brep_tool
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


def _subdivide_step_mesh(mesh: TriangleMesh) -> Tuple[TriangleMesh, float]:
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


@dataclass
class ImportResult:
    mesh: TriangleMesh
    emitters: List[EmitterConfig]
    receiver_face_indices: List[int]
    synthetic: bool
    note: str
    feature_edge_segments: Optional[List[Dict]] = None


def import_geometry(file_path: Optional[str]) -> ImportResult:
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
    if suffix in {".stl", ".obj", ".step", ".stp"} or is_xt:
        try:
            if suffix == ".obj":
                return _import_obj(path)
            if suffix == ".stl":
                return _import_stl_ascii(path)
            if suffix in {".step", ".stp"}:
                return _import_step(path)
            if is_xt:
                mesh, emitters, receiver = generate_synthetic_leakage_scene()
                return ImportResult(
                    mesh=mesh,
                    emitters=emitters,
                    receiver_face_indices=receiver,
                    synthetic=True,
                    note=(
                        "X_T importer is not implemented yet in V1."
                        " Synthetic geometry generated for immediate execution."
                    ),
                )
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


def _import_step(path: Path) -> ImportResult:
    if _ensure_ocp_available():
        try:
            return _import_step_ocp(path)
        except Exception:
            pass
    if not _ensure_cadquery_available():
        mesh, emitters, receiver = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=mesh,
            emitters=emitters,
            receiver_face_indices=receiver,
            synthetic=True,
            note="CadQuery is not installed, so STEP import fell back to synthetic geometry.",
        )

    mesh = TriangleMesh()
    emitters: List[EmitterConfig] = []
    material_library = default_material_library()
    default_material = material_library["black_pc_resin"].material_id

    workplane = cq.importers.importStep(str(path))
    shape = workplane.val()
    vertices, triangles = shape.tessellate(0.5, 0.5)

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

    feature_edge_segments = build_feature_edge_segments(mesh)
    mesh, target_area = _subdivide_step_mesh(mesh)
    receiver_faces = _guess_receiver_faces(mesh)
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver_faces,
        synthetic=False,
        note=(
            "STEP parsed with CadQuery and adaptively tessellated "
            f"(target area {target_area:.4g} mm^2, {len(mesh.faces)} faces)."
        ),
        feature_edge_segments=feature_edge_segments,
    )


def _import_step_ocp(path: Path) -> ImportResult:
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError("OCP STEP reader could not open file")
    reader.TransferRoots()
    shape = reader.OneShape()

    mesh_builder = BRepMesh_IncrementalMesh(shape, 0.5, False, 0.5, True)
    try:
        mesh_builder.Perform()
    except Exception:
        pass

    mesh = TriangleMesh()
    emitters: List[EmitterConfig] = []
    receiver_faces: List[int] = []
    material_library = default_material_library()
    default_material = material_library["black_pc_resin"].material_id
    global_vertex_map: Dict[Tuple[int, int, int], int] = {}

    def add_deduped_vertex(x: float, y: float, z: float) -> int:
        key = (round(x * 1000000), round(y * 1000000), round(z * 1000000))
        existing = global_vertex_map.get(key)
        if existing is not None:
            return existing
        vertex_index = mesh.add_vertex((x, y, z))
        global_vertex_map[key] = vertex_index
        return vertex_index

    face_counter = 0

    def import_face(face, component_index: int, component_name: str) -> None:
        nonlocal face_counter
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None and triangulation.NbNodes() > 0 and triangulation.NbTriangles() > 0:
            transform = location.Transformation()
            vertex_map = {}
            for node_index in range(1, triangulation.NbNodes() + 1):
                point = triangulation.Node(node_index).Transformed(transform)
                vertex_map[node_index] = add_deduped_vertex(point.X(), point.Y(), point.Z())

            for tri_index in range(1, triangulation.NbTriangles() + 1):
                a, b, c = triangulation.Triangle(tri_index).Get()
                face_id = mesh.add_face(
                    vertex_map[a],
                    vertex_map[b],
                    vertex_map[c],
                    default_material,
                    {
                        "source": "step_ocp",
                        "face_index": face_counter,
                        "step_component_id": component_index,
                        "step_component_name": component_name,
                    },
                )
                if face_id % 13 == 0:
                    receiver_faces.append(face_id)
        face_counter += 1

    solid_explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solid_counter = 0
    while solid_explorer.More():
        solid_counter += 1
        solid = solid_explorer.Current()
        component_name = "STEP Solid {}".format(solid_counter)
        face_explorer = TopExp_Explorer(solid, TopAbs_FACE)
        while face_explorer.More():
            face = TopoDS.Face_s(face_explorer.Current())
            import_face(face, solid_counter - 1, component_name)
            face_explorer.Next()
        solid_explorer.Next()

    if solid_counter == 0:
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face = TopoDS.Face_s(explorer.Current())
            import_face(face, 0, "STEP Body")
            explorer.Next()

    if not mesh.faces:
        fallback_mesh, fallback_emitters, fallback_receivers = generate_synthetic_leakage_scene()
        return ImportResult(
            mesh=fallback_mesh,
            emitters=fallback_emitters,
            receiver_face_indices=fallback_receivers,
            synthetic=True,
            note="STEP parsed with OCP but tessellation produced no triangles; synthetic fallback used.",
        )

    feature_edge_segments = build_feature_edge_segments(mesh)
    mesh, target_area = _subdivide_step_mesh(mesh)

    guessed_receivers = _guess_receiver_faces(mesh)
    if guessed_receivers:
        receiver_faces = guessed_receivers
    return ImportResult(
        mesh=mesh,
        emitters=emitters,
        receiver_face_indices=receiver_faces,
        synthetic=False,
        note=(
            "STEP parsed with OCP and adaptively tessellated "
            f"(target area {target_area:.4g} mm^2, {len(mesh.faces)} faces)."
        ),
        feature_edge_segments=feature_edge_segments,
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
