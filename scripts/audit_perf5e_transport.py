from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


RAY_DTYPE = np.dtype([
    ("origin", "<f8", 3), ("direction", "<f8", 3),
    ("minimum", "<f8"), ("maximum", "<f8"), ("ignored_face", "<i8"),
])
HIT_DTYPE = np.dtype([
    ("distance", "<f8"), ("second_distance", "<f8"), ("face", "<i8"),
    ("flags", "<i8"), ("candidates", "<i8"), ("intersections", "<i8"),
])
ORIGINAL_DTYPES = {"rays": RAY_DTYPE, "candidate": HIT_DTYPE, "reference_face": np.dtype("<i8"),
                   "reference_distance": np.dtype("<f8"), "visited": np.dtype("u1")}
SELECTIVE_DTYPES = {**{key: value for key, value in ORIGINAL_DTYPES.items() if key != "visited"},
                    "corrected_face": np.dtype("<i8"), "corrected_distance": np.dtype("<f8"),
                    "replay": np.dtype("bool"), "rejected": np.dtype("bool")}
DEVICE_V2 = "experimental_device_selective_replay_shared_oracle_v2"
DEVICE_V2_SOURCES = {
    "adapter": ("tools/perf5e_native/device_replay.py", "c5d89965c26651273eb03efb86dd309fda94fc813eb727d1761a97c0fe8316ed"),
    "observer": ("tools/perf5e_native/device_transport.py", "96acc121bf9b7ccccb28c73136ae8d3e6c31d2e667d44c56774eb6404ad5bd70"),
}


def audit_device_identity(report):
    identity = report.get("device_implementation", {})
    checks = [identity.get("contract") == DEVICE_V2]
    for role, (suffix, expected) in DEVICE_V2_SOURCES.items():
        checks.append(identity.get(f"{role}_sha256") == expected)
        for phase in ("source_before", "source_after"):
            matches = [value for path, value in report.get(phase, {}).items()
                       if path.replace("\\", "/").endswith("/" + suffix)]
            checks.append(matches == [expected])
    return checks


def audit_device_batch(batch, count):
    metadata = batch.get("device_adapter")
    if batch.get("refinement_backend") == "cpu":
        return [metadata is None]
    if batch.get("refinement_backend") != "gpu_cuda" or not isinstance(metadata, dict):
        return [False]
    return [type(count) is int and count > 0, metadata.get("contract") == DEVICE_V2,
            metadata.get("oracle_strategy") == "one_full_canonical_pass_shared_with_replay",
            metadata.get("checked_rows") == metadata.get("full_oracle_rows") == metadata.get("canonical_query_rows") == count,
            metadata.get("canonical_launches") == int(count > 0),
            metadata.get("duplicate_replay_rows_avoided") == batch.get("replay_rows"),
            *[metadata.get(key) == batch.get(key) for key in ("replay_rows", "winner_only_rows", "refinement_rejected_rows")],
            *[metadata.get(key) == 0 for key in ("invalid_rows", "face_mismatches", "distance_bit_mismatches",
                                                "overflow_rows", "ray_upload_bytes", "hit_download_bytes")],
            metadata.get("status_download_bytes") == 56,
            metadata.get("production_enabled") is False, metadata.get("production_coverage_proven") is False,
            metadata.get("performance_acceptance_eligible") is False]


def valid_rows(arrays, count, dtypes):
    return all(field in arrays and arrays[field].shape == (count,) and arrays[field].dtype == dtype
               for field, dtype in dtypes.items())


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalized_result(payload):
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return json.dumps(payload, sort_keys=True, allow_nan=False)


def audit_selective(batch, original_artifact):
    artifact = batch["selective_artifact"]
    checks = [file_hash(artifact["path"]) == artifact["sha256"]]
    with np.load(artifact["path"], allow_pickle=False) as arrays, np.load(original_artifact["path"], allow_pickle=False) as original:
        candidate = arrays["candidate"]
        replay, rejected = arrays["replay"], arrays["rejected"]
        if original["rays"].ndim != 1:
            return checks + [False], {}
        count = len(original["rays"])
        if not valid_rows(original, count, ORIGINAL_DTYPES):
            return checks + [False], {}
        if not valid_rows(arrays, count, SELECTIVE_DTYPES):
            return checks + [False], {}
        if arrays["backend"].shape != () or arrays["backend"].dtype.kind != "U":
            return checks + [False], {}
        expected_replay = (candidate["flags"] != 0) | (candidate["face"] < 0) | rejected
        face_errors = int(np.count_nonzero(arrays["corrected_face"] != arrays["reference_face"]))
        distance_errors = int(np.count_nonzero(arrays["corrected_distance"].view(np.uint64) != arrays["reference_distance"].view(np.uint64)))
        checks.extend((
            arrays["rays"].tobytes() == original["rays"].tobytes(),
            candidate.tobytes() == original["candidate"].tobytes(),
            arrays["reference_face"].tobytes() == original["reference_face"].tobytes(),
            arrays["reference_distance"].tobytes() == original["reference_distance"].tobytes(),
            replay.dtype == np.bool_ and rejected.dtype == np.bool_,
            np.array_equal(replay, expected_replay),
            str(arrays["backend"]) == batch["refinement_backend"] in ("cpu", "gpu_cuda"),
            face_errors == batch["corrected_face_mismatches"] == 0,
            distance_errors == batch["corrected_distance_bit_mismatches"] == 0,
        ))
        totals = {"replay_rows": int(replay.sum()), "winner_only_rows": int((~replay).sum()),
                  "refinement_rejected_rows": int(rejected.sum()),
                  "corrected_face_mismatches": face_errors, "corrected_distance_bit_mismatches": distance_errors}
        checks.extend(batch[key] == value for key, value in totals.items())
    return checks, totals


