from __future__ import annotations

import math
import random

import numpy as np

from .geometry import TriangleMesh
from .types import EmitterAimSpec, EmitterSpec, Vec3


AIM_SAMPLING_CONTRACT = "target_only_uniform_area_v1"


def validate_emitter_aim(
    emitter: EmitterSpec,
    mesh: TriangleMesh,
    epsilon_mm: float,
) -> None:
    aim = emitter.aim
    if aim is None or not aim.enabled:
        return
    if emitter.emitter_type == "face":
        points = [
            point
            for face_index in emitter.face_indices
            if 0 <= face_index < len(mesh.faces)
            for point in mesh.face_vertices(face_index)
        ]
    elif emitter.surface_construction == "polygon_auto":
        points = emitter.polygon_vertices
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
            for sign_u in (-1, 1)
            for sign_v in (-1, 1)
        ]
    if not points:
        raise ValueError(f"{emitter.emitter_id}: Aim emitter has no valid surface")
    normal = np.cross(aim.u_axis, aim.v_axis)
    distances = (np.asarray(points, dtype=np.float64) - aim.center) @ normal
    margin = max(1e-9, 2.0 * epsilon_mm)
    if not (np.all(distances > margin) or np.all(distances < -margin)):
        raise ValueError(
            f"{emitter.emitter_id}: Target plane must not intersect the emitter. "
            "Change Target position or tilt."
        )


def sample_aim_ray_batch(
    generator: np.random.Generator,
    points: np.ndarray,
    aim: EmitterAimSpec,
    epsilon_mm: float,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(points)
    first = generator.random(count)
    second = generator.random(count)
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
