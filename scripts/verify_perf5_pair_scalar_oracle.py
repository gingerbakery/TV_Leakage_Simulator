from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts'), str(ROOT / 'tests')]

from benchmark_perf5_gpu_guarded_bvh import dual_scene
from benchmark_perf5_gpu_pair_cluster import pair_factory
from perf5_isolated_hit_cluster import build_original_topology
from test_perf5_gpu_isolated_cluster import cluster_scalar_functions
from leakage_simulator.geometry import TriangleMesh


def geometry(seed, gap, panel_count):
    generator = np.random.default_rng(seed)
    triangles = []
    targets = []
    for index in range(panel_count):
        center = generator.uniform(-10.0, 10.0, 3)
        axis = index % 3
        normal = np.eye(3)[axis]
        first_axis = np.eye(3)[(axis + 1) % 3]
        second_axis = np.eye(3)[(axis + 2) % 3]
        size = generator.uniform(0.5, 2.0)
        targets.append((center, normal))
        for offset in (0.0, gap):
            shifted = center + normal * offset
            triangles.append((shifted - first_axis * size - second_axis * size,
                shifted + first_axis * size - second_axis * size, shifted + second_axis * size))
    for _ in range(panel_count):
        center = generator.uniform(-15.0, 15.0, 3)
        edge_first = generator.normal(size=3)
        edge_second = generator.normal(size=3)
        triangles.append((center, center + edge_first, center + edge_second))
    mesh = TriangleMesh()
    for index in generator.permutation(len(triangles)):
        vertices = [mesh.add_vertex(tuple(vertex)) for vertex in triangles[index]]
        mesh.add_face(*vertices, 'opaque')
    return mesh, targets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--queries', type=int, default=4000)
    parser.add_argument('--panels', type=int, default=128)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.queries < 1 or args.panels < 1:
        parser.error('Positive query and panel counts required')
    paths = [Path(__file__), ROOT / 'src/leakage_simulator/gpu_cuda_resident_wavefront.py',
        ROOT / 'scripts/benchmark_perf5_gpu_guarded_bvh.py', ROOT / 'scripts/benchmark_perf5_gpu_isolated_cluster.py',
        ROOT / 'scripts/benchmark_perf5_gpu_pair_cluster.py', ROOT / 'scripts/perf5_isolated_hit_cluster.py',
        ROOT / 'tests/test_perf5_gpu_isolated_cluster.py']
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    started = time.perf_counter()
    cases = []
    for slab_full in (False, True):
        canonical, candidate = cluster_scalar_functions(4,
            factory_builder=lambda capacity: pair_factory(slab_full=slab_full),
            generated_filename='<perf5_scalar_pair_cluster_bvh>')
        for scenario, gap in enumerate((0.0, 5e-11, 1.5e-10, 5e-10, 1e-6)):
            mesh, targets = geometry(191100 + scenario, gap, args.panels)
            original = mesh.prepare_gpu_cuda_scene()
            topology = build_original_topology(original)
            merged, _ = dual_scene(mesh, padded=True)
            arrays = tuple(getattr(merged, name) for name in ('triangle_v0', 'triangle_edge1',
                'triangle_edge2', 'node_bounds_min', 'node_bounds_max', 'node_left', 'node_right',
                'node_start', 'node_count', 'ordered_faces', 'traceable_face_mask'))
            generator = np.random.default_rng(192100 + scenario)
            counters = np.zeros(2, dtype=np.int64)
            differences = []
            hits = 0
            for sample in range(args.queries):
                origin = generator.uniform(-18, 18, 3)
                direction = generator.normal(size=3)
                direction /= np.linalg.norm(direction)
                distance_to_target = generator.uniform(0.1, 5.0)
                if sample % 3 != 0:
                    center, normal = targets[sample % len(targets)]
                    side = 1.0 if sample % 2 == 0 else -1.0
                    origin = center + side * normal * distance_to_target
                    direction = -side * normal
                if sample % 11 == 0:
                    direction = np.asarray((1.0, 1e-14, 0.0))
                maximum = distance_to_target if sample % 7 == 0 else math.inf
                ignored = int(generator.integers(0, len(mesh.faces))) if sample % 5 == 0 else -1
                values = (0, merged.stack_width, *origin, *direction, 1e-8, maximum, ignored, *arrays)
                expected = canonical(*values, np.empty(merged.stack_width),
                    np.empty(merged.stack_width, dtype=np.int64))
                actual = candidate(*values, np.empty(merged.stack_width),
                    np.empty(merged.stack_width, dtype=np.int64), topology.face_leaf, topology.face_order,
                    topology.node_path, topology.node_code, counters)
                hits += int(expected[1] >= 0)
                if actual[1:] != expected[1:] or np.float64(actual[0]).view(np.uint64) != np.float64(expected[0]).view(np.uint64):
                    differences.append({'sample': sample, 'expected_face': int(expected[1]), 'actual_face': int(actual[1])})
            coverage = counters[0] > args.queries * 0.9 if gap == 5e-10 else counters[1] > 100
            record = {'slab_full': slab_full, 'gap': gap, 'triangles': len(mesh.faces),
                'original_nodes': len(original.node_count), 'path_width': topology.node_path.shape[1],
                'queries': args.queries, 'hits': hits, 'canonical_replays': int(counters[0]),
                'certified': int(counters[1]), 'interference_band_fixture': gap == 5e-10,
                'coverage_passed': bool(coverage), 'differences': differences, 'passed': bool(not differences and coverage)}
            cases.append(record)
            print(json.dumps(record), flush=True)
    unchanged = hashes == {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = {'contract': 'multilevel_random_pair_bvh_scalar_oracle_v1', 'gpu_executed': False,
        'source_hashes': hashes, 'source_unchanged': unchanged, 'seconds': time.perf_counter() - started,
        'cases': cases, 'passed': unchanged and all(case['passed'] for case in cases)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
