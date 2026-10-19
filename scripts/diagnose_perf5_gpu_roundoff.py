from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from test_perf4e_bounce_mis import _build_reflected_case, _semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as cuda_backend
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator import raytracer
from leakage_simulator import fast_sampling
from leakage_simulator.raytracer import run_direct_ray_trace


def legacy_functions(revision):
    replacements = []
    for module, names in (
        (raytracer, ('_build_gpu_resident_wavefront_context',)),
        (resident, ('_make_kernel', '_ensure_workspace', 'trace_resident_wavefront_gpu_cuda')),
        (summary, ('_make_kernels', '_initialize_session', 'accumulate_resident_summary_gpu_cuda')),
        (fast_sampling, ('iter_face_emitter_ray_batches', '_sample_virtual_plane_origins', '_sample_direction_batch', '_sample_direction_rows')),
    ):
        path = Path(module.__file__).relative_to(ROOT).as_posix()
        source = subprocess.run(['git', 'show', f'{revision}:{path}'], cwd=ROOT,
            check=True, capture_output=True, text=True, encoding='utf-8').stdout
        tree = ast.parse(source)
        for name in names:
            definition = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            namespace = {}
            exec(compile(ast.Module(body=[definition], type_ignores=[]), f'{revision}:{path}', 'exec'),
                 module.__dict__, namespace)
            replacements.append((module, name, namespace[name]))
            if name == 'accumulate_resident_summary_gpu_cuda':
                replacements.append((resident, name, namespace[name]))
            elif name == 'trace_resident_wavefront_gpu_cuda':
                replacements.append((raytracer, name, namespace[name]))
            elif name == 'iter_face_emitter_ray_batches':
                replacements.append((raytracer, name, namespace[name]))
    return replacements


def summarize_roundoff_cases(records):
    summary = {}
    for variant in ('legacy', 'candidate'):
        samples = records.get(variant, [])
        summary[variant + '_passed'] = bool(samples) and all(sample.get('passed') is True for sample in samples)
        summary[variant + '_passed_count'] = sum(sample.get('passed') is True for sample in samples)
        summary[variant + '_case_count'] = len(samples)
    summary['gate_scope'] = 'production_candidate'
    summary['passed'] = summary['candidate_passed']
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-revision', default='157230c6')
    parser.add_argument('--repeats', type=int, default=8)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('At least one repetition is required')
    preflight = cuda_backend.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in (
        'available', 'strict_float64', 'kernel_executed', 'kernel_verified',
    )) or preflight.preflight_scope != 'production_ray_bvh' or cuda_backend.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    baseline = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='cpu'),
        wavefront_residency='host_roundtrip')
    replacements = legacy_functions(args.reference_revision)
    records = {}
    for variant in ('legacy', 'candidate'):
        resident._reset_gpu_resident_wavefront_for_tests()
        summary.reset_gpu_summary_accumulator_for_tests()
        samples = []
        with ExitStack() as context:
            if variant == 'legacy':
                for owner, name, function in replacements:
                    context.enter_context(patch.object(owner, name, function))
            for repeat in range(args.repeats):
                result = run_direct_ray_trace(_build_reflected_case(8192, 7171, 'receiver_mis', backend='gpu_cuda'),
                    wavefront_residency='gpu_resident')
                report = compare_semantic_payloads(_semantic_payload(baseline), _semantic_payload(result),
                    absolute_tolerance=1e-12, relative_tolerance=1e-12, max_ulp_distance=32)
                performance = result.metrics['_performance_summary']
                if performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_gpu_success_count'] <= 0:
                    raise RuntimeError('CUDA execution evidence failed')
                samples.append(report.to_dict())
                print(variant, repeat, report.passed, report.max_ulp_distance, report.max_absolute_error, flush=True)
        records[variant] = samples
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = {'preflight': asdict(preflight), 'reference': args.reference_revision,
        'cases': records, **summarize_roundoff_cases(records)}
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
