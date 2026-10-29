"""Real-device gate for direction-based Preview; absence of CUDA is a failure, never a pass."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakage_simulator import gpu_cuda_intersection as cuda
from leakage_simulator.geometry import TriangleMesh
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, ReceiverSpec, OpticalProfile, RayTraceConfig


def build_case(angle, rays, backend, reflected=False, face=False, bsdf=False):
    mesh = TriangleMesh()
    points = ((-40,-40,0), (-39,-40,0), (-40,-39,0))
    ids = [mesh.add_vertex(p) for p in points]
    mesh.add_face(*ids, "default")
    if reflected:
        ids = [mesh.add_vertex(p) for p in ((-40,-40,-10), (40,-40,-10), (40,40,-10), (-40,40,-10))]
        mesh.add_face(ids[0], ids[1], ids[2], "mirror")
        mesh.add_face(ids[0], ids[2], ids[3], "mirror")
    a = math.radians(angle)
    source = EmitterSpec(emitter_id="source", emitter_type="datum_plane", center=(0,0,0),
        u_axis=(math.cos(a),0,math.sin(a)), v_axis=(0,-1,0), width_mm=.1, height_mm=.1,
        direction_distribution="gaussian", gaussian_sigma_deg=.1, ray_count=rays, power_lumen=1., seed=20261029)
    if face:
        ids = [mesh.add_vertex(p) for p in ((-.5,-.5,0),(-.5,.5,0),(.5,.5,0),(.5,-.5,0))]
        source.face_indices = [mesh.add_face(ids[0],ids[1],ids[2],"default"),mesh.add_face(ids[0],ids[2],ids[3],"default")]
        source.emitter_type = "face"
    detectors = []
    for axis, name in enumerate("xyz"):
        for sign, prefix in ((1,"pos_"),(-1,"neg_")):
            center, normal, u, v = [0.,0.,0.], [0.,0.,0.], [0.,0.,0.], [0.,0.,0.]
            center[axis], normal[axis] = sign*50., -sign
            u[(axis+1)%3], v[(axis+2)%3] = 1., 1.
            detectors.append(ReceiverSpec(receiver_id="__leak_preview_"+prefix+name, center=tuple(center), normal=tuple(normal),
                u_axis=tuple(u), v_axis=tuple(v), width_mm=100., height_mm=100., resolution=(24,24)))
    profiles = [OpticalProfile(profile_id="mirror", reflectance=.6, absorption=.4, scatter_model="lambertian")]
    assets = []
    if bsdf:
        from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
        asset = import_lt_ascii_bsdf({"files": [
            {"name":"master.txt","text":"#UDOP AngGridMultiple 1.0\nFileName = grid.txt\nMeshFormat = Polar\nMeshDataType = BSDF\nRAOI = 0\nLambda = 550\nPOI = 0\nIndex = 1\nIndexIncident = 1\n"},
            {"name":"grid.txt","text":"SphereMesh: 3 2 0 0 360 90\n1 1 1\n1 1 1\n"},
            {"name":"rt.txt","text":"DFAT Version 1.0\nABSORBING: YES\nINDEX: 1\nDATAITEMS: TAVG RAVG\nwv 550\nAOI 0 0 0.3\n"},
        ]})
        asset["transport"] = {"model":"isotropic_no_shift","wavelengthNm":550,"aoiExtrapolation":"clamp"}
        assets = [asset]
        profiles = [OpticalProfile(profile_id="mirror", reflectance=0., scatter_model="none", bsdf_asset_id=asset["id"])]
    return DirectRayTraceInput(mesh=mesh, emitters=[source], receivers=detectors,
        optical_profiles=profiles, bsdf_assets=assets,
        config=RayTraceConfig(ray_count=rays,max_depth=3,compute_backend=backend,intersection_backend="bvh",
            primary_sampling_strategy="source",bounce_sampling_strategy="source",store_ray_paths=False),
        escape_collection={"minimum":[-50.,-50.,-50.], "maximum":[50.,50.,50.],
            "direction":"pos_z" if reflected else "neg_z", "allowed_regions":[]})


def compare(left, right, path=""):
    if isinstance(left, float) or isinstance(right, float):
        return [] if math.isclose(float(left),float(right),rel_tol=1e-9,abs_tol=1e-12) else [path]
    if isinstance(left, dict) and isinstance(right, dict):
        if left.keys()!=right.keys(): return [path+":keys"]
        return [issue for key in left for issue in compare(left[key],right[key],path+"/"+str(key))]
    if isinstance(left,list) and isinstance(right,list):
        if len(left)!=len(right): return [path+":length"]
        return [issue for i,(a,b) in enumerate(zip(left,right)) for issue in compare(a,b,path+"/"+str(i))]
    return [] if left==right else [path]


def verify_case(name, angle, rays, reflected=False, face=False, bsdf=False):
    cpu = run_direct_ray_trace(build_case(angle,rays,"cpu",reflected,face,bsdf))
    reference = cpu.metrics["_escape_direction_summary"]
    runs = []
    gpu_trace = build_case(angle,rays,"gpu_cuda",reflected,face,bsdf)
    for repeat in range(3):
        started = time.perf_counter()
        gpu = run_direct_ray_trace(gpu_trace)
        elapsed = time.perf_counter()-started
        perf = gpu.metrics["_performance_summary"]
        actual = gpu.metrics["_escape_direction_summary"]
        canonical = lambda s: {**s, "cells": sorted(s["cells"],key=lambda c:(c["receiver_id"],c["row"],c["column"]))}
        issues = compare(canonical(reference), canonical(actual))
        proven = perf.get("compute_execution_state") in ("gpu_active","gpu_mixed") and perf.get("gpu_cuda_gpu_success_count",0)>0
        integrity = all(
            r.receiver_hit_count + r.terminated_ray_count == r.total_rays == rays
            for r in (cpu, gpu)
        ) and all(
            s["selected_count"] + s["excluded_count"] <= s["escaped_count"] <= rays
            and sum(bin["count"] for bin in s["bins"]) == s["selected_count"]
            and sum(cell["count"] for cell in s["cells"]) == s["selected_count"]
            for s in (reference, actual)
        )
        contract = reference.get("sample_contract")==actual.get("sample_contract")=="escape_shared_counter_rng_batch_v1"
        keys = ("compute_execution_state","compute_execution_reason","gpu_cuda_device_name","gpu_cuda_gpu_attempt_count",
            "gpu_cuda_gpu_success_count","gpu_cuda_hybrid_cpu_success_count","intersection_fallback_count","intersection_fallback_reason")
        runs.append({"run":repeat+1,"kind":"first" if repeat==0 else "warm","wall_sec":elapsed,
            "gpu_execution_proven":proven,"sample_contract_valid":contract,"count_integrity":integrity,"difference_count":len(issues),
            "differences":issues[:20],"execution":{key:perf.get(key) for key in keys},
            "selected_count":actual["selected_count"],"bins":actual["bins"],
            "passed":not issues and proven and contract and integrity and actual["selected_count"]>=30})
    return {"name":name,"emitter_types":["face" if face else "datum_plane"],"rays":rays,"cpu_runtime_sec":cpu.runtime_sec,
        "cpu_bins":reference["bins"],"runs":runs,"passed":all(run["passed"] for run in runs)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rays",type=int,default=16384)
    parser.add_argument("--json-out",type=Path)
    args=parser.parse_args()
    if args.rays<8192: parser.error("--rays must be at least 8192 to exercise a real CUDA batch")
    preflight={**asdict(cuda.preflight_gpu_cuda(refresh=True)), "provider_contract":cuda.PROVIDER_CONTRACT}
    ready=all(preflight.get(key) is True for key in ("available","strict_float64","kernel_executed","kernel_verified")) and preflight.get("preflight_scope")=="production_ray_bvh" and preflight.get("provider_contract")=="strict_float64_bvh_v1"
    files=["src/leakage_simulator/raytracer.py","src/leakage_simulator/escape_collection.py","src/leakage_simulator/raytrace_bridge.py","scripts/verify_escape_direction_accuracy.py"]
    report={"delivery":"source checkout","commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "source_sha256":{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files},
        "scope":"CUDA BVH intersections with shared CPU transport and direction reduction",
        "float64_tolerance":{"relative":1e-9,"absolute":1e-12},"preflight":preflight,
        "preflight_passed":ready,"cases":[],"passed":False}
    if ready:
        report["cases"]=[verify_case("rear_0",0,args.rays),verify_case("rear_45",45,args.rays),
            verify_case("rear_extreme_89_9",89.9,args.rays),verify_case("reflected",0,args.rays,True),verify_case("face_source",0,args.rays,face=True),verify_case("bsdf_reflected",0,args.rays,True,bsdf=True)]
        report["passed"]=all(case["passed"] for case in report["cases"])
    payload=json.dumps(report,ensure_ascii=False,indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True,exist_ok=True)
        args.json_out.write_text(payload,encoding="utf-8")
    print(payload)
    return 0 if report["passed"] else 2


if __name__=="__main__":
    raise SystemExit(main())
