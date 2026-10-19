from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf5_cpu import semantic_hash, source_hashes
from leakage_simulator.raytracer import run_direct_ray_trace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rays', type=int, default=100000)
    parser.add_argument('--depths', type=int, nargs='+', default=[16, 20, 32])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    hashes = source_hashes()
    cases = []
    for depth in args.depths:
        trace_input = build_depth_ten_case(args.rays)
        trace_input.mesh.vertices = [(point[0] * 20.0, point[1], point[2]) for point in trace_input.mesh.vertices]
        trace_input.receivers[0].center = (400.0, 0.0, 0.0)
        trace_input.config.max_depth = depth
        trace_input.mesh.prepare_acceleration()
        records = {mode: {'warm_sec': []} for mode in ('dense', 'compact')}
        expected = None
        for repeat in range(args.repeats + 1):
            modes = tuple(records) if repeat % 2 == 0 else tuple(reversed(records))
            for mode in modes:
                with patch.dict(os.environ, {'LEAKAGE_CPU_TRANSPORT': 'fused', 'LEAKAGE_CPU_EVENT_LAYOUT': mode, 'LEAKAGE_CPU_WORKERS': '8'}):
                    started = time.perf_counter()
                    result = run_direct_ray_trace(trace_input)
                    elapsed = time.perf_counter() - started
                digest = semantic_hash(result)
                if expected is None:
                    expected = digest
                if digest != expected or result.surface_hit_count != args.rays * (depth + 1):
                    raise RuntimeError('Filled-depth CPU layout comparison did not preserve physical work')
                performance = result.metrics['_performance_summary']
                if performance.get('wavefront_cpu_transport_fallback_count', 0) or performance['wavefront_max_observed_depth'] != depth:
                    raise RuntimeError('Filled-depth CPU transport did not execute the required path')
                if repeat:
                    records[mode]['warm_sec'].append(elapsed)
                else:
                    records[mode]['first_sec'] = elapsed
                records[mode]['performance'] = performance
        for record in records.values():
            record['median_sec'] = statistics.median(record['warm_sec'])
        case = {'depth': depth, 'rays': args.rays, 'semantic_hash': expected, 'records': records,
            'dense_over_compact_speedup': records['dense']['median_sec'] / records['compact']['median_sec']}
        cases.append(case)
        print(json.dumps({key: value for key, value in case.items() if key != 'records'}), flush=True)
    report = {'contract': 'filled_depth_cpu_record_layout_paired_v1', 'source_hashes': hashes,
        'source_unchanged': hashes == source_hashes(), 'cases': cases, 'passed': hashes == source_hashes()}
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
