from __future__ import annotations

from array import array
from typing import Dict, List, Optional, Sequence, Set
import threading
import time

from .components import build_face_groups
from .geometry import TriangleMesh, build_feature_edge_segments
from .importers import import_geometry
from .types import ReceiverPatchConfig, ROIComponentClip, ROIPointSelection, ROIRegionResult, Vec3


class _TriangleFaceRows(Sequence):
    def __init__(self, faces) -> None:
        self._faces = faces

    def __len__(self) -> int:
        return len(self._faces)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        face = self._faces[index]
        return (face.v0, face.v1, face.v2)


class _MeshMaterialIds(Sequence):
    def __init__(self, mesh: TriangleMesh) -> None:
        self._mesh = mesh

    def __len__(self) -> int:
        return len(self._mesh.faces)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self._mesh.material_id(index)


class _FaceSourceIds(Sequence):
    def __init__(self, mesh: TriangleMesh) -> None:
        self._mesh = mesh

    def __len__(self) -> int:
        return len(self._mesh.faces)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return int(self._mesh.metadata(index).get("face_index", index))


def _scene_stage_start(stage: str) -> None:
    print(
        "[CAD] {:<24} {:>8}".format(stage, "START"),
        flush=True,
    )


def build_default_receivers(
    face_indices: List[int],
    name: str = "viewer_side",
) -> List[ReceiverPatchConfig]:
    return [ReceiverPatchConfig(receiver_id=name, face_indices=face_indices, weight=1.0)]


def resolve_receiver_faces(
    import_receiver_faces: List[int],
    roi_face_indices: Optional[List[int]],
) -> List[int]:
    if roi_face_indices:
        return roi_face_indices
    return import_receiver_faces


