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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator.raytracer import run_direct_ray_trace

MODES = ('original', 'unrolled', 'outlined_bvh', 'outlined_box', 'outlined_both',
    'summary', 'summary_unrolled', 'summary_outlined', 'warp_flags', 'summary_flags', 'scalar_flags', 'immediate_stack',
    'early_u', 'unit_norm', 'sincos', 'unit_sincos', 'zero_pow', 'unit_sincos_powzero')


def paired_trigonometry(definition):
    class Pair(ast.NodeTransformer):
        def visit_Assign(self, node):
            target = ast.unparse(node.targets[0]) if len(node.targets) == 1 else ''
            if target == 'phi':
                return [node, ast.parse('sin_phi, cos_phi = libdevice.sincos(phi)').body[0]]
            if target == 'cosine' and ast.unparse(node.value) == 'math.cos(theta)':
                return ast.parse('sine, cosine = libdevice.sincos(theta)').body[0]
            if target == 'sine' and ast.unparse(node.value) == 'math.sin(theta)':
                return None
            return self.generic_visit(node)

        def visit_Call(self, node):
            if ast.unparse(node.func) in ('math.sin', 'math.cos') and len(node.args) == 1 and ast.unparse(node.args[0]) == 'phi':
                return ast.copy_location(ast.Name(id='sin_phi' if ast.unparse(node.func) == 'math.sin' else 'cos_phi', ctx=ast.Load()), node)
            return self.generic_visit(node)

    return Pair().visit(definition)


def early_barycentric_reject(definition):
    replacements = 0
    for leaf in ast.walk(definition):
        if not isinstance(leaf, ast.For) or ast.unparse(leaf.target) != 'ordered_index':
            continue
        body = leaf.body
        inverse_index = next(index for index, statement in enumerate(body)
            if isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'inverse_determinant')
        numerator_index = next(index for index, statement in enumerate(body)
            if isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'u_value')
        assignment = body[numerator_index]
        if not isinstance(assignment.value, ast.BinOp) or not isinstance(assignment.value.op, ast.Mult) or ast.unparse(assignment.value.right) != 'inverse_determinant':
            raise ValueError('Unchanged FP64 barycentric multiplication is required')
        numerator = ast.Assign(targets=[ast.Name(id='u_numerator', ctx=ast.Store())], value=copy.deepcopy(assignment.value.left))
        assignment.value.left = ast.Name(id='u_numerator', ctx=ast.Load())
        guard = ast.parse('''
if math.isfinite(determinant) and math.isfinite(u_numerator):
    positive_determinant = abs(determinant)
    signed_numerator = u_numerator if determinant > 0.0 else -u_numerator
    if signed_numerator < -positive_determinant * 1e-12 or signed_numerator > positive_determinant * (1.0 + 1e-12):
        continue
''').body[0]
        inverse = body.pop(inverse_index)
        numerator_index -= 1
        body[numerator_index:numerator_index] = [numerator, guard, inverse]
        replacements += 1
    if replacements != 1:
        raise ValueError('Exactly one original-order triangle loop must be guarded')
    return definition


