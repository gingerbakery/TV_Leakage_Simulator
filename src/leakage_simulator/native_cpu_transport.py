from __future__ import annotations

from dataclasses import dataclass, replace
import importlib
import os
import threading
import time

import numpy as np

from . import native_cpu_counter_wavefront as counter
from . import native_cpu_intersection as intersection
from .cpu_parallel import run_cpu_ranges
from .wavefront_event_tape import (
    EVENT_TAPE_CONTRACT,
    MAX_BATCH_EVENT_SLOTS,
    PATH_PAYLOAD_FULL,
    PATH_PAYLOAD_OMITTED,
    PrimaryMajorEventTape,
    STATUS_EMITTED,
    TERMINAL_BLOCKED,
    TERMINAL_ESCAPED,
    TERMINAL_RECEIVER,
)


CONTRACT = 'strict_fp64_fused_cpu_transport_v1'
_LOCK = threading.Lock()
_KERNEL = None
_COMPACT_KERNEL = None
_PACK_KERNEL = None


class NativeCpuEventBudgetExceeded(ValueError):
    def __init__(self, capacity, execute_sec):
        super().__init__('native CPU transport actual-event capacity exceeded')
        self.capacity = capacity
        self.execute_sec = execute_sec


@dataclass(frozen=True, slots=True)
class NativeCpuTransportExecution:
    tape: PrimaryMajorEventTape
    execute_sec: float
    jit_compile_sec: float
    validation_sec: float
    materialize_sec: float
    parallel_workers: int
    stochastic_primary_count: int
    importance_flags: np.ndarray
    importance_weights: np.ndarray
    numba_version: str
    scene_build_sec: float
    record_layout: str
    event_pool_capacity: int


