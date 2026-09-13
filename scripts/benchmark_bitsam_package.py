from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.api.runtime import ApiRuntime
from leakage_simulator.bitsam_package import mesh_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cad", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "bitsam-package-benchmark.json")
    args = parser.parse_args()
    report = {"cad_name": args.cad.name, "cad_bytes": args.cad.stat().st_size, "compute_backend": "cpu"}
    with tempfile.TemporaryDirectory(prefix="bitsam-benchmark-") as directory:
        root = Path(directory)
        runtime = ApiRuntime(root / "before")
        started = time.perf_counter()
        scene = runtime.load_scene(str(args.cad.resolve()))
        report["cad_import_sec"] = time.perf_counter() - started
        mesh = scene["mesh"]
        bounds_min = [min(vertex[axis] for vertex in mesh["vertices"]) for axis in range(3)]
        bounds_max = [max(vertex[axis] for vertex in mesh["vertices"]) for axis in range(3)]
        center = [(lower + upper) / 2 for lower, upper in zip(bounds_min, bounds_max)]
        request = {
            "scene_token": scene["metadata"]["scene_token"],
            "emitters": [{"emitter_id": "source", "emitter_type": "datum_plane",
                          "center": [center[0], center[1], bounds_max[2] + 1], "u_axis": [1, 0, 0], "v_axis": [0, 1, 0],
                          "width_mm": 1, "height_mm": 1, "direction_distribution": "gaussian", "gaussian_sigma_deg": 1,
                          "power_lumen": 1, "ray_count": 200, "seed": 42}],
            "receivers": [{"receiver_id": "observer", "center": [center[0], center[1], bounds_max[2] + 10],
                           "normal": [0, 0, -1], "width_mm": 20, "height_mm": 20, "resolution": [8, 8]}],
            "config": {"compute_backend": "cpu", "intersection_backend": "bvh", "max_depth": 2, "seed": 42, "max_stored_paths": 12},
        }
        started = time.perf_counter()
        baseline = runtime.run_raytrace_direct(request)
        report["initial_trace_with_preparation_sec"] = time.perf_counter() - started
        report["original_warm_trace_sec"] = []
        for _ in range(2):
            started = time.perf_counter()
            warm_result = runtime.run_raytrace_direct(request)
            report["original_warm_trace_sec"].append(time.perf_counter() - started)
            assert warm_result["receiver_grids"] == baseline["receiver_grids"]
        report["viewer_faces"] = len(mesh["faces"])
        trace = runtime._scene_mesh_for_request(request)
        report["trace_faces"] = len(trace["faces"])
        project = {
            "format": "tv-leakage-simulator-project", "schema_version": "bitsam-project.v1",
            "project_name": args.cad.stem,
            "cad": {"display_name": args.cad.name, "fingerprint": {
                key: scene["metadata"][key] for key in ("face_count", "vertex_count", "component_count")
            }},
            "workspace": {"emitters": request["emitters"], "receivers": request["receivers"], "rayTraceConfig": request["config"]},
            "analysis_result": baseline,
        }
        started = time.perf_counter()
        saved = runtime.export_project({"scene_token": request["scene_token"], "project": project})
        report["save_sec"] = time.perf_counter() - started
        report["package_bytes"] = saved["size_bytes"]
        package, _ = runtime.project_export_file(saved["download_url"].split("/")[-1])
        report["restore_runs"] = []
        for index in range(3):
            loader = Mock(side_effect=AssertionError("CAD was parsed during cache restore"))
            restored_runtime = ApiRuntime(root / ("after-" + str(index)), scene_loader=loader)
            started = time.perf_counter()
            restored = restored_runtime.import_project(package)
            restored_scene = restored_runtime.load_scene(restored["cad"]["path"])
            load_sec = time.perf_counter() - started
            assert mesh_identity(restored_scene["mesh"]) == mesh_identity(mesh)
            request["scene_token"] = restored_scene["metadata"]["scene_token"]
            assert mesh_identity(restored_runtime._scene_mesh_for_request(request)) == mesh_identity(trace)
            assert restored["project"]["analysis_result"] == json.loads(json.dumps(baseline))
            started = time.perf_counter()
            repeated = restored_runtime.run_raytrace_direct(request)
            trace_sec = time.perf_counter() - started
            assert repeated["receiver_grids"] == baseline["receiver_grids"]
            assert repeated["stored_paths"] == baseline["stored_paths"]
            warm_times = []
            for _ in range(2):
                started = time.perf_counter()
                warm_result = restored_runtime.run_raytrace_direct(request)
                warm_times.append(time.perf_counter() - started)
                assert warm_result["receiver_grids"] == baseline["receiver_grids"]
            report["restore_runs"].append({"load_sec": load_sec, "first_trace_with_bvh_sec": trace_sec, "warm_trace_sec": warm_times})
            loader.assert_not_called()
        report["exact_geometry_results_and_retrace_match"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
