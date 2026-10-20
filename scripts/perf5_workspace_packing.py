from __future__ import annotations

import ast
import inspect
import math
import textwrap

import numpy as np

from leakage_simulator import gpu_cuda_resident_wavefront as resident


MODES = ('current', 'reuse_context', 'packed', 'packed_reuse')


def allocation_specs(ray_count, depth_count, geometry_count, contract, logical_depth_count, stack_width):
    logical_depth_count = depth_count if logical_depth_count is None else logical_depth_count
    capacity = resident._workspace_capacity(ray_count)
    geometry_capacity = min(capacity, resident._workspace_capacity(max(1, geometry_count)))
    event_shape = (capacity, depth_count)
    geometry_shape = (geometry_capacity, depth_count)
    event_order = 'F' if contract == resident.COMPACT_WORKSPACE_CONTRACT else 'C'
    input_bytes = capacity * 72 + logical_depth_count * 8 + 88
    return [
        ((input_bytes,), np.dtype(np.uint8), 'C'),
        (event_shape, np.dtype(np.int32), event_order),
        (geometry_shape, np.dtype(np.float64), 'C'),
        ((*geometry_shape, 3), np.dtype(np.float64), 'C'),
        ((*geometry_shape, 3), np.dtype(np.float64), 'C'),
        *[(event_shape, np.dtype(np.float64), event_order) for _ in range(3)],
        (event_shape, np.dtype(np.uint16), event_order),
        *[(event_shape, np.dtype(np.int8), event_order) for _ in range(2)],
        ((capacity,), np.dtype(np.int8), 'C'),
        ((capacity,), np.dtype(np.int16), 'C'),
        ((capacity,), np.dtype(np.float64), 'C'),
        ((capacity,), np.dtype(np.int8), 'C'),
        *[((capacity,), np.dtype(np.int32), 'C') for _ in range(3)],
        ((capacity,), np.dtype(np.float64), 'C'),
        *[((geometry_capacity, 3), np.dtype(np.float64), 'C') for _ in range(2)],
        ((geometry_capacity,), np.dtype(np.float64), 'C'),
        ((capacity,), np.dtype(np.float64), 'C'),
        ((capacity,), np.dtype(np.uint8), 'C'),
        ((capacity * stack_width,), np.dtype(np.float64), 'C'),
        ((capacity * stack_width,), np.dtype(np.int64), 'C'),
        ((capacity,), np.dtype(np.uint8), 'C'),
    ]


def view_strides(shape, dtype, order):
    if order not in ('C', 'F') or any(dimension < 1 for dimension in shape):
        raise ValueError('Packed workspace requires positive C/F array dimensions')
    stride = dtype.itemsize
    strides = []
    for dimension in reversed(shape) if order == 'C' else shape:
        strides.append(stride)
        stride *= dimension
    return tuple(reversed(strides)) if order == 'C' else tuple(strides)


class PackedWorkspaceCuda:
    def __init__(self, cuda, ray_count, depth_count, geometry_count, contract,
                 logical_depth_count, stack_width):
        self.cuda = cuda
        self.specs = allocation_specs(ray_count, depth_count, geometry_count,
            contract, logical_depth_count, stack_width)
        self.layout = []
        self.byte_count = 0
        self.cursor = 0
        self.buffer = None
        for shape, dtype, order in self.specs:
            self.byte_count += -self.byte_count % max(256, dtype.alignment)
            end = self.byte_count + math.prod(shape) * dtype.itemsize
            self.layout.append((self.byte_count, end, shape, dtype, order))
            self.byte_count = end

    def device_array(self, shape, dtype=np.float64, order='C'):
        shape = (shape,) if isinstance(shape, int) else tuple(shape)
        if self.cursor >= len(self.layout):
            raise ValueError('Unexpected additional workspace allocation')
        start, end, expected_shape, expected_dtype, expected_order = self.layout[self.cursor]
        if (shape, np.dtype(dtype), order) != (expected_shape, expected_dtype, expected_order):
            raise ValueError('Workspace allocation no longer matches the packing contract')
        if self.buffer is None:
            self.buffer = self.cuda.device_array(self.byte_count, dtype=np.uint8)
        self.cursor += 1
        if not hasattr(self.buffer, 'gpu_data'):
            return self.buffer[start:end].view(dtype).reshape(shape, order=order)
        from numba.cuda.cudadrv.devicearray import DeviceNDArray
        return DeviceNDArray(shape, view_strides(shape, expected_dtype, order), dtype,
            stream=self.buffer.stream, gpu_data=self.buffer.gpu_data.view(start, end))


def workspace_factory(mode):
    if mode not in MODES:
        raise ValueError('Unknown workspace experiment')
    original = resident._ensure_workspace
    if mode == 'current':
        return original
    tree = ast.parse(textwrap.dedent(inspect.getsource(original)))
    definition = tree.body[0]
    guards = [node for node in ast.walk(definition) if isinstance(node, ast.BoolOp)
        and any(isinstance(value, ast.Compare) and ast.unparse(value)
            == 'existing.context_identity == id(context)' for value in node.values)]
    if len(guards) != 1:
        raise ValueError('Workspace context guard changed')
    if mode in ('reuse_context', 'packed_reuse'):
        guards[0].values = [value for value in guards[0].values if ast.unparse(value)
            != 'existing.context_identity == id(context)']
        returns = [node for node in ast.walk(definition) if isinstance(node, ast.If)
            and any(isinstance(statement, ast.Return) and ast.unparse(statement.value)
                == '(existing, True, 0.0)' for statement in node.body)]
        if len(returns) != 1:
            raise ValueError('Workspace reuse return changed')
        returns[0].body.insert(0, ast.parse('existing.context_identity = id(context)').body[0])
    if mode in ('packed', 'packed_reuse'):
        insertion = [index for index, node in enumerate(definition.body)
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == 'started' for target in node.targets)]
        if len(insertion) != 1:
            raise ValueError('Workspace preparation timing boundary changed')
        definition.args.kwonlyargs.append(ast.arg(arg='_packed_workspace_factory'))
        definition.args.kw_defaults.append(ast.Constant(value=None))
        statement = ast.parse('cuda = _packed_workspace_factory(cuda, ray_count, depth_count, '
            'geometry_count, workspace_contract, logical_depth_count, context.scene.stack_width)').body[0]
        definition.body.insert(insertion[0] + 1, statement)
    ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5-workspace-packing-experiment>', 'exec'), resident.__dict__, namespace)
    candidate = namespace[definition.name]
    if mode in ('packed', 'packed_reuse'):
        candidate.__kwdefaults__['_packed_workspace_factory'] = PackedWorkspaceCuda
    return candidate
