from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.geometry import RayBatch, TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


OUTPUT_DIR = ROOT / "outputs" / "perf3b2a_multibounce"


def _add_diagonal_mirror(
    mesh: TriangleMesh,
    center: tuple[float, float, float],
    material_id: str,
    component_id: int,
    half_extent: float = 4.0,
) -> None:
    inverse_root_two = 1.0 / math.sqrt(2.0)
    tangent = (inverse_root_two, 0.0, inverse_root_two)
    vertical = (0.0, 1.0, 0.0)
    points = [
        (
            center[0] + tangent[0] * tangent_scale,
            center[1] + vertical[1] * vertical_scale,
            center[2] + tangent[2] * tangent_scale,
        )
        for tangent_scale, vertical_scale in (
            (-half_extent, -half_extent),
            (half_extent, -half_extent),
            (half_extent, half_extent),
            (-half_extent, half_extent),
        )
    ]
    vertices = [mesh.add_vertex(point) for point in points]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id, metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id, metadata)


def build_two_bounce_case(ray_count: int) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    _add_diagonal_mirror(mesh, (0.0, 0.0, 10.0), "mirror_a", 101)
    _add_diagonal_mirror(mesh, (10.0, 0.0, 10.0), "mirror_b", 202)
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="source",
                emitter_type="datum_plane",
                center=(0.0, 0.0, 0.0),
                u_axis=(1.0, 0.0, 0.0),
                v_axis=(0.0, 1.0, 0.0),
                width_mm=0.02,
                height_mm=0.02,
                direction_distribution="gaussian",
                gaussian_sigma_deg=0.001,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=20260721,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="observer",
                center=(10.0, 0.0, 20.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=4.0,
                height_mm=4.0,
                resolution=(12, 12),
            )
        ],
        optical_profiles=[
            OpticalProfile("mirror_a", 0.8, scatter_model="specular"),
            OpticalProfile("mirror_b", 0.5, scatter_model="specular"),
        ],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=2,
            seed=31,
            min_energy=1e-9,
            contribution_mode="summary",
            intersection_backend="bvh",
            store_ray_paths=False,
        ),
    )


def _add_corridor_wall(
    mesh: TriangleMesh,
    y_position: float,
    material_id: str,
    component_id: int,
) -> None:
    if y_position > 0.0:
        points = [
            (0.0, y_position, -2.0),
            (21.0, y_position, -2.0),
            (21.0, y_position, 2.0),
            (0.0, y_position, 2.0),
        ]
    else:
        points = [
            (0.0, y_position, -2.0),
            (0.0, y_position, 2.0),
            (21.0, y_position, 2.0),
            (21.0, y_position, -2.0),
        ]
    vertices = [mesh.add_vertex(point) for point in points]
    metadata = {"component_id": component_id}
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id, metadata)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id, metadata)


def build_depth_ten_case(ray_count: int) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    _add_corridor_wall(mesh, 1.0, "high_reflector", 301)
    _add_corridor_wall(mesh, -1.0, "high_reflector", 302)
    inverse_root_two = 1.0 / math.sqrt(2.0)
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="corridor_source",
                emitter_type="datum_plane",
                center=(0.0, 0.0, 0.0),
                u_axis=(0.0, 0.0, 1.0),
                v_axis=(inverse_root_two, -inverse_root_two, 0.0),
                width_mm=0.001,
                height_mm=0.001,
                direction_distribution="gaussian",
                gaussian_sigma_deg=0.001,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=20260727,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="corridor_observer",
                center=(20.0, 0.0, 0.0),
                normal=(-1.0, 0.0, 0.0),
                width_mm=1.5,
                height_mm=1.5,
                resolution=(10, 10),
            )
        ],
        optical_profiles=[
            OpticalProfile(
                "high_reflector",
                0.95,
                scatter_model="specular",
            )
        ],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=10,
            seed=73,
            min_energy=1e-12,
            contribution_mode="summary",
            intersection_backend="bvh",
            store_ray_paths=False,
        ),
    )


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _percentile_95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _warm_native_provider(trace_input: DirectRayTraceInput) -> None:
    trace_input.mesh.prepare_acceleration()
    rays = RayBatch(
        np.asarray([[0.0, 0.0, 0.0]], dtype=np.float64),
        np.asarray([[0.0, 0.0, 1.0]], dtype=np.float64),
    )
    trace_input.mesh.intersect_rays_native_cpu(rays, backend="bvh")


