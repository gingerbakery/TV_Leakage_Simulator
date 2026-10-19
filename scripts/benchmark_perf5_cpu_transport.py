from __future__ import annotations

import argparse
from contextlib import nullcontext
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

from benchmark_perf5_cpu import WORKLOADS, cad_case, semantic_hash, source_hashes
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from perf5_cpu_reference import counter_reference, load_counter_factory
from leakage_simulator.raytracer import run_direct_ray_trace


def benchmark(args):
    initial_hashes = source_hashes()
    initial_hashes[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    reference_path = ROOT / 'scripts' / 'perf5_cpu_reference.py'
    initial_hashes[str(reference_path.relative_to(ROOT))] = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    reference_factory = load_counter_factory(args.reference_revision, ROOT)
    reference_state = {}
    scenes = [(name, builder(args.rays)) for name, builder in WORKLOADS.items() if name in args.scenes]
    if not args.skip_cad:
        trace_input, _ = cad_case(args.cad_rays)
        scenes.append(('tv_corner_cad_probe', trace_input))
    for depth in args.depth_caps:
        trace_input = build_depth_ten_case(args.depth_rays)
        trace_input.config.max_depth = depth
        trace_input.optical_profiles[0].reflectance = 0.0
        trace_input.config.angle_dependent_reflectance = False
        scenes.append((f'absorber_cap_{depth}', trace_input))
    cases = []
    for name, trace_input in scenes:
        trace_input.mesh.prepare_acceleration()
        expected = None
        measurements = {mode: {'warm_sec': [], 'warm_process_sec': []} for mode in ('wavefront', 'fused')}
        for repeat in range(args.repeats + 1):
            modes = tuple(measurements) if repeat % 2 == 0 else tuple(reversed(measurements))
            for mode in modes:
                workers = args.candidate_workers if mode == 'fused' else args.workers
                batch_size = args.candidate_batch_size if mode == 'fused' else None
                requested_transport = args.candidate_mode if mode == 'fused' else 'wavefront'
                reference_context = counter_reference(reference_factory, reference_state) if mode == 'wavefront' else nullcontext()
                with reference_context, patch.dict(os.environ, {
                    'LEAKAGE_CPU_TRANSPORT': requested_transport, 'LEAKAGE_CPU_WORKERS': str(workers),
                    'LEAKAGE_OPTICAL_TABLES': 'off' if mode == 'wavefront' else 'auto',
                }):
                    started, process_started = time.perf_counter(), time.process_time()
                    result = run_direct_ray_trace(trace_input, intersection_batch_size=batch_size)
                    elapsed, process_elapsed = time.perf_counter() - started, time.process_time() - process_started
                digest = semantic_hash(result)
                if expected is None:
                    expected = digest
                if digest != expected:
                    raise RuntimeError(f'Bit-exact CPU transport mismatch: {name}, {mode}')
                performance = result.metrics['_performance_summary']
                if performance['intersection_fallback_count'] or performance.get('wavefront_cpu_transport_fallback_count'):
                    raise RuntimeError(f'Unexpected CPU fallback: {name}, {mode}')
                fused_expected = requested_transport in ('auto', 'fused')
                if fused_expected and not performance.get('wavefront_cpu_transport_chunk_count'):
                    raise RuntimeError(f'Fused CPU transport was not executed: {name}')
                measurement = measurements[mode]
                measurement['requested_transport'] = requested_transport
                if repeat == 0:
                    measurement['first_sec'] = elapsed
                else:
                    measurement['warm_sec'].append(elapsed)
                    measurement['warm_process_sec'].append(process_elapsed)
                measurement['performance'] = performance
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed, 'bit_exact': True}), flush=True)
        for measurement in measurements.values():
            measurement['warm_median_sec'] = statistics.median(measurement['warm_sec'])
        speedup = measurements['wavefront']['warm_median_sec'] / measurements['fused']['warm_median_sec']
        cases.append({'scene': name, 'rays': trace_input.config.ray_count, 'max_depth': trace_input.config.max_depth,
                      'measurements': measurements, 'speedup': speedup, 'bit_exact': True})
        print(json.dumps({'scene': name, 'speedup': speedup}), flush=True)
    final_hashes = source_hashes()
    final_hashes[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    final_hashes[str(reference_path.relative_to(ROOT))] = hashlib.sha256(reference_path.read_bytes()).hexdigest()
    return {'contract': 'perf5_cpu_fused_transport_paired_v1', 'delivery_path': 'source_checkout',
            'source_hashes': initial_hashes, 'source_unchanged': initial_hashes == final_hashes,
            'reference_revision': args.reference_revision,
            'workers': args.workers, 'candidate_workers': args.candidate_workers,
            'candidate_batch_size': args.candidate_batch_size,
            'candidate_mode': args.candidate_mode,
            'repeats': args.repeats, 'cases': cases,
            'passed': initial_hashes == final_hashes and all(case['bit_exact'] for case in cases)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1_000_000)
    parser.add_argument('--reference-revision', default='157230c6')
    parser.add_argument('--cad-rays', type=int, default=100_000)
    parser.add_argument('--depth-rays', type=int, default=100_000)
    parser.add_argument('--depth-caps', type=int, nargs='*', default=[20, 100, 1000])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--candidate-workers', type=int, default=4)
    parser.add_argument('--candidate-mode', choices=('auto', 'fused'), default='auto')
    parser.add_argument('--candidate-batch-size', type=int)
    parser.add_argument('--scenes', nargs='+', choices=tuple(WORKLOADS), default=list(WORKLOADS))
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--skip-cad', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error('At least two warm runs are required')
    report = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
