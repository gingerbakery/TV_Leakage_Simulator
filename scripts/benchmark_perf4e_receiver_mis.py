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

from perf4_accuracy import compare_semantic_payloads
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, RayTraceConfig, ReceiverSpec


CONTRACT = "perf4e_receiver_directed_primary_mis_benchmark_v1"


def _build_case(
    ray_count: int,
    seed: int,
    strategy: str,
    backend: str,
    *,
    max_depth: int = 0,
) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    vertices = [
        mesh.add_vertex(point)
        for point in (
            (-0.5, -0.5, 0.0),
            (0.5, -0.5, 0.0),
            (0.5, 0.5, 0.0),
            (-0.5, 0.5, 0.0),
        )
    ]
    faces = [
        mesh.add_face(vertices[0], vertices[1], vertices[2], "source"),
        mesh.add_face(vertices[0], vertices[2], vertices[3], "source"),
    ]
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="source",
                emitter_type="face",
                face_indices=faces,
                direction_distribution="lambertian",
                power_lumen=1.0,
                ray_count=ray_count,
                seed=seed,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="observer",
                center=(0.0, 0.0, 100.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=4.0,
                height_mm=4.0,
                resolution=(8, 8),
            )
        ],
        optical_profiles=[],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=max_depth,
            seed=seed,
            min_energy=1e-12,
            contribution_mode="summary",
            intersection_backend="bvh",
            compute_backend=backend,
            store_ray_paths=False,
            primary_sampling_strategy=strategy,
            receiver_importance_fraction=0.5,
        ),
    )


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    return payload


def _sample_distribution(
    ray_count: int,
    repeats: int,
    strategy: str,
) -> dict:
    fluxes = []
    hit_counts = []
    elapsed = []
    effective_sample_ratios = []
    for repeat in range(repeats):
        started = time.perf_counter()
        result = run_direct_ray_trace(
            _build_case(
                ray_count,
                10_000 + repeat,
                strategy,
                "cpu",
            )
        )
        elapsed.append(time.perf_counter() - started)
        fluxes.append(result.metrics["observer"]["total_flux_lumen"])
        hit_counts.append(result.receiver_hit_count)
        performance = result.metrics["_performance_summary"]
        effective_sample_ratios.append(
            performance["primary_sampling_effective_sample_ratio"]
        )
    mean_flux = statistics.mean(fluxes)
    standard_deviation = statistics.pstdev(fluxes)
    relative_standard_error = (
        standard_deviation / abs(mean_flux) if mean_flux else float("inf")
    )
    projected_rays_for_five_percent = (
        ray_count * (relative_standard_error / 0.05) ** 2
        if relative_standard_error != float("inf")
        else None
    )
    return {
        "flux_samples_lumen": fluxes,
        "mean_flux_lumen": mean_flux,
        "standard_deviation_lumen": standard_deviation,
        "relative_standard_error": relative_standard_error,
        "mean_receiver_hits": statistics.mean(hit_counts),
        "median_runtime_sec": statistics.median(elapsed),
        "mean_effective_sample_ratio": statistics.mean(
            effective_sample_ratios
        ),
        "projected_rays_for_5_percent": projected_rays_for_five_percent,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rays", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / "perf4e_receiver_mis" / "benchmark.json",
    )
    args = parser.parse_args()
    source = _sample_distribution(args.rays, args.repeats, "source")
    receiver_mis = _sample_distribution(args.rays, args.repeats, "receiver_mis")
    variance_reduction = (
        (source["standard_deviation_lumen"] / receiver_mis["standard_deviation_lumen"])
        ** 2
        if receiver_mis["standard_deviation_lumen"] > 0.0
        else float("inf")
    )

    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    gpu_parity = None
    if (
        preflight.available
        and preflight.strict_float64
        and preflight.kernel_executed
        and preflight.kernel_verified
        and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
    ):
        cpu = run_direct_ray_trace(
            _build_case(8192, 20260825, "receiver_mis", "cpu", max_depth=2)
        )
        gpu = run_direct_ray_trace(
            _build_case(
                8192,
                20260825,
                "receiver_mis",
                "gpu_cuda",
                max_depth=2,
            )
        )
        gpu_parity = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(gpu),
            absolute_tolerance=1e-9,
            relative_tolerance=1e-9,
            max_ulp_distance=1 << 48,
        ).to_dict()
        if not gpu_parity["passed"]:
            raise SystemExit("PERF-4E CPU/GPU parity contract failed")

    payload = {
        "contract": CONTRACT,
        "ray_count_per_repeat": args.rays,
        "repeat_count": args.repeats,
        "scene": {
            "emitter_mm": [1.0, 1.0],
            "receiver_mm": [4.0, 4.0],
            "distance_mm": 100.0,
            "distribution": "lambertian",
            "receiver_importance_fraction": 0.5,
        },
        "source": source,
        "receiver_mis": receiver_mis,
        "variance_reduction_factor": variance_reduction,
        "gpu_preflight": asdict(preflight),
        "gpu_parity": gpu_parity,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
