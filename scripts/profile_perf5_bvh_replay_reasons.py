from __future__ import annotations

import argparse
import ast
import builtins
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_gpu_guarded_bvh import GuardedLaunch, dual_scene, guarded_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer

REASONS = {'near_tied_faces': 1, 'barycentric_edge': 2, 'minimum_or_receiver_clearance': 4,
    'fast_miss': 8, 'fast_stack_overflow': 16, 'near_parallel_direction': 32}


def reason_factory():
    captured = []
    original_compile = builtins.compile

    def snapshot(source, filename, mode, *values, **keywords):
        if filename == '<perf5_guarded_dual_bvh>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=snapshot):
        guarded_factory(padded=True, canonical_inline=True)
    tree = captured[0]
    factory = tree.body[0]
    fast = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'fast_intersect_bvh')

    class Reasons(ast.NodeTransformer):
        modified = 0
        predicates = {ast.dump(ast.parse(expression, mode='eval').body): flag for expression, flag in (
            ('best_face >= 0 and abs(distance - best_distance) <= 1e-10', 1),
            ('u_value < 1e-8 or v_value < 1e-8 or 1.0 - u_value - v_value < 1e-8', 2),
            ('abs(distance - minimum_t) < 1e-7 or abs(distance - maximum_t) < 1e-7', 4),
        )}

        def visit_Return(self, node):
            if isinstance(node.value, ast.Tuple) and len(node.value.elts) == 3:
                node.value.elts[2] = ast.Name(id='reason_flags', ctx=ast.Load()) if isinstance(node.value.elts[2], ast.IfExp) else ast.Constant(8)
            return node

        def visit_If(self, node):
            self.generic_visit(node)
            condition = ast.unparse(node.test)
            flag = self.predicates.get(ast.dump(node.test), 0)
            if flag:
                node.body.extend(ast.parse(f'reason_flags |= {flag}').body)
                self.modified += 1
            if condition == 'stack_size + needed > stack_width':
                node.body[0].value.elts[2] = ast.Constant(16)
                self.modified += 1
            return node

    transform = Reasons()
    fast = transform.visit(fast)
    if transform.modified != 4:
        raise ValueError('All guarded traversal reason sites must be classified')
    fast.body.insert(0, ast.parse('reason_flags = 0').body[0])
    wrapper = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    classified = 0
    for statement in wrapper.body:
        if isinstance(statement, ast.If) and ast.unparse(statement.test) == 'status != 0':
            statement.body.insert(0, ast.parse('cuda.atomic.add(canonical_replays, status, 1)').body[0])
            classified += 1
        elif isinstance(statement, ast.If) and '1e-12' in ast.unparse(statement.test):
            statement.body.insert(0, ast.parse('cuda.atomic.add(canonical_replays, 32, 1)').body[0])
            classified += 1
    if classified != 2:
        raise ValueError('All canonical replay entry branches must be classified')
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(original_compile(tree, '<perf5_guarded_replay_reasons>', 'exec'), resident.__dict__, namespace)
    return namespace['_make_kernel'], hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rays < 1:
        parser.error('A positive primary ray count is required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    dependencies = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in
        (Path(__file__), ROOT / 'scripts/benchmark_perf5_gpu_guarded_bvh.py')}
    trace_input = cad_case(args.rays)[0]
    trace_input.config.compute_backend = 'gpu_cuda'
    with variant(resident._make_kernel, {}):
        expected = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
    merged, preparation = dual_scene(trace_input.mesh, padded=True)
    candidate, fingerprint = reason_factory()
    original_context = raytracer._build_gpu_resident_wavefront_context
    original_launch = resident._launch_resident_kernel
    counters = np.zeros(65, dtype=np.int64)

    def context(*values, **keywords):
        prepared = original_context(*values, **keywords)
        prepared.scene = merged
        return prepared

    def launch(kernel, *values, **keywords):
        device_counters = gpu._CUDA.to_device(np.zeros(65, dtype=np.int64))
        result = original_launch(GuardedLaunch(kernel, device_counters), *values, **keywords)
        counters[:] += device_counters.copy_to_host()
        return result

    with variant(candidate, {}), patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=context), patch.object(resident, '_launch_resident_kernel', new=launch):
        actual = raytracer.run_direct_ray_trace(trace_input, gpu_accumulator='host')
    performance = actual.metrics['_performance_summary']
    if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
        raise RuntimeError('Full CUDA execution not proven for replay reason profiling')
    parity = compare_semantic_payloads(semantic_payload(expected), semantic_payload(actual)).to_dict()
    unchanged = hashes == source_hashes() and all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value for name, value in dependencies.items())
    reason_counts = {name: sum(int(counters[mask]) for mask in range(1, 65) if mask & flag) for name, flag in REASONS.items()}
    report = {'contract': 'instrumented_guarded_bvh_replay_reason_masks_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_hashes': dependencies, 'source_unchanged': unchanged,
        'factory_sha256': fingerprint, 'parity': parity, 'performance': performance,
        'rays': actual.total_rays, 'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
        'scene_preparation_sec': preparation, 'timing_is_not_a_benchmark': True,
        'logical_queries': performance['gpu_resident_wavefront_logical_intersection_rows'],
        'canonical_queries': int(counters[0]), 'reason_counts_nonexclusive': reason_counts,
        'mask_counts_exclusive': {str(mask): int(counters[mask]) for mask in range(1, 65) if counters[mask]},
        'passed': bool(unchanged and parity['semantic_exact'] and np.sum(counters[1:]) == counters[0])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({name: report[name] for name in ('passed', 'logical_queries', 'canonical_queries', 'reason_counts_nonexclusive', 'mask_counts_exclusive')}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
