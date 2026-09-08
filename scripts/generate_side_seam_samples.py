"""Generate a synthetic sealed TV right-side deco/chassis seam reference.

The STEP assembly is an invented, reduced-size enclosure, not company CAD.
Front is +Z, right is +X, up is +Y. Only a bounded straight seam on the right
side is open. CPU traces and path-crossing checks are geometric diagnostics;
the values are not calibrated perceived brightness.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from generate_closed_enclosure_samples import box, save_step, write_json
from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import materialize_scene_derived_geometry
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec

WIDTH, HEIGHT, DEPTH, WALL = 120.0, 80.0, 16.0, 0.4
SEAM_Y0, SEAM_Y1, SEAM_Z = 8.0, 28.0, 12.0
CASES = (("closed", 0.0), ("gap_0p1", 0.1), ("gap_0p3", 0.3), ("gap_0p5", 0.5))


def make_parts(gap):
    chassis = box(-WALL, -WALL, -WALL, WIDTH+WALL, HEIGHT+WALL, 0)
    for coordinates in (
        (-WALL, -WALL, 0, 0, HEIGHT+WALL, DEPTH+WALL),
        (0, -WALL, 0, WIDTH+WALL, 0, DEPTH+WALL),
        (0, HEIGHT, 0, WIDTH+WALL, HEIGHT+WALL, DEPTH+WALL),
        (WIDTH, 0, 0, WIDTH+WALL, HEIGHT, SEAM_Z),
    ):
        chassis = chassis.union(box(*coordinates))
    screen = box(8, 8, DEPTH, 112, 72, DEPTH+WALL)
    deco = box(0, 0, DEPTH, WIDTH, HEIGHT, DEPTH+WALL).cut(
        box(8, 8, DEPTH-1, 112, 72, DEPTH+WALL+1))
    side = box(WIDTH, 0, SEAM_Z, WIDTH+WALL, HEIGHT, DEPTH+WALL)
    if gap > 0:
        side = side.cut(box(WIDTH-1, SEAM_Y0, SEAM_Z-1,
                            WIDTH+WALL+1, SEAM_Y1, SEAM_Z+gap))
    deco = deco.union(side)
    parts = [
        ("Opaque_Rear_Chassis", chassis, (0.25, 0.27, 0.30)),
        ("Opaque_LCD_Screen", screen, (0.12, 0.15, 0.19)),
        ("Front_and_Right_Deco", deco, (0.06, 0.07, 0.09)),
    ]
    for name, shape, _ in parts:
        if not shape.val().isValid() or shape.val().Volume() <= 0:
            raise AssertionError(f"Invalid fixture solid: {name}")
    return parts


def make_request(ray_count, seed):
    emitter = EmitterSpec(
        emitter_id="synthetic_blu", emitter_type="datum_plane",
        center=(60, 40, 11.5), u_axis=(1, 0, 0), v_axis=(0, 1, 0),
        width_mm=104, height_mm=64, direction_distribution="lambertian",
        power_mode="total", power_lumen=1.0, ray_count=ray_count, seed=seed,
    )
    receivers = [
        ReceiverSpec(receiver_id="right_side", display_name="Right side seam",
            center=(130,40,8), normal=(-1,0,0), u_axis=(0,-1,0), v_axis=(0,0,1),
            width_mm=100, height_mm=40, resolution=(200,160)),
        ReceiverSpec(receiver_id="front", display_name="Front guard",
            center=(60,40,28), normal=(0,0,-1), u_axis=(1,0,0), v_axis=(0,-1,0),
            width_mm=140, height_mm=100, resolution=(28,20)),
        ReceiverSpec(receiver_id="back", display_name="Back guard",
            center=(60,40,-12), normal=(0,0,1), u_axis=(1,0,0), v_axis=(0,1,0),
            width_mm=140, height_mm=100, resolution=(28,20)),
        ReceiverSpec(receiver_id="left", display_name="Left guard",
            center=(-10,40,8), normal=(1,0,0), u_axis=(0,1,0), v_axis=(0,0,1),
            width_mm=100, height_mm=40, resolution=(20,8)),
        ReceiverSpec(receiver_id="bottom", display_name="Bottom guard",
            center=(60,-10,8), normal=(0,1,0), u_axis=(1,0,0), v_axis=(0,0,-1),
            width_mm=140, height_mm=40, resolution=(28,8)),
        ReceiverSpec(receiver_id="top", display_name="Top guard",
            center=(60,90,8), normal=(0,-1,0), u_axis=(1,0,0), v_axis=(0,0,1),
            width_mm=140, height_mm=40, resolution=(28,8)),
    ]
    profile = OpticalProfile("default", reflectance=.2,
        specular_ratio=0, diffuse_ratio=1, scatter_model="lambertian",
        roughness=.88, gaussian_sigma_deg=32,
        notes="Synthetic opaque matte reference; not measured TV material")
    config = RayTraceConfig(ray_count=ray_count, seed=seed, max_depth=4,
        compute_backend="cpu", intersection_backend="bvh", min_energy=0,
        contribution_mode="summary", store_ray_paths=True, max_stored_paths=5000,
        termination_mode="threshold")
    return {"project_name":"Right-side straight seam", "emitters":[emitter.to_dict()],
        "receivers":[receiver.to_dict() for receiver in receivers],
        "config":config.to_dict(), "optical_profiles":[profile.to_dict()],
        "optical_assignments":[], "transform_rules":[],
        "excluded_component_ids":[], "roi_faces":[]}


def side_crossing(path):
    for a, b in zip(path, path[1:]):
        p, q = a["point"], b["point"]
        if p[0] <= WIDTH+WALL < q[0]:
            t = (WIDTH+WALL-p[0])/(q[0]-p[0])
            return [p[axis]+t*(q[axis]-p[axis]) for axis in range(3)]
    return None


def in_slit(point, gap):
    if point is None or gap <= 0:
        return False
    _, y, z = point
    eps = 1e-6
    return SEAM_Y0-eps <= y <= SEAM_Y1+eps and SEAM_Z-eps <= z <= SEAM_Z+gap+eps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"samples"/"side_seam")
    parser.add_argument("--rays", type=int, default=500000)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--geometry-only", action="store_true")
    args = parser.parse_args()
    if args.rays < 1000:
        parser.error("Use at least 1000 rays")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = ApiRuntime(ROOT, max_cached_scenes=4)
    summaries = []
    for name, gap in CASES:
        print(f"[STEP] {name}", flush=True)
        step_path = output/f"{name}.step"
        parts = make_parts(gap)
        save_step(parts, step_path)
        scene = runtime.load_scene(str(step_path))
        if scene.get("metadata", {}).get("synthetic"):
            raise AssertionError("Expected actual STEP CAD import")
        request = make_request(args.rays, args.seed)
        request["project_name"] = f"TV right-side lower seam / {gap:g} mm"
        request["scene_token"] = scene["metadata"]["scene_token"]
        request["optical_assignments"] = [{
            "assignment_id":f"opaque_{component['component_id']}",
            "target_type":"part", "component_id":component["component_id"],
            "profile_id":"default", "face_indices":[], "priority":0, "enabled":True,
        } for component in scene["components"]]
        write_json(output/f"{name}.scene.json", materialize_scene_derived_geometry(scene))
        write_json(output/f"{name}.request.json", request)
        if args.geometry_only:
            continue
        trace_mesh = runtime._scene_mesh_for_request(request)
        engine_input = runtime._build_trace_input_for_request(trace_mesh, request)
        print(f"[CPU] {name}: {args.rays} rays / {len(engine_input.mesh.faces)} triangles", flush=True)
        started = time.perf_counter()
        result = run_direct_ray_trace(engine_input)
        elapsed = time.perf_counter()-started
        data = result.to_dict()
        receiver_paths = [path for path in data["stored_paths"]
                          if path and path[-1]["event_type"] == "receiver"]
        crossings = [side_crossing(path) for path in receiver_paths]
        if not all(in_slit(point,gap) for point in crossings):
            raise AssertionError(f"Captured path escaped outside intended side seam: {name}")
        if len(receiver_paths) != result.receiver_hit_count:
            raise AssertionError(f"Receiver capture incomplete: {name}")
        total_flux = sum(data["metrics"][receiver["receiver_id"]]["total_flux_lumen"]
                         for receiver in request["receivers"])
        captured_flux = sum(path[-1]["receiver_flux_lumen"] for path in receiver_paths)
        if abs(total_flux-captured_flux) > max(1e-12,total_flux*1e-10):
            raise AssertionError(f"Captured receiver flux mismatch: {name}")
        summary = {
            "case":name, "gap_mm":gap, "seam_length_mm":20.0,
            "physical_aperture_area_mm2":20.0*gap,
            "cad_file":step_path.name, "parts":len(parts),
            "triangles":len(engine_input.mesh.faces), "ray_count":args.rays,
            "seed":args.seed, "max_depth":4, "compute_backend":"cpu",
            "emitted_lumen":1.0, "elapsed_seconds":elapsed,
            "intersection_provider":data["metrics"]["_performance_summary"]["intersection_provider"],
            "compute_execution_state":data["metrics"]["_performance_summary"]["compute_execution_state"],
            "receiver_hits":result.receiver_hit_count,
            "captured_receiver_paths":len(receiver_paths),
            "full_receiver_capture":True, "checked_side_slit_crossings":len(crossings),
            "outside_slit_count":0, "receiver_flux_lumen":total_flux,
            "captured_incoming_energy_lumen":sum(
                path[-1]["incoming_energy_lumen"] for path in receiver_paths),
            "per_receiver":data["metrics"], "calibrated_appearance":False,
            "source_context_binding":"live UI run required for fresh Case identity",
        }
        write_json(output/f"{name}.result.json", data)
        write_json(output/f"{name}.crossings.json", crossings)
        summaries.append(summary)
        print(f"[RESULT] {name}: {result.receiver_hit_count} hits, {total_flux:.12g} lm, {elapsed:.3f}s", flush=True)
    if summaries:
        assert summaries[0]["receiver_hits"] == 0, "Closed fixture leaked"
        assert all(row["receiver_hits"] > 0 for row in summaries[1:]), "Gap fixture not detected"
        fluxes = [row["receiver_flux_lumen"] for row in summaries]
        assert all(a < b for a,b in zip(fluxes,fluxes[1:])), "Flux must increase in this same-seed gap sequence"
        write_json(output/"validation.json", {
            "schema":"side-seam-validation.v1",
            "purpose":"synthetic fully enclosed right-side straight-gap CPU reference",
            "axes":{"front":"+Z", "right":"+X", "up":"+Y"},
            "enclosure_inner_mm":[WIDTH,HEIGHT,DEPTH], "wall_mm":WALL,
            "seam":{"plane_x_mm":WIDTH+WALL, "y_mm":[SEAM_Y0,SEAM_Y1], "base_z_mm":SEAM_Z},
            "optical_domain":"full closed enclosure; no ROI or excluded part",
            "calibrated_appearance":False, "validation_passed":True, "cases":summaries,
        })
        print("[PASS] Closed case and every captured gap escape verified", flush=True)


if __name__ == "__main__":
    main()
