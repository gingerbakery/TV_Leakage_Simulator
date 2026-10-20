from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import asdict
import hashlib
import inspect
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace

MODES = ('original', 'regs64', 'regs80', 'config', 'models', 'optics', 'models_regs80')
OPTICAL_FIELDS = ('face_reflectance', 'face_roughness', 'face_scatter',
    'face_specular_ratio', 'face_gaussian_sigma_deg')
SCATTER_NAMES = {name: getattr(resident, name) for name in (
    'SCATTER_NONE', 'SCATTER_SPECULAR', 'SCATTER_LAMBERTIAN', 'SCATTER_GAUSSIAN', 'SCATTER_MIXED')}


def signature_key(constants, optical, models, registers):
    def value_key(value):
        return ('float64', int(np.float64(value).view(np.uint64))) if isinstance(value, float) else ('integer', int(value))
    return (tuple((name, value_key(value)) for name, value in sorted(constants.items())),
        tuple((name, value_key(value)) for name, value in sorted(optical.items())), tuple(models), registers)


def context_values(context, mode):
    models = tuple(int(value) for value in np.unique(context.face_scatter)) if mode in ('models', 'models_regs80') else ()
    optical = {}
    if mode == 'optics':
        for name in OPTICAL_FIELDS:
            values = getattr(context, name)
            if not len(values):
                continue
            bits = values.view(np.uint64) if values.dtype == np.float64 else values
            if np.all(bits == bits[0]):
                optical[name] = values[0].item()
    return optical, models


def batch_values(batch, record_geometry, mode):
    if mode in ('original', 'regs64', 'regs80'):
        return {}
    return {'record_geometry': int(record_geometry),
        'bounce_receiver_mis_enabled': int(batch.bounce_receiver_mis_enabled),
        'angle_dependent_reflectance': int(batch.angle_dependent_reflectance),
        'termination_mode': int(batch.termination_mode)}


def specialized_factory(constants, optical, models, registers):
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    definition = next(node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')

    class Specialize(ast.NodeTransformer):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load) and node.id in constants:
                return ast.copy_location(ast.Constant(constants[node.id]), node)
            return node

        def visit_Subscript(self, node):
            if isinstance(node.value, ast.Name) and node.value.id in optical:
                if ast.unparse(node.slice) != 'face_index':
                    raise ValueError('Optical specialization must preserve face lookup identity')
                return ast.copy_location(ast.Constant(optical[node.value.id]), node)
            return self.generic_visit(node)

        def visit_Compare(self, node):
            if models and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq) and ast.unparse(node.left) == 'scatter':
                name = ast.unparse(node.comparators[0])
                if name in SCATTER_NAMES and SCATTER_NAMES[name] not in models:
                    return ast.copy_location(ast.Constant(False), node)
            return self.generic_visit(node)

    if set(constants) - set(argument.arg for argument in definition.args.args) or set(optical) - set(OPTICAL_FIELDS):
        raise ValueError('Unsupported kernel specialization contract')
    definition.body = [Specialize().visit(statement) for statement in definition.body]
    if registers is not None:
        if len(definition.decorator_list) != 1 or not isinstance(definition.decorator_list[0], ast.Call):
            raise ValueError('One CUDA kernel decorator is required')
        definition.decorator_list[0].keywords.append(ast.keyword(arg='max_registers', value=ast.Constant(registers)))
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_runtime_contract_specialization>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def kernel_resources(kernel):
    return {name: {str(key): value for key, value in function().items()} for name, function in (
        ('registers', kernel.get_regs_per_thread), ('local_bytes', kernel.get_local_mem_per_thread),
        ('shared_bytes', kernel.get_shared_mem_per_block))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays) < 1 or 'original' not in args.modes or len(set(args.modes)) != len(args.modes):
        parser.error('Positive rays, two warm repeats and a distinct original reference are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    states = {mode: {} for mode in args.modes}
    original_launch = resident._launch_resident_kernel
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        cache = {mode: {} for mode in args.modes}
        records = {mode: {'warm_sec': [], 'parity': [], 'selection_sec': [], 'kernels': {}} for mode in args.modes}
        expected = None
        for repeat in range(args.repeats + 1):
            order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
            for mode in order:
                selected_context = None
                context_contract = None
                selection_seconds = 0.0
                used_kernels = {}

                def launch(kernel, context, scene, bindings, workspace, batch, count, depths, *, record_geometry):
                    nonlocal selected_context, context_contract, selection_seconds
                    if mode == 'original':
                        used_kernels['generic'] = kernel
                        return original_launch(kernel, context, scene, bindings, workspace, batch, count, depths, record_geometry=record_geometry)
                    started = time.perf_counter()
                    if selected_context is not context:
                        context_contract = context_values(context, mode)
                        selected_context = context
                    optical, models = context_contract
                    constants = batch_values(batch, record_geometry, mode)
                    registers = 64 if mode == 'regs64' else 80 if mode in ('regs80', 'models_regs80') else None
                    key = signature_key(constants, optical, models, registers)
                    if key not in cache[mode]:
                        factory, fingerprint = specialized_factory(constants, optical, models, registers)
                        cache[mode][key] = factory(), fingerprint
                    candidate, fingerprint = cache[mode][key]
                    used_kernels[fingerprint] = candidate
                    selection_seconds += time.perf_counter() - started
                    return original_launch(candidate, context, scene, bindings, workspace, batch, count, depths, record_geometry=record_geometry)

                with variant(resident._make_kernel, states[mode]), patch.object(resident, '_launch_resident_kernel', new=launch):
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
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                record['kernels'].update((key, kernel_resources(value)) for key, value in used_kernels.items())
                if repeat:
                    record['warm_sec'].append(elapsed)
                    record['selection_sec'].append(selection_seconds)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'selection_sec': selection_seconds, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['selection_median_sec'] = statistics.median(record['selection_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [value.emitter_type for value in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'gpu_exact_runtime_contract_specialization_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
