from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
from functools import lru_cache
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

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

import benchmark_perf5_transport as transport
from benchmark_perf5_cpu import cad_case
from diagnose_perf5_gpu_roundoff import legacy_functions
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


@lru_cache(maxsize=None)
def fast_node_count(face_count, leaf_size):
    if face_count <= leaf_size:
        return 1
    middle = face_count // 2
    return 1 + fast_node_count(middle, leaf_size) + fast_node_count(face_count - middle, leaf_size)


def has_leaf_scene(scene, leaf_size):
    if leaf_size not in (1, 2, 4, 8, 16):
        return False
    faces = scene.ordered_faces
    if faces.ndim != 1 or faces.dtype != np.int64 or len(faces) < 3 or faces[-1] != pair_scene.PAIR_BVH_MAGIC:
        return False
    if len(scene.node_count) < 2 or scene.node_count[0] != 0:
        return False
    original_nodes = int(scene.node_start[0])
    metadata_start = int(faces[-3])
    path_width = int(faces[-2])
    face_count = len(scene.triangle_v0)
    if not 0 < original_nodes < len(scene.node_count) or not 1 <= path_width <= 63:
        return False
    if metadata_start <= 0 or metadata_start % 2 or metadata_start > 2 * face_count:
        return False
    if len(scene.node_count) != original_nodes + fast_node_count(metadata_start // 2, leaf_size):
        return False
    expected = metadata_start + 2 * face_count + original_nodes * (1 + path_width) + 3
    return expected == len(faces)


@lru_cache(maxsize=5)
def leaf_builder(leaf_size):
    if leaf_size not in (1, 2, 4, 8, 16):
        raise ValueError('Experimental pair leaf size must be 1, 2, 4, 8 or 16')
    tree = ast.parse(inspect.getsource(pair_scene._build_pair_scene))
    factory = tree.body[0]
    splitter_slots = [index for index, argument in enumerate(factory.args.kwonlyargs)
        if argument.arg == 'splitter_factory']
    if len(splitter_slots) != 1:
        raise ValueError('Median leaf experiments require an explicit splitter factory override')
    factory.args.kw_defaults[splitter_slots[0]] = ast.Constant(value=None)
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
        and ast.unparse(node.test) == 'len(indices) == 1']
    if len(matches) != 1:
        raise ValueError('Exactly one pair leaf cutoff is required')
    leaf = matches[0]
    expected = ast.parse('''
triangle = triangles[indices[0]]
nodes.append([triangle.bounds_min, triangle.bounds_max, -1, -1, len(ordered), 1])
ordered.extend(indices)
return address
''').body
    if [ast.dump(statement) for statement in leaf.body] != [ast.dump(statement) for statement in expected]:
        raise ValueError('The pair leaf initialization contract changed')
    leaf.body = ast.parse('''
lower = tuple(min(triangles[index].bounds_min[axis] for index in indices) for axis in range(3))
upper = tuple(max(triangles[index].bounds_max[axis] for index in indices) for axis in range(3))
nodes.append([lower, upper, -1, -1, len(ordered), len(indices)])
ordered.extend(indices)
return address
''').body
    matches[0].test = ast.Compare(left=ast.Call(func=ast.Name(id='len', ctx=ast.Load()),
        args=[ast.Name(id='indices', ctx=ast.Load())], keywords=[]),
        ops=[ast.LtE()], comparators=[ast.Constant(value=leaf_size)])
    namespace = dict(pair_scene.__dict__)
    namespace['has_pair_scene'] = lambda scene: has_leaf_scene(scene, leaf_size)
    exec(compile(ast.fix_missing_locations(tree), '<perf5_pair_leaf_scene>', 'exec'), namespace)
    return namespace['_build_pair_scene']


