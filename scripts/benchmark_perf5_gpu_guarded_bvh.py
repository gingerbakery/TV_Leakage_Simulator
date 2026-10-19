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

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def dual_scene(mesh, *, padded=False):
    original = mesh.prepare_gpu_cuda_scene()
    if len(original.node_count) < 2:
        return original, 0.0
    coordinates = np.asarray(mesh.vertices, dtype=np.float64)
    if np.any(~np.isfinite(coordinates)) or np.max(np.abs(coordinates), initial=0.0) > 1e6:
        return original, 0.0
    first_norms = np.linalg.norm(original.triangle_edge1, axis=1)
    second_norms = np.linalg.norm(original.triangle_edge2, axis=1)
    cross_norms = np.linalg.norm(np.cross(original.triangle_edge1, original.triangle_edge2), axis=1)
    product = first_norms * second_norms
    eligible = product > 1e-8
    if np.any(eligible & (cross_norms < product * 1e-8)):
        return original, 0.0
    started = time.perf_counter()
    prepared = mesh._prepared_triangles
    nodes = []
    ordered = []
    maximum_depth = 0

    def build(indices, depth):
        nonlocal maximum_depth
        maximum_depth = max(maximum_depth, depth)
        lower = tuple(min(prepared[index].bounds_min[axis] for index in indices) for axis in range(3))
        upper = tuple(max(prepared[index].bounds_max[axis] for index in indices) for axis in range(3))
        address = len(nodes)
        nodes.append([lower, upper, -1, -1, 0, 0])
        if len(indices) == 1:
            nodes[address][4] = len(ordered)
            nodes[address][5] = 1
            ordered.extend(indices)
            return address
        extents = [max(prepared[index].centroid[axis] for index in indices)
            - min(prepared[index].centroid[axis] for index in indices) for axis in range(3)]
        axis = max(range(3), key=lambda value: extents[value])
        sequence = sorted(indices, key=lambda index: prepared[index].centroid[axis])
        middle = len(sequence) // 2
        nodes[address][2] = build(sequence[:middle], depth + 1)
        nodes[address][3] = build(sequence[middle:], depth + 1)
        return address

    build(original.ordered_faces.tolist(), 1)
    root = len(original.node_count)
    face_offset = len(original.ordered_faces)
    fast_lower = np.asarray([node[0] for node in nodes], dtype=np.float64)
    fast_upper = np.asarray([node[1] for node in nodes], dtype=np.float64)
    padding = 1e-7 * (np.abs(fast_lower) + np.abs(fast_upper) + 1.0) if padded else 0.0
    fields = {}
    for name, offset, dtype in (('node_bounds_min', 0, np.float64), ('node_bounds_max', 1, np.float64),
            ('node_left', 2, np.int64), ('node_right', 3, np.int64),
            ('node_start', 4, np.int64), ('node_count', 5, np.int64)):
        values = np.asarray([node[offset] for node in nodes], dtype=dtype)
        if name == 'node_bounds_min':
            values -= padding
        elif name == 'node_bounds_max':
            values += padding
        elif name in ('node_left', 'node_right'):
            values[values >= 0] += root
        elif name == 'node_start':
            values += face_offset
        combined = np.concatenate((getattr(original, name), values), axis=0)
        if name == 'node_start':
            combined[0] = root
        combined.setflags(write=False)
        fields[name] = combined
    faces = np.concatenate((original.ordered_faces, np.asarray(ordered, dtype=np.int64)))
    faces.setflags(write=False)
    return gpu.GpuCudaScene(triangle_v0=original.triangle_v0,
        triangle_edge1=original.triangle_edge1, triangle_edge2=original.triangle_edge2,
        traceable_face_mask=original.traceable_face_mask, ordered_faces=faces,
        stack_width=max(original.stack_width, maximum_depth + 2),
        build_sec=original.build_sec + time.perf_counter() - started, **fields), time.perf_counter() - started


