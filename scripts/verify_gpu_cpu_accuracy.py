from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b2a_multibounce import build_two_bounce_case
from leakage_simulator import gpu_cuda_intersection as gpu_cuda
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import (
    EmitterSpec,
    OpticalProfile,
    RayTraceConfig,
    ReceiverSpec,
)


CONTRACT = "gpu_cpu_accuracy_gate_v1"
MONTE_CARLO_CONTRACT = "cpu_gpu_deterministic_batch_v1"


def _add_quad(
    mesh: TriangleMesh,
    points: tuple[tuple[float, float, float], ...],
    material_id: str,
) -> list[int]:
    vertices = [mesh.add_vertex(point) for point in points]
    return [
        mesh.add_face(vertices[0], vertices[1], vertices[2], material_id),
        mesh.add_face(vertices[0], vertices[2], vertices[3], material_id),
    ]


def build_face_direct_case(ray_count: int) -> DirectRayTraceInput:
    mesh = TriangleMesh()
    source_faces = _add_quad(
        mesh,
        (
            (-5.0, -5.0, 0.0),
            (5.0, -5.0, 0.0),
            (5.0, 5.0, 0.0),
            (-5.0, 5.0, 0.0),
        ),
        "source_surface",
    )
    return DirectRayTraceInput(
        mesh=mesh,
        emitters=[
            EmitterSpec(
                emitter_id="face_source",
                emitter_type="face",
                face_indices=source_faces,
                direction_distribution="lambertian",
                power_lumen=1.0,
                ray_count=ray_count,
                seed=20260825,
            )
        ],
        receivers=[
            ReceiverSpec(
                receiver_id="face_observer",
                center=(0.0, 0.0, 10.0),
                normal=(0.0, 0.0, -1.0),
                width_mm=30.0,
                height_mm=30.0,
                resolution=(40, 40),
            )
        ],
        optical_profiles=[],
        config=RayTraceConfig(
            ray_count=ray_count,
            max_depth=1,
            seed=20260825,
            min_energy=1e-12,
            contribution_mode="summary",
            intersection_backend="bvh",
            store_ray_paths=False,
        ),
    )


def build_stochastic_two_bounce_case(ray_count: int) -> DirectRayTraceInput:
    trace_input = build_two_bounce_case(ray_count)
    trace_input.optical_profiles = [
        OpticalProfile(
            "mirror_a",
            0.8,
            scatter_model="mixed",
            specular_ratio=0.55,
            diffuse_ratio=0.45,
            gaussian_sigma_deg=12.0,
        ),
        OpticalProfile(
            "mirror_b",
            0.5,
            scatter_model="lambertian",
        ),
    ]
    trace_input.config.min_energy = 0.005
    trace_input.config.termination_mode = "russian_roulette"
    trace_input.config.store_ray_paths = False
    trace_input.config.max_stored_paths = 0
    return trace_input


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    payload["config"]["compute_backend"] = "normalized"
    return payload


def _receiver_snapshot(result) -> dict:
    receiver_id = result.receivers[0].receiver_id
    metrics = result.metrics[receiver_id]
    return {
        "receiver_id": receiver_id,
        "hit_count": result.receiver_hit_count,
        "surface_hit_count": result.surface_hit_count,
        "peak_nit_est": metrics["peak_nit_est"],
        "mean_nit_est": metrics["mean_nit_est"],
        "total_flux_lumen": metrics["total_flux_lumen"],
        "error_estimate_percent": metrics["error_estimate_percent"],
        "peak_area_error_estimate_percent": metrics[
            "peak_area_error_estimate_percent"
        ],
        "statistical_quality": metrics["statistical_quality"],
    }


def _run(builder: Callable[[int], DirectRayTraceInput], ray_count: int, backend: str):
    trace_input = builder(ray_count)
    trace_input.config.compute_backend = backend
    started = time.perf_counter()
    result = run_direct_ray_trace(trace_input)
    return result, time.perf_counter() - started


def _verify_case(
    name: str,
    builder: Callable[[int], DirectRayTraceInput],
    ray_count: int,
) -> dict:
    warm_count = min(ray_count, 8192)
    _run(builder, warm_count, "cpu")
    _run(builder, warm_count, "gpu_cuda")

    cpu_result, cpu_sec = _run(builder, ray_count, "cpu")
    gpu_result, gpu_sec = _run(builder, ray_count, "gpu_cuda")
    cpu_performance = cpu_result.metrics["_performance_summary"]
    gpu_performance = gpu_result.metrics["_performance_summary"]
    semantic_exact = _semantic_payload(cpu_result) == _semantic_payload(gpu_result)
    gpu_execution_proven = (
        gpu_performance["compute_execution_state"] in {"gpu_active", "gpu_mixed"}
        and gpu_performance["gpu_cuda_gpu_success_count"] > 0
    )
    contract_valid = (
        cpu_performance["monte_carlo_contract"] == MONTE_CARLO_CONTRACT
        and gpu_performance["monte_carlo_contract"] == MONTE_CARLO_CONTRACT
    )
    return {
        "name": name,
        "ray_count": ray_count,
        "passed": semantic_exact and gpu_execution_proven and contract_valid,
        "semantic_exact": semantic_exact,
        "contract_valid": contract_valid,
        "gpu_execution_proven": gpu_execution_proven,
        "cpu_runtime_sec": cpu_sec,
        "gpu_runtime_sec": gpu_sec,
        "speedup": cpu_sec / gpu_sec if gpu_sec > 0.0 else None,
        "cpu": _receiver_snapshot(cpu_result),
        "gpu": _receiver_snapshot(gpu_result),
        "gpu_execution_state": gpu_performance["compute_execution_state"],
        "gpu_cuda_gpu_success_count": gpu_performance[
            "gpu_cuda_gpu_success_count"
        ],
        "gpu_cuda_hybrid_cpu_success_count": gpu_performance[
            "gpu_cuda_hybrid_cpu_success_count"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact CPU/GPU Monte Carlo parity on real CUDA hardware."
    )
    parser.add_argument("--rays", type=int, default=100_000)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    if args.rays < 8192:
        parser.error("--rays must be at least 8192 so a real CUDA batch executes")

    preflight = gpu_cuda.preflight_gpu_cuda(refresh=True)
    preflight_payload = asdict(preflight)
    preflight_passed = (
        preflight.available
        and preflight.strict_float64
        and preflight.kernel_executed
        and preflight.kernel_verified
        and preflight.preflight_scope == gpu_cuda.PREFLIGHT_SCOPE
        and gpu_cuda.PROVIDER_CONTRACT == "strict_float64_bvh_v1"
    )
    report = {
        "contract": CONTRACT,
        "monte_carlo_contract": MONTE_CARLO_CONTRACT,
        "preflight": {
            **preflight_payload,
            "provider_contract": gpu_cuda.PROVIDER_CONTRACT,
            "passed": preflight_passed,
        },
        "cases": [],
        "passed": False,
    }
    if preflight_passed:
        report["cases"] = [
            _verify_case("face_direct", build_face_direct_case, args.rays),
            _verify_case(
                "stochastic_two_bounce",
                build_stochastic_two_bounce_case,
                args.rays,
            ),
        ]
        report["passed"] = all(case["passed"] for case in report["cases"])

    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
