from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=11)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('At least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scenes = [(name, builder(1000000)) for name, builder in WORKLOADS.items()]
    scenes.append(('tv_corner_cad_probe', cad_case(100000)[0]))
    for depth in (20, 100, 1000):
        trace_input = build_depth_ten_case(100000)
        trace_input.config.max_depth = depth
        trace_input.config.angle_dependent_reflectance = False
        trace_input.optical_profiles[0].reflectance = 0.0
        scenes.append((f'absorber_cap_{depth}', trace_input))
    states = {mode: {} for mode in ('scalar', 'warp')}
    cases = []
    for name, trace_input in scenes:
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.mesh.prepare_acceleration()
        expected = None
        records = {mode: {'warm_sec': [], 'parity': []} for mode in states}
        for repeat in range(args.repeats + 1):
            order = tuple(states) if repeat % 2 == 0 else tuple(reversed(states))
            for mode in order:
                with patch.dict(os.environ, {'LEAKAGE_GPU_PRESENCE_FLAGS': mode}), variant(resident._make_kernel, states[mode]):
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
                else:
                    record['first_sec'] = elapsed
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        case = {'scene': name, 'rays': result.total_rays, 'emitter_types': [value.emitter_type for value in trace_input.emitters],
            'records': records, 'speedup': records['scalar']['median_sec'] / records['warp']['median_sec'],
            'no_regression_below_three_percent': records['warp']['median_sec'] <= records['scalar']['median_sec'] * 1.03}
        cases.append(case)
        print(json.dumps({key: value for key, value in case.items() if key != 'records'}), flush=True)
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'active_warp_presence_flag_same_source_paired_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(case['no_regression_below_three_percent'] for case in cases)
            and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
