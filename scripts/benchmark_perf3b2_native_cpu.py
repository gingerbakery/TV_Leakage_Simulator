from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b_batch_backend import build_probe_rays
from benchmark_perf3b1_wavefront import build_case, semantic_payload
from leakage_simulator.geometry import RayBatch
from leakage_simulator.importers import import_geometry
from leakage_simulator.raytracer import run_direct_ray_trace


DEFAULT_CAD_PATH = ROOT / "samples" / "tv_leakage_roi_right_bottom_no_gap.stp"
OUTPUT_DIR = ROOT / "outputs" / "perf3b2_native_cpu"
SEED = 20260717


def _measure(repeats: int, operation) -> tuple[list[float], object]:
    durations = []
    result = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = operation()
        durations.append(time.perf_counter() - started)
    return durations, result


def _rate(durations: list[float], ray_count: int) -> dict:
    median_sec = statistics.median(durations)
    return {
        "durations_sec": durations,
        "median_sec": median_sec,
        "rays_per_sec": ray_count / median_sec,
    }


def _package_size_mib(module) -> float:
    package_root = Path(module.__file__).resolve().parent
    return sum(
        path.stat().st_size
        for path in package_root.rglob("*")
        if path.is_file()
    ) / (1024.0 * 1024.0)


def geometry_benchmark(
    path: Path,
    ray_count: int,
    repeats: int,
    batch_sizes: list[int],
) -> dict:
    import_started = time.perf_counter()
    imported = import_geometry(str(path))
    import_sec = time.perf_counter() - import_started
    mesh = imported.mesh
    origins, directions = build_probe_rays(mesh, ray_count, SEED)
    rays = RayBatch(origins, directions)

    prepare_started = time.perf_counter()
    acceleration = mesh.prepare_acceleration()
    bvh_prepare_sec = time.perf_counter() - prepare_started

    cold_started = time.perf_counter()
    cold_hits, cold_execution = mesh.intersect_rays_native_cpu(
        rays,
        backend="bvh",
    )
    native_cold_wall_sec = time.perf_counter() - cold_started

    scalar_rays = [
        (
            tuple(float(value) for value in origin),
            tuple(float(value) for value in direction),
        )
        for origin, direction in zip(origins, directions)
    ]

    def python_scalar_operation():
        distances = np.full(ray_count, float("inf"), dtype=np.float64)
        face_indices = np.full(ray_count, -1, dtype=np.int64)
        for index, (origin, direction) in enumerate(scalar_rays):
            hit = mesh.intersect_ray(origin, direction, backend="bvh")
            if hit is not None:
                distances[index] = hit.t
                face_indices[index] = hit.face_index
        return distances, face_indices

    scalar_durations, scalar_result = _measure(repeats, python_scalar_operation)
    scalar_rate = _rate(scalar_durations, ray_count)
    scalar_distances, scalar_faces = scalar_result

    def native_scalar_operation():
        hit_count = 0
        for origin, direction in scalar_rays:
            hit, _ = mesh.intersect_ray_native_cpu(
                origin,
                direction,
                backend="bvh",
            )
            hit_count += int(hit is not None)
        return hit_count

    native_scalar_durations, native_scalar_hit_count = _measure(
        repeats,
        native_scalar_operation,
    )
    native_scalar_rate = _rate(native_scalar_durations, ray_count)
    native_scalar_rate.update(
        {
            "hit_count": native_scalar_hit_count,
            "speedup_vs_python_scalar": (
                native_scalar_rate["rays_per_sec"] / scalar_rate["rays_per_sec"]
            ),
        }
    )

    native_batches = []
    for requested_size in batch_sizes:
        batch_size = max(1, min(ray_count, requested_size))
        chunks = [
            RayBatch(
                origins[start : start + batch_size],
                directions[start : start + batch_size],
            )
            for start in range(0, ray_count, batch_size)
        ]

        def native_batch_operation(chunks=chunks):
            return sum(
                int(
                    np.count_nonzero(
                        mesh.intersect_rays_native_cpu(
                            chunk,
                            backend="bvh",
                        )[0].hit_mask
                    )
                )
                for chunk in chunks
            )

        durations, hit_count = _measure(repeats, native_batch_operation)
        summary = _rate(durations, ray_count)
        summary.update(
            {
                "batch_size": batch_size,
                "batch_count": len(chunks),
                "hit_count": hit_count,
                "speedup_vs_python_scalar": (
                    summary["rays_per_sec"] / scalar_rate["rays_per_sec"]
                ),
            }
        )
        native_batches.append(summary)

    face_mismatch_count = int(
        np.count_nonzero(scalar_faces != cold_hits.face_indices)
    )
    distance_mismatch_count = int(
        np.count_nonzero(scalar_distances != cold_hits.t)
    )

    import numba
    import llvmlite

    return {
        "path": str(path),
        "triangle_count": len(mesh.faces),
        "ray_count": ray_count,
        "repeats": repeats,
        "import_sec": import_sec,
        "bvh_prepare_sec": bvh_prepare_sec,
        "bvh_node_count": acceleration["bvh_node_count"],
        "native_scene_build_sec": cold_execution.scene_build_sec,
        "native_jit_compile_sec": cold_execution.jit_compile_sec,
        "native_cold_execute_sec": cold_execution.execute_sec,
        "native_cold_wall_sec": native_cold_wall_sec,
        "python_scalar": scalar_rate,
        "native_scalar": native_scalar_rate,
        "native_batches": native_batches,
        "face_mismatch_count": face_mismatch_count,
        "distance_bit_mismatch_count": distance_mismatch_count,
        "numba_version": numba.__version__,
        "llvmlite_version": llvmlite.__version__,
        "numba_package_mib": _package_size_mib(numba),
        "llvmlite_package_mib": _package_size_mib(llvmlite),
    }


