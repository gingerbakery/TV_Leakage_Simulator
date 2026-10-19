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
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_receiver_flux as moments
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('At least two warm repetitions are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items()]
    cad, _ = cad_case(args.cad_rays)
    scenes.append(('tv_corner_cad_probe', cad))
    cases = []
    for name, trace_input in scenes:
        trace_input.mesh.prepare_acceleration()
        trace_input.config.compute_backend = 'gpu_cuda'
        records = {mode: {'warm_sec': [], 'parity': []} for mode in ('atomic', 'ordered')}
        expected = None
        for repeat in range(args.repeats + 1):
            modes = tuple(records) if repeat % 2 == 0 else tuple(reversed(records))
            for mode in modes:
                with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_MOMENTS': mode}):
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
                    raise RuntimeError('Receiver moment benchmark did not execute CUDA')
                records[mode]['parity'].append(parity)
                if repeat:
                    records[mode]['warm_sec'].append(elapsed)
                else:
                    records[mode]['first_sec'] = elapsed
                records[mode]['performance'] = performance
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        samples = []
        original = moments.accumulate_device_ordered_flux

        def profile(*values, **keywords):
            gpu._CUDA.synchronize()
            started = time.perf_counter()
            result = original(*values, **keywords)
            gpu._CUDA.synchronize()
            samples.append(time.perf_counter() - started)
            return result

        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_MOMENTS': 'ordered'}), patch.object(moments, 'accumulate_device_ordered_flux', new=profile):
            profiled = run_direct_ray_trace(trace_input)
        if not samples:
            performance = profiled.metrics['_performance_summary']
            if profiled.receiver_hit_count or not performance.get('gpu_summary_receiver_reduction_skipped_count'):
                raise RuntimeError('Ordered Receiver profiling stage was not executed')
        case = {'scene': name, 'records': records, 'profiled_ordered_stage_sec': sum(samples),
            'profiled_chunks': len(samples), 'ordered_over_atomic_latency_ratio': records['ordered']['median_sec'] / records['atomic']['median_sec']}
        cases.append(case)
        print(json.dumps({key: value for key, value in case.items() if key != 'records'}), flush=True)
    unchanged = hashes == source_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_source_receiver_flux_moment_mode_paired_v2',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes,
        'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