def _make_kernel(intersect_kernel, plan_kernel, *, compact=False):
    numba = importlib.import_module('numba')
    intersect_kernel = numba.njit(
        nogil=True, fastmath=False, inline='always',
    )(intersect_kernel.py_func)

    @numba.njit(nogil=True, fastmath=False)
    def transport_kernel(
        start, stop, scene, face_tables, triangle_normals, receivers,
        origins, directions, powers, source_faces, seeds, max_depth,
        min_energy, termination_mode, angle_dependent, importance_fraction,
        epsilon, include_paths, events, terminals, stochastic,
        record_offsets, event_start, event_stop, overflow_flags,
    ):
        (
            receiver_centers, receiver_normals, receiver_u, receiver_v,
            half_widths, half_heights, minimum_cosines, inverse_widths,
            inverse_heights, grid_rows, grid_columns,
        ) = receivers
        (
            faces, incoming, reflected, emitted, statuses, lobes, kinds,
            points, normals, distances, importance_flags, importance_weights,
        ) = events
        (
            terminal_kinds, terminal_depths, terminal_power, terminal_ray_kinds,
            terminal_receivers, terminal_rows, terminal_columns, terminal_received,
            terminal_points, terminal_normals, terminal_distances, terminal_incoming,
        ) = terminals
        current_origins = np.empty((1, 3), dtype=np.float64)
        current_directions = np.empty((1, 3), dtype=np.float64)
        current_power = np.empty(1, dtype=np.float64)
        ignored = np.empty(1, dtype=np.int64)
        minimum_t = np.empty(1, dtype=np.float64)
        maximum_t = np.empty(1, dtype=np.float64)
        hit_distances = np.empty(1, dtype=np.float64)
        hit_faces = np.empty(1, dtype=np.int64)
        stack_entries = np.empty(max(1, len(scene[8])), dtype=np.float64)
        stack_nodes = np.empty(max(1, len(scene[8])), dtype=np.int64)
        surface_points = np.empty((1, 3), dtype=np.float64)
        surface_normals = np.empty((1, 3), dtype=np.float64)
        profile_reflectance = np.empty(1, dtype=np.float64)
        profile_roughness = np.empty(1, dtype=np.float64)
        profile_scatter = np.empty(1, dtype=np.int8)
        profile_specular = np.empty(1, dtype=np.float64)
        profile_sigma = np.empty(1, dtype=np.float64)
        keys = np.empty(1, dtype=np.uint64)
        supported = np.empty(1, dtype=np.bool_)
        planned_reflected = np.empty(1, dtype=np.float64)
        planned_emitted = np.empty(1, dtype=np.float64)
        planned_directions = np.empty((1, 3), dtype=np.float64)
        planned_status = np.empty(1, dtype=np.uint16)
        planned_lobes = np.empty(1, dtype=np.int8)
        draw_counts = np.empty(1, dtype=np.uint8)
        eligible = np.empty(1, dtype=np.bool_)
        directed = np.empty(1, dtype=np.bool_)
        zero_weight = np.empty(1, dtype=np.bool_)
        unsupported = np.empty(1, dtype=np.bool_)
        weights = np.empty(1, dtype=np.float64)
        minimum_t[0] = epsilon
        event_cursor = event_start
        for primary in range(start, stop):
            if compact:
                record_offsets[primary] = event_cursor
            for axis in range(3):
                current_origins[0, axis] = origins[primary, axis]
                current_directions[0, axis] = directions[primary, axis]
            current_power[0] = powers[primary]
            ignored[0] = source_faces[primary]
            keys[0] = seeds[primary]
            ray_kind = 0
            stochastic[primary] = False
            terminal_receivers[primary] = -1
            terminal_rows[primary] = -1
            terminal_columns[primary] = -1
            terminal_received[primary] = 0.0
            terminal_incoming[primary] = 0.0
            if include_paths:
                terminal_distances[primary] = 0.0
                for axis in range(3):
                    terminal_points[primary, axis] = 0.0
                    terminal_normals[primary, axis] = 0.0
            for depth in range(max_depth + 1):
                origin_x, origin_y, origin_z = current_origins[0]
                direction_x, direction_y, direction_z = current_directions[0]
                best_distance = np.inf
                best_receiver = -1
                best_row = -1
                best_column = -1
                for receiver in range(len(half_widths)):
                    denominator = (
                        direction_x * receiver_normals[receiver, 0]
                        + direction_y * receiver_normals[receiver, 1]
                        + direction_z * receiver_normals[receiver, 2]
                    )
                    if abs(denominator) < 1e-12:
                        continue
                    distance = (
                        (receiver_centers[receiver, 0] - origin_x) * receiver_normals[receiver, 0]
                        + (receiver_centers[receiver, 1] - origin_y) * receiver_normals[receiver, 1]
                        + (receiver_centers[receiver, 2] - origin_z) * receiver_normals[receiver, 2]
                    ) / denominator
                    if distance <= epsilon or distance >= best_distance:
                        continue
                    local_x = origin_x + direction_x * distance - receiver_centers[receiver, 0]
                    local_y = origin_y + direction_y * distance - receiver_centers[receiver, 1]
                    local_z = origin_z + direction_z * distance - receiver_centers[receiver, 2]
                    u_value = (
                        local_x * receiver_u[receiver, 0]
                        + local_y * receiver_u[receiver, 1]
                        + local_z * receiver_u[receiver, 2]
                    )
                    v_value = (
                        local_x * receiver_v[receiver, 0]
                        + local_y * receiver_v[receiver, 1]
                        + local_z * receiver_v[receiver, 2]
                    )
                    if (
                        u_value < -half_widths[receiver]
                        or u_value > half_widths[receiver]
                        or v_value < -half_heights[receiver]
                        or v_value > half_heights[receiver]
                        or max(0.0, -denominator) < minimum_cosines[receiver]
                    ):
                        continue
                    best_distance = distance
                    best_receiver = receiver
                    best_column = min(grid_columns[receiver] - 1, max(0, int((u_value + half_widths[receiver]) * inverse_widths[receiver] * grid_columns[receiver])))
                    best_row = min(grid_rows[receiver] - 1, max(0, int((v_value + half_heights[receiver]) * inverse_heights[receiver] * grid_rows[receiver])))
                maximum_t[0] = best_distance
                intersect_kernel(
                    current_origins, current_directions, minimum_t, maximum_t,
                    ignored, scene[0], scene[1], scene[2], scene[3], scene[4],
                    scene[5], scene[6], scene[7], scene[8], scene[9],
                    hit_distances, hit_faces, stack_entries, stack_nodes,
                )
                face = hit_faces[0]
                terminal_depths[primary] = depth
                terminal_power[primary] = current_power[0]
                terminal_ray_kinds[primary] = ray_kind
                if face < 0:
                    terminal_kinds[primary] = TERMINAL_RECEIVER if best_receiver >= 0 else TERMINAL_ESCAPED
                    if best_receiver >= 0:
                        terminal_receivers[primary] = best_receiver
                        terminal_rows[primary] = best_row
                        terminal_columns[primary] = best_column
                        terminal_received[primary] = current_power[0]
                        terminal_incoming[primary] = current_power[0]
                        if include_paths:
                            terminal_distances[primary] = best_distance
                            for axis in range(3):
                                terminal_points[primary, axis] = current_origins[0, axis] + current_directions[0, axis] * best_distance
                                terminal_normals[primary, axis] = receiver_normals[best_receiver, axis]
                    break
                distance = hit_distances[0]
                for axis in range(3):
                    surface_points[0, axis] = current_origins[0, axis] + current_directions[0, axis] * distance
                    surface_normals[0, axis] = triangle_normals[face, axis]
                orientation = surface_normals[0, 0] * direction_x + surface_normals[0, 1] * direction_y + surface_normals[0, 2] * direction_z
                if orientation > 0.0:
                    for axis in range(3):
                        surface_normals[0, axis] = -surface_normals[0, axis]
                profile_reflectance[0] = face_tables[0][face]
                profile_roughness[0] = face_tables[1][face]
                profile_scatter[0] = face_tables[2][face]
                profile_specular[0] = face_tables[3][face]
                profile_sigma[0] = face_tables[4][face]
                plan_kernel(
                    (
                        current_directions, surface_normals, current_power,
                        profile_reflectance, profile_roughness, profile_scatter,
                        profile_specular, profile_sigma, keys, surface_points,
                    ),
                    (
                        depth, max_depth, min_energy, termination_mode,
                        angle_dependent, importance_fraction, epsilon,
                    ),
                    (
                        receiver_centers, receiver_normals, receiver_u, receiver_v,
                        half_widths, half_heights, minimum_cosines,
                    ),
                    (
                        supported, planned_reflected, planned_emitted,
                        planned_directions, planned_status, planned_lobes, draw_counts,
                        eligible, directed, zero_weight, unsupported, weights,
                    ),
                )
                if not supported[0]:
                    raise ValueError('unsupported native CPU transport reflection')
                if compact and event_cursor >= event_stop:
                    overflow_flags[start] = True
                    return
                record_row = event_cursor if compact else primary
                record_column = 0 if compact else depth
                faces[record_row, record_column] = face
                incoming[record_row, record_column] = current_power[0]
                reflected[record_row, record_column] = planned_reflected[0]
                emitted[record_row, record_column] = planned_emitted[0]
                statuses[record_row, record_column] = planned_status[0]
                lobes[record_row, record_column] = planned_lobes[0]
                kinds[record_row, record_column] = ray_kind
                importance_flags[record_row, record_column] = int(eligible[0]) | (int(directed[0]) << 1) | (int(zero_weight[0]) << 2) | (int(unsupported[0]) << 3)
                importance_weights[record_row, record_column] = weights[0]
                stochastic[primary] = stochastic[primary] or draw_counts[0] > 0
                if include_paths:
                    distances[record_row, record_column] = distance
                    for axis in range(3):
                        points[record_row, record_column, axis] = surface_points[0, axis]
                        normals[record_row, record_column, axis] = surface_normals[0, axis]
                if compact:
                    event_cursor += 1
                if not planned_status[0] & STATUS_EMITTED:
                    terminal_kinds[primary] = TERMINAL_BLOCKED
                    break
                for axis in range(3):
                    current_directions[0, axis] = planned_directions[0, axis]
                    current_origins[0, axis] = surface_points[0, axis] + planned_directions[0, axis] * epsilon
                current_power[0] = planned_emitted[0]
                ignored[0] = face
                ray_kind = planned_lobes[0] + 1

    return transport_kernel


