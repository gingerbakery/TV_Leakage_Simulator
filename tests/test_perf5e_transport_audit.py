from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from scripts.audit_perf5e_transport import (
    DEVICE_V2, DEVICE_V2_SOURCES, ORIGINAL_DTYPES, audit, audit_device_batch,
    audit_device_identity, audit_selective, file_hash, valid_rows,
)
from tools.perf5e_native.probe import HIT_DTYPE, RAY_DTYPE


class TransportAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        candidate = np.zeros(1, dtype=HIT_DTYPE)
        candidate["distance"] = 1.0
        arrays = self.root / "query.npz"
        np.savez(arrays, rays=np.zeros(1, dtype=RAY_DTYPE), candidate=candidate, reference_face=np.array([0]),
                 reference_distance=np.array([1.0]), visited=np.array([3], dtype=np.uint8))
        payload = self.root / "result.json"
        payload.write_text(json.dumps({"metrics": {"_performance_summary": {}}, "receiver_grids": [1.0]}))
        artifact = {"path": str(arrays), "sha256": file_hash(arrays)}
        counters = dict(queries=1, missing_reference_visits=0, raw_face_mismatches=0, unflagged_mismatches=0)
        record = {
            "scene": "fixture", "backend": "cpu", "repeat": 0,
            "results": {mode: {"path": str(payload), "sha256": file_hash(payload)} for mode in ("baseline", "shadow")},
            "observation": {**counters, "candidate_results_used": 0, "attempts": 1, "successes": 1,
                            "records": [{**counters, "artifact": {**artifact, "scene": artifact}}]},
        }
        self.report = {
            "records": [dict(copy.deepcopy(record), repeat=repeat) for repeat in range(3)],
            "source_before": {"fixture": "hash"}, "source_after": {"fixture": "hash"},
            "production_enabled": False, "passed": True,
        }
        self.path = self.root / "report.json"

    def run_audit(self):
        self.path.write_text(json.dumps(self.report), encoding="utf-8")
        return audit(self.path)

    def test_raw_artifacts_are_recomputed(self):
        self.assertTrue(self.run_audit()["passed"])
        self.report["records"][0]["observation"]["queries"] = 99
        self.assertFalse(self.run_audit()["passed"])

    def test_empty_incomplete_or_duplicate_runs_fail(self):
        original = copy.deepcopy(self.report)
        for records in ([], original["records"][:1], [original["records"][0]] * 3):
            self.report = dict(original, records=records)
            self.assertFalse(self.run_audit()["passed"])

    def test_failed_report_and_missing_source_identity_fail(self):
        self.report["passed"] = False
        self.assertFalse(self.run_audit()["passed"])
        self.report["passed"] = True
        self.report["source_before"] = self.report["source_after"] = {}
        self.assertFalse(self.run_audit()["passed"])

    def test_probe_failure_and_candidate_use_cannot_be_success(self):
        self.report["records"][0]["observation"]["successes"] = 0
        self.assertFalse(self.run_audit()["passed"])
        self.report["records"][0]["observation"]["successes"] = 1
        self.report["records"][0]["observation"]["candidate_results_used"] = 1
        self.assertFalse(self.run_audit()["passed"])

    def test_original_shape_with_identical_bytes_fails(self):
        path = self.root / "query.npz"
        with np.load(path, allow_pickle=False) as source:
            arrays = {key: source[key] for key in source.files}
        arrays["reference_face"] = arrays["reference_face"].reshape(1, 1)
        np.savez(path, **arrays)
        for record in self.report["records"]:
            artifact = record["observation"]["records"][0]["artifact"]
            artifact["sha256"] = artifact["scene"]["sha256"] = file_hash(path)
        self.assertFalse(self.run_audit()["passed"])

    def test_canonical_abi_dtypes_match_writer(self):
        self.assertEqual(ORIGINAL_DTYPES["rays"], RAY_DTYPE)
        self.assertEqual(ORIGINAL_DTYPES["candidate"], HIT_DTYPE)

    def test_multiline_candidate_subarray_broadcast_rejected(self):
        arrays = {field: np.zeros(2, dtype=dtype) for field, dtype in ORIGINAL_DTYPES.items()}
        self.assertTrue(valid_rows(arrays, 2, ORIGINAL_DTYPES))
        arrays["candidate"] = np.zeros(2, dtype=[("distance", "<f8", 2), ("face", "<i8"), ("flags", "<i8")])
        arrays["candidate"]["distance"] = [[1.0, 2.0], [1.0, 2.0]]
        arrays["reference_distance"] = np.array([1.0, 2.0])
        self.assertTrue(np.isclose(arrays["candidate"]["distance"], arrays["reference_distance"]).all())
        self.assertFalse(valid_rows(arrays, 2, ORIGINAL_DTYPES))

    def test_original_wrong_dtype_fails(self):
        path = self.root / "query.npz"
        with np.load(path, allow_pickle=False) as source:
            arrays = {key: source[key] for key in source.files}
        arrays["reference_face"] = arrays["reference_face"].astype(np.float64)
        np.savez(path, **arrays)
        for record in self.report["records"]:
            artifact = record["observation"]["records"][0]["artifact"]
            artifact["sha256"] = artifact["scene"]["sha256"] = file_hash(path)
        self.assertFalse(self.run_audit()["passed"])

    def test_relabeling_selective_v1_cannot_certify_device_v2(self):
        original = self.root / "query.npz"
        with np.load(original, allow_pickle=False) as source:
            arrays = {key: source[key] for key in source.files}
        arrays.update(corrected_face=arrays["reference_face"].copy(), corrected_distance=arrays["reference_distance"].copy(),
                      replay=np.array([False]), rejected=np.array([False]), backend=np.array("cpu"))
        selective = self.root / "selective.npz"
        np.savez(selective, **arrays)
        totals = dict(replay_rows=0, winner_only_rows=1, refinement_rejected_rows=0,
                      corrected_face_mismatches=0, corrected_distance_bit_mismatches=0)
        for record in self.report["records"]:
            record["observation"].update(**totals, candidate_results_used=1, production_coverage_proven=False)
            record["observation"]["records"][0].update(
                **totals, refinement_backend="cpu", selective_artifact={"path": str(selective), "sha256": file_hash(selective)},
            )
        self.report.update(contract="experimental_optix_selective_replay_oracle_checked_v1",
                           production_coverage_proven=False, performance_acceptance_eligible=False,
                           acceptance_gates={"distance": "bit_exact"})
        self.assertTrue(self.run_audit()["passed"])
        self.report["contract"] = DEVICE_V2
        self.assertFalse(self.run_audit()["passed"])

    def test_all_empty_gpu_transport_cannot_pass_positive_use_gate(self):
        path = self.root / "query.npz"
        with np.load(path, allow_pickle=False) as source:
            arrays = {key: source[key][:0] for key in source.files}
        np.savez(path, **arrays)
        arrays.update(corrected_face=arrays["reference_face"].copy(), corrected_distance=arrays["reference_distance"].copy(),
                      replay=np.empty(0, bool), rejected=np.empty(0, bool), backend=np.array("gpu_cuda"))
        selective = self.root / "selective.npz"
        np.savez(selective, **arrays)
        totals = dict(replay_rows=0, winner_only_rows=0, refinement_rejected_rows=0,
                      corrected_face_mismatches=0, corrected_distance_bit_mismatches=0)
        metadata = dict(contract=DEVICE_V2, oracle_strategy="one_full_canonical_pass_shared_with_replay",
                        checked_rows=0, full_oracle_rows=0, canonical_query_rows=0, canonical_launches=0,
                        duplicate_replay_rows_avoided=0, replay_rows=0, winner_only_rows=0, refinement_rejected_rows=0,
                        invalid_rows=0, face_mismatches=0, distance_bit_mismatches=0, overflow_rows=0,
                        ray_upload_bytes=0, hit_download_bytes=0, status_download_bytes=56, production_enabled=False,
                        production_coverage_proven=False, performance_acceptance_eligible=False)
        for record in self.report["records"]:
            record["backend"] = "gpu_cuda"
            observation = record["observation"]
            observation.update(**totals, queries=0, production_coverage_proven=False, device_adapter_calls=1,
                               device_adapter_rows=0, device_adapter_scope="device_query_inside_host_roundtrip_harness_not_fused_transport")
            batch = observation["records"][0]
            batch.update(**totals, queries=0, refinement_backend="gpu_cuda", device_adapter=metadata,
                         selective_artifact={"path": str(selective), "sha256": file_hash(selective)})
            batch["artifact"]["sha256"] = batch["artifact"]["scene"]["sha256"] = file_hash(path)
        self.report.update(contract=DEVICE_V2, production_coverage_proven=False,
                           performance_acceptance_eligible=False, acceptance_gates={"distance": "bit_exact"},
                           device_implementation={"contract": DEVICE_V2}, source_before={}, source_after={})
        for role, (suffix, expected) in DEVICE_V2_SOURCES.items():
            self.report["device_implementation"][f"{role}_sha256"] = expected
            for phase in ("source_before", "source_after"):
                self.report[phase]["C:/snapshot/" + suffix] = expected
        self.assertFalse(self.run_audit()["passed"])


