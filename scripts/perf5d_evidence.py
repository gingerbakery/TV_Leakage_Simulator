from __future__ import annotations

import hashlib
import json
from pathlib import Path


PAIR_CONTRACT = 'strict_float64_original_order_pair_bvh_v1'
REFERENCE_CONTRACT = 'perf5d_preparation_before_bottom_up_sah_v1'
APPROVED_REFERENCE_HASHES = {
    'gpu_cuda_pair_scene.py': 'ae63a5b297c1d64166b1998e0aecab47e5511d8d10a96ed8537fa450110ee6c1',
    'gpu_cuda_pair_sah.py': '0e6152f31f9ff9cd7de70989e63de9ff14271ad8d0ddb994214576352db74227',
}
POSITIVE_COUNTERS = (
    'gpu_cuda_gpu_attempt_count', 'gpu_cuda_gpu_success_count',
    'gpu_resident_wavefront_attempt_count', 'gpu_resident_wavefront_success_count',
    'gpu_pair_bvh_selected_batches',
)
ZERO_COUNTERS = (
    'gpu_cuda_hybrid_cpu_attempt_count', 'gpu_cuda_hybrid_cpu_success_count',
    'gpu_cuda_hybrid_cpu_failure_count', 'gpu_resident_wavefront_fallback_count',
    'intersection_fallback_count', 'wavefront_planner_fallback_count',
    'wavefront_reducer_fallback_count',
)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_hashes(root):
    paths = set()
    for directory in ('src', 'scripts', 'tests'):
        paths.update((root / directory).rglob('*.py'))
    paths.update(root.glob('requirements*.txt'))
    for name in ('pyproject.toml', 'run_web_gpu.ps1'):
        path = root / name
        if path.is_file():
            paths.add(path)
    return {path.relative_to(root).as_posix(): file_sha256(path) for path in sorted(paths)}


def reference_hashes(directory):
    paths = sorted(directory.rglob('*.py'))
    required = {'gpu_cuda_pair_scene.py', 'gpu_cuda_pair_sah.py'}
    if not all((directory / name).is_file() for name in required):
        raise ValueError('Both pre-5D reference modules are required')
    return {path.relative_to(directory).as_posix(): file_sha256(path) for path in paths}


def identity_gate(before, after):
    missing = sorted(set(before) - set(after))
    added = sorted(set(after) - set(before))
    changed = sorted(key for key in set(before) & set(after) if before[key] != after[key])
    return {'passed': bool(before) and not (missing or added or changed),
        'missing': missing, 'added': added, 'changed': changed}


def reference_identity_gate(hashes):
    checks = {name: hashes.get(name) == digest for name, digest in APPROVED_REFERENCE_HASHES.items()}
    return {'contract': REFERENCE_CONTRACT, 'passed': all(checks.values()),
        'expected_hashes': APPROVED_REFERENCE_HASHES.copy(), 'checks': checks}


def transport_accuracy_gates(reference, first_same_mode, candidate):
    from perf4_accuracy import compare_semantic_payloads

    def receiver(payload):
        return {'receiver_grids': payload['receiver_grids'],
            'metrics': {key: value for key, value in payload['metrics'].items() if not key.startswith('_')}}

    strict = {'absolute_tolerance': 1e-12, 'relative_tolerance': 1e-12, 'max_ulp_distance': 32}
    physical = {'absolute_tolerance': 1e-9, 'relative_tolerance': 1e-9, 'max_ulp_distance': 1 << 48}
    return {
        'strict_all_fields': compare_semantic_payloads(reference, candidate, **strict).to_dict(),
        'repeatability': compare_semantic_payloads(first_same_mode, candidate, **strict).to_dict(),
        'parity': compare_semantic_payloads(reference, candidate, **physical).to_dict(),
        'physical_repeatability': compare_semantic_payloads(first_same_mode, candidate, **physical).to_dict(),
        'receiver_parity': compare_semantic_payloads(receiver(reference), receiver(candidate), **strict).to_dict(),
        'receiver_repeatability': compare_semantic_payloads(receiver(first_same_mode), receiver(candidate), **strict).to_dict(),
    }


def transport_acceptance_gate(run):
    checks = {name: isinstance(run.get(name), dict) and run[name].get('passed') is True
        for name in ('parity', 'physical_repeatability', 'receiver_parity',
            'receiver_repeatability', 'execution_gate')}
    return {'passed': all(checks.values()), 'checks': checks,
        'failed_checks': [name for name, passed in checks.items() if not passed]}


def pair_execution_gate(performance):
    checks = {
        'state': performance.get('compute_execution_state') == 'gpu_active',
        'reason': 'compute_execution_reason' in performance and performance['compute_execution_reason'] is None,
        'pair_contract': performance.get('gpu_pair_bvh_contract') == PAIR_CONTRACT,
        'cuda_contract': performance.get('gpu_cuda_contract') == 'strict_float64_bvh_v1',
        'resident_contract': performance.get('gpu_resident_wavefront_contract') == 'strict_float64_resident_wavefront_v1',
        'strict_float64': performance.get('gpu_cuda_strict_float64') is True
            and performance.get('gpu_resident_wavefront_strict_float64') is True,
    }
    for name in POSITIVE_COUNTERS:
        value = performance.get(name)
        checks[name] = type(value) is int and value > 0
    for name in ZERO_COUNTERS:
        value = performance.get(name)
        checks[name] = type(value) is int and value == 0
    checks['all_cuda_attempts_succeeded'] = (
        performance.get('gpu_cuda_gpu_attempt_count') == performance.get('gpu_cuda_gpu_success_count'))
    checks['all_resident_attempts_succeeded'] = (
        performance.get('gpu_resident_wavefront_attempt_count') == performance.get('gpu_resident_wavefront_success_count'))
    checks['all_resident_batches_selected_pair'] = (
        performance.get('gpu_pair_bvh_selected_batches') == performance.get('gpu_resident_wavefront_success_count'))
    return {'passed': all(checks.values()), 'checks': checks,
        'failed_checks': [name for name, passed in checks.items() if not passed]}


def artifact_record(path):
    return {'path': str(path.resolve()), 'sha256': file_sha256(path), 'bytes': path.stat().st_size}


def write_json_artifact(path, payload):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, allow_nan=False)
        stream.write('\n')
    return artifact_record(path)
