"""CPU-only continuity validation using an existing synthetic enclosure fixture.

Writes separate immutable request, scene and result snapshots; never edits samples.
Results remain uncalibrated lumen/path diagnostics, not a perceived-nit renderer.
"""
from __future__ import annotations
import argparse
import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import materialize_scene_derived_geometry


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def front_aperture_crossing(path):
    """Known fixture front is z=18; both arms have a physical 0.5 mm clearance."""
    for a, b in zip(path, path[1:]):
        p, q = a["point"], b["point"]
        if p[2] <= 18.0 < q[2]:
            t = (18.0 - p[2]) / (q[2] - p[2])
            return [p[axis] + t * (q[axis] - p[axis]) for axis in range(3)]
    return None


def in_fixture_aperture(point):
    if point is None:
        return False
    x, y, _ = point
    eps = 1e-6
    return ((92-eps <= x <= 112.5+eps and 7.5-eps <= y <= 8+eps)
            or (112-eps <= x <= 112.5+eps and 7.5-eps <= y <= 28+eps))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "leakage-continuity-validation")
    parser.add_argument("--half-power", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sample_dir = ROOT / "samples" / "closed_enclosure"
    baseline = json.loads((sample_dir / "gap_0p5.request.json").read_text(encoding="utf-8-sig"))
    runtime = ApiRuntime(ROOT, max_cached_scenes=2)
    scene = runtime.load_scene(str(sample_dir / "gap_0p5.step"))
    if scene.get("metadata", {}).get("synthetic"):
        raise AssertionError("CAD importer used a fallback rather than the existing fixture STEP")
    scene_snapshot = materialize_scene_derived_geometry(scene)
    cases = [("gap_0p5_20k_1lm", 20000, 1.0), ("gap_0p5_80k_1lm", 80000, 1.0)]
    if args.half_power:
        cases.append(("gap_0p5_80k_0p5lm", 80000, 0.5))
    rows = []
    for name, ray_count, source_power in cases:
        request = copy.deepcopy(baseline)
        request["scene_token"] = scene["metadata"]["scene_token"]
        request["config"].update(ray_count=ray_count, compute_backend="cpu", max_stored_paths=2000)
        for emitter in request["emitters"]:
            emitter.update(ray_count=ray_count, power_mode="total", power_lumen=source_power)
        mesh = runtime._scene_mesh_for_request(request)
        trace_input = runtime._build_trace_input_for_request(mesh, request)
        print(f"[START] {name}: {ray_count} CPU rays, {len(trace_input.mesh.faces)} triangles", flush=True)
        started = time.perf_counter()
        result = run_direct_ray_trace(trace_input, intersection_provider="python_cpu",
            wavefront_planner="python_cpu", wavefront_reducer="python_cpu", wavefront_pipeline="object_reference")
        elapsed = time.perf_counter() - started
        data = result.to_dict()
        receiver_paths = [p for p in data["stored_paths"] if p and p[-1]["event_type"] == "receiver"]
        crossings = [front_aperture_crossing(path) for path in receiver_paths]
        if not all(in_fixture_aperture(point) for point in crossings):
            raise AssertionError(f"Receiver path escaped outside the fixture aperture: {name}")
        captured_flux = sum(p[-1]["receiver_flux_lumen"] for p in receiver_paths)
        total_flux = sum(data["metrics"][r["receiver_id"]]["total_flux_lumen"] for r in request["receivers"])
        row = {"case": name, "ray_count": ray_count, "source_lumen": source_power,
            "seed": request["config"]["seed"], "elapsed_seconds": elapsed,
            "compute_backend": "cpu", "intersection_provider": "python_cpu",
            "receiver_hit_count": result.receiver_hit_count, "captured_receiver_path_count": len(receiver_paths),
            "full_receiver_capture": len(receiver_paths) == result.receiver_hit_count,
            "checked_gap_crossing_count": len(crossings), "outside_gap_count": 0,
            "receiver_flux_lumen": total_flux, "captured_receiver_flux_lumen": captured_flux,
            "captured_incoming_energy_lumen": sum(path[-1]["incoming_energy_lumen"] for path in receiver_paths),
            "max_stored_paths": 2000, "source_context_binding": "parent frontend binder required",
            "per_receiver": data["metrics"]}
        if not row["full_receiver_capture"]:
            raise AssertionError(f"Incomplete receiver path capture: {name}")
        if abs(captured_flux - total_flux) > max(1e-12, total_flux * 1e-10):
            raise AssertionError(f"Captured energy mismatch: {name}")
        write_json(output / f"{name}.scene.json", scene_snapshot)
        write_json(output / f"{name}.request.json", request)
        write_json(output / f"{name}.result.json", data)
        rows.append(row)
        write_json(output / "summary.json", {"schema": "leakage-continuity-validation.v1",
            "calibrated_appearance": False, "purpose": "CPU receiver-path density and relative-energy validation",
            "cases": rows})
        print(f"[DONE] {name}: {result.receiver_hit_count} hits, {total_flux:.12g} lm, {elapsed:.3f}s; full receiver capture", flush=True)
    if args.half_power:
        full, half = rows[-2:]
        ratio = half["receiver_flux_lumen"] / full["receiver_flux_lumen"]
        incoming_ratio = half["captured_incoming_energy_lumen"] / full["captured_incoming_energy_lumen"]
        if (full["receiver_hit_count"] != half["receiver_hit_count"]
                or abs(ratio - 0.5) > 1e-10 or abs(incoming_ratio - 0.5) > 1e-10):
            raise AssertionError("Half-power trace did not preserve sample count and halve energy")
        print(f"[PASS] Same-seed half power scales receiver energy by {ratio:.12g}", flush=True)
    comparison = {"ray_density_ratio": 4,
        "receiver_flux_80k_over_20k": rows[1]["receiver_flux_lumen"] / rows[0]["receiver_flux_lumen"],
        "incoming_energy_80k_over_20k": rows[1]["captured_incoming_energy_lumen"] / rows[0]["captured_incoming_energy_lumen"]}
    if args.half_power:
        comparison.update(half_power_receiver_flux_ratio=ratio, half_power_incoming_energy_ratio=incoming_ratio)
    write_json(output / "summary.json", {"schema": "leakage-continuity-validation.v1",
        "calibrated_appearance": False, "purpose": "CPU receiver-path density and relative-energy validation",
        "cases": rows, "comparison": comparison})
    print(f"[PASS] CPU result snapshots: {output}", flush=True)


if __name__ == "__main__":
    main()
