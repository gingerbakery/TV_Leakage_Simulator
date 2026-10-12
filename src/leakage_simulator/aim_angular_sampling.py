"""Physical angular laws restricted to an Aim region.

Area samples are drawn jointly in source position and outgoing direction, then
conditioned on hitting the Target.  Rejected source positions are redrawn too:
normalizing independently at each source position would change the radiance.
The caller assigns equal flux to the accepted rays, so input power denotes the
power within the complete Aim region, rather than an unrestricted hemisphere.
"""
from __future__ import annotations

from functools import lru_cache
import math

import numpy as np


@lru_cache(maxsize=128)
def _gaussian_cdf(sigma_deg: float, lower_deg: float, upper_deg: float):
    """CDF of exp(-theta²/2sigma²) per unit solid angle, without tail underflow.

    In q=(theta²-lower²)/(2sigma²), the density is exp(-q)*sinc(theta).
    A 40-unit q tail has relative exponential mass below 4.3e-18; sinc(theta)
    is decreasing on [0, pi], so its omission is smaller still.
    """
    sigma = math.radians(sigma_deg)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("Aim Gaussian sigma must be finite and positive")
    lower, upper = math.radians(lower_deg), math.radians(upper_deg)
    variance = sigma * sigma
    if variance == 0.0 or not math.isfinite(variance):
        raise ValueError("Aim Gaussian sigma is outside the numerical range")
    span = (upper - lower) * (upper + lower) / (2.0 * variance)
    q = np.linspace(0.0, min(40.0, span), 8193)
    theta = np.sqrt(lower * lower + 2.0 * variance * q)
    density = np.exp(-q) * np.sinc(theta / math.pi)
    increments = (density[:-1] + density[1:]) * 0.5 * np.diff(q)
    cdf = np.concatenate(([0.0], np.cumsum(increments)))
    mass = float(cdf[-1])
    if not math.isfinite(mass) or mass <= 0.0:
        raise ValueError("Aim Gaussian interval has no numerically resolvable power")
    cdf /= mass
    cdf[-1] = 1.0
    # Roundoff may make the negligible tail flat.  np.interp requires an
    # increasing abscissa; preserving the first occurrence also avoids jumps.
    keep = np.concatenate(([True], np.diff(cdf) > 0.0))
    return q[keep], cdf[keep], variance * mass


def _gaussian_theta(first, sigma_deg, lower_deg, upper_deg):
    q, cdf, _ = _gaussian_cdf(float(sigma_deg), float(lower_deg), float(upper_deg))
    sampled_q = np.interp(first, cdf, q)
    lower = math.radians(lower_deg)
    sigma = math.radians(sigma_deg)
    return np.sqrt(lower * lower + 2.0 * sigma * sigma * sampled_q)


def sample_sphere_angles(first, second, aim):
    """Return local x/y/z under the law about the Aim Sphere's +Z axis."""
    lower_deg, upper_deg = aim.sphere_upper_deg, aim.sphere_lower_deg
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    if lower_deg == upper_deg == 0.0:
        cosine = np.ones_like(first)
        sine = np.zeros_like(first)
    elif aim.distribution == "lambertian":
        if lower_deg >= 90.0:
            raise ValueError("Lambertian Aim Sphere has no power beyond its forward hemisphere")
        low_angle = math.radians(lower_deg)
        high_angle = math.radians(min(upper_deg, 90.0))
        low = math.sin(low_angle) ** 2
        high = math.sin(high_angle) ** 2
        sine_squared = (1.0 - first) * low + first * high
        # Compute both squares directly: 1-sin²(theta) loses all precision for
        # very thin angular bands next to 90 degrees.
        cosine_squared = (1.0 - first) * math.cos(low_angle) ** 2 + first * math.cos(high_angle) ** 2
        sine = np.sqrt(np.clip(sine_squared, 0.0, 1.0))
        cosine = np.sqrt(np.clip(cosine_squared, 0.0, 1.0))
    elif aim.distribution == "gaussian":
        theta = _gaussian_theta(first, aim.gaussian_sigma_deg, lower_deg, upper_deg)
        cosine, sine = np.cos(theta), np.sin(theta)
    else:
        raise ValueError("Angular Aim sampler requires Lambertian or Gaussian")
    phi = 2.0 * math.pi * second
    return sine * np.cos(phi), sine * np.sin(phi), cosine