def _run_case(
    trace_input: DirectRayTraceInput,
    ray_count: int,
    repeats: int,
    name: str,
    dispatch: str,
    provider: str,
    batch_size: int,
) -> tuple[dict, dict]:
    durations: list[float] = []
    payload = None
    representative = None
    timing_keys = (
        "intersection_sec",
        "wavefront_state_build_sec",
        "wavefront_receiver_sec",
        "wavefront_plan_sec",
        "wavefront_commit_sec",
        "wavefront_total_sec",
    )
    timing_values = {key: [] for key in timing_keys}
    for _ in range(repeats):
        started = time.perf_counter()
        result = run_direct_ray_trace(
            trace_input,
            intersection_dispatch=dispatch,
            intersection_batch_size=batch_size,
            intersection_provider=provider,
        )
        durations.append(time.perf_counter() - started)
        current_payload = _semantic_payload(result)
        if payload is None:
            payload = current_payload
            representative = result
        elif current_payload != payload:
            raise RuntimeError(f"{name} was not deterministic across repeats")
        performance = result.metrics["_performance_summary"]
        for key in timing_keys:
            timing_values[key].append(float(performance[key]))

    assert payload is not None and representative is not None
    performance = representative.metrics["_performance_summary"]
    median_sec = statistics.median(durations)
    summary = {
        "name": name,
        "dispatch": dispatch,
        "requested_provider": provider,
        "effective_provider": performance["intersection_provider"],
        "batch_size": batch_size if dispatch == "batch" else None,
        "durations_sec": durations,
        "median_sec": median_sec,
        "p95_sec": _percentile_95(durations),
        "primary_rays_per_sec": ray_count / median_sec,
        "receiver_hit_count": representative.receiver_hit_count,
        "surface_hit_count": representative.surface_hit_count,
        "terminated_ray_count": representative.terminated_ray_count,
        "intersection_ray_count": performance["intersection_ray_count"],
        "intersection_batch_count": performance["intersection_batch_count"],
        "native_used": performance["native_used"],
        "timing_medians_sec": {
            key: statistics.median(values)
            for key, values in timing_values.items()
        },
    }
    return summary, payload


def benchmark_case(
    name: str,
    builder: Callable[[int], DirectRayTraceInput],
    ray_count: int,
    repeats: int,
    batch_sizes: list[int],
) -> dict:
    trace_input = builder(ray_count)
    trace_input.mesh.prepare_acceleration()
    _warm_native_provider(trace_input)
    case_specs = [
        ("python_scalar", "scalar", "python_cpu", batch_sizes[0]),
        ("numba_scalar", "scalar", "numba_cpu", batch_sizes[0]),
        ("python_wavefront", "batch", "python_cpu", batch_sizes[0]),
        *[
            (f"numba_wavefront_{batch_size}", "batch", "numba_cpu", batch_size)
            for batch_size in batch_sizes
        ],
    ]
    summaries = []
    baseline_payload = None
    semantic_mismatch_count = 0
    for case_name, dispatch, provider, batch_size in case_specs:
        summary, payload = _run_case(
            trace_input,
            ray_count,
            repeats,
            case_name,
            dispatch,
            provider,
            batch_size,
        )
        if baseline_payload is None:
            baseline_payload = payload
        elif payload != baseline_payload:
            semantic_mismatch_count += 1
        summaries.append(summary)

    baseline_rate = summaries[0]["primary_rays_per_sec"]
    for summary in summaries:
        summary["speedup_vs_python_scalar"] = (
            summary["primary_rays_per_sec"] / baseline_rate
        )
    return {
        "name": name,
        "ray_count": ray_count,
        "repeats": repeats,
        "cases": summaries,
        "semantic_mismatch_count": semantic_mismatch_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark PERF-3B-2A deterministic multi-bounce wavefront tracing."
    )
    parser.add_argument("--rays", type=int, default=50_000)
    parser.add_argument("--depth-ten-rays", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--batch-sizes",
        type=int,
        nargs="+",
        default=[512, 1024, 4096],
    )
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    if args.rays <= 0 or args.depth_ten_rays <= 0 or args.repeats <= 0:
        raise SystemExit("ray counts and repeats must be positive")
    if not args.batch_sizes or any(size <= 0 for size in args.batch_sizes):
        raise SystemExit("batch sizes must be positive")

    summary = {
        "contract": "perf3b2a_multibounce_wavefront_v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sampler_batch_size": 65_536,
        "native_cold_start_excluded": True,
        "cases": [
            benchmark_case(
                "two_bounce_specular",
                build_two_bounce_case,
                args.rays,
                args.repeats,
                args.batch_sizes,
            ),
            benchmark_case(
                "depth_ten_specular_corridor",
                build_depth_ten_case,
                args.depth_ten_rays,
                args.repeats,
                args.batch_sizes,
            ),
        ],
    }
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
