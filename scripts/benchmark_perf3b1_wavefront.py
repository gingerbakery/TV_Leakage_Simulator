from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_rt2c_reflection_report import build_model_input
from leakage_simulator.raytracer import run_direct_ray_trace


OUTPUT_DIR = ROOT / "outputs" / "perf3b1_wavefront"


def build_case(ray_count: int):
    trace_input = build_model_input("gaussian")
    trace_input.emitters[0].ray_count = ray_count
    trace_input.config.ray_count = ray_count
    trace_input.config.max_depth = 1
    trace_input.config.contribution_mode = "summary"
    trace_input.config.store_ray_paths = False
    return trace_input


def semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def run_case(
    ray_count: int,
    repeats: int,
    dispatch: str,
    batch_size: int,
) -> tuple[dict, dict]:
    durations = []
    intersection_durations = []
    representative = None
    representative_payload = None
    for _ in range(repeats):
        trace_input = build_case(ray_count)
        started = time.perf_counter()
        result = run_direct_ray_trace(
            trace_input,
            intersection_dispatch=dispatch,
            intersection_batch_size=batch_size,
        )
        durations.append(time.perf_counter() - started)
        intersection_durations.append(
            float(result.metrics["_performance_summary"]["intersection_sec"])
        )
        payload = semantic_payload(result)
        if representative_payload is None:
            representative_payload = payload
            representative = result
        elif representative_payload != payload:
            raise RuntimeError("Repeated PERF-3B-1 benchmark results were not deterministic")

    assert representative is not None and representative_payload is not None
    median_sec = statistics.median(durations)
    performance = representative.metrics["_performance_summary"]
    return (
        {
            "dispatch": dispatch,
            "batch_size": batch_size if dispatch == "batch" else None,
            "durations_sec": durations,
            "median_sec": median_sec,
            "primary_rays_per_sec": ray_count / median_sec,
            "receiver_hit_count": representative.receiver_hit_count,
            "surface_hit_count": representative.surface_hit_count,
            "terminated_ray_count": representative.terminated_ray_count,
            "receiver_flux_lumen": representative.metrics["observer"]["total_flux_lumen"],
            "intersection_dispatch": performance["intersection_dispatch"],
            "intersection_batch_count": performance["intersection_batch_count"],
            "intersection_batch_max_size": performance["intersection_batch_max_size"],
            "intersection_ray_count": performance["intersection_ray_count"],
            "intersection_durations_sec": intersection_durations,
            "intersection_sec": statistics.median(intersection_durations),
            "intersection_timing_scope": performance["intersection_timing_scope"],
            "native_batch": performance["native_batch"],
        },
        representative_payload,
    )


def benchmark(ray_count: int, repeats: int, batch_sizes: list[int]) -> dict:
    scalar, scalar_payload = run_case(
        ray_count,
        repeats,
        dispatch="scalar",
        batch_size=batch_sizes[0],
    )
    batch_cases = []
    semantic_mismatch_count = 0
    for batch_size in batch_sizes:
        case, payload = run_case(
            ray_count,
            repeats,
            dispatch="batch",
            batch_size=batch_size,
        )
        case["speedup_vs_scalar"] = (
            case["primary_rays_per_sec"] / scalar["primary_rays_per_sec"]
        )
        if payload != scalar_payload:
            semantic_mismatch_count += 1
        batch_cases.append(case)

    return {
        "contract": "perf3b1_wavefront_batch_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ray_count": ray_count,
        "repeats": repeats,
        "sampler_batch_size": 65_536,
        "scalar": scalar,
        "batch_cases": batch_cases,
        "semantic_mismatch_count": semantic_mismatch_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark PERF-3B-1 scalar and wavefront batch dispatch."
    )
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[256, 4096, 65_536])
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.rays <= 0 or args.repeats <= 0:
        raise SystemExit("rays and repeats must be positive")
    if not args.batch_sizes or any(size <= 0 for size in args.batch_sizes):
        raise SystemExit("batch sizes must be positive")

    summary = benchmark(args.rays, args.repeats, args.batch_sizes)
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
