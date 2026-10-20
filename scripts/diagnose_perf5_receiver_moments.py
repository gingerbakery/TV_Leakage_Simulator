from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from test_perf4e_bounce_mis import _build_reflected_case, _semantic_payload
from perf4_accuracy import compare_semantic_payloads
from benchmark_perf5_cpu import source_hashes
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer
from leakage_simulator.native_cpu_ordered_reducer import TERMINAL_RECEIVER


def packet_snapshot(kind, receiver, row, column, power, count):
    return tuple(np.array(values[:count], copy=True) for values in (kind, receiver, row, column, power))


def moment_sums(packets, columns, *, square, ordered):
    bins = [[] for _ in range(columns * columns)]
    all_values = []
    for kinds, receivers, rows, column_values, powers in packets:
        for index in np.flatnonzero(kinds == TERMINAL_RECEIVER):
            if receivers[index] != 0:
                raise ValueError('Diagnostic fixture requires one Receiver')
            power = float(powers[index])
            value = power ** 2 if square else power
            bins[int(rows[index]) * columns + int(column_values[index])].append(value)
            all_values.append(value)

    def aggregate(values):
        if not ordered:
            return math.fsum(values)
        total = 0.0
        for value in values:
            total += value
        return total

    return np.asarray([aggregate(values) for values in bins]), aggregate(all_values)


def ulp_distance(first, second):
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    if np.any(first < 0.0) or np.any(second < 0.0):
        raise ValueError('Moment ULP diagnostic requires nonnegative values')
    first_bits, second_bits = first.view(np.uint64), second.view(np.uint64)
    distances = np.maximum(first_bits, second_bits) - np.minimum(first_bits, second_bits)
    return int(np.max(distances, initial=0))


