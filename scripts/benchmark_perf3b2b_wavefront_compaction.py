from __future__ import annotations

import argparse
import hashlib
import json
import math
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

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from leakage_simulator.raytracer import run_direct_ray_trace


OUTPUT_DIR = ROOT / "outputs" / "perf3b2b_wavefront_compaction"
CONTRACT = "perf3b2b_wavefront_compaction_v1"
DEFAULT_BATCH_SIZES = [256, 1024, 4096]
TIMING_FIELDS = (
    "intersection_sec",
    "native_scene_build_sec",
    "native_jit_compile_sec",
    "native_execute_sec",
    "wavefront_state_build_sec",
    "wavefront_receiver_sec",
    "wavefront_geometry_sec",
    "wavefront_plan_sec",
    "wavefront_commit_sec",
    "wavefront_total_sec",
    "wavefront_planner_native_face_table_prepare_sec",
    "wavefront_planner_native_input_prepare_sec",
    "wavefront_planner_native_dispatch_sec",
    "wavefront_planner_native_execute_sec",
    "wavefront_planner_native_jit_compile_sec",
)
COUNTER_FIELDS = (
    "intersection_batch_count",
    "intersection_batch_max_size",
    "intersection_ray_count",
    "wavefront_chunk_count",
    "wavefront_primary_ray_count",
    "wavefront_depth_batch_count",
    "wavefront_max_active_ray_count",
    "wavefront_max_observed_depth",
    "wavefront_geometry_ray_count",
    "wavefront_geometry_hit_count",
    "wavefront_compacted_ray_count",
    "stored_path_count",
    "wavefront_path_materialized_count",
    "wavefront_path_materialization_skipped_count",
    "wavefront_planner_logical_row_count",
    "wavefront_planner_python_sidecar_row_count",
    "wavefront_planner_native_attempt_count",
    "wavefront_planner_native_attempt_row_count",
    "wavefront_planner_native_success_count",
    "wavefront_planner_native_success_row_count",
    "wavefront_planner_fallback_count",
    "wavefront_planner_fallback_row_count",
)
DISPATCH_FIELDS = (
    "intersection_dispatch",
    "intersection_provider",
    "wavefront_receiver_dispatch",
    "wavefront_surface_geometry_dispatch",
    "wavefront_path_quota_dispatch",
    "wavefront_reflection_rng",
    "wavefront_planner",
)


def _percentile_95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _distribution(values: list[float]) -> dict:
    return {
        "samples_sec": values,
        "p50_sec": statistics.median(values),
        "p95_sec": _percentile_95(values),
    }


def _semantic_json(result) -> str:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_case(
    ray_count: int,
    contribution_mode: str,
    store_ray_paths: bool,
    max_stored_paths: int,
):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.contribution_mode = contribution_mode
    trace_input.config.store_ray_paths = store_ray_paths
    trace_input.config.max_stored_paths = max_stored_paths
    trace_input.mesh.prepare_acceleration()
    return trace_input


def _performance_signature(result) -> dict:
    performance = result.metrics["_performance_summary"]
    return {
        "result_counts": {
            "total_rays": result.total_rays,
            "receiver_hit_count": result.receiver_hit_count,
            "surface_hit_count": result.surface_hit_count,
            "terminated_ray_count": result.terminated_ray_count,
        },
        "counters": {key: performance[key] for key in COUNTER_FIELDS},
        "dispatch": {key: performance[key] for key in DISPATCH_FIELDS},
        "native_used": performance["native_used"],
        "native_provider_version": performance["native_provider_version"],
    }


def _semantic_reference(
    ray_count: int,
    contribution_mode: str,
    store_ray_paths: bool,
    max_stored_paths: int,
) -> tuple[dict, str]:
    trace_input = _build_case(
        ray_count,
        contribution_mode,
        store_ray_paths,
        max_stored_paths,
    )
    started = time.perf_counter()
    result = run_direct_ray_trace(
        trace_input,
        intersection_dispatch="scalar",
        intersection_provider="python_cpu",
    )
    wall_sec = time.perf_counter() - started
    semantic_json = _semantic_json(result)
    return (
        {
            "dispatch": "scalar",
            "provider": "python_cpu",
            "wall_sec": wall_sec,
            "primary_rays_per_sec": ray_count / wall_sec,
            "semantic_sha256": _sha256(semantic_json),
            "receiver_hit_count": result.receiver_hit_count,
            "surface_hit_count": result.surface_hit_count,
            "terminated_ray_count": result.terminated_ray_count,
            "stored_path_count": len(result.stored_paths),
        },
        semantic_json,
    )


