from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
import threading
import time
from typing import Any, Callable, Optional

import numpy as np
from numpy.typing import NDArray


FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]


class NativeCpuUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.phase = "probe"


class NativeCpuProviderError(RuntimeError):
    def __init__(self, phase: str, reason_code: str) -> None:
        super().__init__(reason_code)
        self.phase = phase
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class NativeCpuCapability:
    available: bool
    reason_code: Optional[str]
    numba_version: Optional[str]


@dataclass(frozen=True, slots=True)
class NativeCpuScene:
    triangle_v0: FloatArray
    triangle_edge1: FloatArray
    triangle_edge2: FloatArray
    node_bounds_min: FloatArray
    node_bounds_max: FloatArray
    node_left: IntArray
    node_right: IntArray
    node_start: IntArray
    node_count: IntArray
    ordered_faces: IntArray
    traceable_face_mask: BoolArray
    build_sec: float


@dataclass(frozen=True, slots=True)
class NativeCpuExecution:
    distances: FloatArray
    face_indices: IntArray
    scene_build_sec: float
    jit_compile_sec: float
    execute_sec: float
    numba_version: str


@dataclass(frozen=True, slots=True)
class NativeCpuScalarExecution:
    distance: float
    face_index: int
    scene_build_sec: float
    jit_compile_sec: float
    execute_sec: float
    numba_version: str


_STATE_LOCK = threading.RLock()
_CAPABILITY: Optional[NativeCpuCapability] = None
_KERNEL: Optional[Callable[..., None]] = None
_KERNEL_COMPILED = False
_THREAD_BUFFERS = threading.local()


def probe_native_cpu() -> NativeCpuCapability:
    """Lazily probe the optional Numba runtime without affecting app import."""
    global _CAPABILITY
    if _CAPABILITY is not None:
        return _CAPABILITY
    with _STATE_LOCK:
        if _CAPABILITY is not None:
            return _CAPABILITY
        try:
            numba = importlib.import_module("numba")
        except ModuleNotFoundError:
            _CAPABILITY = NativeCpuCapability(False, "numba_not_installed", None)
        except Exception:
            _CAPABILITY = NativeCpuCapability(False, "numba_import_failed", None)
        else:
            _CAPABILITY = NativeCpuCapability(
                True,
                None,
                str(getattr(numba, "__version__", "unknown")),
            )
        return _CAPABILITY


