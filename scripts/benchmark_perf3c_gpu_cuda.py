from __future__ import annotations

import argparse
from contextlib import redirect_stdout
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
from benchmark_perf3b_batch_backend import build_probe_rays
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.geometry import RayBatch
from leakage_simulator.importers import import_geometry
from leakage_simulator.raytracer import run_direct_ray_trace


CONTRACT = "perf3c_gpu_cuda_benchmark_v1"
DEFAULT_CAD_PATH = ROOT / "samples" / "tv_leakage_roi_right_bottom_no_gap.stp"
OUTPUT_DIR = ROOT / "outputs" / "perf3c_gpu_cuda"
SEED = 20260717
ABS_TOLERANCE = 1e-12
REL_TOLERANCE = 1e-12


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_files() -> tuple[Path, ...]:
    return (
        ROOT / "src" / "leakage_simulator" / "gpu_cuda_intersection.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_counter_wavefront.py",
        ROOT / "src" / "leakage_simulator" / "geometry.py",
        ROOT / "src" / "leakage_simulator" / "raytracer.py",
        ROOT / "src" / "leakage_simulator" / "types.py",
        Path(__file__).resolve(),
    )


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): _file_sha256(path)
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


def _capability_payload(capability: gpu_cuda.GpuCudaCapability) -> dict:
    return {
        "available": capability.available,
        "reason_code": capability.reason_code,
        "numba_version": capability.numba_version,
        "device_name": capability.device_name,
        "compute_capability": capability.compute_capability,
        "device_id": capability.device_id,
        "strict_float64": capability.strict_float64,
        "toolkit_layout": capability.toolkit_layout,
    }


def _chunked_intersection(mesh, rays: RayBatch, provider: str, batch_size: int):
    distances = np.full(len(rays), math.inf, dtype=np.float64)
    face_indices = np.full(len(rays), -1, dtype=np.int64)
    executions = []
    for start in range(0, len(rays), batch_size):
        stop = min(len(rays), start + batch_size)
        chunk = RayBatch(
            rays.origins[start:stop],
            rays.directions[start:stop],
            rays.min_t[start:stop],
            rays.max_t[start:stop],
            rays.ignore_faces[start:stop],
        )
        if provider == "gpu_cuda":
            hits, execution = mesh.intersect_rays_gpu_cuda(chunk, backend="bvh")
        elif provider == "numba_cpu":
            hits, execution = mesh.intersect_rays_native_cpu(chunk, backend="bvh")
        else:
            hits = mesh.intersect_rays(chunk, backend="bvh")
            execution = None
        distances[start:stop] = hits.t
        face_indices[start:stop] = hits.face_indices
        if execution is not None:
            executions.append(execution)
    return distances, face_indices, executions


def _measure(operation, repeats: int):
    durations: list[float] = []
    latest = None
    for _ in range(repeats):
        started = time.perf_counter()
        latest = operation()
        durations.append(time.perf_counter() - started)
    return durations, latest


