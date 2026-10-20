from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
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
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_emitted_flux as flux
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--widths', nargs='+', type=int, default=[4096, 512, 1024, 2048])
    parser.add_argument('--scenes', nargs='+', choices=('cad', 'mis', 'stochastic_two_bounce', 'trapped_corridor_depth10'),
        default=['cad', 'mis', 'stochastic_two_bounce', 'trapped_corridor_depth10'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2 or any(
            width < 128 or width > 4096 or width & (width - 1) for width in args.widths):
        parser.error('Positive rays, at least two warm runs, and power-of-two widths 128..4096 are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
            preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    os.environ['LEAKAGE_GPU_EMITTED_FLUX'] = 'ordered'
    os.environ['LEAKAGE_GPU_EMITTED_UNIFORM'] = 'auto'
    cases = []
    kernels = {}
    for name in args.scenes:
        scene = cad_case(args.cad_rays)[0] if name == 'cad' else (
            _build_reflected_case(65536, 7170, 'receiver_mis', backend='gpu_cuda') if name == 'mis' else WORKLOADS[name](args.rays))
        scene.config.compute_backend = 'gpu_cuda'
        scene.config.store_ray_paths = False
        scene.mesh.prepare_acceleration()
        records = {width: {'warm_sec': [], 'parity': []} for width in args.widths}
        expected = None
        for repeat in range(args.repeats + 1):
            for width in args.widths if repeat % 2 == 0 else list(reversed(args.widths)):
                with patch.object(flux, 'TILE_WIDTH', width):
                    if width not in kernels:
                        kernels[width] = flux.make_kernel(gpu._CUDA)
                    with patch.object(flux, '_KERNEL', kernels[width]):
                        started = time.perf_counter()
                        result = run_direct_ray_trace(scene)
                        elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or (
                        performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']):
                    raise RuntimeError('Full CUDA execution not proven')
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                record = records[width]
                record['parity'].append(parity)
                record['performance'] = performance
                record['resources'] = {'registers': max(kernels[width].get_regs_per_thread().values()),
                    'local_bytes': max(kernels[width].get_local_mem_per_thread().values())}
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'width': width, 'repeat': repeat, 'seconds': elapsed,
                    'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['speedup_over_first_width'] = statistics.median(records[args.widths[0]]['warm_sec']) / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays, 'emitter_types': [emitter.emitter_type for emitter in scene.emitters],
            'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_source_ordered_emission_tile_probe_v1', 'delivery': {'path': 'source_checkout',
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
