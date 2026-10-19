from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
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
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--scenes', nargs='+', choices=(*WORKLOADS, 'cad'), default=[*WORKLOADS, 'cad'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays) < 1:
        parser.error('Positive Ray counts and at least two warm runs are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items() if name in args.scenes]
    if 'cad' in args.scenes:
        cad, _ = cad_case(args.cad_rays)
        scenes.append(('tv_corner_cad_probe', cad))
    cases = []
    for name, trace_input in scenes:
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.mesh.prepare_acceleration()
        records = {threads: {'warm_sec': [], 'parity': []} for threads in (128, 32, 64, 256)}
        expected = None
        for repeat in range(args.repeats + 1):
            order = tuple(records) if repeat % 2 == 0 else tuple(reversed(records))
            for threads in order:
                with patch.object(resident, 'THREADS_PER_BLOCK', threads):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count']:
                    raise RuntimeError('CUDA execution was not proven')
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                record = records[threads]
                record['parity'].append(parity)
                record['performance'] = performance
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        baseline = records[128]['median_sec']
        for record in records.values():
            record['speedup_over_128'] = baseline / record['median_sec']
        cases.append({'scene': name, 'records': records})
        print(json.dumps({'scene': name, 'speedup': {threads: record['speedup_over_128'] for threads, record in records.items()}}), flush=True)
    unchanged = hashes == source_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_kernel_resident_launch_geometry_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': script_hash, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
