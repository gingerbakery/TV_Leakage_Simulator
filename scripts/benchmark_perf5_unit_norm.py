from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import copy
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

import benchmark_perf5_transport as transport
from benchmark_perf5_cpu import WORKLOADS, cad_case
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace


def factory_tree(shortcut):
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    helpers = [node for node in tree.body[0].body
        if isinstance(node, ast.FunctionDef) and node.name == 'normalize']
    if len(helpers) != 1:
        raise RuntimeError('Expected exactly one production normalize helper')
    helper = helpers[0]
    matches = [index for index, node in enumerate(helper.body)
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'inverse']
    if len(matches) != 1:
        raise RuntimeError('Expected one reciprocal magnitude operation')
    if shortcut:
        helper.body[matches[0]:matches[0]] = ast.parse(
            'if magnitude_squared == 1.0:\n    return x_value, y_value, z_value').body
    return ast.fix_missing_locations(tree), helper


def candidate_factory():
    tree, _ = factory_tree(True)
    namespace = dict(vars(resident))
    exec(compile(tree, '<perf5_exact_unit_factory>', 'exec'), namespace)
    return namespace['_make_kernel']


def normalized_helper(shortcut):
    _, helper = factory_tree(shortcut)
    definition = copy.deepcopy(helper)
    tree = ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[]))
    namespace = dict(vars(resident))
    namespace['cuda'] = gpu._CUDA
    exec(compile(tree, '<perf5_exact_unit_helper>', 'exec'), namespace)
    return namespace['normalize']


@contextmanager
def kernel_variant(mode, factory, caches):
    saved_pair = resident._PAIR_KERNEL, resident._PAIR_KERNEL_COMPILED
    pair_state = caches.setdefault(('pair', mode), (None, False))
    resident._PAIR_KERNEL, resident._PAIR_KERNEL_COMPILED = pair_state
    try:
        with transport.runtime_variant(mode, (), caches, 'gpu_cuda'):
            if mode == 'exact_unit':
                with patch.object(resident, '_make_kernel', factory):
                    yield
            else:
                yield
    finally:
        caches['pair', mode] = resident._PAIR_KERNEL, resident._PAIR_KERNEL_COMPILED
        resident._PAIR_KERNEL, resident._PAIR_KERNEL_COMPILED = saved_pair


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=1000000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS),
        default=['cad', *WORKLOADS])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2:
        parser.error('Positive Ray counts and at least two warm runs are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh'
        or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = transport.transport_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    factory = candidate_factory()
    caches = {}
    cases = []
    for name in args.scenes:
        scene = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        scene.config.compute_backend = 'gpu_cuda'
        scene.config.store_ray_paths = False
        scene.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': []} for mode in ('current', 'exact_unit')}
        expected = None
        for repeat in range(args.repeats + 1):
            order = tuple(records) if repeat % 2 == 0 else tuple(reversed(records))
            for mode in order:
                with kernel_variant(mode, factory, caches):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(scene)
                    elapsed = time.perf_counter() - started
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or (
                    performance['gpu_cuda_gpu_success_count'] <= 0
                    or performance['gpu_resident_wavefront_fallback_count']
                    or performance['gpu_cuda_hybrid_cpu_attempt_count']
                    or performance.get('native_primary_preparation_fallback_batches', 0)
                ):
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}')
                payload = transport.semantic_payload(result)
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
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['speedup_over_current'] = statistics.median(records['current']['warm_sec']) / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'max_depth': scene.config.max_depth, 'triangles': len(scene.mesh.faces),
            'emitter_types': [emitter.emitter_type for emitter in scene.emitters],
            'records': records})
    resources = {}
    for mode in ('current', 'exact_unit'):
        generic = caches.get(mode, {}).get((resident, '_KERNEL'))
        pair = caches.get(('pair', mode), (None, False))[0]
        resources[mode] = {}
        for tag, kernel in (('generic', generic), ('pair', pair)):
            if kernel is not None and kernel.signatures:
                resources[mode][tag] = {
                    'registers_per_thread': max(kernel.get_regs_per_thread().values()),
                    'local_bytes_per_thread': max(kernel.get_local_mem_per_thread().values())}
    unchanged = hashes == transport.transport_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'exact_unit_fp64_normalization_same_session_probe_v1',
        'delivery': {'path': 'source_checkout',
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'candidate': 'experimental_factory_only'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged,
        'kernel_resources': resources, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
