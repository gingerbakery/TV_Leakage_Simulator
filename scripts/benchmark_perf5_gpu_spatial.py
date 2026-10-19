from __future__ import annotations

import argparse
import ast
import builtins
import copy
import hashlib
import inspect
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

import benchmark_perf5_gpu_coherent as coherent
from benchmark_perf5_gpu_frontier import _function
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident

BASE_FACTORY = coherent.coherent_factory


def spatial_classification(cells):
    body = []
    for axis, coordinate in enumerate(('x', 'y', 'z')):
        body.extend(ast.parse(f'''
scaled = (origin_{coordinate} - grid_basis[{axis}]) * grid_basis[{axis + 3}]
if not math.isfinite(scaled) or scaled <= 0.0:
    cell_{coordinate} = 0
elif scaled >= {cells}:
    cell_{coordinate} = {cells - 1}
else:
    cell_{coordinate} = int(scaled)
''').body)
    body.extend(ast.parse(f'''
octant = int(direction_x < 0.0) + 2 * int(direction_y < 0.0) + 4 * int(direction_z < 0.0)
bucket = octant + 8 * (cell_x + {cells} * (cell_y + {cells} * cell_z))
''').body)
    return body


def spatial_factory(cells=4):
    if cells not in (4, 8):
        raise ValueError('Spatial coherence supports four or eight cells per axis')
    bucket_count = cells ** 3 * 8
    original_compile = builtins.compile
    captured = []

    def capture(source, filename, mode, *values, **keywords):
        if filename == '<perf5_device_direction_coherent_frontier>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=capture):
        BASE_FACTORY(8)
    if len(captured) != 1:
        raise ValueError('Exactly one coherent transport factory must be captured')
    tree = captured[0]
    factory = tree.body[0]
    original = ast.parse(inspect.getsource(resident._make_kernel)).body[0]
    original_kernel = next(node for node in original.body if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    arguments = tuple(argument.arg for argument in original_kernel.args.args)
    extra = ('depth', 'active_ids', 'next_ids', 'active_counts', 'bucket_counts', 'bucket_offsets',
        'bucket_keys', 'bucket_ranks', 'alive_flags', 'query_distances', 'query_faces', 'grid_basis')
    stages = []
    parameter_sets = []
    names = ('coherent_reset', 'coherent_init', 'coherent_prefix', 'coherent_scatter', 'coherent_query', 'coherent_shade')
    for name in names:
        definition = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == name)
        if name in ('coherent_init', 'coherent_shade'):
            classified = 0
            for node in ast.walk(definition):
                if isinstance(node, ast.If) and ast.unparse(node.test) == 'eligible':
                    if any(isinstance(step, ast.Assign) and ast.unparse(step.targets[0]) == 'bucket' for step in node.body):
                        node.body = spatial_classification(cells)
                        classified += 1
                if isinstance(node, ast.Call) and ast.unparse(node.func) == 'cuda.atomic.add' and ast.unparse(node.args[0]) == 'bucket_counts':
                    node.args[1] = ast.parse(f'({"0" if name == "coherent_init" else "depth + 1"}) * {bucket_count} + bucket', mode='eval').body
            if classified != 1:
                raise ValueError('Each initialization and survivor stage must classify exactly once')
        if name == 'coherent_reset':
            definition.body.extend(ast.parse(f'''
if cuda.grid(1) == 0:
    for axis in range(3):
        lower = 0.0
        scale = 0.0
        if node_bounds_min.shape[0] > 0:
            lower = node_bounds_min[0, axis]
            span = node_bounds_max[0, axis] - lower
            if math.isfinite(span) and span > 0.0:
                scale = {cells}.0 / span
        grid_basis[axis] = lower
        grid_basis[axis + 3] = scale
''').body)
        elif name == 'coherent_prefix':
            per_thread = (bucket_count + 255) // 256
            definition.body = ast.parse(f'''
lane = cuda.threadIdx.x
partial = cuda.shared.array(256, dtype=np.int64)
start = lane * {per_thread}
stop = start + {per_thread}
local_sum = 0
for bucket in range(start, stop):
    if bucket < {bucket_count}:
        local_sum += bucket_counts[depth * {bucket_count} + bucket]
partial[lane] = local_sum
cuda.syncthreads()
offset = 1
while offset < 256:
    increment = 0
    if lane >= offset:
        increment = partial[lane - offset]
    cuda.syncthreads()
    partial[lane] += increment
    cuda.syncthreads()
    offset *= 2
prefix = 0
if lane > 0:
    prefix = partial[lane - 1]
for bucket in range(start, stop):
    if bucket < {bucket_count}:
        slot = depth * {bucket_count} + bucket
        bucket_offsets[slot] = prefix
        prefix += bucket_counts[slot]
if lane == 0:
    active_counts[depth] = partial[255]
''').body
        elif name == 'coherent_scatter':
            for node in ast.walk(definition):
                if isinstance(node, ast.Subscript) and ast.unparse(node.value) == 'bucket_offsets':
                    node.slice = ast.parse(f'depth * {bucket_count} + bucket_keys[ray_index]', mode='eval').body
        regenerated, parameters = _function(name, definition.body, (*arguments, *extra))
        factory.body[factory.body.index(definition)] = regenerated
        stages.append(regenerated)
        parameter_sets.append(parameters)
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(original_compile(tree, '<perf5_device_spatial_direction_coherent_frontier>', 'exec'), resident.__dict__, namespace)

    def create():
        kernels = list(namespace['_make_kernel']())
        kernels[2] = PrefixKernel(kernels[2])
        return SpatialKernels(tuple(kernels), arguments, tuple(parameter_sets), bucket_count)

    return create, hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class PrefixKernel:
    def __init__(self, kernel):
        self.kernel = kernel

    def __getitem__(self, dimensions):
        return self.kernel[1, 256]

    def __getattr__(self, name):
        return getattr(self.kernel, name)


