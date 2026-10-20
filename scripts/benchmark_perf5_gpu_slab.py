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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def slab_factory(*, full=False):
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    box = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry')
    general = copy.deepcopy(box)
    general.name = 'general_ray_box_entry'
    names = ', '.join(argument.arg for argument in box.args.args)
    box.args.args.append(ast.arg(arg='ray_nonparallel'))
    box.body = ast.parse(f'''
if not ray_nonparallel:
    return general_ray_box_entry({names})
entry = minimum_t
exit_distance = maximum_t
''').body
    for axis, coordinate in enumerate(('x', 'y', 'z')):
        box.body.extend(ast.parse(f'''
axis_lower = (bounds_min[node_index, {axis}] - origin_{coordinate}) * inverse_{coordinate}
axis_upper = (bounds_max[node_index, {axis}] - origin_{coordinate}) * inverse_{coordinate}
axis_entry = axis_lower if inverse_{coordinate} > 0.0 else axis_upper
axis_exit = axis_upper if inverse_{coordinate} > 0.0 else axis_lower
if axis_entry > entry:
    entry = axis_entry
if axis_exit < exit_distance:
    exit_distance = axis_exit
''').body)
        if not full:
            box.body.extend(ast.parse('if exit_distance < entry:\n    return math.inf').body)
    if full:
        box.body.extend(ast.parse('if exit_distance < entry:\n    return math.inf').body)
    box.body.append(ast.parse('return entry').body[0])
    factory.body.insert(factory.body.index(box), general)
    traversal = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    traversal.body.insert(0, ast.parse('ray_nonparallel = abs(direction_x) >= 1e-12 and abs(direction_y) >= 1e-12 and abs(direction_z) >= 1e-12').body[0])
    replaced = 0
    for node in ast.walk(traversal):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == 'ray_box_entry':
            node.args.append(ast.Name(id='ray_nonparallel', ctx=ast.Load()))
            replaced += 1
    if replaced != 3:
        raise ValueError('Root and both child box queries must use the unchanged ray classification')
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_same_order_nonparallel_slabs>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    factories = {'original': (resident._make_kernel, None), 'slab_early': slab_factory(), 'slab_full': slab_factory(full=True)}
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
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}')
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                record['kernel_resources'] = kernel_resources(states[mode]['_KERNEL'])
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
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_order_nonparallel_slab_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash,
        'factory_sha256': {mode: values[1] for mode, values in factories.items()}, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