def guarded_factory(*, padded=False, canonical_inline=False):
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    original = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    box = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry')
    canonical = copy.deepcopy(original)
    canonical.name = 'canonical_intersect_bvh'
    next(keyword for keyword in canonical.decorator_list[0].keywords if keyword.arg == 'inline').value = ast.Constant(canonical_inline)
    guarded_box = copy.deepcopy(box)
    guarded_box.name = 'guarded_ray_box_entry'

    class BoxGuard(ast.NodeTransformer):
        def visit_If(self, node):
            if ast.unparse(node.test) == 'exit_distance < entry':
                node.body = ast.parse('''
margin = 1e-7 * (abs(entry) + abs(exit_distance) + 1.0)
if entry - exit_distance <= margin:
    return minimum_t, 0
return math.inf, 0
''').body
                return node
            if 'origin_value < bounds_min[node_index, axis]' in ast.unparse(node.test):
                node.body = ast.parse('''
margin = 1e-7 * (abs(origin_value) + abs(bounds_min[node_index, axis]) + abs(bounds_max[node_index, axis]) + 1.0)
if bounds_min[node_index, axis] - origin_value <= margin and origin_value - bounds_max[node_index, axis] <= margin:
    return minimum_t, 0
return math.inf, 0
''').body
                return node
            return self.generic_visit(node)

        def visit_Return(self, node):
            flag = 1 if ast.unparse(node.value) == 'math.inf' else 0
            return ast.copy_location(ast.Return(value=ast.Tuple(elts=[node.value, ast.Constant(flag)], ctx=ast.Load())), node)

    guarded_box = BoxGuard().visit(guarded_box)
    fast = copy.deepcopy(original)
    fast.name = 'fast_intersect_bvh'
    fast.body.insert(0, ast.parse('uncertain = 0').body[0])
    box_arguments = [argument.arg for argument in box.args.args]

    class FastGuard(ast.NodeTransformer):
        def visit_Assign(self, node):
            target = ast.unparse(node.targets[0]) if len(node.targets) == 1 else ''
            if target in ('root_entry', 'left_entry', 'right_entry') and isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'ray_box_entry':
                node.value.func.id = 'ray_box_entry' if padded else 'guarded_ray_box_entry'
                if target == 'root_entry':
                    node.value.args[box_arguments.index('node_index')] = ast.parse('node_start[0]', mode='eval').body
                else:
                    node.value.args[box_arguments.index('maximum_t')] = ast.parse('best_distance + 1e-10', mode='eval').body
                if padded:
                    return node
                node.targets[0] = ast.Tuple(elts=[ast.Name(id=target, ctx=ast.Store()),
                    ast.Name(id='box_uncertain', ctx=ast.Store())], ctx=ast.Store())
                return [node, ast.parse('uncertain |= box_uncertain').body[0]]
            if target == 'stack_nodes[base]':
                node.value = ast.parse('node_start[0]', mode='eval').body
            return self.generic_visit(node)

        def visit_If(self, node):
            condition = ast.unparse(node.test)
            if condition == 'distance <= minimum_t or distance > best_distance':
                return ast.parse('''
if distance <= minimum_t or distance > maximum_t:
    continue
if best_face >= 0 and abs(distance - best_distance) <= 1e-10:
    uncertain = 1
if u_value < 1e-8 or v_value < 1e-8 or 1.0 - u_value - v_value < 1e-8:
    uncertain = 1
if abs(distance - minimum_t) < 1e-7 or abs(distance - maximum_t) < 1e-7:
    uncertain = 1
if distance > best_distance:
    continue
''').body
            if condition == 'entry > best_distance':
                if padded:
                    return ast.parse('if entry > best_distance + 1e-10:\n    continue').body[0]
                return ast.parse('''
if entry > best_distance + 1e-10:
    margin = 1e-7 * (abs(entry) + abs(best_distance) + 1.0)
    if entry - best_distance > margin:
        continue
''').body[0]
            return self.generic_visit(node)

        def visit_Return(self, node):
            if not isinstance(node.value, ast.Tuple) or len(node.value.elts) != 3:
                raise ValueError('Original intersection result must have three fields')
            if ast.unparse(node.value.elts[1]) == 'best_face':
                node.value.elts[2] = ast.parse('2 if uncertain else 0', mode='eval').body
            else:
                node.value.elts[2] = ast.Constant(2)
            return node

    fast = FastGuard().visit(fast)
    wrapper = copy.deepcopy(original)
    wrapper.args.args.append(ast.arg(arg='canonical_replays'))
    names = ', '.join(argument.arg for argument in original.args.args)
    wrapper.body = ast.parse(f'''
if node_count.shape[0] == 0 or node_start[0] <= 0:
    return canonical_intersect_bvh({names})
if (0.0 < abs(direction_x) < 1e-12 or 0.0 < abs(direction_y) < 1e-12 or 0.0 < abs(direction_z) < 1e-12):
    cuda.atomic.add(canonical_replays, 0, 1)
    return canonical_intersect_bvh({names})
distance, face, status = fast_intersect_bvh({names})
if status != 0:
    cuda.atomic.add(canonical_replays, 0, 1)
    return canonical_intersect_bvh({names})
return distance, face, 0
''').body
    original_index = factory.body.index(original)
    factory.body[original_index:original_index + 1] = [guarded_box, canonical, fast, wrapper]
    kernel = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    kernel.args.args.append(ast.arg(arg='canonical_replays'))

    class Calls(ast.NodeTransformer):
        replaced = 0

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'intersect_bvh':
                node.args.append(ast.Name(id='canonical_replays', ctx=ast.Load()))
                self.replaced += 1
            return self.generic_visit(node)

    calls = Calls()
    tree = calls.visit(tree)
    if calls.replaced != 1:
        raise ValueError('Exactly one resident traversal call must use guarded replay')
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_guarded_dual_bvh>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def unsafe_leaf_factory():
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    traversal = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    replaced = 0
    for node in ast.walk(traversal):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = ast.unparse(node.targets[0])
        if target == 'root_entry' and isinstance(node.value, ast.Call):
            node.value.args[-3] = ast.parse('node_start[0]', mode='eval').body
            replaced += 1
        elif target == 'stack_nodes[base]':
            node.value = ast.parse('node_start[0]', mode='eval').body
            replaced += 1
    if replaced != 2:
        raise ValueError('Both unsafe diagnostic root locations must be updated')
    kernel = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    kernel.args.args.append(ast.arg(arg='canonical_replays'))
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, '<perf5_unsafe_leaf_one_control>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class GuardedLaunch:
    def __init__(self, kernel, replays):
        self.kernel = kernel
        self.replays = replays

    def __getitem__(self, dimensions):
        configured = self.kernel[dimensions]

        def launch(*arguments):
            return configured(*arguments, self.replays)

        return launch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--unsafe-control', action='store_true')
    parser.add_argument('--padded-variants', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rays < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    trace_input, _ = cad_case(args.rays)
    trace_input.config.compute_backend = 'gpu_cuda'
    merged, preparation = dual_scene(trace_input.mesh)
    factory, fingerprint = guarded_factory()
    factories = {'original': resident._make_kernel, 'guarded': factory}
    if args.unsafe_control:
        factories['unsafe_leaf_one'] = unsafe_leaf_factory()[0]
    merged_scenes = {'guarded': merged, 'unsafe_leaf_one': merged}
    if args.padded_variants:
        padded_scene, padded_preparation = dual_scene(trace_input.mesh, padded=True)
        preparation += padded_preparation
        for mode, inlined in (('padded_guarded', False), ('padded_inlined', True)):
            factories[mode] = guarded_factory(padded=True, canonical_inline=inlined)[0]
            merged_scenes[mode] = padded_scene
    states = {mode: {} for mode in factories}
    records = {mode: {'warm_sec': [], 'parity': [], 'canonical_replays': []} for mode in states}
    original_context = raytracer._build_gpu_resident_wavefront_context
    original_launch = resident._launch_resident_kernel
    expected = None
    for repeat in range(args.repeats + 1):
        order = tuple(states) if repeat % 2 == 0 else tuple(reversed(states))
        for mode in order:
            replay_count = 0

            def build_context(*values, **keywords):
                context = original_context(*values, **keywords)
                if mode != 'original':
                    context.scene = merged_scenes[mode]
                return context

            def launch(kernel, *values, **keywords):
                nonlocal replay_count
                if mode == 'original':
                    return original_launch(kernel, *values, **keywords)
                replays = gpu._CUDA.to_device(np.zeros(1, dtype=np.int64))
                result = original_launch(GuardedLaunch(kernel, replays), *values, **keywords)
                replay_count += int(replays.copy_to_host()[0])
                return result

            with variant(factories[mode], states[mode]), patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=build_context), patch.object(resident, '_launch_resident_kernel', new=launch):
                started = time.perf_counter()
                result = raytracer.run_direct_ray_trace(trace_input)
                elapsed = time.perf_counter() - started
            payload = semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            performance = result.metrics['_performance_summary']
            if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                raise RuntimeError(f'Full CUDA execution not proven: {mode}')
            record = records[mode]
            record['parity'].append(parity)
            record['canonical_replays'].append(replay_count)
            record['performance'] = performance
            record['kernel_resources'] = kernel_resources(states[mode]['_KERNEL'])
            if repeat:
                record['warm_sec'].append(elapsed)
            else:
                record['first_sec'] = elapsed
            print(json.dumps({'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                'canonical_replays': replay_count, 'passed': parity['passed']}), flush=True)
    for record in records.values():
        record['median_sec'] = statistics.median(record['warm_sec'])
    for record in records.values():
        record['speedup'] = records['original']['median_sec'] / record['median_sec']
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'guarded_leaf_one_canonical_gpu_replay_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'factory_sha256': fingerprint,
        'source_unchanged': unchanged, 'scene_preparation_sec': preparation,
        'unsafe_control_is_never_production_eligible': bool(args.unsafe_control),
        'padded_guard_variants': bool(args.padded_variants),
        'rays': result.total_rays, 'emitter_types': [value.emitter_type for value in trace_input.emitters],
        'records': records, 'passed': unchanged and all(parity['passed'] for record in records.values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