@contextmanager
def leaf_variant(leaf_size, states):
    builder = leaf_builder(leaf_size)
    def prepare(mesh):
        mode = pair_scene.pair_mode()
        with mesh._gpu_cuda_scene_lock:
            original = mesh.prepare_gpu_cuda_scene()
            if mode == 'off' or len(original.node_count) < 2 or (
                mode == 'auto' and len(original.ordered_faces) < pair_scene.AUTO_MIN_FACES
            ):
                return original, 0.0, False
            key = (id(mesh), leaf_size)
            cached = states.get(key)
            if cached is not None and cached[0] is original:
                return cached[1], 0.0, True
            started = time.perf_counter()
            scene = builder(mesh, original)
            states[key] = (original, scene)
            return scene, time.perf_counter() - started, False
    with patch.object(pair_scene, 'prepare_pair_scene', new=prepare), patch.object(
        pair_scene, 'has_pair_scene', new=lambda scene: has_leaf_scene(scene, leaf_size)
    ):
        yield


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--leaf-sizes', type=int, nargs='+', choices=(1, 2, 4, 8, 16), default=[1, 2, 4, 8, 16])
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
    replacements = legacy_functions(args.reference_revision)
    trace_input = cad_case(args.rays)[0]
    trace_input.config.compute_backend = 'gpu_cuda'
    trace_input.config.store_ray_paths = False
    trace_input.mesh.prepare_acceleration()
    modes = ['reference', *[str(size) for size in args.leaf_sizes]]
    records = {mode: {'warm_sec': [], 'parity': []} for mode in modes}
    scene_states = {}
    caches = {}
    expected = None
    for repeat in range(args.repeats + 1):
        for mode in modes if repeat % 2 == 0 else list(reversed(modes)):
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ, {
                    'LEAKAGE_GPU_STATIC_TRANSFER': 'legacy' if mode == 'reference' else 'packed',
                    'LEAKAGE_GPU_PAIR_BVH': 'off' if mode == 'reference' else 'auto',
                    'LEAKAGE_GPU_TRANSFER_VIEWS': 'sliced' if mode == 'reference' else 'direct',
                }))
                stack.enter_context(transport.runtime_variant('reference' if mode == 'reference' else 'candidate',
                    replacements, caches, 'gpu_cuda'))
                if mode != 'reference':
                    stack.enter_context(leaf_variant(int(mode), scene_states))
                started = time.perf_counter()
                result = raytracer.run_direct_ray_trace(trace_input)
                elapsed = time.perf_counter() - started
            performance = result.metrics['_performance_summary']
            if performance['compute_execution_state'] != 'gpu_active' or (
                performance['gpu_cuda_gpu_success_count'] <= 0
                or performance['gpu_resident_wavefront_fallback_count']
                or performance['gpu_cuda_hybrid_cpu_attempt_count']
            ):
                raise RuntimeError(f'Full CUDA execution not proven: {mode}')
            if mode != 'reference' and performance['gpu_pair_bvh_selected_batches'] == 0:
                raise RuntimeError('The leaf candidate did not execute the pair provider')
            payload = transport.semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            record = records[mode]
            record['parity'].append(parity)
            record['performance'] = performance
            if repeat:
                record['warm_sec'].append(elapsed)
            else:
                record['first_sec'] = elapsed
            if mode != 'reference':
                original, scene = scene_states[id(trace_input.mesh), int(mode)]
                record['nodes'] = len(scene.node_count)
                record['fast_nodes'] = len(scene.node_count) - len(original.node_count)
                record['scene_array_bytes'] = sum(getattr(scene, name).nbytes for name in
                    ('triangle_v0', 'triangle_edge1', 'triangle_edge2', 'traceable_face_mask',
                     'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
                     'node_start', 'node_count', 'ordered_faces'))
            print(json.dumps({'mode': mode, 'repeat': repeat, 'seconds': elapsed,
                'nodes': record.get('nodes'), 'passed': parity['passed']}), flush=True)
    for record in records.values():
        record['median_sec'] = statistics.median(record['warm_sec'])
    for record in records.values():
        record['fixed_reference_speedup'] = records['reference']['median_sec'] / record['median_sec']
        if '1' in records:
            record['current_leaf_one_speedup'] = records['1']['median_sec'] / record['median_sec']
    unchanged = hashes == transport.transport_hashes() and script_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {'contract': 'gpu_pair_fast_leaf_size_probe_v1',
        'delivery': {'path': 'source_checkout',
            'branch': subprocess.check_output(['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'reference_revision': args.reference_revision, 'candidate': 'experimental_fast_tree_leaf_sizes'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': script_hash, 'source_unchanged': unchanged,
        'rays': result.total_rays, 'surface_hits': result.surface_hit_count,
        'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
        'records': records, 'passed': unchanged and all(parity['passed']
            for record in records.values() for parity in record['parity'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
