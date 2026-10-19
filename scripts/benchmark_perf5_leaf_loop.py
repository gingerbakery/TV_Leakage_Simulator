from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def tiled_factory(width):
    source = inspect.getsource(resident._make_kernel)
    if not width:
        return resident._make_kernel, hashlib.sha256(source.encode()).hexdigest()
    tree = ast.parse(source)

    class LeafLoop(ast.NodeTransformer):
        replacements = 0

        def visit_For(self, node):
            if not isinstance(node.target, ast.Name) or node.target.id != 'ordered_index':
                return self.generic_visit(node)
            if ast.unparse(node.iter) != 'range(start, end)':
                raise ValueError('Unexpected leaf iteration contract')
            replacement = ast.parse(f'''
for leaf_chunk_start in range(start, end, {width}):
    for leaf_chunk_offset in range({width}):
        ordered_index = leaf_chunk_start + leaf_chunk_offset
        if ordered_index >= end:
            break
''').body[0]
            replacement.body[0].body.extend(copy.deepcopy(node.body))
            self.replacements += 1
            return ast.copy_location(replacement, node)

    transform = LeafLoop()
    tree = ast.fix_missing_locations(transform.visit(tree))
    if transform.replacements != 1:
        raise ValueError('Exactly one unchanged-order leaf loop must be tiled')
    namespace = {}
    exec(compile(tree, f'<perf5_leaf_loop_{width}>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


@contextmanager
def variant(factory, state):
    names = ('_KERNEL', '_KERNEL_COMPILED', '_WORKSPACES')
    saved = {name: getattr(resident, name) for name in names}
    for name in names:
        if name not in state:
            state[name] = threading.local() if name == '_WORKSPACES' else False if name == '_KERNEL_COMPILED' else None
        setattr(resident, name, state[name])
    try:
        with patch.object(resident, '_make_kernel', new=factory), patch.dict(os.environ, {'LEAKAGE_GPU_PAIR_BVH': 'off'}):
            yield
    finally:
        for name, value in saved.items():
            state[name] = getattr(resident, name)
            setattr(resident, name, value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--widths', type=int, nargs='+', default=[0, 2, 4, 8])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    trace_input, _ = cad_case(args.rays)
    trace_input.config.compute_backend = 'gpu_cuda'
    factories = {width: tiled_factory(width) for width in args.widths}
    caches = {width: {} for width in args.widths}
    records = {width: {'warm_sec': [], 'parity': [], 'factory_sha256': factories[width][1]} for width in args.widths}
    expected = None
    for repeat in range(args.repeats + 1):
        widths = args.widths if repeat % 2 == 0 else list(reversed(args.widths))
        for width in widths:
            with variant(factories[width][0], caches[width]):
                started = time.perf_counter()
                result = run_direct_ray_trace(trace_input)
                elapsed = time.perf_counter() - started
            payload = semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            performance = result.metrics['_performance_summary']
            if performance['compute_execution_state'] != 'gpu_active' or not performance['gpu_cuda_gpu_success_count'] or performance['gpu_resident_wavefront_fallback_count']:
                raise RuntimeError('Leaf-loop probe did not execute the requested CUDA path')
            records[width]['parity'].append(parity)
            if repeat:
                records[width]['warm_sec'].append(elapsed)
            else:
                records[width]['first_sec'] = elapsed
            records[width]['performance'] = performance
            print(json.dumps({'width': width, 'repeat': repeat, 'sec': elapsed, 'passed': parity['passed']}), flush=True)
    for record in records.values():
        record['median_sec'] = statistics.median(record['warm_sec'])
    unchanged = hashes == source_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'unchanged_bvh_leaf_order_tiling_probe_v1', 'source_hashes': hashes,
        'source_unchanged': unchanged, 'records': records,
        'passed': unchanged and all(parity['passed'] for record in records.values() for parity in record['parity'])}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
