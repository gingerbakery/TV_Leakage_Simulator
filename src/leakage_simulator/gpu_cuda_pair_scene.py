from __future__ import annotations

from dataclasses import dataclass
import os
import time

import numpy as np

from .gpu_cuda_intersection import GpuCudaScene
from .gpu_cuda_pair_sah import SahSplitter

PAIR_BVH_MAGIC = -729742139826170815
PAIR_BVH_CONTRACT = 'strict_float64_original_order_pair_bvh_v1'
AUTO_MIN_FACES = 4096


@dataclass(frozen=True, slots=True)
class OriginalBvhTopology:
    face_leaf: np.ndarray
    face_order: np.ndarray
    node_path: np.ndarray
    node_code: np.ndarray


def build_original_topology(scene):
    face_count = len(scene.triangle_v0)
    node_count = len(scene.node_count)
    face_leaf = np.full(face_count, -1, dtype=np.int32)
    face_order = np.full(face_count, -1, dtype=np.int32)
    codes = np.zeros(node_count, dtype=np.uint64)
    paths = [None] * node_count
    if any(len(getattr(scene, name)) != node_count for name in
        ('node_bounds_min', 'node_bounds_max', 'node_left', 'node_right', 'node_start')):
        raise ValueError('Original BVH table sizes do not agree')
    if not np.all(np.isfinite(scene.node_bounds_min)) or not np.all(np.isfinite(scene.node_bounds_max)):
        raise ValueError('Original BVH bounds must be finite')
    if np.any(scene.node_bounds_min > scene.node_bounds_max):
        raise ValueError('Original BVH bounds are inverted')

    def visit(node, path, code):
        if node < 0 or node >= node_count or paths[node] is not None:
            raise ValueError('Original BVH must be an acyclic tree')
        if len(path) >= 63:
            raise ValueError('Original BVH exceeds the topology code depth')
        path = (*path, node)
        paths[node] = path
        codes[node] = code
        count = int(scene.node_count[node])
        if count > 0:
            start = int(scene.node_start[node])
            if start < 0 or start + count > len(scene.ordered_faces):
                raise ValueError('Original BVH leaf range is invalid')
            for position in range(start, start + count):
                face = int(scene.ordered_faces[position])
                if face < 0 or face >= face_count or face_leaf[face] != -1:
                    raise ValueError('Original BVH face ownership must be unique')
                face_leaf[face] = node
                face_order[face] = position
            return
        if count < 0:
            raise ValueError('Original BVH leaf count is negative')
        for side, child in enumerate((int(scene.node_left[node]), int(scene.node_right[node]))):
            if child < 0 or child >= node_count:
                raise ValueError('Original BVH child is invalid')
            if not np.all(scene.node_bounds_min[node] <= scene.node_bounds_min[child]) or not np.all(
                scene.node_bounds_max[node] >= scene.node_bounds_max[child]
            ):
                raise ValueError('Original BVH child bounds are not nested')
            child_code = code | (side << (64 - len(path)))
            visit(child, path, child_code)

    if node_count:
        visit(0, (), 0)
    if any(path is None for path in paths):
        raise ValueError('Original BVH contains unreachable nodes')
    width = max((len(path) for path in paths), default=1)
    node_path = np.full((node_count, width), -1, dtype=np.int32)
    for node, path in enumerate(paths):
        node_path[node, :len(path)] = path
    for values in (face_leaf, face_order, node_path, codes):
        values.setflags(write=False)
    return OriginalBvhTopology(face_leaf, face_order, node_path, codes)


def has_pair_scene(scene):
    faces = scene.ordered_faces
    if faces.ndim != 1 or faces.dtype != np.int64 or len(faces) < 3 or faces[-1] != PAIR_BVH_MAGIC:
        return False
    if len(scene.node_count) < 2 or scene.node_count[0] != 0:
        return False
    original_nodes = int(scene.node_start[0])
    metadata_start = int(faces[-3])
    path_width = int(faces[-2])
    face_count = len(scene.triangle_v0)
    if not 0 < original_nodes < len(scene.node_count) or not 1 <= path_width <= 63:
        return False
    if metadata_start <= 0 or metadata_start % 2 or metadata_start > 2 * face_count:
        return False
    if len(scene.node_count) != original_nodes + metadata_start - 1:
        return False
    expected = metadata_start + 2 * face_count + original_nodes * (1 + path_width) + 3
    return expected == len(faces)


def pair_mode():
    mode = os.environ.get('LEAKAGE_GPU_PAIR_BVH', 'auto')
    if mode not in ('auto', 'off', 'force'):
        raise ValueError('GPU pair BVH mode must be auto, off or force')
    return mode


def prepare_pair_scene(mesh):
    mode = pair_mode()
    with mesh._gpu_cuda_scene_lock:
        original = mesh.prepare_gpu_cuda_scene()
        if mode == 'off' or len(original.node_count) < 2 or (
            mode == 'auto' and len(original.ordered_faces) < AUTO_MIN_FACES
        ):
            return original, 0.0, False
        cached = getattr(mesh, '_gpu_pair_scene_cache', None)
        if cached is not None and cached[0] is original:
            return cached[1], 0.0, True
        started = time.perf_counter()
        prepared = _build_pair_scene(mesh, original)
        mesh._gpu_pair_scene_cache = (original, prepared)
        return prepared, time.perf_counter() - started, False


