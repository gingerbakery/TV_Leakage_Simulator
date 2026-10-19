from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf4a_target_workloads import WORKLOADS
from leakage_simulator.importers import import_geometry
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec


TIMINGS = (
    'intersection_sec', 'native_jit_compile_sec', 'native_execute_sec',
    'wavefront_state_build_sec', 'wavefront_receiver_sec', 'wavefront_geometry_sec',
    'wavefront_plan_sec', 'wavefront_commit_sec', 'wavefront_total_sec',
)


def semantic_hash(result):
    payload = result.to_dict()
    payload.pop('run_id', None)
    payload.pop('runtime_sec', None)
    payload['metrics'].pop('_performance_summary', None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_hashes():
    paths = list((ROOT / 'src' / 'leakage_simulator').glob('*.py'))
    paths.extend((Path(__file__), ROOT / 'requirements-cpu-accelerated.txt', ROOT / 'requirements-dev.txt'))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def cad_case(ray_count):
    started = time.perf_counter()
    imported = import_geometry(str(ROOT / 'samples' / 'tv_leakage_roi_right_bottom_no_gap.stp'))
    vertices = np.asarray(imported.mesh.vertices)
    minimum, maximum = vertices.min(axis=0), vertices.max(axis=0)
    center = (minimum + maximum) * 0.5
    span = maximum - minimum
    imported.mesh.prepare_acceleration()
    prepared_sec = time.perf_counter() - started
    trace_input = DirectRayTraceInput(
        mesh=imported.mesh,
        emitters=[EmitterSpec(
            emitter_id='cad_probe', emitter_type='datum_plane', center=tuple(center),
            u_axis=(1.0, 0.0, 0.0), v_axis=(0.0, 1.0, 0.0),
            width_mm=float(span[0] * 0.3), height_mm=float(span[1] * 0.3),
            direction_distribution='lambertian', power_lumen=1.0,
            ray_count=ray_count, seed=42,
        )],
        receivers=[ReceiverSpec(
            receiver_id='cad_observer',
            center=(float(center[0]), float(center[1]), float(maximum[2] + max(span) * 0.1)),
            normal=(0.0, 0.0, -1.0), width_mm=float(span[0]), height_mm=float(span[1]),
            resolution=(40, 24),
        )],
        optical_profiles=[OpticalProfile('default', 0.6, scatter_model='mixed',
                                        specular_ratio=0.5, diffuse_ratio=0.5)],
        config=RayTraceConfig(ray_count=ray_count, max_depth=10, seed=42,
                              intersection_backend='bvh', store_ray_paths=False),
    )
    return trace_input, prepared_sec


def benchmark(args):
    hashes = source_hashes()
    scenes = []
    for name, builder in WORKLOADS.items():
        trace_input = builder(args.rays)
        trace_input.mesh.prepare_acceleration()
        scenes.append((name, trace_input, 0.0))
    if not args.skip_cad:
        trace_input, preparation = cad_case(args.cad_rays)
        scenes.append(('tv_corner_cad_probe', trace_input, preparation))
    if not args.skip_depth_sweep:
        for depth in (20, 50, 100, 300, 1000):
            trace_input = build_depth_ten_case(args.depth_rays)
            trace_input.config.max_depth = depth
            trace_input.optical_profiles[0].reflectance = 0.0
            trace_input.config.angle_dependent_reflectance = False
            trace_input.mesh.prepare_acceleration()
            scenes.append((f'absorber_cap_{depth}', trace_input, 0.0))
    cases = []
    for name, trace_input, preparation in scenes:
        expected = None
        settings = [(batch_size, workers) for batch_size in args.batch_sizes for workers in args.workers]
        measurements = {setting: {'warm_sec': [], 'warm_process_sec': [],
                                   'sections': {key: [] for key in TIMINGS}} for setting in settings}
        for repeat in range(args.repeats + 1):
            order = settings if repeat % 2 == 0 else list(reversed(settings))
            for batch_size, workers in order:
                os.environ['LEAKAGE_CPU_WORKERS'] = str(workers)
                measurement = measurements[batch_size, workers]
                started, process_started = time.perf_counter(), time.process_time()
                result = run_direct_ray_trace(trace_input, intersection_batch_size=batch_size)
                elapsed, process_elapsed = time.perf_counter() - started, time.process_time() - process_started
                digest = semantic_hash(result)
                if expected is None:
                    expected = digest
                if digest != expected:
                    raise RuntimeError(f'Semantic mismatch: {name}, workers={workers}, batch={batch_size}')
                performance = result.metrics['_performance_summary']
                if performance['intersection_fallback_count'] or not performance['native_used']:
                    raise RuntimeError(f'Unexpected provider fallback: {name}')
                if repeat == 0:
                    measurement['first_run_sec'] = elapsed
                else:
                    measurement['warm_sec'].append(elapsed)
                    measurement['warm_process_sec'].append(process_elapsed)
                    for key in TIMINGS:
                        measurement['sections'][key].append(float(performance.get(key, 0.0)))
                measurement['performance'] = performance
                measurement['result'] = result
        for batch_size, workers in settings:
            measurement = measurements[batch_size, workers]
            samples = measurement['warm_sec']
            process_samples = measurement['warm_process_sec']
            performance = measurement['performance']
            result = measurement['result']
            case = {
                'name': name, 'rays': result.total_rays, 'triangles': len(trace_input.mesh.faces),
                'requested_workers': workers, 'requested_batch_size': batch_size,
                'effective_batch_size': performance['intersection_batch_max_size'],
                'first_run_sec': measurement['first_run_sec'], 'warm_sec': samples,
                'warm_p50_sec': statistics.median(samples),
                'warm_process_sec': process_samples,
                'cpu_core_equivalents': sum(process_samples) / sum(samples),
                'preparation_sec': preparation, 'semantic_hash': expected,
                'receiver_hits': result.receiver_hit_count, 'surface_hits': result.surface_hit_count,
                'timing_medians_sec': {key: statistics.median(values) for key, values in measurement['sections'].items()},
                'performance': performance,
            }
            cases.append(case)
            print(f'{name}: workers={workers}, batch={batch_size}, p50={case["warm_p50_sec"]:.6f}s', flush=True)
    return {
        'contract': 'perf5_cpu_fixed_scene_v1', 'python': platform.python_version(),
        'logical_cpus': os.cpu_count(), 'source_hashes': hashes,
        'source_unchanged': hashes == source_hashes(),
        'measurement_policy': 'first_per_setting_then_counterbalanced_warm_rounds_same_process',
        'gpu_runtime_imported': 'numba.cuda' in sys.modules,
        'passed': hashes == source_hashes() and 'numba.cuda' not in sys.modules, 'cases': cases,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--cad-rays', type=int, default=20000)
    parser.add_argument('--depth-rays', type=int, default=10000)
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 4])
    parser.add_argument('--batch-sizes', type=int, nargs='+', default=[1024, 8192])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--skip-cad', action='store_true')
    parser.add_argument('--skip-depth-sweep', action='store_true')
    parser.add_argument('--json-out', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays, args.repeats, *args.workers, *args.batch_sizes) < 1:
        parser.error('counts must be positive')
    original = os.environ.get('LEAKAGE_CPU_WORKERS')
    try:
        summary = benchmark(args)
    finally:
        if original is None:
            os.environ.pop('LEAKAGE_CPU_WORKERS', None)
        else:
            os.environ['LEAKAGE_CPU_WORKERS'] = original
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
