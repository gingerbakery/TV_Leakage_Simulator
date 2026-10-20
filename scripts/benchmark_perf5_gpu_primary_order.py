from __future__ import annotations

import argparse
import ast
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
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace

MODES = ('original', 'direction_origin', 'origin_direction', 'octant_origin')


def morton(cells):
    result = np.zeros(len(cells), dtype=np.uint64)
    for bit in range(4):
        for axis in range(3):
            result |= ((cells[:, axis] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(bit * 3 + axis)
    return result


def primary_order(origins, directions, mode):
    if not len(origins) or mode == 'original':
        return np.arange(len(origins), dtype=np.int64)
    minimum = origins.min(axis=0)
    span = origins.max(axis=0) - minimum
    span[span == 0.0] = 1.0
    origins_key = morton(np.clip((origins - minimum) * (15.0 / span), 0, 15).astype(np.uint64))
    directions_key = morton(np.clip((directions + 1.0) * 7.5, 0, 15).astype(np.uint64))
    if mode == 'direction_origin':
        keys = (directions_key << np.uint64(12)) | origins_key
    elif mode == 'origin_direction':
        keys = (origins_key << np.uint64(12)) | directions_key
    elif mode == 'octant_origin':
        octants = np.sum((directions < 0.0).astype(np.uint64) * np.asarray([1, 2, 4], dtype=np.uint64), axis=1)
        keys = (octants << np.uint64(24)) | (origins_key << np.uint64(12)) | directions_key
    else:
        raise ValueError('Unsupported primary scheduling order')
    return np.argsort(keys, kind='stable').astype(np.int64, copy=False)


def ordered_factory():
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    kernel = next(node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    kernel.args.args.append(ast.arg(arg='primary_order'))
    replaced = 0
    for index, statement in enumerate(kernel.body):
        if isinstance(statement, ast.Assign) and ast.unparse(statement) == 'ray_index = cuda.grid(1)':
            kernel.body[index:index + 1] = ast.parse('''
launch_index = cuda.grid(1)
if launch_index >= ray_count:
    return
ray_index = primary_order[launch_index]
''').body
            replaced += 1
            break
    traversal = next(node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    for statement in traversal.body:
        if isinstance(statement, ast.Assign) and ast.unparse(statement) == 'base = ray_index':
            statement.value = ast.parse('cuda.grid(1)', mode='eval').body
            replaced += 1
    if replaced != 2:
        raise ValueError('Both primary output identity and thread-local stack addressing must be preserved')
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_primary_scheduling>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class ScheduledKernel:
    def __init__(self, kernel, order):
        self.kernel = kernel
        self.order = order

    def __getitem__(self, dimensions):
        configured = self.kernel[dimensions]

        def launch(*arguments):
            return configured(*arguments, self.order)

        return launch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or args.rays < 1 or 'original' not in args.modes:
        parser.error('Positive rays, at least two warm repeats and an original reference are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    factory, factory_hash = ordered_factory()
    states = {mode: {} for mode in args.modes}
    original_launch = resident._launch_resident_kernel
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': [], 'schedule_sec': []} for mode in args.modes}
        expected = None
        for repeat in range(args.repeats + 1):
            order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
            for mode in order:
                schedule_seconds = 0.0

                def launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords):
                    nonlocal schedule_seconds
                    if mode == 'original':
                        return original_launch(kernel, context, scene, bindings, workspace, batch, count, depths, **keywords)
                    started = time.perf_counter()
                    permutation = primary_order(batch.origins[:count], batch.directions[:count], mode)
                    device_order = gpu._CUDA.to_device(permutation)
                    schedule_seconds += time.perf_counter() - started
                    return original_launch(ScheduledKernel(kernel, device_order), context, scene, bindings,
                        workspace, batch, count, depths, **keywords)

                with variant(resident._make_kernel if mode == 'original' else factory, states[mode]), patch.object(resident, '_launch_resident_kernel', new=launch):
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
                if repeat:
                    record['warm_sec'].append(elapsed)
                    record['schedule_sec'].append(schedule_seconds)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'schedule_sec': schedule_seconds, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['schedule_median_sec'] = statistics.median(record['schedule_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [value.emitter_type for value in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'original_primary_identity_spatial_scheduling_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'factory_sha256': factory_hash,
        'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
