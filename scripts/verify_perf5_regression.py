from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import source_hashes
from leakage_simulator import gpu_cuda_intersection as gpu

MODULES = (
    'test_perf5_bsdf_integration',
    'test_perf5_warp_ordered_fold',
    'test_perf5_emitted_flux',
    'test_perf5_receiver_hit_probe',
    'test_perf5_receiver_grid',
    'test_perf5_presence_flags',
    'test_perf5_gpu_event_window',
    'test_perf5_receiver_flux',
    'test_perf5_receiver_moments',
    'test_perf5_optical_palette',
    'test_perf5_primary_prefetch',
    'test_perf5_native_primary',
    'test_perf5_gpu_partitions',
    'test_perf5_cpu_transport',
    'test_perf5_cpu_parallel',
    'test_perf5_cpu_preparation',
    'test_native_cpu_backend_perf3b2',
    'test_perf3c_counter_wavefront_planner',
    'test_perf3b2a_multibounce_wavefront',
    'test_perf3b2b_native_wavefront_planner',
    'test_perf3b2b_wavefront_compaction',
    'test_perf3b2c_ordered_reducer',
    'test_perf3b2c2_native_ordered_reducer',
    'test_perf3b2c1_runtime_integration',
    'test_perf3b2c1_event_tape_validation',
    'test_optical_transport_audit',
    'test_optical_portable_audit',
    'test_surface_distribution_audit',
    'test_geometry_optical_audit',
    'test_termination_policy',
    'test_gpu_source_bootstrap',
    'test_emitter_aim',
    'test_emitter_aim_sphere',
    'test_emitter_aim_proximity',
    'test_aim_distribution_transport',
    'test_aim_angular_sampling',
    'test_perf5_gpu_workspace',
    'test_perf4b_gpu_resident_wavefront',
    'test_perf4c_gpu_summary_accumulator',
    'test_perf4d_compact_workspace',
    'test_perf4e_bounce_mis',
    'test_gpu_face_emitter_batch',
    'test_perf5_barycentric_probe',
    'test_perf5_gpu_primary_order',
    'test_perf5_guarded_bvh',
    'test_perf5_gpu_scheduling_probes',
    'test_perf5_gpu_frontier',
    'test_perf5_gpu_axis_triangles',
    'test_perf5_optical_routing_cache',
    'test_perf5_gpu_slab',
    'test_perf5_gpu_coherent',
    'test_perf5_gpu_spatial',
    'test_perf5_isolated_hit_cluster',
    'test_perf5_gpu_isolated_cluster',
    'test_perf5_gpu_pair_cluster',
    'test_perf5_gpu_static_transfer',
    'test_perf5_gpu_pair_production',
    'test_perf5_gpu_transfer_views',
    'test_perf5_gpu_pair_leaf',
    'test_perf5_pair_affine_box',
    'test_perf5_pair_masked_box',
    'test_perf5_uniform_emissions',
    'test_perf5_pair_sah',
    'test_perf5_pair_traversal_profile',
)


def test_hashes():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / 'tests').glob('*.py')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--modules', nargs='+', default=MODULES)
    parser.add_argument('--expected-tests', type=int, default=580)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    sources = source_hashes()
    tests = test_hashes()
    scripts = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / 'scripts').glob('*.py')}
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.modules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    unchanged = sources == source_hashes() and tests == test_hashes() and scripts == {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (ROOT / 'scripts').glob('*.py')}
    report = {'contract': 'perf5_frozen_source_real_cuda_regression_v1',
        'delivery': {'path': 'source_checkout',
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip()},
        'modules': list(args.modules), 'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': sources, 'test_hashes': tests, 'script_hashes': scripts,
        'source_unchanged': unchanged, 'expected_tests': args.expected_tests, 'tests_run': result.testsRun,
        'failures': result.failures, 'errors': result.errors, 'skips': result.skipped,
        'seconds': time.perf_counter() - started,
        'passed': unchanged and result.wasSuccessful() and not result.skipped
            and result.testsRun == args.expected_tests}
    for name in ('failures', 'errors'):
        report[name] = [(str(test), detail) for test, detail in report[name]]
    report['skips'] = [(str(test), reason) for test, reason in report['skips']]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
