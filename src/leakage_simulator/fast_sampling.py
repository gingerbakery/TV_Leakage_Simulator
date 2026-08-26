from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Tuple
import math

import numpy as np

from .geometry import TriangleMesh
from .types import EmitterSpec


RayBatch = Tuple[np.ndarray, np.ndarray]
FaceRayBatch = Tuple[np.ndarray, np.ndarray, np.ndarray]
WeightedRayBatch = Tuple[np.ndarray, np.ndarray, np.ndarray, int]
WeightedFaceRayBatch = Tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    int,
]


@dataclass(frozen=True, slots=True)
class ReceiverImportanceGeometry:
    centers: np.ndarray
    normals: np.ndarray
    u_axes: np.ndarray
    v_axes: np.ndarray
    half_widths: np.ndarray
    half_heights: np.ndarray
    minimum_cosines: np.ndarray

    def __post_init__(self) -> None:
        centers = np.ascontiguousarray(self.centers, dtype=np.float64)
        normals = np.ascontiguousarray(self.normals, dtype=np.float64)
        u_axes = np.ascontiguousarray(self.u_axes, dtype=np.float64)
        v_axes = np.ascontiguousarray(self.v_axes, dtype=np.float64)
        half_widths = np.ascontiguousarray(self.half_widths, dtype=np.float64)
        half_heights = np.ascontiguousarray(self.half_heights, dtype=np.float64)
        minimum_cosines = np.ascontiguousarray(
            self.minimum_cosines,
            dtype=np.float64,
        )
        receiver_count = len(centers)
        if centers.shape != (receiver_count, 3):
            raise ValueError("receiver centers must have shape (N, 3)")
        if normals.shape != centers.shape:
            raise ValueError("receiver normals must match receiver centers")
        if u_axes.shape != centers.shape or v_axes.shape != centers.shape:
            raise ValueError("receiver axes must match receiver centers")
        for values, name in (
            (half_widths, "half_widths"),
            (half_heights, "half_heights"),
            (minimum_cosines, "minimum_cosines"),
        ):
            if values.shape != (receiver_count,):
                raise ValueError(f"receiver {name} must have shape (N,)")
        if receiver_count == 0:
            raise ValueError("receiver importance sampling requires a receiver")
        if np.any(half_widths <= 0.0) or np.any(half_heights <= 0.0):
            raise ValueError("receiver half sizes must be positive")
        if not all(
            np.all(np.isfinite(values))
            for values in (
                centers,
                normals,
                u_axes,
                v_axes,
                half_widths,
                half_heights,
                minimum_cosines,
            )
        ):
            raise ValueError("receiver importance geometry must be finite")
        object.__setattr__(self, "centers", centers)
        object.__setattr__(self, "normals", _normalize_rows(normals))
        object.__setattr__(self, "u_axes", _normalize_rows(u_axes))
        object.__setattr__(self, "v_axes", _normalize_rows(v_axes))
        object.__setattr__(self, "half_widths", half_widths)
        object.__setattr__(self, "half_heights", half_heights)
        object.__setattr__(self, "minimum_cosines", minimum_cosines)

    @property
    def receiver_count(self) -> int:
        return len(self.centers)

    @property
    def areas_mm2(self) -> np.ndarray:
        return 4.0 * self.half_widths * self.half_heights


@dataclass(frozen=True, slots=True)
class FaceEmitterBatchGeometry:
    face_indices: np.ndarray
    triangle_vertices: np.ndarray
    normals: np.ndarray
    cumulative_weights: np.ndarray


def supports_fast_virtual_plane_sampling(emitter: EmitterSpec) -> bool:
    return (
        emitter.emitter_type != "face"
        and emitter.surface_construction != "polygon_auto"
        and emitter.center is not None
        and emitter.u_axis is not None
        and emitter.v_axis is not None
        and emitter.width_mm is not None
        and emitter.height_mm is not None
    )


