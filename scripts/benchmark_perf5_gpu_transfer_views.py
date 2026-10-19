from __future__ import annotations

import argparse
from contextlib import ExitStack
import cProfile
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

import benchmark_perf5_transport as transport
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_cpu import WORKLOADS, cad_case
from diagnose_perf5_gpu_roundoff import legacy_functions
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_transfer as transfer
from leakage_simulator import raytracer

SCENES = ('cad', *WORKLOADS, 'absorber_cap_20', 'absorber_cap_100', 'absorber_cap_1000')


def view_metadata_probe(cuda):
    layout = {}
    offset = 0
    for index in range(48):
        dtype = np.dtype('int8' if index % 5 == 0 else 'int64' if index % 3 == 0 else 'float64')
        shape = (0, 3) if index % 7 == 0 else (9, 3) if index % 2 else (9,)
        offset += -offset % 256
        end = offset + math.prod(shape) * dtype.itemsize
        layout[str(index)] = (offset, end, dtype, shape)
        offset = end
    device = cuda.to_device(np.zeros(offset, dtype=np.uint8))
    timings = {mode: [] for mode in ('sliced', 'direct')}
    for repeat in range(101):
        for mode in ('sliced', 'direct') if repeat % 2 == 0 else ('direct', 'sliced'):
            with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': mode}):
                started = time.perf_counter()
                views = transfer.create_device_views(device, layout)
                elapsed = time.perf_counter() - started
            if repeat:
                timings[mode].append(elapsed)
            del views
    return {mode: {'median_sec': statistics.median(values), 'warm_sec': values}
        for mode, values in timings.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--depth-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--scenes', choices=SCENES, nargs='+', default=list(SCENES))
    parser.add_argument('--modes', choices=('reference', 'sliced', 'direct'), nargs='+',
        default=['reference', 'sliced', 'direct'])
    parser.add_argument('--reference-revision', default='157230c6')
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays) < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = transport.transport_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    replacements = legacy_functions(args.reference_revision) if 'reference' in args.modes else []
    caches = {}
    cases = []
    profiles = {}
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
                    stack.enter_context(patch.dict(os.environ, {
                        'LEAKAGE_GPU_STATIC_TRANSFER': 'legacy' if mode == 'reference' else 'packed',
                        'LEAKAGE_GPU_PAIR_BVH': 'off' if mode == 'reference' else 'auto',
                        'LEAKAGE_GPU_TRANSFER_VIEWS': 'sliced' if mode == 'reference' else mode,
                    }))
                    stack.enter_context(transport.runtime_variant('reference' if mode == 'reference' else 'candidate',
                        replacements, caches, 'gpu_cuda'))
                    started = time.perf_counter()
                    result = raytracer.run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or (
                    performance['gpu_cuda_gpu_success_count'] <= 0
                    or performance['gpu_resident_wavefront_fallback_count']
                    or performance['gpu_cuda_hybrid_cpu_attempt_count']
                ):
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}')
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
                    record['first_performance'] = performance
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        baseline = records['reference'] if 'reference' in records else records[args.modes[0]]
        for record in records.values():
            record['speedup'] = baseline['median_sec'] / record['median_sec']
        if 'sliced' in records and 'direct' in records:
            records['direct']['same_source_view_speedup'] = records['sliced']['median_sec'] / records['direct']['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays, 'max_depth': trace_input.config.max_depth,
            'triangles': len(trace_input.mesh.faces), 'surface_hits': result.surface_hit_count,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
        if args.profile and name == 'absorber_cap_20':
            for mode in ('sliced', 'direct'):
                with patch.dict(os.environ, {'LEAKAGE_GPU_TRANSFER_VIEWS': mode}):
                    profiler = cProfile.Profile()
                    result = profiler.runcall(raytracer.run_direct_ray_trace, trace_input)
                stats = pstats.Stats(profiler)
                profiles[mode] = [{'file': filename, 'line': line, 'function': function,
                    'primitive_calls': values[0], 'calls': values[1],
                    'self_sec': values[2], 'cumulative_sec': values[3]}
                    for (filename, line, function), values in sorted(stats.stats.items(),
                        key=lambda item: item[1][3], reverse=True)[:60]]
    micro = view_metadata_probe(gpu._CUDA)
    unchanged = hashes == transport.transport_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'gpu_device_view_metadata_fixed_reference_v1',
        'delivery': {'path': 'source_checkout',
            'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'reference_revision': args.reference_revision, 'candidate': 'uncommitted_direct_view_probe'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged,
        'metadata_microbenchmark': micro, 'profiles': profiles, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