def _angular_directions(generator, normals, distribution, sigma_deg):
    count = len(normals)
    first, second = generator.random(count), generator.random(count)
    if distribution == "lambertian":
        sine = np.sqrt(first)
        cosine = np.sqrt(1.0 - first)
    else:
        theta = _gaussian_theta(first, sigma_deg, 0.0, 90.0)
        sine, cosine = np.sin(theta), np.cos(theta)
    phi = 2.0 * math.pi * second
    reference = np.zeros_like(normals)
    reference[:, 1] = 1.0
    vertical = np.abs(normals[:, 1]) > 0.9
    reference[vertical] = (1.0, 0.0, 0.0)
    u = np.cross(reference, normals)
    u /= np.linalg.norm(u, axis=1)[:, None]
    v = np.cross(normals, u)
    return (sine * np.cos(phi))[:, None] * u + (sine * np.sin(phi))[:, None] * v + cosine[:, None] * normals


def _target_points(generator, count, aim):
    first, second = generator.random(count), generator.random(count)
    if aim.shape == "circle":
        radius = aim.radius_mm * np.sqrt(first)
        phi = 2.0 * math.pi * second
        u, v = radius * np.cos(phi), radius * np.sin(phi)
    else:
        u, v = (first - 0.5) * aim.width_mm, (second - 0.5) * aim.height_mm
    return np.asarray(aim.center) + u[:, None] * np.asarray(aim.u_axis) + v[:, None] * np.asarray(aim.v_axis)


def _target_proposal_bound(aim, source_bounds, maximum_pdf):
    if source_bounds is None:
        return math.inf
    source_min, source_max = (np.asarray(value, dtype=np.float64) for value in source_bounds)
    if source_min.shape != (3,) or source_max.shape != (3,) or np.any(source_min > source_max):
        raise ValueError("Aim source_bounds must be (minimum_xyz, maximum_xyz)")
    u, v = np.asarray(aim.u_axis), np.asarray(aim.v_axis)
    if aim.shape == "circle":
        extent = aim.radius_mm * np.sqrt(u * u + v * v)
        area = math.pi * aim.radius_mm ** 2
    else:
        extent = np.abs(u) * aim.width_mm / 2.0 + np.abs(v) * aim.height_mm / 2.0
        area = aim.width_mm * aim.height_mm
    center = np.asarray(aim.center)
    separation = np.maximum(np.maximum(source_min - (center + extent), (center - extent) - source_max), 0.0)
    distance_squared = float(np.dot(separation, separation))
    if distance_squared <= 0.0:
        return math.inf
    return area * maximum_pdf / distance_squared