def e2e_benchmark(ray_count: int, repeats: int, batch_size: int) -> dict:
    cases = [
        ("python_scalar", "scalar", "python_cpu"),
        ("numba_scalar", "scalar", "numba_cpu"),
        ("python_batch", "batch", "python_cpu"),
        ("numba_batch", "batch", "numba_cpu"),
    ]
    summaries = []
    reference_payload = None
    semantic_mismatch_count = 0
    for name, dispatch, provider in cases:
        durations = []
        representative = None
        payload = None
        for _ in range(repeats):
            trace_input = build_case(ray_count)
            trace_input.config.intersection_backend = "bvh"
            started = time.perf_counter()
            result = run_direct_ray_trace(
                trace_input,
                intersection_dispatch=dispatch,
                intersection_batch_size=batch_size,
                intersection_provider=provider,
            )
            durations.append(time.perf_counter() - started)
            current_payload = semantic_payload(result)
            if payload is None:
                payload = current_payload
                representative = result
            elif payload != current_payload:
                raise RuntimeError(f"{name} was not deterministic")
        assert representative is not None and payload is not None
        if reference_payload is None:
            reference_payload = payload
        elif payload != reference_payload:
            semantic_mismatch_count += 1
        summary = _rate(durations, ray_count)
        performance = representative.metrics["_performance_summary"]
        summary.update(
            {
                "name": name,
                "dispatch": dispatch,
                "requested_provider": provider,
                "effective_provider": performance["intersection_provider"],
                "native_used": performance["native_used"],
                "intersection_ray_count": performance["intersection_ray_count"],
                "receiver_hit_count": representative.receiver_hit_count,
                "surface_hit_count": representative.surface_hit_count,
                "receiver_flux_lumen": representative.metrics["observer"][
                    "total_flux_lumen"
                ],
            }
        )
        summaries.append(summary)
    baseline_rate = summaries[0]["rays_per_sec"]
    for summary in summaries:
        summary["speedup_vs_python_scalar"] = (
            summary["rays_per_sec"] / baseline_rate
        )
    return {
        "ray_count": ray_count,
        "repeats": repeats,
        "batch_size": batch_size,
        "cases": summaries,
        "semantic_mismatch_count": semantic_mismatch_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the optional PERF-3B-2 Numba CPU provider."
    )
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CAD_PATH)
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--e2e-rays", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[256, 4096, 65_536])
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if (
        args.rays <= 0
        or args.e2e_rays <= 0
        or args.repeats <= 0
        or not args.batch_sizes
        or any(size <= 0 for size in args.batch_sizes)
    ):
        raise SystemExit("ray counts, repeats and batch sizes must be positive")
    if not args.path.exists():
        raise SystemExit(f"CAD benchmark path was not found: {args.path}")

    summary = {
        "contract": "perf3b2_native_cpu_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "geometry": geometry_benchmark(
            args.path,
            args.rays,
            args.repeats,
            args.batch_sizes,
        ),
        "end_to_end": e2e_benchmark(
            args.e2e_rays,
            args.repeats,
            4096,
        ),
    }
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
