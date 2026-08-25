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
from leakage_simulator.gpu_cuda_summary_accumulator import PROVIDER_CONTRACT
from leakage_simulator.raytracer import run_direct_ray_trace


CONTRACT = "perf4c_gpu_accumulator_benchmark_v1"


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _run(builder, ray_count: int, chunk_size: int, accumulator: str):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = "gpu_cuda"
    started = time.perf_counter()
    result = run_direct_ray_trace(
        trace_input,
        intersection_batch_size=chunk_size,
        wavefront_residency="gpu_resident",
        gpu_accumulator=accumulator,
    )
    return result, time.perf_counter() - started


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "samples_sec": values,
        "median_sec": statistics.median(values),
        "minimum_sec": ordered[0],
        "maximum_sec": ordered[-1],
    }


def _case(builder, ray_count: int, chunk_size: int, repeats: int) -> dict:
    cold = {}
    representatives = {}
    for mode in ("host", "gpu"):
        result, elapsed = _run(builder, ray_count, chunk_size, mode)
        cold[mode] = elapsed
        representatives[mode] = result

    warm = {"host": [], "gpu": []}
    for repeat in range(repeats):
        order = ("host", "gpu") if repeat % 2 == 0 else ("gpu", "host")
        for mode in order:
            result, elapsed = _run(builder, ray_count, chunk_size, mode)
            warm[mode].append(elapsed)
            representatives[mode] = result

    parity = compare_semantic_payloads(
        _semantic_payload(representatives["host"]),
        _semantic_payload(representatives["gpu"]),
        absolute_tolerance=1e-9,
        relative_tolerance=1e-9,
        max_ulp_distance=1 << 48,
    )
    host_distribution = _distribution(warm["host"])
    gpu_distribution = _distribution(warm["gpu"])
    performance = representatives["gpu"].metrics["_performance_summary"]
    host_performance = representatives["host"].metrics["_performance_summary"]
    speedup = host_distribution["median_sec"] / gpu_distribution["median_sec"]
    return {
        "ray_count": ray_count,
        "chunk_size": chunk_size,
        "cold_sec": cold,
        "warm": {
            "host_ordered_reducer": host_distribution,
            "gpu_accumulator": gpu_distribution,
        },
        "speedup": speedup,
        "projection_100m_sec": {
            "host_ordered_reducer": (
                host_distribution["median_sec"] * 100_000_000 / ray_count
            ),
            "gpu_accumulator": (
                gpu_distribution["median_sec"] * 100_000_000 / ray_count
            ),
        },
        "parity": parity.to_dict(),
        "transfer": {
            "host_event_tape_bytes": host_performance[
                "wavefront_event_tape_copy_bytes"
            ],
            "gpu_compact_output_bytes": performance[
                "wavefront_event_tape_copy_bytes"
            ],
            "reduction_ratio": (
                1.0
                - performance["wavefront_event_tape_copy_bytes"]
                / max(1, host_performance["wavefront_event_tape_copy_bytes"])
            ),
        },
        "evidence": {
            "wavefront_residency": performance["wavefront_residency"],
            "accumulator_contract": performance[
                "gpu_summary_accumulator_contract"
            ],
            "accumulator_success_count": performance[
                "gpu_summary_accumulator_success_count"
            ],
            "resident_fallback_count": performance[
                "gpu_resident_wavefront_fallback_count"
            ],
            "accumulator_kernel_sec": performance[
                "gpu_summary_accumulator_kernel_sec"
            ],
            "accumulator_output_download_sec": performance[
                "gpu_summary_accumulator_output_download_sec"
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
        default=ROOT / "outputs" / "perf4c_gpu_accumulator" / "benchmark.json",
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
        raise SystemExit("PERF-4C parity contract failed")
    if any(
        case["evidence"]["accumulator_contract"] != PROVIDER_CONTRACT
        or case["evidence"]["accumulator_success_count"] <= 0
        or case["evidence"]["resident_fallback_count"] != 0
        for case in cases.values()
    ):
        raise SystemExit("PERF-4C production execution proof failed")
    payload = {
        "contract": CONTRACT,
        "provider_contract": PROVIDER_CONTRACT,
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