def _make_kernel() -> Callable[..., None]:
    numba = importlib.import_module("numba")

    @numba.njit(inline="always", fastmath=False)
    def ray_box_entry(
        origin_x: float,
        origin_y: float,
        origin_z: float,
        direction_x: float,
        direction_y: float,
        direction_z: float,
        inverse_x: float,
        inverse_y: float,
        inverse_z: float,
        bounds_min: FloatArray,
        bounds_max: FloatArray,
        node_index: int,
        minimum_t: float,
        maximum_t: float,
    ) -> float:
        entry = minimum_t
        exit_distance = maximum_t
        for axis in range(3):
            if axis == 0:
                origin_value = origin_x
                direction_value = direction_x
                inverse_value = inverse_x
            elif axis == 1:
                origin_value = origin_y
                direction_value = direction_y
                inverse_value = inverse_y
            else:
                origin_value = origin_z
                direction_value = direction_z
                inverse_value = inverse_z
            if abs(direction_value) < 1e-12:
                if (
                    origin_value < bounds_min[node_index, axis]
                    or origin_value > bounds_max[node_index, axis]
                ):
                    return math.inf
                continue
            axis_entry = (
                bounds_min[node_index, axis] - origin_value
            ) * inverse_value
            axis_exit = (
                bounds_max[node_index, axis] - origin_value
            ) * inverse_value
            if axis_entry > axis_exit:
                temporary = axis_entry
                axis_entry = axis_exit
                axis_exit = temporary
            entry = max(entry, axis_entry)
            exit_distance = min(exit_distance, axis_exit)
            if exit_distance < entry:
                return math.inf
        return entry

    @numba.njit(nogil=True, fastmath=False)
    def intersect_kernel(
        origins: FloatArray,
        directions: FloatArray,
        minimum_t_values: FloatArray,
        maximum_t_values: FloatArray,
        ignored_faces: IntArray,
        triangle_v0: FloatArray,
        triangle_edge1: FloatArray,
        triangle_edge2: FloatArray,
        node_bounds_min: FloatArray,
        node_bounds_max: FloatArray,
        node_left: IntArray,
        node_right: IntArray,
        node_start: IntArray,
        node_count: IntArray,
        ordered_faces: IntArray,
        distances: FloatArray,
        face_indices: IntArray,
        stack_entries: FloatArray,
        stack_nodes: IntArray,
    ) -> None:
        ray_count = origins.shape[0]
        total_nodes = node_count.shape[0]
        for ray_index in range(ray_count):
            distances[ray_index] = math.inf
            face_indices[ray_index] = -1
            if total_nodes == 0:
                continue
            minimum_t = minimum_t_values[ray_index]
            maximum_t = maximum_t_values[ray_index]
            if maximum_t <= minimum_t:
                continue

            origin_x = origins[ray_index, 0]
            origin_y = origins[ray_index, 1]
            origin_z = origins[ray_index, 2]
            direction_x = directions[ray_index, 0]
            direction_y = directions[ray_index, 1]
            direction_z = directions[ray_index, 2]
            inverse_x = 0.0 if abs(direction_x) < 1e-15 else 1.0 / direction_x
            inverse_y = 0.0 if abs(direction_y) < 1e-15 else 1.0 / direction_y
            inverse_z = 0.0 if abs(direction_z) < 1e-15 else 1.0 / direction_z
            root_entry = ray_box_entry(
                origin_x,
                origin_y,
                origin_z,
                direction_x,
                direction_y,
                direction_z,
                inverse_x,
                inverse_y,
                inverse_z,
                node_bounds_min,
                node_bounds_max,
                0,
                minimum_t,
                maximum_t,
            )
            if math.isinf(root_entry):
                continue

            best_distance = maximum_t
            best_face_index = -1
            stack_entries[0] = root_entry
            stack_nodes[0] = 0
            stack_size = 1
            while stack_size:
                stack_size -= 1
                entry = stack_entries[stack_size]
                current_node = stack_nodes[stack_size]
                if entry > best_distance:
                    continue

                leaf_count = node_count[current_node]
                if leaf_count > 0:
                    start = node_start[current_node]
                    end = start + leaf_count
                    for ordered_index in range(start, end):
                        face_index = ordered_faces[ordered_index]
                        if face_index == ignored_faces[ray_index]:
                            continue

                        edge1_x = triangle_edge1[face_index, 0]
                        edge1_y = triangle_edge1[face_index, 1]
                        edge1_z = triangle_edge1[face_index, 2]
                        edge2_x = triangle_edge2[face_index, 0]
                        edge2_y = triangle_edge2[face_index, 1]
                        edge2_z = triangle_edge2[face_index, 2]
                        cross_x = direction_y * edge2_z - direction_z * edge2_y
                        cross_y = direction_z * edge2_x - direction_x * edge2_z
                        cross_z = direction_x * edge2_y - direction_y * edge2_x
                        determinant = (
                            edge1_x * cross_x
                            + edge1_y * cross_y
                            + edge1_z * cross_z
                        )
                        if -1e-8 < determinant < 1e-8:
                            continue
                        inverse_determinant = 1.0 / determinant
                        offset_x = origin_x - triangle_v0[face_index, 0]
                        offset_y = origin_y - triangle_v0[face_index, 1]
                        offset_z = origin_z - triangle_v0[face_index, 2]
                        u = (
                            offset_x * cross_x
                            + offset_y * cross_y
                            + offset_z * cross_z
                        ) * inverse_determinant
                        if u < 0.0 or u > 1.0:
                            continue
                        offset_cross_x = offset_y * edge1_z - offset_z * edge1_y
                        offset_cross_y = offset_z * edge1_x - offset_x * edge1_z
                        offset_cross_z = offset_x * edge1_y - offset_y * edge1_x
                        v = (
                            direction_x * offset_cross_x
                            + direction_y * offset_cross_y
                            + direction_z * offset_cross_z
                        ) * inverse_determinant
                        if v < 0.0 or u + v > 1.0:
                            continue
                        distance = (
                            edge2_x * offset_cross_x
                            + edge2_y * offset_cross_y
                            + edge2_z * offset_cross_z
                        ) * inverse_determinant
                        if distance <= minimum_t or distance > best_distance:
                            continue
                        if (
                            best_face_index >= 0
                            and abs(distance - best_distance) <= 1e-10
                            and face_index >= best_face_index
                        ):
                            continue
                        best_distance = distance
                        best_face_index = face_index
                    continue

                left_index = node_left[current_node]
                right_index = node_right[current_node]
                left_entry = ray_box_entry(
                    origin_x,
                    origin_y,
                    origin_z,
                    direction_x,
                    direction_y,
                    direction_z,
                    inverse_x,
                    inverse_y,
                    inverse_z,
                    node_bounds_min,
                    node_bounds_max,
                    left_index,
                    minimum_t,
                    best_distance,
                )
                right_entry = ray_box_entry(
                    origin_x,
                    origin_y,
                    origin_z,
                    direction_x,
                    direction_y,
                    direction_z,
                    inverse_x,
                    inverse_y,
                    inverse_z,
                    node_bounds_min,
                    node_bounds_max,
                    right_index,
                    minimum_t,
                    best_distance,
                )
                left_hit = not math.isinf(left_entry)
                right_hit = not math.isinf(right_entry)
                if left_hit and right_hit:
                    if left_entry <= right_entry:
                        stack_entries[stack_size] = right_entry
                        stack_nodes[stack_size] = right_index
                        stack_size += 1
                        stack_entries[stack_size] = left_entry
                        stack_nodes[stack_size] = left_index
                        stack_size += 1
                    else:
                        stack_entries[stack_size] = left_entry
                        stack_nodes[stack_size] = left_index
                        stack_size += 1
                        stack_entries[stack_size] = right_entry
                        stack_nodes[stack_size] = right_index
                        stack_size += 1
                elif left_hit:
                    stack_entries[stack_size] = left_entry
                    stack_nodes[stack_size] = left_index
                    stack_size += 1
                elif right_hit:
                    stack_entries[stack_size] = right_entry
                    stack_nodes[stack_size] = right_index
                    stack_size += 1

            if best_face_index >= 0:
                distances[ray_index] = best_distance
                face_indices[ray_index] = best_face_index
        return None

    return intersect_kernel