def geometry_benchmark(
    path: Path,
    ray_count: int,
    repeats: int,
    batch_sizes: list[int],
) -> dict:
    import_started = time.perf_counter()
    # Keep stdout machine-readable; the importer progress stream remains visible
    # on stderr for long CAD loads.
    with redirect_stdout(sys.stderr):
        imported = import_geometry(str(path))
    import_sec = time.perf_counter() - import_started
    mesh = imported.mesh
    origins, directions = build_probe_rays(mesh, ray_count, SEED)
    rays = RayBatch(origins, directions)
    acceleration = mesh.prepare_acceleration()
    capability = gpu_cuda.probe_gpu_cuda()
    result = {
        "path": str(path),
        "synthetic": imported.synthetic,
        "import_note": imported.note,
        "import_sec": import_sec,
        "triangle_count": len(mesh.faces),
        "bvh_node_count": acceleration["bvh_node_count"],
        "bvh_leaf_count": acceleration["bvh_leaf_count"],
        "ray_count": ray_count,
        "repeats": repeats,
        "batch_sizes": batch_sizes,
        "capability": _capability_payload(capability),
        "precision_contract": {
            "face_indices": "exact",
            "distances": {
                "absolute_tolerance": ABS_TOLERANCE,
                "relative_tolerance": REL_TOLERANCE,
            },
        },
        "cases": [],
    }
    if not capability.available:
        result["status"] = "gpu_unavailable"
        return result

    cold_size = min(ray_count, batch_sizes[0])
    cold_rays = RayBatch(
        rays.origins[:cold_size],
        rays.directions[:cold_size],
        rays.min_t[:cold_size],
        rays.max_t[:cold_size],
        rays.ignore_faces[:cold_size],
    )
    cold_started = time.perf_counter()
    _, cold_execution = mesh.intersect_rays_gpu_cuda(cold_rays, backend="bvh")
    cold_wall_sec = time.perf_counter() - cold_started
    result["cold"] = {
        "ray_count": cold_size,
        "wall_sec": cold_wall_sec,
        "scene_build_sec": cold_execution.scene_build_sec,
        "scene_upload_sec": cold_execution.scene_upload_sec,
        "workspace_prepare_sec": cold_execution.workspace_prepare_sec,
        "input_upload_sec": cold_execution.input_upload_sec,
        "jit_compile_sec": cold_execution.jit_compile_sec,
        "kernel_sec": cold_execution.kernel_sec,
        "output_download_sec": cold_execution.output_download_sec,
    }

    reference_distances, reference_faces, _ = _chunked_intersection(
        mesh,
        rays,
        "numba_cpu",
        batch_sizes[0],
    )
    for batch_size in batch_sizes:
        durations, measured = _measure(
            lambda size=batch_size: _chunked_intersection(
                mesh,
                rays,
                "gpu_cuda",
                size,
            ),
            repeats,
        )
        assert measured is not None
        distances, faces, executions = measured
        hit_mask = reference_faces >= 0
        absolute_error = np.abs(distances[hit_mask] - reference_distances[hit_mask])
        scale = np.maximum(np.abs(reference_distances[hit_mask]), 1.0)
        allowed = ABS_TOLERANCE + REL_TOLERANCE * scale
        case = {
            "provider": "gpu_cuda",
            "batch_size": batch_size,
            "batch_count": len(executions),
            **_distribution(durations, ray_count),
            "face_mismatch_count": int(np.count_nonzero(faces != reference_faces)),
            "distance_tolerance_mismatch_count": int(
                np.count_nonzero(absolute_error > allowed)
            ),
            "maximum_absolute_distance_error": (
                float(np.max(absolute_error)) if absolute_error.size else 0.0
            ),
            "scene_reuse_count": sum(
                int(item.reused_device_scene) for item in executions
            ),
            "workspace_reuse_count": sum(
                int(item.reused_workspace) for item in executions
            ),
            "timing_totals_sec": {
                "scene_upload": sum(item.scene_upload_sec for item in executions),
                "workspace_prepare": sum(
                    item.workspace_prepare_sec for item in executions
                ),
                "input_upload": sum(item.input_upload_sec for item in executions),
                "jit_compile": sum(item.jit_compile_sec for item in executions),
                "kernel": sum(item.kernel_sec for item in executions),
                "output_download": sum(
                    item.output_download_sec for item in executions
                ),
            },
        }
        result["cases"].append(case)

    cpu_durations, _ = _measure(
        lambda: _chunked_intersection(mesh, rays, "numba_cpu", batch_sizes[0]),
        repeats,
    )
    cpu_summary = {
        "provider": "numba_cpu",
        "batch_size": batch_sizes[0],
        **_distribution(cpu_durations, ray_count),
    }
    result["reference"] = cpu_summary
    for case in result["cases"]:
        case["speedup_vs_numba_cpu"] = (
            cpu_summary["p50_sec"] / case["p50_sec"]
        )
    result["status"] = "measured"
    return result


def _normalize_semantic(result) -> tuple[dict, list]:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    paths = payload.pop("stored_paths")
    return payload, paths


def _path_tolerance_mismatches(actual: list, expected: list) -> int:
    if len(actual) != len(expected):
        return abs(len(actual) - len(expected)) + 1
    mismatches = 0
    for actual_path, expected_path in zip(actual, expected):
        if len(actual_path) != len(expected_path):
            mismatches += abs(len(actual_path) - len(expected_path)) + 1
            continue
        for actual_event, expected_event in zip(actual_path, expected_path):
            for key in actual_event:
                if key in {"point", "normal"}:
                    if not np.allclose(
                        actual_event[key],
                        expected_event[key],
                        atol=ABS_TOLERANCE,
                        rtol=REL_TOLERANCE,
                    ):
                        mismatches += 1
                elif key == "distance_mm":
                    if not math.isclose(
                        actual_event[key],
                        expected_event[key],
                        abs_tol=ABS_TOLERANCE,
                        rel_tol=REL_TOLERANCE,
                    ):
                        mismatches += 1
                elif actual_event[key] != expected_event[key]:
                    mismatches += 1
    return mismatches


