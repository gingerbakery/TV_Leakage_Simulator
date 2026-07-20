from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import math
import random

from .types import OpticalProfile, Vec3


REFLECTION_LOBES = ("specular", "lambertian", "gaussian")
_TAU = 2.0 * math.pi


@dataclass(frozen=True, slots=True)
class ReflectionSample:
    direction: Vec3
    lobe: str


def ideal_specular_direction(incoming: Vec3, normal: Vec3) -> Vec3:
    incoming_direction = _normalize(incoming)
    surface_normal = _oriented_surface_normal(incoming_direction, normal)
    return _ideal_specular_from_unit(incoming_direction, surface_normal)


def sample_reflection_direction(
    rng: random.Random,
    incoming: Vec3,
    normal: Vec3,
    profile: OpticalProfile,
) -> Optional[ReflectionSample]:
    if profile.scatter_model == "none" or profile.reflectance <= 0.0:
        return None

    incoming_direction = _normalize(incoming)
    surface_normal = _oriented_surface_normal(incoming_direction, normal)
    if profile.scatter_model == "specular":
        return ReflectionSample(
            direction=_ideal_specular_from_unit(incoming_direction, surface_normal),
            lobe="specular",
        )
    if profile.scatter_model == "lambertian":
        return ReflectionSample(
            direction=_sample_cosine_weighted_hemisphere_unit(
                rng,
                surface_normal,
            ),
            lobe="lambertian",
        )

    specular_axis = _ideal_specular_from_unit(incoming_direction, surface_normal)
    if profile.scatter_model == "gaussian":
        return ReflectionSample(
            direction=_sample_gaussian_lobe_unit(
                rng,
                specular_axis,
                surface_normal,
                profile.gaussian_sigma_deg,
            ),
            lobe="gaussian",
        )

    if rng.random() < profile.specular_ratio:
        if profile.gaussian_sigma_deg <= 0.01:
            return ReflectionSample(direction=specular_axis, lobe="specular")
        return ReflectionSample(
            direction=_sample_gaussian_lobe_unit(
                rng,
                specular_axis,
                surface_normal,
                profile.gaussian_sigma_deg,
            ),
            lobe="gaussian",
        )
    return ReflectionSample(
        direction=_sample_cosine_weighted_hemisphere_unit(
            rng,
            surface_normal,
        ),
        lobe="lambertian",
    )


def sample_cosine_weighted_hemisphere(rng: random.Random, normal: Vec3) -> Vec3:
    return _sample_cosine_weighted_hemisphere_unit(rng, _normalize(normal))


def _sample_cosine_weighted_hemisphere_unit(
    rng: random.Random,
    normal: Vec3,
) -> Vec3:
    u_axis, v_axis, w_axis = _orthonormal_basis_unit(normal)
    radial_sample = rng.random()
    azimuth_sample = rng.random()
    radius = math.sqrt(radial_sample)
    phi = _TAU * azimuth_sample
    x = radius * math.cos(phi)
    y = radius * math.sin(phi)
    z = math.sqrt(max(0.0, 1.0 - radial_sample))
    return (
        u_axis[0] * x + v_axis[0] * y + w_axis[0] * z,
        u_axis[1] * x + v_axis[1] * y + w_axis[1] * z,
        u_axis[2] * x + v_axis[2] * y + w_axis[2] * z,
    )


def sample_gaussian_lobe(
    rng: random.Random,
    axis: Vec3,
    surface_normal: Vec3,
    sigma_deg: float,
) -> Vec3:
    lobe_axis = _normalize(axis)
    normal = _normalize(surface_normal)
    return _sample_gaussian_lobe_unit(
        rng,
        lobe_axis,
        normal,
        sigma_deg,
    )


def _sample_gaussian_lobe_unit(
    rng: random.Random,
    lobe_axis: Vec3,
    normal: Vec3,
    sigma_deg: float,
) -> Vec3:
    u_axis, v_axis, _ = _orthonormal_basis_unit(lobe_axis)
    sigma_rad = math.radians(max(1e-6, sigma_deg))
    for _ in range(32):
        radial_random = max(1e-12, 1.0 - rng.random())
        theta = sigma_rad * math.sqrt(-2.0 * math.log(radial_random))
        if theta >= math.pi * 0.5:
            continue
        phi = _TAU * rng.random()
        cosine_theta = math.cos(theta)
        sine_theta = math.sin(theta)
        u_scale = sine_theta * math.cos(phi)
        v_scale = sine_theta * math.sin(phi)
        direction = (
            lobe_axis[0] * cosine_theta + u_axis[0] * u_scale + v_axis[0] * v_scale,
            lobe_axis[1] * cosine_theta + u_axis[1] * u_scale + v_axis[1] * v_scale,
            lobe_axis[2] * cosine_theta + u_axis[2] * u_scale + v_axis[2] * v_scale,
        )
        if (
            direction[0] * normal[0]
            + direction[1] * normal[1]
            + direction[2] * normal[2]
        ) > 1e-9:
            return direction
    return lobe_axis


def orthonormal_basis(normal: Vec3) -> Tuple[Vec3, Vec3, Vec3]:
    w_axis = _normalize(normal)
    return _orthonormal_basis_unit(w_axis)


def _orthonormal_basis_unit(w_axis: Vec3) -> Tuple[Vec3, Vec3, Vec3]:
    if abs(w_axis[2]) > 0.95:
        u_axis = _normalize((w_axis[2], 0.0, -w_axis[0]))
    else:
        u_axis = _normalize((-w_axis[1], w_axis[0], 0.0))
    v_axis = (
        w_axis[1] * u_axis[2] - w_axis[2] * u_axis[1],
        w_axis[2] * u_axis[0] - w_axis[0] * u_axis[2],
        w_axis[0] * u_axis[1] - w_axis[1] * u_axis[0],
    )
    return u_axis, v_axis, w_axis


def _oriented_surface_normal(incoming: Vec3, normal: Vec3) -> Vec3:
    surface_normal = _normalize(normal)
    if (
        incoming[0] * surface_normal[0]
        + incoming[1] * surface_normal[1]
        + incoming[2] * surface_normal[2]
    ) > 0.0:
        surface_normal = (
            -surface_normal[0],
            -surface_normal[1],
            -surface_normal[2],
        )
    return surface_normal


def _ideal_specular_from_unit(
    incoming_direction: Vec3,
    surface_normal: Vec3,
) -> Vec3:
    incidence = (
        incoming_direction[0] * surface_normal[0]
        + incoming_direction[1] * surface_normal[1]
        + incoming_direction[2] * surface_normal[2]
    )
    reflected = (
        incoming_direction[0] - 2.0 * incidence * surface_normal[0],
        incoming_direction[1] - 2.0 * incidence * surface_normal[1],
        incoming_direction[2] - 2.0 * incidence * surface_normal[2],
    )
    return reflected


def _normalize(vector: Vec3) -> Vec3:
    x_value, y_value, z_value = vector
    magnitude_squared = x_value * x_value + y_value * y_value + z_value * z_value
    if magnitude_squared <= 1e-30:
        return (0.0, 0.0, 0.0)
    inverse_magnitude = 1.0 / math.sqrt(magnitude_squared)
    return (
        x_value * inverse_magnitude,
        y_value * inverse_magnitude,
        z_value * inverse_magnitude,
    )