def iter_virtual_plane_ray_batches(
    emitter: EmitterSpec,
    epsilon_mm: float,
    seed: int,
    batch_size: int = 65536,
) -> Iterator[RayBatch]:
    center = np.asarray(emitter.center, dtype=np.float64)
    u_axis = _normalize(np.asarray(emitter.u_axis, dtype=np.float64))
    raw_v = np.asarray(emitter.v_axis, dtype=np.float64)
    raw_v = raw_v - u_axis * float(np.dot(raw_v, u_axis))
    v_axis = _normalize(raw_v)
    normal = _normalize(np.cross(u_axis, v_axis))
    if emitter.normal_flip:
        normal = -normal
    basis_u, basis_v = _orthonormal_basis(normal)
    generator = np.random.default_rng(seed)
    remaining = emitter.ray_count
    while remaining > 0:
        count = min(batch_size, remaining)
        remaining -= count
        u_offsets = (generator.random(count) - 0.5) * emitter.width_mm
        v_offsets = (generator.random(count) - 0.5) * emitter.height_mm
        origins = (
            center[None, :]
            + u_offsets[:, None] * u_axis[None, :]
            + v_offsets[:, None] * v_axis[None, :]
            + epsilon_mm * normal[None, :]
        )
        directions = _sample_direction_batch(
            generator,
            emitter,
            normal,
            basis_u,
            basis_v,
            count,
        )
        yield origins, directions


def build_face_emitter_batch_geometry(
    mesh: TriangleMesh,
    emitter: EmitterSpec,
) -> Optional[FaceEmitterBatchGeometry]:
    if emitter.emitter_type != "face":
        return None

    face_indices = []
    triangle_vertices = []
    normals = []
    areas = []
    custom_normal = (
        np.asarray(emitter.custom_normal, dtype=np.float64)
        if emitter.normal_mode == "custom" and emitter.custom_normal is not None
        else None
    )
    for face_index in emitter.face_indices:
        if face_index < 0 or face_index >= len(mesh.faces):
            continue
        area = max(0.0, mesh.area(face_index))
        if area <= 0.0:
            continue
        face_indices.append(face_index)
        triangle_vertices.append(mesh.face_vertices(face_index))
        normals.append(
            custom_normal
            if custom_normal is not None
            else np.asarray(mesh.normal(face_index), dtype=np.float64)
        )
        areas.append(area)
    if not face_indices:
        return None

    normal_rows = _normalize_rows(np.asarray(normals, dtype=np.float64))
    if emitter.normal_flip:
        normal_rows = -normal_rows
    cumulative_weights = np.cumsum(np.asarray(areas, dtype=np.float64))
    cumulative_weights /= cumulative_weights[-1]
    cumulative_weights[-1] = 1.0
    return FaceEmitterBatchGeometry(
        face_indices=np.ascontiguousarray(face_indices, dtype=np.int64),
        triangle_vertices=np.ascontiguousarray(
            triangle_vertices,
            dtype=np.float64,
        ),
        normals=np.ascontiguousarray(normal_rows, dtype=np.float64),
        cumulative_weights=np.ascontiguousarray(
            cumulative_weights,
            dtype=np.float64,
        ),
    )


