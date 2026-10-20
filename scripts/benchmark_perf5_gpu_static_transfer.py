from __future__ import annotations

import argparse
from contextlib import ExitStack
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

import benchmark_perf5_gpu_isolated_cluster as cluster
import benchmark_perf5_transport as transport
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_cpu import WORKLOADS, cad_case
from benchmark_perf5_gpu_pair_cluster import selected_factory
from diagnose_perf5_gpu_roundoff import legacy_functions
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import raytracer

SCENES = ('cad', *WORKLOADS, 'absorber_cap_20', 'absorber_cap_100', 'absorber_cap_1000')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--depth-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--scenes', choices=SCENES, nargs='+', default=list(SCENES))
    parser.add_argument('--modes', choices=('reference', 'legacy', 'packed', 'pair'), nargs='+',
        default=['reference', 'legacy', 'packed', 'pair'])
    parser.add_argument('--reference-revision', default='157230c6')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays) < 1 or args.repeats < 2:
        parser.error('Positive rays and two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = transport.transport_hashes()
    dependencies = ('benchmark_perf5_gpu_static_transfer.py', 'benchmark_perf5_gpu_pair_cluster.py',
        'benchmark_perf5_gpu_isolated_cluster.py', 'benchmark_perf5_gpu_guarded_bvh.py',
        'perf5_isolated_hit_cluster.py', 'benchmark_perf5_leaf_loop.py', 'perf4_accuracy.py')
    script_hashes = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependencies}
    replacements = legacy_functions(args.reference_revision) if 'reference' in args.modes else []
    caches = {}
    pair_state = {}
    cases = []
    for name in args.scenes:
        if name == 'cad':
            trace_input = cad_case(args.cad_rays)[0]
        elif name.startswith('absorber_cap_'):
            trace_input = build_depth_ten_case(args.depth_rays)
            trace_input.config.max_depth = int(name.rsplit('_', 1)[-1])
            trace_input.optical_profiles[0].reflectance = 0.0
            trace_input.config.angle_dependent_reflectance = False
        else:
            trace_input = WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': []} for mode in args.modes}
        expected = None
        for repeat in range(args.repeats + 1):
            for mode in args.modes if repeat % 2 == 0 else list(reversed(args.modes)):
                with ExitStack() as stack:
                    stack.enter_context(patch.dict(os.environ, {'LEAKAGE_GPU_STATIC_TRANSFER':
                        'legacy' if mode in ('reference', 'legacy') else 'packed'}))
                    stack.enter_context(transport.runtime_variant('reference' if mode == 'reference' else 'candidate',
                        replacements, caches, 'gpu_cuda'))
                    setup_elapsed = 0.0
                    if mode == 'pair':
                        setup_started = time.perf_counter()
                        stack.enter_context(patch.object(cluster, 'isolated_factory', new=selected_factory('pair')))
                        stack.enter_context(cluster.cluster_variant(trace_input, pair_state, diagnostics=False))
                        setup_elapsed = time.perf_counter() - setup_started
                    started = time.perf_counter()
                    result = raytracer.run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}: {performance.get("gpu_resident_wavefront_fallback_reason")}')
                payload = transport.semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                    record['first_candidate_setup_sec'] = setup_elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        baseline = records['reference'] if 'reference' in records else records[args.modes[0]]
        for record in records.values():
            record['speedup'] = baseline['median_sec'] / record['median_sec']
        if 'legacy' in records and 'packed' in records:
            records['packed']['same_source_transfer_speedup'] = records['legacy']['median_sec'] / records['packed']['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays, 'max_depth': trace_input.config.max_depth,
            'triangles': len(trace_input.mesh.faces),
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
    unchanged = hashes == transport.transport_hashes() and script_hashes == {
        name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependencies}
    report = {'contract': 'static_gpu_transfer_and_pair_fixed_reference_probe_v1',
        'delivery': {'path': 'source_checkout', 'branch': 'main', 'reference_revision': args.reference_revision,
            'candidate': 'uncommitted_static_transfer_with_optional_pair_probe'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_hashes': script_hashes, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
