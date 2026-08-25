from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
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
from leakage_simulator.types import (
    EmitterSpec,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


CONTRACT = "perf4e_b_lambertian_bounce_mis_benchmark_v1"


def _add_quad(
    mesh: TriangleMesh,
    points: tuple[tuple[float, float, float], ...],
    material_id: str,
) -> None:
    vertices = [mesh.add_vertex(point) for point in points]
    mesh.add_face(vertices[0], vertices[1], vertices[2], material_id)
    mesh.add_face(vertices[0], vertices[2], vertices[3], material_id)


def _build_case(
    ray_count: int,
    seed: int,
    strategy: str,
    backend: str,
    *,
    with_blocker: bool = False,
) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    _add_quad(
        mesh,
        (
            (-20.0, -20.0, 10.0),
            (20.0, -20.0, 10.0),
            (20.0, 20.0, 10.0),
            (-20.0, 20.0, 10.0),
        ),
        "reflector",
    )
    emitter_z = 5.0 if with_blocker else 0.0
    if with_blocker:
        _add_quad(
            mesh,
            (
                (-5.0, -5.0, 0.0),
                (5.0, -5.0, 0.0),
                (5.0, 5.0, 0.0),
                (-5.0, 5.0, 0.0),
            ),
            "blocker",
        )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="source",
                emitter_type="datum_plane",
                center=(0.0, 0.0, emitter_z),
                u_axis=(1.0, 0.0, 0.0),
                v_axis=(0.0, 1.0, 0.0),
                width_mm=0.2,
                height_mm=0.2,
                direction_distribution="gaussian",
                gaussian_sigma_deg=0.01,
                power_lumen=1.0,
                ray_count=ray_count,
                seed=seed,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="observer",
                center=(0.0, 0.0, -10.0),
                normal=(0.0, 0.0, 1.0),
                width_mm=1.0,
                height_mm=1.0,
                resolution=(4, 4),
            )
        ],
        optical_profiles=[
            OpticalProfile("reflector", 0.8, scatter_model="lambertian"),
            OpticalProfile("blocker", 0.0, scatter_model="none"),
        ],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=2,
            seed=seed,
            min_energy=1e-12,
            contribution_mode="summary",
            intersection_backend="bvh",
            compute_backend=backend,
            store_ray_paths=False,
            bounce_sampling_strategy=strategy,
            bounce_receiver_importance_fraction=0.5,
        ),
    )


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    return payload


def _sample_distribution(ray_count: int, repeats: int, strategy: str) -> dict:
    fluxes = []
    hit_counts = []
    elapsed = []
    effective_sample_ratios = []
    directed_fractions = []
    for repeat in range(repeats):
        started = time.perf_counter()
        result = run_direct_ray_trace(
            _build_case(
                ray_count,
                20_000 + repeat,
                strategy,
                "cpu",
            )
        )
        elapsed.append(time.perf_counter() - started)
        fluxes.append(result.metrics["observer"]["total_flux_lumen"])
        hit_counts.append(result.receiver_hit_count)
        performance = result.metrics["_performance_summary"]
        effective_sample_ratios.append(
            performance["bounce_sampling_effective_sample_ratio"]
        )
        directed_fractions.append(
            performance["bounce_sampling_receiver_directed_fraction"]
        )
    mean_flux = statistics.mean(fluxes)
    standard_deviation = statistics.pstdev(fluxes)
    relative_standard_error = (
        standard_deviation / abs(mean_flux) if mean_flux else None
    )
    projected_rays_for_five_percent = (
        ray_count * (relative_standard_error / 0.05) ** 2
        if relative_standard_error is not None
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
        "mean_receiver_directed_fraction": statistics.mean(
            directed_fractions
        ),
        "projected_rays_for_5_percent": projected_rays_for_five_percent,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rays", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--parity-rays", type=int, default=8192)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "outputs"
            / "perf4e_bounce_mis"
            / "benchmark.json"
        ),
    )
    args = parser.parse_args()

    source = _sample_distribution(args.rays, args.repeats, "source")
    receiver_mis = _sample_distribution(
        args.rays,
        args.repeats,
        "receiver_mis",
    )
    variance_reduction = (
        (source["standard_deviation_lumen"] / receiver_mis["standard_deviation_lumen"])
        ** 2
        if receiver_mis["standard_deviation_lumen"] > 0.0
        else None
    )
    expected_flux = 0.8 / (math.pi * 20.0 * 20.0)
    receiver_mis_bias = (
        (receiver_mis["mean_flux_lumen"] - expected_flux) / expected_flux
    )

    blocked = run_direct_ray_trace(
        _build_case(4096, 30_001, "receiver_mis", "cpu", with_blocker=True)
    )

    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    gpu_parity = None
    gpu_runtime_sec = None
    if (
        preflight.available
        and preflight.strict_float64
        and preflight.kernel_executed
        and preflight.kernel_verified
        and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
    ):
        cpu = run_direct_ray_trace(
            _build_case(
                args.parity_rays,
                20260825,
                "receiver_mis",
                "cpu",
            ),
            wavefront_residency="host_roundtrip",
        )
        gpu_started = time.perf_counter()
        gpu = run_direct_ray_trace(
            _build_case(
                args.parity_rays,
                20260825,
                "receiver_mis",
                "gpu_cuda",
            ),
            wavefront_residency="gpu_resident",
        )
        gpu_runtime_sec = time.perf_counter() - gpu_started
        gpu_parity = compare_semantic_payloads(
            _semantic_payload(cpu),
            _semantic_payload(gpu),
            absolute_tolerance=1e-12,
            relative_tolerance=1e-12,
            max_ulp_distance=32,
        ).to_dict()
        if not gpu_parity["passed"]:
            raise SystemExit("PERF-4E-B CPU/GPU parity contract failed")

    payload = {
        "contract": CONTRACT,
        "ray_count_per_repeat": args.rays,
        "repeat_count": args.repeats,
        "scene": {
            "primary_emitter": "narrow Gaussian toward reflector",
            "reflector": "Lambertian, reflectance 0.8",
            "reflection_distance_to_receiver_mm": 20.0,
            "receiver_mm": [1.0, 1.0],
            "receiver_importance_fraction": 0.5,
            "direct_receiver_path": False,
        },
        "expected_flux_lumen_small_angle": expected_flux,
        "source": source,
        "receiver_mis": receiver_mis,
        "receiver_mis_relative_bias": receiver_mis_bias,
        "variance_reduction_factor": variance_reduction,
        "occlusion_gate": {
            "receiver_hits": blocked.receiver_hit_count,
            "receiver_flux_lumen": blocked.metrics["observer"][
                "total_flux_lumen"
            ],
            "passed": blocked.receiver_hit_count == 0,
        },
        "gpu_preflight": asdict(preflight),
        "gpu_runtime_sec": gpu_runtime_sec,
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
