from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import math
import threading
import time

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .types import Vec3, clamp


def vec_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec_mul(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def vec_div(a: Vec3, s: float) -> Vec3:
    return (a[0] / s, a[1] / s, a[2] / s)


def vec_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vec_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vec_len(a: Vec3) -> float:
    return math.sqrt(vec_dot(a, a))


def vec_norm(a: Vec3) -> Vec3:
    length = vec_len(a)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return vec_div(a, length)


def vec_reflect(v: Vec3, n: Vec3) -> Vec3:
    scale = 2.0 * vec_dot(v, n)
    return vec_sub(v, vec_mul(n, scale))


def clamp01(value: float) -> float:
    return clamp(value, 0.0, 1.0)


def sample_point_on_triangle(
    a: Vec3, b: Vec3, c: Vec3, u: float, v: float
) -> Vec3:
    return vec_add(
        vec_add(a, vec_mul(vec_sub(b, a), u)),
        vec_mul(vec_sub(c, a), v),
    )


def face_area(a: Vec3, b: Vec3, c: Vec3) -> float:
    return 0.5 * vec_len(vec_cross(vec_sub(b, a), vec_sub(c, a)))


def face_normal(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    n = vec_norm(vec_cross(vec_sub(b, a), vec_sub(c, a)))
    return n


def midpoint(a: Vec3, b: Vec3, c: Vec3) -> Vec3:
    return ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)


@dataclass(slots=True)
class TriangleFace:
    v0: int
    v1: int
    v2: int


@dataclass(slots=True)
class HitRecord:
    t: float
    point: Vec3
    normal: Vec3
    face_index: int
    triangle: TriangleFace


def _ray_vector_array(value: ArrayLike, field_name: str) -> NDArray[np.float64]:
    array = np.asarray(value, dtype=np.float64)
    if array.size == 0 and array.shape == (0,):
        array = array.reshape((0, 3))
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError(f"{field_name} must have shape (N, 3)")
    array = np.ascontiguousarray(array, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{field_name} must contain only finite values")
    return array


def _per_ray_float_array(
    value: ArrayLike | float | None,
    ray_count: int,
    field_name: str,
    default: float,
) -> NDArray[np.float64]:
    if value is None:
        return np.full(ray_count, default, dtype=np.float64)
    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 0:
        array = np.full(ray_count, float(array), dtype=np.float64)
    elif array.shape == (ray_count,):
        array = np.ascontiguousarray(array, dtype=np.float64)
    else:
        raise ValueError(f"{field_name} must be a scalar or have shape (N,)")
    if np.any(np.isnan(array)):
        raise ValueError(f"{field_name} must not contain NaN")
    return array


def _per_ray_face_array(
    value: ArrayLike | int | None,
    ray_count: int,
    field_name: str,
) -> NDArray[np.int64]:
    if value is None:
        return np.full(ray_count, -1, dtype=np.int64)
    raw = np.asarray(value)
    if raw.ndim == 0:
        raw = np.full(ray_count, raw)
    elif raw.shape != (ray_count,):
        raise ValueError(f"{field_name} must be a scalar or have shape (N,)")
    if not np.issubdtype(raw.dtype, np.integer):
        numeric = np.asarray(raw, dtype=np.float64)
        if not np.all(np.isfinite(numeric)) or not np.all(numeric == np.floor(numeric)):
            raise ValueError(f"{field_name} must contain integers")
    array = np.ascontiguousarray(raw, dtype=np.int64)
    if np.any(array < -1):
        raise ValueError(f"{field_name} values must be -1 or a face index")
    return array


@dataclass(slots=True)
class RayBatch:
    """GPU-friendly, row-preserving batch of normalized CAD-space rays.

    Per-ray distance limits and ignored faces are always materialized as
    contiguous arrays. ``-1`` means that no face is ignored.
    """

    origins: NDArray[np.float64] | ArrayLike
    directions: NDArray[np.float64] | ArrayLike
    min_t: NDArray[np.float64] | ArrayLike | float = 1e-8
    max_t: NDArray[np.float64] | ArrayLike | float | None = None
    ignore_faces: NDArray[np.int64] | ArrayLike | int | None = None

    def __post_init__(self) -> None:
        self.origins = _ray_vector_array(self.origins, "origins")
        self.directions = _ray_vector_array(self.directions, "directions")
        ray_count = len(self.origins)
        if len(self.directions) != ray_count:
            raise ValueError("origins and directions must contain the same number of rows")
        if ray_count:
            direction_lengths_squared = np.einsum(
                "ij,ij->i",
                self.directions,
                self.directions,
            )
            if not np.allclose(
                direction_lengths_squared,
                1.0,
                rtol=1e-7,
                atol=1e-9,
            ):
                raise ValueError("directions must contain normalized vectors")
        self.min_t = np.maximum(
            _per_ray_float_array(self.min_t, ray_count, "min_t", 1e-8),
            1e-8,
        )
        self.max_t = _per_ray_float_array(
            self.max_t,
            ray_count,
            "max_t",
            float("inf"),
        )
        self.ignore_faces = _per_ray_face_array(
            self.ignore_faces,
            ray_count,
            "ignore_faces",
        )

    def __len__(self) -> int:
        return int(self.origins.shape[0])


@dataclass(slots=True)
class RayHitBatch:
    """Compact closest-hit result aligned one-to-one with a :class:`RayBatch`.

    Misses use the unambiguous pair ``(t=+inf, face_index=-1)``. Hit points,
    normals and TriangleFace objects are materialized only when requested.
    """

    t: NDArray[np.float64] | ArrayLike
    face_indices: NDArray[np.int64] | ArrayLike

    def __post_init__(self) -> None:
        self.t = np.ascontiguousarray(np.asarray(self.t, dtype=np.float64))
        self.face_indices = np.ascontiguousarray(
            np.asarray(self.face_indices, dtype=np.int64)
        )
        if self.t.ndim != 1 or self.face_indices.ndim != 1:
            raise ValueError("batch hit arrays must have shape (N,)")
        if self.t.shape != self.face_indices.shape:
            raise ValueError("t and face_indices must contain the same number of rows")
        if np.any(np.isnan(self.t)):
            raise ValueError("t must not contain NaN")
        if np.any(self.face_indices < -1):
            raise ValueError("face_indices values must be -1 or a face index")
        miss_mask = self.face_indices == -1
        if np.any(miss_mask & ~np.isposinf(self.t)):
            raise ValueError("miss rows must use t=+inf and face_index=-1")
        hit_mask = ~miss_mask
        if np.any(hit_mask & (~np.isfinite(self.t) | (self.t <= 0.0))):
            raise ValueError("hit rows must contain a finite positive t")

    def __len__(self) -> int:
        return int(self.t.shape[0])

    @classmethod
    def empty(cls) -> "RayHitBatch":
        return cls(
            t=np.empty(0, dtype=np.float64),
            face_indices=np.empty(0, dtype=np.int64),
        )

    @property
    def hit_mask(self) -> NDArray[np.bool_]:
        return self.face_indices >= 0

    def materialize(
        self,
        mesh: "TriangleMesh",
        rays: RayBatch,
        index: int,
    ) -> Optional[HitRecord]:
        """Build the legacy scalar hit object for one row when it is needed."""
        if len(rays) != len(self):
            raise ValueError("ray and hit batches must contain the same number of rows")
        if index < 0 or index >= len(self):
            raise IndexError("batch hit index is out of range")
        face_index = int(self.face_indices[index])
        if face_index < 0:
            return None
        if face_index >= len(mesh.faces):
            raise ValueError("batch hit face index is outside the mesh")
        mesh._ensure_prepared_triangles()
        origin = tuple(float(value) for value in rays.origins[index])
        direction = tuple(float(value) for value in rays.directions[index])
        return mesh._make_hit_record(
            face_index,
            float(self.t[index]),
            origin,  # type: ignore[arg-type]
            direction,  # type: ignore[arg-type]
        )


@dataclass(slots=True)
class _PreparedTriangle:
    v0: Vec3
    edge1: Vec3
    edge2: Vec3
    normal: Vec3
    bounds_min: Vec3
    bounds_max: Vec3
    centroid: Vec3


@dataclass(slots=True)
class _FlatBvhNode:
    bounds_min: Vec3
    bounds_max: Vec3
    left: int = -1
    right: int = -1
    start: int = 0
    count: int = 0

    @property
    def is_leaf(self) -> bool:
        return self.count > 0


class TriangleMesh:
    def __init__(self) -> None:
        self.vertices: List[Vec3] = []
        self.faces: List[TriangleFace] = []
        self.face_material: Dict[int, str] = {}
        self.face_metadata: Dict[int, Dict] = {}
        self.intersection_backend = "auto"
        self._prepared_triangles: Optional[List[_PreparedTriangle]] = None
        self._bvh_nodes: Optional[List[_FlatBvhNode]] = None
        self._bvh_face_indices: Optional[List[int]] = None
        self._bvh_build_sec = 0.0
        self._bvh_leaf_count = 0
        self._native_cpu_scene = None
        self._native_cpu_scene_build_sec = 0.0
        self._native_cpu_scene_lock = threading.RLock()

    def add_vertex(self, vertex: Vec3) -> int:
        self.vertices.append(vertex)
        self._invalidate_acceleration()
        return len(self.vertices) - 1

    def add_face(
        self,
        v0: int,
        v1: int,
        v2: int,
        material_id: str,
        metadata: Optional[Dict] = None,
    ) -> int:
        face = TriangleFace(v0=v0, v1=v1, v2=v2)
        self.faces.append(face)
        idx = len(self.faces) - 1
        self.face_material[idx] = material_id
        self.face_metadata[idx] = metadata if metadata is not None else {}
        self._invalidate_acceleration()
        return idx

    def face_vertices(self, index: int) -> Tuple[Vec3, Vec3, Vec3]:
        face = self.faces[index]
        return (
            self.vertices[face.v0],
            self.vertices[face.v1],
            self.vertices[face.v2],
        )

    def area(self, index: int) -> float:
        a, b, c = self.face_vertices(index)
        return face_area(a, b, c)

    def centroid(self, index: int) -> Vec3:
        a, b, c = self.face_vertices(index)
        return midpoint(a, b, c)

    def normal(self, index: int) -> Vec3:
        a, b, c = self.face_vertices(index)
        return face_normal(a, b, c)

    def material_id(self, index: int) -> str:
        return self.face_material.get(index, "")

    def metadata(self, index: int) -> Dict:
        return self.face_metadata.get(index, {})

    def intersect_ray(
        self,
        origin: Vec3,
        direction: Vec3,
        ignore_face: Optional[int] = None,
        min_t: float = 1e-8,
        max_t: Optional[float] = None,
        backend: Optional[str] = None,
    ) -> Optional[HitRecord]:
        if not self.faces:
            return None
        minimum_t = max(1e-8, min_t)
        maximum_t = float("inf") if max_t is None else max_t
        if maximum_t <= minimum_t:
            return None
        selected_backend = self._resolve_intersection_backend(backend)
        self._ensure_prepared_triangles()
        if selected_backend == "brute_force":
            return self._intersect_face_indices(
                self._traceable_face_indices(),
                origin,
                direction,
                ignore_face,
                minimum_t,
                maximum_t,
            )
        if self._bvh_nodes is None or self._bvh_face_indices is None:
            self.prepare_acceleration()
        return self._intersect_bvh(
            origin,
            direction,
            ignore_face,
            minimum_t,
            maximum_t,
        )

    def intersect_rays(
        self,
        rays: RayBatch,
        backend: Optional[str] = None,
    ) -> RayHitBatch:
        """Intersect a row-preserving ray batch using the scalar CPU reference.

        This adapter intentionally delegates every non-empty row to
        :meth:`intersect_ray`. It freezes batch I/O and closest-hit semantics
        before a native CPU or GPU implementation is introduced.
        """
        if not isinstance(rays, RayBatch):
            raise TypeError("rays must be a RayBatch")
        selected_backend = self._resolve_intersection_backend(backend)
        if len(rays) == 0:
            return RayHitBatch.empty()

        distances = np.full(len(rays), float("inf"), dtype=np.float64)
        face_indices = np.full(len(rays), -1, dtype=np.int64)
        for index in range(len(rays)):
            minimum_t = float(rays.min_t[index])
            maximum_t = float(rays.max_t[index])
            if maximum_t <= minimum_t:
                continue
            origin = tuple(float(value) for value in rays.origins[index])
            direction = tuple(float(value) for value in rays.directions[index])
            ignored_face = int(rays.ignore_faces[index])
            hit = self.intersect_ray(
                origin,  # type: ignore[arg-type]
                direction,  # type: ignore[arg-type]
                ignore_face=ignored_face if ignored_face >= 0 else None,
                min_t=minimum_t,
                max_t=maximum_t,
                backend=selected_backend,
            )
            if hit is None:
                continue
            distances[index] = hit.t
            face_indices[index] = hit.face_index
        return RayHitBatch(t=distances, face_indices=face_indices)

    def prepare_native_cpu_scene(self):
        """Pack the flat BVH into immutable arrays for an optional provider."""
        from .native_cpu_intersection import NativeCpuScene

        with self._native_cpu_scene_lock:
            if self._native_cpu_scene is not None:
                return self._native_cpu_scene
            started = time.perf_counter()
            self._ensure_prepared_triangles()
            self.prepare_acceleration()
            prepared = self._prepared_triangles or []
            nodes = self._bvh_nodes or []

            triangle_v0 = np.asarray(
                [triangle.v0 for triangle in prepared], dtype=np.float64
            ).reshape((-1, 3))
            triangle_edge1 = np.asarray(
                [triangle.edge1 for triangle in prepared], dtype=np.float64
            ).reshape((-1, 3))
            triangle_edge2 = np.asarray(
                [triangle.edge2 for triangle in prepared], dtype=np.float64
            ).reshape((-1, 3))
            node_bounds_min = np.asarray(
                [node.bounds_min for node in nodes], dtype=np.float64
            ).reshape((-1, 3))
            node_bounds_max = np.asarray(
                [node.bounds_max for node in nodes], dtype=np.float64
            ).reshape((-1, 3))
            node_left = np.asarray([node.left for node in nodes], dtype=np.int64)
            node_right = np.asarray([node.right for node in nodes], dtype=np.int64)
            node_start = np.asarray([node.start for node in nodes], dtype=np.int64)
            node_count = np.asarray([node.count for node in nodes], dtype=np.int64)
            ordered_faces = np.asarray(
                self._bvh_face_indices or [], dtype=np.int64
            )
            traceable_face_mask = np.asarray(
                [
                    not bool(self.metadata(index).get("trace_excluded", False))
                    for index in range(len(self.faces))
                ],
                dtype=np.bool_,
            )
            arrays = (
                triangle_v0,
                triangle_edge1,
                triangle_edge2,
                node_bounds_min,
                node_bounds_max,
                node_left,
                node_right,
                node_start,
                node_count,
                ordered_faces,
                traceable_face_mask,
            )
            for array in arrays:
                array.setflags(write=False)
            self._native_cpu_scene_build_sec = time.perf_counter() - started
            self._native_cpu_scene = NativeCpuScene(
                triangle_v0=triangle_v0,
                triangle_edge1=triangle_edge1,
                triangle_edge2=triangle_edge2,
                node_bounds_min=node_bounds_min,
                node_bounds_max=node_bounds_max,
                node_left=node_left,
                node_right=node_right,
                node_start=node_start,
                node_count=node_count,
                ordered_faces=ordered_faces,
                traceable_face_mask=traceable_face_mask,
                build_sec=self._native_cpu_scene_build_sec,
            )
            return self._native_cpu_scene

    def intersect_rays_native_cpu(
        self,
        rays: RayBatch,
        backend: Optional[str] = None,
    ):
        """Run one logical batch through the optional strict-float64 provider."""
        from .native_cpu_intersection import (
            NativeCpuCapability,
            NativeCpuExecution,
            NativeCpuProviderError,
            NativeCpuUnavailable,
            intersect_native_cpu,
            probe_native_cpu,
        )

        if not isinstance(rays, RayBatch):
            raise TypeError("rays must be a RayBatch")
        if len(rays) == 0:
            return (
                RayHitBatch.empty(),
                NativeCpuExecution(
                    distances=np.empty(0, dtype=np.float64),
                    face_indices=np.empty(0, dtype=np.int64),
                    scene_build_sec=0.0,
                    jit_compile_sec=0.0,
                    execute_sec=0.0,
                    numba_version="not_probed",
                ),
            )
        if self._resolve_intersection_backend(backend) != "bvh":
            raise NativeCpuUnavailable("native_cpu_requires_bvh")
        capability: NativeCpuCapability = probe_native_cpu()
        if not capability.available:
            raise NativeCpuUnavailable(
                capability.reason_code or "numba_unavailable"
            )
        scene = self.prepare_native_cpu_scene()
        execution = intersect_native_cpu(
            scene,
            rays.origins,
            rays.directions,
            rays.min_t,
            rays.max_t,
            rays.ignore_faces,
        )
        if (
            not isinstance(execution.distances, np.ndarray)
            or not isinstance(execution.face_indices, np.ndarray)
            or execution.distances.dtype != np.float64
            or execution.face_indices.dtype != np.int64
            or execution.distances.shape != (len(rays),)
            or execution.face_indices.shape != (len(rays),)
        ):
            raise NativeCpuProviderError(
                "result_validation", "native_result_shape_or_dtype_invalid"
            )
        if np.any(execution.face_indices < -1) or np.any(
            execution.face_indices >= len(self.faces)
        ):
            raise NativeCpuProviderError(
                "result_validation", "native_result_face_out_of_range"
            )
        hit_mask = execution.face_indices >= 0
        if np.any(
            hit_mask
            & (
                (execution.distances <= rays.min_t)
                | (execution.distances > rays.max_t)
            )
        ):
            raise NativeCpuProviderError(
                "result_validation", "native_result_distance_out_of_bounds"
            )
        if np.any(hit_mask & (execution.face_indices == rays.ignore_faces)):
            raise NativeCpuProviderError(
                "result_validation", "native_result_ignored_face"
            )
        if np.any(hit_mask):
            hit_faces = execution.face_indices[hit_mask]
            if np.any(~scene.traceable_face_mask[hit_faces]):
                raise NativeCpuProviderError(
                    "result_validation", "native_result_trace_excluded_face"
                )
        try:
            hits = RayHitBatch(
                t=execution.distances,
                face_indices=execution.face_indices,
            )
        except (TypeError, ValueError) as exc:
            raise NativeCpuProviderError(
                "result_validation", "native_result_invalid"
            ) from exc
        return hits, execution

    def intersect_ray_native_cpu(
        self,
        origin: Vec3,
        direction: Vec3,
        ignore_face: Optional[int] = None,
        min_t: float = 1e-8,
        max_t: Optional[float] = None,
        backend: Optional[str] = None,
    ):
        """Run one scalar query through the same optional native provider."""
        from .native_cpu_intersection import (
            NativeCpuCapability,
            NativeCpuProviderError,
            NativeCpuScalarExecution,
            NativeCpuUnavailable,
            intersect_one_native_cpu,
            probe_native_cpu,
        )

        minimum_t = max(1e-8, min_t)
        maximum_t = float("inf") if max_t is None else max_t
        if maximum_t <= minimum_t or not self.faces:
            return (
                None,
                NativeCpuScalarExecution(
                    distance=float("inf"),
                    face_index=-1,
                    scene_build_sec=0.0,
                    jit_compile_sec=0.0,
                    execute_sec=0.0,
                    numba_version="not_probed",
                ),
            )
        if self._resolve_intersection_backend(backend) != "bvh":
            raise NativeCpuUnavailable("native_cpu_requires_bvh")
        capability: NativeCpuCapability = probe_native_cpu()
        if not capability.available:
            raise NativeCpuUnavailable(
                capability.reason_code or "numba_unavailable"
            )
        scene = self.prepare_native_cpu_scene()
        execution = intersect_one_native_cpu(
            scene,
            origin,
            direction,
            minimum_t,
            maximum_t,
            ignore_face if ignore_face is not None else -1,
        )
        face_index = execution.face_index
        if face_index < -1 or face_index >= len(self.faces):
            raise NativeCpuProviderError(
                "result_validation", "native_result_face_out_of_range"
            )
        if face_index < 0:
            if not math.isinf(execution.distance) or execution.distance < 0.0:
                raise NativeCpuProviderError(
                    "result_validation", "native_result_invalid"
                )
            return None, execution
        if not math.isfinite(execution.distance) or execution.distance <= 0.0:
            raise NativeCpuProviderError(
                "result_validation", "native_result_invalid"
            )
        if execution.distance <= minimum_t or execution.distance > maximum_t:
            raise NativeCpuProviderError(
                "result_validation", "native_result_distance_out_of_bounds"
            )
        if ignore_face is not None and face_index == ignore_face:
            raise NativeCpuProviderError(
                "result_validation", "native_result_ignored_face"
            )
        if bool(self.metadata(face_index).get("trace_excluded", False)):
            raise NativeCpuProviderError(
                "result_validation", "native_result_trace_excluded_face"
            )
        return (
            self._make_hit_record(
                face_index,
                execution.distance,
                origin,
                direction,
            ),
            execution,
        )

    def set_intersection_backend(self, backend: str) -> None:
        if backend not in {"auto", "brute_force", "bvh"}:
            raise ValueError("intersection backend must be auto, brute_force, or bvh")
        self.intersection_backend = backend

    def prepare_acceleration(self) -> Dict[str, float | int | str]:
        self._ensure_prepared_triangles()
        if not self.faces:
            return self.acceleration_info()
        if self._bvh_nodes is None or self._bvh_face_indices is None:
            started = time.perf_counter()
            self._bvh_nodes = []
            self._bvh_face_indices = []
            self._bvh_leaf_count = 0
            traceable_faces = self._traceable_face_indices()
            if traceable_faces:
                self._build_flat_bvh(traceable_faces)
            self._bvh_build_sec = time.perf_counter() - started
        return self.acceleration_info()

    def acceleration_info(
        self,
        backend: Optional[str] = None,
    ) -> Dict[str, float | int | str]:
        return {
            "selected_backend": self._resolve_intersection_backend(backend),
            "configured_backend": self.intersection_backend,
            "triangle_count": len(self.faces),
            "bvh_node_count": len(self._bvh_nodes or []),
            "bvh_leaf_count": self._bvh_leaf_count,
            "bvh_build_sec": self._bvh_build_sec,
            "native_cpu_scene_build_sec": self._native_cpu_scene_build_sec,
        }

    def _resolve_intersection_backend(self, backend: Optional[str]) -> str:
        selected = backend or self.intersection_backend
        if selected == "auto":
            return "brute_force" if len(self.faces) <= 24 else "bvh"
        if selected not in {"brute_force", "bvh"}:
            raise ValueError("intersection backend must be auto, brute_force, or bvh")
        return selected

    def _intersect_bvh(
        self,
        origin: Vec3,
        direction: Vec3,
        ignore_face: Optional[int],
        minimum_t: float,
        maximum_t: float,
    ) -> Optional[HitRecord]:
        nodes = self._bvh_nodes
        ordered_faces = self._bvh_face_indices
        if not nodes or ordered_faces is None:
            return None
        inverse_direction = tuple(
            0.0 if abs(value) < 1e-15 else 1.0 / value
            for value in direction
        )
        root_entry = self._ray_box_entry_fast(
            origin,
            direction,
            inverse_direction,
            nodes[0].bounds_min,
            nodes[0].bounds_max,
            minimum_t,
            maximum_t,
        )
        if root_entry is None:
            return None
        best_distance = maximum_t
        best_face_index = -1
        stack: List[Tuple[float, int]] = [(root_entry, 0)]
        while stack:
            entry, node_index = stack.pop()
            if entry > best_distance:
                continue
            node = nodes[node_index]
            if node.is_leaf:
                best_distance, best_face_index = self._intersect_prepared_range(
                    ordered_faces,
                    node.start,
                    node.count,
                    origin,
                    direction,
                    ignore_face,
                    minimum_t,
                    best_distance,
                    best_face_index,
                )
                continue
            left_node = nodes[node.left]
            right_node = nodes[node.right]
            left_entry = self._ray_box_entry_fast(
                origin,
                direction,
                inverse_direction,
                left_node.bounds_min,
                left_node.bounds_max,
                minimum_t,
                best_distance,
            )
            right_entry = self._ray_box_entry_fast(
                origin,
                direction,
                inverse_direction,
                right_node.bounds_min,
                right_node.bounds_max,
                minimum_t,
                best_distance,
            )
            if left_entry is not None and right_entry is not None:
                if left_entry <= right_entry:
                    stack.append((right_entry, node.right))
                    stack.append((left_entry, node.left))
                else:
                    stack.append((left_entry, node.left))
                    stack.append((right_entry, node.right))
            elif left_entry is not None:
                stack.append((left_entry, node.left))
            elif right_entry is not None:
                stack.append((right_entry, node.right))
        if best_face_index < 0:
            return None
        return self._make_hit_record(
            best_face_index,
            best_distance,
            origin,
            direction,
        )

    def _intersect_face_indices(
        self,
        face_indices: Iterable[int],
        origin: Vec3,
        direction: Vec3,
        ignore_face: Optional[int],
        min_t: float,
        max_t: float,
    ) -> Optional[HitRecord]:
        self._ensure_prepared_triangles()
        prepared = self._prepared_triangles or []
        best_distance = max_t
        best_face_index = -1
        eps = 1e-8
        for idx in face_indices:
            if ignore_face is not None and idx == ignore_face:
                continue
            triangle = prepared[idx]
            edge1_x, edge1_y, edge1_z = triangle.edge1
            edge2_x, edge2_y, edge2_z = triangle.edge2
            cross_x = direction[1] * edge2_z - direction[2] * edge2_y
            cross_y = direction[2] * edge2_x - direction[0] * edge2_z
            cross_z = direction[0] * edge2_y - direction[1] * edge2_x
            determinant = edge1_x * cross_x + edge1_y * cross_y + edge1_z * cross_z
            if abs(determinant) < eps:
                continue
            inverse_determinant = 1.0 / determinant
            offset_x = origin[0] - triangle.v0[0]
            offset_y = origin[1] - triangle.v0[1]
            offset_z = origin[2] - triangle.v0[2]
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
                direction[0] * offset_cross_x
                + direction[1] * offset_cross_y
                + direction[2] * offset_cross_z
            ) * inverse_determinant
            if v < 0.0 or u + v > 1.0:
                continue
            distance = (
                edge2_x * offset_cross_x
                + edge2_y * offset_cross_y
                + edge2_z * offset_cross_z
            ) * inverse_determinant
            if distance <= min_t or distance > best_distance:
                continue
            if (
                best_face_index >= 0
                and abs(distance - best_distance) <= 1e-10
                and idx >= best_face_index
            ):
                continue
            best_distance = distance
            best_face_index = idx
        if best_face_index < 0:
            return None
        return self._make_hit_record(
            best_face_index,
            best_distance,
            origin,
            direction,
        )

    def _traceable_face_indices(self) -> List[int]:
        """Faces eligible for ray collision.

        Emitter-only CAD faces remain in the mesh for origin/normal sampling,
        but TRACE OFF components must be transparent to every traced ray.
        """
        return [
            index
            for index in range(len(self.faces))
            if not bool(self.metadata(index).get("trace_excluded", False))
        ]

    def _intersect_prepared_range(
        self,
        ordered_faces: List[int],
        start: int,
        count: int,
        origin: Vec3,
        direction: Vec3,
        ignore_face: Optional[int],
        min_t: float,
        max_t: float,
        current_best_face: int,
    ) -> Tuple[float, int]:
        prepared = self._prepared_triangles or []
        best_distance = max_t
        best_face_index = current_best_face
        eps = 1e-8
        end = start + count
        for ordered_index in range(start, end):
            face_index = ordered_faces[ordered_index]
            if ignore_face is not None and face_index == ignore_face:
                continue
            triangle = prepared[face_index]
            edge1_x, edge1_y, edge1_z = triangle.edge1
            edge2_x, edge2_y, edge2_z = triangle.edge2
            cross_x = direction[1] * edge2_z - direction[2] * edge2_y
            cross_y = direction[2] * edge2_x - direction[0] * edge2_z
            cross_z = direction[0] * edge2_y - direction[1] * edge2_x
            determinant = edge1_x * cross_x + edge1_y * cross_y + edge1_z * cross_z
            if -eps < determinant < eps:
                continue
            inverse_determinant = 1.0 / determinant
            offset_x = origin[0] - triangle.v0[0]
            offset_y = origin[1] - triangle.v0[1]
            offset_z = origin[2] - triangle.v0[2]
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
                direction[0] * offset_cross_x
                + direction[1] * offset_cross_y
                + direction[2] * offset_cross_z
            ) * inverse_determinant
            if v < 0.0 or u + v > 1.0:
                continue
            distance = (
                edge2_x * offset_cross_x
                + edge2_y * offset_cross_y
                + edge2_z * offset_cross_z
            ) * inverse_determinant
            if distance <= min_t or distance > best_distance:
                continue
            if (
                best_face_index >= 0
                and abs(distance - best_distance) <= 1e-10
                and face_index >= best_face_index
            ):
                continue
            best_distance = distance
            best_face_index = face_index
        return best_distance, best_face_index

    def _build_flat_bvh(self, face_indices: List[int], leaf_size: int = 8) -> int:
        prepared = self._prepared_triangles or []
        nodes = self._bvh_nodes
        ordered_faces = self._bvh_face_indices
        if nodes is None or ordered_faces is None:
            raise RuntimeError("BVH storage was not initialized")
        bounds_min = tuple(
            min(prepared[index].bounds_min[axis] for index in face_indices)
            for axis in range(3)
        )
        bounds_max = tuple(
            max(prepared[index].bounds_max[axis] for index in face_indices)
            for axis in range(3)
        )
        node_index = len(nodes)
        nodes.append(_FlatBvhNode(bounds_min=bounds_min, bounds_max=bounds_max))
        if len(face_indices) <= leaf_size:
            start = len(ordered_faces)
            ordered_faces.extend(face_indices)
            nodes[node_index].start = start
            nodes[node_index].count = len(face_indices)
            self._bvh_leaf_count += 1
            return node_index
        extents = [
            max(prepared[index].centroid[axis] for index in face_indices)
            - min(prepared[index].centroid[axis] for index in face_indices)
            for axis in range(3)
        ]
        split_axis = max(range(3), key=lambda axis: extents[axis])
        ordered = sorted(
            face_indices,
            key=lambda face_index: prepared[face_index].centroid[split_axis],
        )
        midpoint_index = len(ordered) // 2
        nodes[node_index].left = self._build_flat_bvh(
            ordered[:midpoint_index],
            leaf_size,
        )
        nodes[node_index].right = self._build_flat_bvh(
            ordered[midpoint_index:],
            leaf_size,
        )
        return node_index

    def _ensure_prepared_triangles(self) -> None:
        if self._prepared_triangles is not None:
            return
        prepared: List[_PreparedTriangle] = []
        for face in self.faces:
            v0 = self.vertices[face.v0]
            v1 = self.vertices[face.v1]
            v2 = self.vertices[face.v2]
            edge1 = (
                v1[0] - v0[0],
                v1[1] - v0[1],
                v1[2] - v0[2],
            )
            edge2 = (
                v2[0] - v0[0],
                v2[1] - v0[1],
                v2[2] - v0[2],
            )
            normal = vec_norm(vec_cross(edge1, edge2))
            prepared.append(
                _PreparedTriangle(
                    v0=v0,
                    edge1=edge1,
                    edge2=edge2,
                    normal=normal,
                    bounds_min=(
                        min(v0[0], v1[0], v2[0]),
                        min(v0[1], v1[1], v2[1]),
                        min(v0[2], v1[2], v2[2]),
                    ),
                    bounds_max=(
                        max(v0[0], v1[0], v2[0]),
                        max(v0[1], v1[1], v2[1]),
                        max(v0[2], v1[2], v2[2]),
                    ),
                    centroid=(
                        (v0[0] + v1[0] + v2[0]) / 3.0,
                        (v0[1] + v1[1] + v2[1]) / 3.0,
                        (v0[2] + v1[2] + v2[2]) / 3.0,
                    ),
                )
            )
        self._prepared_triangles = prepared

    def _make_hit_record(
        self,
        face_index: int,
        distance: float,
        origin: Vec3,
        direction: Vec3,
    ) -> HitRecord:
        triangle = (self._prepared_triangles or [])[face_index]
        normal = triangle.normal
        if (
            normal[0] * direction[0]
            + normal[1] * direction[1]
            + normal[2] * direction[2]
        ) > 0.0:
            normal = (-normal[0], -normal[1], -normal[2])
        return HitRecord(
            t=distance,
            point=(
                origin[0] + direction[0] * distance,
                origin[1] + direction[1] * distance,
                origin[2] + direction[2] * distance,
            ),
            normal=normal,
            face_index=face_index,
            triangle=self.faces[face_index],
        )

    def _invalidate_acceleration(self) -> None:
        self._prepared_triangles = None
        self._bvh_nodes = None
        self._bvh_face_indices = None
        self._bvh_build_sec = 0.0
        self._bvh_leaf_count = 0
        self._native_cpu_scene = None
        self._native_cpu_scene_build_sec = 0.0

    @staticmethod
    def _ray_box_entry_fast(
        origin: Vec3,
        direction: Vec3,
        inverse_direction: Vec3,
        bounds_min: Vec3,
        bounds_max: Vec3,
        min_t: float,
        max_t: float,
    ) -> Optional[float]:
        entry = min_t
        exit_distance = max_t
        for axis in range(3):
            if abs(direction[axis]) < 1e-12:
                if origin[axis] < bounds_min[axis] or origin[axis] > bounds_max[axis]:
                    return None
                continue
            axis_entry = (
                bounds_min[axis] - origin[axis]
            ) * inverse_direction[axis]
            axis_exit = (
                bounds_max[axis] - origin[axis]
            ) * inverse_direction[axis]
            if axis_entry > axis_exit:
                axis_entry, axis_exit = axis_exit, axis_entry
            entry = max(entry, axis_entry)
            exit_distance = min(exit_distance, axis_exit)
            if exit_distance < entry:
                return None
        return entry