def _run_wavefront_case(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_size: int,
    provider: str,
    planner_provider: str,
    contribution_mode: str,
    store_ray_paths: bool,
    max_stored_paths: int,
    reference_semantic_json: str,
) -> dict:
    trace_input = _build_case(
        ray_count,
        contribution_mode,
        store_ray_paths,
        max_stored_paths,
    )

    warmup_semantic_mismatch_count = 0
    for _ in range(warmups):
        warmup_result = run_direct_ray_trace(
            trace_input,
            intersection_dispatch="batch",
            intersection_batch_size=batch_size,
            intersection_provider=provider,
            wavefront_planner=planner_provider,
        )
        if _semantic_json(warmup_result) != reference_semantic_json:
            warmup_semantic_mismatch_count += 1

    wall_values: list[float] = []
    reported_runtime_values: list[float] = []
    timing_values = {key: [] for key in TIMING_FIELDS}
    semantic_values: list[str] = []
    signatures: list[dict] = []
    representative = None
    for _ in range(repeats):
        started = time.perf_counter()
        result = run_direct_ray_trace(
            trace_input,
            intersection_dispatch="batch",
            intersection_batch_size=batch_size,
            intersection_provider=provider,
            wavefront_planner=planner_provider,
        )
        wall_values.append(time.perf_counter() - started)
        reported_runtime_values.append(float(result.runtime_sec))
        performance = result.metrics["_performance_summary"]
        for key in TIMING_FIELDS:
            value = float(performance[key])
            if not math.isfinite(value) or value < 0.0:
                raise RuntimeError(f"{key} must be a finite non-negative timing")
            timing_values[key].append(value)
        semantic_values.append(_semantic_json(result))
        signatures.append(_performance_signature(result))
        if representative is None:
            representative = result

    assert representative is not None
    representative_semantic = semantic_values[0]
    measured_semantic_mismatch_count = sum(
        value != reference_semantic_json for value in semantic_values
    )
    repeat_semantic_mismatch_count = sum(
        value != representative_semantic for value in semantic_values[1:]
    )
    performance_counter_mismatch_count = sum(
        value != signatures[0] for value in signatures[1:]
    )
    wall_distribution = _distribution(wall_values)
    p50_sec = wall_distribution["p50_sec"]
    performance = representative.metrics["_performance_summary"]

    return {
        "name": f"batch_{batch_size}",
        "batch_size": batch_size,
        "repeats": repeats,
        "warmups": warmups,
        "requested_provider": provider,
        "requested_planner_provider": planner_provider,
        "effective_provider": performance["intersection_provider"],
        "native_used": performance["native_used"],
        "native_provider_version": performance["native_provider_version"],
        "wall_time": wall_distribution,
        "reported_runtime": _distribution(reported_runtime_values),
        "primary_rays_per_sec": ray_count / p50_sec,
        "timings": {
            key: _distribution(values)
            for key, values in timing_values.items()
        },
        "geometry": {
            "dispatch": performance["wavefront_surface_geometry_dispatch"],
            "ray_count": performance["wavefront_geometry_ray_count"],
            "hit_count": performance["wavefront_geometry_hit_count"],
        },
        "path": {
            "enabled": store_ray_paths,
            "max_stored_paths": max_stored_paths,
            "dispatch": performance["wavefront_path_quota_dispatch"],
            "stored_path_count": performance["stored_path_count"],
            "materialized_count": performance[
                "wavefront_path_materialized_count"
            ],
            "materialization_skipped_count": performance[
                "wavefront_path_materialization_skipped_count"
            ],
        },
        "result_counts": signatures[0]["result_counts"],
        "wavefront_counters": signatures[0]["counters"],
        "dispatch": signatures[0]["dispatch"],
        "semantic_sha256": _sha256(representative_semantic),
        "semantic_match_reference": measured_semantic_mismatch_count == 0,
        "warmup_semantic_mismatch_count": warmup_semantic_mismatch_count,
        "measured_semantic_mismatch_count": measured_semantic_mismatch_count,
        "repeat_semantic_mismatch_count": repeat_semantic_mismatch_count,
        "performance_counter_mismatch_count": (
            performance_counter_mismatch_count
        ),
        "semantic_mismatch_count": (
            warmup_semantic_mismatch_count
            + measured_semantic_mismatch_count
        ),
    }