def immediate_stack(definition):
    rewritten = []
    replacements = 0
    for statement in definition.body:
        text = ast.unparse(statement)
        if text in ('stack_entries[base] = root_entry', 'stack_nodes[base] = 0'):
            replacements += 1
            continue
        if text == 'stack_size = 1':
            rewritten.extend(ast.parse('stack_size = 0\ncurrent_node = 0\nentry = root_entry').body)
            replacements += 1
            continue
        if not isinstance(statement, ast.While) or ast.unparse(statement.test) != 'stack_size > 0':
            rewritten.append(statement)
            continue
        if [ast.unparse(value) for value in statement.body[:3]] != ['stack_size -= 1', 'entry = stack_entries[base + stack_size * stack_stride]', 'current_node = stack_nodes[base + stack_size * stack_stride]']:
            raise ValueError('Unexpected original stack pop contract')
        statement.test = ast.parse('current_node >= 0 or stack_size > 0', mode='eval').body
        pop = ast.parse('if current_node < 0:\n    pass').body[0]
        pop.body = statement.body[:3]
        body = [pop]
        for step in statement.body[3:]:
            if isinstance(step, ast.If) and ast.unparse(step.test) in ('entry > best_distance', 'leaf_count > 0'):
                if not isinstance(step.body[-1], ast.Continue):
                    raise ValueError('Expected original terminal-node continuation')
                step.body.insert(-1, ast.parse('current_node = -1').body[0])
            if isinstance(step, ast.If) and ast.unparse(step.test) == 'left_hit and right_hit':
                body.extend(ast.parse('''
if left_hit and right_hit:
    if left_entry <= right_entry:
        stack_entries[base + stack_size * stack_stride] = right_entry
        stack_nodes[base + stack_size * stack_stride] = right_index
        stack_size += 1
        current_node = left_index
        entry = left_entry
    else:
        stack_entries[base + stack_size * stack_stride] = left_entry
        stack_nodes[base + stack_size * stack_stride] = left_index
        stack_size += 1
        current_node = right_index
        entry = right_entry
elif left_hit:
    current_node = left_index
    entry = left_entry
elif right_hit:
    current_node = right_index
    entry = right_entry
else:
    current_node = -1
''').body)
                replacements += 1
            else:
                body.append(step)
        statement.body = body
        rewritten.append(statement)
        replacements += 1
    if replacements != 5:
        raise ValueError('All five immediate traversal transformations are required')
    definition.body = rewritten
    return definition


