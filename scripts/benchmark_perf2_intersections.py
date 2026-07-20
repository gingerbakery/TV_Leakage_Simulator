from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MATPLOTLIB_CACHE = Path(tempfile.gettempdir()) / "tv_leakage_matplotlib"
MATPLOTLIB_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MATPLOTLIB_CACHE))

import matplotlib.pyplot as plt

from leakage_simulator.geometry import TriangleMesh, vec_norm
from leakage_simulator.importers import import_geometry


OUTPUT_DIR = ROOT / "outputs" / "perf2_intersection_benchmark"
DEFAULT_TV_PATH = ROOT / "samples" / "tv_leakage_full_assembled_no_gap.stp"
DEFAULT_GEAR_PATH = Path(
    r"C:\Users\Administrator\Downloads\MODULE_3_Z27_HELICAL_GEAR_SAG.stp"
)
LEGACY_BVH_RATES = {
    "tv_leakage_full_assembled_no_gap.stp": 21767.333334716925,
    "MODULE_3_Z27_HELICAL_GEAR_SAG.stp": 4971.619509807423,
}


def build_probe_rays(
    mesh: TriangleMesh,
    ray_count: int,
    seed: int,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
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
    rays = []
    for _ in range(ray_count):
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
        direction = vec_norm(
            (
                target[0] - origin[0],
                target[1] - origin[1],
                target[2] - origin[2],
            )
        )
        rays.append((origin, direction))
    return rays


def _measure_backend(
    mesh: TriangleMesh,
    rays,
    backend: str,
    repeats: int,
) -> tuple[float, int]:
    durations = []
    hit_count = 0
    for _ in range(repeats):
        started = time.perf_counter()
        current_hit_count = sum(
            mesh.intersect_ray(origin, direction, backend=backend) is not None
            for origin, direction in rays
        )
        durations.append(time.perf_counter() - started)
        hit_count = current_hit_count
    return statistics.median(durations), hit_count


def benchmark_path(
    path: Path,
    ray_count: int,
    brute_count: int,
    repeats: int,
) -> dict:
    import_started = time.perf_counter()
    import_result = import_geometry(str(path))
    import_sec = time.perf_counter() - import_started
    mesh = import_result.mesh
    rays = build_probe_rays(mesh, ray_count, 20260717)

    build_started = time.perf_counter()
    acceleration = mesh.prepare_acceleration()
    prepare_sec = time.perf_counter() - build_started

    accelerated_sec, accelerated_hits = _measure_backend(
        mesh,
        rays,
        "bvh",
        repeats,
    )

    reference_rays = rays[: min(brute_count, len(rays))]
    brute_sec, brute_hits = _measure_backend(
        mesh,
        reference_rays,
        "brute_force",
        repeats,
    )

    mismatch_count = 0
    for origin, direction in reference_rays:
        brute_hit = mesh.intersect_ray(
            origin,
            direction,
            backend="brute_force",
        )
        accelerated_hit = mesh.intersect_ray(
            origin,
            direction,
            backend="bvh",
        )
        if (brute_hit is None) != (accelerated_hit is None):
            mismatch_count += 1
            continue
        if brute_hit is None or accelerated_hit is None:
            continue
        if (
            brute_hit.face_index != accelerated_hit.face_index
            or abs(brute_hit.t - accelerated_hit.t) > 1e-8
        ):
            mismatch_count += 1

    accelerated_rate = ray_count / accelerated_sec
    brute_rate = len(reference_rays) / brute_sec
    legacy_rate = LEGACY_BVH_RATES.get(path.name)
    return {
        "name": path.stem,
        "path": str(path),
        "synthetic": import_result.synthetic,
        "import_note": import_result.note,
        "vertex_count": len(mesh.vertices),
        "triangle_count": len(mesh.faces),
        "import_sec": import_sec,
        "bvh_prepare_sec": prepare_sec,
        "bvh_node_count": acceleration["bvh_node_count"],
        "bvh_leaf_count": acceleration["bvh_leaf_count"],
        "benchmark_repeats": repeats,
        "accelerated_ray_count": ray_count,
        "accelerated_hit_count": accelerated_hits,
        "accelerated_runtime_sec": accelerated_sec,
        "accelerated_rays_per_sec": accelerated_rate,
        "brute_ray_count": len(reference_rays),
        "brute_hit_count": brute_hits,
        "brute_runtime_sec": brute_sec,
        "brute_rays_per_sec": brute_rate,
        "speedup_vs_brute": accelerated_rate / brute_rate,
        "legacy_bvh_rays_per_sec": legacy_rate,
        "speedup_vs_legacy_bvh": (
            accelerated_rate / legacy_rate if legacy_rate else None
        ),
        "reference_mismatch_count": mismatch_count,
    }


def write_chart(cases: list[dict]) -> Path:
    labels = [
        "TV sample\n{:,.0f} triangles".format(item["triangle_count"])
        if item["name"].startswith("tv_leakage")
        else "Helical gear\n{:,.0f} triangles".format(item["triangle_count"])
        for item in cases
    ]
    positions = list(range(len(cases)))
    width = 0.24
    figure, axis = plt.subplots(
        figsize=(11, 6),
        constrained_layout=True,
        facecolor="white",
    )
    axis.set_facecolor("white")
    brute_values = [item["brute_rays_per_sec"] for item in cases]
    legacy_values = [
        item["legacy_bvh_rays_per_sec"] or 0.0
        for item in cases
    ]
    accelerated_values = [item["accelerated_rays_per_sec"] for item in cases]
    axis.bar(
        [value - width for value in positions],
        brute_values,
        width,
        label="Brute-force reference",
        color="#94a3b8",
    )
    axis.bar(
        positions,
        legacy_values,
        width,
        label="Legacy recursive BVH",
        color="#f59e0b",
    )
    bars = axis.bar(
        [value + width for value in positions],
        accelerated_values,
        width,
        label="PERF-2 flat BVH",
        color="#0ea5e9",
    )
    axis.set_yscale("log")
    axis.set_ylabel("Rays / second (log scale)")
    axis.set_title("PERF-2 CAD Intersection Throughput", fontweight="bold")
    axis.set_xticks(positions, labels)
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    for bar, rate in zip(bars, accelerated_values):
        axis.text(
            bar.get_x() + bar.get_width() * 0.5,
            rate,
            f"{rate:,.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    chart_path = OUTPUT_DIR / "perf2_intersection_throughput.png"
    figure.savefig(chart_path, dpi=160, facecolor="white")
    plt.close(figure)
    return chart_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark PERF-2 CAD intersection backends."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="STEP/OBJ/STL paths. Defaults to bundled TV and local gear samples.",
    )
    parser.add_argument("--rays", type=int, default=5000)
    parser.add_argument("--brute-rays", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    candidate_paths = (
        [Path(value) for value in args.paths]
        if args.paths
        else [DEFAULT_TV_PATH, DEFAULT_GEAR_PATH]
    )
    paths = [path for path in candidate_paths if path.exists()]
    if not paths:
        raise SystemExit("No benchmark CAD files were found.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = [
        benchmark_path(path, args.rays, args.brute_rays, max(1, args.repeats))
        for path in paths
    ]
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cases": cases,
    }
    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    chart_path = write_chart(cases)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"summary={summary_path}")
    print(f"chart={chart_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
