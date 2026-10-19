from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace

MODES = ('original', 'workers4096', 'workers8192', 'workers16384', 'outlined8192')
STATE = ('origin_x', 'origin_y', 'origin_z', 'direction_x', 'direction_y', 'direction_z',
    'current_power', 'current_source_face', 'current_ray_kind')


def persistent_factory(*, outlined=False):
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    kernel = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    arguments = [argument.arg for argument in kernel.args.args]
    depth_loop = next(node for node in kernel.body
        if isinstance(node, ast.For) and ast.unparse(node.iter) == 'range(depth_count)')
    if [ast.unparse(node) for node in kernel.body[:2]] != ['ray_index = cuda.grid(1)', 'if ray_index >= ray_count:\n    return']:
        raise ValueError('Original primary identity and bounds checks are required')
    initialization = copy.deepcopy(kernel.body[2:kernel.body.index(depth_loop)])
    step = copy.deepcopy(kernel)
    step.name = 'advance_persistent_ray'
    step.args.args = [ast.arg(arg=name) for name in ('ray_index', 'depth', *STATE, 'key', *arguments)]
    step.decorator_list = ast.parse('@cuda.jit(device=True, inline=True, fastmath=False)\ndef placeholder():\n    pass').body[0].decorator_list
    if outlined:
        next(keyword for keyword in step.decorator_list[0].keywords if keyword.arg == 'inline').value = ast.Constant(False)
    step.body = copy.deepcopy(depth_loop.body)

    class Terminal(ast.NodeTransformer):
        replaced = 0

        def visit_Return(self, node):
            if node.value is not None:
                raise ValueError('Transport terminal returns must not carry an existing value')
            self.replaced += 1
            return ast.copy_location(ast.Return(value=ast.Tuple(elts=[ast.Constant(False),
                *(ast.Name(id=name, ctx=ast.Load()) for name in STATE)], ctx=ast.Load())), node)

    terminals = Terminal()
    step = terminals.visit(step)
    if terminals.replaced < 5:
        raise ValueError('All transport terminal branches must return worker state')
    step.body.append(ast.Return(value=ast.Tuple(elts=[ast.Constant(True),
        *(ast.Name(id=name, ctx=ast.Load()) for name in STATE)], ctx=ast.Load())))
    traversal = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    stacks = 0
    for statement in traversal.body:
        if isinstance(statement, ast.Assign) and ast.unparse(statement) == 'base = ray_index':
            statement.value = ast.parse('cuda.grid(1)', mode='eval').body
            stacks += 1
    if stacks != 1:
        raise ValueError('Persistent stack ownership must remain bound to the physical thread')
    kernel.args.args.append(ast.arg(arg='primary_queue'))
    kernel.body = ast.parse('''
ray_index = cuda.grid(1)
lane = cuda.threadIdx.x & 31
active = ray_index < ray_count
needs_initialization = active
depth = 0
origin_x = 0.0
origin_y = 0.0
origin_z = 0.0
direction_x = 0.0
direction_y = 0.0
direction_z = 0.0
current_power = 0.0
current_source_face = -1
current_ray_kind = RAY_KIND_DIRECT
key = np.uint64(0)
while True:
    if needs_initialization:
        pass
    if active:
        pass
    needs_initialization = False
    available = cuda.ballot_sync(0xffffffff, not active)
    has_work = 0
    if lane == 0:
        has_work = int(primary_queue[0] < ray_count)
    has_work = cuda.shfl_sync(0xffffffff, has_work, 0)
    if available != 0 and has_work:
        first = 0
        if lane == 0:
            first = cuda.atomic.add(primary_queue, 0, cuda.popc(available))
        first = cuda.shfl_sync(0xffffffff, first, 0)
        if not active:
            earlier = (np.uint32(1) << np.uint32(lane)) - np.uint32(1)
            rank = cuda.popc(available & earlier)
            ray_index = first + rank
            active = ray_index < ray_count
            needs_initialization = active
            depth = 0
    if not cuda.any_sync(0xffffffff, active):
        return
''').body
    loop = kernel.body[-1]
    loop.body[0].body = initialization
    loop.body[1].body = ast.parse(f'''
active, {', '.join(STATE)} = advance_persistent_ray(ray_index, depth,
    {', '.join(STATE)}, key, {', '.join(arguments)})
if active:
    depth += 1
''').body
    factory.body.insert(factory.body.index(kernel), step)
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_primary_identity_persistent_workers>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class PersistentLaunch:
    def __init__(self, kernel, queue, blocks):
        self.kernel = kernel
        self.queue = queue
        self.blocks = blocks

    def __getitem__(self, dimensions):
        configured = self.kernel[self.blocks, dimensions[1]]

        def launch(*arguments):
            return configured(*arguments, self.queue)

        return launch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays) < 1 or 'original' not in args.modes:
        parser.error('Positive rays, two warm repeats and an original reference are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    factories = {mode: (resident._make_kernel, None) if mode == 'original' else persistent_factory(outlined=mode.startswith('outlined')) for mode in args.modes}
    states = {mode: {} for mode in args.modes}
    original_launch = resident._launch_resident_kernel
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': [], 'queue_sec': []} for mode in args.modes}
        expected = None
        for repeat in range(args.repeats + 1):
            order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
            for mode in order:
                queue_seconds = 0.0

                def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
                    nonlocal queue_seconds
                    if mode == 'original':
                        return original_launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords)
                    workers = int(''.join(character for character in mode if character.isdigit()))
                    blocks = (min(count, workers) + resident.THREADS_PER_BLOCK - 1) // resident.THREADS_PER_BLOCK
                    started = time.perf_counter()
                    queue = gpu._CUDA.to_device(np.asarray([blocks * resident.THREADS_PER_BLOCK], dtype=np.int64))
                    queue_seconds += time.perf_counter() - started
                    return original_launch(PersistentLaunch(kernel, queue, blocks), context, scene, bindings,
                        workspace, batch, count, depths, **keywords)

                with variant(factories[mode][0], states[mode]), patch.object(resident, '_launch_resident_kernel', new=launch):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}')
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                record['kernel_resources'] = kernel_resources(states[mode]['_KERNEL'])
                if repeat:
                    record['warm_sec'].append(elapsed)
                    record['queue_sec'].append(queue_seconds)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'queue_sec': queue_seconds, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['queue_median_sec'] = statistics.median(record['queue_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [value.emitter_type for value in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'primary_identity_warp_refill_persistent_workers_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