def iter_face_emitter_ray_batches(
    emitter: EmitterSpec,
    geometry: FaceEmitterBatchGeometry,
    epsilon_mm: float,
    seed: int,
    batch_size: int = 65536,
) -> Iterator[FaceRayBatch]:
    if emitter.emitter_type != "face":
        raise ValueError("Face ray batching requires a face emitter")
    if batch_size <= 0:
        raise ValueError("Face ray batch_size must be positive")

    generator = np.random.default_rng(seed ^ 0x5DEECE66D)
    remaining = emitter.ray_count
    while remaining > 0:
        count = min(batch_size, remaining)
        remaining -= count
        selected_slots = np.searchsorted(
            geometry.cumulative_weights,
            generator.random(count),
            side="left",
        )
        selected_slots = np.minimum(
            selected_slots,
            len(geometry.face_indices) - 1,
        )
        selected_triangles = geometry.triangle_vertices[selected_slots]
        selected_normals = geometry.normals[selected_slots]

        first_random = generator.random(count)
        second_random = generator.random(count)
        root = np.sqrt(first_random)
        weight_a = 1.0 - root
        weight_b = root * (1.0 - second_random)
        weight_c = root * second_random
        points = (
            weight_a[:, None] * selected_triangles[:, 0, :]
            + weight_b[:, None] * selected_triangles[:, 1, :]
            + weight_c[:, None] * selected_triangles[:, 2, :]
        )
        origins = points + epsilon_mm * selected_normals
        directions = _sample_direction_rows(
            generator,
            emitter,
            selected_normals,
        )
        source_faces = geometry.face_indices[selected_slots]
        yield (
            np.ascontiguousarray(origins, dtype=np.float64),
            np.ascontiguousarray(directions, dtype=np.float64),
            np.ascontiguousarray(source_faces, dtype=np.int64),
        )


def iter_virtual_plane_receiver_mis_batches(
    emitter: EmitterSpec,
    receivers: ReceiverImportanceGeometry,
    epsilon_mm: float,
    seed: int,
    receiver_fraction: float,
    batch_size: int = 65536,
) -> Iterator[WeightedRayBatch]:
    if emitter.direction_distribution not in {"lambertian", "isotropic"}:
        raise ValueError(
            "receiver MIS supports lambertian and isotropic emitters"
        )
    center = np.asarray(emitter.center, dtype=np.float64)
    u_axis = _normalize(np.asarray(emitter.u_axis, dtype=np.float64))
    raw_v = np.asarray(emitter.v_axis, dtype=np.float64)
    raw_v = raw_v - u_axis * float(np.dot(raw_v, u_axis))
    v_axis = _normalize(raw_v)
    normal = _normalize(np.cross(u_axis, v_axis))
    if emitter.normal_flip:
        normal = -normal
    generator = np.random.default_rng(seed)
    remaining = emitter.ray_count
    while remaining > 0:
        count = min(batch_size, remaining)
        remaining -= count
        u_offsets = (generator.random(count) - 0.5) * emitter.width_mm
        v_offsets = (generator.random(count) - 0.5) * emitter.height_mm
        origins = (
            center[None, :]
            + u_offsets[:, None] * u_axis[None, :]
            + v_offsets[:, None] * v_axis[None, :]
            + epsilon_mm * normal[None, :]
        )
        normal_rows = np.repeat(normal[None, :], count, axis=0)
        directions, weights, directed_count = sample_receiver_mis_directions(
            generator,
            emitter,
            origins,
            normal_rows,
            receivers,
            receiver_fraction,
            epsilon_mm,
        )
        yield (
            np.ascontiguousarray(origins, dtype=np.float64),
            directions,
            weights,
            directed_count,
        )


