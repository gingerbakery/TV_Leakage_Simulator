from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

MATPLOTLIB_CACHE = Path(tempfile.gettempdir()) / "tv_leakage_matplotlib"
MATPLOTLIB_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MATPLOTLIB_CACHE))

import matplotlib.pyplot as plt

from generate_rt2c_reflection_report import build_model_input
from leakage_simulator.raytracer import run_direct_ray_trace


OUTPUT_DIR = ROOT / "outputs" / "perf1_benchmark"
INITIAL_GAUSSIAN_100K_SEC = 5.12615


def run_case(model: str, ray_count: int) -> dict:
    trace_input = build_model_input(model)
    trace_input.emitters[0].ray_count = ray_count
    trace_input.config.ray_count = ray_count
    result = run_direct_ray_trace(trace_input)
    performance = result.metrics.get("_performance_summary", {})
    return {
        "model": model,
        "ray_count": ray_count,
        "runtime_sec": result.runtime_sec,
        "rays_per_sec": performance.get(
            "rays_per_sec",
            result.total_rays / result.runtime_sec,
        ),
        "receiver_hit_count": result.receiver_hit_count,
        "receiver_flux_lumen": result.metrics["observer"]["total_flux_lumen"],
        "backend": performance.get("backend", "unknown"),
        "fast_primary_ray_count": performance.get("fast_primary_ray_count", 0),
        "scalar_primary_ray_count": performance.get("scalar_primary_ray_count", 0),
    }


def write_chart(cases: list[dict]) -> Path:
    labels = [
        f"{item['model']}\n{item['ray_count'] // 1000:,}k"
        for item in cases
    ]
    rates = [item["rays_per_sec"] for item in cases]
    figure, axis = plt.subplots(figsize=(10, 5.5), constrained_layout=True)
    bars = axis.bar(labels, rates, color=["#64748b", "#0ea5e9", "#8b5cf6", "#f97316"])
    axis.set_title("PERF-1 CPU Ray Tracing Throughput", fontweight="bold")
    axis.set_ylabel("Rays / second")
    axis.grid(axis="y", alpha=0.25)
    for bar, rate in zip(bars, rates):
        axis.text(
            bar.get_x() + bar.get_width() * 0.5,
            bar.get_height(),
            f"{rate:,.0f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    chart_path = OUTPUT_DIR / "perf1_throughput.png"
    figure.savefig(chart_path, dpi=160)
    plt.close(figure)
    return chart_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeatable PERF-1 benchmarks.")
    parser.add_argument(
        "--million-rays",
        action="store_true",
        help="Also run the Gaussian one-million-ray regression case.",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = [
        run_case("specular", 100_000),
        run_case("gaussian", 100_000),
        run_case("lambertian", 100_000),
    ]
    if args.million_rays:
        cases.append(run_case("gaussian", 1_000_000))

    gaussian_100k = next(
        item
        for item in cases
        if item["model"] == "gaussian" and item["ray_count"] == 100_000
    )
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "initial_gaussian_100k_runtime_sec": INITIAL_GAUSSIAN_100K_SEC,
        "optimized_gaussian_100k_runtime_sec": gaussian_100k["runtime_sec"],
        "gaussian_100k_speedup": (
            INITIAL_GAUSSIAN_100K_SEC / gaussian_100k["runtime_sec"]
        ),
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