class DeviceV2EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.report = dict(device_implementation={"contract": DEVICE_V2}, source_before={}, source_after={})
        for role, (suffix, expected) in DEVICE_V2_SOURCES.items():
            self.report["device_implementation"][f"{role}_sha256"] = expected
            for phase in ("source_before", "source_after"):
                self.report[phase]["C:/snapshot/" + suffix] = expected

    def test_pinned_identity_matches_approved_experiment_sources(self):
        root = Path(__file__).resolve().parents[1]
        for suffix, expected in DEVICE_V2_SOURCES.values():
            self.assertEqual(file_hash(root / suffix), expected)
        self.assertTrue(all(audit_device_identity(self.report)))

    def test_missing_or_old_implementation_is_not_v2(self):
        for field in ("contract", "adapter_sha256", "observer_sha256"):
            changed = copy.deepcopy(self.report)
            changed["device_implementation"][field] = "old"
            self.assertFalse(all(audit_device_identity(changed)))
        self.assertFalse(all(audit_device_identity({})))

    def test_stale_or_duplicate_source_identity_is_rejected(self):
        source = next(iter(self.report["source_before"]))
        self.report["source_before"][source] = "old"
        self.assertFalse(all(audit_device_identity(self.report)))
        self.report["source_before"] = dict(self.report["source_after"])
        self.report["source_before"][source.replace("snapshot", "duplicate")] = self.report["source_before"][source]
        self.assertFalse(all(audit_device_identity(self.report)))

    def test_gpu_batch_requires_matching_contract_and_complete_oracle(self):
        batch = dict(refinement_backend="gpu_cuda", replay_rows=3, winner_only_rows=2, refinement_rejected_rows=1)
        metadata = dict(contract=DEVICE_V2, oracle_strategy="one_full_canonical_pass_shared_with_replay",
                        checked_rows=5, full_oracle_rows=5, canonical_query_rows=5, canonical_launches=1,
                        duplicate_replay_rows_avoided=3, replay_rows=3, winner_only_rows=2, refinement_rejected_rows=1,
                        invalid_rows=0, face_mismatches=0, distance_bit_mismatches=0, overflow_rows=0,
                        ray_upload_bytes=0, hit_download_bytes=0, status_download_bytes=56, production_enabled=False,
                        production_coverage_proven=False, performance_acceptance_eligible=False)
        self.assertFalse(all(audit_device_batch(batch, 5)))
        batch["device_adapter"] = metadata
        self.assertTrue(all(audit_device_batch(batch, 5)))
        for field, value in (("contract", "old"), ("full_oracle_rows", 4), ("canonical_launches", 2),
                             ("face_mismatches", 1), ("replay_rows", 4)):
            changed = copy.deepcopy(batch)
            changed["device_adapter"][field] = value
            self.assertFalse(all(audit_device_batch(changed, 5)))

    def test_cpu_batches_cannot_claim_device_execution(self):
        self.assertTrue(all(audit_device_batch({"refinement_backend": "cpu"}, 5)))
        self.assertFalse(all(audit_device_batch({"refinement_backend": "cpu", "device_adapter": {}}, 5)))

    def test_empty_gpu_batch_does_not_prove_device_use(self):
        batch = dict(refinement_backend="gpu_cuda", replay_rows=0, winner_only_rows=0, refinement_rejected_rows=0)
        batch["device_adapter"] = dict(
            contract=DEVICE_V2, oracle_strategy="one_full_canonical_pass_shared_with_replay",
            checked_rows=0, full_oracle_rows=0, canonical_query_rows=0, canonical_launches=0,
            duplicate_replay_rows_avoided=0, replay_rows=0, winner_only_rows=0, refinement_rejected_rows=0,
            invalid_rows=0, face_mismatches=0, distance_bit_mismatches=0, overflow_rows=0,
            ray_upload_bytes=0, hit_download_bytes=0, status_download_bytes=56, production_enabled=False,
            production_coverage_proven=False, performance_acceptance_eligible=False,
        )
        self.assertFalse(all(audit_device_batch(batch, 0)))


class SelectiveAuditTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        candidate = np.zeros(1, dtype=HIT_DTYPE)
        candidate["distance"] = 1.0
        self.arrays = dict(
            rays=np.zeros(1, dtype=RAY_DTYPE), candidate=candidate,
            reference_face=np.array([0]), reference_distance=np.array([1.0]),
            corrected_face=np.array([0]), corrected_distance=np.array([1.0]),
            replay=np.array([False]), rejected=np.array([False]), backend=np.array("cpu"),
            visited=np.array([3], dtype=np.uint8),
        )
        original = self.root / "original.npz"
        np.savez(original, **self.arrays)
        self.original = {"path": str(original), "sha256": file_hash(original)}
        self.batch = dict(refinement_backend="cpu", corrected_face_mismatches=0,
                          corrected_distance_bit_mismatches=0, replay_rows=0, winner_only_rows=1,
                          refinement_rejected_rows=0)

    def run_audit(self):
        path = self.root / "selective.npz"
        np.savez(path, **self.arrays)
        self.batch["selective_artifact"] = {"path": str(path), "sha256": file_hash(path)}
        checks, _ = audit_selective(self.batch, self.original)
        return all(checks)

    def test_selective_evidence_recomputed(self):
        self.assertTrue(self.run_audit())
        self.batch["winner_only_rows"] = 99
        self.assertFalse(self.run_audit())

    def test_one_ulp_corrected_distance_fails(self):
        self.arrays["corrected_distance"][0] = np.nextafter(1.0, 2.0)
        self.assertFalse(self.run_audit())

    def test_oracle_input_cannot_be_changed_to_match_candidate(self):
        self.arrays["reference_face"][0] = self.arrays["corrected_face"][0] = 1
        self.assertFalse(self.run_audit())

    def test_unreported_backend_switch_fails(self):
        self.arrays["backend"] = np.array("gpu_cuda")
        self.assertFalse(self.run_audit())

    def test_cardinality_mismatch_fails(self):
        self.arrays["corrected_face"] = np.array([0, 0])
        self.arrays["corrected_distance"] = np.array([1.0, 1.0])
        self.assertFalse(self.run_audit())

    def test_scalar_dtypes_cannot_mimic_values_or_bytes(self):
        original = copy.deepcopy(self.arrays)
        for field, dtype, reinterpret in (("corrected_face", np.float64, False),
                                           ("corrected_distance", np.uint64, True),
                                           ("replay", np.uint8, True),
                                           ("reference_face", np.float64, False)):
            with self.subTest(field=field):
                self.arrays = copy.deepcopy(original)
                self.arrays[field] = self.arrays[field].view(dtype) if reinterpret else self.arrays[field].astype(dtype)
                self.assertFalse(self.run_audit())

    def test_backend_must_be_scalar_text(self):
        self.arrays["backend"] = np.array(["cpu"])
        self.assertFalse(self.run_audit())

    def test_original_shape_with_identical_bytes_fails(self):
        original = dict(self.arrays)
        original["reference_face"] = original["reference_face"].reshape(1, 1)
        np.savez(self.original["path"], **original)
        self.original["sha256"] = file_hash(self.original["path"])
        self.assertFalse(self.run_audit())


if __name__ == "__main__":
    unittest.main()