def iter_face_emitter_receiver_mis_batches(
    emitter: EmitterSpec,
    geometry: FaceEmitterBatchGeometry,
    receivers: ReceiverImportanceGeometry,
    epsilon_mm: float,
    seed: int,
    receiver_fraction: float,
    batch_size: int = 65536,
) -> Iterator[WeightedFaceRayBatch]:
    if emitter.emitter_type != "face":
        raise ValueError("Face receiver MIS requires a face emitter")
    if emitter.direction_distribution not in {"lambertian", "isotropic"}:
        raise ValueError(
            "receiver MIS supports lambertian and isotropic emitters"
        )
    generator = np.random.default_rng(seed ^ 0x5DEECE66D)
    remaining = emitter.ray_count
    while remaining > 0:
        count = min(batch_size, remaining)
        remaining -= count
        selected_slots = np.searchsorted(
            geometry.cumulative_weights,
            generator.random(count),
            side="left",
        )
        selected_slots = np.minimum(
            selected_slots,
            len(geometry.face_indices) - 1,
        )
        selected_triangles = geometry.triangle_vertices[selected_slots]
        selected_normals = geometry.normals[selected_slots]
        first_random = generator.random(count)
        second_random = generator.random(count)
        root = np.sqrt(first_random)
        points = (
            (1.0 - root)[:, None] * selected_triangles[:, 0, :]
            + (root * (1.0 - second_random))[:, None]
            * selected_triangles[:, 1, :]
            + (root * second_random)[:, None] * selected_triangles[:, 2, :]
        )
        origins = points + epsilon_mm * selected_normals
        directions, weights, directed_count = sample_receiver_mis_directions(
            generator,
            emitter,
            origins,
            selected_normals,
            receivers,
            receiver_fraction,
            epsilon_mm,
        )
        yield (
            np.ascontiguousarray(origins, dtype=np.float64),
            directions,
            np.ascontiguousarray(
                geometry.face_indices[selected_slots],
                dtype=np.int64,
            ),
            weights,
            directed_count,
        )


def sample_receiver_mis_directions(
    generator: np.random.Generator,
    emitter: EmitterSpec,
    origins: np.ndarray,
    emitter_normals: np.ndarray,
    receivers: ReceiverImportanceGeometry,
    receiver_fraction: float,
    epsilon_mm: float,
) -> tuple[np.ndarray, np.ndarray, int]:
    receiver_fraction = float(receiver_fraction)
    if not 0.0 < receiver_fraction < 1.0:
        raise ValueError("receiver_fraction must be within (0, 1)")
    origins = np.ascontiguousarray(origins, dtype=np.float64)
    normals = _normalize_rows(
        np.ascontiguousarray(emitter_normals, dtype=np.float64)
    )
    if origins.ndim != 2 or origins.shape[1] != 3:
        raise ValueError("origins must have shape (N, 3)")
    if normals.shape != origins.shape:
        raise ValueError("emitter_normals must match origins")
    count = len(origins)
    directions = _sample_direction_rows(generator, emitter, normals)
    directed_mask = generator.random(count) < receiver_fraction
    directed_rows = np.flatnonzero(directed_mask)
    if len(directed_rows):
        receiver_slots = generator.integers(
            0,
            receivers.receiver_count,
            size=len(directed_rows),
        )
        u_offsets = (
            generator.random(len(directed_rows)) * 2.0 - 1.0
        ) * receivers.half_widths[receiver_slots]
        v_offsets = (
            generator.random(len(directed_rows)) * 2.0 - 1.0
        ) * receivers.half_heights[receiver_slots]
        target_points = (
            receivers.centers[receiver_slots]
            + u_offsets[:, None] * receivers.u_axes[receiver_slots]
            + v_offsets[:, None] * receivers.v_axes[receiver_slots]
        )
        target_vectors = target_points - origins[directed_rows]
        target_distances = np.linalg.norm(target_vectors, axis=1)
        valid = target_distances > max(1e-12, float(epsilon_mm))
        if np.any(valid):
            directions[directed_rows[valid]] = (
                target_vectors[valid] / target_distances[valid, None]
            )
        if np.any(~valid):
            directed_mask[directed_rows[~valid]] = False

    source_pdf = source_direction_pdf(emitter, directions, normals)
    receiver_pdf = receiver_direction_pdf(
        origins,
        directions,
        receivers,
        epsilon_mm,
    )
    mixture_pdf = (
        (1.0 - receiver_fraction) * source_pdf
        + receiver_fraction * receiver_pdf
    )
    weights = np.zeros(count, dtype=np.float64)
    valid_pdf = mixture_pdf > 0.0
    weights[valid_pdf] = source_pdf[valid_pdf] / mixture_pdf[valid_pdf]
    if not np.all(np.isfinite(weights)) or np.any(weights < 0.0):
        raise ValueError("receiver MIS produced invalid weights")
    return (
        np.ascontiguousarray(_normalize_rows(directions), dtype=np.float64),
        np.ascontiguousarray(weights, dtype=np.float64),
        int(np.count_nonzero(directed_mask)),
    )


