from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np


_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _rotation_matrix_xyz(tilt: dict[str, Any]) -> np.ndarray:
    rx, ry, rz = (
        math.radians(float(tilt.get(axis, 0.0) or 0.0))
        for axis in ("x", "y", "z")
    )
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.asarray(((1, 0, 0), (0, cx, -sx), (0, sx, cx)))
    my = np.asarray(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
    mz = np.asarray(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)))
    # Three.js Euler's default XYZ order produces Rz * Ry * Rx.
    return mz @ my @ mx


def _component_transforms(
    mesh: dict[str, Any],
    rules: list[dict[str, Any]],
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    raw_vertices = mesh.get("vertices")
    raw_faces = mesh.get("faces")
    vertices = np.asarray(raw_vertices if raw_vertices is not None else [], dtype=np.float64)
    faces = np.asarray(raw_faces if raw_faces is not None else [], dtype=np.int64)
    raw_components = mesh.get("face_component_ids")
    component_values = raw_components if raw_components is not None else []
    components = np.asarray(
        [(-1 if value is None else int(value)) for value in component_values],
        dtype=np.int64,
    )
    result: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for rule in rules:
        if not rule.get("enabled", True) or rule.get("targetType", rule.get("target_type")) != "component":
            continue
        component_id = int(rule.get("componentId", rule.get("component_id", -1)))
        raw_pivot = rule.get("pivot")
        if isinstance(raw_pivot, dict):
            pivot = np.asarray(
                [raw_pivot.get(axis, 0.0) for axis in ("x", "y", "z")],
                dtype=np.float64,
            )
        else:
            component_faces = faces[components == component_id]
            if component_faces.size == 0:
                continue
            component_vertices = vertices[np.unique(component_faces.reshape(-1))]
            pivot = (
                component_vertices.min(axis=0) + component_vertices.max(axis=0)
            ) * 0.5
        move_value = rule.get("move") or {}
        move = np.asarray([move_value.get(axis, 0.0) for axis in ("x", "y", "z")], dtype=np.float64)
        result[component_id] = (
            _rotation_matrix_xyz(rule.get("tilt") or {}),
            pivot,
            move,
        )
    return result


def _transform_triangles(
    triangles: np.ndarray,
    component_ids: np.ndarray,
    transforms: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]],
) -> np.ndarray:
    if not transforms:
        return triangles
    transformed = triangles.copy()
    for component_id, (rotation, pivot, move) in transforms.items():
        mask = component_ids == component_id
        if not np.any(mask):
            continue
        transformed[mask] = (
            (transformed[mask] - pivot) @ rotation.T + pivot + move
        )
    return transformed