def build_scene_payload(cad_path: Optional[str]) -> Dict:
    total_started_at = time.perf_counter()
    import_started_at = time.perf_counter()
    _scene_stage_start("geometry import")
    # The interactive scene is returned first. STEP trace tessellation is
    # retained as a server-side lazy loader and runs only when tracing starts.
    import_result = import_geometry(cad_path, defer_trace_mesh=True)
    import_sec = time.perf_counter() - import_started_at
    print(
        "[CAD] {:<24} {:>8.3f}s | {} faces".format(
            "geometry import",
            import_sec,
            len(import_result.mesh.faces),
        ),
        flush=True,
    )
    mesh = import_result.viewer_mesh or import_result.mesh
    grouping_started_at = time.perf_counter()
    _scene_stage_start("component grouping")
    # Viewer/Binary V2 consumes float32 values. Building the same data as
    # millions of boxed Python floats costs far more memory and makes the
    # post-STEP payload stage noticeably slower on large assemblies.
    face_areas = array(
        "f",
        (mesh.area(face_index) for face_index in range(len(mesh.faces))),
    )
    objects = build_face_groups(
        mesh,
        max_faces_per_object=None,
        face_areas=face_areas,
    )
    grouping_sec = time.perf_counter() - grouping_started_at
    print(
        "[CAD] {:<24} {:>8.3f}s | {} components".format(
            "component grouping",
            grouping_sec,
            len(objects),
        ),
        flush=True,
    )
    arrays_started_at = time.perf_counter()
    _scene_stage_start("scene mesh arrays")
    face_component_ids: List[Optional[int]] = [None] * len(mesh.faces)
    for item in objects:
        component_id = item["object_id"]
        item["component_id"] = component_id
        item["component_name"] = item["object_name"]
        for face_index in item["face_indices"]:
            face_component_ids[face_index] = component_id

    face_material_ids = _MeshMaterialIds(mesh)
    # One CAD/B-rep face may be tessellated (and later ROI-subdivided) into
    # many triangles. Preserve its authored face identity for UI selection
    # counts and whole-surface picking.
    face_source_ids = _FaceSourceIds(mesh)
    step_component_to_component: Dict[int, int] = {}
    for face_index, component_id in enumerate(face_component_ids):
        if component_id is None:
            continue
        step_component_id = mesh.metadata(face_index).get("step_component_id")
        if step_component_id is not None:
            step_component_to_component[int(step_component_id)] = component_id

    # The browser receives only the lighter display tessellation. Ray tracing
    # keeps the precision tessellation server-side and links both levels by
    # the stable authored B-rep face ID stored in `face_source_ids`.
    def trace_mesh_payload(trace_mesh: TriangleMesh) -> Dict:
        trace_face_component_ids = []
        trace_face_material_ids = []
        trace_face_source_ids = []
        for face_index in range(len(trace_mesh.faces)):
            metadata = trace_mesh.metadata(face_index)
            step_component_id = metadata.get("step_component_id")
            trace_face_component_ids.append(
                step_component_to_component.get(int(step_component_id))
                if step_component_id is not None
                else None
            )
            trace_face_material_ids.append(trace_mesh.material_id(face_index))
            trace_face_source_ids.append(
                int(metadata.get("face_index", face_index))
            )
        return {
            "vertices": trace_mesh.vertices,
            "faces": _TriangleFaceRows(trace_mesh.faces),
            "face_component_ids": trace_face_component_ids,
            "face_material_ids": trace_face_material_ids,
            "face_source_ids": trace_face_source_ids,
        }

    deferred_trace_loader = import_result.trace_mesh_loader
    immediate_trace_mesh = (
        None if deferred_trace_loader is not None else import_result.mesh
    )
    viewer_first_face_by_source: Dict[int, int] = {}
    for viewer_face_index, source_id in enumerate(face_source_ids):
        viewer_first_face_by_source.setdefault(int(source_id), viewer_face_index)
    viewer_receiver_hint = []
    for imported_face_index in import_result.receiver_face_indices:
        if deferred_trace_loader is not None:
            viewer_face_index = imported_face_index
        elif immediate_trace_mesh is not None and (
            0 <= imported_face_index < len(immediate_trace_mesh.faces)
        ):
            source_id = int(
                immediate_trace_mesh.metadata(imported_face_index).get(
                    "face_index", imported_face_index
                )
            )
            viewer_face_index = viewer_first_face_by_source.get(source_id)
        else:
            viewer_face_index = None
        if viewer_face_index is not None and viewer_face_index not in viewer_receiver_hint:
            viewer_receiver_hint.append(int(viewer_face_index))
        if len(viewer_receiver_hint) >= 30:
            break
    arrays_sec = time.perf_counter() - arrays_started_at
    print(
        "[CAD] {:<24} {:>8.3f}s".format(
            "scene mesh arrays",
            arrays_sec,
        ),
        flush=True,
    )

    edges_started_at = time.perf_counter()
    _scene_stage_start("scene feature edges")
    source_feature_edges = import_result.feature_edge_segments
    if source_feature_edges is None:
        source_feature_edges = build_feature_edge_segments(mesh)
    feature_edge_segments = []
    for segment in source_feature_edges:
        step_component_id = segment.get("step_component_id")
        component_id = (
            step_component_to_component.get(int(step_component_id))
            if step_component_id is not None
            else None
        )
        if component_id is None:
            adjacent_faces = segment.get("adjacent_face_indices") or []
            if adjacent_faces:
                component_id = face_component_ids[int(adjacent_faces[0])]
        feature_edge_segments.append(
            {
                "start": [round(float(value), 6) for value in segment["start"]],
                "end": [round(float(value), 6) for value in segment["end"]],
                "component_id": component_id,
            }
        )
    edges_sec = time.perf_counter() - edges_started_at
    print(
        "[CAD] {:<24} {:>8.3f}s | {} segments".format(
            "scene feature edges",
            edges_sec,
            len(feature_edge_segments),
        ),
        flush=True,
    )
    timings = dict(import_result.timings_sec or {})
    timings.update(
        {
            "geometry_import": import_sec,
            "component_grouping": grouping_sec,
            "scene_mesh_arrays": arrays_sec,
            "scene_feature_edges": edges_sec,
            "scene_payload_total": time.perf_counter()
            - total_started_at,
        }
    )
    payload = {
        "schema_version": "mesh-scene.v1",
        "units": {
            "length": "mm",
        },
        "coordinate_system": {
            "handedness": "right",
            "axes": {
                "x": "model_x",
                "y": "model_y",
                "z": "model_z",
            },
        },
        "mesh": {
            "vertices": mesh.vertices,
            "faces": _TriangleFaceRows(mesh.faces),
            # Binary clients create this monotonic sequence without sending
            # millions of redundant integers. JSON fallback materializes it
            # only on explicit request.
            "face_ids": [],
            "face_component_ids": face_component_ids,
            "face_material_ids": face_material_ids,
            "face_source_ids": face_source_ids,
            # Derived lazily in the browser (or by the JSON fallback helper)
            # from vertices/faces. They are not transmitted in Binary V2.
            "face_normals": [],
            "face_centroids": [],
            "face_areas_mm2": face_areas,
            "feature_edge_segments": feature_edge_segments,
        },
        "objects": objects,
        "components": objects,
        "metadata": {
            "face_count": len(mesh.faces),
            "vertex_count": len(mesh.vertices),
            "trace_face_count": (
                len(immediate_trace_mesh.faces)
                if immediate_trace_mesh is not None
                else 0
            ),
            "trace_vertex_count": (
                len(immediate_trace_mesh.vertices)
                if immediate_trace_mesh is not None
                else 0
            ),
            "trace_mesh_deferred": deferred_trace_loader is not None,
            "dual_mesh": deferred_trace_loader is not None or mesh is not immediate_trace_mesh,
            "component_count": len(objects),
            "source_file": cad_path or "",
            "synthetic": import_result.synthetic,
            "import_note": import_result.note,
            "import_timings_sec": timings,
            "receiver_face_hint": viewer_receiver_hint,
        },
    }
    if deferred_trace_loader is not None:
        deferred_payload_lock = threading.Lock()
        deferred_payload_cache: Dict[str, Dict] = {}

        def load_trace_payload() -> Dict:
            with deferred_payload_lock:
                cached = deferred_payload_cache.get("mesh")
                if cached is None:
                    cached = trace_mesh_payload(deferred_trace_loader())
                    deferred_payload_cache["mesh"] = cached
                return cached

        payload["_trace_mesh_loader"] = load_trace_payload
    elif immediate_trace_mesh is not None:
        payload["_trace_mesh"] = trace_mesh_payload(immediate_trace_mesh)
    print(
        "[CAD] {:<24} {:>8.3f}s | payload ready".format(
            "scene payload total",
            timings["scene_payload_total"],
        ),
        flush=True,
    )
    return payload


