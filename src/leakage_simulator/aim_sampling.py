from __future__ import annotations

import math
import random

import numpy as np

from .geometry import TriangleMesh
from .types import EmitterAimSpec, EmitterSpec, Vec3


AIM_SAMPLING_CONTRACT = "target_only_uniform_area_v1"
AIM_SPHERE_SAMPLING_CONTRACT = "angular_region_uniform_solid_angle_v1"


def aim_sphere_rotation(aim: EmitterAimSpec) -> tuple[Vec3, Vec3, Vec3]:
    alpha = math.radians(aim.sphere_alpha_deg % 360.0)
    beta = math.radians(aim.sphere_beta_deg % 360.0)
    sin_alpha, cos_alpha = math.sin(alpha), math.cos(alpha)
    sin_beta, cos_beta = math.sin(beta), math.cos(beta)
    return (
        (cos_beta, sin_beta * sin_alpha, sin_beta * cos_alpha),
        (0.0, cos_alpha, -sin_alpha),
        (-sin_beta, cos_beta * sin_alpha, cos_beta * cos_alpha),
    )


def _sphere_local_direction(first, second, aim: EmitterAimSpec):
    upper = math.sin(math.radians(aim.sphere_upper_deg) * 0.5) ** 2
    lower = math.sin(math.radians(aim.sphere_lower_deg) * 0.5) ** 2
    half_sine_squared = upper + (lower - upper) * first
    cosine = 1.0 - 2.0 * half_sine_squared
    sine = np.sqrt(np.maximum(0.0, 4.0 * half_sine_squared * (1.0 - half_sine_squared)))
    azimuth = 2.0 * math.pi * second
    return sine * np.cos(azimuth), sine * np.sin(azimuth), cosine