def _triangle_plane_segment(
    triangle: np.ndarray,
    axis_index: int,
    position: float,
    tolerance: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    distances = triangle[:, axis_index] - position
    if np.all(np.abs(distances) <= tolerance):
        return None
    points: list[np.ndarray] = []
    for start_index, end_index in ((0, 1), (1, 2), (2, 0)):
        start = triangle[start_index]
        end = triangle[end_index]
        start_distance = distances[start_index]
        end_distance = distances[end_index]
        if abs(start_distance) <= tolerance:
            points.append(start.copy())
        if start_distance * end_distance < -(tolerance * tolerance):
            ratio = start_distance / (start_distance - end_distance)
            points.append(start + (end - start) * ratio)
    unique: list[np.ndarray] = []
    for point in points:
        if not any(np.linalg.norm(point - previous) <= tolerance for previous in unique):
            unique.append(point)
    if len(unique) != 2 or np.linalg.norm(unique[0] - unique[1]) <= tolerance:
        return None
    return unique[0], unique[1]


def _point_key(point: np.ndarray, tolerance: float) -> tuple[int, int, int]:
    return tuple(int(round(float(value) / tolerance)) for value in point)  # type: ignore[return-value]


def _closed_loops(
    segments: list[tuple[np.ndarray, np.ndarray]],
    tolerance: float,
) -> tuple[list[list[list[float]]], int]:
    points: dict[tuple[int, int, int], np.ndarray] = {}
    edges: set[tuple[tuple[int, int, int], tuple[int, int, int]]] = set()
    adjacency: dict[tuple[int, int, int], set[tuple[int, int, int]]] = defaultdict(set)
    for start, end in segments:
        a, b = _point_key(start, tolerance), _point_key(end, tolerance)
        if a == b:
            continue
        edge = (a, b) if a < b else (b, a)
        if edge in edges:
            continue
        edges.add(edge)
        points.setdefault(a, start)
        points.setdefault(b, end)
        adjacency[a].add(b)
        adjacency[b].add(a)

    unused = set(edges)
    loops: list[list[list[float]]] = []
    open_chain_count = 0
    while unused:
        seed = next(iter(unused))
        start, current = seed
        path = [start, current]
        unused.remove(seed)
        previous = start
        closed = False
        while True:
            if current == start:
                closed = True
                break
            candidates = []
            for neighbor in adjacency[current]:
                edge = (current, neighbor) if current < neighbor else (neighbor, current)
                if edge in unused:
                    candidates.append((neighbor, edge))
            if not candidates:
                break
            # Closed manifold cuts have degree two. At a coincident/T-junction,
            # continuing without immediately reversing is the stable fallback.
            next_key, next_edge = next(
                ((key, edge) for key, edge in candidates if key != previous),
                candidates[0],
            )
            unused.remove(next_edge)
            previous, current = current, next_key
            path.append(current)
            if len(path) > len(edges) + 2:
                break
        if closed and len(path) >= 4:
            loops.append([points[key].astype(float).tolist() for key in path[:-1]])
        else:
            open_chain_count += 1
    return loops, open_chain_count


def build_section_cap_contours(
    mesh: dict[str, Any],
    *,
    axis: str,
    position: float,
    hidden_component_ids: list[int] | None = None,
    transform_rules: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    axis_key = axis.strip().lower()
    if axis_key not in _AXIS_INDEX:
        raise ValueError("Section axis must be x, y, or z")
    raw_vertices = mesh.get("vertices")
    raw_faces = mesh.get("faces")
    vertices = np.asarray(raw_vertices if raw_vertices is not None else [], dtype=np.float64)
    faces = np.asarray(raw_faces if raw_faces is not None else [], dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1:] != (3,):
        raise ValueError("Scene mesh vertices must be Nx3")
    if faces.ndim != 2 or faces.shape[1:] != (3,):
        raise ValueError("Scene mesh faces must be Nx3")
    raw_components = mesh.get("face_component_ids") or []
    if len(raw_components) != len(faces):
        raw_components = [-1] * len(faces)
    components = np.asarray(
        [(-1 if value is None else int(value)) for value in raw_components],
        dtype=np.int64,
    )
    if faces.size == 0:
        return {"axis": axis_key, "position": float(position), "contours": [], "open_chain_count": 0}

    scale = float(np.ptp(vertices, axis=0).max(initial=0.0))
    tolerance = max(scale * 1.0e-7, 1.0e-7)
    axis_index = _AXIS_INDEX[axis_key]
    hidden = set(int(value) for value in hidden_component_ids or [])
    transforms = _component_transforms(mesh, transform_rules or [])
    grouped: dict[int, list[tuple[np.ndarray, np.ndarray]]] = defaultdict(list)
    # A 5M-face CAD can otherwise create a >350 MB Nx3x3 temporary array.
    # Chunk the broad phase and materialize only triangles that can cross the
    # active plane. This keeps the UI section request independent of CAD size.
    chunk_size = 250_000
    for chunk_start in range(0, len(faces), chunk_size):
        chunk_end = min(chunk_start + chunk_size, len(faces))
        face_chunk = faces[chunk_start:chunk_end]
        component_chunk = components[chunk_start:chunk_end]
        transformed_chunk = None
        if transforms:
            transformed_chunk = _transform_triangles(
                vertices[face_chunk], component_chunk, transforms
            )
            axis_values = transformed_chunk[:, :, axis_index]
        else:
            axis_values = vertices[face_chunk, axis_index]
        mask = (
            (axis_values.min(axis=1) <= position + tolerance)
            & (axis_values.max(axis=1) >= position - tolerance)
        )
        if hidden:
            mask &= ~np.isin(component_chunk, list(hidden))
        if not np.any(mask):
            continue
        candidate_components = component_chunk[mask]
        candidate_triangles = (
            transformed_chunk[mask]
            if transformed_chunk is not None
            else vertices[face_chunk[mask]]
        )
        for triangle, component_id in zip(candidate_triangles, candidate_components):
            segment = _triangle_plane_segment(
                triangle, axis_index, position, tolerance
            )
            if segment is not None:
                grouped[int(component_id)].append(segment)

    contours: list[dict[str, Any]] = []
    open_chain_count = 0
    for component_id, segments in grouped.items():
        loops, component_open_count = _closed_loops(segments, tolerance)
        open_chain_count += component_open_count
        contours.extend(
            {"component_id": None if component_id < 0 else component_id, "points": loop}
            for loop in loops
        )
    return {
        "axis": axis_key,
        "position": float(position),
        "contours": contours,
        "open_chain_count": open_chain_count,
    }
