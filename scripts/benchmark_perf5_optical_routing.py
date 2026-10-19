from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--depth-rays', type=int, default=100000)
    parser.add_argument('--depth-caps', type=int, nargs='*', default=[20, 100, 1000])
    parser.add_argument('--scenes', nargs='*', choices=tuple(WORKLOADS), default=list(WORKLOADS))
    parser.add_argument('--backends', nargs='+', choices=('cpu', 'gpu_cuda'), default=['cpu', 'gpu_cuda'])
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays) < 1 or args.repeats < 2:
        parser.error('Positive ray counts and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scenes = [(name, WORKLOADS[name](args.rays)) for name in args.scenes]
    scenes.append(('cad', cad_case(args.cad_rays)[0]))
    for depth in args.depth_caps:
        trace_input = build_depth_ten_case(args.depth_rays)
        trace_input.config.max_depth = depth
        trace_input.config.angle_dependent_reflectance = False
        trace_input.optical_profiles[0].reflectance = 0.0
        scenes.append((f'absorber_cap_{depth}', trace_input))
    cases = []
    for name, trace_input in scenes:
        trace_input.mesh.prepare_acceleration()
        expected = {}
        records = {}
        for backend in args.backends:
            trace_input.config.compute_backend = backend
            measurements = {mode: {'warm_sec': [], 'optical_sec': [], 'parity': []} for mode in ('off', 'auto')}
            for repeat in range(args.repeats + 1):
                for mode in measurements if repeat % 2 == 0 else tuple(reversed(measurements)):
                    with patch.dict(os.environ, {'LEAKAGE_OPTICAL_ROUTING_CACHE': mode}):
                        started = time.perf_counter()
                        result = run_direct_ray_trace(trace_input)
                        elapsed = time.perf_counter() - started
                    performance = result.metrics['_performance_summary']
                    if backend == 'gpu_cuda' and (performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']):
                        raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}')
                    if performance['intersection_fallback_count'] or performance['wavefront_reducer_fallback_count']:
                        raise RuntimeError(f'Unexpected fallback: {name}/{mode}/{backend}')
                    payload = semantic_payload(result)
                    if backend not in expected:
                        expected[backend] = payload
                    parity = compare_semantic_payloads(expected[backend], payload, absolute_tolerance=1e-9,
                        relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                    if not parity['passed'] or (backend == 'cpu' and not parity['semantic_exact']):
                        raise RuntimeError(f'Optical routing changed transport: {name}/{mode}/{backend}: {parity}')
                    record = measurements[mode]
                    record['parity'].append(parity)
                    record['performance'] = performance
                    if repeat:
                        record['warm_sec'].append(elapsed)
                        record['optical_sec'].append(performance['optical_resolution_sec'])
                    else:
                        record['first_sec'] = elapsed
                    print(json.dumps({'scene': name, 'backend': backend, 'mode': mode, 'repeat': repeat,
                        'seconds': elapsed, 'optical_sec': performance['optical_resolution_sec'], 'passed': parity['passed']}), flush=True)
            for record in measurements.values():
                record['median_sec'] = statistics.median(record['warm_sec'])
                record['optical_median_sec'] = statistics.median(record['optical_sec'])
            measurements['auto']['speedup'] = measurements['off']['median_sec'] / measurements['auto']['median_sec']
            records[backend] = measurements
        parity = None
        if len(expected) == 2:
            parity = compare_semantic_payloads(expected['cpu'], expected['gpu_cuda'], absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            if not parity['passed']:
                raise RuntimeError(f'CPU/GPU transport mismatch: {name}: {parity}')
        cached = getattr(trace_input.mesh, '_optical_routing_cache', None)
        cases.append({'scene': name, 'rays': result.total_rays, 'triangles': len(trace_input.mesh.faces),
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
            'cached_metadata_count': len(cached.metadata_refs) if cached is not None else 0,
            'cache_array_bytes': cached.metadata_ids.nbytes + cached.slots.nbytes if cached is not None else 0,
            'records': records, 'cpu_gpu_parity': parity})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'mutation_guarded_optical_routing_same_session_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'delivery': {'type': 'source_checkout', 'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged,
        'cpu_workers_override': os.environ.get('LEAKAGE_CPU_WORKERS'), 'cases': cases, 'passed': unchanged}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
