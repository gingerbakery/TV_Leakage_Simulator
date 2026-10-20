from __future__ import annotations

import argparse
import ast
import copy
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_receiver_grid as grid
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator.raytracer import run_direct_ray_trace


@contextmanager
def variant(mode, state):
    names = ('_KERNEL', '_PROFILE_FACE_KERNEL', '_KERNEL_COMPILED', '_PARTITION_KERNELS', '_PARTITION_COMPILED', '_GRID_KERNEL_MODE')
    saved = {name: getattr(summary, name) for name in names}
    saved_grid = grid._KERNELS
    saved_factory = grid.make_kernels
    if mode == 'ordered_scan':
        grid.make_kernels = scan_factory()
    grid._KERNELS = state.get('grid_kernels')
    for name in names:
        if name not in state:
            state[name] = False if name.endswith('COMPILED') else None
        setattr(summary, name, state[name])
    try:
        with patch.dict(os.environ, {'LEAKAGE_GPU_RECEIVER_GRIDS': 'ordered' if mode == 'ordered_scan' else mode,
            'LEAKAGE_GPU_EMITTED_FLUX': 'atomic'}):
            yield
    finally:
        state['grid_kernels'] = grid._KERNELS
        grid._KERNELS = saved_grid
        grid.make_kernels = saved_factory
        for name, value in saved.items():
            state[name] = getattr(summary, name)
            setattr(summary, name, value)


def scan_factory():
    tree = ast.parse(inspect.getsource(grid.make_kernels))

    class ScanOnly(ast.NodeTransformer):
        removed = 0

        def visit_If(self, node):
            expression = ast.unparse(node.test)
            if expression in ('bits < minimum_bits[cell]', 'bits > maximum_bits[cell]',
                    'minimum_bits[cell] == maximum_bits[cell]'):
                self.removed += 1
                return None
            return self.generic_visit(node)

        def visit_Assign(self, node):
            targets = [ast.unparse(target) for target in node.targets]
            if targets in (['minimum_bits[address]'], ['maximum_bits[address]'], ['bits']):
                self.removed += 1
                return None
            return self.generic_visit(node)

    transform = ScanOnly()
    tree = ast.fix_missing_locations(transform.visit(copy.deepcopy(tree)))
    if transform.removed != 6:
        raise ValueError('All six uniform-power shortcut operations must be removed')
    namespace = {}
    exec(compile(tree, '<perf5_scan_only_receiver_cells>', 'exec'), grid.__dict__, namespace)
    return namespace['make_kernels']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--modes', nargs='+', choices=('atomic', 'ordered', 'ordered_scan'), default=['atomic', 'ordered'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or 'ordered' not in args.modes or len(set(args.modes)) != len(args.modes) or len(args.modes) < 2:
        parser.error('At least two warm repeats, ordered mode and a distinct reference mode are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    fixture_path = ROOT / 'tests/test_perf4e_bounce_mis.py'
    fixture_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    scenes = [('reflected_receiver_mis', _build_reflected_case(rays, 7172, 'receiver_mis', backend='gpu_cuda'))
        for rays in (65536, 1000000)]
    scenes.extend((name, builder(1000000)) for name, builder in WORKLOADS.items())
    scenes.append(('tv_corner_cad_probe', cad_case(100000)[0]))
    states = {mode: {} for mode in args.modes}
    reference = 'atomic' if 'atomic' in states else 'ordered_scan'
    cases = []
    for name, trace_input in scenes:
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.mesh.prepare_acceleration()
        expected = None
        records = {mode: {'warm_sec': [], 'parity': [], 'grid_digests': []} for mode in states}
        for repeat in range(args.repeats + 1):
            order = tuple(states) if repeat % 2 == 0 else tuple(reversed(states))
            for mode in order:
                with variant(mode, states[mode]):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}')
                grids = [{field: receiver[field] for field in ('flux_lumen', 'flux_squared_lumen2_grid')}
                    for receiver in payload['receiver_grids']]
                record = records[mode]
                record['grid_digests'].append(hashlib.sha256(json.dumps(grids, sort_keys=True, allow_nan=False).encode()).hexdigest())
                record['parity'].append(parity)
                record['performance'] = performance
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['grid_repeatable'] = len(set(record['grid_digests'])) == 1
        case = {'scene': name, 'rays': result.total_rays, 'emitter_types': [value.emitter_type for value in trace_input.emitters],
            'records': records, 'reference_mode': reference,
            'speedup': records[reference]['median_sec'] / records['ordered']['median_sec'],
            'no_regression_below_three_percent': records['ordered']['median_sec'] <= records[reference]['median_sec'] * 1.03}
        cases.append(case)
        print(json.dumps({key: value for key, value in case.items() if key != 'records'}), flush=True)
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and fixture_hash == hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    report = {'contract': 'ordered_receiver_cell_grid_same_source_paired_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'fixture_sha256': fixture_hash, 'source_unchanged': unchanged,
        'cases': cases, 'performance_eligible': all(case['no_regression_below_three_percent'] for case in cases),
        'passed': unchanged and all(case['records']['ordered']['grid_repeatable'] for case in cases)
            and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