def _benchmark_scenario(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_sizes: list[int],
    provider: str,
    planner_provider: str,
    contribution_mode: str,
    store_ray_paths: bool,
    max_stored_paths: int,
) -> dict:
    reference, reference_semantic_json = _semantic_reference(
        ray_count,
        contribution_mode,
        store_ray_paths,
        max_stored_paths,
    )
    batch_cases = [
        _run_wavefront_case(
            ray_count=ray_count,
            repeats=repeats,
            warmups=warmups,
            batch_size=batch_size,
            provider=provider,
            planner_provider=planner_provider,
            contribution_mode=contribution_mode,
            store_ray_paths=store_ray_paths,
            max_stored_paths=max_stored_paths,
            reference_semantic_json=reference_semantic_json,
        )
        for batch_size in batch_sizes
    ]
    return {
        "name": (
            f"depth10_{contribution_mode}_"
            f"paths_{'on' if store_ray_paths else 'off'}"
        ),
        "contribution_mode": contribution_mode,
        "store_ray_paths": store_ray_paths,
        "max_stored_paths": max_stored_paths,
        "semantic_reference": reference,
        "batch_cases": batch_cases,
        "semantic_mismatch_count": sum(
            case["semantic_mismatch_count"] for case in batch_cases
        ),
    }


def benchmark(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_sizes: list[int],
    provider: str,
    planner_provider: str,
    max_stored_paths: int,
) -> dict:
    scenarios = [
        _benchmark_scenario(
            ray_count=ray_count,
            repeats=repeats,
            warmups=warmups,
            batch_sizes=batch_sizes,
            provider=provider,
            planner_provider=planner_provider,
            contribution_mode=contribution_mode,
            store_ray_paths=store_ray_paths,
            max_stored_paths=max_stored_paths,
        )
        for contribution_mode in ("summary", "detailed")
        for store_ray_paths in (False, True)
    ]
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "source_sha256": {
            "geometry.py": _file_sha256(
                ROOT / "src" / "leakage_simulator" / "geometry.py"
            ),
            "raytracer.py": _file_sha256(
                ROOT / "src" / "leakage_simulator" / "raytracer.py"
            ),
            "native_cpu_wavefront.py": _file_sha256(
                ROOT
                / "src"
                / "leakage_simulator"
                / "native_cpu_wavefront.py"
            ),
        },
        "scene": "deterministic_depth10_specular_corridor",
        "ray_count": ray_count,
        "max_depth": 10,
        "repeats": repeats,
        "warmups_per_case": warmups,
        "batch_sizes": batch_sizes,
        "requested_provider": provider,
        "requested_planner_provider": planner_provider,
        "max_stored_paths": max_stored_paths,
        "native_cold_start_excluded": warmups > 0,
        "semantic_comparison": (
            "exact ordered JSON excluding run_id/runtime/performance summary"
        ),
        "scenarios": scenarios,
        "semantic_mismatch_count": sum(
            scenario["semantic_mismatch_count"] for scenario in scenarios
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark PERF-3B-2B depth-10 wavefront geometry and ordered "
            "path compaction."
        )
    )
    parser.add_argument(
        "--rays",
        type=int,
        default=10_000,
        help="Primary rays per depth-10 scenario; use a small value for smoke runs.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Measured warm repeats per scenario and chunk size.",
    )
    parser.add_argument(
        "--warmups",
        type=int,
        default=1,
        help="Unmeasured warmup runs per scenario and chunk size.",
    )
    parser.add_argument(
        "--batch-sizes",
        type=int,
        nargs="+",
        default=DEFAULT_BATCH_SIZES,
    )
    parser.add_argument(
        "--provider",
        choices=("python_cpu", "numba_cpu"),
        default="numba_cpu",
    )
    parser.add_argument(
        "--planner-provider",
        choices=("auto", "python_cpu", "numba_cpu"),
        default="python_cpu",
    )
    parser.add_argument("--max-stored-paths", type=int, default=500)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    if args.rays <= 0 or args.repeats <= 0:
        raise SystemExit("rays and repeats must be positive")
    if args.warmups < 0:
        raise SystemExit("warmups must be non-negative")
    if not args.batch_sizes or any(size <= 0 for size in args.batch_sizes):
        raise SystemExit("batch sizes must be positive")
    if args.max_stored_paths <= 0:
        raise SystemExit("max stored paths must be positive")

    summary = benchmark(
        ray_count=args.rays,
        repeats=args.repeats,
        warmups=args.warmups,
        batch_sizes=args.batch_sizes,
        provider=args.provider,
        planner_provider=args.planner_provider,
        max_stored_paths=args.max_stored_paths,
    )
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
    )
    print(encoded)
    if not args.no_write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = OUTPUT_DIR / "summary.json"
        summary_path.write_text(f"{encoded}\n", encoding="utf-8")
        print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
