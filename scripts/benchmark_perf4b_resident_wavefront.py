from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import platform
from pathlib import Path
import statistics
import sys
import time
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from benchmark_perf4a_target_workloads import _stochastic_two_bounce_case
from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.gpu_cuda_resident_wavefront import (
    PROVIDER_CONTRACT as RESIDENT_PROVIDER_CONTRACT,
)
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace


CONTRACT = "perf4b_resident_wavefront_benchmark_v1"
MONTE_CARLO_CONTRACT = "cpu_gpu_deterministic_batch_v1"
OUTPUT_DIR = ROOT / "outputs" / "perf4b_resident_wavefront"
DEFAULT_TARGET_PRIMARY_RAYS = 100_000_000


WorkloadBuilder = Callable[[int], DirectRayTraceInput]

WORKLOADS: dict[str, WorkloadBuilder] = {
    "stochastic_two_bounce": _stochastic_two_bounce_case,
    "trapped_corridor_depth10": build_depth_ten_case,
}


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_files() -> tuple[Path, ...]:
    return (
        ROOT / "scripts" / "benchmark_perf4b_resident_wavefront.py",
        ROOT / "scripts" / "benchmark_perf4a_target_workloads.py",
        ROOT / "scripts" / "perf4_accuracy.py",
        ROOT / "src" / "leakage_simulator" / "raytracer.py",
        ROOT / "src" / "leakage_simulator" / "gpu_cuda_intersection.py",
        ROOT / "src" / "leakage_simulator" / "gpu_cuda_resident_wavefront.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_counter_wavefront.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_ordered_reducer.py",
        ROOT / "src" / "leakage_simulator" / "wavefront_event_tape.py",
    )


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
        for path in _source_files()
    }


def _p95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _distribution(values: list[float], ray_count: int) -> dict:
    median = statistics.median(values)
    return {
        "samples_sec": values,
        "p50_sec": median,
        "p95_sec": _p95(values),
        "primary_rays_per_sec_p50": ray_count / median,
    }


def _projection(measured_sec: float, measured_rays: int) -> dict:
    projected_sec = measured_sec * DEFAULT_TARGET_PRIMARY_RAYS / measured_rays
    return {
        "target_primary_rays": DEFAULT_TARGET_PRIMARY_RAYS,
        "linear_projected_sec": projected_sec,
        "linear_projected_min": projected_sec / 60.0,
    }


def _run(
    builder: WorkloadBuilder,
    ray_count: int,
    chunk_size: int,
    residency: str,
):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = "gpu_cuda"
    trace_input.config.contribution_mode = "summary"
    started = time.perf_counter()
    result = run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider="gpu_cuda",
        wavefront_planner="numba_cpu",
        wavefront_pipeline="soa_event_tape",
        wavefront_reducer="numba_cpu",
        wavefront_rng="counter_rng_v2",
        wavefront_reducer_commit="run_accumulator",
        wavefront_residency=residency,
        gpu_accumulator="host",
    )
    return result, time.perf_counter() - started


def _execution_evidence(result) -> dict:
    performance = result.metrics["_performance_summary"]
    return {
        "compute_execution_state": performance["compute_execution_state"],
        "compute_execution_reason": performance["compute_execution_reason"],
        "monte_carlo_contract": performance["monte_carlo_contract"],
        "wavefront_residency": performance["wavefront_residency"],
        "wavefront_pipeline": performance["wavefront_pipeline"],
        "gpu_cuda_gpu_success_count": performance[
            "gpu_cuda_gpu_success_count"
        ],
        "intersection_fallback_count": performance[
            "intersection_fallback_count"
        ],
        "resident_contract": performance[
            "gpu_resident_wavefront_contract"
        ],
        "resident_success_count": performance[
            "gpu_resident_wavefront_success_count"
        ],
        "resident_fallback_count": performance[
            "gpu_resident_wavefront_fallback_count"
        ],
        "resident_fallback_phase": performance[
            "gpu_resident_wavefront_fallback_phase"
        ],
        "resident_fallback_reason": performance[
            "gpu_resident_wavefront_fallback_reason"
        ],
        "resident_kernel_sec": performance[
            "gpu_resident_wavefront_kernel_sec"
        ],
        "resident_output_download_sec": performance[
            "gpu_resident_wavefront_output_download_sec"
        ],
        "resident_tape_build_sec": performance[
            "gpu_resident_wavefront_tape_build_sec"
        ],
        "resident_logical_intersection_rows": performance[
            "gpu_resident_wavefront_logical_intersection_rows"
        ],
        "event_tape_copy_bytes": performance[
            "wavefront_event_tape_copy_bytes"
        ],
        "receiver_hit_count": result.receiver_hit_count,
        "surface_hit_count": result.surface_hit_count,
        "terminated_ray_count": result.terminated_ray_count,
    }


def _validate_evidence(evidence: dict, residency: str) -> None:
    if evidence["compute_execution_state"] not in {"gpu_active", "gpu_mixed"}:
        raise RuntimeError(f"CUDA execution was not proven for {residency}")
    if evidence["gpu_cuda_gpu_success_count"] <= 0:
        raise RuntimeError(f"CUDA committed no batch for {residency}")
    if evidence["intersection_fallback_count"] != 0:
        raise RuntimeError(f"intersection fallback occurred for {residency}")
    if evidence["monte_carlo_contract"] != MONTE_CARLO_CONTRACT:
        raise RuntimeError(f"Monte Carlo contract mismatch for {residency}")
    if evidence["wavefront_residency"] != residency:
        raise RuntimeError(f"wavefront residency mismatch for {residency}")
    if residency == "gpu_resident":
        if evidence["resident_contract"] != RESIDENT_PROVIDER_CONTRACT:
            raise RuntimeError("resident provider contract mismatch")
        if evidence["resident_success_count"] <= 0:
            raise RuntimeError("resident provider committed no batch")
        if evidence["resident_fallback_count"] != 0:
            raise RuntimeError("resident provider fell back")


