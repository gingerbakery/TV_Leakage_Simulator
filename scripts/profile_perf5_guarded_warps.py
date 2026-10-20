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
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_gpu_guarded_bvh import GuardedLaunch, dual_scene, guarded_factory
from benchmark_perf5_leaf_loop import variant
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def profiled_factory():
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
    factory.body.insert(0, ast.parse("libdevice = importlib.import_module('numba.cuda.libdevice')").body[0])
    wrapper = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'intersect_bvh')
    group_counter = '''
if (cuda.threadIdx.x & 31) == libdevice.ffs(np.int32(cuda.activemask())) - 1:
    cuda.atomic.add(canonical_replays, {slot}, 1)
'''
    modified = 0
    for statement in wrapper.body:
        if isinstance(statement, ast.If) and any(isinstance(node, ast.Call)
                and ast.unparse(node.func) == 'cuda.atomic.add'
                and ast.unparse(node.args[0]) == 'canonical_replays'
                and ast.unparse(node.args[1]) == '0' for node in ast.walk(statement)):
            statement.body.insert(0, ast.parse(group_counter.format(slot=1)).body[0])
            modified += 1
    if modified != 2:
        raise ValueError('Both actual canonical replay branches must be instrumented')
    wrapper.body.insert(0, ast.parse(group_counter.format(slot=2)).body[0])
    tree = ast.fix_missing_locations(tree)
    namespace = {}
    exec(original_compile(tree, '<profile_guarded_query_subgroups>', 'exec'), resident.__dict__, namespace)
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
    source_files = [Path(__file__), ROOT / 'scripts/benchmark_perf5_gpu_guarded_bvh.py']
    script_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}
    trace_input = cad_case(args.rays)[0]
    trace_input.config.compute_backend = 'gpu_cuda'
    merged, preparation = dual_scene(trace_input.mesh, padded=True)
    factory, fingerprint = profiled_factory()
    original_context = raytracer._build_gpu_resident_wavefront_context
    original_launch = resident._launch_resident_kernel
    counters = np.zeros(3, dtype=np.int64)

    def context(*values, **keywords):
        prepared = original_context(*values, **keywords)
        prepared.scene = merged
        return prepared

    def launch(kernel, *values, **keywords):
        device_counters = gpu._CUDA.to_device(np.zeros(3, dtype=np.int64))
        result = original_launch(GuardedLaunch(kernel, device_counters), *values, **keywords)
        counters[:] += device_counters.copy_to_host()
        return result

    with variant(factory, {}), patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=context), patch.object(resident, '_launch_resident_kernel', new=launch):
        started = time.perf_counter()
        result = raytracer.run_direct_ray_trace(trace_input)
        elapsed = time.perf_counter() - started
    performance = result.metrics['_performance_summary']
    if performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
        raise RuntimeError('Full CUDA execution not proven for subgroup profiling')
    queries = performance['gpu_resident_wavefront_logical_intersection_rows']
    unchanged = hashes == source_hashes() and all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value for name, value in script_hashes.items())
    report = {'contract': 'instrumented_gpu_query_subgroup_replay_counts_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_hashes': script_hashes, 'source_unchanged': unchanged,
        'factory_sha256': fingerprint, 'rays': result.total_rays,
        'emitter_types': [value.emitter_type for value in trace_input.emitters],
        'logical_queries': queries, 'canonical_queries': int(counters[0]),
        'canonical_subgroup_executions': int(counters[1]), 'query_subgroup_executions': int(counters[2]),
        'canonical_query_fraction': int(counters[0]) / queries,
        'canonical_subgroup_execution_ratio': int(counters[1]) / int(counters[2]),
        'average_query_lanes': queries / int(counters[2]),
        'average_canonical_lanes': int(counters[0]) / int(counters[1]),
        'performance': performance, 'instrumented_first_sec': elapsed, 'scene_preparation_sec': preparation,
        'timing_is_not_a_benchmark': True,
        'passed': bool(unchanged and 0 < counters[0] <= queries and 0 < counters[1] <= counters[0] and 0 < counters[2] <= queries)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({name: report[name] for name in ('passed', 'logical_queries', 'canonical_queries',
        'canonical_query_fraction', 'canonical_subgroup_execution_ratio', 'average_query_lanes', 'average_canonical_lanes')}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
