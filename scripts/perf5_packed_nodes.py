from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import replace
import importlib
import inspect
import time
from unittest.mock import patch

import numpy as np

from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident


INTEGER_FIELDS = ('node_left', 'node_right', 'node_start', 'node_count')


def pack_nodes(scene, layout):
    if layout not in (64, 80):
        raise ValueError('Node layout must be 64 or 80 bytes')
    count = len(scene.node_count)
    if scene.node_bounds_min.shape != (count, 3) or scene.node_bounds_max.shape != (count, 3):
        raise ValueError('Original node bounds must have three FP64 coordinates')
    if scene.node_bounds_min.dtype != np.float64 or scene.node_bounds_max.dtype != np.float64:
        raise ValueError('Node bounds must remain FP64')
    for name in INTEGER_FIELDS:
        values = getattr(scene, name)
        if values.shape != (count,) or values.dtype.kind != 'i':
            raise ValueError('Original node indices must be signed integer vectors')
        if layout == 64 and values.size and (
                values.min() < np.iinfo(np.int32).min or values.max() > np.iinfo(np.int32).max):
            raise ValueError('Node index cannot be represented exactly in signed int32')
    packed = np.empty((count, layout // 8), dtype=np.float64)
    packed[:, :3] = scene.node_bounds_min
    packed[:, 3:6] = scene.node_bounds_max
    integers = packed[:, 6:].view(np.int32 if layout == 64 else np.int64)
    for column, name in enumerate(INTEGER_FIELDS):
        integers[:, column] = getattr(scene, name)
    packed.setflags(write=False)
    return packed


class PackedReads(ast.NodeTransformer):
    def __init__(self, layout):
        self.layout = layout
        self.bounds = 0
        self.integers = 0

    def visit_Subscript(self, node):
        node = self.generic_visit(node)
        if not isinstance(node.value, ast.Name) or not isinstance(node.ctx, ast.Load):
            return node
        name = node.value.id
        if name in ('node_bounds_max', 'bounds_max'):
            if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                raise ValueError('Unexpected upper bound indexing')
            node.value.id = 'node_bounds_min' if name == 'node_bounds_max' else 'bounds_min'
            node.slice.elts[1] = ast.BinOp(left=node.slice.elts[1], op=ast.Add(), right=ast.Constant(value=3))
            self.bounds += 1
            return node
        if name not in INTEGER_FIELDS:
            return node
        field = INTEGER_FIELDS.index(name)
        index = ast.unparse(node.slice)
        if self.layout == 80:
            expression = f'libdevice.double_as_longlong(node_bounds_min[{index}, {6 + field}])'
        else:
            shift = ' >> 32' if field & 1 else ''
            expression = f'np.int64(np.int32(libdevice.double_as_longlong(node_bounds_min[{index}, {6 + field // 2}]){shift}))'
        self.integers += 1
        return ast.copy_location(ast.parse(expression, mode='eval').body, node)


def compile_factory(function, layout):
    tree = ast.parse(inspect.getsource(function))
    transform = PackedReads(layout)
    tree = ast.fix_missing_locations(transform.visit(tree))
    if not transform.bounds or not transform.integers:
        raise RuntimeError('Packed node transform did not cover bounds and integer fields')
    namespace = dict(function.__globals__)
    namespace['libdevice'] = importlib.import_module('numba.cuda.libdevice')
    exec(compile(tree, f'<perf5_direct_nodes_{layout}>', 'exec'), namespace)
    return namespace[function.__name__]


def make_kernel(layout):
    resident_factory = compile_factory(resident._make_kernel, layout)
    pair_factory = compile_factory(pair_bvh.make_pair_intersector, layout)
    return resident_factory(_intersector_factory=pair_factory)


@contextmanager
def device_variant(layout, states):
    original = gpu._ensure_device_scene

    def ensure(scene, capability):
        if not pair_scene.has_pair_scene(scene):
            return original(scene, capability)
        cached = states.get(id(scene))
        if cached is not None and cached[0] is scene:
            return cached[1], True
        packed = pack_nodes(scene, layout)
        device, reused = original(scene, capability)
        started = time.perf_counter()
        uploaded = gpu._CUDA.to_device(packed)
        gpu._CUDA.synchronize()
        candidate = replace(device, node_bounds_min=uploaded,
            upload_sec=device.upload_sec + time.perf_counter() - started)
        states[id(scene)] = scene, candidate
        return candidate, False

    with patch.object(gpu, '_ensure_device_scene', ensure):
        yield
