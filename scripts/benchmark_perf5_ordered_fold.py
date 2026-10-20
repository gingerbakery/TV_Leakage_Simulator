from __future__ import annotations

from dataclasses import asdict
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_receiver_flux as flux
from leakage_simulator import gpu_cuda_receiver_moments as moments
from leakage_simulator.raytracer import run_direct_ray_trace
from test_perf4e_bounce_mis import _build_reflected_case


def serial_factory(cuda):
    @cuda.jit(device=True, inline=True, fastmath=False)
    def fold(total, value, valid):
        lane = cuda.threadIdx.x & 31
        for source_lane in range(32):
            selected = cuda.shfl_sync(0xffffffff, int(valid), source_lane)
            addition = cuda.shfl_sync(0xffffffff, value, source_lane)
            if lane == 0 and selected:
                total = total + addition
        return total, False

    return fold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/perf5_transport/receiver-warp-fold-paired.json')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('At least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fixture = ROOT / 'tests/test_perf4e_bounce_mis.py'
    fixture_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    factory = moments.make_warp_ordered_fold
    cache = {'serial': None, 'integer_fold': None}
    cases = []
    scenes = [('reflected_receiver_mis', _build_reflected_case(rays, 7172, 'receiver_mis', backend='gpu_cuda')) for rays in (65536, 1000000)]
    scenes.extend((name, builder(1000000)) for name, builder in WORKLOADS.items())
    cad, _ = cad_case(100000)
    scenes.append(('tv_corner_cad_probe', cad))
    for name, trace_input in scenes:
        rays = trace_input.config.ray_count
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': [], 'receiver_parity': []} for mode in cache}
        expected = None
        expected_receivers = None
        for repeat in range(args.repeats + 1):
            order = tuple(cache) if repeat % 2 == 0 else tuple(reversed(cache))
            for mode in order:
                saved = flux._KERNELS
                flux._KERNELS = cache[mode]
                try:
                    with patch.dict(os.environ, {'LEAKAGE_GPU_EMITTED_FLUX': 'atomic'}), patch.object(moments, 'make_warp_ordered_fold', new=serial_factory if mode == 'serial' else factory):
                        started = time.perf_counter()
                        result = run_direct_ray_trace(trace_input)
                        elapsed = time.perf_counter() - started
                    cache[mode] = flux._KERNELS
                finally:
                    flux._KERNELS = saved
                payload = semantic_payload(result)
                receivers = result.contribution_summary.to_dict()['receivers']
                if expected is None:
                    expected, expected_receivers = payload, receivers
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                    raise RuntimeError('Full CUDA execution not proven')
                record = records[mode]
                registers = cache[mode][1].get_regs_per_thread()
                record['kernel_registers'] = {str(key): value for key, value in registers.items()} if isinstance(registers, dict) else registers
                if flux._UNIFORM_KERNELS is not None:
                    registers = flux._UNIFORM_KERNELS[1].get_regs_per_thread()
                    record['uniform_kernel_registers'] = {str(key): value for key, value in registers.items()} if isinstance(registers, dict) else registers
                record['performance'] = performance
                record['parity'].append(compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict())
                record['receiver_parity'].append(compare_semantic_payloads(expected_receivers, receivers).to_dict())
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        case = {'scene': name, 'rays': rays, 'emitter_seeds': [value.seed for value in trace_input.emitters], 'emitter_type': trace_input.emitters[0].emitter_type,
            'records': records, 'speedup': records['serial']['median_sec'] / records['integer_fold']['median_sec'],
            'no_regression_below_three_percent': records['integer_fold']['median_sec'] <= records['serial']['median_sec'] * 1.03}
        cases.append(case)
        print(json.dumps({'scene': name, 'rays': rays, 'speedup': case['speedup'], 'no_regression': case['no_regression_below_three_percent']}), flush=True)
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and fixture_hash == hashlib.sha256(fixture.read_bytes()).hexdigest()
    report = {'contract': 'receiver_ordered_fold_same_source_paired_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT}, 'source_hashes': hashes,
        'script_sha256': own_hash, 'fixture_sha256': fixture_hash, 'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(case['no_regression_below_three_percent'] for case in cases) and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity']) and
            all(parity['semantic_exact'] for case in cases for record in case['records'].values() for parity in record['receiver_parity'])}
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
