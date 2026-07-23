from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set

from .components import build_face_groups
from .geometry import TriangleMesh, build_feature_edge_segments
from .importers import import_geometry
from .types import ReceiverPatchConfig, ROIComponentClip, ROIPointSelection, ROIRegionResult, Vec3


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
    import_result = import_geometry(cad_path)
    mesh = import_result.mesh
    objects = build_face_groups(mesh, max_faces_per_object=None)
    face_to_component: Dict[int, int] = {}
    for item in objects:
        component_id = item["object_id"]
        item["component_id"] = component_id
        item["component_name"] = item["object_name"]
        for face_index in item["face_indices"]:
            face_to_component[face_index] = component_id

    face_ids = list(range(len(mesh.faces)))
    face_component_ids = [face_to_component.get(face_index) for face_index in face_ids]
    face_material_ids = [mesh.material_id(face_index) for face_index in face_ids]
    face_normals = [
        [round(value, 6) for value in mesh.normal(face_index)]
        for face_index in face_ids
    ]
    face_centroids = [
        [round(value, 6) for value in mesh.centroid(face_index)]
        for face_index in face_ids
    ]
    face_areas = [round(mesh.area(face_index), 6) for face_index in face_ids]
    step_component_to_component: Dict[int, int] = {}
    for face_index, component_id in face_to_component.items():
        step_component_id = mesh.metadata(face_index).get("step_component_id")
        if step_component_id is not None:
            step_component_to_component[int(step_component_id)] = component_id

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
                component_id = face_to_component.get(int(adjacent_faces[0]))
        feature_edge_segments.append(
            {
                "start": [round(float(value), 6) for value in segment["start"]],
                "end": [round(float(value), 6) for value in segment["end"]],
                "component_id": component_id,
            }
        )
    return {
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
            "vertices": [[round(v[0], 6), round(v[1], 6), round(v[2], 6)] for v in mesh.vertices],
            "faces": [[face.v0, face.v1, face.v2] for face in mesh.faces],
            "face_ids": face_ids,
            "face_component_ids": face_component_ids,
            "face_material_ids": face_material_ids,
            "face_normals": face_normals,
            "face_centroids": face_centroids,
            "face_areas_mm2": face_areas,
            "feature_edge_segments": feature_edge_segments,
        },
        "objects": objects,
        "components": objects,
        "metadata": {
            "face_count": len(mesh.faces),
            "vertex_count": len(mesh.vertices),
            "component_count": len(objects),
            "source_file": cad_path or "",
            "synthetic": import_result.synthetic,
            "import_note": import_result.note,
            "receiver_face_hint": import_result.receiver_face_indices[
                : min(30, len(import_result.receiver_face_indices))
            ],
        },
    }


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