def _owned(values, dtype=None):
    result = np.array(values, dtype=dtype, order='C', copy=True)
    result.setflags(write=False)
    return result


def _make_packing_kernel():
    numba = importlib.import_module('numba')

    @numba.njit(nogil=True, fastmath=False)
    def pack_kernel(start, stop, counts, offsets, source, destination, include_paths, source_offsets):
        for primary in range(start, stop):
            for depth in range(counts[primary]):
                position = offsets[primary] + depth
                record_row = source_offsets[primary] + depth if len(source_offsets) else primary
                record_column = 0 if len(source_offsets) else depth
                destination[0][position] = source[0][record_row, record_column]
                destination[1][position] = source[1][record_row, record_column]
                destination[2][position] = source[2][record_row, record_column]
                destination[3][position] = source[3][record_row, record_column]
                destination[4][position] = source[4][record_row, record_column]
                destination[5][position] = source[5][record_row, record_column]
                destination[6][position] = source[6][record_row, record_column]
                if include_paths:
                    for axis in range(3):
                        destination[7][position, axis] = source[7][record_row, record_column, axis]
                        destination[8][position, axis] = source[8][record_row, record_column, axis]
                    destination[9][position] = source[9][record_row, record_column]
                destination[10][position] = source[10][record_row, record_column]
                destination[11][position] = source[11][record_row, record_column]

    return pack_kernel