def materialize_scene_derived_geometry(payload: Dict) -> Dict:
    """Populate JSON-only face sequences omitted from the Binary fast path."""
    mesh_value = payload.get("mesh")
    if not isinstance(mesh_value, dict):
        return payload
    vertices = mesh_value.get("vertices") or []
    faces = mesh_value.get("faces") or []
    face_count = len(faces)
    if (
        len(mesh_value.get("face_ids") or []) == face_count
        and len(mesh_value.get("face_normals") or []) == face_count
        and len(mesh_value.get("face_centroids") or []) == face_count
    ):
        return payload

    face_normals = []
    face_centroids = []
    for face in faces:
        first = vertices[int(face[0])]
        second = vertices[int(face[1])]
        third = vertices[int(face[2])]
        ab = (
            second[0] - first[0],
            second[1] - first[1],
            second[2] - first[2],
        )
        ac = (
            third[0] - first[0],
            third[1] - first[1],
            third[2] - first[2],
        )
        normal = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        length = sum(value * value for value in normal) ** 0.5
        face_normals.append(
            [value / length for value in normal]
            if length > 1e-12
            else [0.0, 0.0, 1.0]
        )
        face_centroids.append(
            [
                (first[axis] + second[axis] + third[axis]) / 3.0
                for axis in range(3)
            ]
        )

    response = dict(payload)
    response_mesh = dict(mesh_value)
    response_mesh["vertices"] = [list(vertex) for vertex in vertices]
    response_mesh["faces"] = [list(face) for face in faces]
    response_mesh["face_component_ids"] = list(
        mesh_value.get("face_component_ids") or []
    )
    response_mesh["face_material_ids"] = list(
        mesh_value.get("face_material_ids") or []
    )
    response_mesh["face_source_ids"] = list(
        mesh_value.get("face_source_ids") or []
    )
    response_mesh["face_areas_mm2"] = list(
        mesh_value.get("face_areas_mm2") or []
    )
    response_mesh["face_ids"] = list(range(face_count))
    response_mesh["face_normals"] = face_normals
    response_mesh["face_centroids"] = face_centroids
    response["mesh"] = response_mesh
    return response