def kernel_factory(mode):
    source = inspect.getsource(resident._make_kernel)
    if mode == 'original':
        return resident._make_kernel, hashlib.sha256(source.encode()).hexdigest()
    tree = ast.parse(source)

    class Transform(ast.NodeTransformer):
        unrolled = 0
        specialized = 0
        flags = 0
        presence_mode = 0

        def visit_FunctionDef(self, node):
            if node.name == 'normalize' and mode.startswith('unit_'):
                node.body.insert(2, ast.parse('if magnitude_squared == 1.0:\n    return x_value, y_value, z_value').body[0])
            if node.name in ('lambertian', 'gaussian') and 'sincos' in mode:
                node = paired_trigonometry(node)
            if node.name == 'effective_reflectance' and (mode == 'zero_pow' or mode.endswith('powzero')):
                for index, statement in enumerate(node.body):
                    if isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'grazing_term':
                        conditional = ast.parse('if grazing_coordinate == 0.0:\n    grazing_term = 0.0\nelse:\n    pass').body[0]
                        conditional.orelse = [statement]
                        node.body[index] = conditional
                        break
            if node.name == 'intersect_bvh' and mode == 'immediate_stack':
                node = immediate_stack(node)
            if node.name == 'intersect_bvh' and mode == 'early_u':
                node = early_barycentric_reject(node)
            outlined = node.name == 'intersect_bvh' and mode in ('outlined_bvh', 'outlined_both', 'summary_outlined')
            outlined |= node.name == 'ray_box_entry' and mode in ('outlined_box', 'outlined_both')
            if outlined:
                for decorator in node.decorator_list:
                    for keyword in decorator.keywords:
                        if keyword.arg == 'inline':
                            keyword.value = ast.Constant(False)
            if node.name == 'ray_box_entry' and mode in ('unrolled', 'summary_unrolled'):
                rewritten = []
                for statement in node.body:
                    if not isinstance(statement, ast.For) or ast.unparse(statement.iter) != 'range(3)':
                        rewritten.append(statement)
                        continue
                    split = next(index for index, value in enumerate(statement.body)
                        if isinstance(value, ast.If) and any(isinstance(value, ast.Continue) for value in value.body))
                    for coordinate in range(3):
                        body = copy.deepcopy(statement.body[:split])
                        parallel = copy.deepcopy(statement.body[split])
                        parallel.body = [value for value in parallel.body if not isinstance(value, ast.Continue)]
                        parallel.orelse = copy.deepcopy(statement.body[split + 1:])
                        rewritten.extend([ast.Assign(targets=[ast.Name(id='axis', ctx=ast.Store())], value=ast.Constant(coordinate)), *body, parallel])
                    self.unrolled += 1
                node.body = rewritten
            return self.generic_visit(node)

        def visit_Name(self, node):
            if node.id == 'record_geometry' and isinstance(node.ctx, ast.Load) and mode.startswith('summary'):
                self.specialized += 1
                return ast.copy_location(ast.Constant(0), node)
            return node

        def visit_Assign(self, node):
            if mode in ('warp_flags', 'summary_flags', 'scalar_flags') and len(node.targets) == 1 and ast.unparse(node.targets[0]) == 'presence_mode':
                node.value = ast.Constant('scalar' if mode == 'scalar_flags' else 'warp')
                self.presence_mode += 1
            return self.generic_visit(node)

        def visit_Expr(self, node):
            call = node.value
            if mode in ('warp_flags', 'summary_flags') and isinstance(call, ast.Call) and ast.unparse(call.func) == 'cuda.atomic.max' and len(call.args) == 3 and ast.unparse(call.args[0]) == 'bounce_importance_counts' and ast.unparse(call.args[1]) in ('5', '6'):
                result = ast.parse('if (cuda.threadIdx.x & 31) == libdevice.ffs(np.int32(cuda.activemask())) - 1:\n    pass').body[0]
                result.body = [copy.deepcopy(node)]
                self.flags += 1
                return ast.copy_location(result, node)
            return self.generic_visit(node)

    transform = Transform()
    tree = ast.fix_missing_locations(transform.visit(tree))
    if 'sincos' in mode:
        tree.body[0].body.insert(0, ast.parse("libdevice = importlib.import_module('numba.cuda.libdevice')").body[0])
        tree = ast.fix_missing_locations(tree)
    if mode in ('unrolled', 'summary_unrolled') and transform.unrolled != 1:
        raise ValueError('Exactly one box loop must be unrolled without changing axis order')
    if mode.startswith('summary') and transform.specialized != 5:
        raise ValueError(f'Unexpected compact geometry condition count: {transform.specialized}')
    if mode in ('warp_flags', 'summary_flags', 'scalar_flags'):
        if transform.flags != 2 and transform.presence_mode != 1:
            raise ValueError('Both presence flags must use active-warp aggregation')
        if transform.flags:
            tree.body[0].body.insert(0, ast.parse("libdevice = importlib.import_module('numba.cuda.libdevice')").body[0])
            tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(compile(tree, f'<perf5_bvh_kernel_{mode}>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--cad-rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    parser.add_argument('--scenes', nargs='+', choices=('cad', *WORKLOADS), default=['cad'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 2 or min(args.rays, args.cad_rays) < 1 or 'original' not in args.modes:
        parser.error('Positive Ray count, two warm repeats and original mode are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    factories = {mode: kernel_factory(mode) for mode in args.modes}
    states = {mode: {} for mode in args.modes}
    scenes = []
    for name in args.scenes:
        trace_input = cad_case(args.cad_rays)[0] if name == 'cad' else WORKLOADS[name](args.rays)
        trace_input.config.compute_backend = 'gpu_cuda'
        trace_input.config.store_ray_paths = False
        trace_input.mesh.prepare_acceleration()
        scenes.append((name, trace_input))
    cases = []
    for name, trace_input in scenes:
        expected = None
        records = {mode: {'warm_sec': [], 'parity': [], 'factory_sha256': factories[mode][1]} for mode in args.modes}
        for repeat in range(args.repeats + 1):
            order = args.modes if repeat % 2 == 0 else list(reversed(args.modes))
            for mode in order:
                with variant(factories[mode][0], states[mode]):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input, wavefront_residency='gpu_resident')
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
                record['performance'] = performance
                registers = states[mode]['_KERNEL'].get_regs_per_thread()
                record['kernel_registers'] = {str(key): value for key, value in registers.items()}
                record['kernel_local_bytes'] = {str(key): value for key, value in states[mode]['_KERNEL'].get_local_mem_per_thread().items()}
                if repeat:
                    record['warm_sec'].append(elapsed)
                else:
                    record['first_sec'] = elapsed
                print(json.dumps({'scene': name, 'mode': mode, 'repeat': repeat, 'sec': elapsed, 'passed': parity['passed']}), flush=True)
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        for record in records.values():
            record['speedup'] = records['original']['median_sec'] / record['median_sec']
        cases.append({'scene': name, 'rays': trace_input.config.ray_count, 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'unchanged_order_bvh_kernel_specialization_probe_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged, 'cases': cases,
        'passed': unchanged and all(parity['passed'] for case in cases for record in case['records'].values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
