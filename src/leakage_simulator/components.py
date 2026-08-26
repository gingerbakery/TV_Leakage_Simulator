from __future__ import annotations

from collections import defaultdict, deque
from math import inf
from typing import Dict, List, Optional, Sequence, Set, Tuple


def build_face_groups(
    mesh,
    max_faces_per_object: Optional[int] = None,
    face_areas: Optional[Sequence[float]] = None,
) -> List[Dict]:
    face_count = len(mesh.faces)
    if face_count == 0:
        return []

    step_groups: Dict[int, List[int]] = defaultdict(list)
    step_names: Dict[int, str] = {}
    step_colors: Dict[int, Optional[str]] = {}
    for face_index in range(face_count):
        metadata = mesh.metadata(face_index)
        component_id = metadata.get("step_component_id")
        if component_id is None:
            continue
        component_id = int(component_id)
        step_groups[component_id].append(face_index)
        step_names[component_id] = metadata.get("step_component_name") or "STEP Solid {}".format(component_id + 1)
        if step_colors.get(component_id) is None:
            step_colors[component_id] = metadata.get("step_component_color")

    if step_groups:
        return _build_group_items(
            mesh,
            [step_groups[key] for key in sorted(step_groups.keys())],
            [step_names[key] for key in sorted(step_groups.keys())],
            max_faces_per_object,
            [step_colors[key] for key in sorted(step_groups.keys())],
            face_areas,
        )

    adjacency: List[Set[int]] = [set() for _ in range(face_count)]
    edge_map: Dict[Tuple[int, int], List[int]] = defaultdict(list)

    for idx, face in enumerate(mesh.faces):
        tri = (face.v0, face.v1, face.v2)
        edges = ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0]))
        for a, b in edges:
            key = (a, b) if a < b else (b, a)
            edge_map[key].append(idx)

    for face_indices in edge_map.values():
        for i in range(len(face_indices)):
            a = face_indices[i]
            for j in range(i + 1, len(face_indices)):
                b = face_indices[j]
                adjacency[a].add(b)
                adjacency[b].add(a)

    visited = [False] * face_count
    components: List[Dict] = []
    for start in range(face_count):
        if visited[start]:
            continue
        queue = deque([start])
        visited[start] = True
        face_indices: List[int] = []
        while queue:
            i = queue.popleft()
            face_indices.append(i)
            for nxt in adjacency[i]:
                if not visited[nxt]:
                    visited[nxt] = True
                    queue.append(nxt)

        components.append(face_indices)

    return _build_group_items(
        mesh,
        components,
        None,
        max_faces_per_object,
        face_areas=face_areas,
    )


def _build_group_items(
    mesh,
    face_groups: List[List[int]],
    names: Optional[List[str]],
    max_faces_per_object: Optional[int],
    colors: Optional[List[Optional[str]]] = None,
    face_areas: Optional[Sequence[float]] = None,
) -> List[Dict]:
    components: List[Dict] = []
    for group_index, face_indices in enumerate(face_groups):
        face_indices.sort()
        min_x = inf
        min_y = inf
        min_z = inf
        max_x = -inf
        max_y = -inf
        max_z = -inf
        area = 0.0
        for fidx in face_indices:
            a, b, c = mesh.face_vertices(fidx)
            area += (
                face_areas[fidx]
                if face_areas is not None
                else mesh.area(fidx)
            )
            for vx, vy, vz in (a, b, c):
                min_x = min(min_x, vx)
                max_x = max(max_x, vx)
                min_y = min(min_y, vy)
                max_y = max(max_y, vy)
                min_z = min(min_z, vz)
                max_z = max(max_z, vz)

        if max_faces_per_object is None:
            export_faces = face_indices
            is_truncated = False
        else:
            export_faces = face_indices[: max_faces_per_object + 1]
            is_truncated = len(face_indices) > max_faces_per_object

        components.append(
            {
                "object_name": names[group_index] if names else "Part {}".format(group_index + 1),
                "object_id": len(components),
                "face_indices": export_faces,
                "face_count": len(face_indices),
                "area_mm2": round(area, 3),
                "bbox_min": [round(min_x, 3), round(min_y, 3), round(min_z, 3)],
                "bbox_max": [round(max_x, 3), round(max_y, 3), round(max_z, 3)],
                "is_truncated": is_truncated,
                "color": colors[group_index] if colors else None,
            }
        )

    components.sort(key=lambda item: item["area_mm2"], reverse=True)
    for idx, item in enumerate(components):
        item["object_id"] = idx
    return components