def benchmark_workload(
    name: str,
    builder: WorkloadBuilder,
    ray_count: int,
    repeats: int,
    chunk_size: int,
) -> dict:
    cold_results = {}
    cold_seconds = {}
    for residency in ("host_roundtrip", "gpu_resident"):
        result, elapsed = _run(builder, ray_count, chunk_size, residency)
        cold_results[residency] = result
        cold_seconds[residency] = elapsed

    warm_seconds = {"host_roundtrip": [], "gpu_resident": []}
    representatives = dict(cold_results)
    for repeat_index in range(repeats):
        order = (
            ("host_roundtrip", "gpu_resident")
            if repeat_index % 2 == 0
            else ("gpu_resident", "host_roundtrip")
        )
        for residency in order:
            result, elapsed = _run(builder, ray_count, chunk_size, residency)
            warm_seconds[residency].append(elapsed)
            representatives[residency] = result

    host_payload = _semantic_payload(representatives["host_roundtrip"])
    resident_payload = _semantic_payload(representatives["gpu_resident"])
    parity = compare_semantic_payloads(host_payload, resident_payload)
    host_evidence = _execution_evidence(representatives["host_roundtrip"])
    resident_evidence = _execution_evidence(representatives["gpu_resident"])
    _validate_evidence(host_evidence, "host_roundtrip")
    _validate_evidence(resident_evidence, "gpu_resident")

    host_distribution = _distribution(warm_seconds["host_roundtrip"], ray_count)
    resident_distribution = _distribution(
        warm_seconds["gpu_resident"],
        ray_count,
    )
    speedup = host_distribution["p50_sec"] / resident_distribution["p50_sec"]
    return {
        "name": name,
        "primary_ray_count": ray_count,
        "chunk_size": chunk_size,
        "max_reflections": builder(ray_count).config.max_depth,
        "cold_sec": cold_seconds,
        "warm": {
            "host_roundtrip": host_distribution,
            "gpu_resident": resident_distribution,
        },
        "resident_speedup_p50": speedup,
        "performance_improved": speedup > 1.0,
        "projection_100m": {
            "host_roundtrip": _projection(
                host_distribution["p50_sec"],
                ray_count,
            ),
            "gpu_resident": _projection(
                resident_distribution["p50_sec"],
                ray_count,
            ),
        },
        "parity": parity.to_dict(),
        "host_evidence": host_evidence,
        "resident_evidence": resident_evidence,
        "passed": parity.passed,
    }


def benchmark(
    *,
    workload_names: tuple[str, ...],
    ray_count: int,
    repeats: int,
    chunk_size: int,
) -> dict:
    unknown = sorted(set(workload_names) - set(WORKLOADS))
    if unknown:
        raise ValueError(f"unknown workload(s): {', '.join(unknown)}")

    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    preflight_payload = {
        **asdict(preflight),
        "provider_contract": gpu_cuda.PROVIDER_CONTRACT,
    }
    preflight_passed = bool(
        preflight.available
        and preflight.strict_float64
        and preflight.kernel_executed
        and preflight.kernel_verified
        and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
        and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1"
    )
    preflight_payload["passed"] = preflight_passed
    if not preflight_passed:
        raise RuntimeError(
            "CUDA production preflight failed: "
            f"{preflight.reason_code or 'unknown'}"
        )

    source_start = _source_hashes()
    cases = [
        benchmark_workload(
            name,
            WORKLOADS[name],
            ray_count,
            repeats,
            chunk_size,
        )
        for name in workload_names
    ]
    source_end = _source_hashes()
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ray_count_per_workload": ray_count,
        "repeats": repeats,
        "chunk_size": chunk_size,
        "workload_order": list(workload_names),
        "measurement_policy": "cold_each_then_counterbalanced_warm_v1",
        "source_hash_start": source_start,
        "source_hash_end": source_end,
        "source_changed_during_measurement": source_start != source_end,
        "gpu_preflight": preflight_payload,
        "cases": cases,
        "correctness_passed": all(case["passed"] for case in cases),
        "performance_improved": all(
            case["performance_improved"] for case in cases
        ),
        "passed": (
            source_start == source_end
            and all(case["passed"] for case in cases)
        ),
        "limitations": [
            "Synthetic controls do not replace anonymized in-house TV ROI scenes.",
            "100M projections are linear and do not guarantee 5% receiver error.",
            "CUDA and CPU transcendental functions may differ by a few float64 ULPs.",
            "PERF-4B still reduces the downloaded event tape on the CPU.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare PERF-4B host-roundtrip and GPU-resident wavefronts."
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        choices=tuple(WORKLOADS),
        default=tuple(WORKLOADS),
    )
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=65_536)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if min(args.rays, args.repeats, args.chunk_size) <= 0:
        parser.error("rays, repeats and chunk-size must be positive")
    if args.rays < 8192:
        parser.error("GPU evidence requires at least 8192 rays")

    summary = benchmark(
        workload_names=tuple(args.workloads),
        ray_count=args.rays,
        repeats=args.repeats,
        chunk_size=args.chunk_size,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if not args.no_write:
        output_path = args.json_out or OUTPUT_DIR / "summary.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"summary={output_path}", file=sys.stderr)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
