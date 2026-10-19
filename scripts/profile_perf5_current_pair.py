from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import cad_case
from benchmark_perf5_transport import semantic_payload, transport_hashes
from perf4_accuracy import compare_semantic_payloads
from perf5_pair_traversal_profile import CountingKernel, counts_valid, make_kernel
from perf5_pair_sah import scene_variant as sah_scene_variant
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def require_cuda(performance):
    if (performance['compute_execution_state'] != 'gpu_active'
        or performance['gpu_cuda_gpu_success_count'] <= 0
        or performance['gpu_resident_wavefront_fallback_count']
        or performance['gpu_cuda_hybrid_cpu_attempt_count']
        or performance['gpu_pair_bvh_selected_batches'] <= 0):
        raise RuntimeError('Current pair profiling did not prove full CUDA execution')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--sah-bins', type=int, choices=(0, 8, 16, 32), default=0)
    parser.add_argument('--sah-cutoff', type=int, choices=(16, 32, 64, 128), default=32)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rays < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two repeated count observations required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if (not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified'))
        or preflight.preflight_scope != 'production_ray_bvh'
        or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = transport_hashes()
    dependencies = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), ROOT / 'scripts/perf5_pair_traversal_profile.py',
            ROOT / 'scripts/perf5_pair_sah.py')}
    scene = cad_case(args.rays)[0]
    scene.config.compute_backend = 'gpu_cuda'
    scene.config.store_ray_paths = False
    scene.mesh.prepare_acceleration()
    baseline_times = []
    expected = None
    for repeat in range(4):
        started = time.perf_counter()
        expected = run_direct_ray_trace(scene)
        baseline_times.append(time.perf_counter() - started)
        require_cuda(expected.metrics['_performance_summary'])
    kernel, fields, rejection_sites, fingerprint = make_kernel()
    collector = CountingKernel(kernel, gpu._CUDA, fields)
    observations = []
    scene_context = sah_scene_variant(args.sah_bins, args.sah_cutoff, {}) if args.sah_bins else nullcontext()
    with scene_context, patch.object(resident, '_PAIR_KERNEL', collector), patch.object(resident, '_PAIR_KERNEL_COMPILED', False):
        for repeat in range(args.repeats):
            collector.samples.clear()
            started = time.perf_counter()
            actual = run_direct_ray_trace(scene)
            diagnostic_sec = time.perf_counter() - started
            performance = actual.metrics['_performance_summary']
            require_cuda(performance)
            counts = collector.totals()
            parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual),
                absolute_tolerance=1e-9, relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            valid = counts_valid(counts) and counts['pair_queries'] == performance['gpu_resident_wavefront_logical_intersection_rows']
            record = {'counts': counts, 'counter_invariants_passed': valid, 'parity': parity,
                'diagnostic_sec_not_a_benchmark': diagnostic_sec, 'performance': performance}
            observations.append(record)
            print(json.dumps({'repeat': repeat, 'counts': counts, 'invariants': valid,
                'parity': parity['passed'], 'diagnostic_sec_not_a_benchmark': diagnostic_sec}), flush=True)
    unchanged = hashes == transport_hashes() and all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value
        for name, value in dependencies.items())
    stable = all(record['counts'] == observations[0]['counts'] for record in observations)
    report = {'contract': 'current_pair_traversal_counts_diagnostic_v1',
        'delivery': {'path': 'source_checkout',
            'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'dependency_hashes': dependencies, 'source_unchanged': unchanged,
        'factory_sha256': fingerprint, 'rays': actual.total_rays, 'surface_hits': actual.surface_hit_count,
        'scene_candidate': {'sah_bins': args.sah_bins, 'sah_cutoff': args.sah_cutoff},
        'emitter_types': [emitter.emitter_type for emitter in scene.emitters],
        'unmodified_baseline_first_sec': baseline_times[0], 'unmodified_baseline_warm_sec': baseline_times[1:],
        'instrumented_timings_are_not_benchmarks': True, 'rejection_site_predicates': rejection_sites,
        'counts_identical_across_repeats': stable, 'observations': observations,
        'passed': bool(unchanged and stable and all(record['counter_invariants_passed'] and record['parity']['passed']
            for record in observations))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'stable_counts': stable, 'output': str(args.output)}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
