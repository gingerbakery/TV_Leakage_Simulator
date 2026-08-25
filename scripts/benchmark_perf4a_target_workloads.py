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

from benchmark_perf3b2a_multibounce import (
    build_depth_ten_case,
    build_two_bounce_case,
)
from verify_gpu_cpu_accuracy import build_face_direct_case
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace


CONTRACT = "perf4a_target_workload_benchmark_v1"
MONTE_CARLO_CONTRACT = "cpu_gpu_deterministic_batch_v1"
OUTPUT_DIR = ROOT / "outputs" / "perf4a_target_workloads"
DEFAULT_TARGET_PRIMARY_RAYS = 100_000_000
DEFAULT_TARGET_SECONDS = (300, 600, 900, 1200, 1800)


WorkloadBuilder = Callable[[int], DirectRayTraceInput]


def _stochastic_two_bounce_case(ray_count: int) -> DirectRayTraceInput:
    trace_input = build_two_bounce_case(ray_count)
    trace_input.optical_profiles[0].scatter_model = "mixed"
    trace_input.optical_profiles[0].specular_ratio = 0.55
    trace_input.optical_profiles[0].diffuse_ratio = 0.45
    trace_input.optical_profiles[0].gaussian_sigma_deg = 12.0
    trace_input.optical_profiles[1].scatter_model = "lambertian"
    trace_input.config.min_energy = 0.005
    trace_input.config.termination_mode = "russian_roulette"
    trace_input.config.store_ray_paths = False
    trace_input.config.max_stored_paths = 0
    return trace_input


WORKLOADS: dict[str, WorkloadBuilder] = {
    "face_direct": build_face_direct_case,
    "stochastic_two_bounce": _stochastic_two_bounce_case,
    "trapped_corridor_depth10": build_depth_ten_case,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_files() -> tuple[Path, ...]:
    return (
        ROOT / "scripts" / "benchmark_perf4a_target_workloads.py",
        ROOT / "scripts" / "benchmark_perf3b2a_multibounce.py",
        ROOT / "scripts" / "verify_gpu_cpu_accuracy.py",
        ROOT / "src" / "leakage_simulator" / "raytracer.py",
        ROOT / "src" / "leakage_simulator" / "geometry.py",
        ROOT / "src" / "leakage_simulator" / "gpu_cuda_intersection.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_counter_wavefront.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_ordered_reducer.py",
        ROOT / "src" / "leakage_simulator" / "wavefront_event_tape.py",
        ROOT / "src" / "leakage_simulator" / "reflection.py",
        ROOT / "src" / "leakage_simulator" / "types.py",
    )


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
        for path in _source_files()
        if path.exists()
    }


