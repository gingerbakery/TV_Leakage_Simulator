from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
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
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import TIMINGS, WORKLOADS, cad_case, semantic_hash, source_hashes
from benchmark_perf3b2a_multibounce import build_depth_ten_case
from leakage_simulator import native_cpu_counter_wavefront as planner
from leakage_simulator import native_cpu_ordered_reducer as reducer
from leakage_simulator import raytracer


REFERENCES = (
    (planner, 'CounterWavefrontPlanInput', '__post_init__'),
    (reducer, 'OrderedSummaryBatch', '__post_init__'),
    (reducer, None, '_validate_result'),
    (raytracer, None, '_prepare_ordered_summary_batch'),
    (raytracer, None, '_stage_ordered_paths_only'),
)
SECTIONS = TIMINGS + (
    'wavefront_reducer_native_prepare_sec',
    'wavefront_reducer_native_execute_sec',
    'wavefront_reducer_native_result_validation_sec',
    'wavefront_reducer_native_apply_sec',
)


def benchmark_source_hashes():
    hashes = source_hashes()
    hashes[str(Path(__file__).relative_to(ROOT))] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return hashes


def reference_functions(revision):
    replacements, records = [], []
    sources = {}
    for module, class_name, name in REFERENCES:
        path = Path(module.__file__).relative_to(ROOT).as_posix()
        if path not in sources:
            sources[path] = subprocess.run(
                ['git', 'show', f'{revision}:{path}'], cwd=ROOT,
                check=True, capture_output=True, text=True, encoding='utf-8',
            ).stdout
        source = sources[path]
        tree = ast.parse(source)
        owner = module
        definitions = tree.body
        if class_name:
            definitions = next(
                node.body for node in definitions
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            owner = getattr(module, class_name)
        definition = next(
            node for node in definitions
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        namespace = {}
        code = compile(ast.Module(body=[definition], type_ignores=[]),
                       f'{revision}:{path}', 'exec')
        exec(code, module.__dict__, namespace)
        replacements.append((owner, name, namespace[name]))
        records.append({
            'path': path, 'class': class_name, 'function': name,
            'function_sha256': hashlib.sha256(
                ast.get_source_segment(source, definition).encode('utf-8')
            ).hexdigest(),
        })
    return replacements, records


def benchmark(args):
    revision = subprocess.run(
        ['git', 'rev-parse', '--verify', f'{args.reference_revision}^{{commit}}'],
        cwd=ROOT, capture_output=True, check=True, text=True,
    ).stdout.strip()
    replacements, reference_records = reference_functions(revision)
    hashes = benchmark_source_hashes()
    expected_by_name = {}
    if args.baseline_json:
        baseline = json.loads(args.baseline_json.read_text(encoding='utf-8'))
        if not baseline['passed']:
            raise ValueError('baseline report did not pass')
        expected_by_name = {case['name']: case['semantic_hash'] for case in baseline['cases']}
    scenes = []
    for name, builder in WORKLOADS.items():
        trace_input = builder(args.rays)
        trace_input.mesh.prepare_acceleration()
        scenes.append((name, trace_input, 0.0))
    trace_input, preparation = cad_case(args.cad_rays)
    scenes.append(('tv_corner_cad_probe', trace_input, preparation))
    for depth in (20, 50, 100, 300, 1000):
        trace_input = build_depth_ten_case(args.depth_rays)
        trace_input.config.max_depth = depth
        trace_input.optical_profiles[0].reflectance = 0.0
        trace_input.config.angle_dependent_reflectance = False
        trace_input.mesh.prepare_acceleration()
        scenes.append((f'absorber_cap_{depth}', trace_input, 0.0))
    cases = []
    for name, trace_input, preparation in scenes:
        expected = expected_by_name.get(name)
        variants = {variant: {
            'warm_sec': [], 'warm_process_sec': [],
            'sections': {section: [] for section in SECTIONS},
        } for variant in ('reference', 'optimized')}
        for repeat in range(args.repeats + 1):
            order = ('reference', 'optimized') if repeat % 2 == 0 else ('optimized', 'reference')
            for variant in order:
                measurement = variants[variant]
                with ExitStack() as context:
                    if variant == 'reference':
                        for owner, function_name, function in replacements:
                            context.enter_context(patch.object(owner, function_name, function))
                    started, process_started = time.perf_counter(), time.process_time()
                    result = raytracer.run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                    process_elapsed = time.process_time() - process_started
                digest = semantic_hash(result)
                if expected is None:
                    expected = digest
                if digest != expected:
                    raise RuntimeError(f'Semantic mismatch: {name}, {variant}, repeat {repeat}')
                performance = result.metrics['_performance_summary']
                for prefix in ('intersection', 'wavefront_planner', 'wavefront_reducer'):
                    if performance[f'{prefix}_fallback_count']:
                        raise RuntimeError(f'Unexpected {prefix} fallback: {name}, {variant}')
                if not performance['native_used'] or not performance['wavefront_reducer_native_used']:
                    raise RuntimeError(f'Native CPU execution missing: {name}, {variant}')
                if repeat == 0:
                    measurement['first_run_sec'] = elapsed
                else:
                    measurement['warm_sec'].append(elapsed)
                    measurement['warm_process_sec'].append(process_elapsed)
                    for section in SECTIONS:
                        measurement['sections'][section].append(float(performance.get(section, 0.0)))
                measurement['performance'] = performance
        for measurement in variants.values():
            measurement['warm_p50_sec'] = statistics.median(measurement['warm_sec'])
            measurement['timing_medians_sec'] = {
                name: statistics.median(samples) for name, samples in measurement.pop('sections').items()
            }
        before = variants['reference']['warm_p50_sec']
        after = variants['optimized']['warm_p50_sec']
        case = {
            'name': name, 'rays': result.total_rays, 'triangles': len(trace_input.mesh.faces),
            'preparation_sec': preparation, 'semantic_hash': expected,
            'receiver_hits': result.receiver_hit_count, 'surface_hits': result.surface_hit_count,
            'variants': variants, 'speedup': before / after,
            'time_reduction_percent': (1.0 - after / before) * 100.0,
            'no_regression_over_3_percent': after <= before * 1.03,
        }
        cases.append(case)
        print(f'{name}: {before:.6f}s -> {after:.6f}s, {case["speedup"]:.3f}x', flush=True)
    return {
        'contract': 'perf5_cpu_preparation_v1',
        'reference_revision': revision, 'reference_functions': reference_records,
        'reference_policy': 'only_five_preparation_validation_functions_replaced_all_other_cpu_settings_fixed',
        'measurement_policy': 'same_process_counterbalanced_first_then_warm_rounds',
        'workers': args.workers, 'source_hashes': hashes,
        'source_unchanged': hashes == benchmark_source_hashes(),
        'gpu_runtime_imported': 'numba.cuda' in sys.modules,
        'baseline_report': str(args.baseline_json) if args.baseline_json else None,
        'semantic_and_execution_passed': hashes == benchmark_source_hashes() and 'numba.cuda' not in sys.modules,
        'no_regression_over_3_percent': all(case['no_regression_over_3_percent'] for case in cases),
        'cases': cases,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-revision', required=True)
    parser.add_argument('--baseline-json', type=Path)
    parser.add_argument('--json-out', type=Path, required=True)
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--cad-rays', type=int, default=20000)
    parser.add_argument('--depth-rays', type=int, default=10000)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.depth_rays, args.workers, args.repeats) < 1:
        parser.error('counts must be positive')
    previous = os.environ.get('LEAKAGE_CPU_WORKERS')
    os.environ['LEAKAGE_CPU_WORKERS'] = str(args.workers)
    try:
        summary = benchmark(args)
    finally:
        if previous is None:
            os.environ.pop('LEAKAGE_CPU_WORKERS', None)
        else:
            os.environ['LEAKAGE_CPU_WORKERS'] = previous
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if summary['semantic_and_execution_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
