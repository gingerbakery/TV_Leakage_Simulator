from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from perf4_accuracy import compare_semantic_payloads
from verify_gpu_cpu_accuracy import build_stochastic_two_bounce_case
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.gpu_cuda_resident_wavefront import (
    COMPACT_WORKSPACE_CONTRACT,
    FULL_WORKSPACE_CONTRACT,
)
from leakage_simulator.raytracer import run_direct_ray_trace


CONTRACT = "perf4d_compact_summary_workspace_benchmark_v1"


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _run(builder, ray_count: int, chunk_size: int, workspace: str):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = "gpu_cuda"
    trace_input.config.store_ray_paths = True
    trace_input.config.max_stored_paths = 500
    started = time.perf_counter()
    result = run_direct_ray_trace(
        trace_input,
        intersection_batch_size=chunk_size,
        wavefront_residency="gpu_resident",
        gpu_accumulator="gpu",
        gpu_workspace=workspace,
    )
    return result, time.perf_counter() - started


def _distribution(values: list[float]) -> dict:
    return {
        "samples_sec": values,
        "median_sec": statistics.median(values),
        "minimum_sec": min(values),
        "maximum_sec": max(values),
    }


def _case(builder, ray_count: int, chunk_size: int, repeats: int) -> dict:
    representatives = {}
    cold = {}
    for mode in ("full", "compact"):
        result, elapsed = _run(builder, ray_count, chunk_size, mode)
        representatives[mode] = result
        cold[mode] = elapsed

    warm = {"full": [], "compact": []}
    for repeat in range(repeats):
        order = ("full", "compact") if repeat % 2 == 0 else ("compact", "full")
        for mode in order:
            result, elapsed = _run(builder, ray_count, chunk_size, mode)
            representatives[mode] = result
            warm[mode].append(elapsed)

    parity = compare_semantic_payloads(
        _semantic_payload(representatives["full"]),
        _semantic_payload(representatives["compact"]),
        absolute_tolerance=1e-9,
        relative_tolerance=1e-9,
        max_ulp_distance=1 << 48,
    )
    full_distribution = _distribution(warm["full"])
    compact_distribution = _distribution(warm["compact"])
    full_performance = representatives["full"].metrics["_performance_summary"]
    compact_performance = representatives["compact"].metrics[
        "_performance_summary"
    ]
    full_bytes = full_performance["gpu_resident_workspace_peak_bytes"]
    compact_bytes = compact_performance["gpu_resident_workspace_peak_bytes"]
    return {
        "ray_count": ray_count,
        "chunk_size": chunk_size,
        "stored_path_quota": 500,
        "cold_sec": cold,
        "warm": {
            "full_geometry": full_distribution,
            "compact_sparse_retrace": compact_distribution,
        },
        "speedup": (
            full_distribution["median_sec"]
            / compact_distribution["median_sec"]
        ),
        "parity": parity.to_dict(),
        "workspace": {
            "full_bytes": full_bytes,
            "compact_bytes": compact_bytes,
            "reduction_ratio": 1.0 - compact_bytes / max(1, full_bytes),
            "full_geometry_capacity": full_performance[
                "gpu_resident_event_geometry_capacity"
            ],
            "compact_geometry_capacity": compact_performance[
                "gpu_resident_event_geometry_capacity"
            ],
        },
        "compact_timing": {
            "path_select_sec": compact_performance[
                "gpu_summary_path_select_sec"
            ],
            "path_retrace_sec": compact_performance[
                "gpu_summary_path_retrace_sec"
            ],
            "path_download_sec": compact_performance[
                "gpu_summary_path_download_sec"
            ],
        },
        "evidence": {
            "full_contract": full_performance[
                "gpu_resident_workspace_contract"
            ],
            "compact_contract": compact_performance[
                "gpu_resident_workspace_contract"
            ],
            "wavefront_residency": compact_performance[
                "wavefront_residency"
            ],
            "fallback_count": compact_performance[
                "gpu_resident_wavefront_fallback_count"
            ],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--chunk-size", type=int, default=65_536)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "perf4d_compact_workspace" / "benchmark.json",
    )
    args = parser.parse_args()
    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    if not (
        preflight.available
        and preflight.strict_float64
        and preflight.kernel_executed
        and preflight.kernel_verified
        and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
    ):
        raise SystemExit(preflight.reason_code or "production CUDA unavailable")

    cases = {
        "stochastic_depth2": _case(
            build_stochastic_two_bounce_case,
            args.rays,
            args.chunk_size,
            args.repeats,
        ),
        "trapped_depth10": _case(
            build_depth_ten_case,
            args.rays,
            args.chunk_size,
            args.repeats,
        ),
    }
    if any(not case["parity"]["passed"] for case in cases.values()):
        raise SystemExit("PERF-4D parity contract failed")
    if any(
        case["evidence"]["full_contract"] != FULL_WORKSPACE_CONTRACT
        or case["evidence"]["compact_contract"] != COMPACT_WORKSPACE_CONTRACT
        or case["evidence"]["wavefront_residency"] != "gpu_resident"
        or case["evidence"]["fallback_count"] != 0
        for case in cases.values()
    ):
        raise SystemExit("PERF-4D production execution proof failed")
    payload = {
        "contract": CONTRACT,
        "preflight": asdict(preflight),
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
