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


def hashes():
    values = source_hashes()
    for path in (Path(__file__), ROOT / 'scripts/benchmark_perf5_transport.py',
                 ROOT / 'scripts/benchmark_perf4a_target_workloads.py',
                 ROOT / 'scripts/benchmark_perf3b2a_multibounce.py'):
        values[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays) < 1:
        parser.error('Positive ray counts and at least two warm repetitions are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    initial_hashes = hashes()
    scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items()]
    cad, _ = cad_case(args.cad_rays)
    scenes.append(('tv_corner_cad_probe', cad))
    for depth in (20, 100, 1000):
        trace_input = build_depth_ten_case(args.cad_rays)
        trace_input.config.max_depth = depth
        trace_input.config.angle_dependent_reflectance = False
        trace_input.optical_profiles[0].reflectance = 0.0
        scenes.append((f'absorber_cap_{depth}', trace_input))
    cases = []
    for name, trace_input in scenes:
        trace_input.mesh.prepare_acceleration()
        trace_input.config.compute_backend = 'gpu_cuda'
        records = {mode: {'warm_sec': [], 'parity': [], 'execution': []} for mode in ('off', 'auto')}
        expected = None
        for repeat in range(args.repeats + 1):
            order = tuple(records) if repeat % 2 == 0 else tuple(reversed(records))
            for mode in order:
                with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_MOMENTS': 'ordered', 'LEAKAGE_GPU_RECEIVER_HIT_PROBE': mode}):
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
                record['execution'].append({key: value for key, value in performance.items()
                    if key.startswith(('compute_', 'gpu_cuda_', 'gpu_resident_wavefront_', 'gpu_summary_receiver_'))})
                record['performance'] = performance
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        speedup = records['off']['median_sec'] / records['auto']['median_sec']
        case = {'scene': name, 'rays': result.total_rays, 'triangles': len(trace_input.mesh.faces),
            'surface_hits': result.surface_hit_count, 'receiver_hits': result.receiver_hit_count,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
            'records': records, 'same_source_probe_speedup': speedup,
            'latency_regression_below_three_percent': speedup >= 1 / 1.03}
        cases.append(case)
        print(json.dumps({key: value for key, value in case.items() if key != 'records'}), flush=True)
    unchanged = initial_hashes == hashes()
    report = {'contract': 'same_source_zero_receiver_hit_probe_paired_v1',
        'delivery': {'path': 'source_checkout_existing_verified_runtime', 'workspace': str(ROOT),
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'working_tree_changes': True},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': initial_hashes, 'source_unchanged': unchanged,
        'measurement_policy': 'counterbalanced_same_process_first_plus_warm_repeats',
        'cases': cases, 'passed': unchanged and all(case['latency_regression_below_three_percent'] and
            all(parity['passed'] for record in case['records'].values() for parity in record['parity']) for case in cases)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
