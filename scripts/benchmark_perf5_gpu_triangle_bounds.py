from __future__ import annotations

import argparse
import ast
import copy
from dataclasses import asdict, replace
import hashlib
import inspect
import json
from pathlib import Path
import statistics
import sys
import time
import traceback
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace

MODES = ('original', 'bounds', 'bounds_outlined', 'pairs', 'quads', 'pairs_outlined')


def triangle_auxiliary(context, group_width=1):
    scene = context.scene
    with np.errstate(over='ignore', invalid='ignore'):
        second = scene.triangle_v0 + scene.triangle_edge1
        third = scene.triangle_v0 + scene.triangle_edge2
        lower = np.minimum(np.minimum(scene.triangle_v0, second), third)
        upper = np.maximum(np.maximum(scene.triangle_v0, second), third)
        if group_width > 1:
            leaf_mask = scene.node_count > 0
            starts = scene.node_start[leaf_mask]
            counts = scene.node_count[leaf_mask]
            groups = (counts + group_width - 1) // group_width
            group_leaves = np.repeat(np.arange(len(starts)), groups)
            ordinals = np.arange(int(groups.sum())) - np.repeat(np.cumsum(groups) - groups, groups)
            heads = starts[group_leaves] + ordinals * group_width
            stops = starts[group_leaves] + counts[group_leaves]
            faces = scene.ordered_faces[heads]
            selected_lower, selected_upper = lower[faces].copy(), upper[faces].copy()
            for offset in range(1, group_width):
                following = scene.ordered_faces[np.minimum(heads + offset, stops - 1)]
                selected_lower = np.minimum(selected_lower, lower[following])
                selected_upper = np.maximum(selected_upper, upper[following])
            lower[faces], upper[faces] = selected_lower, selected_upper
        scale = np.maximum(np.maximum(np.abs(lower), np.abs(upper)), 1.0)
        scale = np.maximum(scale, np.maximum(np.abs(scene.triangle_edge1), np.abs(scene.triangle_edge2)))
        padding = np.ldexp(scale, -40) + 1e-9
        lower -= padding
        upper += padding
    lower[~np.isfinite(lower)] = -np.inf
    upper[~np.isfinite(upper)] = np.inf
    return np.ascontiguousarray(np.concatenate((context.triangle_normals, lower, upper), axis=1))