def _scene_signature(trace_input: DirectRayTraceInput) -> str:
    payload = {
        "vertices": trace_input.mesh.vertices,
        "faces": [
            (face.v0, face.v1, face.v2)
            for face in trace_input.mesh.faces
        ],
        "face_material": sorted(trace_input.mesh.face_material.items()),
        "emitters": [emitter.to_dict() for emitter in trace_input.emitters],
        "receivers": [receiver.to_dict() for receiver in trace_input.receivers],
        "optical_profiles": [
            profile.to_dict() for profile in trace_input.optical_profiles
        ],
        "config": {
            "max_depth": trace_input.config.max_depth,
            "min_energy": trace_input.config.min_energy,
            "epsilon_mm": trace_input.config.epsilon_mm,
            "termination_mode": trace_input.config.termination_mode,
            "contribution_mode": trace_input.config.contribution_mode,
            "intersection_backend": trace_input.config.intersection_backend,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
        "primary_rays_per_sec_p50": ray_count / p50,
    }


def _metric(performance: dict, name: str, default=0):
    value = performance.get(name, default)
    return default if value is None else value


def _active_depth_counts(performance: dict) -> dict[str, int]:
    return {
        str(depth): int(count)
        for depth, count in _metric(
            performance,
            "wavefront_active_ray_count_by_depth",
            {},
        ).items()
    }


def _execution_evidence(result) -> dict:
    performance = result.metrics["_performance_summary"]
    active_by_depth = _active_depth_counts(performance)
    logical_intersections = int(
        _metric(performance, "intersection_ray_count", 0)
    )
    total_rays = max(1, int(result.total_rays))
    return {
        "compute_backend": performance["compute_backend"],
        "compute_execution_state": performance["compute_execution_state"],
        "compute_execution_reason": performance["compute_execution_reason"],
        "monte_carlo_contract": performance["monte_carlo_contract"],
        "intersection_provider": performance["intersection_provider"],
        "intersection_batch_count": int(
            _metric(performance, "intersection_batch_count", 0)
        ),
        "logical_intersection_rows": logical_intersections,
        "logical_intersections_per_primary": logical_intersections / total_rays,
        "active_ray_count_by_depth": active_by_depth,
        "max_observed_depth": int(
            _metric(performance, "wavefront_max_observed_depth", 0)
        ),
        "surface_hit_count": int(result.surface_hit_count),
        "receiver_hit_count": int(result.receiver_hit_count),
        "receiver_hit_rate": result.receiver_hit_count / total_rays,
        "terminated_ray_count": int(result.terminated_ray_count),
        "gpu_cuda_used": bool(_metric(performance, "gpu_cuda_used", False)),
        "gpu_cuda_gpu_success_count": int(
            _metric(performance, "gpu_cuda_gpu_success_count", 0)
        ),
        "gpu_cuda_gpu_success_ray_count": int(
            _metric(performance, "gpu_cuda_gpu_success_ray_count", 0)
        ),
        "gpu_cuda_hybrid_cpu_success_count": int(
            _metric(performance, "gpu_cuda_hybrid_cpu_success_count", 0)
        ),
        "intersection_fallback_count": int(
            _metric(performance, "intersection_fallback_count", 0)
        ),
        "gpu_cuda_scene_upload_sec": float(
            _metric(performance, "gpu_cuda_scene_upload_sec", 0.0)
        ),
        "gpu_cuda_input_upload_sec": float(
            _metric(performance, "gpu_cuda_input_upload_sec", 0.0)
        ),
        "gpu_cuda_kernel_sec": float(
            _metric(performance, "gpu_cuda_kernel_sec", 0.0)
        ),
        "gpu_cuda_output_download_sec": float(
            _metric(performance, "gpu_cuda_output_download_sec", 0.0)
        ),
        "receiver_sec": float(
            _metric(performance, "wavefront_receiver_sec", 0.0)
        ),
        "reflection_plan_sec": float(
            _metric(performance, "wavefront_plan_sec", 0.0)
        ),
        "ordered_commit_sec": float(
            _metric(performance, "wavefront_commit_sec", 0.0)
        ),
        "wavefront_total_sec": float(
            _metric(performance, "wavefront_total_sec", 0.0)
        ),
        "event_tape_copy_bytes": int(
            _metric(performance, "wavefront_event_tape_copy_bytes", 0)
        ),
        "event_tape_peak_bytes": int(
            _metric(performance, "wavefront_event_tape_peak_bytes", 0)
        ),
        "wavefront_residency": _metric(
            performance,
            "wavefront_residency",
            "host_roundtrip",
        ),
    }


def _validate_gpu_evidence(evidence: dict) -> None:
    if evidence["compute_execution_state"] not in {"gpu_active", "gpu_mixed"}:
        raise RuntimeError(
            "GPU workload did not commit CUDA work: "
            f"{evidence['compute_execution_state']} / "
            f"{evidence['compute_execution_reason']}"
        )
    if evidence["gpu_cuda_gpu_success_count"] <= 0:
        raise RuntimeError("GPU workload recorded no successful CUDA batch")
    if evidence["intersection_fallback_count"] != 0:
        raise RuntimeError("GPU workload recorded an unexpected hard fallback")
    if evidence["monte_carlo_contract"] != MONTE_CARLO_CONTRACT:
        raise RuntimeError("GPU workload did not use the deterministic parity contract")


def _run(
    builder: WorkloadBuilder,
    ray_count: int,
    chunk_size: int,
    backend: str,
):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = backend
    trace_input.config.contribution_mode = "summary"
    started = time.perf_counter()
    result = run_direct_ray_trace(
        trace_input,
        intersection_batch_size=chunk_size,
        wavefront_residency="host_roundtrip",
    )
    return result, time.perf_counter() - started


def _projection(
    p50_sec: float,
    measured_rays: int,
    target_rays: int,
    target_seconds: tuple[int, ...],
) -> dict:
    throughput = measured_rays / p50_sec
    projected_seconds = target_rays / throughput
    return {
        "target_primary_rays": target_rays,
        "linear_projected_sec": projected_seconds,
        "linear_projected_min": projected_seconds / 60.0,
        "target_gates": {
            str(seconds): {
                "target_sec": seconds,
                "required_primary_rays_per_sec": target_rays / seconds,
                "required_speedup": projected_seconds / seconds,
                "currently_meets": projected_seconds <= seconds,
            }
            for seconds in target_seconds
        },
        "interpretation": (
            "Linear projection only; receiver convergence and CAD-dependent "
            "BVH traversal are evaluated separately."
        ),
    }


def benchmark_workload(
    name: str,
    builder: WorkloadBuilder,
    ray_count: int,
    repeats: int,
    chunk_size: int,
    backend: str,
    target_rays: int,
) -> dict:
    scene = builder(ray_count)
    scene_signature = _scene_signature(scene)
    cold_result, cold_sec = _run(builder, ray_count, chunk_size, backend)
    warm_seconds: list[float] = []
    representative = cold_result
    for _ in range(repeats):
        representative, elapsed = _run(builder, ray_count, chunk_size, backend)
        warm_seconds.append(elapsed)
    evidence = _execution_evidence(representative)
    if backend == "gpu_cuda":
        _validate_gpu_evidence(evidence)
    distribution = _distribution(warm_seconds, ray_count)
    return {
        "name": name,
        "scene_sha256": scene_signature,
        "triangle_count": len(scene.mesh.faces),
        "receiver_count": len(scene.receivers),
        "emitter_count": len(scene.emitters),
        "configured_max_reflections": scene.config.max_depth,
        "primary_ray_count": ray_count,
        "chunk_size": chunk_size,
        "cold_sec": cold_sec,
        "warm": distribution,
        "projection": _projection(
            distribution["p50_sec"],
            ray_count,
            target_rays,
            DEFAULT_TARGET_SECONDS,
        ),
        "evidence": evidence,
    }


def benchmark(
    *,
    workload_names: tuple[str, ...],
    ray_count: int,
    repeats: int,
    chunk_size: int,
    backend: str,
    target_rays: int = DEFAULT_TARGET_PRIMARY_RAYS,
) -> dict:
    unknown = sorted(set(workload_names) - set(WORKLOADS))
    if unknown:
        raise ValueError(f"unknown workload(s): {', '.join(unknown)}")
    source_start = _source_hashes()
    preflight_payload = None
    if backend == "gpu_cuda":
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

    cases = [
        benchmark_workload(
            name,
            WORKLOADS[name],
            ray_count,
            repeats,
            chunk_size,
            backend,
            target_rays,
        )
        for name in workload_names
    ]
    source_end = _source_hashes()
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "backend": backend,
        "ray_count_per_workload": ray_count,
        "repeats": repeats,
        "chunk_size": chunk_size,
        "target_primary_rays": target_rays,
        "workload_order": list(workload_names),
        "measurement_policy": "one_cold_then_warm_repeats_v1",
        "source_hash_start": source_start,
        "source_hash_end": source_end,
        "source_changed_during_measurement": source_start != source_end,
        "gpu_preflight": preflight_payload,
        "cases": cases,
        "passed": (
            source_start == source_end
            and all(
                case["evidence"]["intersection_fallback_count"] == 0
                for case in cases
            )
        ),
        "limitations": [
            "Synthetic controls do not replace anonymized in-house TV ROI scenes.",
            "100M projections are linear and do not guarantee a 5% receiver error.",
            "Cold and warm results must be compared separately in one process.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the fixed PERF-4A GPU/CPU target workload contract."
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
    parser.add_argument(
        "--backend",
        choices=("cpu", "gpu_cuda"),
        default="gpu_cuda",
    )
    parser.add_argument(
        "--target-rays",
        type=int,
        default=DEFAULT_TARGET_PRIMARY_RAYS,
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if min(args.rays, args.repeats, args.chunk_size, args.target_rays) <= 0:
        parser.error("rays, repeats, chunk-size and target-rays must be positive")
    if args.backend == "gpu_cuda" and args.rays < 8192:
        parser.error("GPU evidence requires at least 8192 rays")

    summary = benchmark(
        workload_names=tuple(args.workloads),
        ray_count=args.rays,
        repeats=args.repeats,
        chunk_size=args.chunk_size,
        backend=args.backend,
        target_rays=args.target_rays,
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
