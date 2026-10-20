from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_cpu import WORKLOADS, cad_case, source_hashes
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from perf5_adaptive_gpu_packets import adaptive_batch_request, adaptive_packets
from perf5_ordered_parity_maps import receiver_parity_maps, emission_parity_maps
from perf5_emission_aliases import emission_aliases
from perf5_initial_event_window import initial_event_window
from perf5_workspace_packing import workspace_factory
from test_perf4e_bounce_mis import _build_reflected_case
from leakage_simulator import gpu_cuda_emitted_flux as emissions
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_receiver_flux as receiver_flux
from leakage_simulator import gpu_cuda_receiver_grid as receiver_grid
from leakage_simulator import gpu_cuda_receiver_moments as receiver_moments
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import gpu_cuda_summary_accumulator as summary
from leakage_simulator.raytracer import run_direct_ray_trace


class TimedKernel:
    def __init__(self, kernel, name, samples, cuda):
        self.kernel = kernel
        self.name = name
        self.samples = samples
        self.cuda = cuda

    def __getattr__(self, name):
        return getattr(self.kernel, name)

    def __getitem__(self, configuration):
        configured = self.kernel[configuration]

        def invoke(*values, **keywords):
            started = self.cuda.event(timing=True)
            finished = self.cuda.event(timing=True)
            started.record()
            result = configured(*values, **keywords)
            finished.record()
            finished.synchronize()
            self.samples.append({'stage': self.name,
                'device_ms': float(self.cuda.event_elapsed_time(started, finished))})
            return result

        return invoke


def wrap(value, name, samples, cuda):
    if value is None:
        return None
    if isinstance(value, tuple):
        return tuple(wrap(item, f'{name}.{index}', samples, cuda) for index, item in enumerate(value))
    if not hasattr(value, 'signatures'):
        raise TypeError(f'Unexpected kernel cache: {name}')
    return TimedKernel(value, name, samples, cuda)


def build_profile_scene(name, rays, cad_rays, mis_rays):
    if name == 'cad':
        return cad_case(cad_rays)[0]
    if name == 'mis':
        return _build_reflected_case(mis_rays, 7172, 'receiver_mis', backend='gpu_cuda')
    return WORKLOADS[name](rays)