def source_direction_pdf(
    emitter: EmitterSpec,
    directions: np.ndarray,
    normals: np.ndarray,
) -> np.ndarray:
    count = len(directions)
    if emitter.direction_distribution == "isotropic":
        return np.full(count, 1.0 / (4.0 * math.pi), dtype=np.float64)
    if emitter.direction_distribution == "lambertian":
        cosine = np.sum(directions * normals, axis=1)
        return np.maximum(0.0, cosine) / math.pi
    raise ValueError(
        "receiver MIS source PDF supports lambertian and isotropic emitters"
    )


def receiver_direction_pdf(
    origins: np.ndarray,
    directions: np.ndarray,
    receivers: ReceiverImportanceGeometry,
    epsilon_mm: float,
) -> np.ndarray:
    ray_count = len(origins)
    density = np.zeros(ray_count, dtype=np.float64)
    receiver_probability = 1.0 / float(receivers.receiver_count)
    areas = receivers.areas_mm2
    for receiver_index in range(receivers.receiver_count):
        normal = receivers.normals[receiver_index]
        denominator = directions @ normal
        acceptance_cosine = -denominator
        numerator = (receivers.centers[receiver_index] - origins) @ normal
        valid_denominator = np.abs(denominator) >= 1e-12
        distances = np.full(ray_count, float("inf"), dtype=np.float64)
        distances[valid_denominator] = (
            numerator[valid_denominator] / denominator[valid_denominator]
        )
        valid = (
            valid_denominator
            & (distances > float(epsilon_mm))
            & (
                acceptance_cosine
                >= receivers.minimum_cosines[receiver_index]
            )
            & (acceptance_cosine > 0.0)
        )
        rows = np.flatnonzero(valid)
        if not len(rows):
            continue
        points = origins[rows] + directions[rows] * distances[rows, None]
        local = points - receivers.centers[receiver_index]
        u_values = local @ receivers.u_axes[receiver_index]
        v_values = local @ receivers.v_axes[receiver_index]
        inside = (
            (np.abs(u_values) <= receivers.half_widths[receiver_index] + 1e-9)
            & (
                np.abs(v_values)
                <= receivers.half_heights[receiver_index] + 1e-9
            )
        )
        rows = rows[inside]
        if not len(rows):
            continue
        density[rows] += (
            receiver_probability
            * distances[rows] ** 2
            / (areas[receiver_index] * acceptance_cosine[rows])
        )
    return density


def _sample_direction_batch(
    generator: np.random.Generator,
    emitter: EmitterSpec,
    normal: np.ndarray,
    basis_u: np.ndarray,
    basis_v: np.ndarray,
    count: int,
) -> np.ndarray:
    if emitter.direction_distribution == "isotropic":
        z_values = generator.uniform(-1.0, 1.0, count)
        phi_values = generator.uniform(0.0, 2.0 * math.pi, count)
        radial = np.sqrt(np.maximum(0.0, 1.0 - z_values * z_values))
        return np.column_stack(
            (
                radial * np.cos(phi_values),
                radial * np.sin(phi_values),
                z_values,
            )
        )
    if emitter.direction_distribution == "gaussian":
        sigma_rad = math.radians(max(1e-6, emitter.gaussian_sigma_deg))
        theta_values = np.minimum(
            np.abs(generator.normal(0.0, sigma_rad, count)),
            math.pi * 0.5,
        )
        phi_values = generator.uniform(0.0, 2.0 * math.pi, count)
        sin_theta = np.sin(theta_values)
        directions = (
            sin_theta[:, None] * np.cos(phi_values)[:, None] * basis_u[None, :]
            + sin_theta[:, None] * np.sin(phi_values)[:, None] * basis_v[None, :]
            + np.cos(theta_values)[:, None] * normal[None, :]
        )
        return _normalize_rows(directions)
    radial_samples = generator.random(count)
    azimuth_samples = generator.random(count)
    radius = np.sqrt(radial_samples)
    phi_values = 2.0 * math.pi * azimuth_samples
    x_values = radius * np.cos(phi_values)
    y_values = radius * np.sin(phi_values)
    z_values = np.sqrt(np.maximum(0.0, 1.0 - radial_samples))
    directions = (
        x_values[:, None] * basis_u[None, :]
        + y_values[:, None] * basis_v[None, :]
        + z_values[:, None] * normal[None, :]
    )
    return _normalize_rows(directions)


