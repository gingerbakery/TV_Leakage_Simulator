from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from perf5_emission_warp import make_factory
import perf5_uniform_warp_metadata as metadata
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_emitted_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=1000000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--probe', choices=('warp_fold', 'metadata_warp', 'metadata_count_warp'), default='warp_fold')
    parser.add_argument('--scenes', nargs='+', choices=('cad', 'mis', *WORKLOADS),
        default=['cad', 'mis', *WORKLOADS])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two warm runs are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh'
        or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), ROOT / 'scripts/perf5_emission_warp.py',
            ROOT / 'scripts/perf5_uniform_warp_metadata.py')}
    kernels = {'block': flux.make_kernel(gpu._CUDA), 'warp': make_factory()(gpu._CUDA)} if args.probe == 'warp_fold' else {}
    metadata_factory = metadata.make_factory('count' if args.probe == 'metadata_count_warp' else 'all') if args.probe != 'warp_fold' else None
    caches = {}
    cases = []
    for name in args.scenes:
        scene = cad_case(args.cad_rays)[0] if name == 'cad' else (
            _build_reflected_case(65536, 7170, 'receiver_mis', backend='gpu_cuda')
            if name == 'mis' else WORKLOADS[name](args.rays))
        scene.config.compute_backend = 'gpu_cuda'
        scene.config.store_ray_paths = False
        scene.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': []} for mode in ('block', 'warp')}
        expected = None
        for repeat in range(args.repeats + 1):
            for mode in tuple(records) if repeat % 2 == 0 else tuple(reversed(records)):
                with ExitStack() as stack:
                    if args.probe == 'warp_fold':
                        stack.enter_context(patch.object(flux, '_KERNEL', kernels[mode]))
                    else:
                        stack.enter_context(metadata.kernel_variant(mode, metadata_factory, caches))
                    started = time.perf_counter()
                    result = run_direct_ray_trace(scene)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or (
                    performance['gpu_cuda_gpu_success_count'] <= 0
                    or performance['gpu_resident_wavefront_fallback_count']
                    or performance['gpu_cuda_hybrid_cpu_attempt_count']
                ):
                    raise RuntimeError('Full CUDA execution not proven')
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload,
                    absolute_tolerance=1e-9, relative_tolerance=1e-9,
                    max_ulp_distance=1 << 48).to_dict()
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat,
                    'seconds': elapsed, 'passed': parity['passed']}), flush=True)
        for mode, record in records.items():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['speedup_over_block'] = statistics.median(records['block']['warm_sec']) / record['median_sec']
            compiled = kernels.get(mode)
            if args.probe != 'warp_fold':
                compiled = caches[mode]['_PARTITION_KERNELS'][0] if caches[mode]['_PARTITION_KERNELS'] else caches[mode]['_KERNEL']
            if compiled is not None and compiled.signatures:
                record['resources'] = {
                    'registers_per_thread': max(compiled.get_regs_per_thread().values()),
                    'local_bytes_per_thread': max(compiled.get_local_mem_per_thread().values()),
                    'shared_bytes_per_block': max(compiled.get_shared_mem_per_block().values())}
        cases.append({'scene': name, 'rays': result.total_rays, 'max_depth': scene.config.max_depth,
            'emitter_types': [emitter.emitter_type for emitter in scene.emitters], 'records': records})
    unchanged = hashes == source_hashes() and all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value
        for name, value in own_hashes.items())
    report = {'contract': 'warp_exact_integer_emission_reduction_probe_v1', 'probe': args.probe,
        'delivery': {'path': 'source_checkout', 'candidate': 'experimental_factory_only',
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_hashes': own_hashes, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
