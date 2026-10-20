from __future__ import annotations

import argparse
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
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf5_gpu_event_window import absorber_case, cavity_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--deep-rays', type=int, default=512)
    parser.add_argument('--filled-rays', type=int, default=10000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.deep_rays, args.filled_rays, args.cad_rays) < 1:
        parser.error('Positive counts and at least two warm runs are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    fixture_path = ROOT / 'tests' / 'test_perf5_gpu_event_window.py'
    fixture_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scenes = []
    for depth in (100, 1000):
        trace_input = absorber_case(args.rays)
        trace_input.config.max_depth = depth
        scenes.append((f'absorber_cap_{depth}', trace_input, args.rays))
    scenes.append(('filled_depth_80', cavity_case(args.filled_rays, 80), args.filled_rays * 81))
    scenes.append(('filled_depth_1000', cavity_case(args.deep_rays), args.deep_rays * 1001))
    scenes.append(('late_deep_emitter', cavity_case(args.deep_rays, 1000, late_emitter=True), args.deep_rays * 1002))
    cad, _ = cad_case(args.cad_rays)
    cad.config.compute_backend = 'gpu_cuda'
    cad.config.max_depth = 1000
    scenes.append(('tv_corner_cad_cap1000', cad, None))
    caches = {mode: threading.local() for mode in ('off', 'auto')}
    cases = []
    for name, trace_input, expected_hits in scenes:
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': []} for mode in caches}
        expected = None
        for repeat in range(args.repeats + 1):
            order = tuple(records) if repeat % 2 == 0 else tuple(reversed(records))
            for mode in order:
                with patch.dict(os.environ, {'LEAKAGE_GPU_EVENT_WINDOW': mode}), patch.object(resident, '_WORKSPACES', caches[mode]):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count']:
                    raise RuntimeError('GPU event window benchmark used fallback')
                if expected_hits is not None and result.surface_hit_count != expected_hits:
                    raise RuntimeError('Event window lost a required surface event')
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                record['surface_hits'] = result.surface_hit_count
                record['receiver_hits'] = result.receiver_hit_count
                record['rays'] = result.total_rays
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        case = {'scene': name, 'max_depth': trace_input.config.max_depth, 'records': records,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
            'speedup': records['off']['median_sec'] / records['auto']['median_sec']}
        cases.append(case)
        print(json.dumps({'scene': name, 'speedup': case['speedup'],
            'off_sec': records['off']['median_sec'], 'auto_sec': records['auto']['median_sec'],
            'retries': records['auto']['performance']['gpu_event_window_retry_count']}), flush=True)
    unchanged = hashes == source_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and fixture_hash == hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    report = {'contract': 'same_source_bounded_gpu_event_window_paired_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': script_hash, 'fixture_sha256': fixture_hash,
        'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