def _sample_direction_rows(
    generator: np.random.Generator,
    emitter: EmitterSpec,
    normals: np.ndarray,
) -> np.ndarray:
    count = len(normals)
    if emitter.direction_distribution == "isotropic":
        z_values = generator.uniform(-1.0, 1.0, count)
        phi_values = generator.uniform(0.0, 2.0 * math.pi, count)
        radial = np.sqrt(np.maximum(0.0, 1.0 - z_values * z_values))
        return np.column_stack(
            (
                radial * np.cos(phi_values),
                radial * np.sin(phi_values),
                z_values,
            )
        )

    basis_u, basis_v = _orthonormal_basis_rows(normals)
    if emitter.direction_distribution == "gaussian":
        sigma_rad = math.radians(max(1e-6, emitter.gaussian_sigma_deg))
        theta_values = np.minimum(
            np.abs(generator.normal(0.0, sigma_rad, count)),
            math.pi * 0.5,
        )
        phi_values = generator.uniform(0.0, 2.0 * math.pi, count)
        sin_theta = np.sin(theta_values)
        directions = (
            sin_theta[:, None] * np.cos(phi_values)[:, None] * basis_u
            + sin_theta[:, None] * np.sin(phi_values)[:, None] * basis_v
            + np.cos(theta_values)[:, None] * normals
        )
        return _normalize_rows(directions)

    radial_samples = generator.random(count)
    azimuth_samples = generator.random(count)
    radius = np.sqrt(radial_samples)
    phi_values = 2.0 * math.pi * azimuth_samples
    x_values = radius * np.cos(phi_values)
    y_values = radius * np.sin(phi_values)
    z_values = np.sqrt(np.maximum(0.0, 1.0 - radial_samples))
    directions = (
        x_values[:, None] * basis_u
        + y_values[:, None] * basis_v
        + z_values[:, None] * normals
    )
    return _normalize_rows(directions)


def _orthonormal_basis(normal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    helper = np.array((0.0, 0.0, 1.0), dtype=np.float64)
    if abs(float(np.dot(normal, helper))) > 0.95:
        helper = np.array((0.0, 1.0, 0.0), dtype=np.float64)
    u_axis = _normalize(np.cross(helper, normal))
    v_axis = _normalize(np.cross(normal, u_axis))
    return u_axis, v_axis


def _orthonormal_basis_rows(
    normals: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    helpers = np.zeros_like(normals)
    helpers[:, 2] = 1.0
    near_z = np.abs(normals[:, 2]) > 0.95
    helpers[near_z] = np.array((0.0, 1.0, 0.0), dtype=np.float64)
    u_axes = _normalize_rows(np.cross(helpers, normals))
    v_axes = _normalize_rows(np.cross(normals, u_axes))
    return u_axes, v_axes


def _normalize(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        raise ValueError("Fast ray sampling received a zero-length vector")
    return vector / length


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    lengths = np.linalg.norm(vectors, axis=1)
    lengths = np.maximum(lengths, 1e-18)
    return vectors / lengths[:, None]
