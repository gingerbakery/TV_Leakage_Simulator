from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import struct
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from leakage_simulator.raytracer import run_direct_ray_trace


CONTRACT = "perf3d_host_overhead_benchmark_v1"
OUTPUT_DIR = ROOT / "outputs" / "perf3d_host_overhead"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_files() -> tuple[Path, ...]:
    return (
        ROOT / "scripts" / "benchmark_perf3b2a_multibounce.py",
        ROOT / "src" / "leakage_simulator" / "raytracer.py",
        ROOT / "src" / "leakage_simulator" / "geometry.py",
        ROOT / "src" / "leakage_simulator" / "gpu_cuda_intersection.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_intersection.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_ordered_reducer.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_counter_wavefront.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_wavefront.py",
        ROOT / "src" / "leakage_simulator" / "wavefront_event_tape.py",
        ROOT / "src" / "leakage_simulator" / "fast_sampling.py",
        ROOT / "src" / "leakage_simulator" / "reflection.py",
        ROOT / "src" / "leakage_simulator" / "types.py",
        Path(__file__).resolve(),
    )


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): _sha256(path)
        for path in _source_files()
    }


def _p95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _distribution(values: list[float], ray_count: int) -> dict:
    p50 = statistics.median(values)
    return {
        "samples_sec": values,
        "p50_sec": p50,
        "p95_sec": _p95(values),
        "rays_per_sec_p50": ray_count / p50,
    }


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _float_bits(value):
    if isinstance(value, dict):
        return [(key, _float_bits(item)) for key, item in value.items()]
    if isinstance(value, (list, tuple)):
        return [_float_bits(item) for item in value]
    if isinstance(value, float):
        return {"float64_bits": struct.pack(">d", value).hex()}
    return value


def _payload_hash(payload) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run(ray_count: int, chunk_size: int, provider: str, commit_policy: str):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.compute_backend = (
        "gpu_cuda" if provider == "gpu_cuda" else "cpu"
    )
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=chunk_size,
        intersection_provider=provider,
        wavefront_planner="numba_cpu",
        wavefront_pipeline="soa_event_tape",
        wavefront_reducer="numba_cpu",
        wavefront_rng="counter_rng_v2",
        wavefront_reducer_commit=commit_policy,
    )


def _evidence(result) -> dict:
    performance = result.metrics["_performance_summary"]
    names = (
        "requested_intersection_provider",
        "intersection_provider",
        "native_available",
        "native_used",
        "native_attempt_count",
        "native_success_count",
        "intersection_fallback_count",
        "intersection_provider_unavailable_reason",
        "gpu_cuda_used",
        "requested_wavefront_planner",
        "wavefront_planner",
        "wavefront_planner_native_available",
        "wavefront_planner_native_used",
        "wavefront_planner_native_attempt_count",
        "wavefront_planner_native_success_count",
        "wavefront_planner_fallback_count",
        "wavefront_planner_unavailable_reason",
        "requested_wavefront_reducer",
        "wavefront_reducer",
        "wavefront_reducer_native_available",
        "wavefront_reducer_native_used",
        "wavefront_reflection_seed_dispatch",
        "wavefront_receiver_dispatch",
        "wavefront_reflection_rng",
        "wavefront_plan_sec",
        "wavefront_commit_sec",
        "wavefront_total_sec",
        "wavefront_event_tape_copy_bytes",
        "wavefront_event_tape_peak_bytes",
        "wavefront_event_tape_path_payload",
        "wavefront_event_tape_path_payload_full_chunk_count",
        "wavefront_event_tape_path_payload_omitted_chunk_count",
        "wavefront_event_tape_path_payload_suppressed_chunk_count",
        "wavefront_reducer_commit_policy",
        "wavefront_reducer_native_attempt_count",
        "wavefront_reducer_native_success_count",
        "wavefront_reducer_fallback_count",
        "wavefront_reducer_unavailable_reason",
        "wavefront_reducer_retained_tape_count",
        "wavefront_reducer_retained_primary_count",
        "wavefront_reducer_retained_event_count",
        "wavefront_reducer_final_flush_count",
        "wavefront_reducer_final_flush_sec",
        "wavefront_reducer_fallback_flush_count",
    )
    return {name: performance[name] for name in names}