def _ensure_kernel(scene: NativeCpuScene) -> tuple[Callable[..., Any], float]:
    global _KERNEL, _KERNEL_COMPILED
    if _KERNEL is not None and _KERNEL_COMPILED:
        return _KERNEL, 0.0
    with _STATE_LOCK:
        if _KERNEL is None:
            try:
                _KERNEL = _make_kernel()
            except Exception as exc:
                raise NativeCpuProviderError(
                    "initialize", "numba_kernel_create_failed"
                ) from exc
        if _KERNEL_COMPILED:
            return _KERNEL, 0.0
        started = time.perf_counter()
        try:
            empty_vectors = np.empty((0, 3), dtype=np.float64)
            empty_floats = np.empty(0, dtype=np.float64)
            empty_faces = np.empty(0, dtype=np.int64)
            stack_size = max(1, len(scene.node_count))
            _KERNEL(
                empty_vectors,
                empty_vectors,
                empty_floats,
                empty_floats,
                empty_faces,
                scene.triangle_v0,
                scene.triangle_edge1,
                scene.triangle_edge2,
                scene.node_bounds_min,
                scene.node_bounds_max,
                scene.node_left,
                scene.node_right,
                scene.node_start,
                scene.node_count,
                scene.ordered_faces,
                empty_floats,
                empty_faces,
                np.empty(stack_size, dtype=np.float64),
                np.empty(stack_size, dtype=np.int64),
            )
        except Exception as exc:
            raise NativeCpuProviderError(
                "initialize", "numba_jit_compile_failed"
            ) from exc
        _KERNEL_COMPILED = True
        return _KERNEL, time.perf_counter() - started


