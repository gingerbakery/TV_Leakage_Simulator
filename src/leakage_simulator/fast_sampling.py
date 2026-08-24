from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional, Tuple
import math

import numpy as np

from .geometry import TriangleMesh
from .types import EmitterSpec


RayBatch = Tuple[np.ndarray, np.ndarray]
FaceRayBatch = Tuple[np.ndarray, np.ndarray, np.ndarray]


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