def _pack_events(events, counts, offsets, include_paths, source_offsets=None):
    if source_offsets is None:
        source_offsets = np.empty(0, dtype=np.int64)
    event_count = int(offsets[-1])
    packed = tuple(
        np.empty(
            (event_count if include_paths else 0, 3) if index in (7, 8)
            else event_count if include_paths or index != 9 else 0,
            dtype=values.dtype,
        )
        for index, values in enumerate(events)
    )
    global _PACK_KERNEL
    with _LOCK:
        if _PACK_KERNEL is None:
            _PACK_KERNEL = _make_packing_kernel()
        kernel = _PACK_KERNEL
        started = time.perf_counter()
        prior_signatures = len(kernel.signatures)
        kernel(0, 0, counts, offsets, events, packed, include_paths, source_offsets)
        compile_sec = time.perf_counter() - started if len(kernel.signatures) > prior_signatures else 0.0
    run_cpu_ranges(
        len(counts), lambda start, stop: kernel(start, stop, counts, offsets, events, packed, include_paths, source_offsets),
        minimum_rows=512, default_worker_limit=8,
    )
    for values in packed:
        values.setflags(write=False)
    return packed, compile_sec


def trace_native_cpu_transport(
    mesh, origins, directions, powers, source_faces, seeds, face_tables,
    receivers, config, *, include_paths, importance_fraction=0.0,
):
    row_count = len(powers)
    depth_count = config.max_depth + 1
    requested_layout = os.environ.get('LEAKAGE_CPU_EVENT_LAYOUT', 'auto')
    compact = requested_layout == 'compact' or (requested_layout == 'auto' and config.max_depth >= 16)
    if not compact and row_count * depth_count > MAX_BATCH_EVENT_SLOTS:
        raise ValueError('native CPU transport event capacity exceeded')
    if not row_count:
        raise ValueError('native CPU transport requires a nonempty batch')
    vectors = tuple(np.ascontiguousarray(values, dtype=np.float64) for values in (origins, directions))
    if any(values.shape != (row_count, 3) or not np.all(np.isfinite(values)) for values in vectors):
        raise ValueError('native CPU transport requires finite aligned XYZ arrays')
    powers = np.ascontiguousarray(powers, dtype=np.float64)
    source_faces = np.ascontiguousarray(source_faces, dtype=np.int64)
    seeds = np.ascontiguousarray(seeds, dtype=np.uint64)
    if any(values.shape != (row_count,) for values in (powers, source_faces, seeds)) or not np.all(np.isfinite(powers)) or np.any(powers < 0.0) or np.any(source_faces < -1) or np.any(source_faces >= len(mesh.faces)):
        raise ValueError('native CPU transport requires valid aligned ray state')
    if len(face_tables) != 5 or any(values.shape != (len(mesh.faces),) for values in face_tables):
        raise ValueError('native CPU transport requires complete face tables')
    scene = mesh.prepare_native_cpu_scene()
    intersect_kernel, intersection_jit = intersection._ensure_kernel(scene)
    global _KERNEL, _COMPACT_KERNEL
    with _LOCK:
        if compact:
            if _COMPACT_KERNEL is None:
                _COMPACT_KERNEL = _make_kernel(intersect_kernel, counter._make_kernel(packed=True), compact=True)
            kernel = _COMPACT_KERNEL
        else:
            if _KERNEL is None:
                _KERNEL = _make_kernel(intersect_kernel, counter._make_kernel(packed=True))
            kernel = _KERNEL
    scene_arrays = tuple(getattr(scene, name) for name in (
        'triangle_v0', 'triangle_edge1', 'triangle_edge2', 'node_bounds_min',
        'node_bounds_max', 'node_left', 'node_right', 'node_start',
        'node_count', 'ordered_faces',
    ))
    receiver_arrays = tuple(
        np.asarray([getattr(frame, name) if name not in ('center',) else frame.receiver.center for frame in receivers], dtype=np.float64).reshape(-1, 3)
        for name in ('center', 'normal', 'u_axis', 'v_axis')
    ) + tuple(
        np.asarray([getattr(frame, name) for frame in receivers], dtype=np.float64)
        for name in ('half_width', 'half_height', 'minimum_acceptance_cosine', 'inverse_width', 'inverse_height')
    ) + tuple(
        np.asarray([getattr(frame, name) for frame in receivers], dtype=np.int64)
        for name in ('rows', 'columns')
    )
    pool_capacity = min(MAX_BATCH_EVENT_SLOTS, row_count * depth_count)
    event_shape = (pool_capacity, 1) if compact else (row_count, depth_count)
    path_shape = event_shape if include_paths else (0, 0)
    events = (
        np.empty(event_shape, dtype=np.int64),
        *(np.empty(event_shape, dtype=np.float64) for _ in range(3)),
        np.empty(event_shape, dtype=np.uint16),
        np.empty(event_shape, dtype=np.int8),
        np.empty(event_shape, dtype=np.int8),
        np.empty((*path_shape, 3), dtype=np.float64),
        np.empty((*path_shape, 3), dtype=np.float64),
        np.empty(path_shape, dtype=np.float64),
        np.empty(event_shape, dtype=np.uint8),
        np.empty(event_shape, dtype=np.float64),
    )
    path_rows = row_count if include_paths else 0
    terminals = (
        np.empty(row_count, dtype=np.int8),
        np.empty(row_count, dtype=np.int16),
        np.empty(row_count, dtype=np.float64),
        np.empty(row_count, dtype=np.int8),
        *(np.empty(row_count, dtype=np.int32) for _ in range(3)),
        np.empty(row_count, dtype=np.float64),
        np.empty((path_rows, 3), dtype=np.float64),
        np.empty((path_rows, 3), dtype=np.float64),
        np.empty(path_rows, dtype=np.float64),
        np.empty(row_count, dtype=np.float64),
    )
    stochastic = np.empty(row_count, dtype=np.bool_)
    record_offsets = np.empty(row_count if compact else 0, dtype=np.int64)
    overflow_flags = np.zeros(row_count if compact else 0, dtype=np.bool_)
    args = (
        scene_arrays, face_tables, mesh.prepared_triangle_normals(), receiver_arrays,
        *vectors, powers, source_faces, seeds, config.max_depth, config.min_energy,
        counter.TERMINATION_THRESHOLD if config.termination_mode == 'threshold' else counter.TERMINATION_RUSSIAN_ROULETTE,
        config.angle_dependent_reflectance, float(importance_fraction), config.epsilon_mm,
        include_paths, events, terminals, stochastic,
    )
    started = time.perf_counter()
    with _LOCK:
        prior_signatures = len(kernel.signatures)
        kernel(0, 0, *args, record_offsets, 0, 0, overflow_flags)
        compile_sec = time.perf_counter() - started if len(kernel.signatures) > prior_signatures else 0.0
    started = time.perf_counter()
    workers = run_cpu_ranges(
        row_count, lambda start, stop: kernel(
            start, stop, *args, record_offsets,
            pool_capacity * start // row_count, pool_capacity * stop // row_count,
            overflow_flags,
        ),
        minimum_rows=512, default_worker_limit=8,
    )
    execute_sec = time.perf_counter() - started
    if np.any(overflow_flags):
        raise NativeCpuEventBudgetExceeded(pool_capacity, execute_sec)
    materialize_started = time.perf_counter()
    counts = terminals[1].astype(np.int64) + (terminals[0] == TERMINAL_BLOCKED)
    offsets = np.empty(row_count + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(counts, out=offsets[1:])
    packed_events, packing_jit = _pack_events(events, counts, offsets, include_paths, record_offsets)
    core_events = packed_events[:7]
    path_events = packed_events[7:10]
    importance_flags, importance_weights = packed_events[10:]
    tape = PrimaryMajorEventTape(
        EVENT_TAPE_CONTRACT, PATH_PAYLOAD_FULL if include_paths else PATH_PAYLOAD_OMITTED,
        row_count, _owned(offsets),
        _owned(vectors[0] if include_paths else np.empty((0, 3))),
        _owned(vectors[1] if include_paths else np.empty((0, 3))),
        _owned(source_faces if include_paths else np.empty(0, dtype=np.int64)),
        _owned(powers), _owned(seeds), core_events[0], *path_events,
        *core_events[1:], *(_owned(values) for values in terminals), 0,
    )
    scratch_bytes = sum(values.nbytes for values in (*events, *terminals, stochastic, counts, offsets, importance_flags, importance_weights, record_offsets, overflow_flags))
    scratch_bytes += workers * (2 * len(scene.node_count) * 8 + 512)
    tape = replace(tape, peak_bytes=tape.nbytes + int(scratch_bytes))
    materialize_sec = time.perf_counter() - materialize_started
    started = time.perf_counter()
    tape.validate()
    validation_sec = time.perf_counter() - started
    return NativeCpuTransportExecution(
        tape, execute_sec, intersection_jit + compile_sec + packing_jit,
        validation_sec, materialize_sec, workers, int(np.count_nonzero(stochastic)),
        importance_flags, importance_weights,
        str(importlib.import_module('numba').__version__), scene.build_sec,
        'bounded_actual_event_pool_v1' if compact else 'dense_primary_depth_v1',
        pool_capacity,
    )
