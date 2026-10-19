from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from leakage_simulator import gpu_cuda_emitted_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ['LEAKAGE_GPU_EMITTED_FLUX'] = 'ordered'
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items() if name != 'face_direct']
    cad, _ = cad_case(100000)
    scenes.append(('tv_corner_cad_probe', cad))
    cases = []
    for name, trace_input in scenes:
        trace_input.config.compute_backend = 'gpu_cuda'
        run_direct_ray_trace(trace_input)
        packets = []
        original = flux.accumulate_device_ordered_emissions

        def inspect(cuda, workspace, events, state, chunk_counts):
            cuda.synchronize()
            started = time.perf_counter()
            original(cuda, workspace, events, state, chunk_counts)
            cuda.synchronize()
            elapsed = time.perf_counter() - started
            modes = workspace.modes.copy_to_host()
            packets.append({'rays': events.ray_count, 'seconds': elapsed,
                'mode_totals': modes.sum(axis=0).tolist(), 'modes': modes.tolist()})

        with patch.object(flux, 'accumulate_device_ordered_emissions', new=inspect):
            result = run_direct_ray_trace(trace_input)
        performance = result.metrics['_performance_summary']
        if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_resident_wavefront_fallback_count']:
            raise RuntimeError('CUDA execution proof failed')
        record = {'scene': name, 'stage_sec': sum(packet['seconds'] for packet in packets),
            'packets': packets, 'performance': performance}
        cases.append(record)
        print(json.dumps({'scene': name, 'stage_sec': record['stage_sec'],
            'mode_totals': [sum(packet['mode_totals'][column] for packet in packets) for column in range(3)]}), flush=True)
    report = {'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'source_unchanged': hashes == source_hashes(), 'cases': cases}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