def _validate_provider_evidence(evidence: dict, provider: str) -> None:
    checks = (
        (
            "intersection",
            provider,
            evidence["native_available"],
            evidence["native_success_count"],
            evidence["intersection_fallback_count"],
        ),
        (
            "planner",
            "numba_cpu",
            evidence["wavefront_planner_native_available"],
            evidence["wavefront_planner_native_success_count"],
            evidence["wavefront_planner_fallback_count"],
        ),
        (
            "reducer",
            "numba_cpu",
            evidence["wavefront_reducer_native_available"],
            evidence["wavefront_reducer_native_success_count"],
            evidence["wavefront_reducer_fallback_count"],
        ),
    )
    for component, requested, available, success_count, fallback_count in checks:
        if requested == "python_cpu" or available is not True:
            continue
        if success_count <= 0:
            raise RuntimeError(
                f"{component} provider {requested!r} was available but had no "
                "measured native success"
            )
        if fallback_count != 0:
            raise RuntimeError(
                f"{component} provider {requested!r} recorded "
                f"{fallback_count} measured fallback(s)"
            )


def benchmark(
    ray_count: int,
    repeats: int,
    chunk_size: int,
    provider: str,
) -> dict:
    source_start = _source_hashes()
    # Warm JIT/provider caches before either measured policy.
    _run(min(ray_count, chunk_size), chunk_size, provider, "per_tape")
    _run(min(ray_count, chunk_size), chunk_size, provider, "run_accumulator")

    durations = {"per_tape": [], "run_accumulator": []}
    commit_durations = {"per_tape": [], "run_accumulator": []}
    representatives = {}
    for repeat_index in range(repeats):
        policies = (
            ("per_tape", "run_accumulator")
            if repeat_index % 2 == 0
            else ("run_accumulator", "per_tape")
        )
        for policy in policies:
            started = time.perf_counter()
            result = _run(ray_count, chunk_size, provider, policy)
            durations[policy].append(time.perf_counter() - started)
            commit_durations[policy].append(
                result.metrics["_performance_summary"]["wavefront_commit_sec"]
            )
            representatives[policy] = result

    cases = {}
    for policy in ("per_tape", "run_accumulator"):
        evidence = _evidence(representatives[policy])
        _validate_provider_evidence(evidence, provider)
        cases[policy] = {
            **_distribution(durations[policy], ray_count),
            "commit": _distribution(commit_durations[policy], ray_count),
            "evidence": evidence,
        }

    baseline_payload = _semantic_payload(representatives["per_tape"])
    retained_payload = _semantic_payload(representatives["run_accumulator"])
    baseline_bits = _float_bits(baseline_payload)
    retained_bits = _float_bits(retained_payload)
    source_end = _source_hashes()
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ray_count": ray_count,
        "repeats": repeats,
        "chunk_size": chunk_size,
        "intersection_provider": provider,
        "measurement_order": "counterbalanced_alternating_v1",
        "source_hash_start": source_start,
        "source_hash_end": source_end,
        "source_changed_during_measurement": source_start != source_end,
        "cases": cases,
        "speedup": {
            "wall_p50": (
                cases["per_tape"]["p50_sec"]
                / cases["run_accumulator"]["p50_sec"]
            ),
            "commit_p50": (
                cases["per_tape"]["commit"]["p50_sec"]
                / cases["run_accumulator"]["commit"]["p50_sec"]
            ),
        },
        "correctness": {
            "ordered_float_bits_exact": baseline_bits == retained_bits,
            "semantic_json_exact": baseline_payload == retained_payload,
            "per_tape_semantic_sha256": _payload_hash(baseline_bits),
            "run_accumulator_semantic_sha256": _payload_hash(retained_bits),
        },
        "promotion_policy": (
            "Performance thresholds are report-only. Promotion additionally requires "
            "exact semantics, zero unexpected fallback, CPU default no-probe, Stop, "
            "concurrency, and packaged-runtime gates."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark PERF-3D host-overhead elimination and the run-retained "
            "ordered CPU accumulator."
        )
    )
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=65_536)
    parser.add_argument(
        "--provider",
        choices=("python_cpu", "numba_cpu", "gpu_cuda"),
        default="numba_cpu",
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.rays <= 0 or args.repeats <= 0 or args.chunk_size <= 0:
        raise SystemExit("rays, repeats and chunk-size must be positive")
    summary = benchmark(
        args.rays,
        args.repeats,
        args.chunk_size,
        args.provider,
    )
    encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2)
    print(encoded)
    if not args.no_write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / "summary.json"
        path.write_text(encoded + "\n", encoding="utf-8")
        print(f"summary={path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
