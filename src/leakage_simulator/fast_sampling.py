from __future__ import annotations

from typing import Iterator, Tuple
import math

import numpy as np

from .types import EmitterSpec


RayBatch = Tuple[np.ndarray, np.ndarray]


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


def _orthonormal_basis(normal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    helper = np.array((0.0, 0.0, 1.0), dtype=np.float64)
    if abs(float(np.dot(normal, helper))) > 0.95:
        helper = np.array((0.0, 1.0, 0.0), dtype=np.float64)
    u_axis = _normalize(np.cross(helper, normal))
    v_axis = _normalize(np.cross(normal, u_axis))
    return u_axis, v_axis


def _normalize(vector: np.ndarray) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        raise ValueError("Fast ray sampling received a zero-length vector")
    return vector / length


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    lengths = np.linalg.norm(vectors, axis=1)
    lengths = np.maximum(lengths, 1e-18)
    return vectors / lengths[:, None]
