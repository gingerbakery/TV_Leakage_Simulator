from __future__ import annotations

import argparse
import ast
import builtins
import copy
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_gpu_frontier import _function, frontier_factory
from benchmark_perf5_gpu_persistent import STATE
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def bucket_histogram(bucket_count, predicate, counter_depth):
    if bucket_count == 1:
        classification = 'bucket = 0'
    elif bucket_count == 8:
        classification = 'bucket = int(direction_x < 0.0) + 2 * int(direction_y < 0.0) + 4 * int(direction_z < 0.0)'
    elif bucket_count == 64:
        classification = '''
bucket_x = int((direction_x + 1.0) * 2.0)
bucket_y = int((direction_y + 1.0) * 2.0)
bucket_z = int((direction_z + 1.0) * 2.0)
if bucket_x < 0:
    bucket_x = 0
elif bucket_x > 3:
    bucket_x = 3
if bucket_y < 0:
    bucket_y = 0
elif bucket_y > 3:
    bucket_y = 3
if bucket_z < 0:
    bucket_z = 0
elif bucket_z > 3:
    bucket_z = 3
bucket = bucket_x + 4 * bucket_y + 16 * bucket_z
'''
    else:
        raise ValueError('Supported direction bucket counts are 1, 8 and 64')
    body = ast.parse(f'''
eligible = {predicate}
bucket = -1
if eligible:
    pass
group_mask = np.uint32(cuda.match_any_sync(np.int32(-1), np.int32(bucket)))
leader = libdevice.ffs(np.int32(group_mask)) - 1
lane = cuda.threadIdx.x & 31
first = 0
if eligible and lane == leader:
    first = cuda.atomic.add(bucket_counts, ({counter_depth}) * {bucket_count} + bucket, cuda.popc(group_mask))
first = cuda.shfl_sync(np.int32(group_mask), first, leader)
if eligible:
    earlier = (np.uint32(1) << np.uint32(lane)) - np.uint32(1)
    rank = cuda.popc(group_mask & earlier)
    bucket_ranks[ray_index] = first + rank
    bucket_keys[ray_index] = bucket
''').body
    body[2].body = ast.parse(classification).body
    return body