def estimate_subdivided_face_count(
    mesh: TriangleMesh,
    max_area_mm2: float,
    max_depth: int = 9,
) -> int:
    """Predict the face count produced by midpoint quadrisection."""
    if max_area_mm2 <= 0.0:
        raise ValueError("max_area_mm2 must be positive")
    depth_limit = max(0, int(max_depth))
    total = 0
    for face_index in range(len(mesh.faces)):
        area = mesh.area(face_index)
        depth = 0
        while area > max_area_mm2 and depth < depth_limit:
            area /= 4.0
            depth += 1
        total += 4 ** depth
    return total


def build_feature_edge_segments(
    mesh: TriangleMesh,
    threshold_angle_deg: float = 18.0,
) -> List[Dict]:
    """Build CAD feature edges before adaptive ROI subdivision.

    Coplanar triangle diagonals are tessellation details rather than visible
    CAD edges. Building this list from the original mesh also prevents
    subdivision T-junctions from appearing as false edges in the viewer.
    """
    edge_faces: Dict[Tuple[int, int], List[int]] = {}
    for face_index, face in enumerate(mesh.faces):
        for start, end in (
            (face.v0, face.v1),
            (face.v1, face.v2),
            (face.v2, face.v0),
        ):
            edge = (start, end) if start < end else (end, start)
            edge_faces.setdefault(edge, []).append(face_index)

    threshold_cosine = math.cos(math.radians(float(threshold_angle_deg)))
    segments: List[Dict] = []
    for (start, end), adjacent_faces in edge_faces.items():
        is_feature = len(adjacent_faces) != 2
        if len(adjacent_faces) == 2:
            normal_a = mesh.normal(adjacent_faces[0])
            normal_b = mesh.normal(adjacent_faces[1])
            is_feature = abs(vec_dot(normal_a, normal_b)) <= threshold_cosine
        if not is_feature:
            continue

        component_ids = {
            mesh.metadata(face_index).get("step_component_id")
            for face_index in adjacent_faces
            if mesh.metadata(face_index).get("step_component_id") is not None
        }
        segments.append(
            {
                "start": mesh.vertices[start],
                "end": mesh.vertices[end],
                "adjacent_face_indices": list(adjacent_faces),
                "step_component_id": next(iter(component_ids)) if len(component_ids) == 1 else None,
            }
        )
    return segments


