"""Synthetic right-side horizontal deco/chassis seam and source-power references.

Front +Z, right +X, up +Y. The 12 mm seam runs along Z at a fixed lower
height Y=8 mm. The only opening is this actual gap in a fully opaque enclosure.
Power comparisons use real CPU traces with identical geometry, seed and rays.
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_closed_enclosure_samples import box, save_step, write_json
from generate_side_seam_samples import make_request as vertical_request
from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import materialize_scene_derived_geometry

WIDTH, HEIGHT, DEPTH, WALL = 120.0, 80.0, 16.0, 0.4
SEAM_Y, SEAM_Z0, SEAM_Z1 = 8.0, 2.0, 14.0
CASES = (
    ("horizontal_gap_0p3_off", 0.3, 0.0),
    ("horizontal_gap_0p3_1x", 0.3, 1.0),
    ("horizontal_gap_0p3_4x", 0.3, 4.0),
    ("horizontal_gap_0p3_16x", 0.3, 16.0),
    ("horizontal_gap_0p1_1x", 0.1, 1.0),
    ("horizontal_gap_0p5_1x", 0.5, 1.0),
)


def make_parts(gap):
    chassis = box(-WALL, -WALL, -WALL, WIDTH+WALL, HEIGHT+WALL, 0)
    for coordinates in (
        (-WALL, -WALL, 0, 0, HEIGHT+WALL, DEPTH+WALL),
        (0, -WALL, 0, WIDTH+WALL, 0, DEPTH+WALL),
        (0, HEIGHT, 0, WIDTH+WALL, HEIGHT+WALL, DEPTH+WALL),
        (WIDTH, 0, 0, WIDTH+WALL, SEAM_Y, DEPTH+WALL),
    ):
        chassis = chassis.union(box(*coordinates))
    screen = box(8, 8, DEPTH, 112, 72, DEPTH+WALL)
    deco = box(0, 0, DEPTH, WIDTH, HEIGHT, DEPTH+WALL).cut(
        box(8, 8, DEPTH-1, 112, 72, DEPTH+WALL+1))
    side = box(WIDTH, SEAM_Y, 0, WIDTH+WALL, HEIGHT, DEPTH+WALL)
    if gap > 0:
        side = side.cut(box(WIDTH-1, SEAM_Y-1, SEAM_Z0,
                            WIDTH+WALL+1, SEAM_Y+gap, SEAM_Z1))
    deco = deco.union(side)
    parts = [
        ("Opaque_Rear_and_Lower_Chassis", chassis, (0.25, 0.27, 0.30)),
        ("Opaque_LCD_Screen", screen, (0.12, 0.15, 0.19)),
        ("Front_and_Upper_Right_Deco", deco, (0.06, 0.07, 0.09)),
    ]
    for name, shape, _ in parts:
        if not shape.val().isValid() or shape.val().Volume() <= 0:
            raise AssertionError(f"Invalid solid: {name}")
    return parts


def side_crossing(path):
    for a, b in zip(path, path[1:]):
        p, q = a["point"], b["point"]
        if p[0] <= WIDTH+WALL < q[0]:
            t = (WIDTH+WALL-p[0])/(q[0]-p[0])
            return [p[axis]+t*(q[axis]-p[axis]) for axis in range(3)]
    return None


def in_slit(point, gap):
    if point is None:
        return False
    _, y, z = point
    eps = 1e-6
    return SEAM_Y-eps <= y <= SEAM_Y+gap+eps and SEAM_Z0-eps <= z <= SEAM_Z1+eps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"samples"/"side_seam_horizontal")
    parser.add_argument("--rays", type=int, default=500000)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--geometry-only", action="store_true")
    args = parser.parse_args()
    if args.rays < 1000:
        parser.error("Use at least 1000 rays")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = ApiRuntime(ROOT, max_cached_scenes=8)
    prepared = []
    for name, gap, power in CASES:
        print(f"[STEP] {name}", flush=True)
        step_path = output/f"{name}.step"
        parts = make_parts(gap)
        save_step(parts, step_path)
        scene = runtime.load_scene(str(step_path))
        if scene.get("metadata", {}).get("synthetic"):
            raise AssertionError("Expected actual STEP CAD import")
        request = vertical_request(args.rays, args.seed)
        request["project_name"] = f"Horizontal side seam / {gap:g} mm / {power:g} lm"
        request["scene_token"] = scene["metadata"]["scene_token"]
        request["emitters"][0]["power_lumen"] = power
        request["receivers"][0]["display_name"] = "Right horizontal seam"
        request["optical_assignments"] = [{
            "assignment_id":f"opaque_{component['component_id']}",
            "target_type":"part", "component_id":component["component_id"],
            "profile_id":"default", "face_indices":[], "priority":0, "enabled":True,
        } for component in scene["components"]]
        write_json(output/f"{name}.scene.json", materialize_scene_derived_geometry(scene))
        write_json(output/f"{name}.request.json", request)
        prepared.append((name,gap,power,scene,request))
    print("[READY] All STEP and settings snapshots available", flush=True)
    if args.geometry_only:
        return
    summaries = []
    for name,gap,power,scene,request in prepared:
        trace_mesh = runtime._scene_mesh_for_request(request)
        engine_input = runtime._build_trace_input_for_request(trace_mesh, request)
        print(f"[CPU] {name}: {args.rays} rays / {len(engine_input.mesh.faces)} triangles", flush=True)
        started = time.perf_counter()
        result = run_direct_ray_trace(engine_input)
        elapsed = time.perf_counter()-started
        data = result.to_dict()
        paths = [path for path in data["stored_paths"] if path and path[-1]["event_type"]=="receiver"]
        crossings = [side_crossing(path) for path in paths]
        if not all(in_slit(point,gap) for point in crossings):
            raise AssertionError(f"Captured path escaped outside actual horizontal seam: {name}")
        if len(paths) != result.receiver_hit_count:
            raise AssertionError(f"Incomplete receiver path capture: {name}")
        total_flux = sum(data["metrics"][receiver["receiver_id"]]["total_flux_lumen"]
                         for receiver in request["receivers"])
        captured_flux = sum(path[-1]["receiver_flux_lumen"] for path in paths)
        if abs(total_flux-captured_flux) > max(1e-12,total_flux*1e-10):
            raise AssertionError(f"Captured receiver flux mismatch: {name}")
        perf = data["metrics"]["_performance_summary"]
        row = {
            "case":name, "gap_mm":gap, "source_lumen":power,
            "seam_length_mm":SEAM_Z1-SEAM_Z0,
            "physical_aperture_area_mm2":(SEAM_Z1-SEAM_Z0)*gap,
            "cad_file":f"{name}.step", "ray_count":args.rays, "seed":args.seed,
            "max_depth":4, "max_stored_paths":5000, "compute_backend":"cpu",
            "intersection_provider":perf["intersection_provider"],
            "compute_execution_state":perf["compute_execution_state"],
            "elapsed_seconds":elapsed, "receiver_hits":result.receiver_hit_count,
            "captured_receiver_paths":len(paths), "full_receiver_capture":True,
            "checked_side_slit_crossings":len(crossings), "outside_slit_count":0,
            "receiver_flux_lumen":total_flux,
            "captured_incoming_energy_lumen":sum(path[-1]["incoming_energy_lumen"] for path in paths),
            "per_receiver":{receiver["receiver_id"]:data["metrics"][receiver["receiver_id"]]
                            for receiver in request["receivers"]},
            "calibrated_appearance":False,
        }
        write_json(output/f"{name}.result.json", data)
        write_json(output/f"{name}.crossings.json", crossings)
        summaries.append(row)
        print(f"[RESULT] {name}: {result.receiver_hit_count} hits, {total_flux:.12g} lm, {elapsed:.3f}s", flush=True)
    off,one,four,sixteen,small,large = summaries
    assert off["receiver_flux_lumen"] == 0, "Zero source emitted nonzero energy"
    assert off["captured_incoming_energy_lumen"] == 0, "Zero source produced incoming energy"
    assert one["receiver_hits"] > 0 and small["receiver_hits"] > 0 and large["receiver_hits"] > 0
    for row,scale in ((four,4.0),(sixteen,16.0)):
        assert row["receiver_hits"] == one["receiver_hits"], "Power changed hit count"
        for key in ("receiver_flux_lumen","captured_incoming_energy_lumen"):
            assert abs(row[key]/one[key]-scale) < 1e-10, "Power response is not linear"
    assert small["receiver_flux_lumen"] < one["receiver_flux_lumen"] < large["receiver_flux_lumen"]
    write_json(output/"validation.json", {
        "schema":"horizontal-side-seam-validation.v1",
        "axes":{"front":"+Z","right":"+X","up":"+Y"},
        "enclosure_inner_mm":[WIDTH,HEIGHT,DEPTH], "wall_mm":WALL,
        "seam":{"plane_x_mm":WIDTH+WALL,"base_y_mm":SEAM_Y,"z_mm":[SEAM_Z0,SEAM_Z1]},
        "optical_domain":"full opaque enclosure with actual side gap; no ROI or excluded part",
        "calibrated_appearance":False, "validation_passed":True, "cases":summaries,
        "power_response":{"off_flux_lumen":off["receiver_flux_lumen"],
            "four_over_one":four["receiver_flux_lumen"]/one["receiver_flux_lumen"],
            "sixteen_over_one":sixteen["receiver_flux_lumen"]/one["receiver_flux_lumen"]},
    })
    print("[PASS] Horizontal seam crossings and actual source power response verified", flush=True)


if __name__ == "__main__":
    main()
