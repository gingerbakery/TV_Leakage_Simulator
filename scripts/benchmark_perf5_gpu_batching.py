from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import threading
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer

SETTINGS = {'original': (65536, 32), 'batch262k': (262144, 32),
    'batch262k_window8': (262144, 8), 'batch524k_window8': (524288, 8),
    'batch131k': (131072, 32), 'batch524k': (524288, 32)}
SCENES = (*WORKLOADS, 'cad', 'absorber_cap_20', 'absorber_cap_100',
    'absorber_cap_1000', 'reflected_receiver_mis')


def coalesce(batches, target):
    pending = []
    count = 0

    def joined():
        if len(pending) == 1:
            return pending[0]
        return (*tuple(np.concatenate([batch[index] for batch in pending], axis=0) for index in range(4)),
            sum(int(batch[4]) for batch in pending))

    for batch in batches:
        pending.append(batch)
        count += len(batch[0])
        if count >= target:
            yield joined()
            pending = []
            count = 0
    if pending:
        yield joined()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--depth-rays', type=int, default=100000)
    parser.add_argument('--scenes', nargs='+', choices=SCENES, default=list(SCENES))
    parser.add_argument('--modes', nargs='+', choices=tuple(SETTINGS),
        default=['original', 'batch262k', 'batch262k_window8', 'batch524k_window8'])
    parser.add_argument('--coalesce', action='store_true')
    parser.add_argument('--budget-aware', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays, args.depth_rays) < 1 or 'original' not in args.modes:
        parser.error('Positive Ray counts, original mode and at least two warm repeats are required')
    if args.budget_aware and not args.coalesce:
        parser.error('Budget-aware packets require canonical packet coalescing')
    settings = {name: SETTINGS[name] for name in args.modes}
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fixture_path = ROOT / 'tests/test_perf4e_bounce_mis.py'
    fixture_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items() if name in args.scenes]
    if 'cad' in args.scenes:
        scenes.append(('tv_corner_cad_probe', cad_case(args.cad_rays)[0]))
    for depth in (20, 100, 1000):
        if f'absorber_cap_{depth}' not in args.scenes:
            continue
        trace_input = build_depth_ten_case(args.depth_rays)
        trace_input.config.max_depth = depth
        trace_input.config.angle_dependent_reflectance = False
        trace_input.optical_profiles[0].reflectance = 0.0
        scenes.append((f'absorber_cap_{depth}', trace_input))
    if 'reflected_receiver_mis' in args.scenes:
        scenes.append(('reflected_receiver_mis', _build_reflected_case(args.rays, 7172, 'receiver_mis', backend='gpu_cuda')))
    workspaces = {name: threading.local() for name in settings}
    original_context = raytracer._build_gpu_resident_wavefront_context
    original_prefetch = raytracer.prefetch_primary_batches
    cases = []
    for name, trace_input in scenes:
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.mesh.prepare_acceleration()
        expected = None
        records = {mode: {'warm_sec': [], 'parity': [], 'packet_targets': []} for mode in settings}
        for repeat in range(args.repeats + 1):
            order = tuple(settings) if repeat % 2 == 0 else tuple(reversed(settings))
            for mode in order:
                batch_size, window = settings[mode]
                context_holder = []

                def build_context(*values, **keywords):
                    context = original_context(*values, **keywords)
                    context._event_depth_hint = window
                    context_holder[:] = [context]
                    return context

                def prefetch(batches, stats, *, enabled):
                    target = batch_size
                    if args.budget_aware:
                        target = resident.event_window_batch_size(context_holder[0], batch_size, trace_input.config.max_depth) if context_holder else 65536
                    records[mode]['packet_targets'].append(target)
                    return original_prefetch(coalesce(batches, target) if args.coalesce else batches, stats, enabled=enabled)

                with patch.object(resident, 'INITIAL_EVENT_WINDOW_DEPTH', window), patch.object(resident, '_WORKSPACES', workspaces[mode]), patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=build_context), patch.object(raytracer, 'prefetch_primary_batches', new=prefetch):
                    started = time.perf_counter()
                    result = raytracer.run_direct_ray_trace(trace_input, intersection_batch_size=batch_size)
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
                else:
                    record['first_sec'] = elapsed
            print(json.dumps({'scene': name, 'repeat': repeat, 'seconds': {mode: record['warm_sec'][-1] if repeat else record['first_sec'] for mode, record in records.items()}}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
            record['no_regression_below_three_percent'] = record['median_sec'] <= records['original']['median_sec'] * 1.03
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [value.emitter_type for value in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and fixture_hash == hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    report = {'contract': 'bounded_gpu_primary_batch_window_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'fixture_sha256': fixture_hash,
        'source_unchanged': unchanged, 'settings': settings, 'coalescing_canonical_packets': args.coalesce,
        'budget_aware_packet_target': args.budget_aware, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
