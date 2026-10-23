import argparse
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_perf5e_transport import DEVICE_V2, HIT_DTYPE, RAY_DTYPE, audit_device_identity, file_hash, valid_rows


DTYPES = {"rays": RAY_DTYPE, "candidate": HIT_DTYPE, "replay": np.dtype("u1"), "rejected": np.dtype("u1"),
          "corrected_face": np.dtype("<i8"), "reference_face": np.dtype("<i8"),
          "corrected_distance": np.dtype("<f8"), "reference_distance": np.dtype("<f8")}
EXPECTED_SCENES = {
    "gap_0_far_first", "gap_1e-12_far_first", "gap_1e-10_far_first", "gap_1e-08_far_first",
    "gap_1e-05_far_first", "gap_1_far_first", "rotated_translated", "large_offset_small_triangles",
    "very_large_offset", "trace_excluded_front", "ignore_front_triangle", "finite_limit_miss",
    "maximum_inclusive", "minimum_exclusive", "empty_interval", "tiny_direction_guard",
    "oblique_incidence", "distant_origin_guard",
}


def audit_matrix(report):
    count = report.get("rays_per_full_query")
    if type(count) is not int or count < 32:
        return [False]
    expected = (count, count, count, 17, 0, count)
    groups = {}
    checks = []
    for record in report["records"]:
        groups.setdefault(record["scene"], []).append(record)
        checks.append(record.get("capacity") == count)
    checks.append(set(groups) == EXPECTED_SCENES)
    for records in groups.values():
        checks.extend(([row["repeat"] for row in records] == list(range(6)),
                       tuple(row["rays"] for row in records) == expected))
    return checks


def audit_rows(arrays, record):
    count = record["rays"]
    if type(count) is not int or count < 0 or not valid_rows(arrays, count, DTYPES):
        return [False]
    replay, rejected, candidate = arrays["replay"], arrays["rejected"], arrays["candidate"]
    checks = [
        np.isin(replay, (0, 1)).all(), np.isin(rejected, (0, 1)).all(),
        np.array_equal(replay.astype(bool), (candidate["flags"] != 0) | (candidate["face"] < 0) | rejected.astype(bool)),
        np.array_equal(arrays["corrected_face"], arrays["reference_face"]),
        arrays["corrected_distance"].tobytes() == arrays["reference_distance"].tobytes(),
        record["replay_rows"] == int(replay.sum()), record["winner_only_rows"] == count - int(replay.sum()),
        record["refinement_rejected_rows"] == int(rejected.sum()),
        record["checked_rows"] == record["full_oracle_rows"] == count,
        all(record[key] == 0 for key in ("invalid_rows", "face_mismatches", "distance_bit_mismatches", "overflow_rows",
                                       "ray_upload_bytes", "hit_download_bytes")),
        record["status_download_bytes"] == 56,
        record["input_unchanged"] is True, record["workspace_reused"] is True,
        record["producer_stream"] != record["consumer_stream"],
        record["production_enabled"] is False, record["production_coverage_proven"] is False,
        record["performance_acceptance_eligible"] is False,
    ]
    if record.get("contract") == "experimental_device_selective_replay_shared_oracle_v2":
        checks.extend((record.get("canonical_query_rows") == count,
                       record.get("canonical_launches") == int(count > 0),
                       record.get("observed_canonical_launches") == int(count > 0),
                       record.get("duplicate_replay_rows_avoided") == int(replay.sum()),
                       record.get("workspace_bytes_saved_vs_v1") == 25 * record.get("capacity", -1),
                       record.get("oracle_strategy") == "one_full_canonical_pass_shared_with_replay"))
    return checks


def audit(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    checks = [report["contract"] in ("experimental_device_selective_replay_full_oracle_v1",
                                     "experimental_device_selective_replay_shared_oracle_v2"),
              report["passed"] is True, "error" not in report, report["production_enabled"] is False,
              report["production_coverage_proven"] is False, report["performance_acceptance_eligible"] is False,
              bool(report["source_before"]) and report["source_before"] == report["source_after"]]
    preflight = report["preflight"]
    checks.extend(preflight.get(key) is True for key in ("available", "strict_float64", "kernel_executed", "kernel_verified"))
    checks.extend((preflight.get("preflight_scope") == "production_ray_bvh",
                   preflight.get("provider_contract") == "strict_float64_bvh_v1"))
    if report["contract"] == DEVICE_V2:
        checks.extend(audit_device_identity(report))
        checks.extend(audit_matrix(report))
    groups, first = {}, {}
    for record in report["records"]:
        checks.append(record.get("contract") == report["contract"])
        groups.setdefault(record["scene"], []).append(record["repeat"])
        artifact = record["artifact"]
        checks.append(file_hash(artifact["path"]) == artifact["sha256"])
        with np.load(artifact["path"], allow_pickle=False) as arrays:
            checks.extend(audit_rows(arrays, record))
            if record["repeat"] in (0, 1, 2, 5):
                repeated = tuple((arrays[field].dtype.descr, arrays[field].shape, arrays[field].tobytes()) for field in DTYPES)
                first.setdefault(record["scene"], repeated)
                checks.append(first[record["scene"]] == repeated)
    checks.append(len(groups) == 18)
    checks.extend(samples == list(range(6)) for samples in groups.values())
    faults = {item["case"]: item for item in report["fault_checks"]}
    expected_faults = 7 if report["contract"] == "experimental_device_selective_replay_shared_oracle_v2" else 5
    checks.extend((len(faults) == len(report["fault_checks"]) == expected_faults,
                   faults["wrong_unflagged_far_hit"].get("raised") is True,
                   faults["unknown_flags"].get("raised") is True,
                   faults["all_candidates_missing"].get("all_replayed_exact") is True,
                   faults["launch_failure_cleanup"].get("synchronized_before_raise") is True,
                   faults["recovery_after_failure"].get("exact") is True))
    if expected_faults == 7:
        checks.extend((faults["winner_distance_bit_corruption"].get("raised") is True,
                       faults["canonical_stack_overflow"].get("raised") is True))
    return {"passed": bool(checks) and all(checks), "checks": len(checks), "failures": checks.count(False),
            "records": len(report["records"]), "report_sha256": file_hash(path), "auditor_sha256": file_hash(__file__),
            "scope": "device_query_raw_evidence_not_physical_or_performance_or_coverage_proof",
            **{key: sum(record[key] for record in report["records"]) for key in (
                "checked_rows", "replay_rows", "winner_only_rows", "refinement_rejected_rows", "face_mismatches",
                "distance_bit_mismatches", "ray_upload_bytes", "hit_download_bytes", "status_download_bytes")}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
