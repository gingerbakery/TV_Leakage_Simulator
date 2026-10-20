from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_cpu import WORKLOADS, cad_case
import benchmark_perf5_transport as transport
from diagnose_perf5_gpu_roundoff import legacy_functions
from perf4_accuracy import compare_semantic_payloads
from perf5_adaptive_gpu_packets import adaptive_batch_request, adaptive_packets
from perf5_ordered_parity_maps import receiver_parity_maps, emission_parity_maps
from perf5_emission_aliases import emission_aliases
from perf5_initial_event_window import initial_event_window
from perf5_workspace_packing import MODES, workspace_factory
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer

SCENES = (*WORKLOADS, 'cad', 'absorber_cap_20', 'absorber_cap_100',
    'absorber_cap_1000', 'reflected_receiver_mis')
ADAPTIVE_MODES = ('packed_adaptive', 'packed_adaptive_parity', 'packed_adaptive_parity_both',
    'packed_adaptive_parity_alias', 'packed_adaptive_window16_alias')
BENCHMARK_MODES = (*MODES, *ADAPTIVE_MODES)


def execute(trace_input, thread_mode, **trace_options):
    if thread_mode == 'same':
        return raytracer.run_direct_ray_trace(trace_input, **trace_options)
    outcomes = []
    failures = []
    def worker():
        try:
            outcomes.append(raytracer.run_direct_ray_trace(trace_input, **trace_options))
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=worker, name='perf5-api-style-probe')
    thread.start()
    thread.join()
    if failures:
        raise failures[0]
    if len(outcomes) != 1:
        raise RuntimeError('The fresh-thread probe did not produce exactly one result')
    return outcomes[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--depth-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--modes', choices=('reference', *BENCHMARK_MODES), nargs='+',
        default=['current', 'packed', 'packed_reuse'])
    parser.add_argument('--scenes', choices=SCENES, nargs='+', default=list(SCENES))
    parser.add_argument('--thread-modes', choices=('same', 'fresh'), nargs='+', default=['same', 'fresh'])
    parser.add_argument('--reference-revision', default='157230c6')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays) < 1 or args.repeats < 2:
        parser.error('Positive Ray counts and at least two warm repeats are required')
    if 'current' not in args.modes or len(set(args.modes)) != len(args.modes):
        parser.error('Unique modes including current are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    sources = transport.transport_hashes()
    paths = (Path(__file__), ROOT / 'scripts/perf5_workspace_packing.py',
        ROOT / 'scripts/perf5_adaptive_gpu_packets.py', ROOT / 'scripts/perf5_ordered_parity_maps.py',
        ROOT / 'scripts/perf5_emission_aliases.py',
        ROOT / 'scripts/perf5_initial_event_window.py',
        ROOT / 'scripts/benchmark_perf5_gpu_batching.py',
        ROOT / 'src/leakage_simulator/api/runtime.py', ROOT / 'tests/test_perf4e_bounce_mis.py')
    diagnostic_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths}
    factories = {mode: workspace_factory('packed_reuse' if mode in ADAPTIVE_MODES else mode)
        for mode in args.modes if mode != 'reference'}
    replacements = legacy_functions(args.reference_revision) if 'reference' in args.modes else []
    cases = []
    for name in args.scenes:
        if name == 'cad':
            trace_input = cad_case(args.cad_rays)[0]
        elif name.startswith('absorber_cap_'):
            trace_input = build_depth_ten_case(args.depth_rays)
            trace_input.config.max_depth = int(name.rsplit('_', 1)[-1])
            trace_input.optical_profiles[0].reflectance = 0.0
            trace_input.config.angle_dependent_reflectance = False
        elif name == 'reflected_receiver_mis':
            trace_input = _build_reflected_case(args.rays, 7172, 'receiver_mis', backend='gpu_cuda')
        else:
            trace_input = WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        for thread_mode in args.thread_modes:
            caches = {mode: {} for mode in args.modes}
            records = {mode: {'warm_sec': [], 'parity': [], 'execution_evidence': []} for mode in args.modes}
            expected = None
            for repeat in range(args.repeats + 1):
                for mode in args.modes if repeat % 2 == 0 else list(reversed(args.modes)):
                    with ExitStack() as stack:
                        stack.enter_context(transport.runtime_variant(
                            'reference' if mode == 'reference' else 'candidate', replacements,
                            caches[mode], 'gpu_cuda'))
                        stack.enter_context(patch.dict(os.environ, {
                            'LEAKAGE_GPU_STATIC_TRANSFER': 'legacy' if mode == 'reference' else 'packed',
                            'LEAKAGE_GPU_TRANSFER_VIEWS': 'sliced' if mode == 'reference' else 'direct',
                            'LEAKAGE_GPU_PAIR_BVH': 'off' if mode == 'reference' else 'auto',
                        }))
                        if mode != 'reference':
                            stack.enter_context(patch.object(resident, '_ensure_workspace', new=factories[mode]))
                        minimum_rays = 100000 if mode == 'packed_adaptive_window16_alias' else 1000000
                        if mode == 'packed_adaptive_window16_alias':
                            stack.enter_context(initial_event_window(16))
                        if mode in ('packed_adaptive_parity', 'packed_adaptive_parity_both',
                                'packed_adaptive_parity_alias', 'packed_adaptive_window16_alias'):
                            stack.enter_context(receiver_parity_maps(gpu._CUDA))
                        if mode == 'packed_adaptive_parity_both':
                            stack.enter_context(emission_parity_maps(gpu._CUDA))
                        if mode in ('packed_adaptive_parity_alias', 'packed_adaptive_window16_alias'):
                            stack.enter_context(emission_aliases(gpu._CUDA))
                        decisions = stack.enter_context(adaptive_packets(trace_input,
                            minimum_rays=minimum_rays)) if mode in ADAPTIVE_MODES else []
                        requested_batch = adaptive_batch_request(trace_input,
                            minimum_rays=minimum_rays) if mode in ADAPTIVE_MODES else None
                        trace_options = {'intersection_batch_size': requested_batch} if requested_batch else {}
                        started = time.perf_counter()
                        result = execute(trace_input, thread_mode, **trace_options)
                        elapsed = time.perf_counter() - started
                    performance = result.metrics['_performance_summary']
                    if performance['compute_execution_state'] != 'gpu_active' or (
                        performance['gpu_cuda_gpu_success_count'] <= 0
                        or performance['gpu_resident_wavefront_fallback_count']
                        or performance['gpu_cuda_hybrid_cpu_attempt_count']
                    ):
                        raise RuntimeError(f'Full CUDA execution not proven: {name}/{thread_mode}/{mode}')
                    payload = transport.semantic_payload(result)
                    if expected is None:
                        expected = payload
                    parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                        relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                    record = records[mode]
                    record['parity'].append(parity)
                    record['execution_evidence'].append(performance)
                    record.setdefault('packet_decisions', []).append(list(decisions))
                    if repeat:
                        record['warm_sec'].append(elapsed)
                    else:
                        record['first_sec'] = elapsed
                    print(json.dumps({'scene': name, 'thread_mode': thread_mode, 'mode': mode,
                        'repeat': repeat, 'seconds': elapsed, 'physical_passed': parity['passed']}), flush=True)
            for record in records.values():
                record['median_sec'] = statistics.median(record['warm_sec'])
            reference = records.get('reference', records['current'])
            for record in records.values():
                record['fixed_reference_speedup'] = reference['median_sec'] / record['median_sec']
                record['same_source_speedup'] = records['current']['median_sec'] / record['median_sec']
                record['same_source_no_regression_below_three_percent'] = (
                    record['median_sec'] <= records['current']['median_sec'] * 1.03)
            cases.append({'scene': name, 'thread_mode': thread_mode, 'rays': result.total_rays,
                'max_depth': trace_input.config.max_depth, 'triangles': len(trace_input.mesh.faces),
                'receiver_hit_count': result.receiver_hit_count, 'surface_hit_count': result.surface_hit_count,
                'terminated_ray_count': result.terminated_ray_count,
                'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
    unchanged = sources == transport.transport_hashes() and all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        for name, digest in diagnostic_hashes.items())
    report = {'contract': 'gpu_workspace_packing_same_and_fresh_thread_probe_v1',
        'delivery': {'path': 'source_checkout', 'branch': subprocess.check_output(
            ['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'reference_revision': args.reference_revision, 'candidate': 'isolated_uncommitted_workspace_packing'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': sources, 'diagnostic_hashes': diagnostic_hashes, 'source_unchanged': unchanged,
        'thread_scope': 'fresh matches API job lifetime; HTTP parsing and rendering are excluded',
        'cases': cases, 'physical_passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity']),
        'performance_no_regression': all(record['same_source_no_regression_below_three_percent']
            for case in cases for record in case['records'].values() if record is not case['records'].get('reference'))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['physical_passed'] and report['performance_no_regression'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