def intersect_native_cpu(
    scene: NativeCpuScene,
    origins: FloatArray,
    directions: FloatArray,
    minimum_t: FloatArray,
    maximum_t: FloatArray,
    ignored_faces: IntArray,
) -> NativeCpuExecution:
    capability = probe_native_cpu()
    if not capability.available:
        raise NativeCpuUnavailable(capability.reason_code or "numba_unavailable")
    kernel, jit_compile_sec = _ensure_kernel(scene)
    distances = np.empty(len(origins), dtype=np.float64)
    face_indices = np.empty(len(origins), dtype=np.int64)
    stack_size = max(1, len(scene.node_count))
    stack_entries = np.empty(stack_size, dtype=np.float64)
    stack_nodes = np.empty(stack_size, dtype=np.int64)
    started = time.perf_counter()
    try:
        kernel(
            origins,
            directions,
            minimum_t,
            maximum_t,
            ignored_faces,
            scene.triangle_v0,
            scene.triangle_edge1,
            scene.triangle_edge2,
            scene.node_bounds_min,
            scene.node_bounds_max,
            scene.node_left,
            scene.node_right,
            scene.node_start,
            scene.node_count,
            scene.ordered_faces,
            distances,
            face_indices,
            stack_entries,
            stack_nodes,
        )
    except Exception as exc:
        raise NativeCpuProviderError("execute", "numba_execute_failed") from exc
    return NativeCpuExecution(
        distances=np.ascontiguousarray(distances, dtype=np.float64),
        face_indices=np.ascontiguousarray(face_indices, dtype=np.int64),
        scene_build_sec=scene.build_sec,
        jit_compile_sec=jit_compile_sec,
        execute_sec=time.perf_counter() - started,
        numba_version=capability.numba_version or "unknown",
    )


def intersect_one_native_cpu(
    scene: NativeCpuScene,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    minimum_t: float,
    maximum_t: float,
    ignored_face: int,
) -> NativeCpuScalarExecution:
    capability = probe_native_cpu()
    if not capability.available:
        raise NativeCpuUnavailable(capability.reason_code or "numba_unavailable")
    kernel, jit_compile_sec = _ensure_kernel(scene)
    stack_size = max(1, len(scene.node_count))
    buffers = getattr(_THREAD_BUFFERS, "values", None)
    if buffers is None or len(buffers[7]) < stack_size:
        buffers = (
            np.empty((1, 3), dtype=np.float64),
            np.empty((1, 3), dtype=np.float64),
            np.empty(1, dtype=np.float64),
            np.empty(1, dtype=np.float64),
            np.empty(1, dtype=np.int64),
            np.empty(1, dtype=np.float64),
            np.empty(1, dtype=np.int64),
            np.empty(stack_size, dtype=np.float64),
            np.empty(stack_size, dtype=np.int64),
        )
        _THREAD_BUFFERS.values = buffers
    (
        origins,
        directions,
        minimum_t_values,
        maximum_t_values,
        ignored_faces,
        distances,
        face_indices,
        stack_entries,
        stack_nodes,
    ) = buffers
    origins[0, 0], origins[0, 1], origins[0, 2] = origin
    directions[0, 0], directions[0, 1], directions[0, 2] = direction
    minimum_t_values[0] = minimum_t
    maximum_t_values[0] = maximum_t
    ignored_faces[0] = ignored_face
    try:
        kernel(
            origins,
            directions,
            minimum_t_values,
            maximum_t_values,
            ignored_faces,
            scene.triangle_v0,
            scene.triangle_edge1,
            scene.triangle_edge2,
            scene.node_bounds_min,
            scene.node_bounds_max,
            scene.node_left,
            scene.node_right,
            scene.node_start,
            scene.node_count,
            scene.ordered_faces,
            distances,
            face_indices,
            stack_entries,
            stack_nodes,
        )
    except Exception as exc:
        raise NativeCpuProviderError("execute", "numba_execute_failed") from exc
    return NativeCpuScalarExecution(
        distance=float(distances[0]),
        face_index=int(face_indices[0]),
        scene_build_sec=scene.build_sec,
        jit_compile_sec=jit_compile_sec,
        execute_sec=0.0,
        numba_version=capability.numba_version or "unknown",
    )
