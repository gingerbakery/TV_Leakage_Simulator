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
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_gpu_guarded_bvh import dual_scene
from benchmark_perf5_gpu_specialization import kernel_resources
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf5_isolated_hit_cluster import build_original_topology
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def isolated_factory(capacity=8, *, diagnostics=True):
    if capacity not in (4, 8):
        raise ValueError('Isolated hit clusters require four or eight slots')
    tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = tree.body[0]
    factory.body.insert(0, ast.parse('from numba.cuda import libdevice').body[0])
    original = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    canonical = copy.deepcopy(original)
    canonical.name = 'canonical_intersect_bvh'
    next(keyword for keyword in canonical.decorator_list[0].keywords
        if keyword.arg == 'inline').value = ast.Constant(False)
    fast = copy.deepcopy(original)
    fast.name = 'isolated_intersect_bvh'
    extra_names = ('original_face_leaf', 'original_face_order', 'original_node_path', 'original_node_code')
    fast.args.args.extend(ast.arg(arg=name) for name in extra_names)
    fast.body[:0] = ast.parse(f'''
candidate_faces = cuda.local.array({capacity}, dtype=np.int64)
candidate_distances = cuda.local.array({capacity}, dtype=np.float64)
candidate_count = 0
uncertain = 0
''').body

    class FastTraversal(ast.NodeTransformer):
        leaves = 0

        def visit_Assign(self, node):
            target = ast.unparse(node.targets[0]) if len(node.targets) == 1 else ''
            if target == 'root_entry':
                node.value.args[-3] = ast.parse('node_start[0]', mode='eval').body
            elif target == 'stack_nodes[base]':
                node.value = ast.parse('node_start[0]', mode='eval').body
            elif target in ('left_entry', 'right_entry'):
                node.value.args[-1] = ast.parse('maximum_t if maximum_t < best_distance + 8e-10 else best_distance + 8e-10', mode='eval').body
            return self.generic_visit(node)

        def visit_If(self, node):
            if ast.unparse(node.test) == 'entry > best_distance':
                node.test = ast.parse('entry > best_distance + 8e-10', mode='eval').body
            return self.generic_visit(node)

        def visit_Return(self, node):
            node.value.elts[2] = ast.Constant(2)
            return node

        def visit_For(self, node):
            if ast.unparse(node.target) != 'ordered_index':
                return self.generic_visit(node)
            self.leaves += 1
            distance_index = next(index for index, item in enumerate(node.body)
                if isinstance(item, ast.Assign) and ast.unparse(item.targets[0]) == 'distance')
            node.body = node.body[:distance_index + 1] + ast.parse(f'''
if distance <= minimum_t or distance > maximum_t:
    continue
if not math.isfinite(distance):
    uncertain = 1
    continue
if distance < best_distance:
    best_distance = distance
    best_face = face_index
    retained = 0
    for candidate_index in range(candidate_count):
        if candidate_distances[candidate_index] <= best_distance + 8e-10:
            candidate_faces[retained] = candidate_faces[candidate_index]
            candidate_distances[retained] = candidate_distances[candidate_index]
            retained += 1
    candidate_count = retained
elif best_face < 0 and distance == best_distance:
    best_face = face_index
if distance > best_distance + 8e-10:
    continue
if u_value < 1e-8 or v_value < 1e-8 or 1.0 - u_value - v_value < 1e-8:
    uncertain = 1
if abs(distance - minimum_t) < 1e-7 or abs(distance - maximum_t) < 1e-7:
    uncertain = 1
if candidate_count >= {capacity}:
    uncertain = 1
else:
    candidate_faces[candidate_count] = face_index
    candidate_distances[candidate_count] = distance
    candidate_count += 1
''').body
            return node

    transform = FastTraversal()
    fast = transform.visit(fast)
    if transform.leaves != 1:
        raise ValueError('The original raw triangle loop must remain unique')
    fast.body = fast.body[:-2] + ast.parse('''
if best_face < 0 or uncertain or candidate_count == 0 or best_distance > 1e4:
    return math.inf, -1, 2
near_cap = maximum_t if maximum_t < best_distance + 4e-10 else best_distance + 4e-10
for candidate_index in range(candidate_count):
    candidate_face = candidate_faces[candidate_index]
    candidate_distance = candidate_distances[candidate_index]
    if candidate_distance > near_cap:
        return math.inf, -1, 2
    original_leaf = original_face_leaf[candidate_face]
    if original_leaf < 0:
        return math.inf, -1, 2
for candidate_index in range(1, candidate_count):
    position = candidate_index
    candidate_face = candidate_faces[position]
    candidate_distance = candidate_distances[position]
    while position > 0:
        previous_face = candidate_faces[position - 1]
        first_leaf = original_face_leaf[candidate_face]
        second_leaf = original_face_leaf[previous_face]
        first_precedes = False
        if first_leaf == second_leaf:
            first_precedes = original_face_order[candidate_face] < original_face_order[previous_face]
        else:
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
            break
        candidate_faces[position] = previous_face
        candidate_distances[position] = candidate_distances[position - 1]
        position -= 1
    candidate_faces[position] = candidate_face
    candidate_distances[position] = candidate_distance
best_distance = maximum_t
best_face = -1
active_original_leaf = -1
original_leaf_allowed = False
for candidate_index in range(candidate_count):
    face_index = candidate_faces[candidate_index]
    distance = candidate_distances[candidate_index]
    original_leaf = original_face_leaf[face_index]
    if original_leaf != active_original_leaf:
        active_original_leaf = original_leaf
        original_entry = ray_box_entry(origin_x, origin_y, origin_z,
            direction_x, direction_y, direction_z, inverse_x, inverse_y, inverse_z,
            node_bounds_min, node_bounds_max, original_leaf, minimum_t, near_cap)
        if not math.isfinite(original_entry):
            return math.inf, -1, 2
        original_leaf_allowed = original_entry <= best_distance
    if not original_leaf_allowed:
        continue
    if distance <= minimum_t or distance > best_distance:
        continue
    if best_face >= 0 and abs(distance - best_distance) <= 1e-10 and face_index >= best_face:
        continue
    best_distance = distance
    best_face = face_index
if best_face < 0:
    return math.inf, -1, 2
return best_distance, best_face, 0
''').body
    wrapper = copy.deepcopy(original)
    wrapper.args.args.extend(ast.arg(arg=name) for name in (*extra_names, 'cluster_counters'))
    names = ', '.join(argument.arg for argument in original.args.args)
    extra = ', '.join(extra_names)
    replay = 'cuda.atomic.add(cluster_counters, 0, 1)' if diagnostics else 'pass'
    accepted = 'cuda.atomic.add(cluster_counters, 1, 1)' if diagnostics else 'pass'
    wrapper.body = ast.parse(f'''
if node_count.shape[0] == 0 or node_start[0] <= 0:
    return canonical_intersect_bvh({names})
if (not math.isfinite(origin_x) or not math.isfinite(origin_y) or not math.isfinite(origin_z)
    or abs(origin_x) > 1e6 or abs(origin_y) > 1e6 or abs(origin_z) > 1e6
    or not math.isfinite(direction_x) or not math.isfinite(direction_y) or not math.isfinite(direction_z)
    or 0.0 < abs(direction_x) < 1e-12 or 0.0 < abs(direction_y) < 1e-12
    or 0.0 < abs(direction_z) < 1e-12):
    {replay}
    return canonical_intersect_bvh({names})
distance, face, status = isolated_intersect_bvh({names}, {extra})
if status != 0:
    {replay}
    return canonical_intersect_bvh({names})
{accepted}
return distance, face, 0
''').body
    index = factory.body.index(original)
    factory.body[index:index + 1] = [canonical, fast, wrapper]
    kernel = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'resident_kernel')
    kernel.args.args.extend(ast.arg(arg=name) for name in (*extra_names, 'cluster_counters'))
    class Calls(ast.NodeTransformer):
        count = 0
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'intersect_bvh':
                node.args.extend(ast.Name(id=name, ctx=ast.Load()) for name in (*extra_names, 'cluster_counters'))
                self.count += 1
            return self.generic_visit(node)
    calls = Calls()
    tree = ast.fix_missing_locations(calls.visit(tree))
    if calls.count != 1:
        raise ValueError('Only the resident query may use the isolated-cluster wrapper')
    namespace = {}
    exec(compile(tree, '<perf5_isolated_hit_cluster_bvh>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class ClusterLaunch:
    def __init__(self, kernel, topology, counters):
        self.kernel, self.topology, self.counters = kernel, topology, counters

    def __getitem__(self, dimensions):
        configured = self.kernel[dimensions]
        def launch(*arguments):
            return configured(*arguments, *self.topology, self.counters)
        return launch


@contextmanager
def cluster_variant(trace_input, state, capacity=8, *, diagnostics=True):
    original_scene = trace_input.mesh.prepare_gpu_cuda_scene()
    preparation = 0.0
    if state.get('scene') is not original_scene:
        topology = build_original_topology(original_scene)
        merged, preparation = dual_scene(trace_input.mesh, padded=True)
        state['scene'] = original_scene
        state['topology'] = topology
        state['merged'] = merged
        state.pop('device_topology', None)
    topology = state['topology']
    merged = state['merged']
    factory, fingerprint = isolated_factory(capacity, diagnostics=diagnostics)
    original_context = raytracer._build_gpu_resident_wavefront_context
    original_launch = resident._launch_resident_kernel
    counters = np.zeros(2, dtype=np.int64)
    def context(*values, **keywords):
        result = original_context(*values, **keywords)
        result.scene = merged
        return result
    def launch(kernel, *values, **keywords):
        if 'device_topology' not in state:
            state['device_topology'] = tuple(gpu._CUDA.to_device(values) for values in
                (topology.face_leaf, topology.face_order, topology.node_path, topology.node_code))
        if 'device_counters' not in state:
            state['device_counters'] = gpu._CUDA.to_device(np.zeros(2, dtype=np.int64))
        device_counters = state['device_counters']
        if diagnostics:
            device_counters.copy_to_device(np.zeros(2, dtype=np.int64))
        result = original_launch(ClusterLaunch(kernel, state['device_topology'], device_counters), *values, **keywords)
        if diagnostics:
            counters[:] += device_counters.copy_to_host()
        return result
    with variant(factory, state), patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=context), patch.object(resident, '_launch_resident_kernel', new=launch):
        yield {'counters': counters, 'factory_sha256': fingerprint, 'scene_preparation_sec': preparation}


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
        parser.error('Positive ray counts and two warm repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    dependencies = ('benchmark_perf5_gpu_isolated_cluster.py', 'benchmark_perf5_gpu_guarded_bvh.py',
        'perf5_isolated_hit_cluster.py', 'benchmark_perf5_leaf_loop.py', 'benchmark_perf5_transport.py',
        'benchmark_perf5_gpu_specialization.py', 'perf4_accuracy.py')
    script_hashes = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependencies}
    cases = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        states = {mode: {} for mode in ('original', 'cluster4', 'cluster8')}
        records = {mode: {'warm_sec': [], 'parity': [], 'counters': []} for mode in states}
        expected = None
        for repeat in range(args.repeats + 1):
            for mode in states if repeat % 2 == 0 else tuple(reversed(states)):
                counters = np.zeros(2, dtype=np.int64)
                setup_started = time.perf_counter()
                setup_elapsed = 0.0
                if mode == 'original':
                    with variant(resident._make_kernel, states[mode]):
                        started = time.perf_counter()
                        result = raytracer.run_direct_ray_trace(trace_input)
                        elapsed = time.perf_counter() - started
                else:
                    with cluster_variant(trace_input, states[mode], int(mode[-1]), diagnostics=args.diagnostics) as evidence:
                        setup_elapsed = time.perf_counter() - setup_started
                        started = time.perf_counter()
                        result = raytracer.run_direct_ray_trace(trace_input)
                        elapsed = time.perf_counter() - started
                    counters = evidence['counters']
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
                record['counters'].append(counters.tolist())
                record['performance'] = performance
                record['kernel_resources'] = kernel_resources(states[mode]['_KERNEL'])
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                    record['first_candidate_setup_sec'] = setup_elapsed
                    record['first_including_candidate_setup_sec'] = elapsed + setup_elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                    'counters': counters.tolist(), 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': result.total_rays,
            'emitter_types': [value.emitter_type for value in trace_input.emitters], 'records': records})
    unchanged = hashes == source_hashes() and script_hashes == {
        name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependencies}
    report = {'contract': 'isolated_hit_cluster_original_bvh_order_probe_v1', 'diagnostics': args.diagnostics,
        'delivery': {'path': 'source_checkout', 'branch': 'main', 'baseline': '157230c68dabaf5dca1f82b1767793f9ef835a0a',
            'candidate': 'uncommitted_isolated_hit_cluster_probe'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_hashes': script_hashes, 'source_unchanged': unchanged,
        'cases': cases, 'passed': unchanged and all(parity['passed'] for case in cases
            for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
