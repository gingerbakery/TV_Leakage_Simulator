from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, source_hashes
from benchmark_perf5_transport import semantic_payload
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.raytracer import run_direct_ray_trace
from perf4_accuracy import compare_semantic_payloads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1_000_000)
    parser.add_argument('--partitions', nargs='+', default=['1', '8', '32', '128'])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('At least two warm runs are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    assert all(getattr(preflight, key) is True for key in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified'))
    assert preflight.preflight_scope == 'production_ray_bvh' and gpu.PROVIDER_CONTRACT == 'strict_float64_bvh_v1'
    hashes = source_hashes()
    cases = []
    for name, builder in WORKLOADS.items():
        scene = builder(args.rays)
        scene.config.compute_backend = 'gpu_cuda'
        expected = None
        records = {count: {'warm_sec': []} for count in args.partitions}
        for repeat in range(args.repeats + 1):
            order = args.partitions if repeat % 2 == 0 else list(reversed(args.partitions))
            for count in order:
                with patch.dict(os.environ, {'LEAKAGE_NATIVE_SAMPLING': 'auto', 'LEAKAGE_GPU_SUMMARY_PARTITIONS': count}):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(scene)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or not performance['gpu_cuda_gpu_success_count'] or performance['gpu_resident_wavefront_fallback_count']:
                    raise RuntimeError(f'CUDA execution was not proven: {name}, {count}')
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48)
                if not parity.passed:
                    raise RuntimeError(parity.to_dict())
                record = records[count]
                record['performance'] = performance
                if repeat == 0:
                    record['first_sec'] = elapsed
                else:
                    record['warm_sec'].append(elapsed)
        for count, record in records.items():
            record['warm_median_sec'] = statistics.median(record['warm_sec'])
            print(name, count, record['warm_median_sec'], flush=True)
        cases.append({'name': name, 'measurements': records})
    report = {'delivery_path': 'source_checkout', 'preflight': asdict(preflight),
              'provider_contract': gpu.PROVIDER_CONTRACT, 'source_hashes': hashes,
              'source_unchanged': hashes == source_hashes(), 'cases': cases}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['source_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
