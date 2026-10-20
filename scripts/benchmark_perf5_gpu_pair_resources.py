from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import inspect
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

import benchmark_perf5_transport as transport
from benchmark_perf5_cpu import cad_case
from diagnose_perf5_gpu_roundoff import legacy_functions
from perf4_accuracy import compare_semantic_payloads
from perf5_pair_affine_box import affine_pair_factory, padded_scene_variant
from perf5_pair_masked_box import masked_pair_factory
from perf5_pair_shared_inverse import make_factory as shared_inverse_factory
import perf5_packed_nodes as packed_nodes
from perf5_pair_coherent import make_factory as pair_coherent_factory
from perf5_pair_slot_steps import make_factory as pair_slot_factory
from perf5_pair_stack import make_kernel as pair_stack_kernel
from perf5_pair_sah import scene_variant as sah_scene_variant
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer

VARIANTS = ('reference', 'current', 'cap128', 'cap120', 'cap112', 'cap96',
    'outline', 'outline_cap128', 'outline_wrapper', 'affine', 'affine_padded', 'affine_padded_cap128',
    'masked', 'masked_affine', 'masked_affine_padded', 'shared_inverse', 'nodes64', 'nodes80',
    'coherent1', 'coherent8', 'coherent64', 'pair_spatial4', 'pair_spatial8', 'slot_steps',
    'shared_stack4', 'shared_stack8', 'immediate_pair', 'sah8', 'sah16', 'sah16_64')


def make_resource_kernel(mode):
    if mode not in VARIANTS or mode == 'reference':
        raise ValueError('A production pair resource candidate is required')
    if mode in ('nodes64', 'nodes80'):
        return packed_nodes.make_kernel(int(mode[5:]))
    if mode.startswith('coherent'):
        return pair_coherent_factory(int(mode[8:]))[0]()
    if mode.startswith('pair_spatial'):
        return pair_coherent_factory(int(mode[12:]), spatial=True)[0]()
    if mode == 'slot_steps':
        return pair_slot_factory()[0]()
    if mode.startswith('shared_stack'):
        return pair_stack_kernel(int(mode[12:]))
    if mode == 'immediate_pair':
        return pair_stack_kernel(immediate=True)
    factory = pair_bvh.make_pair_intersector
    if mode == 'shared_inverse':
        factory = shared_inverse_factory()
    elif mode == 'affine':
        factory = affine_pair_factory()
    elif mode.startswith('affine_padded'):
        factory = affine_pair_factory(padded=True)
    elif mode.startswith('masked'):
        factory = masked_pair_factory(affine='affine' in mode, padded='padded' in mode)
    if mode.startswith('outline'):
        tree = ast.parse(inspect.getsource(factory))
        name = 'pair_intersect_bvh' if mode == 'outline_wrapper' else 'isolated_intersect_bvh'
        helper = next(node for node in tree.body[0].body
            if isinstance(node, ast.FunctionDef) and node.name == name)
        decorator = helper.decorator_list[0]
        inline = next(keyword for keyword in decorator.keywords if keyword.arg == 'inline')
        inline.value = ast.Constant(value=False)
        namespace = dict(pair_bvh.__dict__)
        exec(compile(ast.fix_missing_locations(tree), '<perf5_pair_resource_intersector>', 'exec'), namespace)
        factory = namespace['make_pair_intersector']
    limit = int(mode.rsplit('cap', 1)[1]) if 'cap' in mode else None
    original_jit = gpu._CUDA.jit
    def restricted_jit(*values, **keywords):
        if limit is not None and not keywords.get('device', False):
            keywords['max_registers'] = limit
        return original_jit(*values, **keywords)
    with patch.object(gpu._CUDA, 'jit', new=restricted_jit):
        return resident._make_kernel(_intersector_factory=factory)