def choose_adaptive_subdivision_area_mm2(
    mesh: TriangleMesh,
    target_divisions_across_diagonal: int = 512,
    min_target_edge_mm: float = 0.5,
    max_target_edge_mm: float = 3.0,
    max_output_faces: int = 750_000,
    max_depth: int = 9,
) -> float:
    """Choose a CAD-size-aware triangle area target for precise ROI picking.

    The nominal edge scale follows the model diagonal, while lower/upper edge
    bounds keep very small and very large CAD models useful. If that density
    would exceed the output budget, the area target is raised just enough to
    select the finest quadrisection level that stays within the budget.
    """
    if not mesh.vertices or not mesh.faces:
        return max(1e-9, 0.5 * min_target_edge_mm * min_target_edge_mm)
    if target_divisions_across_diagonal <= 0:
        raise ValueError("target_divisions_across_diagonal must be positive")
    if min_target_edge_mm <= 0.0 or max_target_edge_mm < min_target_edge_mm:
        raise ValueError("adaptive subdivision edge bounds are invalid")

    spans = [
        max(vertex[axis] for vertex in mesh.vertices)
        - min(vertex[axis] for vertex in mesh.vertices)
        for axis in range(3)
    ]
    diagonal = math.sqrt(sum(span * span for span in spans))
    nominal_edge = diagonal / float(target_divisions_across_diagonal)
    target_edge = clamp(nominal_edge, min_target_edge_mm, max_target_edge_mm)
    nominal_area = max(1e-9, 0.5 * target_edge * target_edge)

    face_budget = max(len(mesh.faces), int(max_output_faces))
    if estimate_subdivided_face_count(mesh, nominal_area, max_depth) <= face_budget:
        return nominal_area

    low = nominal_area
    high = max(max(mesh.area(index) for index in range(len(mesh.faces))), low)
    while estimate_subdivided_face_count(mesh, high, max_depth) > face_budget:
        high *= 4.0

    # The estimator changes in discrete quadrisection steps. A geometric
    # search finds the smallest safe threshold without assuming uniform faces.
    for _ in range(48):
        middle = math.sqrt(low * high)
        if estimate_subdivided_face_count(mesh, middle, max_depth) > face_budget:
            low = middle
        else:
            high = middle
    return high * (1.0 + 1e-12)


