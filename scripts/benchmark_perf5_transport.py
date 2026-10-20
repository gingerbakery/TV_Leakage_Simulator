from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
import hashlib
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

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from perf4_accuracy import compare_semantic_payloads
from diagnose_perf5_gpu_roundoff import legacy_functions
from perf5_cpu_reference import counter_reference, load_counter_factory
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator.raytracer import run_direct_ray_trace


def semantic_payload(result):
    payload = result.to_dict()
    payload.pop('run_id', None)
    payload.pop('runtime_sec', None)
    payload['metrics'].pop('_performance_summary', None)
    payload['config'].pop('compute_backend', None)
    return payload


def transport_hashes():
    hashes = source_hashes()
    for path in (Path(__file__), ROOT / 'scripts' / 'diagnose_perf5_gpu_roundoff.py', ROOT / 'scripts' / 'perf5_cpu_reference.py'):
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


@contextmanager
def runtime_variant(variant, replacements, caches, backend, gpu_summary_partitions='auto', cpu_reference_factory=None):
    saved = {}
    with ExitStack() as context:
        context.enter_context(patch.dict(os.environ, {
            'LEAKAGE_NATIVE_SAMPLING': 'off' if variant == 'reference' else 'auto',
            'LEAKAGE_PRIMARY_PREFETCH': 'off' if variant == 'reference' else os.environ.get('LEAKAGE_PRIMARY_PREFETCH', 'auto'),
            'LEAKAGE_OPTICAL_TABLES': 'off' if variant == 'reference' else os.environ.get('LEAKAGE_OPTICAL_TABLES', 'auto'),
            'LEAKAGE_GPU_EVENT_WINDOW': 'off' if variant == 'reference' else os.environ.get('LEAKAGE_GPU_EVENT_WINDOW', 'auto'),
        }))
        if backend == 'cpu':
            context.enter_context(patch.dict(os.environ, {
                'LEAKAGE_CPU_TRANSPORT': 'wavefront' if variant == 'reference' else 'auto',
            }))
            if variant == 'reference' and cpu_reference_factory is not None:
                context.enter_context(counter_reference(cpu_reference_factory, caches.setdefault('cpu_reference_state', {})))
        if variant == 'reference' and backend == 'gpu_cuda':
            for owner, name, function in replacements:
                context.enter_context(patch.object(owner, name, function))
        if backend == 'gpu_cuda':
            context.enter_context(patch.dict(os.environ, {
                'LEAKAGE_GPU_SUMMARY_PARTITIONS': '1' if variant == 'reference' else gpu_summary_partitions,
                'LEAKAGE_GPU_EMITTED_FLUX': 'atomic' if variant == 'reference' else os.environ.get('LEAKAGE_GPU_EMITTED_FLUX', 'ordered'),
                'LEAKAGE_GPU_EMITTED_UNIFORM': 'off' if variant == 'reference' else os.environ.get('LEAKAGE_GPU_EMITTED_UNIFORM', 'auto'),
            }))
            state = caches.setdefault(variant, {})
            for module, names in (
                (resident, ('_KERNEL', '_KERNEL_COMPILED', '_PATH_SELECT_KERNEL', '_PATH_GATHER_KERNEL', '_WORKSPACES')),
                (summary, ('_KERNEL', '_KERNEL_COMPILED', '_PROFILE_FACE_KERNEL')),
            ):
                for name in names:
                    key = (module, name)
                    saved[key] = getattr(module, name)
                    if key not in state:
                        state[key] = threading.local() if name == '_WORKSPACES' else False if name == '_KERNEL_COMPILED' else None
                    setattr(module, name, state[key])
        try:
            yield
        finally:
            for (module, name), value in saved.items():
                caches[variant][module, name] = getattr(module, name)
                setattr(module, name, value)