def _e2e_run(ray_count: int, provider: str):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.compute_backend = (
        "gpu_cuda" if provider == "gpu_cuda" else "cpu"
    )
    return run_direct_ray_trace(
        trace_input,
        intersection_dispatch="batch",
        intersection_batch_size=65536,
        intersection_provider=provider,
        wavefront_planner="numba_cpu",
        wavefront_pipeline="soa_event_tape",
        wavefront_reducer="numba_cpu",
        wavefront_rng="counter_rng_v2",
    )


def end_to_end_benchmark(ray_count: int, repeats: int, gpu_available: bool) -> dict:
    result = {
        "ray_count": ray_count,
        "repeats": repeats,
        "batch_size": 65536,
        "stack": "SoA + counter_rng_v2 Numba planner + Numba reducer",
        "batch_policy": {
            "gpu_primary_chunk_rays": 65536,
            "hybrid_numba_cpu_below_rays": 8192,
            "hybrid_comparison": "strictly_less_than",
            "memory_scaling": "O(primary_chunk_rays + active_event_rows)",
            "stop_atomicity": (
                "A started primary chunk and its current intersection chunk are "
                "published atomically; Stop is observed at the next boundary."
            ),
            "tradeoff": (
                "65536 reduces GPU launch/transfer overhead but raises transient "
                "host/device workspace and coarsens worst-case Stop latency."
            ),
        },
        "cases": [],
    }
    providers = ["numba_cpu"] + (["gpu_cuda"] if gpu_available else [])
    representative = {}
    for provider in providers:
        durations, latest = _measure(
            lambda selected=provider: _e2e_run(ray_count, selected),
            repeats,
        )
        assert latest is not None
        representative[provider] = latest
        performance = latest.metrics["_performance_summary"]
        case = {
            "requested_provider": provider,
            "effective_provider": performance["intersection_provider"],
            "gpu_used": performance["gpu_cuda_used"],
            "fallback_count": performance["intersection_fallback_count"],
            "fallback_ray_count": performance["intersection_fallback_ray_count"],
            "receiver_hit_count": latest.receiver_hit_count,
            "surface_hit_count": latest.surface_hit_count,
            "terminated_ray_count": latest.terminated_ray_count,
            "intersection_ray_count": performance["intersection_ray_count"],
            "wavefront_rng": performance["wavefront_reflection_rng"],
            "wavefront_counter_apply_dispatch": performance.get(
                "wavefront_counter_apply_dispatch"
            ),
            "planner_attempt_count": performance[
                "wavefront_planner_native_attempt_count"
            ],
            "planner_success_count": performance[
                "wavefront_planner_native_success_count"
            ],
            "planner_fallback_count": performance[
                "wavefront_planner_fallback_count"
            ],
            "reducer_attempt_count": performance[
                "wavefront_reducer_native_attempt_count"
            ],
            "reducer_success_count": performance[
                "wavefront_reducer_native_success_count"
            ],
            "reducer_fallback_count": performance[
                "wavefront_reducer_fallback_count"
            ],
            **_distribution(durations, ray_count),
        }
        if provider == "gpu_cuda":
            case["gpu_cuda"] = {
                "execution_policy": performance["gpu_cuda_execution_policy"],
                "hybrid_cpu_below_rays": performance[
                    "gpu_cuda_hybrid_cpu_below_rays"
                ],
                "available": performance["gpu_cuda_available"],
                "device_name": performance["gpu_cuda_device_name"],
                "compute_capability": performance[
                    "gpu_cuda_compute_capability"
                ],
                "device_id": performance["gpu_cuda_device_id"],
                "contract": performance["gpu_cuda_contract"],
                "strict_float64": performance["gpu_cuda_strict_float64"],
                "gpu_attempt_count": performance["gpu_cuda_gpu_attempt_count"],
                "gpu_attempt_ray_count": performance[
                    "gpu_cuda_gpu_attempt_ray_count"
                ],
                "gpu_success_count": performance["gpu_cuda_gpu_success_count"],
                "gpu_success_ray_count": performance[
                    "gpu_cuda_gpu_success_ray_count"
                ],
                "hybrid_attempt_count": performance[
                    "gpu_cuda_hybrid_cpu_attempt_count"
                ],
                "hybrid_attempt_ray_count": performance[
                    "gpu_cuda_hybrid_cpu_attempt_ray_count"
                ],
                "hybrid_success_count": performance[
                    "gpu_cuda_hybrid_cpu_success_count"
                ],
                "hybrid_success_ray_count": performance[
                    "gpu_cuda_hybrid_cpu_success_ray_count"
                ],
                "hybrid_failure_count": performance[
                    "gpu_cuda_hybrid_cpu_failure_count"
                ],
                "hybrid_disabled": performance[
                    "gpu_cuda_hybrid_cpu_disabled"
                ],
                "scene_upload_sec": performance["gpu_cuda_scene_upload_sec"],
                "workspace_prepare_sec": performance[
                    "gpu_cuda_workspace_prepare_sec"
                ],
                "input_upload_sec": performance["gpu_cuda_input_upload_sec"],
                "kernel_sec": performance["gpu_cuda_kernel_sec"],
                "output_download_sec": performance[
                    "gpu_cuda_output_download_sec"
                ],
            }
        result["cases"].append(case)

    if gpu_available:
        cpu_payload, cpu_paths = _normalize_semantic(representative["numba_cpu"])
        gpu_payload, gpu_paths = _normalize_semantic(representative["gpu_cuda"])
        cpu_p50 = result["cases"][0]["p50_sec"]
        gpu_p50 = result["cases"][1]["p50_sec"]
        result["correctness"] = {
            "count_grid_summary_exact": gpu_payload == cpu_payload,
            "path_tolerance_mismatch_count": _path_tolerance_mismatches(
                gpu_paths,
                cpu_paths,
            ),
            "absolute_tolerance": ABS_TOLERANCE,
            "relative_tolerance": REL_TOLERANCE,
        }
        result["gpu_speedup_vs_numba_cpu"] = cpu_p50 / gpu_p50
    return result


