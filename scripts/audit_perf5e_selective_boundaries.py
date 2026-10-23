import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    checks = [report["passed"] is True, "error" not in report, report["production_enabled"] is False,
              report["performance_acceptance_eligible"] is False,
              bool(report["source_before"]) and report["source_before"] == report["source_after"]]
    groups = {}
    first = {}
    totals = dict.fromkeys(("queries", "replay_rows", "winner_only_rows", "guarded_queries",
                           "missing_reference_visits", "raw_face_mismatches", "corrected_face_mismatches",
                           "corrected_distance_bit_mismatches"), 0)
    for record in report["records"]:
        identity = (record["scene"], record["backend"])
        groups.setdefault(identity, []).append(record["repeat"])
        artifact = record["artifact"]
        checks.append(digest(artifact["path"]) == artifact["sha256"])
        with np.load(artifact["path"], allow_pickle=False) as arrays:
            candidate, reference, distance = arrays["candidate"], arrays["reference_face"], arrays["reference_distance"]
            count = len(arrays["rays"])
            fields = ("candidate", "reference_face", "reference_distance", "corrected_face", "corrected_distance",
                      "replay", "rejected", "visited")
            if any(arrays[field].shape != (count,) for field in fields):
                checks.append(False)
                continue
            repeated = tuple(arrays[field].tobytes() for field in (
                "rays", "reference_face", "reference_distance", "triangle_v0", "triangle_edge1",
                "triangle_edge2", "traceable_face_mask",
            ))
            first.setdefault(identity, repeated)
            checks.append(first[identity] == repeated)
            guarded = (candidate["flags"] & 4) != 0
            replay = (candidate["flags"] != 0) | (candidate["face"] < 0) | arrays["rejected"]
            checks.append(np.array_equal(replay, arrays["replay"]))
            values = {
                "queries": count, "replay_rows": int(replay.sum()), "winner_only_rows": int((~replay).sum()),
                "guarded_queries": int(guarded.sum()),
                "missing_reference_visits": int(np.count_nonzero((reference >= 0) & ~guarded & ((arrays["visited"] & 1) == 0))),
                "raw_face_mismatches": int(np.count_nonzero(candidate["face"] != reference)),
                "corrected_face_mismatches": int(np.count_nonzero(arrays["corrected_face"] != reference)),
                "corrected_distance_bit_mismatches": int(np.count_nonzero(arrays["corrected_distance"].view(np.uint64) != distance.view(np.uint64))),
            }
            for key, value in values.items():
                checks.append(record[key] == value)
                totals[key] += value
            checks.extend((values["corrected_face_mismatches"] == 0, values["corrected_distance_bit_mismatches"] == 0,
                           record["same_mode_repeat_exact"] is True, record["provider_call_completed"] is True))
    checks.append(len(groups) == 36)
    for samples in groups.values():
        checks.append(len(samples) >= 3 and sorted(samples) == list(range(len(samples))))
    return {"report_sha256": digest(path), "auditor_sha256": digest(__file__), "records": len(report["records"]),
            "checks": len(checks), "failures": checks.count(False), "passed": all(checks), **totals,
            "scope": "raw_query_preservation_not_physical_or_complete_coverage"}


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