def benchmark(args):
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in (
        'available', 'strict_float64', 'kernel_executed', 'kernel_verified',
    )) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production GPU preflight failed')
    records = []
    for rays in args.rays:
        cpu_packets = []
        original_reduce = raytracer._reduce_wavefront_event_tape_ordered

        def capture_cpu(tape, *values):
            cpu_packets.append(packet_snapshot(
                tape.terminal_kind_codes, tape.terminal_receiver_indices,
                tape.terminal_rows, tape.terminal_columns,
                tape.terminal_received_power_lumen, len(tape),
            ))
            return original_reduce(tape, *values)

        with patch.object(raytracer, '_reduce_wavefront_event_tape_ordered', new=capture_cpu):
            cpu = raytracer.run_direct_ray_trace(
                _build_reflected_case(rays, args.seed, 'receiver_mis', backend='cpu'),
                wavefront_residency='host_roundtrip',
            )
        cpu_grid = cpu.to_dict()['receiver_grids'][0]
        cpu_flux = np.asarray(cpu_grid['flux_lumen']).reshape(-1)
        cpu_squared = np.asarray(cpu_grid['flux_squared_lumen2_grid']).reshape(-1)
        cpu_total_squared = cpu_grid['flux_squared_lumen2']
        cpu_ordered, cpu_ordered_total = moment_sums(cpu_packets, 4, square=True, ordered=True)
        if not np.array_equal(cpu_ordered.view(np.uint64), cpu_squared.view(np.uint64)):
            raise RuntimeError('CPU packet capture does not reproduce the exact reference')
        if cpu_ordered_total != cpu_total_squared:
            raise RuntimeError('CPU total moment capture is not exact')
        cpu_fsum, cpu_fsum_total = moment_sums(cpu_packets, 4, square=True, ordered=False)
        for repeat in range(args.repeats):
            gpu_packets = []
            original_accumulate = resident.accumulate_resident_summary_gpu_cuda

            def capture_gpu(events, request, session):
                gpu_packets.append(packet_snapshot(
                    events.terminal_kind.copy_to_host(), events.terminal_receiver.copy_to_host(),
                    events.terminal_row.copy_to_host(), events.terminal_column.copy_to_host(),
                    events.terminal_received_power.copy_to_host(), events.ray_count,
                ))
                return original_accumulate(events, request, session)

            with patch.object(resident, 'accumulate_resident_summary_gpu_cuda', new=capture_gpu):
                actual = raytracer.run_direct_ray_trace(
                    _build_reflected_case(rays, args.seed, 'receiver_mis', backend='gpu_cuda'),
                    wavefront_residency='gpu_resident',
                )
            performance = actual.metrics['_performance_summary']
            if performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                raise RuntimeError('CUDA execution proof failed')
            grid = actual.to_dict()['receiver_grids'][0]
            squared = np.asarray(grid['flux_squared_lumen2_grid']).reshape(-1)
            flux = np.asarray(grid['flux_lumen']).reshape(-1)
            ordered_squared, ordered_total = moment_sums(gpu_packets, 4, square=True, ordered=True)
            fsum_squared, fsum_total = moment_sums(gpu_packets, 4, square=True, ordered=False)
            ordered_flux, ordered_flux_total = moment_sums(gpu_packets, 4, square=False, ordered=True)
            actual_flux_total = actual.contribution_summary.to_dict()['receivers']['observer']['total']['flux_lumen']
            receiver_parity = compare_semantic_payloads(cpu.contribution_summary.to_dict()['receivers'], actual.contribution_summary.to_dict()['receivers'],
                absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
            parity = compare_semantic_payloads(_semantic_payload(cpu), _semantic_payload(actual),
                absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
            record = {
                'seed': args.seed, 'rays': rays, 'repeat': repeat, 'parity': parity.to_dict(),
                'receiver_flux_parity': receiver_parity.to_dict(),
                'gpu_native_total_flux_vs_ordered_ulp': ulp_distance(actual_flux_total, ordered_flux_total),
                'cpu_ordered_vs_fsum_squared_ulp': ulp_distance(cpu_squared, cpu_fsum),
                'gpu_ordered_vs_cpu_squared_ulp': ulp_distance(ordered_squared, cpu_squared),
                'gpu_native_vs_ordered_squared_ulp': ulp_distance(squared, ordered_squared),
                'gpu_native_vs_fsum_squared_ulp': ulp_distance(squared, fsum_squared),
                'cpu_total_vs_fsum_ulp': ulp_distance(cpu_total_squared, cpu_fsum_total),
                'gpu_ordered_total_vs_cpu_ulp': ulp_distance(ordered_total, cpu_total_squared),
                'gpu_native_total_vs_ordered_ulp': ulp_distance(grid['flux_squared_lumen2'], ordered_total),
                'gpu_native_total_vs_fsum_ulp': ulp_distance(grid['flux_squared_lumen2'], fsum_total),
                'gpu_ordered_vs_cpu_flux_ulp': ulp_distance(ordered_flux, cpu_flux),
                'gpu_native_vs_cpu_flux_ulp': ulp_distance(flux, cpu_flux),
                'compute_execution_state': performance['compute_execution_state'],
                'cuda_success': performance['gpu_cuda_gpu_success_count'],
                'cuda_attempts': performance['gpu_cuda_gpu_attempt_count'],
                'resident_fallback': performance['gpu_resident_wavefront_fallback_count'],
                'cuda_hybrid': performance['gpu_cuda_hybrid_cpu_attempt_count'],
                'receiver_flux_contract': performance.get('gpu_summary_receiver_flux_contract'),
                'receiver_flux_workspace_bytes': performance.get('gpu_summary_receiver_flux_workspace_bytes'),
                'emitted_flux_contract': performance.get('gpu_summary_emitted_flux_contract'),
                'emitted_flux_workspace_bytes': performance.get('gpu_summary_emitted_flux_workspace_bytes'),
                'receiver_grid_contract': performance.get('gpu_summary_receiver_grid_contract'),
                'receiver_grid_workspace_bytes': performance.get('gpu_summary_receiver_grid_workspace_bytes'),
            }
            records.append(record)
            print(json.dumps({key: value for key, value in record.items() if key not in ('parity', 'receiver_flux_parity')}), flush=True)
    return {'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT}, 'cases': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, nargs='+', default=[8192, 65536])
    parser.add_argument('--seed', type=int, default=7171)
    parser.add_argument('--seeds', type=int, nargs='+')
    parser.add_argument('--repeats', type=int, default=8)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--emitted-flux-mode', choices=('atomic', 'ordered'), default='atomic')
    parser.add_argument('--receiver-grid-mode', choices=('atomic', 'ordered'), default='atomic')
    args = parser.parse_args()
    os.environ['LEAKAGE_GPU_EMITTED_FLUX'] = args.emitted_flux_mode
    os.environ['LEAKAGE_GPU_RECEIVER_GRIDS'] = args.receiver_grid_mode
    if args.repeats < 1 or min(args.rays) < 1:
        parser.error('Ray and repetition counts must be positive')
    hashes = source_hashes()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    seeds = args.seeds or [args.seed]
    reports = []
    for seed in seeds:
        args.seed = seed
        reports.append(benchmark(args))
    records = [record for report in reports for record in report['cases']]
    unchanged = hashes == source_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'captured_packets_ordered_receiver_flux_moments_v2',
        'preflight': reports[0]['preflight'], 'seeds': seeds,
        'source_hashes': hashes, 'script_sha256': script_hash, 'source_unchanged': unchanged,
        'cases': records, 'passed': unchanged and bool(records) and all(record['parity']['passed'] for record in records)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
