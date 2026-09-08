"""Extract display samples from the three existing synthetic enclosure results.

This is stdlib-only result postprocessing, not another optical simulation. It
uses the fixture's known outer plane (Z=18 mm); it does not discover openings in
arbitrary CAD. STEP files are read only to record a current-file SHA-256 digest.

Usage:
    python scripts/extract_closed_enclosure_leakage_samples.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CASES = (("closed", 0.0), ("gap_0p2", 0.2), ("gap_0p5", 0.5))
OUTER_Z_MM = 18.0
GEOMETRY_TOLERANCE_MM = 1e-6


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"))
    require(isinstance(value, dict), f"Expected a JSON object: {path.name}")
    return value, raw


def vector(value: Any, label: str) -> list[float]:
    require(isinstance(value, list) and len(value) == 3, f"{label}: expected XYZ")
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) for v in value), f"{label}: non-finite XYZ")
    return [float(v) for v in value]


def aperture_rectangles(gap_mm: float) -> list[dict[str, list[float]]]:
    if gap_mm == 0:
        return []
    return [
        {"x_mm": [92.0, 112.0 + gap_mm], "y_mm": [8.0 - gap_mm, 8.0]},
        {"x_mm": [112.0, 112.0 + gap_mm], "y_mm": [8.0 - gap_mm, 28.0]},
    ]


def in_known_aperture(point: list[float], gap_mm: float) -> bool:
    epsilon = GEOMETRY_TOLERANCE_MM
    if abs(point[2] - OUTER_Z_MM) > epsilon:
        return False
    return any(
        rectangle["x_mm"][0] - epsilon <= point[0] <= rectangle["x_mm"][1] + epsilon
        and rectangle["y_mm"][0] - epsilon <= point[1] <= rectangle["y_mm"][1] + epsilon
        for rectangle in aperture_rectangles(gap_mm)
    )


def extract_case(sample_dir: Path, case_name: str, gap_mm: float) -> dict[str, Any]:
    result_path = sample_dir / f"{case_name}.result.json"
    scene_path = sample_dir / f"{case_name}.scene.json"
    step_path = sample_dir / f"{case_name}.step"
    result, result_raw = load_json(result_path)
    scene, scene_raw = load_json(scene_path)
    require(scene.get("schema_version") == "mesh-scene.v1", f"{case_name}: unsupported scene")
    require(scene.get("units", {}).get("length") == "mm", f"{case_name}: expected mm geometry")
    run_id = result.get("run_id")
    require(isinstance(run_id, str) and bool(run_id), f"{case_name}: missing run_id")
    stored_paths = result.get("stored_paths")
    require(isinstance(stored_paths, list), f"{case_name}: missing stored_paths")
    samples: list[dict[str, Any]] = []
    stored_receiver_paths = 0
    for path_index, path in enumerate(stored_paths):
        require(isinstance(path, list), f"{case_name}: invalid stored path {path_index}")
        if not path or path[-1].get("event_type") != "receiver":
            continue
        stored_receiver_paths += 1
        label = f"{case_name}/stored_paths/{path_index}"
        require(len(path) >= 2, f"{label}: terminal segment is missing")
        previous, terminal = path[-2], path[-1]
        require(previous.get("event_type") in {"emitter", "surface"},
                f"{label}: unexpected event before receiver")
        start = vector(previous.get("point"), f"{label}/start")
        end = vector(terminal.get("point"), f"{label}/end")
        delta = [end[axis] - start[axis] for axis in range(3)]
        segment_length = math.hypot(*delta)
        require(math.isfinite(segment_length) and segment_length > 0,
                f"{label}: degenerate segment")
        direction = [value / segment_length for value in delta]
        require(abs(math.hypot(*direction) - 1.0) < 1e-12,
                f"{label}: direction is not a unit vector")
        require(delta[2] > 0, f"{label}: segment does not leave through the known front plane")
        fraction = (OUTER_Z_MM - start[2]) / delta[2]
        require(0.0 <= fraction <= 1.0, f"{label}: outer plane is outside terminal segment")
        exit_point = [start[axis] + fraction * delta[axis] for axis in range(3)]
        exit_point[2] = OUTER_Z_MM
        require(in_known_aperture(exit_point, gap_mm), f"{label}: exit is outside known L-shaped gap")
        energy = terminal.get("incoming_energy_lumen")
        require(isinstance(energy, (float, int)) and not isinstance(energy, bool)
                and math.isfinite(energy) and energy >= 0, f"{label}: invalid relative weight")
        receiver_id = terminal.get("receiver_id")
        require(isinstance(receiver_id, str) and bool(receiver_id), f"{label}: missing receiver ID")
        samples.append({
            "sample_id": f"{run_id}:stored-path:{path_index}",
            "run_id": run_id,
            "stored_path_index": path_index,
            "receiver_id": receiver_id,
            "exit_point_mm": exit_point,
            "outgoing_direction_unit": direction,
            "incoming_energy_lumen": float(energy),
            "terminal_segment_start_mm": start,
            "terminal_segment_end_mm": end,
            "exit_segment_fraction": fraction,
            "last_event_type": previous.get("event_type"),
            "last_component_id": previous.get("component_id"),
            "reflection_depth": terminal.get("depth"),
            "ray_kind": terminal.get("ray_kind"),
        })

    total_hits = result.get("receiver_hit_count")
    require(isinstance(total_hits, int) and total_hits >= 0, f"{case_name}: invalid hit count")
    require(stored_receiver_paths <= total_hits, f"{case_name}: stored paths exceed hit count")
    require(len(samples) == stored_receiver_paths, f"{case_name}: silently omitted receiver paths")
    if gap_mm == 0:
        require(total_hits == 0 and not samples, "Closed control must have zero external receiver hits/samples")
    else:
        require(bool(samples), f"{case_name}: expected nonempty fixture leakage samples")

    mesh = scene.get("mesh", {})
    geometry_snapshot = {
        key: mesh.get(key) for key in
        ("vertices", "faces", "face_component_ids", "face_source_ids")
    }
    geometry_bytes = json.dumps(geometry_snapshot, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).encode("utf-8")
    return {
        "case_id": case_name,
        "run_id": run_id,
        "gap_mm": gap_mm,
        "known_aperture": {"plane_z_mm": OUTER_Z_MM, "union_rectangles": aperture_rectangles(gap_mm)},
        "source_files": {"result": result_path.name, "scene": scene_path.name, "step": step_path.name},
        "source_digests_at_extraction": {
            "algorithm": "sha256",
            "result_file": hashlib.sha256(result_raw).hexdigest(),
            "scene_file": hashlib.sha256(scene_raw).hexdigest(),
            "step_file": hashlib.sha256(step_path.read_bytes()).hexdigest(),
            "viewer_geometry_snapshot": hashlib.sha256(geometry_bytes).hexdigest(),
            "scope": "Current extraction inputs only; original trace-to-CAD association is not verified by these digests.",
        },
        "counts": {
            "total_traced_rays": result.get("total_rays"),
            "total_receiver_hits": total_hits,
            "stored_paths": len(stored_paths),
            "stored_receiver_terminal_paths": stored_receiver_paths,
            "extracted_exit_samples": len(samples),
        },
        "source_sampling": {
            key: result.get("config", {}).get(key) for key in
            ("store_ray_paths", "max_stored_paths", "primary_sampling_strategy", "bounce_sampling_strategy", "seed")
        },
        "validation": {
            "all_stored_receiver_paths_extracted": True,
            "all_exit_points_in_known_aperture": True,
            "all_directions_finite_and_unit_length": True,
            "all_weights_finite_and_nonnegative": True,
            "all_exit_points_between_segment_endpoints": True,
            "closed_control_zero": True if gap_mm == 0 else None,
            "geometry_tolerance_mm": GEOMETRY_TOLERANCE_MM,
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, default=ROOT / "samples" / "closed_enclosure")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    sample_dir = args.sample_dir.resolve()
    output = args.output.resolve() if args.output else sample_dir / "leakage_preview_samples.json"
    cases = [extract_case(sample_dir, name, gap) for name, gap in CASES]
    artifact = {
        "schema_version": "leakage-preview-samples.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Intersection of stored receiver-terminal segments with the synthetic fixture's known Z=18 mm exterior plane.",
        "units": {"position": "mm", "direction": "unit vector", "raw_weight": "incoming_energy_lumen"},
        "scope": {
            "synthetic_fixtures_only": True,
            "automatic_exit_detection_for_general_cad": False,
            "transport_rerun": False,
            "input_population": "Stored paths ending at a receiver only; other escaping rays and capped-away paths are absent.",
            "sample_selection": "Receiver-priority bounded path store; not an unbiased spatial or angular sample.",
            "weight_interpretation": "Raw terminal incoming ray power before receiver cosine weighting; usable only as a sample-relative display weight.",
            "forbidden_interpretations": [
                "camera luminance or nit image",
                "complete all-angle leakage reconstruction",
                "absence of a stored sample proves absence of leakage",
                "sum of raw sample weights measures total leakage or compares models' total light output",
                "known fixture plane method detects real product openings automatically",
            ],
            "normalization": "None. No raw-weight sum or full-population scaling is computed.",
            "geometry_association": "Current scene/STEP digests record extraction inputs, not the original result's geometry provenance.",
        },
        "cases": cases,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for case in cases:
        print(json.dumps({"case": case["case_id"], **case["counts"], "validation_passed": True}))
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
