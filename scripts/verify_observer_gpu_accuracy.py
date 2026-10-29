"""Observer CPU scalar/batch correctness and real CUDA parity/timing gate.

No installation, server restart, mock GPU, tolerance relaxation or CPU-as-GPU
success. A missing production preflight leaves GPU verification FAILED.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from leakage_simulator import gpu_cuda_intersection as cuda
from leakage_simulator.bsdf_guard import bind_bsdf_profiles
from leakage_simulator.escape_collection import EscapeCollector, DIRECTIONS
from leakage_simulator.geometry import TriangleMesh, vec_add, vec_mul
from leakage_simulator.observer_probe import trace_observer_paths
from leakage_simulator.optics import OpticalPropertyResolver
from leakage_simulator.raytracer import DirectRayTraceInput, run_direct_ray_trace
from leakage_simulator.types import EmitterSpec, OpticalProfile, RayTraceConfig, ReceiverSpec, ReceiverGrid

CASES = {
    "rear_0": "direct", "rear_45": "direct", "rear_85": "direct", "rear_89_9": "direct",
    "vertical_89_9": "direct", "compound_neg85_80": "direct", "face_emitter": "direct",
    "thin_emitter_extreme": "direct", "narrow_hole": "direct", "mirror_once": "reflected",
    "mirror_twice": "reflected", "diffuse": "reflected", "bsdf": "reflected",
    "blocked": "zero", "allowed": "excluded", "source_backside": "zero", "bsdf_zero_rt": "zero",
}


def quad(mesh, center, u=(1,0,0), v=(0,1,0), width=40, height=40, material="black"):
    points = [vec_add(center, vec_add(vec_mul(u, su*width/2), vec_mul(v, sv*height/2))) for su, sv in ((-1,-1),(1,-1),(1,1),(-1,1))]
    ids = [mesh.add_vertex(point) for point in points]
    return [mesh.add_face(ids[0], ids[1], ids[2], material), mesh.add_face(ids[0], ids[2], ids[3], material)]


def build_case(name, resolution=32, backend="cpu"):
    if name not in CASES:
        raise ValueError(name)
    mesh = TriangleMesh()
    reflection = name in ("mirror_once", "mirror_twice", "diffuse", "bsdf", "bsdf_zero_rt")
    source = EmitterSpec(emitter_id="source", emitter_type="datum_plane", center=(0,0,-5),
        u_axis=(1,0,0), v_axis=(0,1 if reflection else -1,0), width_mm=18, height_mm=18,
        power_lumen=1., ray_count=64, seed=1729)
    profiles = [OpticalProfile(profile_id="black", reflectance=0, absorption=1, scatter_model="none"),
                OpticalProfile(profile_id="mirror", reflectance=.7, absorption=.3,
                    scatter_model="lambertian" if name == "diffuse" else "specular")]
    assets = []
    if name == "mirror_twice":
        d = math.sqrt(.5)
        quad(mesh, (0,0,0), (d,0,d), width=4, height=16, material="mirror")
        quad(mesh, (5,0,0), (d,0,-d), width=4, height=16, material="mirror")
        source.center, source.width_mm = (5,0,-5), 4
    else:
        quad(mesh, (0,0,0 if reflection else 9), material="mirror" if reflection else "black")
    if name in ("bsdf", "bsdf_zero_rt"):
        from leakage_simulator.bsdf_import import import_lt_ascii_bsdf
        asset = import_lt_ascii_bsdf({"files": [
            {"name":"master.txt", "text":"#UDOP AngGridMultiple 1.0\nFileName = grid.txt\nMeshFormat = Polar\nMeshDataType = BSDF\nRAOI = 0\nLambda = 550\nPOI = 0\nIndex = 1\nIndexIncident = 1\n"},
            {"name":"grid.txt", "text":"SphereMesh: 3 2 0 0 360 90\n1 1 1\n1 1 1\n"},
            {"name":"rt.txt", "text":f"DFAT Version 1.0\nABSORBING: YES\nINDEX: 1\nDATAITEMS: TAVG RAVG\nwv 550\nAOI 0 0 {0 if name == 'bsdf_zero_rt' else .3}\n"},
        ]})
        asset["transport"] = {"model":"isotropic_no_shift", "wavelengthNm":550, "aoiExtrapolation":"clamp"}
        assets = [asset]
        profiles[1] = OpticalProfile(profile_id="mirror", reflectance=0, scatter_model="none", bsdf_asset_id=asset["id"])
    if name == "blocked":
        quad(mesh, (0,0,-8))
    if name == "narrow_hole":
        # A 0.4 mm-wide vertical slit in a genuinely opaque wall.
        quad(mesh, (-10.1,0,-7), width=19.8)
        quad(mesh, (10.1,0,-7), width=19.8)
    if name == "source_backside":
        source.normal_flip = True
    if name == "face_emitter":
        source.face_indices = quad(mesh, source.center, width=18, height=18)
        source.emitter_type, source.normal_flip = "face", True
    if name == "thin_emitter_extreme":
        source.width_mm = source.height_mm = .001
    detectors = []
    for face, axis in DIRECTIONS.items():
        normal = tuple(-x for x in axis)
        index = next(i for i, x in enumerate(axis) if x)
        u, v = [0.,0.,0.], [0.,0.,0.]
        u[(index+1)%3], v[(index+2)%3] = 1., 1.
        detectors.append(ReceiverSpec(receiver_id="__leak_preview_"+face, center=tuple(x*10 for x in axis), normal=normal,
            u_axis=tuple(u), v_axis=tuple(v), width_mm=20, height_mm=20, resolution=(8,8)))
    horizontal = {"rear_45":45, "rear_85":85, "rear_89_9":89.9, "compound_neg85_80":-85, "thin_emitter_extreme":89.9}.get(name, 0)
    vertical = {"vertical_89_9":89.9, "compound_neg85_80":80}.get(name, 0)
    spec = {"horizontal_deg":horizontal, "vertical_deg":vertical, "range_deg":0, "step_deg":1,
            "resolution":resolution, "samples_per_cell":2}
    return DirectRayTraceInput(mesh=mesh, emitters=[source], receivers=detectors, optical_profiles=profiles, bsdf_assets=assets,
        config=RayTraceConfig(ray_count=64, max_depth=4, store_ray_paths=False, compute_backend=backend, intersection_backend="bvh"),
        escape_collection={"minimum":[-10,-10,-10], "maximum":[10,10,10], "direction":"neg_z",
            "allowed_regions": [{"plane":"xy", "xMin":-10, "xMax":10, "yMin":-10, "yMax":10, "zMin":-7, "zMax":-7}] if name == "allowed" else []},
        observer_probe=spec)


def run_probe(trace, dispatch="batch", batch_size=8192):
    profiles, _ = bind_bsdf_profiles(trace.optical_profiles, trace.bsdf_assets, trace.config)
    resolved = OpticalPropertyResolver(trace.mesh, profiles, []).resolve_all()
    collector = EscapeCollector(trace.escape_collection, trace.receivers,
        {r.receiver_id: ReceiverGrid.empty(r) for r in trace.receivers})
    trace.mesh.prepare_acceleration()
    return trace_observer_paths(trace, collector, resolved, dispatch=dispatch, batch_size=batch_size)


def physical(summary):
    return {key:value for key,value in summary.items() if key not in
            ("performance", "runtime_sec", "execution_backend", "requested_backend")}


def differences(a, b, path=""):
    if type(a) is bool or type(b) is bool:
        return [] if a is b else [path]
    if isinstance(a, float) or isinstance(b, float):
        return [] if math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12) else [path]
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys(): return [path+":keys"]
        return [p for key in a for p in differences(a[key], b[key], path+"/"+str(key))]
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b): return [path+":length"]
        return [p for i,(x,y) in enumerate(zip(a,b)) for p in differences(x,y,path+"/"+str(i))]
    return [] if a == b else [path]


def acceptance(summary, expected):
    direct, reflected = summary["direct_count"], summary["reflected_count"]
    integrity = (summary["complete"] and not summary["stopped"] and summary["attempted_count"] == summary["planned_count"]
        and direct+reflected+summary["excluded_count"] <= summary["attempted_count"]
        and all(sum(cell[key] for cell in summary["cells"]) == summary[key] == sum(view[key] for view in summary["views"])
                for key in ("direct_count", "reflected_count")))
    physical_gate = (direct >= 30 and reflected == 0 if expected == "direct" else
        reflected >= 30 and direct == 0 if expected == "reflected" else
        direct+reflected == 0 and summary["excluded_count"] >= 30 if expected == "excluded" else direct+reflected == 0)
    return bool(integrity and physical_gate)


def gpu_proven(summary):
    perf = summary.get("performance", {})
    return (summary.get("sample_contract") == "observer_per_ray_seed_batch_v1"
        and summary.get("requested_backend") == "gpu_cuda"
        and summary.get("execution_backend") == "gpu_cuda"
        and perf.get("compute_execution_state") in ("gpu_active", "gpu_mixed")
        and perf.get("gpu_cuda_gpu_success_count", 0) > 0)


def verify_case(name, resolution, gpu_ready):
    trace = build_case(name, resolution)
    scalar = run_probe(trace, "scalar")
    cpu = [run_probe(trace) for _ in range(3)]
    issues = [p for result in cpu for p in differences(physical(scalar), physical(result))]
    result = {"name":name, "expected":CASES[name], "emitter_types":[e.emitter_type for e in trace.emitters],
        "settings":trace.observer_probe, "cpu_validation_passed":not issues and all(acceptance(s, CASES[name]) for s in [scalar, *cpu]),
        "cpu_differences":issues[:20], "cpu_scalar_sec":scalar["runtime_sec"], "cpu_first_warm_sec":[s["runtime_sec"] for s in cpu],
        "direct_count":scalar["direct_count"], "reflected_count":scalar["reflected_count"], "excluded_count":scalar["excluded_count"],
        "gpu_runs":[], "gpu_validation_passed":False}
    if gpu_ready:
        trace.config.compute_backend = "gpu_cuda"
        for repeat in range(3):
            actual = run_probe(trace)
            issues = differences(physical(cpu[0]), physical(actual))
            proven = gpu_proven(actual)
            result["gpu_runs"].append({"repeat":repeat+1, "runtime_sec":actual["runtime_sec"],
                "difference_count":len(issues), "differences":issues[:20], "gpu_execution_proven":proven,
                "acceptance_passed":acceptance(actual, CASES[name]), "performance":actual["performance"]})
        result["gpu_validation_passed"] = all(r["gpu_execution_proven"] and r["acceptance_passed"] and r["difference_count"] == 0 for r in result["gpu_runs"])
        cpu_warm = sum(s["runtime_sec"] for s in cpu[1:])/2
        gpu_warm = sum(s["runtime_sec"] for s in result["gpu_runs"][1:])/2
        result["warm_speedup_ratio"] = cpu_warm/gpu_warm if gpu_warm > 0 else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolution", type=int, default=32, choices=range(16,129), metavar="16..128")
    parser.add_argument("--case", action="append", choices=list(CASES))
    parser.add_argument("--cpu-only", action="store_true", help="Explicit CPU validation; never a GPU pass")
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    preflight = None if args.cpu_only else {**asdict(cuda.preflight_gpu_cuda(refresh=True)), "provider_contract":cuda.PROVIDER_CONTRACT}
    ready = bool(preflight and all(preflight.get(k) is True for k in ("available","strict_float64","kernel_executed","kernel_verified"))
        and preflight.get("preflight_scope") == "production_ray_bvh" and preflight.get("provider_contract") == "strict_float64_bvh_v1")
    files = ["src/leakage_simulator/observer_probe.py", "src/leakage_simulator/raytracer.py", "src/leakage_simulator/geometry.py",
             "src/leakage_simulator/gpu_cuda_intersection.py", "scripts/verify_observer_gpu_accuracy.py"]
    report = {"timestamp_utc":datetime.now(timezone.utc).isoformat(), "delivery":"Git source checkout",
        "commit":subprocess.check_output(["git","rev-parse","HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256":{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
        "scope":"Observer CAD intersections on CUDA; common CPU transport, source tests and BSDF support",
        "tolerance":{"relative":1e-9,"absolute":1e-12}, "positive_minimum_hits":30,
        "preflight":preflight, "preflight_passed":ready, "gpu_validation_status":"not_started", "cases":[], "passed":False}
    for name in args.case or CASES:
        print(f"Checking {name}...", flush=True)
        try:
            report["cases"].append(verify_case(name, args.resolution, ready))
        except Exception as exc:
            report["cases"].append({"name":name, "cpu_validation_passed":False, "gpu_validation_passed":False,
                "error":f"{type(exc).__name__}: {exc}"})
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["cpu_validation_passed"] = all(c["cpu_validation_passed"] for c in report["cases"])
    report["gpu_validation_status"] = "passed" if ready and all(c["gpu_validation_passed"] for c in report["cases"]) else "failed" if ready else "not_verified_preflight_failed" if not args.cpu_only else "not_requested_cpu_only"
    report["passed"] = report["cpu_validation_passed"] and report["gpu_validation_status"] == "passed"
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:report[k] for k in ("cpu_validation_passed", "gpu_validation_status", "passed")}, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