def execute_profile_scene(scene, variant, factory, *, activate_parity=True):
    with ExitStack() as stack:
        options = {}
        minimum_rays = 100000 if variant == 'packed_adaptive_window16_alias' else 1000000
        if variant == 'packed_adaptive_window16_alias':
            stack.enter_context(initial_event_window(16))
        if variant != 'current':
            stack.enter_context(patch.object(resident, '_ensure_workspace', new=factory))
            stack.enter_context(adaptive_packets(scene, minimum_rays=minimum_rays))
            requested = adaptive_batch_request(scene, minimum_rays=minimum_rays)
            if requested:
                options['intersection_batch_size'] = requested
        if activate_parity and variant in ('packed_adaptive_parity', 'packed_adaptive_parity_both',
                'packed_adaptive_parity_alias', 'packed_adaptive_window16_alias'):
            stack.enter_context(receiver_parity_maps(gpu._CUDA))
        if activate_parity and variant == 'packed_adaptive_parity_both':
            stack.enter_context(emission_parity_maps(gpu._CUDA))
        if activate_parity and variant in ('packed_adaptive_parity_alias', 'packed_adaptive_window16_alias'):
            stack.enter_context(emission_aliases(gpu._CUDA))
        return run_direct_ray_trace(scene, **options)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=1000000)
    parser.add_argument('--cad-rays', type=int, default=1000000)
    parser.add_argument('--mis-rays', type=int, default=65536)
    parser.add_argument('--variant', choices=('current', 'packed_adaptive',
        'packed_adaptive_parity', 'packed_adaptive_parity_both', 'packed_adaptive_parity_alias',
        'packed_adaptive_window16_alias'), default='current')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--scenes', nargs='+', choices=('cad', 'mis', 'trapped_corridor_depth10'),
        default=['cad', 'mis', 'trapped_corridor_depth10'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if min(args.rays, args.cad_rays, args.mis_rays) < 1 or args.repeats < 2:
        parser.error('Positive Ray counts and at least two measurements are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in
        ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or (
        preflight.preflight_scope != 'production_ray_bvh'
        or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1'
    ):
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    own_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    diagnostic_paths = [ROOT / 'scripts' / name for name in
        ('perf5_adaptive_gpu_packets.py', 'perf5_workspace_packing.py',
            'perf5_ordered_parity_maps.py', 'perf5_emission_aliases.py',
            'perf5_initial_event_window.py', 'benchmark_perf5_gpu_batching.py')]
    diagnostic_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in diagnostic_paths}
    factory = workspace_factory('packed_reuse') if args.variant != 'current' else None
    owners = ((resident, ('_KERNEL', '_PAIR_KERNEL')),
        (summary, ('_KERNEL', '_PROFILE_FACE_KERNEL', '_PARTITION_KERNELS')),
        (emissions, ('_KERNEL',)),
        (receiver_flux, ('_KERNELS', '_UNIFORM_KERNELS')),
        (receiver_moments, ('_KERNELS',)), (receiver_grid, ('_KERNELS',)))
    cases = []
    for name in args.scenes:
        scene = build_profile_scene(name, args.rays, args.cad_rays, args.mis_rays)
        scene.config.compute_backend = 'gpu_cuda'
        scene.config.store_ray_paths = False
        scene.mesh.prepare_acceleration()
        started = time.perf_counter()
        expected = execute_profile_scene(scene, args.variant, factory)
        first_sec = time.perf_counter() - started
        expected_payload = semantic_payload(expected)
        records = []
        instrumentation_warmup = None
        for repeat in range(args.repeats + 1):
            samples = []
            with ExitStack() as stack:
                if args.variant in ('packed_adaptive_parity', 'packed_adaptive_parity_both',
                        'packed_adaptive_parity_alias', 'packed_adaptive_window16_alias'):
                    stack.enter_context(receiver_parity_maps(gpu._CUDA))
                if args.variant == 'packed_adaptive_parity_both':
                    stack.enter_context(emission_parity_maps(gpu._CUDA))
                if args.variant in ('packed_adaptive_parity_alias', 'packed_adaptive_window16_alias'):
                    stack.enter_context(emission_aliases(gpu._CUDA))
                for owner, fields in owners:
                    for field in fields:
                        original = getattr(owner, field)
                        if original is not None:
                            tag = owner.__name__.rsplit('.', 1)[-1] + '.' + field
                            stack.enter_context(patch.object(owner, field,
                                wrap(original, tag, samples, gpu._CUDA)))
                started = time.perf_counter()
                result = execute_profile_scene(scene, args.variant, factory, activate_parity=False)
                wall_sec = time.perf_counter() - started
            performance = result.metrics['_performance_summary']
            if performance['compute_execution_state'] != 'gpu_active' or (
                performance['gpu_cuda_gpu_success_count'] <= 0
                or performance['gpu_resident_wavefront_fallback_count']
                or performance['gpu_cuda_hybrid_cpu_attempt_count']
            ):
                raise RuntimeError('Full CUDA execution not proven')
            parity = compare_semantic_payloads(expected_payload, semantic_payload(result),
                absolute_tolerance=1e-9, relative_tolerance=1e-9,
                max_ulp_distance=1 << 48).to_dict()
            totals = {}
            for sample in samples:
                totals[sample['stage']] = totals.get(sample['stage'], 0.0) + sample['device_ms']
            record = {'profiled_wall_sec': wall_sec, 'stage_device_ms': totals,
                'launches': samples, 'parity': parity, 'performance': performance}
            if repeat:
                records.append(record)
            else:
                instrumentation_warmup = record
            print(json.dumps({'scene': name, 'repeat': repeat, 'device_ms': totals,
                'passed': parity['passed']}), flush=True)
        stages = sorted({stage for record in records for stage in record['stage_device_ms']})
        cases.append({'scene': name, 'rays': result.total_rays, 'max_depth': scene.config.max_depth,
            'surface_hits': result.surface_hit_count,
            'emitter_types': [emitter.emitter_type for emitter in scene.emitters],
            'first_unprofiled_sec': first_sec, 'instrumentation_warmup': instrumentation_warmup,
            'median_device_ms': {stage: statistics.median(record['stage_device_ms'].get(stage, 0.0)
                for record in records) for stage in stages}, 'records': records})
    unchanged = hashes == source_hashes() and own_hash == hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest for name, digest in diagnostic_hashes.items())
    report = {'contract': 'default_stream_cuda_event_stage_diagnostic_v1',
        'not_a_normal_wall_time_benchmark': True,
        'event_intervals_include_launch_submission_gaps': True,
        'delivery': {'path': 'source_checkout',
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'script_sha256': own_hash, 'source_unchanged': unchanged,
        'variant': args.variant, 'diagnostic_hashes': diagnostic_hashes,
        'cases': cases, 'passed': unchanged and all(record['parity']['passed']
            for case in cases for record in case['records'])}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
