from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf5_cpu import cad_case, source_hashes
from benchmark_perf5_gpu_coherent import coherent_factory
from benchmark_perf5_leaf_loop import variant
from benchmark_perf5_transport import semantic_payload
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


class TimedKernel:
    def __init__(self, kernel, stage, samples, enabled):
        self.kernel = kernel
        self.stage = stage
        self.samples = samples
        self.enabled = enabled

    def __getitem__(self, dimensions):
        configured = self.kernel[dimensions]

        def launch(*arguments):
            if not self.enabled[0]:
                return configured(*arguments)
            begin = gpu._CUDA.event(timing=True)
            end = gpu._CUDA.event(timing=True)
            begin.record()
            started = time.perf_counter()
            result = configured(*arguments)
            elapsed = time.perf_counter() - started
            end.record()
            self.samples.append((self.stage, begin, end, elapsed))
            return result

        return launch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--buckets', type=int, nargs='+', choices=(1, 8, 64), default=[1, 64])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.rays < 1 or args.repeats < 2:
        parser.error('Positive rays and at least two measured repeats are required')
    preflight = gpu.preflight_gpu_cuda(refresh=True)
    if not all(getattr(preflight, name) is True for name in ('available', 'strict_float64', 'kernel_executed', 'kernel_verified')) or preflight.preflight_scope != 'production_ray_bvh' or gpu.PROVIDER_CONTRACT != 'strict_float64_bvh_v1':
        raise RuntimeError('Production CUDA preflight failed')
    hashes = source_hashes()
    dependency_names = ('profile_perf5_gpu_coherent.py', 'benchmark_perf5_gpu_coherent.py',
        'benchmark_perf5_gpu_frontier.py', 'benchmark_perf5_gpu_guarded_bvh.py', 'benchmark_perf5_gpu_persistent.py',
        'benchmark_perf5_leaf_loop.py', 'benchmark_perf5_transport.py')
    dependencies = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() for name in dependency_names}
    trace_input = cad_case(args.rays)[0]
    trace_input.config.compute_backend = 'gpu_cuda'
    trace_input.config.store_ray_paths = False
    factories = {'original': resident._make_kernel, **{str(count): coherent_factory(count)[0] for count in args.buckets}}
    states = {mode: {} for mode in factories}
    records = {mode: [] for mode in factories}
    expected = None
    for repeat in range(args.repeats + 1):
        for mode in factories if repeat % 2 == 0 else tuple(reversed(factories)):
            samples = []
            enabled = [False]

            def create():
                kernel = factories[mode]()
                if mode == 'original':
                    return TimedKernel(kernel, 'original', samples, enabled)
                kernel.kernels = tuple(TimedKernel(value, stage, samples, enabled) for stage, value in zip(
                    ('reset', 'initialize_histogram', 'prefix', 'scatter', 'canonical_query', 'shade_histogram'), kernel.kernels))
                return kernel

            with variant(create, states[mode]):
                compiled = resident._KERNEL
                if compiled is not None:
                    wrappers = (compiled,) if mode == 'original' else compiled.kernels
                    for wrapper in wrappers:
                        wrapper.samples = samples
                        wrapper.enabled = enabled
                enabled[0] = repeat > 0
                started = time.perf_counter()
                result = raytracer.run_direct_ray_trace(trace_input)
                elapsed = time.perf_counter() - started
            payload = semantic_payload(result)
            if expected is None:
                expected = payload
            parity = compare_semantic_payloads(expected, payload, absolute_tolerance=1e-9,
                relative_tolerance=1e-9, max_ulp_distance=1 << 48).to_dict()
            performance = result.metrics['_performance_summary']
            if not parity['passed'] or performance['compute_execution_state'] != 'gpu_active' or performance['gpu_cuda_gpu_success_count'] <= 0 or performance['gpu_resident_wavefront_fallback_count'] or performance['gpu_cuda_hybrid_cpu_attempt_count']:
                raise RuntimeError(f'Coherent profiling correctness or execution proof failed: {mode}')
            if repeat:
                stages = {}
                for stage, begin, end, host_elapsed in samples:
                    record = stages.setdefault(stage, {'calls': 0, 'device_event_interval_sec': 0.0, 'host_launch_call_sec': 0.0})
                    record['calls'] += 1
                    record['device_event_interval_sec'] += gpu._CUDA.event_elapsed_time(begin, end) / 1000.0
                    record['host_launch_call_sec'] += host_elapsed
                records[mode].append({'wall_sec': elapsed, 'stages': stages, 'parity': parity, 'performance': performance})
            print(json.dumps({'mode': mode, 'repeat': repeat, 'instrumented': repeat > 0, 'wall_sec': elapsed}), flush=True)
    summaries = {}
    for mode, measurements in records.items():
        stages = {}
        for stage in measurements[0]['stages']:
            stages[stage] = {name: statistics.median(record['stages'][stage][name] for record in measurements)
                for name in ('calls', 'device_event_interval_sec', 'host_launch_call_sec')}
        summaries[mode] = {'instrumented_wall_median_sec': statistics.median(record['wall_sec'] for record in measurements), 'stages': stages}
    unchanged = hashes == source_hashes() and all(hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest() == value for name, value in dependencies.items())
    report = {'contract': 'instrumented_direction_coherent_stage_intervals_v1',
        'preflight': {**asdict(preflight), 'provider_contract': gpu.PROVIDER_CONTRACT},
        'source_hashes': hashes, 'dependency_hashes': dependencies, 'source_unchanged': unchanged,
        'timing_is_not_an_uninstrumented_benchmark': True,
        'device_event_intervals_include_host_dispatch_gaps': True,
        'host_launch_calls_include_driver_blocking_and_overlap_device_work': True,
        'rays': result.total_rays, 'emitter_types': [emitter.emitter_type for emitter in trace_input.emitters],
        'records': records, 'summaries': summaries, 'passed': unchanged}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(summaries), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
