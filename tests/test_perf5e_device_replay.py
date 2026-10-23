from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tools.perf5e_native.device_replay import DeviceSelectiveReplay, check_status
from scripts.audit_perf5e_device_replay import DTYPES, EXPECTED_SCENES, audit_matrix, audit_rows


class DeviceReplayContractTests(unittest.TestCase):
    def test_success_requires_all_rows_checked(self):
        values = check_status(np.array([0, 4, 1, 0, 0, 0, 7], np.int64), 7)
        self.assertEqual(values["replay_rows"], 4)

    def test_each_failure_counter_fails_closed(self):
        for index in (0, 3, 4, 5):
            with self.subTest(index=index):
                status = np.array([0, 4, 1, 0, 0, 0, 7], np.int64)
                status[index] = 1
                with self.assertRaisesRegex(RuntimeError, "failed closed"):
                    check_status(status, 7)

    def test_incomplete_or_impossible_counts_rejected(self):
        for status in ([0, 4, 1, 0, 0, 0, 6], [0, 1, 2, 0, 0, 0, 7],
                       [0, 8, 1, 0, 0, 0, 7], [0, -1, 0, 0, 0, 0, 7]):
            with self.subTest(status=status), self.assertRaises(RuntimeError):
                check_status(np.array(status, np.int64), 7)

    def test_empty_verification(self):
        self.assertEqual(check_status(np.zeros(7, np.int64), 0)["checked_rows"], 0)

    def test_wrong_dtype_shape_rejected(self):
        for status in (np.zeros((1, 7), np.int64), np.zeros(7, np.int32), np.zeros(6, np.int64)):
            with self.subTest(shape=status.shape), self.assertRaisesRegex(RuntimeError, "layout"):
                check_status(status, 0)

    def test_capacity_validation_before_cuda_initialization(self):
        for capacity in (0, -1, True, 1.5, 2 ** 32):
            with self.subTest(capacity=capacity), self.assertRaisesRegex(ValueError, "Capacity"):
                DeviceSelectiveReplay(None, None, capacity)


class DeviceReplayAuditTests(unittest.TestCase):
    def setUp(self):
        self.arrays = {name: np.zeros(2, dtype=dtype) for name, dtype in DTYPES.items()}
        self.arrays["candidate"]["distance"] = [1.0, 2.0]
        self.arrays["corrected_distance"][:] = self.arrays["reference_distance"][:] = [1.0, 2.0]
        self.record = dict(rays=2, replay_rows=0, winner_only_rows=2, refinement_rejected_rows=0,
                           checked_rows=2, full_oracle_rows=2, invalid_rows=0, face_mismatches=0,
                           distance_bit_mismatches=0, overflow_rows=0, ray_upload_bytes=0, hit_download_bytes=0,
                           status_download_bytes=56, input_unchanged=True, workspace_reused=True,
                           producer_stream=1, consumer_stream=2, production_enabled=False,
                           production_coverage_proven=False, performance_acceptance_eligible=False)

    def test_valid_raw_rows(self):
        self.assertTrue(all(audit_rows(self.arrays, self.record)))

    def test_dtype_and_shape_changes_fail(self):
        for array in (np.zeros(2, np.float64), np.zeros((1, 2), np.int64)):
            self.arrays["corrected_face"] = array
            self.assertFalse(all(audit_rows(self.arrays, self.record)))

    def test_changed_distance_bit_fails(self):
        self.arrays["corrected_distance"][0] = np.nextafter(1.0, 2.0)
        self.assertFalse(all(audit_rows(self.arrays, self.record)))


    def test_missing_oracle_or_invented_transfer_claim_fails(self):
        for field in ("full_oracle_rows", "status_download_bytes", "ray_upload_bytes"):
            previous = self.record[field]
            self.record[field] = -1
            self.assertFalse(all(audit_rows(self.arrays, self.record)))
            self.record[field] = previous

    def test_unreported_replay_fails(self):
        self.arrays["candidate"]["flags"][1] = 1
        self.assertFalse(all(audit_rows(self.arrays, self.record)))

    def test_shared_oracle_requires_measured_single_launch_and_full_coverage(self):
        self.record.update(contract="experimental_device_selective_replay_shared_oracle_v2",
                           canonical_query_rows=2, canonical_launches=1, observed_canonical_launches=1,
                           duplicate_replay_rows_avoided=0, capacity=8, workspace_bytes_saved_vs_v1=200,
                           oracle_strategy="one_full_canonical_pass_shared_with_replay")
        self.assertTrue(all(audit_rows(self.arrays, self.record)))
        for field in ("canonical_query_rows", "canonical_launches", "observed_canonical_launches",
                      "duplicate_replay_rows_avoided", "workspace_bytes_saved_vs_v1"):
            previous = self.record[field]
            self.record[field] = -1
            self.assertFalse(all(audit_rows(self.arrays, self.record)))
            self.record[field] = previous

    def test_shared_oracle_counts_replayed_rows_without_repeating_traversal(self):
        self.record.update(contract="experimental_device_selective_replay_shared_oracle_v2",
                           canonical_query_rows=2, canonical_launches=1, observed_canonical_launches=1,
                           duplicate_replay_rows_avoided=1, capacity=8, workspace_bytes_saved_vs_v1=200,
                           oracle_strategy="one_full_canonical_pass_shared_with_replay", replay_rows=1, winner_only_rows=1)
        self.arrays["candidate"]["flags"][0] = 1
        self.arrays["replay"][0] = 1
        self.assertTrue(all(audit_rows(self.arrays, self.record)))
        self.record["full_oracle_rows"] = 1
        self.assertFalse(all(audit_rows(self.arrays, self.record)))


class DeviceReplayMatrixTests(unittest.TestCase):
    def setUp(self):
        self.report = dict(rays_per_full_query=256, records=[
            dict(scene=scene, repeat=repeat, rays=count, capacity=256)
            for scene in sorted(EXPECTED_SCENES)
            for repeat, count in enumerate((256, 256, 256, 17, 0, 256))
        ])

    def test_declared_matrix(self):
        self.assertTrue(all(audit_matrix(self.report)))

    def test_all_empty_records_cannot_replace_nonempty_exercise(self):
        for record in self.report["records"]:
            record["rays"] = 0
        self.assertFalse(all(audit_matrix(self.report)))

    def test_unknown_scene_missing_repeat_or_changed_capacity_fails(self):
        for field, changed in (("scene", "fake"), ("repeat", 99), ("capacity", 17), ("rays", 0)):
            original = self.report["records"][0][field]
            self.report["records"][0][field] = changed
            self.assertFalse(all(audit_matrix(self.report)))
            self.report["records"][0][field] = original

    def test_missing_full_size_rejected(self):
        self.report.pop("rays_per_full_query")
        self.assertFalse(all(audit_matrix(self.report)))


if __name__ == "__main__":
    unittest.main()
