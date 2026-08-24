from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import RayBatch, TriangleMesh, vec_norm
from leakage_simulator.importers import import_geometry


DEFAULT_CAD_PATH = ROOT / "samples" / "tv_leakage_roi_right_bottom_no_gap.stp"
OUTPUT_DIR = ROOT / "outputs" / "perf3b_batch_backend"
SEED = 20260717


def build_probe_rays(
    mesh: TriangleMesh,
    ray_count: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    x_values = [vertex[0] for vertex in mesh.vertices]
    y_values = [vertex[1] for vertex in mesh.vertices]
    z_values = [vertex[2] for vertex in mesh.vertices]
    center = (
        (min(x_values) + max(x_values)) * 0.5,
        (min(y_values) + max(y_values)) * 0.5,
        (min(z_values) + max(z_values)) * 0.5,
    )
    span = max(
        max(x_values) - min(x_values),
        max(y_values) - min(y_values),
        max(z_values) - min(z_values),
        1.0,
    )
    rng = random.Random(seed)
    origins = np.empty((ray_count, 3), dtype=np.float64)
    directions = np.empty((ray_count, 3), dtype=np.float64)
    for index in range(ray_count):
        origin = (
            center[0] + rng.uniform(-span, span),
            center[1] + rng.uniform(-span, span),
            center[2] - span * 2.0,
        )
        target = (
            center[0] + rng.uniform(-span * 0.4, span * 0.4),
            center[1] + rng.uniform(-span * 0.4, span * 0.4),
            center[2] + rng.uniform(-span * 0.3, span * 0.3),
        )
        origins[index] = origin
        directions[index] = vec_norm(
            (
                target[0] - origin[0],
                target[1] - origin[1],
                target[2] - origin[2],
            )
        )
    return origins, directions


def _measure(repeats: int, operation) -> tuple[list[float], int]:
    durations = []
    hit_count = 0
    for _ in range(repeats):
        started = time.perf_counter()
        hit_count = operation()
        durations.append(time.perf_counter() - started)
    return durations, hit_count


def _rate_summary(
    durations: list[float],
    ray_count: int,
    hit_count: int,
) -> dict:
    median_sec = statistics.median(durations)
    return {
        "durations_sec": durations,
        "median_sec": median_sec,
        "rays_per_sec": ray_count / median_sec,
        "hit_count": hit_count,
    }


def _chunk_batches(
    origins: np.ndarray,
    directions: np.ndarray,
    batch_size: int,
) -> list[RayBatch]:
    return [
        RayBatch(origins[start : start + batch_size], directions[start : start + batch_size])
        for start in range(0, len(origins), batch_size)
    ]


def benchmark(
    path: Path,
    ray_count: int,
    batch_sizes: list[int],
    repeats: int,
    reference_ray_count: int,
) -> dict:
    import_started = time.perf_counter()
    import_result = import_geometry(str(path))
    import_sec = time.perf_counter() - import_started
    mesh = import_result.mesh
    origins, directions = build_probe_rays(mesh, ray_count, SEED)

    prepare_started = time.perf_counter()
    acceleration = mesh.prepare_acceleration()
    bvh_prepare_sec = time.perf_counter() - prepare_started
    scalar_rays = [
        (
            tuple(float(value) for value in origin),
            tuple(float(value) for value in direction),
        )
        for origin, direction in zip(origins, directions)
    ]
    mesh.intersect_ray(*scalar_rays[0], backend="bvh")

    scalar_durations, scalar_hit_count = _measure(
        repeats,
        lambda: sum(
            mesh.intersect_ray(origin, direction, backend="bvh") is not None
            for origin, direction in scalar_rays
        ),
    )
    scalar_summary = _rate_summary(
        scalar_durations,
        ray_count,
        scalar_hit_count,
    )

    batch_cases = []
    normalized_sizes = sorted({max(1, min(ray_count, size)) for size in batch_sizes})
    for batch_size in normalized_sizes:
        chunks = _chunk_batches(origins, directions, batch_size)
        batch_durations, batch_hit_count = _measure(
            repeats,
            lambda chunks=chunks: sum(
                int(np.count_nonzero(mesh.intersect_rays(chunk, backend="bvh").hit_mask))
                for chunk in chunks
            ),
        )
        summary = _rate_summary(batch_durations, ray_count, batch_hit_count)
        summary.update(
            {
                "batch_size": batch_size,
                "batch_count": len(chunks),
                "native_batch": False,
                "speedup_vs_scalar": (
                    summary["rays_per_sec"] / scalar_summary["rays_per_sec"]
                ),
            }
        )
        batch_cases.append(summary)

    full_batch_hits = mesh.intersect_rays(
        RayBatch(origins, directions),
        backend="bvh",
    )
    scalar_faces = np.full(ray_count, -1, dtype=np.int64)
    scalar_distances = np.full(ray_count, float("inf"), dtype=np.float64)
    for index, (origin, direction) in enumerate(scalar_rays):
        hit = mesh.intersect_ray(origin, direction, backend="bvh")
        if hit is not None:
            scalar_faces[index] = hit.face_index
            scalar_distances[index] = hit.t
    scalar_batch_face_mismatches = int(
        np.count_nonzero(scalar_faces != full_batch_hits.face_indices)
    )
    scalar_batch_t_mismatches = int(
        np.count_nonzero(
            ~np.isclose(
                scalar_distances,
                full_batch_hits.t,
                rtol=0.0,
                atol=1e-9,
                equal_nan=False,
            )
        )
    )

    brute_count = min(reference_ray_count, ray_count)
    brute_mismatches = 0
    for origin, direction in scalar_rays[:brute_count]:
        brute_hit = mesh.intersect_ray(
            origin,
            direction,
            backend="brute_force",
        )
        bvh_hit = mesh.intersect_ray(
            origin,
            direction,
            backend="bvh",
        )
        if (brute_hit is None) != (bvh_hit is None):
            brute_mismatches += 1
        elif brute_hit is not None and bvh_hit is not None:
            if (
                brute_hit.face_index != bvh_hit.face_index
                or abs(brute_hit.t - bvh_hit.t) > 1e-9
            ):
                brute_mismatches += 1

    return {
        "contract": "perf3b_batch_intersection_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "path": str(path),
        "synthetic": import_result.synthetic,
        "import_note": import_result.note,
        "seed": SEED,
        "triangle_count": len(mesh.faces),
        "ray_count": ray_count,
        "repeats": repeats,
        "import_sec": import_sec,
        "bvh_prepare_sec": bvh_prepare_sec,
        "bvh_node_count": acceleration["bvh_node_count"],
        "bvh_leaf_count": acceleration["bvh_leaf_count"],
        "scalar_bvh": scalar_summary,
        "batch_cpu_reference": batch_cases,
        "scalar_batch_face_mismatch_count": scalar_batch_face_mismatches,
        "scalar_batch_t_mismatch_count": scalar_batch_t_mismatches,
        "brute_reference_ray_count": brute_count,
        "brute_bvh_mismatch_count": brute_mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the PERF-3B batch intersection contract and CPU adapter."
    )
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CAD_PATH)
    parser.add_argument("--rays", type=int, default=50_000)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 256, 4096, 65_536])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--reference-rays", type=int, default=50)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.rays <= 0 or args.repeats <= 0 or args.reference_rays < 0:
        raise SystemExit("rays/repeats must be positive and reference-rays non-negative")
    if not args.path.exists():
        raise SystemExit(f"CAD benchmark path was not found: {args.path}")

    summary = benchmark(
        args.path,
        args.rays,
        args.batch_sizes,
        args.repeats,
        args.reference_rays,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.no_write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = OUTPUT_DIR / "summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