def subdivide_flat_mesh(
    mesh: TriangleMesh,
    max_area_mm2: float,
    max_depth: int = 9,
) -> TriangleMesh:
    """Returns a new TriangleMesh where any face whose area exceeds
    max_area_mm2 is recursively split into 4 (by connecting edge
    midpoints, i.e. plain triangle quadrisection) until every piece is
    small enough or max_depth is reached.

    BRepMesh_IncrementalMesh's deflection tolerance only governs curvature
    error, so a perfectly flat panel (e.g. a diffuser/LGP sheet) still
    comes out of STEP tessellation as just 1-2 huge triangles no matter
    how fine the deflection is set. ROI box-drag selection clips at face
    granularity (no sub-triangle clipping - see roi.py), so without this
    step, dragging over any part of such a coarsely-tessellated panel
    swept in the whole thing - reported as ROI area always coming out to
    that one panel's total area regardless of where the box was drawn.
    This closes that gap at import time instead of requiring real
    polygon-clipping.

    Quadrisection preserves the exact original flat shape (midpoints of a
    planar triangle are themselves in-plane), so this changes tessellation
    density only, not geometry. All resulting sub-faces inherit their
    parent's material_id and metadata unchanged (multiple sub-faces
    sharing the same "source" component/step-face metadata is expected -
    downstream component grouping in components.py already aggregates by
    that metadata across many faces per component).
    """
    result = TriangleMesh()
    vertex_map: Dict[Tuple[int, int, int], int] = {}

    def dedup_vertex(point: Vec3) -> int:
        key = (round(point[0] * 1000.0), round(point[1] * 1000.0), round(point[2] * 1000.0))
        existing = vertex_map.get(key)
        if existing is not None:
            return existing
        index = result.add_vertex(point)
        vertex_map[key] = index
        return index

    def edge_midpoint(a: Vec3, b: Vec3) -> Vec3:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)

    def emit(a: Vec3, b: Vec3, c: Vec3, material_id: str, metadata: Dict, depth: int) -> None:
        area = face_area(a, b, c)
        if area <= max_area_mm2 or depth <= 0 or area <= 1e-9:
            result.add_face(dedup_vertex(a), dedup_vertex(b), dedup_vertex(c), material_id, dict(metadata))
            return
        ab = edge_midpoint(a, b)
        bc = edge_midpoint(b, c)
        ca = edge_midpoint(c, a)
        emit(a, ab, ca, material_id, metadata, depth - 1)
        emit(ab, b, bc, material_id, metadata, depth - 1)
        emit(ca, bc, c, material_id, metadata, depth - 1)
        emit(ab, bc, ca, material_id, metadata, depth - 1)

    for face_index in range(len(mesh.faces)):
        v0, v1, v2 = mesh.face_vertices(face_index)
        emit(v0, v1, v2, mesh.material_id(face_index), mesh.metadata(face_index), max_depth)

    return result


