from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "scripts")]

from leakage_simulator.geometry import RayBatch, TriangleMesh
from tools.perf5e_native.transport_validation import production_preflight_gate
from verify_gpu_cuda_runtime import verify_device
from verify_perf5e_transport import sha256, write_json


def cutoff_fixture(order):
    heights = (5.0 + 1.5e-10, 5.0 + 0.75e-10, 5.0)
    mesh = TriangleMesh()
    for index in order:
        vertices = [mesh.add_vertex(point) for point in (
            (-1.0, -1.0, heights[index]), (1.0, -1.0, heights[index]), (0.0, 1.0, heights[index]),
        )]
        mesh.add_face(*vertices, "tie_control")
    original = RayBatch([[0.0, 0.0, 0.0]], [[0.0, 0.0, 1.0]], 1e-8, np.inf)
    bounded = RayBatch(original.origins.copy(), original.directions.copy(), 1e-8, 5.0 + 1e-10)
    return mesh, original, bounded


def evaluate(backend):
    records = []
    for order in itertools.permutations(range(3)):
        mesh, original, bounded = cutoff_fixture(order)
        query = mesh.intersect_rays_gpu_cuda if backend == "gpu_cuda" else mesh.intersect_rays_native_cpu
        first = None
        for repeat in range(3):
            full, execution = query(original, backend="bvh")
            clipped, _ = query(bounded, backend="bvh")
            current = full.face_indices.tobytes(), full.t.tobytes(), clipped.face_indices.tobytes(), clipped.t.tobytes()
            if first is None:
                first = current
            if first != current:
                raise AssertionError("Cutoff control is not repeatable")
            records.append({"backend": backend, "order": list(order), "repeat": repeat,
                            "original_face": int(full.face_indices[0]), "bounded_face": int(clipped.face_indices[0]),
                            "original_distance": float(full.t[0]), "bounded_distance": float(clipped.t[0]),
                            "original_distance_bits": int(full.t.view(np.uint64)[0]),
                            "bounded_distance_bits": int(clipped.t.view(np.uint64)[0]),
                            "provider_contract": getattr(execution, "provider_contract", None),
                            "device": getattr(execution, "device_name", None), "repeat_exact": True})
    witness = next(record for record in records if record["order"] == [0, 1, 2])
    if witness["original_face"] != 2 or witness["bounded_face"] != 1:
        raise AssertionError("Expected near-tie cutoff counterexample was not reproduced")
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__), ROOT / "src/leakage_simulator/gpu_cuda_intersection.py",
               ROOT / "src/leakage_simulator/native_cpu_intersection.py", ROOT / "src/leakage_simulator/geometry.py"]
    identity = lambda: {str(path.relative_to(ROOT)): sha256(path) for path in sources}
    report = {"contract": "unsafe_near_tie_cutoff_counterexample_v1", "records": [], "passed": False,
              "source_before": identity(), "source_after": None,
              "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "delivery": "source_checkout_existing_run_web_gpu_environment",
              "scope": "direct_intersection_not_product_transport_or_physical_accuracy",
              "acceptance_gates": {"witness": "full face 2 versus bounded face 1", "repeat": "bit_exact"},
              "performance_acceptance_eligible": False, "production_enabled": False}
    try:
        if args.gpu:
            report["preflight"] = verify_device()
            if not production_preflight_gate(report["preflight"]):
                raise RuntimeError("Production preflight failed")
        report["records"].extend(evaluate("cpu"))
        if args.gpu:
            report["records"].extend(evaluate("gpu_cuda"))
        report["passed"] = True
    finally:
        report["source_after"] = identity()
        report["passed"] = report["passed"] and report["source_before"] == report["source_after"]
        write_json(args.output / "report.json", report)
    print(json.dumps({"passed": report["passed"], "records": len(report["records"]),
                      "counterexamples": sum(row["original_face"] != row["bounded_face"] for row in report["records"])}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