def coherent_factory(bucket_count=8):
    if bucket_count not in (1, 8, 64):
        raise ValueError('Unsupported coherent direction bucket count')
    original_compile = builtins.compile
    captured = []

    def capture(source, filename, mode, *values, **keywords):
        if filename == '<perf5_compacted_canonical_frontier>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=capture):
        frontier_factory()
    if len(captured) != 1:
        raise ValueError('Exactly one exact transport step factory must be captured')
    tree = captured[0]
    factory = tree.body[0]
    factory.body.insert(0, ast.parse("libdevice = importlib.import_module('numba.cuda.libdevice')").body[0])
    original_factory = ast.parse(inspect.getsource(resident._make_kernel)).body[0]
    original_kernel = next(node for node in original_factory.body if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    arguments = tuple(argument.arg for argument in original_kernel.args.args)
    depth_loop = next(node for node in original_kernel.body if isinstance(node, ast.For) and ast.unparse(node.iter) == 'range(depth_count)')
    initialization = copy.deepcopy(original_kernel.body[2:original_kernel.body.index(depth_loop)])
    query_index = next(index for index, node in enumerate(depth_loop.body) if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'intersect_bvh')
    receiver_scan = copy.deepcopy(depth_loop.body[:query_index])
    query_call = copy.deepcopy(depth_loop.body[query_index].value)
    query_call.func.id = 'canonical_intersect_bvh'
    query_call.args[0] = ast.Name(id='physical_index', ctx=ast.Load())
    advance = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'advance_frontier_ray')
    advance_names = tuple(argument.arg for argument in advance.args.args)
    extra = ('depth', 'active_ids', 'next_ids', 'active_counts', 'bucket_counts', 'bucket_offsets',
        'bucket_keys', 'bucket_ranks', 'alive_flags', 'query_distances', 'query_faces')
    state_loads = ast.parse('''
origin_x = origins[ray_index, 0]
origin_y = origins[ray_index, 1]
origin_z = origins[ray_index, 2]
direction_x = directions[ray_index, 0]
direction_y = directions[ray_index, 1]
direction_z = directions[ray_index, 2]
current_power = powers[ray_index]
current_source_face = source_faces[ray_index]
current_ray_kind = terminal_ray_kind[ray_index]
key = reflection_seeds[ray_index]
''').body
    reset_body = ast.parse('''
physical_index = cuda.grid(1)
for slot in range(physical_index, bucket_counts.size, cuda.gridsize(1)):
    bucket_counts[slot] = 0
for slot in range(physical_index, active_counts.size, cuda.gridsize(1)):
    active_counts[slot] = 0
''').body
    reset, reset_names = _function('coherent_reset', reset_body, (*arguments, *extra))
    init_body = ast.parse('''
ray_index = cuda.grid(1)
valid = ray_index < ray_count
direction_x = 0.0
direction_y = 0.0
direction_z = 0.0
if valid:
    active_ids[ray_index] = ray_index
    alive_flags[ray_index] = 1
''').body
    init_body[-1].body.extend(initialization)
    init_body.extend(bucket_histogram(bucket_count, 'valid', '0'))
    initialize, initialize_names = _function('coherent_init', init_body, (*arguments, *extra))
    prefix_body = ast.parse(f'''
if cuda.grid(1) != 0:
    return
offset = 0
for bucket in range({bucket_count}):
    slot = depth * {bucket_count} + bucket
    bucket_offsets[slot] = offset
    offset += bucket_counts[slot]
active_counts[depth] = offset
''').body
    prefix, prefix_names = _function('coherent_prefix', prefix_body, (*arguments, *extra))
    scatter_body = ast.parse(f'''
physical_index = cuda.grid(1)
input_count = ray_count if depth == 0 else active_counts[depth - 1]
if physical_index >= input_count:
    return
ray_index = active_ids[physical_index]
if alive_flags[ray_index]:
    target = bucket_offsets[depth * {bucket_count} + bucket_keys[ray_index]] + bucket_ranks[ray_index]
    next_ids[target] = ray_index
''').body
    scatter, scatter_names = _function('coherent_scatter', scatter_body, (*arguments, *extra))
    query_body = ast.parse('''
physical_index = cuda.grid(1)
if physical_index >= active_counts[depth]:
    return
ray_index = active_ids[physical_index]
''').body + copy.deepcopy(state_loads) + receiver_scan
    query_body.extend(ast.parse(f'''
distance, face_index, overflow = {ast.unparse(query_call)}
query_distances[ray_index] = distance
query_faces[ray_index] = face_index
overflow_flags[ray_index] = overflow
''').body)
    query, query_names = _function('coherent_query', query_body, (*arguments, *extra))
    shade_body = ast.parse('''
physical_index = cuda.grid(1)
valid = physical_index < active_counts[depth]
survives = False
ray_index = 0
direction_x = 0.0
direction_y = 0.0
direction_z = 0.0
if valid:
    ray_index = active_ids[physical_index]
''').body
    shade_body[-1].body.extend(copy.deepcopy(state_loads))
    shade_body[-1].body.extend(ast.parse(f'''
survives, {', '.join(STATE)} = advance_frontier_ray({', '.join(advance_names)})
alive_flags[ray_index] = 1 if survives else 0
if survives:
    origins[ray_index, 0] = origin_x
    origins[ray_index, 1] = origin_y
    origins[ray_index, 2] = origin_z
    directions[ray_index, 0] = direction_x
    directions[ray_index, 1] = direction_y
    directions[ray_index, 2] = direction_z
    powers[ray_index] = current_power
    source_faces[ray_index] = current_source_face
    terminal_ray_kind[ray_index] = current_ray_kind
''').body)
    shade_body.extend(bucket_histogram(bucket_count, 'valid and survives', 'depth + 1'))
    shade, shade_names = _function('coherent_shade', shade_body, (*arguments, *extra))
    factory.body = [node for node in factory.body if not isinstance(node, ast.Return)
        and not (isinstance(node, ast.FunctionDef) and node.name.startswith('frontier_'))]
    factory.body.extend([reset, initialize, prefix, scatter, query, shade])
    factory.body.append(ast.Return(value=ast.Tuple(elts=[ast.Name(id=node.name, ctx=ast.Load()) for node in
        (reset, initialize, prefix, scatter, query, shade)], ctx=ast.Load())))
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(original_compile(tree, '<perf5_device_direction_coherent_frontier>', 'exec'), resident.__dict__, namespace)
    parameter_sets = (reset_names, initialize_names, prefix_names, scatter_names, query_names, shade_names)

    def create():
        return CoherentKernels(namespace['_make_kernel'](), arguments, parameter_sets, bucket_count)

    return create, hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class CoherentKernels:
    def __init__(self, kernels, arguments, parameter_sets, bucket_count):
        self.kernels = kernels
        self.arguments = arguments
        self.parameter_sets = parameter_sets
        self.bucket_count = bucket_count
        self.buffers = None
        self.capacity = 0
        self.depth_capacity = 0
        self.bytes = 0
        self.last_depth_count = 0
        self.last_active_counts = None
        self.last_bucket_counts = None

    def _ensure_buffers(self, count, depths):
        if count <= self.capacity and depths <= self.depth_capacity:
            return
        self.capacity = max(count, self.capacity)
        self.depth_capacity = max(depths, self.depth_capacity)
        layouts = {'active_ids': (self.capacity, np.int32), 'next_ids': (self.capacity, np.int32),
            'active_counts': (self.depth_capacity + 1, np.int64),
            'bucket_counts': ((self.depth_capacity + 1) * self.bucket_count, np.int64),
            'bucket_offsets': ((self.depth_capacity + 1) * self.bucket_count, np.int64),
            'bucket_keys': (self.capacity, np.int32), 'bucket_ranks': (self.capacity, np.int32),
            'alive_flags': (self.capacity, np.uint8), 'query_distances': (self.capacity, np.float64),
            'query_faces': (self.capacity, np.int64)}
        self.buffers = {name: gpu._CUDA.device_array(size, dtype=dtype) for name, (size, dtype) in layouts.items()}
        self.bytes = sum(value.nbytes for value in self.buffers.values())

    def __getitem__(self, dimensions):
        if dimensions[1] % 32:
            raise ValueError('Coherent scheduling requires complete physical warps')

        def launch(*arguments):
            if len(arguments) != len(self.arguments):
                raise ValueError('Coherent scheduling must preserve the original transport ABI')
            values = dict(zip(self.arguments, arguments))
            self._ensure_buffers(values['ray_count'], values['depth_count'])
            values.update(self.buffers)

            def invoke(index):
                layout = (1, 32) if index == 2 else dimensions
                self.kernels[index][layout](*(values[name] for name in self.parameter_sets[index]))

            invoke(0)
            invoke(1)
            limit = min(values['depth_count'], values['event_faces'].shape[1] + 1)
            for depth in range(limit):
                values['depth'] = depth
                invoke(2)
                invoke(3)
                values['active_ids'], values['next_ids'] = values['next_ids'], values['active_ids']
                invoke(4)
                invoke(5)
            self.last_depth_count = values['depth_count']
            self.last_active_counts = values['active_counts']
            self.last_bucket_counts = values['bucket_counts']

        return launch

    def resources(self):
        return {name: kernel_resources(kernel) for name, kernel in zip(
            ('reset', 'initialize', 'prefix', 'scatter', 'query', 'shade'), self.kernels)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--buckets', type=int, nargs='+', choices=(1, 8, 64), default=[1, 8, 64])
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2 or len(set(args.buckets)) != len(args.buckets):
        parser.error('Positive rays, distinct bucket counts and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    dependencies = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in
        ('benchmark_perf5_gpu_coherent.py', 'benchmark_perf5_gpu_frontier.py', 'benchmark_perf5_gpu_guarded_bvh.py',
         'benchmark_perf5_gpu_persistent.py', 'benchmark_perf5_leaf_loop.py', 'benchmark_perf5_transport.py')}
    factories = {'original': (resident._make_kernel, None), **{str(count): coherent_factory(count) for count in args.buckets}}
    states = {mode: {} for mode in factories}
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': []} for mode in states}
        expected = None
        for repeat in range(args.repeats + 1):
            for mode in states if repeat % 2 == 0 else tuple(reversed(states)):
                with variant(factories[mode][0], states[mode]):
                    started = time.perf_counter()
                    result = raytracer.run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}: {performance}')
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                compiled = states[mode]['_KERNEL']
                record['kernel_resources'] = compiled.resources() if mode != 'original' else kernel_resources(compiled)
                if mode != 'original':
                    record['extra_workspace_bytes'] = compiled.bytes
                    record['last_active_counts'] = compiled.last_active_counts[:compiled.last_depth_count + 1].copy_to_host().tolist()
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and all(hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() == value for name, value in dependencies.items())
    report = {'contract': 'device_only_direction_coherent_canonical_bvh_probe_v1',
        'delivery': {'type': 'source_checkout', 'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'dependency_hashes': dependencies,
        'factory_sha256': {mode: values[1] for mode, values in factories.items()}, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