# ---------------------------------------------------------------------------
# Native box-drag / point ROI selection (docs/roi-native-selection-plan.md)
#
# All three functions below are pure - no Three.js/viewer state, no NX/CAD
# kernel calls. They only ever see the already-tessellated `TriangleMesh`
# plus a `face_component_ids` list (same shape as build_scene_payload's
# "mesh.face_component_ids": index-aligned with mesh.faces, None where a
# face has no component). That keeps them unit-testable without a browser
# or a real CAD import.
# ---------------------------------------------------------------------------


def _point_in_box_xy(
    px: float, py: float, x_min: float, x_max: float, y_min: float, y_max: float
) -> bool:
    return x_min <= px <= x_max and y_min <= py <= y_max


def _tri_sign_2d(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    return (px - bx) * (ay - by) - (ax - bx) * (py - by)


def _point_in_triangle_2d(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
) -> bool:
    d1 = _tri_sign_2d(px, py, ax, ay, bx, by)
    d2 = _tri_sign_2d(px, py, bx, by, cx, cy)
    d3 = _tri_sign_2d(px, py, cx, cy, ax, ay)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def _segments_intersect_2d(
    ax: float, ay: float, bx: float, by: float, cx: float, cy: float, dx: float, dy: float
) -> bool:
    def ccw(px: float, py: float, qx: float, qy: float, rx: float, ry: float) -> bool:
        return (ry - py) * (qx - px) > (qy - py) * (rx - px)

    return ccw(ax, ay, cx, cy, dx, dy) != ccw(bx, by, cx, cy, dx, dy) and ccw(
        ax, ay, bx, by, cx, cy
    ) != ccw(ax, ay, bx, by, dx, dy)


def _triangle_intersects_box_xy(
    triangle_xy: Sequence[Vec3], x_min: float, x_max: float, y_min: float, y_max: float
) -> bool:
    for point in triangle_xy:
        if _point_in_box_xy(point[0], point[1], x_min, x_max, y_min, y_max):
            return True
    corners = [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]
    a, b, c = triangle_xy
    for corner in corners:
        if _point_in_triangle_2d(corner[0], corner[1], a[0], a[1], b[0], b[1], c[0], c[1]):
            return True
    box_edges = [(corners[0], corners[1]), (corners[1], corners[2]), (corners[2], corners[3]), (corners[3], corners[0])]
    tri_edges = [(a, b), (b, c), (c, a)]
    for edge_a, edge_b in tri_edges:
        for edge_c, edge_d in box_edges:
            if _segments_intersect_2d(
                edge_a[0], edge_a[1], edge_b[0], edge_b[1],
                edge_c[0], edge_c[1], edge_d[0], edge_d[1],
            ):
                return True
    return False


def resolve_faces_in_xy_box(
    mesh: TriangleMesh,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    face_component_ids: Sequence[Optional[int]],
    visible_component_ids: Optional[Set[int]] = None,
) -> List[int]:
    """Face indices whose XY projection actually intersects the given box.

    Z is never compared - the box acts as an infinite-depth prism along Z,
    by design (see plan doc). If `visible_component_ids` is given, faces
    belonging to a component NOT in that set are excluded before the box
    test even runs (a hidden component should never reappear in a box-drag
    result just because Z is unbounded).

    Faces are included whole-or-not-at-all (no sub-triangle clipping), but
    matched by real 2D triangle-vs-box intersection - not by the face's XY
    bounding box merely overlapping the box (over-includes: a large,
    screen-spanning flat part's bbox overlaps almost any drag box drawn
    anywhere, so it got swept into every unrelated ROI regardless of where
    the box was actually drawn) and not by the face's centroid falling
    inside the box either (over-corrects the other way - a small drag box
    over the edge/corner of a coarsely-tessellated, large triangle almost
    never contains that triangle's centroid, so dragging directly over a
    visible part often matched nothing at all). True intersection (a
    vertex in the box, or a box corner inside the triangle, or an edge
    crossing) gets both cases right.
    """
    included: List[int] = []
    for face_index in range(len(mesh.faces)):
        if visible_component_ids is not None:
            component_id = (
                face_component_ids[face_index] if face_index < len(face_component_ids) else None
            )
            if component_id not in visible_component_ids:
                continue

        v0, v1, v2 = mesh.face_vertices(face_index)
        if _triangle_intersects_box_xy((v0, v1, v2), x_min, x_max, y_min, y_max):
            included.append(face_index)

    return included


def group_faces_by_component(
    mesh: TriangleMesh,
    face_indices: Sequence[int],
    face_component_ids: Sequence[Optional[int]],
    component_names: Optional[Dict[int, str]] = None,
) -> List[ROIComponentClip]:
    """Groups an already-resolved face list by component, computing
    area/bbox from just those faces (the clipped sub-region), not the
    component's full extent."""
    groups: Dict[int, List[int]] = {}
    for face_index in face_indices:
        component_id = (
            face_component_ids[face_index] if face_index < len(face_component_ids) else None
        )
        key = component_id if component_id is not None else -1
        groups.setdefault(key, []).append(face_index)

    results: List[ROIComponentClip] = []
    for component_id in sorted(groups.keys()):
        faces = groups[component_id]
        area_mm2 = sum(mesh.area(face_index) for face_index in faces)

        xs: List[float] = []
        ys: List[float] = []
        zs: List[float] = []
        for face_index in faces:
            for vertex in mesh.face_vertices(face_index):
                xs.append(vertex[0])
                ys.append(vertex[1])
                zs.append(vertex[2])

        name = ""
        if component_names is not None and component_id in component_names:
            name = component_names[component_id]

        results.append(
            ROIComponentClip(
                component_id=component_id,
                component_name=name,
                face_indices=faces,
                area_mm2=area_mm2,
                bbox_min=(min(xs), min(ys), min(zs)),
                bbox_max=(max(xs), max(ys), max(zs)),
            )
        )

    return results


def resolve_faces_in_xy_box_grouped(
    mesh: TriangleMesh,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    face_component_ids: Sequence[Optional[int]],
    visible_component_ids: Optional[Set[int]] = None,
    scope_id: str = "",
    view: str = "front_xy",
    component_names: Optional[Dict[int, str]] = None,
) -> ROIRegionResult:
    """Convenience wrapper: resolve_faces_in_xy_box + group_faces_by_component
    in one call, packaged as the ROIRegionResult shape callers actually want."""
    face_indices = resolve_faces_in_xy_box(
        mesh, x_min, x_max, y_min, y_max, face_component_ids, visible_component_ids
    )
    components = group_faces_by_component(mesh, face_indices, face_component_ids, component_names)
    return ROIRegionResult(
        scope_id=scope_id,
        drag_rect_xy=(x_min, x_max, y_min, y_max),
        view=view,
        components=components,
    )


def resolve_nearest_face_to_point(
    mesh: TriangleMesh,
    coordinate: Vec3,
    face_component_ids: Optional[Sequence[Optional[int]]] = None,
    visible_component_ids: Optional[Set[int]] = None,
) -> Optional[int]:
    """Fallback ROI input path (see plan doc): nearest face (by centroid
    distance) to a directly-specified coordinate. Returns None only if
    there are no eligible faces at all (empty mesh, or every component
    hidden)."""
    best_index: Optional[int] = None
    best_distance_sq: Optional[float] = None

    for face_index in range(len(mesh.faces)):
        if visible_component_ids is not None and face_component_ids is not None:
            component_id = (
                face_component_ids[face_index] if face_index < len(face_component_ids) else None
            )
            if component_id not in visible_component_ids:
                continue

        cx, cy, cz = mesh.centroid(face_index)
        dx, dy, dz = cx - coordinate[0], cy - coordinate[1], cz - coordinate[2]
        distance_sq = dx * dx + dy * dy + dz * dz

        if best_distance_sq is None or distance_sq < best_distance_sq:
            best_distance_sq = distance_sq
            best_index = face_index

    return best_index


def build_point_selection(
    mesh: TriangleMesh,
    coordinate: Vec3,
    face_component_ids: Optional[Sequence[Optional[int]]] = None,
    visible_component_ids: Optional[Set[int]] = None,
    note: str = "",
) -> ROIPointSelection:
    face_index = resolve_nearest_face_to_point(
        mesh, coordinate, face_component_ids, visible_component_ids
    )
    component_id: Optional[int] = None
    if face_index is not None and face_component_ids is not None and face_index < len(face_component_ids):
        component_id = face_component_ids[face_index]

    return ROIPointSelection(
        coordinate=coordinate,
        face_index=face_index,
        component_id=component_id,
        note=note,
    )
