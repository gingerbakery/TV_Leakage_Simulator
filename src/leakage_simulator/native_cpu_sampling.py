from __future__ import annotations

import importlib
import math
import os
import threading
import time

import numpy as np

from .cpu_parallel import run_cpu_ranges


CONTRACT = 'strict_fp64_primary_preparation_v1'
_LOCK = threading.Lock()
_LOCAL = threading.local()
_KERNELS = None
_DISABLED = False


def snapshot():
    return dict(getattr(_LOCAL, 'metrics', {}))


def delta_stats(baseline):
    current = snapshot()
    return {
        'native_primary_preparation_contract': CONTRACT,
        **{f'native_primary_preparation_{name}': current.get(name, 0) - baseline.get(name, 0)
           for name in ('success_batches', 'success_rows', 'fallback_batches', 'jit_sec', 'execute_sec')},
    }


def _record(name, value):
    if not hasattr(_LOCAL, 'metrics'):
        _LOCAL.metrics = {}
    _LOCAL.metrics[name] = _LOCAL.metrics.get(name, 0) + value


def merge_stats(metrics):
    for name in ('success_batches', 'success_rows', 'fallback_batches', 'jit_sec', 'execute_sec'):
        if name in metrics:
            _record(name, metrics[name])


def _make_kernels():
    numba = importlib.import_module('numba')

    @numba.njit(inline='always', fastmath=False)
    def normalize(vector_x, vector_y, vector_z):
        length = math.sqrt(vector_x * vector_x + vector_y * vector_y + vector_z * vector_z)
        if length < 1e-18:
            length = 1e-18
        return vector_x / length, vector_y / length, vector_z / length

    @numba.njit(nogil=True, fastmath=False)
    def directions_kernel(start, stop, normals, basis_u, basis_v, component_x, component_y, component_z, row_basis, output):
        for row in range(start, stop):
            if row_basis:
                normal_x, normal_y, normal_z = normals[row]
                helper_y = 1.0 if abs(normal_z) > 0.95 else 0.0
                helper_z = 0.0 if abs(normal_z) > 0.95 else 1.0
                basis_ux, basis_uy, basis_uz = normalize(
                    helper_y * normal_z - helper_z * normal_y,
                    helper_z * normal_x - 0.0 * normal_z,
                    0.0 * normal_y - helper_y * normal_x,
                )
                basis_vx, basis_vy, basis_vz = normalize(
                    normal_y * basis_uz - normal_z * basis_uy,
                    normal_z * basis_ux - normal_x * basis_uz,
                    normal_x * basis_uy - normal_y * basis_ux,
                )
            else:
                normal_x, normal_y, normal_z = normals[0]
                basis_ux, basis_uy, basis_uz = basis_u[0]
                basis_vx, basis_vy, basis_vz = basis_v[0]
            direction_x = component_x[row] * basis_ux + component_y[row] * basis_vx + component_z[row] * normal_x
            direction_y = component_x[row] * basis_uy + component_y[row] * basis_vy + component_z[row] * normal_y
            direction_z = component_x[row] * basis_uz + component_y[row] * basis_vz + component_z[row] * normal_z
            output[row, 0], output[row, 1], output[row, 2] = normalize(direction_x, direction_y, direction_z)

    @numba.njit(nogil=True, fastmath=False)
    def plane_kernel(start, stop, center, u_axis, v_axis, normal, u_offsets, v_offsets, epsilon, output):
        for row in range(start, stop):
            for axis in range(3):
                point = center[axis] + u_offsets[row] * u_axis[axis] + v_offsets[row] * v_axis[axis]
                output[row, axis] = point + epsilon * normal[axis]

    @numba.njit(nogil=True, fastmath=False)
    def face_kernel(start, stop, triangles, slots, weight_a, weight_b, weight_c, output):
        for row in range(start, stop):
            slot = slots[row]
            for axis in range(3):
                output[row, axis] = weight_a[row] * triangles[slot, 0, axis] + weight_b[row] * triangles[slot, 1, axis] + weight_c[row] * triangles[slot, 2, axis]

    return directions_kernel, plane_kernel, face_kernel


def _execute(slot, row_count, inputs):
    global _KERNELS, _DISABLED
    if not enabled(row_count):
        return None
    try:
        started = time.perf_counter()
        with _LOCK:
            if _KERNELS is None:
                _KERNELS = _make_kernels()
            kernel = _KERNELS[slot]
            output = np.empty((row_count, 3), dtype=np.float64)
            args = (*inputs, output)
            signature_count = len(kernel.signatures)
            kernel(0, 0, *args)
            if len(kernel.signatures) != signature_count:
                _record('jit_sec', time.perf_counter() - started)
        started = time.perf_counter()
        run_cpu_ranges(row_count, lambda start, stop: kernel(start, stop, *args), minimum_rows=8192)
        _record('execute_sec', time.perf_counter() - started)
        if not np.all(np.isfinite(output)):
            raise ValueError('nonfinite native sampling output')
        _record('success_batches', 1)
        _record('success_rows', row_count)
        return output
    except Exception:
        _DISABLED = True
        _record('fallback_batches', 1)
        return None


def enabled(row_count):
    return (
        row_count >= 8192 and not _DISABLED
        and os.environ.get('LEAKAGE_NATIVE_SAMPLING', 'auto') != 'off'
        and os.environ.get('LEAKAGE_CPU_TRANSPORT', 'auto') != 'wavefront'
    )


def direction_rows(normals, component_x, component_y, component_z, *, basis_u=None, basis_v=None):
    row_basis = basis_u is None
    if row_basis:
        basis_u = np.empty((0, 3), dtype=np.float64)
        basis_v = np.empty((0, 3), dtype=np.float64)
    return _execute(0, len(component_x), (
        np.asarray(normals).reshape(-1, 3), np.asarray(basis_u).reshape(-1, 3),
        np.asarray(basis_v).reshape(-1, 3), component_x, component_y, component_z,
        row_basis,
    ))


def plane_origins(center, u_axis, v_axis, normal, u_offsets, v_offsets, epsilon):
    return _execute(1, len(u_offsets), (center, u_axis, v_axis, normal, u_offsets, v_offsets, epsilon))


def face_points(triangles, slots, weight_a, weight_b, weight_c):
    return _execute(2, len(slots), (triangles, slots, weight_a, weight_b, weight_c))
