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
from benchmark_perf5_gpu_guarded_bvh import dual_scene, guarded_factory
from benchmark_perf5_gpu_persistent import STATE
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def _function(name, body, arguments, *, device=False):
    template = ast.parse('@cuda.jit(fastmath=False)\ndef generated():\n    pass').body[0]
    template.name = name
    template.body = body
    if device:
        template.decorator_list = ast.parse(
            '@cuda.jit(device=True, inline=True, fastmath=False)\ndef generated():\n    pass'
        ).body[0].decorator_list
    loaded = {node.id for node in ast.walk(template) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    selected = tuple(name for name in arguments if name in loaded)
    template.args.args = [ast.arg(arg=name) for name in selected]
    return template, selected


def _enqueue(queue, counters, counter_index, predicate):
    return ast.parse(f'''
participants = cuda.ballot_sync(0xffffffff, {predicate})
lane = cuda.threadIdx.x & 31
first = 0
if lane == 0 and participants != 0:
    first = cuda.atomic.add({counters}, {counter_index}, cuda.popc(participants))
first = cuda.shfl_sync(0xffffffff, first, 0)
if {predicate}:
    earlier = (np.uint32(1) << np.uint32(lane)) - np.uint32(1)
    rank = cuda.popc(participants & earlier)
    {queue}[first + rank] = ray_index
''').body


def frontier_factory(*, fused=False):
    captured = []
    original_compile = builtins.compile

    def capture(source, filename, mode, *values, **keywords):
        if filename == '<perf5_guarded_dual_bvh>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=capture):
        guarded_factory(padded=True, canonical_inline=True)
    if len(captured) != 1:
        raise ValueError('Exactly one guarded transport factory must be captured')
    tree = captured[0]
    factory = tree.body[0]
    kernel = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    original_arguments = tuple(inspect.signature(resident._make_kernel).parameters.values())
    if any(argument.name != '_intersector_factory'
        or argument.kind != inspect.Parameter.KEYWORD_ONLY
        or argument.default is not None for argument in original_arguments):
        raise ValueError('The transport factory only supports an optional intersector factory')
    arguments = tuple(argument.arg for argument in kernel.args.args if argument.arg != 'canonical_replays')
    depth_loop = next(node for node in kernel.body if isinstance(node, ast.For) and ast.unparse(node.iter) == 'range(depth_count)')
    query_index = next(index for index, node in enumerate(depth_loop.body)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'intersect_bvh')
    query_call = copy.deepcopy(depth_loop.body[query_index].value)
    query_call.args.pop()
    traversal_arguments = ', '.join(ast.unparse(value) for value in query_call.args)
    receiver_scan = copy.deepcopy(depth_loop.body[:query_index])
    initialization = copy.deepcopy(kernel.body[2:kernel.body.index(depth_loop)])
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
    extra = ('depth', 'active_ids', 'next_ids', 'active_counts', 'replay_ids', 'replay_counts',
        'query_distances', 'query_faces', 'query_maxima')
    init_body = ast.parse('''
ray_index = cuda.grid(1)
for counter_index in range(ray_index, depth_count + 1, cuda.gridsize(1)):
    active_counts[counter_index] = 0
    replay_counts[counter_index] = 0
if ray_index == 0:
    active_counts[0] = ray_count
if ray_index >= ray_count:
    return
active_ids[ray_index] = ray_index
''').body + initialization
    init_kernel, init_names = _function('frontier_init', init_body, (*arguments, *extra))
    query_body = ast.parse('''
physical_index = cuda.grid(1)
valid = physical_index < active_counts[depth]
ray_index = 0
uncertain = False
if valid:
    ray_index = active_ids[physical_index]
''').body
    query_valid = query_body[-1]
    query_valid.body.extend(copy.deepcopy(state_loads) + receiver_scan)
    fast_query_body = ast.parse(f'''
query_maxima[ray_index] = best_receiver_distance
distance = math.inf
face_index = -1
status = 2
if node_count.shape[0] > 0 and node_start[0] > 0:
    if not (0.0 < abs(direction_x) < 1e-12 or 0.0 < abs(direction_y) < 1e-12 or 0.0 < abs(direction_z) < 1e-12):
        distance, face_index, status = fast_intersect_bvh({traversal_arguments})
query_distances[ray_index] = distance
query_faces[ray_index] = face_index
uncertain = status != 0
''').body
    query_valid.body.extend(copy.deepcopy(fast_query_body))
    query_body.extend(_enqueue('replay_ids', 'replay_counts', 'depth', 'valid and uncertain'))
    query_kernel, query_names = _function('frontier_query', query_body, (*arguments, *extra))
    canonical_arguments = copy.deepcopy(query_call.args)
    canonical_arguments[0] = ast.Name(id='physical_index', ctx=ast.Load())
    canonical_arguments[9] = ast.parse('query_maxima[ray_index]', mode='eval').body
    canonical_call = ast.Call(func=ast.Name(id='canonical_intersect_bvh', ctx=ast.Load()), args=canonical_arguments, keywords=[])
    canonical_body = ast.parse('''
physical_index = cuda.grid(1)
if physical_index >= replay_counts[depth]:
    return
ray_index = replay_ids[physical_index]
''').body + copy.deepcopy(state_loads)
    canonical_body.extend(ast.parse(f'''
distance, face_index, overflow = {ast.unparse(canonical_call)}
query_distances[ray_index] = distance
query_faces[ray_index] = face_index
overflow_flags[ray_index] = overflow
''').body)
    canonical_kernel, canonical_names = _function('frontier_canonical', canonical_body, (*arguments, *extra))
    fast = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'fast_intersect_bvh')
    stack_owner = next(node for node in fast.body if isinstance(node, ast.Assign) and ast.unparse(node) == 'base = ray_index')
    stack_owner.value = ast.parse('cuda.grid(1)', mode='eval').body
    advance_body = copy.deepcopy(depth_loop.body)
    advance_body[query_index] = ast.parse('distance, face_index, overflow = query_distances[ray_index], query_faces[ray_index], overflow_flags[ray_index]').body[0]

    class Terminal(ast.NodeTransformer):
        replacements = 0

        def visit_Return(self, node):
            if node.value is not None:
                raise ValueError('Original terminal returns must not carry a value')
            self.replacements += 1
            return ast.copy_location(ast.Return(value=ast.Tuple(elts=[ast.Constant(False),
                *(ast.Name(id=name, ctx=ast.Load()) for name in STATE)], ctx=ast.Load())), node)

    transformed = Terminal()
    advance_body = [transformed.visit(statement) for statement in advance_body]
    if transformed.replacements < 5:
        raise ValueError('All transport exits must preserve terminal state')
    advance_body.append(ast.Return(value=ast.Tuple(elts=[ast.Constant(True),
        *(ast.Name(id=name, ctx=ast.Load()) for name in STATE)], ctx=ast.Load())))
    advance, advance_names = _function('advance_frontier_ray', advance_body,
        ('ray_index', 'depth', *STATE, 'key', *arguments, *extra), device=True)
    advance_names = tuple(dict.fromkeys(advance_names))
    advance.args.args = [ast.arg(arg=name) for name in advance_names]
    shade_body = ast.parse('''
physical_index = cuda.grid(1)
valid = physical_index < active_counts[depth]
survives = False
ray_index = 0
if valid:
    ray_index = active_ids[physical_index]
''').body
    shade_body[-1].body.extend(copy.deepcopy(state_loads))
    shade_body[-1].body.extend(ast.parse(f'''
survives, {', '.join(STATE)} = advance_frontier_ray({', '.join(advance_names)})
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
    shade_body.extend(_enqueue('next_ids', 'active_counts', 'depth + 1', 'valid and survives'))
    shade_kernel, shade_names = _function('frontier_shade', shade_body, (*arguments, *extra))
    fused_body = ast.parse('''
physical_index = cuda.grid(1)
ray_index = physical_index
valid = ray_index < ray_count
survives = False
uncertain = False
if valid:
    if terminal_depth[ray_index] < 0 and overflow_flags[ray_index] == 0:
        pass
''').body
    fused_valid = fused_body[-1].body[0]
    fused_valid.body = copy.deepcopy(state_loads)
    fused_valid.body.extend(ast.parse(f'''
survives = True
if depth >= 0:
    survives, {', '.join(STATE)} = advance_frontier_ray({', '.join(advance_names)})
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
    if depth + 1 < depth_count and depth + 1 <= event_faces.shape[1]:
        pass
''').body)
    fused_valid.body[-1].body[-1].body = copy.deepcopy(receiver_scan) + copy.deepcopy(fast_query_body)
    fused_body.extend(_enqueue('replay_ids', 'replay_counts', 'depth + 1', 'valid and survives and uncertain'))
    fused_kernel, fused_names = _function('frontier_advance_query', fused_body, (*arguments, *extra))
    kernel_index = factory.body.index(kernel)
    factory.body[kernel_index:kernel_index + 1] = [advance, init_kernel, query_kernel, canonical_kernel, shade_kernel, fused_kernel]
    factory.body[-1] = ast.Return(value=ast.Tuple(elts=[ast.Name(id=name, ctx=ast.Load()) for name in
        ('frontier_init', 'frontier_query', 'frontier_canonical', 'frontier_shade', 'frontier_advance_query')], ctx=ast.Load()))
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(original_compile(tree, '<perf5_compacted_canonical_frontier>', 'exec'), resident.__dict__, namespace)
    parameter_sets = (init_names, query_names, canonical_names, shade_names, fused_names)

    def create():
        return FrontierKernels(namespace['_make_kernel'](), arguments, parameter_sets, fused=fused)

    return create, hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class FrontierKernels:
    def __init__(self, kernels, arguments, parameter_sets, *, fused=False):
        self.kernels = kernels
        self.arguments = arguments
        self.parameter_sets = parameter_sets
        self.fused = fused
        self.buffers = None
        self.capacity = 0
        self.depth_capacity = 0
        self.bytes = 0
        self.launches = 0
        self.last_replay_counts = None
        self.last_active_counts = None
        self.last_depth_count = 0

    def _ensure_buffers(self, count, depths):
        if count <= self.capacity and depths <= self.depth_capacity:
            return
        self.capacity = max(count, self.capacity)
        self.depth_capacity = max(depths, self.depth_capacity)
        layouts = {'active_ids': (self.capacity, np.int32), 'next_ids': (self.capacity, np.int32),
            'replay_ids': (self.capacity, np.int32), 'active_counts': (self.depth_capacity + 1, np.int64),
            'replay_counts': (self.depth_capacity + 1, np.int64), 'query_distances': (self.capacity, np.float64),
            'query_faces': (self.capacity, np.int64), 'query_maxima': (self.capacity, np.float64)}
        self.buffers = {name: gpu._CUDA.device_array(size, dtype=dtype) for name, (size, dtype) in layouts.items()}
        self.bytes = sum(value.nbytes for value in self.buffers.values())

    def __getitem__(self, dimensions):
        if dimensions[1] % 32:
            raise ValueError('Frontier queue collectives require complete physical warps')

        def launch(*arguments):
            if len(arguments) != len(self.arguments):
                raise ValueError('Frontier adapter must preserve the original transport ABI')
            values = dict(zip(self.arguments, arguments))
            self._ensure_buffers(values['ray_count'], values['depth_count'])
            values.update(self.buffers)

            def invoke(index):
                self.kernels[index][dimensions](*(values[name] for name in self.parameter_sets[index]))
                self.launches += 1

            invoke(0)
            limit = min(values['depth_count'], values['event_faces'].shape[1] + 1)
            if self.fused:
                values['depth'] = -1
                invoke(4)
            for depth in range(limit):
                values['depth'] = depth
                if self.fused:
                    invoke(2)
                    invoke(4)
                else:
                    invoke(1)
                    invoke(2)
                    invoke(3)
                    values['active_ids'], values['next_ids'] = values['next_ids'], values['active_ids']
            self.last_replay_counts = values['replay_counts']
            self.last_active_counts = values['active_counts']
            self.last_depth_count = values['depth_count']

        return launch

    def resources(self):
        names = ('init', 'query', 'canonical', 'shade', 'advance_query')
        used = (0, 2, 4) if self.fused else (0, 1, 2, 3)
        return {names[index]: kernel_resources(self.kernels[index]) for index in used}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--modes', nargs='+', choices=('original', 'frontier', 'fused'), default=['original', 'frontier', 'fused'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2 or 'original' not in args.modes or len(set(args.modes)) != len(args.modes):
        parser.error('Positive ray counts, distinct modes with an original reference, and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    dependency_hashes = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in
        ('benchmark_perf5_gpu_frontier.py', 'benchmark_perf5_gpu_guarded_bvh.py', 'benchmark_perf5_gpu_persistent.py')}
    factories = {mode: frontier_factory(fused=mode == 'fused') for mode in args.modes if mode != 'original'}
    states = {mode: {} for mode in args.modes}
    original_context = raytracer._build_gpu_resident_wavefront_context
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        scene, prepare_sec = dual_scene(trace_input.mesh, padded=True)
        records = {mode: {'warm_sec': [], 'parity': []} for mode in states}
        expected = None
        for repeat in range(args.repeats + 1):
            for mode in states if repeat % 2 == 0 else tuple(reversed(states)):
                def context(*values, **keywords):
                    prepared = original_context(*values, **keywords)
                    if mode != 'original':
                        prepared.scene = scene
                    return prepared

                with variant(factories[mode][0] if mode != 'original' else resident._make_kernel, states[mode]), patch.object(
                    raytracer, '_build_gpu_resident_wavefront_context', new=context
                ):
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
                    if not compiled.fused:
                        record['last_batch_active_counts'] = compiled.last_active_counts[:compiled.last_depth_count + 1].copy_to_host().tolist()
                    record['last_batch_replay_counts'] = compiled.last_replay_counts[:compiled.last_depth_count + 1].copy_to_host().tolist()
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays, 'scene_preparation_sec': prepare_sec,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and all(hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() == value for name, value in dependency_hashes.items())
    report = {'contract': 'primary_identity_compacted_canonical_frontier_probe_v1',
        'delivery': {'type': 'source_checkout', 'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'dependency_hashes': dependency_hashes,
        'factory_sha256': {mode: values[1] for mode, values in factories.items()},
        'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