def bounded_factory(*, outlined=False, group_width=1):
    if group_width not in (1, 2, 4):
        raise ValueError('Supported original-order triangle group widths are 1, 2 and 4')
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    original_box = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry')
    box = copy.deepcopy(original_box)
    box.name = 'ray_triangle_box_entry'
    box.args.args = [argument for argument in box.args.args if argument.arg != 'bounds_max']
    next(argument for argument in box.args.args if argument.arg == 'bounds_min').arg = 'triangle_auxiliary'
    if outlined:
        next(keyword for keyword in box.decorator_list[0].keywords if keyword.arg == 'inline').value = ast.Constant(False)

    class Bounds(ast.NodeTransformer):
        replaced = 0

        def visit_Subscript(self, node):
            if isinstance(node.value, ast.Name) and node.value.id in ('bounds_min', 'bounds_max'):
                offset = 3 if node.value.id == 'bounds_min' else 6
                if not isinstance(node.slice, ast.Tuple) or len(node.slice.elts) != 2:
                    raise ValueError('Two-dimensional triangle bounds are required')
                node.value.id = 'triangle_auxiliary'
                node.slice.elts[1] = ast.BinOp(left=node.slice.elts[1], op=ast.Add(), right=ast.Constant(offset))
                self.replaced += 1
            return self.generic_visit(node)

    transform = Bounds()
    box = transform.visit(box)
    if transform.replaced != 4:
        raise ValueError('All four original box bound reads must be adapted')
    traversal = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    traversal.args.args.append(ast.arg(arg='triangle_auxiliary'))
    leaves = [node for node in ast.walk(traversal)
        if isinstance(node, ast.For) and ast.unparse(node.target) == 'ordered_index']
    if len(leaves) != 1 or ast.unparse(leaves[0].body[2]).split('=')[0].strip() != 'edge1_x':
        raise ValueError('The original ordered triangle loop must remain unchanged')
    if group_width == 1:
        leaves[0].body[2:2] = ast.parse('''
if math.isinf(ray_triangle_box_entry(origin_x, origin_y, origin_z,
        direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
        triangle_auxiliary, face_index, minimum_t, best_distance)):
    continue
''').body
    else:
        original_loop = copy.deepcopy(leaves[0])
        original_loop.iter = ast.parse('range(group_start, group_stop)', mode='eval').body
        group_loop = ast.parse(f'''
for group_start in range(start, end, {group_width}):
    group_face = ordered_faces[group_start]
    if math.isinf(ray_triangle_box_entry(origin_x, origin_y, origin_z,
            direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
            triangle_auxiliary, group_face, minimum_t, best_distance)):
        continue
    group_stop = group_start + {group_width}
    if group_stop > end:
        group_stop = end
''').body[0]
        group_loop.body.append(original_loop)

        class GroupLoop(ast.NodeTransformer):
            def visit_For(self, node):
                if node is leaves[0]:
                    return group_loop
                return self.generic_visit(node)

        GroupLoop().visit(traversal)

    class Calls(ast.NodeTransformer):
        replaced = 0

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'intersect_bvh':
                node.args.append(ast.Name(id='triangle_normals', ctx=ast.Load()))
                self.replaced += 1
            return self.generic_visit(node)

    calls = Calls()
    tree = calls.visit(tree)
    if calls.replaced != 1:
        raise ValueError('Exactly one resident traversal call must receive the auxiliary table')
    factory.body.insert(factory.body.index(traversal), box)
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_same_order_triangle_bounds>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays) < 1 or 'original' not in args.modes:
        parser.error('Positive rays, two warm repeats and an original reference are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    widths = {mode: 2 if mode.startswith('pairs') else 4 if mode == 'quads' else 1 for mode in args.modes}
    factories = {mode: (resident._make_kernel, None) if mode == 'original' else bounded_factory(
        outlined=mode.endswith('outlined'), group_width=widths[mode]) for mode in args.modes}
    states = {mode: {} for mode in args.modes}
    original_bindings = resident._ensure_device_bindings
    original_launch = resident._launch_resident_kernel

    def launch(*values, **keywords):
        try:
            return original_launch(*values, **keywords)
        except Exception:
            traceback.print_exc()
            raise

    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': [], 'parity': [], 'preparation_sec': []} for mode in args.modes}
        expected = None
        for repeat in range(args.repeats + 1):
            order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
            for mode in order:
                captured_context = None
                captured_bindings = None
                preparation_seconds = 0.0

                def ensure(context, capability):
                    nonlocal captured_context, captured_bindings, preparation_seconds
                    if mode == 'original':
                        return original_bindings(context, capability)
                    if captured_context is context:
                        return captured_bindings, True
                    bindings, _ = original_bindings(context, capability)
                    started = time.perf_counter()
                    auxiliary = gpu._CUDA.to_device(triangle_auxiliary(context, widths[mode]))
                    gpu._CUDA.synchronize()
                    elapsed = time.perf_counter() - started
                    captured_bindings = replace(bindings, triangle_normals=auxiliary, upload_sec=bindings.upload_sec + elapsed)
                    captured_context = context
                    preparation_seconds += elapsed
                    return captured_bindings, False

                with variant(factories[mode][0], states[mode]), patch.object(resident, '_ensure_device_bindings', new=ensure), patch.object(resident, '_launch_resident_kernel', new=launch):
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
                    reasons = {field: performance.get(field) for field in ('compute_execution_state',
                        'gpu_resident_wavefront_fallback_phase', 'gpu_resident_wavefront_fallback_reason')}
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}: {reasons}')
                record = records[mode]
                record['parity'].append(parity)
                record['performance'] = performance
                record['kernel_resources'] = kernel_resources(states[mode]['_KERNEL'])
                if repeat:
                    record['warm_sec'].append(elapsed)
                    record['preparation_sec'].append(preparation_seconds)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'preparation_sec': preparation_seconds, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
            record['preparation_median_sec'] = statistics.median(record['preparation_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [value.emitter_type for value in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'same_order_conservative_triangle_bounds_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
