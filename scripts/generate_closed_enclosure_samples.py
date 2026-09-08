"""Create non-product STEP fixtures and validate closed/gap/ROI-open transport.

All dimensions and optical values are synthetic.  This is a CPU geometry and
receiver diagnostic, not a calibrated appearance renderer or a real BLU model.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cadquery as cq
import numpy as np

from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import materialize_scene_derived_geometry
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec

WIDTH, HEIGHT, DEPTH, WALL = 120.0, 80.0, 16.0, 2.0
PANEL_X0, PANEL_X1, PANEL_Y0, PANEL_Y1 = 8.0, 112.0, 8.0, 72.0
CASES = (("closed", 0.0, False), ("gap_0p2", 0.2, False),
         ("gap_0p5", 0.5, False), ("roi_cut_open_demo", 0.0, True))


def box(x0, y0, z0, x1, y1, z1):
    return cq.Workplane("XY").box(x1-x0, y1-y0, z1-z0).translate(
        ((x0+x1)/2, (y0+y1)/2, (z0+z1)/2))


def make_parts(gap: float, cropped: bool):
    """Four opaque solids; only the explicit LCD/deco clearance is open."""
    # Rear and four side walls enclose [0,W] x [0,H] x [0,D].
    shell = box(-WALL, -WALL, -WALL, WIDTH+WALL, HEIGHT+WALL, 0)
    for coords in ((-WALL, -WALL, 0, 0, HEIGHT+WALL, DEPTH+WALL),
                   (WIDTH, -WALL, 0, WIDTH+WALL, HEIGHT+WALL, DEPTH+WALL),
                   (0, -WALL, 0, WIDTH, 0, DEPTH+WALL),
                   (0, HEIGHT, 0, WIDTH, HEIGHT+WALL, DEPTH+WALL)):
        shell = shell.union(box(*coords))
    panel = box(PANEL_X0, PANEL_Y0, DEPTH, PANEL_X1, PANEL_Y1, DEPTH+WALL)
    deco = box(0, 0, DEPTH, WIDTH, HEIGHT, DEPTH+WALL).cut(
        box(PANEL_X0, PANEL_Y0, DEPTH-1, PANEL_X1, PANEL_Y1, DEPTH+WALL+1))
    if gap > 0:
        # Local L-shaped clearance between LCD and deco, with finite thickness.
        deco = deco.cut(box(92, 8-gap, DEPTH-1, 112+gap, 8, DEPTH+WALL+1))
        deco = deco.cut(box(112, 8-gap, DEPTH-1, 112+gap, 28, DEPTH+WALL+1))
    # Interior support, well away from the intentional corner aperture.
    frame = box(16, 40, 1, 80, 44, 4)
    parts = [("Rear_Closed_Shell", shell, (0.18, 0.19, 0.21)),
             ("LCD_Opaque_Boundary", panel, (0.26, 0.30, 0.36)),
             ("Front_Deco", deco, (0.07, 0.08, 0.10)),
             ("Internal_Frame", frame, (0.30, 0.31, 0.33))]
    if cropped:
        # Cropping existing solids does NOT close the air cavity at x=80/y=40.
        cutter = box(80, -WALL, -WALL, WIDTH+WALL, 40, DEPTH+WALL)
        cropped_parts = []
        for name, shape, color in parts:
            shape = shape.intersect(cutter)
            if shape.solids().size() and shape.val().Volume() > 1e-8:
                cropped_parts.append((name, shape, color))
        parts = cropped_parts
    for name, shape, _ in parts:
        if not shape.val().isValid():
            raise AssertionError(f"Invalid solid: {name}")
    return parts


def save_step(parts, path):
    assembly = cq.Assembly(name=path.stem)
    for name, shape, color in parts:
        assembly.add(shape, name=name, color=cq.Color(*color))
    assembly.save(str(path), exportType="STEP", mode="default", write_pcurves=False)


def make_request(ray_count: int, seed: int):
    emitter = EmitterSpec(
        emitter_id="synthetic_blu", emitter_type="datum_plane",
        center=(100, 20, 8), u_axis=(1, 0, 0), v_axis=(0, 1, 0),
        width_mm=30, height_mm=28, direction_distribution="lambertian",
        power_mode="total", power_lumen=1.0, ray_count=ray_count, seed=seed,
    )
    # A closed rectangular measurement cage. Each escaping path stops at its
    # first receiver, so summing these fluxes does not double-count a path.
    receivers = [
        ReceiverSpec(receiver_id="front", display_name="Front leakage",
            center=(60,40,28), normal=(0,0,-1), u_axis=(1,0,0), v_axis=(0,-1,0),
            width_mm=140, height_mm=100, resolution=(70,50)),
        ReceiverSpec(receiver_id="back", display_name="Back guard",
            center=(60,40,-12), normal=(0,0,1), u_axis=(1,0,0), v_axis=(0,1,0),
            width_mm=140, height_mm=100, resolution=(14,10)),
        ReceiverSpec(receiver_id="left", display_name="Left guard",
            center=(-10,40,8), normal=(1,0,0), u_axis=(0,1,0), v_axis=(0,0,1),
            width_mm=100, height_mm=40, resolution=(20,8)),
        ReceiverSpec(receiver_id="right", display_name="Right guard",
            center=(130,40,8), normal=(-1,0,0), u_axis=(0,-1,0), v_axis=(0,0,1),
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
        contribution_mode="summary", store_ray_paths=True, max_stored_paths=120,
        termination_mode="threshold")
    return {"project_name":"Closed enclosure validation", "emitters":[emitter.to_dict()],
        "receivers":[r.to_dict() for r in receivers], "config":config.to_dict(),
        "optical_profiles":[profile.to_dict()], "optical_assignments":[],
        "transform_rules":[], "excluded_component_ids":[], "roi_faces":[]}


def first_front_crossing(path):
    for a, b in zip(path, path[1:]):
        p, q = np.asarray(a["point"], float), np.asarray(b["point"], float)
        if p[2] <= DEPTH+WALL and q[2] > DEPTH+WALL and q[2] > p[2]:
            s = (DEPTH+WALL-p[2])/(q[2]-p[2])
            return (p+s*(q-p)).tolist()
    return None


def in_aperture(p, gap, eps=1e-6):
    x,y,_ = p
    return ((92-eps <= x <= 112+gap+eps and 8-gap-eps <= y <= 8+eps)
            or (112-eps <= x <= 112+gap+eps and 8-gap-eps <= y <= 28+eps))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def make_figure(output, results):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    fig = plt.figure(figsize=(15, 8.5), facecolor="#f4f5f7")
    gs = fig.add_gridspec(2,4, height_ratios=[1.05,1], hspace=.42, wspace=.24)
    labels = ["SEALED", "0.2 mm GAP", "0.5 mm GAP", "ROI CUT / NO REAL GAP"]
    colors = ["#4e667a", "#df980c", "#d96223", "#ae3e55"]
    for i, ((name,gap,cropped), label) in enumerate(zip(CASES, labels)):
        ax = fig.add_subplot(gs[0,i]); ax.set_facecolor("#e7e9ed")
        ax.add_patch(Rectangle((88,3), 30,30, color="#303947"))
        ax.add_patch(Rectangle((88,8), 24,25, color="#7f94a7"))
        if gap:
            ax.add_patch(Rectangle((92,8-gap),20+gap,gap,color="#ffc857",linewidth=0))
            ax.add_patch(Rectangle((112,8-gap),gap,20+gap,color="#ffc857",linewidth=0))
            ax.annotate(f"{gap:g} mm", xy=(112+gap/2,19), xytext=(114,26),
                        arrowprops={"arrowstyle":"->", "color":"#ffd778"}, color="#8b4100")
        else:
            ax.plot([92,112,112],[8,8,28],color="white",lw=.65)
            ax.text(93,5.1,"Closed LCD/deco seam",fontsize=8,color="white")
        ax.text(95,22,"LCD",color="white",fontsize=13,weight="bold")
        ax.text(92,3.7,"DECO",color="white",fontsize=8)
        ax.set_xlim(90,118); ax.set_ylim(3,31); ax.set_aspect("equal")
        ax.set_title(label, fontsize=11, color=colors[i], fontweight="bold",pad=12)
        ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)")
        if cropped:
            ax.text(.5,-.29,"Open cavity at x=80 and y=40\n(outside this front detail)",
                    transform=ax.transAxes,ha="center",fontsize=8,color=colors[i])
    ax = fig.add_subplot(gs[1,:2])
    names = [row["case"] for row in results]
    total = [row["receiver_flux_lumen"] for row in results]
    bars = ax.bar(range(4), total, color=colors, width=.65)
    ax.set_xticks(range(4), ["sealed","gap 0.2","gap 0.5","ROI cut"])
    ax.set_ylabel("Total exterior receiver flux (lm)")
    ax.set_title("Same synthetic source / six external measurement faces", fontsize=11)
    ax.grid(axis="y",alpha=.2); ax.set_axisbelow(True)
    for bar,v in zip(bars,total):
        ax.annotate(f"{v:.6g}", (bar.get_x()+bar.get_width()/2, v),
                    xytext=(0,5), textcoords="offset points",ha="center",fontsize=10)
    ax.set_ylim(0,max(total)*1.22 if max(total)>0 else 1)
    ax = fig.add_subplot(gs[1,2:]); ax.axis("off")
    lines = ["WHAT THIS CHECKS", "",
             "Opaque closed cavity -> no exterior receiver hits.",
             "Local LCD/deco clearances -> actual front leakage.",
             "Cropped closed cavity -> artificial boundary escape.", "",
             "One Lambertian internal source: 1 lm (synthetic).",
             "Opaque diffuse reflectance: 0.2; up to 4 reflections.",
             "These are flux/geometry diagnostics, not perceived nits.",
             "Front seam diagrams above are geometry, not light renders."]
    ax.text(.02,.97,"\n".join(lines),va="top",fontsize=11,linespacing=1.5,color="#273343")
    fig.suptitle("Closed enclosure / intentional gap / artificial ROI opening",fontsize=19,
                 fontweight="bold",x=.5,y=.98,color="#1c2f42")
    fig.savefig(output / "validation_preview.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"samples"/"closed_enclosure")
    parser.add_argument("--rays",type=int,default=20000)
    parser.add_argument("--seed",type=int,default=20260903)
    args=parser.parse_args()
    if args.rays < 1000:
        parser.error("Use at least 1000 rays for this validation fixture")
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    runtime=ApiRuntime(ROOT,max_cached_scenes=5)
    summaries=[]
    for name,gap,cropped in CASES:
        print(f"[SAMPLE] {name}: exporting opaque STEP solids",flush=True)
        parts=make_parts(gap,cropped)
        path=output/f"{name}.step"
        save_step(parts,path)
        scene=runtime.load_scene(str(path))
        # A successful sample must use the CAD importer, not synthetic fallback.
        if scene.get("metadata",{}).get("synthetic"):
            raise AssertionError("STEP importer used a synthetic fallback")
        request=make_request(args.rays,args.seed)
        request["scene_token"]=scene["metadata"]["scene_token"]
        request["project_name"]=f"Closed enclosure / {name}"
        request["optical_assignments"]=[{
            "assignment_id":f"opaque_{component['component_id']}",
            "target_type":"part", "component_id":component["component_id"],
            "profile_id":"default", "face_indices":[], "priority":0, "enabled":True,
        } for component in scene["components"]]
        trace_mesh=runtime._scene_mesh_for_request(request)
        engine_input=runtime._build_trace_input_for_request(trace_mesh,request)
        if not engine_input.mesh.faces:
            raise AssertionError("Imported STEP produced no trace triangles")
        started=time.perf_counter()
        print(f"[SAMPLE] {name}: CPU trace, {args.rays} rays, {len(engine_input.mesh.faces)} triangles",flush=True)
        result=run_direct_ray_trace(engine_input,
            intersection_provider="python_cpu", wavefront_planner="python_cpu",
            wavefront_reducer="python_cpu", wavefront_pipeline="object_reference")
        data=result.to_dict()
        per_receiver={r["receiver_id"]: {
            "flux_lumen":data["metrics"][r["receiver_id"]]["total_flux_lumen"],
            "hit_count":data["metrics"][r["receiver_id"]]["hit_count"]}
            for r in request["receivers"]}
        reached=[p for p in data["stored_paths"] if p and p[-1]["event_type"]=="receiver"]
        crossing_points=[]
        if gap:
            for p in reached:
                crossing=first_front_crossing(p)
                if crossing is None or not in_aperture(crossing,gap):
                    raise AssertionError(f"Saved escape path did not cross intended gap: {crossing}")
                crossing_points.append(crossing)
        summary={"case":name,"gap_mm":gap,"artificial_roi_cut":cropped,
            "cad_file":path.name,"parts":len(parts),"triangles":len(engine_input.mesh.faces),
            "ray_count":args.rays,"seed":args.seed,"max_depth":4,
            "compute_backend":"cpu","intersection_provider":"python_cpu",
            "emitted_lumen":1.0,"receiver_hits":result.receiver_hit_count,
            "receiver_flux_lumen":sum(r["flux_lumen"] for r in per_receiver.values()),
            "per_receiver":per_receiver,"elapsed_seconds":time.perf_counter()-started,
            "checked_saved_gap_crossings":len(crossing_points),
            "saved_path_check_scope":"bounded diagnostic subset, not a proof for all rays",
            "calibrated_appearance":False}
        write_json(output/f"{name}.scene.json",materialize_scene_derived_geometry(scene))
        write_json(output/f"{name}.request.json",request)
        write_json(output/f"{name}.result.json",data)
        summaries.append(summary)
        print(f"[RESULT] {name}: exterior hits={summary['receiver_hits']}, flux={summary['receiver_flux_lumen']:.9g} lm",flush=True)
    baseline,small,large,cut=summaries
    assert baseline["receiver_hits"] == 0, "Closed sample leaked to an exterior receiver"
    assert small["receiver_hits"] > 0 and large["receiver_hits"] > 0, "Gap was not detected"
    assert large["receiver_flux_lumen"] > small["receiver_flux_lumen"], "Larger aperture did not increase this fixture's flux"
    assert cut["receiver_hits"] > 0, "Open ROI negative control did not escape"
    assert cut["per_receiver"]["left"]["hit_count"]+cut["per_receiver"]["top"]["hit_count"]>0
    write_json(output/"validation.json",{"schema":"closed-enclosure-validation.v1",
        "purpose":"synthetic opaque enclosure and aperture transport regression",
        "roi_demo":"explicit CAD crop; not an exact reproduction of frontend ROI face-selection",
        "source":"1 lm synthetic BLU-equivalent plane, not measured TV",
        "validation_passed":True,"cases":summaries})
    make_figure(output,summaries)
    print(f"[PASS] Closed/gap/ROI-open checks; artifacts: {output}",flush=True)


if __name__=="__main__":
    main()
