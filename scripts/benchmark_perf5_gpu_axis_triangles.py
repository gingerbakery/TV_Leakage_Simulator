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


def axis_triangle_factory():
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    traversal = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    loops = [node for node in ast.walk(traversal) if isinstance(node, ast.For)
        and ast.unparse(node.target) == 'ordered_index' and ast.unparse(node.iter) == 'range(start, end)']
    if len(loops) != 1:
        raise ValueError('Exactly one original ordered triangle loop must be specialized')
    loop = loops[0]
    math_start = next(index for index, node in enumerate(loop.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'cross_x')
    original_math = copy.deepcopy(loop.body[math_start:])

    class ZeroEdges(ast.NodeTransformer):
        def __init__(self, axis):
            self.names = {f'edge1_{axis}', f'edge2_{axis}'}

        def visit_Name(self, node):
            if node.id in self.names and isinstance(node.ctx, ast.Load):
                return ast.copy_location(ast.Constant(0.0), node)
            return node

        def visit_BinOp(self, node):
            self.generic_visit(node)
            if isinstance(node.op, ast.Mult) and any(isinstance(value, ast.Constant) and value.value == 0.0 for value in (node.left, node.right)):
                return ast.copy_location(ast.Constant(0.0), node)
            return node

    fallback = original_math
    for axis in reversed(('z', 'x', 'y')):
        branch = ast.parse(f'if axis_simplification_safe and edge1_{axis} == 0.0 and edge2_{axis} == 0.0:\n    pass').body[0]
        transform = ZeroEdges(axis)
        branch.body = [transform.visit(statement) for statement in copy.deepcopy(original_math)]
        branch.orelse = fallback
        fallback = [branch]
    loop.body[math_start:] = fallback
    root_test = next(index for index, node in enumerate(traversal.body)
        if isinstance(node, ast.If) and ast.unparse(node.test) == 'math.isinf(root_entry)')
    traversal.body[root_test + 1:root_test + 1] = ast.parse('''
axis_simplification_safe = (abs(origin_x) <= 1e90 and abs(origin_y) <= 1e90 and abs(origin_z) <= 1e90
    and abs(direction_x) <= 2.0 and abs(direction_y) <= 2.0 and abs(direction_z) <= 2.0)
for axis_index in range(3):
    if abs(node_bounds_min[0, axis_index]) > 1e90 or abs(node_bounds_max[0, axis_index]) > 1e90:
        axis_simplification_safe = False
''').body
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_same_order_axis_triangle_math>', 'exec'), resident.__dict__, namespace)
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
        parser.error('Positive ray counts and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    candidate, fingerprint = axis_triangle_factory()
    states = {mode: {} for mode in ('original', 'axis')}
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
                with variant(candidate if mode == 'axis' else resident._make_kernel, states[mode]):
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
        records['axis']['speedup'] = records['original']['median_sec'] / records['axis']['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_order_axis_aligned_triangle_math_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'factory_sha256': fingerprint,
        'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