def benchmark(
    path: Path,
    ray_count: int,
    e2e_ray_count: int,
    repeats: int,
    batch_sizes: list[int],
) -> dict:
    source_start = _source_hashes()
    geometry = geometry_benchmark(path, ray_count, repeats, batch_sizes)
    gpu_available = bool(geometry["capability"]["available"])
    end_to_end = end_to_end_benchmark(e2e_ray_count, repeats, gpu_available)
    source_end = _source_hashes()
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "seed": SEED,
        "source_hash_start": source_start,
        "source_hash_end": source_end,
        "source_changed_during_measurement": source_start != source_end,
        "precision_policy": {
            "face_counts_grids_summaries": "exact",
            "distance_and_stored_path_absolute_tolerance": ABS_TOLERANCE,
            "distance_and_stored_path_relative_tolerance": REL_TOLERANCE,
            "strict_float64": True,
            "fastmath": False,
        },
        "promotion_policy": (
            "No automatic promotion from this artifact alone; require actual ROI "
            "warm/cold performance, zero hard fallback, and correctness gates."
        ),
        "geometry": geometry,
        "end_to_end": end_to_end,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the explicit PERF-3C strict-float64 CUDA backend."
    )
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_CAD_PATH)
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--e2e-rays", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--batch-sizes",
        nargs="+",
        type=int,
        default=[8192, 16384, 65536],
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if (
        args.rays <= 0
        or args.e2e_rays <= 0
        or args.repeats <= 0
        or not args.batch_sizes
        or any(value <= 0 for value in args.batch_sizes)
    ):
        raise SystemExit("ray counts, repeats and batch sizes must be positive")
    if not args.path.exists():
        raise SystemExit(f"CAD benchmark path was not found: {args.path}")
    batch_sizes = list(dict.fromkeys(min(args.rays, value) for value in args.batch_sizes))
    summary = benchmark(
        args.path,
        args.rays,
        args.e2e_rays,
        args.repeats,
        batch_sizes,
    )
    encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2)
    print(encoded)
    if not args.no_write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = OUTPUT_DIR / "summary.json"
        summary_path.write_text(f"{encoded}\n", encoding="utf-8")
        print(f"summary={summary_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
