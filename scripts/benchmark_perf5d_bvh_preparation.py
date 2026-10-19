from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import importlib.util
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from perf5d_evidence import (artifact_record, file_sha256, identity_gate, pair_execution_gate,
    reference_hashes, reference_identity_gate, source_hashes, transport_acceptance_gate,
    transport_accuracy_gates, write_json_artifact)

ARRAYS = ('triangle_v0', 'triangle_edge1', 'triangle_edge2', 'traceable_face_mask',
    'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right', 'node_start',
    'node_count', 'ordered_faces')


def load_reference(directory, stem):
    name = 'leakage_simulator._perf5d_reference_' + stem
    path = directory / (stem + '.py')
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


def tree_hash(scene):
    digest = hashlib.sha256()
    for name in ARRAYS:
        values = getattr(scene, name)
        digest.update(name.encode())
        digest.update(str((values.shape, values.dtype.str)).encode())
        digest.update(values.tobytes())
    digest.update(str(scene.stack_width).encode())
    return digest.hexdigest()


def saved_case(path, rays):
    from leakage_simulator.api.runtime import ApiRuntime

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='perf5d-case-') as temporary:
        runtime = ApiRuntime(Path(temporary))
        restored = runtime.import_project(path)
        project = restored['project']
        workspace = project['workspace']
        if workspace['roiScopes'] or workspace['transformRules']:
            raise ValueError('This saved-case benchmark requires no ROI or transform rules')
        if not restored['trace_cached']:
            raise ValueError('A lossless saved trace mesh is required')
        scene = runtime.load_scene(restored['cad']['path'])
        assignments = [{
            'assignment_id': assignment['assignmentId'], 'target_type': assignment['targetType'],
            'component_id': assignment['componentId'], 'profile_id': 'compiled-' + assignment['assignmentId'],
            'face_indices': assignment['faceIds'] if assignment['targetType'] == 'faces' else [],
            'priority': priority, 'enabled': True,
        } for priority, assignment in enumerate(workspace['materialAssignments']) if assignment['enabled']]
        emitters = [dict(emitter, ray_count=rays) for emitter in workspace['emitters'] if emitter['enabled']]
        request = {
            'scene_token': scene['metadata']['scene_token'], 'emitters': emitters,
            'receivers': [receiver for receiver in workspace['receivers'] if receiver['enabled']],
            'optical_profiles': project['analysis_result']['optical_profiles'],
            'optical_assignments': assignments,
            'excluded_component_ids': sorted(set(workspace['excludedComponentIds'] + workspace['deletedComponentIds'])),
            'config': dict(project['analysis_result']['config'], ray_count=rays * len(emitters)),
        }
        trace_input = runtime._build_trace_input_for_request(runtime._scene_mesh_for_request(request), request)
    return trace_input, time.perf_counter() - started


def capture_tree_build(artifacts, name, repeat, scene, expected_hash, elapsed):
    import numpy as np

    digest = tree_hash(scene)
    record = {'mode': name, 'repeat': repeat, 'seconds': elapsed, 'tree_sha256': digest,
        'expected_sha256': expected_hash or digest, 'exact': expected_hash is None or digest == expected_hash,
        'tree_bytes': sum(getattr(scene, key).nbytes for key in ARRAYS)}
    if repeat == 0 or not record['exact']:
        filename = name + '-tree.npz' if repeat == 0 else f'{name}-tree-mismatch-{repeat}.npz'
        tree_path = artifacts / filename
        with tree_path.open('xb') as stream:
            np.savez_compressed(stream, **{key: getattr(scene, key) for key in ARRAYS},
                stack_width=np.asarray(scene.stack_width, dtype=np.int64))
        record['tree_artifact'] = artifact_record(tree_path)
    write_json_artifact(artifacts / f'{name}-build-{repeat}.evidence.json', record)
    return record


def trace_input_payload(trace_input):
    return {
        'project_name': trace_input.project_name, 'config': asdict(trace_input.config),
        'emitters': [asdict(emitter) for emitter in trace_input.emitters],
        'receivers': [asdict(receiver) for receiver in trace_input.receivers],
        'optical_profiles': [asdict(profile) for profile in trace_input.optical_profiles],
        'optical_assignments': [asdict(assignment) for assignment in trace_input.optical_assignments],
        'mesh': {'vertices': trace_input.mesh.vertices,
            'faces': [asdict(face) for face in trace_input.mesh.faces],
            'face_material': trace_input.mesh.face_material,
            'default_material_id': trace_input.mesh.default_material_id,
            'face_metadata': trace_input.mesh.face_metadata},
    }