def validate_emitter_aim(
    emitter: EmitterSpec,
    mesh: TriangleMesh,
    epsilon_mm: float,
) -> None:
    aim = emitter.aim
    if aim is None or not aim.enabled:
        return
    if aim.mode == "sphere":
        return
    if emitter.emitter_type == "face":
        triangles = [
            mesh.face_vertices(face_index)
            for face_index in emitter.face_indices
            if 0 <= face_index < len(mesh.faces)
        ]
    elif emitter.surface_construction == "polygon_auto":
        points = emitter.polygon_vertices
        triangles = [
            (points[0], points[index], points[index + 1])
            for index in range(1, len(points) - 1)
        ]
    else:
        center = np.asarray(emitter.center, dtype=np.float64)
        axis_u = np.asarray(emitter.u_axis, dtype=np.float64)
        axis_v = np.asarray(emitter.v_axis, dtype=np.float64)
        axis_u /= np.linalg.norm(axis_u)
        axis_v -= axis_u * np.dot(axis_v, axis_u)
        axis_v /= np.linalg.norm(axis_v)
        points = [
            center + sign_u * emitter.width_mm * 0.5 * axis_u
            + sign_v * emitter.height_mm * 0.5 * axis_v
            for sign_u, sign_v in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
        triangles = [(points[0], points[1], points[2]), (points[0], points[2], points[3])]
    if not triangles:
        raise ValueError(f"{emitter.emitter_id}: Aim emitter has no valid surface")
    normal = np.cross(aim.u_axis, aim.v_axis)
    normal = normal / np.linalg.norm(normal)
    target_frame = np.column_stack((aim.u_axis, aim.v_axis, normal))
    local_triangles = (np.asarray(triangles, dtype=np.float64) - aim.center) @ target_frame
    margin = max(1e-9, 2.0 * epsilon_mm)
    candidates = (local_triangles[:, :, 2].min(axis=1) <= margin) & (
        local_triangles[:, :, 2].max(axis=1) >= -margin
    )
    for triangle in local_triangles[candidates]:
        polygon = _clip_aim_polygon(list(triangle), 2, 1, margin)
        polygon = _clip_aim_polygon(polygon, 2, -1, margin)
        if aim.shape == "rectangle":
            for axis, half_size in ((0, aim.width_mm / 2), (1, aim.height_mm / 2)):
                for sign in (-1, 1):
                    polygon = _clip_aim_polygon(polygon, axis, sign, half_size + margin)
            overlaps = bool(polygon)
        else:
            overlaps = _aim_circle_overlaps(polygon, aim.radius_mm + margin)
        if overlaps:
            raise ValueError(
                f"{emitter.emitter_id}: Target area overlaps or touches the emitting surface "
                f"within the {margin:g} mm numerical clearance. "
                "Move, resize or tilt the Target to separate the actual areas."
            )


def _clip_aim_polygon(
    polygon: list[np.ndarray], axis: int, sign: int, limit: float,
) -> list[np.ndarray]:
    if not polygon:
        return []
    clipped = []
    previous = polygon[-1]
    previous_distance = sign * previous[axis] - limit
    for current in polygon:
        current_distance = sign * current[axis] - limit
        if (previous_distance <= 0) != (current_distance <= 0):
            fraction = previous_distance / (previous_distance - current_distance)
            clipped.append(previous + fraction * (current - previous))
        if current_distance <= 0:
            clipped.append(current)
        previous = current
        previous_distance = current_distance
    return clipped


def _aim_circle_overlaps(polygon: list[np.ndarray], radius: float) -> bool:
    inside = False
    for index, current in enumerate(polygon):
        start = polygon[index - 1][:2]
        end = current[:2]
        edge = end - start
        length_squared = float(np.dot(edge, edge))
        fraction = float(np.clip(-np.dot(start, edge) / length_squared, 0, 1)) if length_squared else 0.0
        nearest = start + fraction * edge
        if np.dot(nearest, nearest) <= radius * radius:
            return True
        if (start[1] > 0) != (end[1] > 0):
            crossing_x = start[0] - start[1] * edge[0] / edge[1]
            if crossing_x > 0:
                inside = not inside
    return inside


def sample_aim_ray_batch(
    generator: np.random.Generator,
    points: np.ndarray,
    aim: EmitterAimSpec,
    epsilon_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(points)
    first = generator.random(count)
    second = generator.random(count)
    if aim.mode == "sphere":
        local = np.column_stack(_sphere_local_direction(first, second, aim))
        directions = local @ np.asarray(aim_sphere_rotation(aim), dtype=np.float64).T
        return (
            np.ascontiguousarray(points + epsilon_mm * directions, dtype=np.float64),
            np.ascontiguousarray(directions, dtype=np.float64),
        )
    if aim.shape == "circle":
        radius = aim.radius_mm * np.sqrt(first)
        angle = 2.0 * math.pi * second
        offsets_u = radius * np.cos(angle)
        offsets_v = radius * np.sin(angle)
    else:
        offsets_u = (first - 0.5) * aim.width_mm
        offsets_v = (second - 0.5) * aim.height_mm
    targets = (
        np.asarray(aim.center, dtype=np.float64)
        + offsets_u[:, None] * np.asarray(aim.u_axis, dtype=np.float64)
        + offsets_v[:, None] * np.asarray(aim.v_axis, dtype=np.float64)
    )
    vectors = targets - points
    distances = np.linalg.norm(vectors, axis=1)
    if np.any(~np.isfinite(distances)) or np.any(distances <= max(1e-12, epsilon_mm)):
        raise ValueError("Aim target is too close to the emitter")
    directions = vectors / distances[:, None]
    return (
        np.ascontiguousarray(points + epsilon_mm * directions, dtype=np.float64),
        np.ascontiguousarray(directions, dtype=np.float64),
    )


def sample_aim_ray(
    generator: random.Random,
    point: Vec3,
    aim: EmitterAimSpec,
    epsilon_mm: float,
) -> tuple[Vec3, Vec3]:
    first = generator.random()
    second = generator.random()
    if aim.mode == "sphere":
        local = _sphere_local_direction(first, second, aim)
        direction = tuple(float(sum(row[axis] * local[axis] for axis in range(3))) for row in aim_sphere_rotation(aim))
        origin = tuple(point[axis] + epsilon_mm * direction[axis] for axis in range(3))
        return origin, direction
    if aim.shape == "circle":
        radius = aim.radius_mm * math.sqrt(first)
        angle = 2.0 * math.pi * second
        offset_u = radius * math.cos(angle)
        offset_v = radius * math.sin(angle)
    else:
        offset_u = (first - 0.5) * aim.width_mm
        offset_v = (second - 0.5) * aim.height_mm
    target = tuple(
        aim.center[axis] + offset_u * aim.u_axis[axis] + offset_v * aim.v_axis[axis]
        for axis in range(3)
    )
    vector = tuple(target[axis] - point[axis] for axis in range(3))
    distance = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(distance) or distance <= max(1e-12, epsilon_mm):
        raise ValueError("Aim target is too close to the emitter")
    direction = tuple(value / distance for value in vector)
    origin = tuple(point[axis] + epsilon_mm * direction[axis] for axis in range(3))
    return origin, direction