def add_box(
    mesh: TriangleMesh,
    x0: float,
    y0: float,
    z0: float,
    x1: float,
    y1: float,
    z1: float,
    material_id: str,
    metadata: Optional[Dict] = None,
) -> List[int]:
    vs = [
        (x0, y0, z0),
        (x1, y0, z0),
        (x1, y1, z0),
        (x0, y1, z0),
        (x0, y0, z1),
        (x1, y0, z1),
        (x1, y1, z1),
        (x0, y1, z1),
    ]
    idx = [mesh.add_vertex(v) for v in vs]
    i0, i1, i2, i3, i4, i5, i6, i7 = idx
    mesh.add_face(i0, i1, i2, material_id, metadata)
    mesh.add_face(i0, i2, i3, material_id, metadata)
    mesh.add_face(i4, i6, i5, material_id, metadata)
    mesh.add_face(i4, i7, i6, material_id, metadata)
    mesh.add_face(i0, i4, i5, material_id, metadata)
    mesh.add_face(i0, i5, i1, material_id, metadata)
    mesh.add_face(i3, i2, i6, material_id, metadata)
    mesh.add_face(i3, i6, i7, material_id, metadata)
    mesh.add_face(i0, i3, i7, material_id, metadata)
    mesh.add_face(i0, i7, i4, material_id, metadata)
    mesh.add_face(i1, i5, i6, material_id, metadata)
    mesh.add_face(i1, i6, i2, material_id, metadata)
    return list(range(len(mesh.faces) - 12, len(mesh.faces)))