def kernel_resources(kernel):
    if hasattr(kernel, 'resources'):
        return {name: {'registers_per_thread': max(values['registers'].values()),
            'local_bytes_per_thread': max(values['local_bytes'].values()),
            'shared_bytes_per_block': max(values['shared_bytes'].values())}
            for name, values in kernel.resources().items()}
    if kernel is None or not kernel.signatures:
        return None
    return {'registers_per_thread': max(kernel.get_regs_per_thread().values()),
        'local_bytes_per_thread': max(kernel.get_local_mem_per_thread().values()),
        'shared_bytes_per_block': max(kernel.get_shared_mem_per_block().values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--variants', choices=VARIANTS, nargs='+', default=list(VARIANTS))
    parser.add_argument('--reference-revision', default='157230c6')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rays < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = transport.transport_hashes()
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dependencies = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
        for name in ('perf5_pair_affine_box.py', 'perf5_pair_masked_box.py', 'perf5_pair_source.py',
            'perf5_pair_shared_inverse.py', 'perf5_packed_nodes.py', 'perf5_pair_coherent.py',
            'benchmark_perf5_gpu_coherent.py', 'benchmark_perf5_gpu_frontier.py',
            'benchmark_perf5_gpu_guarded_bvh.py', 'benchmark_perf5_gpu_persistent.py',
            'benchmark_perf5_gpu_spatial.py', 'perf5_pair_slot_steps.py', 'perf5_pair_stack.py',
            'benchmark_perf5_gpu_bvh_kernel.py', 'perf5_pair_sah.py')}
    replacements = legacy_functions(args.reference_revision) if 'reference' in args.variants else []
    trace_input = cad_case(args.rays)[0]
    trace_input.config.compute_backend = 'gpu_cuda'
    trace_input.config.store_ray_paths = False
    trace_input.mesh.prepare_acceleration()
    records = {mode: {'warm_sec': [], 'parity': []} for mode in args.variants}
    states = {mode: {'kernel': None, 'compiled': False} for mode in args.variants}
    caches = {}
    padded_states = {}
    expected = None
    for repeat in range(args.repeats + 1):
        for mode in args.variants if repeat % 2 == 0 else list(reversed(args.variants)):
            state = states[mode]
            setup_sec = 0.0
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {
                    'LEAKAGE_GPU_STATIC_TRANSFER': 'legacy' if mode == 'reference' else 'packed',
                    'LEAKAGE_GPU_PAIR_BVH': 'off' if mode == 'reference' else 'auto',
                    'LEAKAGE_GPU_TRANSFER_VIEWS': 'sliced' if mode == 'reference' else 'direct',
                }))
                stack.enter_context(transport.runtime_variant('reference' if mode == 'reference' else 'candidate',
                    replacements, caches, 'gpu_cuda'))
                if 'padded' in mode:
                    stack.enter_context(padded_scene_variant(padded_states))
                if mode in ('nodes64', 'nodes80'):
                    stack.enter_context(packed_nodes.device_variant(int(mode[5:]),
                        padded_states.setdefault(mode, {})))
                if mode.startswith('sah'):
                    bins = int(mode[3:].split('_')[0])
                    cutoff = int(mode.split('_')[1]) if '_' in mode else 32
                    stack.enter_context(sah_scene_variant(bins, cutoff, padded_states.setdefault(mode, {})))
                if mode != 'reference':
                    if state['kernel'] is None:
                        started = time.perf_counter()
                        state['kernel'] = make_resource_kernel(mode)
                        setup_sec = time.perf_counter() - started
                    stack.enter_context(patch.object(resident, '_PAIR_KERNEL', state['kernel']))
                    stack.enter_context(patch.object(resident, '_PAIR_KERNEL_COMPILED', state['compiled']))
                started = time.perf_counter()
                result = raytracer.run_direct_ray_trace(trace_input)
                elapsed = time.perf_counter() - started
                if mode != 'reference':
                    state['compiled'] = resident._PAIR_KERNEL_COMPILED
            performance = result.metrics['_performance_summary']
            if performance['compute_execution_state'] != 'gpu_active' or (
                performance['gpu_cuda_gpu_success_count'] <= 0
                or performance['gpu_resident_wavefront_fallback_count']
                or performance['gpu_cuda_hybrid_cpu_attempt_count']
            ):
                raise RuntimeError(f'Full CUDA execution not proven: {mode}: '
                    f'{performance.get("gpu_resident_wavefront_fallback_reason")}')
            if mode != 'reference' and performance['gpu_pair_bvh_selected_batches'] == 0:
                raise RuntimeError('The resource candidate did not execute the pair provider')
            payload = transport.semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            record = records[mode]
            record['parity'].append(parity)
            record['performance'] = performance
            record['kernel_resources'] = kernel_resources(state['kernel']) if mode != 'reference' else None
            if repeat:
                record['warm_sec'].append(elapsed)
            else:
                record['first_sec'] = elapsed
                record['first_factory_setup_sec'] = setup_sec
                record['first_performance'] = dict(performance)
            print(json.dumps({'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                'resources': record['kernel_resources'], 'passed': parity['passed']}), flush=True)
    for record in records.values():
        record['median_sec'] = statistics.median(record['warm_sec'])
    baseline = records['reference'] if 'reference' in records else records[args.variants[0]]
    for record in records.values():
        record['fixed_reference_speedup'] = baseline['median_sec'] / record['median_sec']
        if 'current' in records:
            record['current_product_speedup'] = records['current']['median_sec'] / record['median_sec']
    device = gpu._CUDA.get_current_device()
    unchanged = hashes == transport.transport_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and all(
        value == hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
        for name, value in dependencies.items())
    report = {'contract': 'gpu_pair_kernel_resource_same_session_probe_v1',
        'delivery': {'path': 'source_checkout',
            'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'reference_revision': args.reference_revision, 'candidate': 'experimental_pair_kernel_candidates'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'hardware': {name: int(getattr(device, name)) for name in
            ('MAX_REGISTERS_PER_MULTIPROCESSOR', 'MAX_THREADS_PER_MULTI_PROCESSOR', 'WARP_SIZE')},
        'source_hashes': hashes, 'script_sha256': script_hash, 'dependency_hashes': dependencies, 'source_unchanged': unchanged,
        'rays': result.total_rays, 'surface_hits': result.surface_hit_count,
        'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
        'records': records, 'passed': unchanged and all(parity['passed']
            for record in records.values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
