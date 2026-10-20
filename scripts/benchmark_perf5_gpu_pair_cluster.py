from __future__ import annotations

import argparse
import ast
import builtins
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

import benchmark_perf5_gpu_isolated_cluster as cluster
from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer

BASE_FACTORY = cluster.isolated_factory


def pair_factory(*, slab_full=False, diagnostics=True):
    captured = []
    original_compile = builtins.compile
    def capture(source, filename, mode, *values, **keywords):
        if filename == '<perf5_isolated_hit_cluster_bvh>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)
    with patch.object(builtins, 'compile', side_effect=capture):
        BASE_FACTORY(4, diagnostics=diagnostics)
    if len(captured) != 1:
        raise ValueError('The isolated-cluster factory must expose one complete AST')
    tree = captured[0]
    factory = tree.body[0]
    fast = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'isolated_intersect_bvh')
    if ast.unparse(fast.body[0].targets[0]) != 'candidate_faces':
        raise ValueError('The candidate-array prefix changed')
    fast.body[:4] = ast.parse('''
first_face = -1
second_face = -1
first_distance = math.inf
second_distance = math.inf
candidate_count = 0
uncertain = 0
''').body

    class ScalarCandidates(ast.NodeTransformer):
        leaves = 0
        def visit_For(self, node):
            if ast.unparse(node.target) != 'ordered_index':
                return self.generic_visit(node)
            self.leaves += 1
            position = next(index for index, item in enumerate(node.body)
                if isinstance(item, ast.Assign) and ast.unparse(item.targets[0]) == 'distance')
            node.body = node.body[:position + 1] + ast.parse('''
if distance <= minimum_t or distance > maximum_t:
    continue
if not math.isfinite(distance):
    uncertain = 1
    continue
if distance < best_distance:
    best_distance = distance
    best_face = face_index
    if candidate_count >= 1 and first_distance > best_distance + 8e-10:
        if candidate_count == 2 and second_distance <= best_distance + 8e-10:
            first_face = second_face
            first_distance = second_distance
            candidate_count = 1
        else:
            candidate_count = 0
    elif candidate_count == 2 and second_distance > best_distance + 8e-10:
        candidate_count = 1
elif best_face < 0 and distance == best_distance:
    best_face = face_index
if distance > best_distance + 8e-10:
    continue
if u_value < 1e-8 or v_value < 1e-8 or 1.0 - u_value - v_value < 1e-8:
    uncertain = 1
if abs(distance - minimum_t) < 1e-7 or abs(distance - maximum_t) < 1e-7:
    uncertain = 1
if candidate_count == 0:
    first_face = face_index
    first_distance = distance
    candidate_count = 1
elif candidate_count == 1:
    second_face = face_index
    second_distance = distance
    candidate_count = 2
else:
    uncertain = 1
''').body
            return node
    transform = ScalarCandidates()
    fast = transform.visit(fast)
    if transform.leaves != 1:
        raise ValueError('The original triangle intersection loop must be unique')
    traversal_index = next(index for index, node in enumerate(fast.body)
        if isinstance(node, ast.While) and ast.unparse(node.test) == 'stack_size > 0')
    fast.body = fast.body[:traversal_index + 1] + ast.parse('''
if best_face < 0 or uncertain or candidate_count == 0 or best_distance > 1e4:
    return math.inf, -1, 2
near_cap = maximum_t if maximum_t < best_distance + 4e-10 else best_distance + 4e-10
if first_distance > near_cap or candidate_count == 2 and second_distance > near_cap:
    return math.inf, -1, 2
first_leaf = original_face_leaf[first_face]
if first_leaf < 0:
    return math.inf, -1, 2
first_entry = ray_box_entry(origin_x, origin_y, origin_z,
    direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
    node_bounds_min, node_bounds_max, first_leaf, minimum_t, near_cap)
if not math.isfinite(first_entry):
    return math.inf, -1, 2
if candidate_count == 1:
    return first_distance, first_face, 0
second_leaf = original_face_leaf[second_face]
if second_leaf < 0:
    return math.inf, -1, 2
if first_leaf == second_leaf:
    second_entry = first_entry
    first_precedes = original_face_order[first_face] < original_face_order[second_face]
else:
    second_entry = ray_box_entry(origin_x, origin_y, origin_z,
        direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
        node_bounds_min, node_bounds_max, second_leaf, minimum_t, near_cap)
    if not math.isfinite(second_entry):
        return math.inf, -1, 2
    differing = original_node_code[first_leaf] ^ original_node_code[second_leaf]
    if differing == 0:
        return math.inf, -1, 2
    ancestor_depth = libdevice.clzll(np.int64(differing))
    ancestor = original_node_path[first_leaf, ancestor_depth]
    left_child = node_left[ancestor]
    right_child = node_right[ancestor]
    left_entry = ray_box_entry(origin_x, origin_y, origin_z,
        direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
        node_bounds_min, node_bounds_max, left_child, minimum_t, near_cap)
    right_entry = ray_box_entry(origin_x, origin_y, origin_z,
        direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
        node_bounds_min, node_bounds_max, right_child, minimum_t, near_cap)
    if not math.isfinite(left_entry) or not math.isfinite(right_entry):
        return math.inf, -1, 2
    earlier = left_child if left_entry <= right_entry else right_child
    first_precedes = original_node_path[first_leaf, ancestor_depth + 1] == earlier
if not first_precedes:
    temporary_face = first_face
    first_face = second_face
    second_face = temporary_face
    temporary_distance = first_distance
    first_distance = second_distance
    second_distance = temporary_distance
    temporary_entry = first_entry
    first_entry = second_entry
    second_entry = temporary_entry
if first_leaf != second_leaf and second_entry > first_distance:
    return first_distance, first_face, 0
if second_distance > first_distance:
    return first_distance, first_face, 0
if abs(second_distance - first_distance) <= 1e-10 and second_face >= first_face:
    return first_distance, first_face, 0
return second_distance, second_face, 0
''').body
    if slab_full:
        box = next(node for node in factory.body
            if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry')
        specialized = copy.deepcopy(box)
        specialized.name = 'pair_ray_box_entry'
        specialized.args.args.append(ast.arg(arg='ray_nonparallel'))
        names = ', '.join(argument.arg for argument in box.args.args)
        specialized.body = ast.parse(f'''
if not ray_nonparallel:
    return ray_box_entry({names})
entry = minimum_t
exit_distance = maximum_t
''').body
        for axis, coordinate in enumerate(('x', 'y', 'z')):
            specialized.body.extend(ast.parse(f'''
axis_lower = (bounds_min[node_index, {axis}] - origin_{coordinate}) * inverse_{coordinate}
axis_upper = (bounds_max[node_index, {axis}] - origin_{coordinate}) * inverse_{coordinate}
axis_entry = axis_lower if inverse_{coordinate} > 0.0 else axis_upper
axis_exit = axis_upper if inverse_{coordinate} > 0.0 else axis_lower
if axis_entry > entry:
    entry = axis_entry
if axis_exit < exit_distance:
    exit_distance = axis_exit
''').body)
        specialized.body.extend(ast.parse('''
if exit_distance < entry:
    return math.inf
return entry
''').body)
        factory.body.insert(factory.body.index(fast), specialized)
        fast.body.insert(0, ast.parse('ray_nonparallel = abs(direction_x) >= 1e-12 and abs(direction_y) >= 1e-12 and abs(direction_z) >= 1e-12').body[0])
        class SlabCalls(ast.NodeTransformer):
            def visit_Call(self, node):
                if isinstance(node.func, ast.Name) and node.func.id == 'ray_box_entry':
                    node.func.id = 'pair_ray_box_entry'
                    node.args.append(ast.Name(id='ray_nonparallel', ctx=ast.Load()))
                return self.generic_visit(node)
        fast = SlabCalls().visit(fast)
    factory.body[factory.body.index(next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'isolated_intersect_bvh'))] = fast
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(original_compile(tree, '<perf5_scalar_pair_cluster_bvh>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def selected_factory(mode):
    def build(capacity=4, *, diagnostics=True):
        if capacity not in (4, 8):
            raise ValueError('The launch adapter requires the existing cluster ABI')
        return pair_factory(slab_full=mode == 'pair_slab', diagnostics=diagnostics)
    return build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--diagnostics', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays) < 1 or args.repeats < 2:
        parser.error('Positive rays and two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    dependencies = ('benchmark_perf5_gpu_pair_cluster.py', 'benchmark_perf5_gpu_isolated_cluster.py',
        'benchmark_perf5_gpu_guarded_bvh.py', 'perf5_isolated_hit_cluster.py', 'benchmark_perf5_leaf_loop.py',
        'benchmark_perf5_transport.py', 'benchmark_perf5_gpu_specialization.py', 'perf4_accuracy.py')
    script_hashes = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependencies}
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        states = {mode: {} for mode in ('original', 'pair', 'pair_slab')}
        records = {mode: {'warm_sec': [], 'parity': [], 'counters': []} for mode in states}
        expected = None
        for repeat in range(args.repeats + 1):
            for mode in states if repeat % 2 == 0 else tuple(reversed(states)):
                counters = [0, 0]
                setup_elapsed = 0.0
                if mode == 'original':
                    with variant(resident._make_kernel, states[mode]):
                        started = time.perf_counter()
                        result = raytracer.run_direct_ray_trace(trace_input)
                        elapsed = time.perf_counter() - started
                else:
                    setup_started = time.perf_counter()
                    with patch.object(cluster, 'isolated_factory', new=selected_factory(mode)), cluster.cluster_variant(
                        trace_input, states[mode], diagnostics=args.diagnostics
                    ) as evidence:
                        setup_elapsed = time.perf_counter() - setup_started
                        started = time.perf_counter()
                        result = raytracer.run_direct_ray_trace(trace_input)
                        elapsed = time.perf_counter() - started
                    counters = evidence['counters'].tolist()
                    records[mode]['factory_sha256'] = evidence['factory_sha256']
                payload = semantic_payload(result)
                if expected is None:
                    expected = payload
                parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                    relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
                performance = result.metrics['_performance_summary']
                if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                    raise RuntimeError(f'Full CUDA execution not proven: {name}/{mode}: {performance.get("gpu_resident_wavefront_fallback_reason")}')
                record = records[mode]
                record['parity'].append(parity)
                record['counters'].append(counters)
                record['performance'] = performance
                record['kernel_resources'] = kernel_resources(states[mode]['_KERNEL'])
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                    record['first_candidate_setup_sec'] = setup_elapsed
                    record['first_including_candidate_setup_sec'] = elapsed + setup_elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'counters': counters, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and script_hashes == {
        name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependencies}
    report = {'contract': 'scalar_pair_cluster_original_bvh_order_probe_v1', 'diagnostics': args.diagnostics,
        'delivery': {'path': 'source_checkout', 'branch': 'main', 'baseline': '157230c68dabaf5dca1f82b1767793f9ef835a0a',
            'candidate': 'uncommitted_scalar_pair_cluster_probe'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_hashes': script_hashes, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