class SpatialKernels(coherent.CoherentKernels):
    def _ensure_buffers(self, count, depths):
        super()._ensure_buffers(count, depths)
        if 'grid_basis' not in self.buffers:
            self.buffers['grid_basis'] = gpu._CUDA.device_array(6, dtype=np.float64)
            self.bytes += self.buffers['grid_basis'].nbytes


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--cells', type=int, nargs='+', choices=(4, 8), default=[4, 8])
    parser.add_argument('--output', type=Path, required=True)
    args, remaining = parser.parse_known_args()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    aliases = {8 if cells == 4 else 64: cells for cells in args.cells}
    mapping = {str(alias): f'spatial{cells}' for alias, cells in aliases.items()}
    print(json.dumps({'mode_aliases': mapping}), flush=True)

    def select(alias):
        return spatial_factory(aliases[alias])

    with patch.object(coherent, 'coherent_factory', new=select), patch.object(sys, 'argv',
        [sys.argv[0], *remaining, '--buckets', *(str(alias) for alias in aliases), '--output', str(args.output)]):
        status = coherent.main()
    report = json.loads(args.output.read_text(encoding='utf-8'))
    report['contract'] = 'device_only_spatial_direction_coherent_canonical_bvh_probe_v1'
    for case in report['cases']:
        case['records'] = {mapping.get(mode, mode): value for mode, value in case['records'].items()}
    report['factory_sha256'] = {mapping.get(mode, mode): value for mode, value in report['factory_sha256'].items()}
    report['bucket_counts'] = {f'spatial{cells}': cells ** 3 * 8 for cells in args.cells}
    report['dependency_hashes'][Path(__file__).name] = own_hash
    report['source_unchanged'] = report['source_unchanged'] and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['passed'] = bool(report['passed'] and report['source_unchanged'])
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return status if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