def run_benchmark(args, artifacts, progress):
    from benchmark_perf5_cpu import cad_case
    from benchmark_perf5_transport import runtime_variant, semantic_payload
    from leakage_simulator import gpu_cuda_intersection as gpu
    from leakage_simulator import gpu_cuda_pair_scene as pair_scene
    from leakage_simulator.raytracer import run_direct_ray_trace

    reference_scene = load_reference(args.reference_dir, 'gpu_cuda_pair_scene')
    reference_sah = load_reference(args.reference_dir, 'gpu_cuda_pair_sah')
    builders = {
        'before': lambda mesh, original: reference_scene._build_pair_scene(mesh, original,
            splitter_factory=reference_sah.SahSplitter),
        'after': pair_scene._build_pair_scene,
    }
    preflight = None
    if args.transport:
        progress['stage'] = 'production_preflight'
        preflight = gpu.preflight_gpu_cuda(refresh=True)
        progress['preflight'] = {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT}
        if not all(getattr(preflight, field) is True for field in
            ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
            preflight.preflight_scope != 'production_ray_bvh'
            or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
        ):
            raise RuntimeError('Production CUDA preflight failed')
    progress['stage'] = 'input_preparation'
    trace_input, cad_sec = saved_case(args.project, args.rays) if args.project else cad_case(args.rays)
    if args.transport:
        trace_input.config.compute_backend = 'gpu_cuda'
    input_artifact = write_json_artifact(artifacts / 'trace-input.json', trace_input_payload(trace_input))
    progress['trace_input_artifact'] = input_artifact
    original = trace_input.mesh.prepare_gpu_cuda_scene()
    records = {name: {'build_warm_sec': [], 'transport_warm_sec': [], 'runs': []}
        for name in builders}
    progress['records'] = records
    progress['builds'] = []
    expected_hash = None
    for repeat in range(args.repeats + 1):
        order = list(builders) if repeat % 2 == 0 else list(reversed(builders))
        for name in order:
            progress['stage'] = {'kind': 'build', 'mode': name, 'repeat': repeat}
            started = time.perf_counter()
            scene = builders[name](trace_input.mesh, original)
            elapsed = time.perf_counter() - started
            build = capture_tree_build(artifacts, name, repeat, scene, expected_hash, elapsed)
            progress['builds'].append(build)
            expected_hash = build['expected_sha256']
            if not build['exact']:
                raise AssertionError('BVH arrays or metadata changed: ' + name)
            records[name]['tree_sha256'] = build['tree_sha256']
            records[name]['tree_bytes'] = build['tree_bytes']
            if repeat:
                records[name]['build_warm_sec'].append(elapsed)
            else:
                records[name]['build_first_sec'] = elapsed
                records[name]['tree_artifact'] = build['tree_artifact']
            print(json.dumps({'stage': 'build', 'mode': name, 'repeat': repeat,
                'seconds': elapsed, 'exact': True}), flush=True)
    caches = {}
    first_payloads = {}
    expected_payload = None
    if args.transport:
        for repeat in range(args.repeats + 1):
            order = list(builders) if repeat % 2 == 0 else list(reversed(builders))
            for name in order:
                progress['stage'] = {'kind': 'transport', 'mode': name, 'repeat': repeat}
                trace_input.mesh._gpu_pair_scene_cache = caches.get(name + '_tree')
                with patch.object(pair_scene, '_build_pair_scene', builders[name]), runtime_variant(
                    name, [], caches, 'gpu_cuda'
                ):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                caches[name + '_tree'] = trace_input.mesh._gpu_pair_scene_cache
                performance = result.metrics['_performance_summary']
                execution = pair_execution_gate(performance)
                payload = semantic_payload(result)
                payload_artifact = write_json_artifact(artifacts / f'{name}-run-{repeat}.json', payload)
                expected_payload = payload if expected_payload is None else expected_payload
                first_payloads.setdefault(name, payload)
                accuracy = transport_accuracy_gates(expected_payload, first_payloads[name], payload)
                run = {'repeat': repeat, 'seconds': elapsed,
                    'execution_gate': execution, 'payload_artifact': payload_artifact,
                    'performance': performance, **accuracy,
                    'total_rays': result.total_rays, 'receiver_hits': result.receiver_hit_count,
                    'surface_hits': result.surface_hit_count}
                run['acceptance_gate'] = transport_acceptance_gate(run)
                records[name]['runs'].append(run)
                write_json_artifact(artifacts / f'{name}-run-{repeat}.evidence.json', run)
                if repeat:
                    records[name]['transport_warm_sec'].append(elapsed)
                else:
                    records[name]['transport_first_sec'] = elapsed
                print(json.dumps({'stage': 'transport', 'mode': name, 'repeat': repeat,
                    'seconds': elapsed, 'passed': run['acceptance_gate']['passed'],
                    'candidate_execution': execution['passed'], 'strict_all_fields': accuracy['strict_all_fields']['passed'],
                    'receiver_exact': accuracy['receiver_parity']['semantic_exact'],
                    'max_absolute_error': accuracy['parity']['max_absolute_error']}), flush=True)
    for record in records.values():
        record['build_median_sec'] = statistics.median(record['build_warm_sec'])
        if record['transport_warm_sec']:
            record['transport_median_sec'] = statistics.median(record['transport_warm_sec'])
    runs = [run for record in records.values() for run in record['runs']]
    passed = all(transport_acceptance_gate(run)['passed'] for run in runs)
    progress['stage'] = 'completed'
    report = {'contract': 'perf5d_frozen_bvh_arrays_and_transport_v3',
        'delivery': {'path': 'source_checkout', 'commit': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'candidate': 'uncommitted_perf5d_bvh_preparation',
            'reference': 'pre-5D adopted local SAH source snapshot'},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT} if preflight else None,
        'accuracy_contract': {'tree': 'all_arrays_and_metadata_byte_exact',
            'decisions': 'exact', 'receiver': '(absolute_le_1e-12_OR_relative_le_1e-12)_AND_ULP_le_32',
            'all_fields': '(absolute_le_1e-9_OR_relative_le_1e-9)_AND_ULP_le_2**48',
            'same_mode_repeats': 'receiver_and_physical_gates_are_both_required',
            'strict_all_fields': 'reported_separately_including_same_mode_repeatability'},
        'project': {'path': str(args.project), 'sha256': file_sha256(args.project)}
            if args.project else None,
        'trace_input_artifact': input_artifact,
        'scene': {'triangles': len(trace_input.mesh.faces), 'rays': args.rays,
            'max_depth': trace_input.config.max_depth, 'cad_import_and_canonical_bvh_sec': cad_sec,
            'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
            'distribution': [emitter.direction_distribution for emitter in trace_input.emitters]},
        'records': records, 'builds': progress['builds'], 'passed': passed,
        'pass_scope': 'tree_and_declared_receiver_and_physical_regression_gates' if args.transport else 'tree_only',
        'transport_verified': bool(runs) and passed,
        'strict_all_fields_passed': all(run['strict_all_fields']['passed'] and run['repeatability']['passed']
            for run in runs) if runs else None,
        'nonzero_receiver_evidence': bool(runs) and all(run['receiver_hits'] > 0 for run in runs)}
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--transport', action='store_true')
    parser.add_argument('--project', type=Path)
    args = parser.parse_args()
    if args.repeats < 2 or args.rays < 1:
        parser.error('At least two warm repeats and positive rays are required')
    args.reference_dir = args.reference_dir.resolve()
    args.output = args.output.resolve()
    input_path = args.project.resolve() if args.project else ROOT / 'samples' / 'tv_leakage_roi_right_bottom_no_gap.stp'
    artifacts = args.output.with_suffix('.artifacts')
    if args.output.exists() or artifacts.exists():
        parser.error('Use a new output path; existing evidence is never overwritten')
    artifacts.mkdir(parents=True)
    sources_before = source_hashes(ROOT)
    reference_before = reference_hashes(args.reference_dir)
    reference_authentication = reference_identity_gate(reference_before)
    inputs_before = {str(input_path): file_sha256(input_path)}
    started_at = datetime.now(timezone.utc).isoformat()
    environment = {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
        'packages': {name: version(name) for name in ('numpy', 'numba', 'llvmlite')},
        'overrides': {key: value for key, value in os.environ.items()
            if key.startswith(('LEAKAGE_', 'NUMBA_', 'CUDA_'))},
        'gpu_inventory': subprocess.check_output(['nvidia-smi',
            '--query-gpu=name,driver_version,compute_cap,utilization.gpu,memory.used,memory.total',
            '--format=csv,noheader'], text=True).strip() if args.transport else None}
    start_record = {'started_at': started_at, 'command': [sys.executable, *sys.argv],
        'working_directory': str(Path.cwd()), 'environment': environment,
        'source_hashes': sources_before, 'reference_hashes': reference_before, 'input_hashes': inputs_before,
        'reference_authentication': reference_authentication}
    write_json_artifact(artifacts / 'start.json', start_record)
    progress = {'stage': 'reference_authentication'}
    try:
        if not reference_authentication['passed']:
            raise ValueError('Reference is not the approved pre-5D SAH snapshot')
        report = run_benchmark(args, artifacts, progress)
    except Exception as error:
        report = {'contract': 'perf5d_frozen_bvh_arrays_and_transport_v3', 'passed': False,
            'transport_verified': False, 'error': f'{type(error).__name__}: {error}',
            'traceback': traceback.format_exc(), 'partial_evidence': progress}
    sources_after = source_hashes(ROOT)
    reference_after = {path.relative_to(args.reference_dir).as_posix(): file_sha256(path)
        for path in sorted(args.reference_dir.rglob('*.py'))}
    inputs_after = {str(input_path): file_sha256(input_path)} if input_path.is_file() else {}
    identity = {'source': identity_gate(sources_before, sources_after),
        'reference': identity_gate(reference_before, reference_after),
        'input': identity_gate(inputs_before, inputs_after)}
    unchanged = all(gate['passed'] for gate in identity.values())
    report.update(start_record)
    report.update({'finished_at': datetime.now(timezone.utc).isoformat(),
        'source_hashes_after': sources_after, 'reference_hashes_after': reference_after,
        'input_hashes_after': inputs_after, 'identity_gates': identity,
        'source_unchanged': unchanged, 'passed': report['passed'] and unchanged})
    report['transport_verified'] = report['transport_verified'] and unchanged
    write_json_artifact(args.output, report)
    print(json.dumps({'stage': 'finished', 'passed': report['passed'], 'source_unchanged': unchanged,
        'strict_all_fields_passed': report.get('strict_all_fields_passed'), 'error': report.get('error')}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
