from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from leakage_simulator.geometry import RayBatch, TriangleMesh
from tools.perf5e_native.probe import OptixCandidateProbe, pack_rays
from tools.perf5e_native.selective_replay import CONTRACT, assert_exact_query, selective_query
from tools.perf5e_native.transport_validation import compare_query, production_preflight_gate
from verify_gpu_cuda_runtime import verify_device
from verify_perf5e_transport import identities, sha256, validate_build, write_json


def boundary_cases(count):
    generator = np.random.default_rng(20261023)
    coordinates = generator.uniform(-0.8, 0.8, size=(count, 2))
    coordinates[:5] = [[0, 0], [-1, -1], [1, 1], [-1, 1], [1, -1]]
    angle = np.deg2rad(31.0)
    rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
    specifications = [(f"gap_{gap:g}_far_first", {"gap": gap}) for gap in (0.0, 1e-12, 1e-10, 1e-8, 1e-5, 1.0)]
    specifications += [
        ("rotated_translated", {"rotation": rotation, "translation": [17, -43, 29], "gap": 1e-7}),
        ("large_offset_small_triangles", {"rotation": rotation, "translation": [1e6, -1e6, 1e6], "scale": 1e-3}),
        ("very_large_offset", {"translation": [1e9, -1e9, 1e9]}),
        ("trace_excluded_front", {"exclude_front": True}),
        ("ignore_front_triangle", {"ignored": 2}),
        ("finite_limit_miss", {"maximum": 4.0}),
        ("maximum_inclusive", {"maximum": 5.0}),
        ("minimum_exclusive", {"minimum": 5.0}),
        ("empty_interval", {"minimum": 5.0, "maximum": 5.0}),
        ("tiny_direction_guard", {"tiny_direction": True}),
        ("oblique_incidence", {"oblique": True}),
        ("distant_origin_guard", {"distance": 1e8}),
    ]
    for name, settings in specifications:
        mesh = TriangleMesh()
        transform = settings.get("rotation", np.eye(3))
        translation = np.array(settings.get("translation", [0, 0, 0]), dtype=np.float64)
        scale = settings.get("scale", 1.0)
        for plane, height in enumerate((5.0 + settings.get("gap", 1.0), 5.0)):
            points = np.array([[-1, -1, height], [1, -1, height], [1, 1, height], [-1, 1, height]]) * scale
            vertices = [mesh.add_vertex(tuple(point)) for point in points @ transform.T + translation]
            metadata = {"component_id": plane, "cad_face_id": plane,
                        "trace_excluded": plane == 1 and settings.get("exclude_front", False)}
            mesh.add_face(vertices[0], vertices[1], vertices[2], f"material_{plane}", dict(metadata))
            mesh.add_face(vertices[0], vertices[2], vertices[3], f"material_{plane}", dict(metadata))
        origins = np.column_stack((coordinates, np.zeros(count))) * scale
        directions = np.tile([0.0, 0.0, 1.0], (count, 1))
        if settings.get("tiny_direction"):
            directions[:, 0] = 1e-14
        if settings.get("oblique"):
            directions[:] = [0.125, 0.25, 1.0]
            origins[:, :2] -= 5 * directions[:, :2]
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        origins[:, 2] -= settings.get("distance", 0.0)
        rays = RayBatch(origins @ transform.T + translation, directions @ transform.T,
                        settings.get("minimum", 1e-8), settings.get("maximum", np.inf), settings.get("ignored", -1))
        yield name, mesh, rays, settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rays", type=int, default=256)
    parser.add_argument("--warm-runs", type=int, default=2)
    args = parser.parse_args()
    if args.rays < 5 or args.warm_runs < 2:
        parser.error("Require at least five rays and two warm runs")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"contract": CONTRACT, "scope": "synthetic_boundary_query_preservation_not_physical_or_production_coverage",
              "delivery": "source_checkout_existing_run_web_gpu_environment",
              "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=ROOT).strip(),
              "production_enabled": False, "performance_acceptance_eligible": False, "passed": False, "records": [],
              "acceptance_gates": {"faces": "exact", "distance": "bit_exact", "repeats": "bit_exact"}}
    path = args.output / "report.json"
    try:
        validate_build(args.native_directory)
        report["source_before"] = identities(args.native_directory)
        report["preflight"] = verify_device()
        if not production_preflight_gate(report["preflight"]):
            raise RuntimeError("Production preflight failed")
        for name, mesh, rays, settings in boundary_cases(args.rays):
            scene = mesh.prepare_native_cpu_scene()
            packed = pack_rays(rays.origins, rays.directions, rays.min_t, rays.max_t, rays.ignore_faces)
            with OptixCandidateProbe(scene, args.native_directory) as probe:
                for backend in ("cpu", "gpu_cuda"):
                    query = mesh.intersect_rays_native_cpu if backend == "cpu" else mesh.intersect_rays_gpu_cuda
                    first = None
                    for repeat in range(args.warm_runs + 1):
                        reference, execution = query(rays, backend="bvh")
                        expected = np.repeat(reference.face_indices[:, None], 2, axis=1)
                        candidate, visited, timings = probe.query_coverage(packed, expected)
                        started = time.perf_counter()
                        corrected, replay, rejected = selective_query(mesh, rays, candidate, backend)
                        selective_seconds = time.perf_counter() - started
                        record = {
                            "scene": name, "backend": backend, "repeat": repeat, **compare_query(candidate, visited, reference),
                            "replay_rows": int(replay.sum()), "winner_only_rows": int((~replay).sum()),
                            "refinement_rejected_rows": int(rejected.sum()), "selective_seconds": selective_seconds,
                            "optix_timings": timings, "provider_call_completed": True,
                            "gpu_device": getattr(execution, "device_name", None),
                            "provider_scope": "direct_intersection_not_full_transport_compute_state",
                            "corrected_face_mismatches": int(np.count_nonzero(corrected.face_indices != reference.face_indices)),
                            "corrected_distance_bit_mismatches": int(np.count_nonzero(corrected.t.view(np.uint64) != reference.t.view(np.uint64))),
                        }
                        artifact = args.output / f"{name}-{backend}-{repeat}.npz"
                        with artifact.open("xb") as stream:
                            np.savez_compressed(stream, rays=packed, candidate=candidate, visited=visited, replay=replay,
                                                rejected=rejected, reference_face=reference.face_indices, reference_distance=reference.t,
                                                corrected_face=corrected.face_indices, corrected_distance=corrected.t,
                                                triangle_v0=scene.triangle_v0, triangle_edge1=scene.triangle_edge1,
                                                triangle_edge2=scene.triangle_edge2, traceable_face_mask=scene.traceable_face_mask)
                        record["artifact"] = {"path": str(artifact.resolve()), "sha256": sha256(artifact)}
                        if first is None:
                            first = reference
                        record["same_mode_repeat_exact"] = (reference.t.tobytes() == first.t.tobytes()
                                                              and reference.face_indices.tobytes() == first.face_indices.tobytes())
                        report["records"].append(record)
                        write_json(path, report)
                        assert_exact_query(corrected, reference)
                        if not record["same_mode_repeat_exact"]:
                            raise AssertionError("Canonical repeat changed")
                    print(json.dumps({"scene": name, "backend": backend, "passed": True}), flush=True)
        report["passed"] = len(report["records"]) == 18 * 2 * (args.warm_runs + 1)
    except Exception:
        report["error"] = traceback.format_exc()
        raise
    finally:
        report["source_after"] = identities(args.native_directory)
        report["source_unchanged"] = report.get("source_before") == report["source_after"]
        report["passed"] = report["passed"] and report["source_unchanged"]
        write_json(path, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