def _build_pair_scene(mesh, original, *, splitter_factory=SahSplitter):
    coordinates = np.asarray(mesh.vertices, dtype=np.float64)
    if np.any(~np.isfinite(coordinates)) or np.max(np.abs(coordinates), initial=0.0) > 1e6:
        return original
    first_norms = np.linalg.norm(original.triangle_edge1, axis=1)
    second_norms = np.linalg.norm(original.triangle_edge2, axis=1)
    cross_norms = np.linalg.norm(np.cross(original.triangle_edge1, original.triangle_edge2), axis=1)
    product = first_norms * second_norms
    if np.any((product > 1e-8) & (cross_norms < product * 1e-8)):
        return original
    started = time.perf_counter()
    topology = build_original_topology(original)
    triangles = mesh._prepared_triangles
    splitter = splitter_factory(triangles) if splitter_factory is not None else None
    nodes = []
    ordered = []
    branch_levels = []
    maximum_depth = 0

    def build(indices, depth):
        nonlocal maximum_depth
        maximum_depth = max(maximum_depth, depth)
        address = len(nodes)
        if len(indices) == 1:
            triangle = triangles[indices[0]]
            nodes.append([triangle.bounds_min, triangle.bounds_max, -1, -1, len(ordered), 1])
            ordered.extend(indices)
            return address
        nodes.append([(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), -1, -1, 0, 0])
        while len(branch_levels) <= depth:
            branch_levels.append([])
        branch_levels[depth].append(address)
        if splitter is None:
            extents = [max(triangles[index].centroid[axis] for index in indices)
                - min(triangles[index].centroid[axis] for index in indices) for axis in range(3)]
            axis = max(range(3), key=lambda value: extents[value])
            sequence = sorted(indices, key=lambda index: triangles[index].centroid[axis])
            middle = len(sequence) // 2
            left_indices, right_indices = sequence[:middle], sequence[middle:]
        else:
            left_indices, right_indices = splitter(indices, depth)
        left = build(left_indices, depth + 1)
        right = build(right_indices, depth + 1)
        nodes[address][2] = left
        nodes[address][3] = right
        return address

    build(original.ordered_faces.tolist(), 1)
    root = len(original.node_count)
    face_offset = len(original.ordered_faces)
    layout = (('node_bounds_min', 0, np.float64), ('node_bounds_max', 1, np.float64),
        ('node_left', 2, np.int64), ('node_right', 3, np.int64),
        ('node_start', 4, np.int64), ('node_count', 5, np.int64))
    auxiliary = {name: np.asarray([node[position] for node in nodes], dtype=dtype)
        for name, position, dtype in layout}
    fast_lower, fast_upper = auxiliary['node_bounds_min'], auxiliary['node_bounds_max']
    for level in reversed(branch_levels):
        if not level:
            continue
        addresses = np.asarray(level, dtype=np.int64)
        left, right = auxiliary['node_left'][addresses], auxiliary['node_right'][addresses]
        fast_lower[addresses] = np.minimum(fast_lower[left], fast_lower[right])
        fast_upper[addresses] = np.maximum(fast_upper[left], fast_upper[right])
    padding = 1e-7 * (np.abs(fast_lower) + np.abs(fast_upper) + 1.0)
    arrays = {}
    for name, values in auxiliary.items():
        if name == 'node_bounds_min':
            values -= padding
        elif name == 'node_bounds_max':
            values += padding
        elif name in ('node_left', 'node_right'):
            values[values >= 0] += root
        elif name == 'node_start':
            values += face_offset
        combined = np.concatenate((getattr(original, name), values), axis=0)
        if name == 'node_start':
            combined[0] = root
        combined.setflags(write=False)
        arrays[name] = combined
    faces = np.concatenate((original.ordered_faces, np.asarray(ordered, dtype=np.int64),
        topology.face_leaf.astype(np.int64), topology.face_order.astype(np.int64),
        topology.node_code.view(np.int64), topology.node_path.astype(np.int64).reshape(-1),
        np.asarray([face_offset + len(ordered), topology.node_path.shape[1], PAIR_BVH_MAGIC], dtype=np.int64)))
    faces.setflags(write=False)
    result = GpuCudaScene(triangle_v0=original.triangle_v0, triangle_edge1=original.triangle_edge1,
        triangle_edge2=original.triangle_edge2, traceable_face_mask=original.traceable_face_mask,
        ordered_faces=faces, stack_width=max(original.stack_width, maximum_depth + 2),
        build_sec=original.build_sec + time.perf_counter() - started, **arrays)
    if not has_pair_scene(result):
        raise ValueError('Prepared GPU pair BVH has an invalid metadata layout')
    return result
