from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, replace
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

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def ordered_factory():
    source = inspect.getsource(resident._make_kernel)
    tree = ast.parse(source)

    class Reorder(ast.NodeTransformer):
        replacements = 0

        def visit_Subscript(self, node):
            if isinstance(node.value, ast.Name) and node.value.id in ('triangle_v0', 'triangle_edge1', 'triangle_edge2'):
                if not isinstance(node.slice, ast.Tuple) or ast.unparse(node.slice.elts[0]) != 'face_index':
                    raise ValueError('Unexpected triangle geometry indexing contract')
                node.slice.elts[0] = ast.copy_location(ast.Name(id='ordered_index', ctx=ast.Load()), node.slice.elts[0])
                self.replacements += 1
            return self.generic_visit(node)

    transform = Reorder()
    transformed = ast.fix_missing_locations(transform.visit(tree))
    if transform.replacements != 9:
        raise ValueError('Exactly nine geometry coordinate loads must be reordered')
    namespace = {}
    exec(compile(transformed, '<perf5_ordered_geometry>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(transformed).encode()).hexdigest()


def device_scene_loader(mode, cache):
    original = gpu._ensure_device_scene

    def ensure(scene, capability):
        identity = id(scene)
        if identity in cache:
            return cache[identity], True
        device, reused = original(scene, capability)
        if mode == 'original':
            return device, reused
        started = time.perf_counter()
        values = {}
        if mode in ('ordered', 'ordered_nodes', 'packed_all'):
            geometry = tuple(np.ascontiguousarray(getattr(scene, name)[scene.ordered_faces])
                for name in ('triangle_v0', 'triangle_edge1', 'triangle_edge2'))
            if mode == 'packed_all':
                packed = gpu._CUDA.to_device(np.concatenate(geometry, axis=1))
                values.update(triangle_v0=packed[:, :3], triangle_edge1=packed[:, 3:6], triangle_edge2=packed[:, 6:9])
            else:
                values.update((name, gpu._CUDA.to_device(array)) for name, array in zip(
                    ('triangle_v0', 'triangle_edge1', 'triangle_edge2'), geometry))
        if mode in ('nodes', 'ordered_nodes', 'packed_all'):
            packed = np.empty((len(scene.node_left), 10), dtype=np.float64)
            packed[:, :3] = scene.node_bounds_min
            packed[:, 3:6] = scene.node_bounds_max
            integers = packed.view(np.int64)
            for offset, name in enumerate(('node_left', 'node_right', 'node_start', 'node_count'), 6):
                integers[:, offset] = getattr(scene, name)
            uploaded = gpu._CUDA.to_device(packed)
            integers = uploaded.view(np.int64)
            values.update(node_bounds_min=uploaded[:, :3], node_bounds_max=uploaded[:, 3:6],
                node_left=integers[:, 6], node_right=integers[:, 7], node_start=integers[:, 8], node_count=integers[:, 9])
        gpu._CUDA.synchronize()
        candidate = replace(device, **values, upload_sec=device.upload_sec + time.perf_counter() - started)
        cache[identity] = candidate
        return candidate, False

    return ensure


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--modes', nargs='+', choices=('original', 'ordered', 'nodes', 'ordered_nodes', 'packed_all'),
        default=['original', 'ordered', 'nodes', 'ordered_nodes', 'packed_all'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or args.rays < 1 or 'original' not in args.modes:
        parser.error('Positive Ray count, two warm repeats and original mode are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    trace_input, _ = cad_case(args.rays)
    trace_input.config.compute_backend = 'gpu_cuda'
    factory, factory_hash = ordered_factory()
    states = {mode: {} for mode in args.modes}
    devices = {mode: {} for mode in args.modes}
    records = {mode: {'warm_sec': [], 'parity': []} for mode in args.modes}
    expected = None
    for repeat in range(args.repeats + 1):
        order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
        for mode in order:
            candidate_factory = factory if mode in ('ordered', 'ordered_nodes', 'packed_all') else resident._make_kernel
            loader = device_scene_loader(mode, devices[mode])
            with variant(candidate_factory, states[mode]), patch.object(gpu, '_ensure_device_scene', new=loader):
                started = time.perf_counter()
                result = run_direct_ray_trace(trace_input, wavefront_residency='gpu_resident')
                elapsed = time.perf_counter() - started
            payload = semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            performance = result.metrics['_performance_summary']
            if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                raise RuntimeError(f'Full CUDA execution not proven: {mode}')
            record = records[mode]
            record['parity'].append(parity)
            record['performance'] = performance
            if repeat:
                record['warm_sec'].append(elapsed)
            else:
                record['first_sec'] = elapsed
            print(json.dumps({'mode': mode, 'repeat': repeat, 'sec': elapsed, 'passed': parity['passed']}), flush=True)
    for record in records.values():
        record['median_sec'] = statistics.median(record['warm_sec'])
    for record in records.values():
        record['speedup'] = records['original']['median_sec'] / record['median_sec']
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_bvh_order_exact_scene_layout_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'factory_sha256': factory_hash,
        'source_unchanged': unchanged, 'rays': args.rays, 'emitter_types': ['datum_plane'], 'records': records,
        'passed': unchanged and all(parity['passed'] for record in records.values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