def sample_area_angular_batch(generator, count, aim, epsilon_mm, source_sampler,
                              *, two_sided=False, source_bounds=None):
    """Equal-power iid joint source/direction samples conditioned on the Target.

    ``source_sampler(n)`` returns (positions[n,3], outward normals[n,3], face IDs[n]).
    It must sample emitting area uniformly, and bounds, when supplied, must cover
    every source point. ``source_bounds`` is ``(minimum_xyz, maximum_xyz)``.
    Both proposals below use one global normalization.  Neither fixes a source
    position while repeatedly sampling directions, which would be a different law.
    """
    count = int(count)
    if count < 0:
        raise ValueError("Aim ray count cannot be negative")
    if aim.distribution not in {"lambertian", "gaussian"}:
        raise ValueError("Angular Aim sampler requires Lambertian or Gaussian")
    if aim.distribution == "lambertian":
        maximum_pdf = 1.0 / math.pi
    else:
        _, _, radial_mass = _gaussian_cdf(float(aim.gaussian_sigma_deg), 0.0, 90.0)
        maximum_pdf = 1.0 / (2.0 * math.pi * radial_mass)
    if two_sided:
        maximum_pdf *= 0.5
    proposal_bound = _target_proposal_bound(aim, source_bounds, maximum_pdf)
    use_target_proposal = proposal_bound < 1.0
    target_area = math.pi * aim.radius_mm ** 2 if aim.shape == "circle" else aim.width_mm * aim.height_mm
    target_normal = np.cross(aim.u_axis, aim.v_axis)
    target_normal = target_normal / np.linalg.norm(target_normal)
    out_points, out_directions = np.empty((count, 3)), np.empty((count, 3))
    out_faces = np.empty(count, dtype=np.int64)
    # Fixed finite budget prevents hung runs for backwards sources or regions
    # with negligible angular power.  Target proposals handle small/far targets.
    budget = min(50_000_000, max(262_144, count * 4096))
    proposed = written = accepted_total = 0
    while written < count and proposed < budget:
        acceptance = accepted_total / proposed if proposed else 0.25
        proposal_count = min(262_144, budget - proposed,
                             max(1024, int((count - written) / max(acceptance, 1e-4) * 1.05)))
        points, normals, faces = source_sampler(proposal_count)
        points, normals = np.asarray(points, dtype=np.float64), np.asarray(normals, dtype=np.float64)
        faces = np.asarray(faces, dtype=np.int64)
        if points.shape != (proposal_count, 3) or normals.shape != points.shape or faces.shape != (proposal_count,):
            raise ValueError("Aim source sampler returned invalid shapes")
        lengths = np.linalg.norm(normals, axis=1)
        if not np.all(np.isfinite(points)) or not np.all(np.isfinite(lengths)) or np.any(lengths <= 0.0):
            raise ValueError("Aim source sampler returned invalid points or normals")
        normals = normals / lengths[:, None]
        if use_target_proposal:
            vectors = _target_points(generator, proposal_count, aim) - points
            distance_squared = np.einsum("ij,ij->i", vectors, vectors)
            if np.any(distance_squared <= max(1e-24, epsilon_mm * epsilon_mm)):
                raise ValueError("Aim target is too close to the emitter")
            directions = vectors / np.sqrt(distance_squared)[:, None]
            cosine_source = np.einsum("ij,ij->i", directions, normals)
            if two_sided:
                cosine_source = np.abs(cosine_source)
            if aim.distribution == "lambertian":
                pdf = np.maximum(cosine_source, 0.0) * maximum_pdf
            else:
                theta = np.arccos(np.clip(cosine_source, -1.0, 1.0))
                pdf = np.exp(-0.5 * (theta / math.radians(aim.gaussian_sigma_deg)) ** 2) * maximum_pdf
                pdf[cosine_source < 0.0] = 0.0
            probability = target_area * pdf * np.abs(directions @ target_normal) / distance_squared / proposal_bound
            if np.any(probability > 1.0 + 1e-10):
                raise ValueError("Aim source bounds do not cover the sampled source geometry")
            keep = generator.random(proposal_count) < np.minimum(probability, 1.0)
        else:
            if two_sided:
                normals *= np.where(generator.random(proposal_count) < 0.5, -1.0, 1.0)[:, None]
            directions = _angular_directions(generator, normals, aim.distribution, aim.gaussian_sigma_deg)
            denominator = directions @ target_normal
            distance = np.divide((np.asarray(aim.center) - points) @ target_normal, denominator,
                                 out=np.full(proposal_count, np.inf), where=np.abs(denominator) > 1e-15)
            valid_distance = np.isfinite(distance) & (distance > max(1e-12, epsilon_mm))
            intersections = points + np.where(valid_distance, distance, 0.0)[:, None] * directions
            relative = intersections - np.asarray(aim.center)
            u, v = relative @ np.asarray(aim.u_axis), relative @ np.asarray(aim.v_axis)
            if aim.shape == "circle":
                inside = u * u + v * v <= aim.radius_mm ** 2
            else:
                inside = (np.abs(u) <= aim.width_mm / 2.0) & (np.abs(v) <= aim.height_mm / 2.0)
            keep = valid_distance & inside
        slots = np.flatnonzero(keep)
        proposed += proposal_count
        accepted_total += len(slots)
        slots = slots[:count - written]
        end = written + len(slots)
        out_points[written:end] = points[slots] + epsilon_mm * directions[slots]
        out_directions[written:end] = directions[slots]
        out_faces[written:end] = faces[slots]
        written = end
    if written < count:
        raise ValueError(
            f"Aim {aim.distribution} accepted only {written}/{count} rays after {proposed} proposals. "
            "The Target has no or too little power in the selected emission direction. "
            "Check source Forward/Reverse/Both, Target position/size, and Gaussian sigma."
        )
    return out_points, out_directions, out_faces