def benchmark(args):
    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified'):
        if getattr(preflight, name) is not True:
            raise RuntimeError(f'Production GPU preflight failed: {name}')
    if preflight.preflight_scope != 'production_ray_bvh' or gpu_cuda.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production GPU preflight contract mismatch')
    hashes = transport_hashes()
    replacements = legacy_functions(args.reference_revision) if args.reference_revision else []
    cpu_reference_factory = load_counter_factory(args.reference_revision, ROOT) if args.reference_revision and 'cpu' in args.backends else None
    caches = {}
    scenes = []
    for name, builder in WORKLOADS.items():
        if name not in args.scenes:
            continue
        trace_input = builder(args.rays)
        trace_input.mesh.prepare_acceleration()
        scenes.append((name, trace_input))
    if not args.skip_cad:
        trace_input, _ = cad_case(args.cad_rays)
        scenes.append(('tv_corner_cad_probe', trace_input))
    for depth in args.depth_caps:
        trace_input = build_depth_ten_case(args.depth_rays)
        trace_input.config.max_depth = depth
        trace_input.optical_profiles[0].reflectance = 0.0
        trace_input.config.angle_dependent_reflectance = False
        trace_input.mesh.prepare_acceleration()
        scenes.append((f'absorber_cap_{depth}', trace_input))
    if not scenes:
        raise ValueError('At least one benchmark workload is required')
    baseline = {}
    if args.baseline:
        source = json.loads(args.baseline.read_text(encoding='utf-8'))
        baseline = {case['name']: case for case in source['cases']}
    cases = []
    for name, trace_input in scenes:
        settings = [(variant, backend) for variant in (('reference', 'candidate') if args.reference_revision else ('candidate',))
                    for backend in args.backends]
        records = {backend + ('_reference' if variant == 'reference' else ''): {'warm_sec': []}
                   for variant, backend in settings}
        for repeat in range(args.repeats + 1):
            order = settings if repeat % 2 == 0 else list(reversed(settings))
            for variant, backend in order:
                tag = backend + ('_reference' if variant == 'reference' else '')
                trace_input.config.compute_backend = backend
                with runtime_variant(variant, replacements, caches, backend, args.gpu_summary_partitions, cpu_reference_factory):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if variant == 'candidate' and performance.get('native_primary_preparation_fallback_batches', 0):
                    raise RuntimeError(f'Unexpected native primary preparation fallback: {name}')
                if backend == 'gpu_cuda' and not (
                    performance['compute_execution_state'] in ('gpu_active', 'gpu_mixed')
                    and performance['gpu_cuda_gpu_success_count'] > 0
                    and performance['gpu_resident_wavefront_fallback_count'] == 0
                ):
                    raise RuntimeError(f'CUDA execution not proven: {name}')
                if performance['intersection_fallback_count'] or performance['wavefront_reducer_fallback_count']:
                    raise RuntimeError(f'Unexpected fallback: {name}, {backend}')
                payload = semantic_payload(result)
                record = records[tag]
                if 'payload' in record:
                    parity = compare_semantic_payloads(record['payload'], payload,
                        absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                    if not parity.passed:
                        raise RuntimeError(f'Repeatability failed: {name}, {backend}: {parity.to_dict()}')
                record['payload'] = payload
                record['performance'] = performance
                if repeat == 0:
                    record['first_run_sec'] = elapsed
                else:
                    record['warm_sec'].append(elapsed)
        for backend, record in records.items():
            record['warm_p50_sec'] = statistics.median(record['warm_sec'])
            if name in baseline and backend in baseline[name]['backends']:
                previous = baseline[name]['backends'][backend]
                parity = compare_semantic_payloads(previous['payload'], record['payload'],
                    absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                record['baseline_parity'] = parity.to_dict()
                if not parity.passed:
                    raise RuntimeError(f'Baseline parity failed: {name}, {backend}: {parity.to_dict()}')
                record['speedup_over_baseline'] = previous['warm_p50_sec'] / record['warm_p50_sec']
            print(f'{name}: {backend}, {record["warm_p50_sec"]:.6f}s', flush=True)
        for backend in args.backends:
            reference = records.get(backend + '_reference')
            if reference is not None:
                candidate = records[backend]
                parity = compare_semantic_payloads(reference['payload'], candidate['payload'],
                    absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                candidate['same_session_reference_parity'] = parity.to_dict()
                candidate['same_session_speedup'] = reference['warm_p50_sec'] / candidate['warm_p50_sec']
                if not parity.passed:
                    raise RuntimeError(f'Same-session reference parity failed: {name}, {backend}')
        parity = None
        cpu_reference = records.get('cpu')
        if cpu_reference is None and name in baseline:
            cpu_reference = baseline[name]['backends'].get('cpu')
        if cpu_reference is not None and 'gpu_cuda' in records:
            parity = compare_semantic_payloads(cpu_reference['payload'], records['gpu_cuda']['payload'],
                absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
            if not parity.passed:
                raise RuntimeError(f'CPU/GPU parity failed: {name}: {parity.to_dict()}')
        cases.append({'name': name, 'rays': result.total_rays,
                      'triangles': len(trace_input.mesh.faces),
                      'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
                      'max_depth': trace_input.config.max_depth, 'backends': records,
                      'cpu_reference_origin': 'current_run' if 'cpu' in records else str(args.baseline),
                      'cpu_gpu_parity': parity.to_dict() if parity else None})
    return {'contract': 'perf5_fixed_transport_v1',
            'preflight': {**asdict(preflight), 'provider_contract': gpu_cuda.PROVIDER_CONTRACT},
            'source_hashes': hashes, 'source_unchanged': hashes == transport_hashes(),
            'reference_revision': args.reference_revision,
            'passed': hashes == transport_hashes(), 'cases': cases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--cad-rays', type=int, default=20000)
    parser.add_argument('--depth-rays', type=int, default=10000)
    parser.add_argument('--depth-caps', type=int, nargs='*', default=[20, 100, 1000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--backends', nargs='+', choices=['cpu', 'gpu_cuda'], default=['cpu', 'gpu_cuda'])
    parser.add_argument('--skip-cad', action='store_true')
    parser.add_argument('--scenes', nargs='*', choices=tuple(WORKLOADS), default=list(WORKLOADS))
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--reference-revision')
    parser.add_argument('--gpu-summary-partitions', default='auto')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays, args.repeats) < 1:
        parser.error('counts must be positive')
    summary = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