def audit(report_path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks = [bool(report["records"]), report.get("passed") is True, "error" not in report]
    first = {}
    repeats = {}
    totals = {key: 0 for key in ("queries", "missing_reference_visits", "raw_face_mismatches", "unflagged_mismatches")}
    selective = report.get("contract") in ("experimental_optix_selective_replay_oracle_checked_v1",
                                          "experimental_device_selective_replay_full_oracle_v1",
                                          "experimental_device_selective_replay_shared_oracle_v2")
    selective_totals = dict.fromkeys(("replay_rows", "winner_only_rows", "refinement_rejected_rows",
                                    "corrected_face_mismatches", "corrected_distance_bit_mismatches"), 0)
    device_v2 = report.get("contract") == DEVICE_V2
    if device_v2:
        checks.extend(audit_device_identity(report))
    device_calls_total = 0
    for record in report["records"]:
        results = {}
        for mode, artifact in record["results"].items():
            checks.append(file_hash(artifact["path"]) == artifact["sha256"])
            results[mode] = normalized_result(json.loads(Path(artifact["path"]).read_text(encoding="utf-8")))
        checks.append(results["baseline"] == results["shadow"])
        identity = (record["scene"], record["backend"])
        repeats.setdefault(identity, []).append(record["repeat"])
        first.setdefault(identity, results["baseline"])
        checks.append(first[identity] == results["baseline"])
        recalculated = dict.fromkeys(totals, 0)
        selective_recalculated = dict.fromkeys(selective_totals, 0)
        device_calls, device_rows = 0, 0
        for batch in record["observation"]["records"]:
            artifact = batch["artifact"]
            checks.extend((file_hash(artifact["path"]) == artifact["sha256"],
                           file_hash(artifact["scene"]["path"]) == artifact["scene"]["sha256"]))
            with np.load(artifact["path"], allow_pickle=False) as arrays:
                if arrays["rays"].ndim != 1:
                    checks.append(False)
                    continue
                count = len(arrays["rays"])
                if not valid_rows(arrays, count, ORIGINAL_DTYPES):
                    checks.append(False)
                    continue
                candidate = arrays["candidate"]
                reference = arrays["reference_face"]
                guarded = (candidate["flags"] & 4) != 0
                face_mismatch = candidate["face"] != reference
                distance_mismatch = ~np.isclose(candidate["distance"], arrays["reference_distance"], rtol=1e-12, atol=1e-12)
                missing = (reference >= 0) & ~guarded & ((arrays["visited"] & 1) == 0)
                raw = {
                    "queries": len(reference), "missing_reference_visits": int(np.count_nonzero(missing)),
                    "raw_face_mismatches": int(np.count_nonzero(face_mismatch)),
                    "unflagged_mismatches": int(np.count_nonzero((face_mismatch | distance_mismatch) & (candidate["flags"] == 0))),
                }
                for key, value in raw.items():
                    checks.append(batch[key] == value)
                    recalculated[key] += value
            if device_v2:
                checks.extend(audit_device_batch(batch, count))
                if batch.get("refinement_backend") == "gpu_cuda":
                    device_calls += 1
                    device_rows += count
            if selective:
                batch_checks, batch_totals = audit_selective(batch, artifact)
                checks.extend(batch_checks)
                for key, value in batch_totals.items():
                    selective_recalculated[key] += value
        if device_v2:
            observation = record["observation"]
            checks.extend((observation.get("device_adapter_calls") == device_calls,
                           observation.get("device_adapter_rows") == device_rows,
                           observation.get("device_adapter_scope") == "device_query_inside_host_roundtrip_harness_not_fused_transport",
                           device_calls > 0 and device_rows > 0 if record["backend"] == "gpu_cuda" else device_calls == 0))
            device_calls_total += device_calls
        for key, value in recalculated.items():
            checks.append(record["observation"][key] == value)
            totals[key] += value
        if selective:
            for key, value in selective_recalculated.items():
                checks.append(record["observation"][key] == value)
                selective_totals[key] += value
            checks.append(record["observation"]["candidate_results_used"] == selective_recalculated["winner_only_rows"])
            checks.append(record["observation"]["production_coverage_proven"] is False)
        else:
            checks.append(record["observation"]["candidate_results_used"] == 0)
        checks.append(record["observation"]["attempts"] == record["observation"]["successes"] > 0)
    for samples in repeats.values():
        checks.append(len(samples) >= 3 and sorted(samples) == list(range(len(samples))))
    checks.append(bool(report["source_before"]) and report["source_before"] == report["source_after"])
    checks.append(report["production_enabled"] is False)
    if device_v2:
        checks.append(device_calls_total > 0)
    if selective:
        checks.append(report["production_coverage_proven"] is False)
        checks.append(report["performance_acceptance_eligible"] is False)
        checks.append(report["acceptance_gates"]["distance"] == "bit_exact")
    return {
        "report_sha256": file_hash(report_path), "records": len(report["records"]),
        "auditor_sha256": file_hash(__file__),
        "checks": len(checks), "failures": checks.count(False), "passed": bool(checks) and all(checks),
        "scope": "raw_artifact_integrity_and_transport_preservation_not_physical_or_optix_promotion",
        **totals, **(selective_totals if selective else {}),
    }


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
