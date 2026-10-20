"""Exercise validation HTTP routes without constructing a simulator runtime."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from leakage_simulator.lt_validation import routes


def dataset_fixture():
    return {
        "schema": "lt-validation.v1", "kind": "manual", "filename": "saved.json", "issues": [],
        "receivers": [{
            "id": "front", "name": "Front", "quantity": "photometric", "raw": True,
            "flux_lm": [[0.1, 0.2], [0.3, 0.4]], "total_flux_lm": 1,
            "u_edges_mm": [-1, 0, 1], "v_edges_mm": [-1, 0, 1],
            "center_mm": [0, 0, 10], "u_axis": [1, 0, 0], "v_axis": [0, 1, 0],
            "normal": [0, 0, -1], "acceptance_angle_deg": 90, "aperture": "none", "issues": [],
        }],
        "sources": [{
            "id": "source", "name": "Source", "total_flux_lm": 100, "flux_reference": "emitted",
            "center_mm": [0, 0, 0], "u_axis": [1, 0, 0], "v_axis": [0, 1, 0], "normal": [0, 0, 1],
            "width_mm": 2, "height_mm": 2, "angular_distribution": "lambertian", "aim": None,
        }],
    }


def comparison_fixture():
    return {"baseline_id": "before", "cases": [{
        "id": "before", "name": "Before", "sim": dataset_fixture(), "lt": dataset_fixture(),
        "sim_receiver_id": "front", "lt_receiver_id": "front",
    }]}


class ValidationApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(routes.router)
        self.app.state.live_scene = object()
        self.sentinel = self.app.state.live_scene
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.assertIs(self.app.state.live_scene, self.sentinel)
        self.client.close()

    def test_import_canonical_json_preserves_values_and_sanitizes_filename(self):
        expected = dataset_fixture()
        response = self.client.post("/api/lt-validation/import", params={"filename": r"C:\private\values.json"},
                                    content=json.dumps(expected))
        self.assertEqual(response.status_code, 200, response.text)
        expected["filename"] = "values.json"
        self.assertEqual(response.json(), expected)

    def test_invalid_extensions_are_rejected_before_parsing(self):
        with patch.object(routes, "_import_file") as reader:
            response = self.client.post("/api/lt-validation/import?filename=photo.jpg", content=b"image")
        self.assertEqual(response.status_code, 400)
        reader.assert_not_called()

    def test_invalid_canonical_envelopes_and_duplicate_receivers_are_rejected(self):
        duplicate = dataset_fixture()
        duplicate["receivers"].append(copy.deepcopy(duplicate["receivers"][0]))
        for invalid in ([], {}, {"schema": "unknown"}, duplicate):
            with self.subTest(invalid=invalid):
                response = self.client.post("/api/lt-validation/import?filename=data.json", content=json.dumps(invalid))
                self.assertEqual(response.status_code, 400)

    def test_canonical_import_never_returns_structures_that_crash_dataset_cards(self):
        missing_issues = dataset_fixture()
        del missing_issues["issues"]
        del missing_issues["receivers"][0]["issues"]
        response = self.client.post("/api/lt-validation/import?filename=data.json", json=missing_issues)
        self.assertIn(response.status_code, (200, 400))
        if response.status_code == 200:
            self.assertIsInstance(response.json().get("issues"), list)
            self.assertIsInstance(response.json()["receivers"][0].get("issues"), list)
        for field, value in (("sources", [None]), ("center_mm", 42), ("u_edges_mm", "bad")):
            invalid = dataset_fixture()
            target = invalid if field == "sources" else invalid["receivers"][0]
            target[field] = value
            with self.subTest(field=field):
                response = self.client.post("/api/lt-validation/import?filename=data.json", json=invalid)
                self.assertEqual(response.status_code, 400, response.text)

    def test_import_invalid_container_and_missing_result_are_distinct(self):
        invalid = self.client.post("/api/lt-validation/import?filename=data.bitsam", content=b"PK\x03\x04broken")
        self.assertEqual(invalid.status_code, 400)
        missing_result = {"format": "tv-leakage-simulator-project", "schema_version": "bitsam-project.v1",
                          "workspace": {"receivers": [{"receiver_id": "not_saved"}]}}
        valid = self.client.post("/api/lt-validation/import?filename=data.bitsam", content=json.dumps(missing_result))
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["receivers"], [])
        self.assertEqual(valid.json()["issues"][0]["code"], "missing_analysis_result")

    def test_upload_is_temporary_even_when_reader_fails(self):
        captured = []

        def reject(path, filename):
            captured.append(path)
            self.assertEqual(path.read_bytes(), b"payload")
            self.assertEqual(filename, "bad.lts")
            raise ValueError("Unsupported layout")

        with patch.object(routes, "_import_file", side_effect=reject):
            response = self.client.post("/api/lt-validation/import?filename=bad.lts", content=b"payload")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].exists())
        self.assertFalse(captured[0].parent.exists())

    def test_size_limits_abort_before_reader_or_comparison_and_remove_upload(self):
        temporary_directories = []
        factory = tempfile.TemporaryDirectory

        def temporary(*args, **kwargs):
            directory = factory(*args, **kwargs)
            temporary_directories.append(Path(directory.name))
            return directory

        with patch.object(routes, "MAX_JSON", 4), patch.object(routes.tempfile, "TemporaryDirectory", side_effect=temporary), \
                patch.object(routes, "_import_file") as reader, patch.object(routes, "compare_cases") as compare:
            upload = self.client.post("/api/lt-validation/import?filename=large.json", content=b"12345")
            request = self.client.post("/api/lt-validation/compare", content=b"12345")
        self.assertEqual(upload.status_code, 413)
        self.assertEqual(request.status_code, 413)
        reader.assert_not_called()
        compare.assert_not_called()
        self.assertTrue(temporary_directories)
        self.assertTrue(all(not directory.exists() for directory in temporary_directories))

    def test_compare_same_conditions_and_flux_never_uses_simulator_runtime(self):
        payload = comparison_fixture()
        original = copy.deepcopy(payload)
        response = self.client.post("/api/lt-validation/compare", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["cases"][0]
        self.assertEqual(result["status"], "ready")
        self.assertAlmostEqual(result["pearson"], 1)
        self.assertEqual(result["overlap_pct"], 100)
        self.assertEqual(result["sim_relative_pct"], 100)
        self.assertEqual(result["lt_relative_pct"], 100)
        self.assertAlmostEqual(sum(result["plots"]["column_sim_pct"]), 100)
        self.assertAlmostEqual(sum(result["plots"]["row_lt_pct"]), 100)
        self.assertEqual(payload, original)

    def test_compare_missing_condition_returns_blocked_diagnostics(self):
        payload = comparison_fixture()
        payload["cases"][0]["lt"]["receivers"][0]["normal"] = None
        response = self.client.post("/api/lt-validation/compare", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["cases"][0]
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["plots"])
        self.assertEqual(result["lt_total_lm"], 1)

    def test_compare_invalid_body_baseline_and_case_limit_return_400(self):
        too_many = {"baseline_id": "0", "cases": [{"id": str(i)} for i in range(51)]}
        for value in ([], {}, {"cases": []}, {"cases": [{"id": "one"}], "baseline_id": "missing"}, too_many):
            with self.subTest(value=value):
                response = self.client.post("/api/lt-validation/compare", json=value)
                self.assertEqual(response.status_code, 400)

    def test_nonfinite_literals_and_overflowed_json_numbers_are_rejected(self):
        for value in ("NaN", "Infinity", "1e999"):
            with self.subTest(value=value):
                encoded = json.dumps(dataset_fixture()).replace('"total_flux_lm": 1,', '"total_flux_lm": ' + value + ',')
                response = self.client.post("/api/lt-validation/import?filename=data.json", content=encoded)
                self.assertEqual(response.status_code, 400, response.text)

    def test_deeply_nested_json_returns_client_error(self):
        encoded = '{"extra":' + '[' * 1100 + '0' + ']' * 1100 + '}'
        for endpoint in ("/api/lt-validation/import?filename=data.json", "/api/lt-validation/compare"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, content=encoded)
                self.assertEqual(response.status_code, 400, response.text)

    def test_finite_inputs_never_emit_nonfinite_comparison_results(self):
        payload = comparison_fixture()
        for tool, value in (("sim", 1e308), ("lt", 1e-308)):
            receiver = payload["cases"][0][tool]["receivers"][0]
            receiver.update(flux_lm=[[value]], total_flux_lm=value,
                            u_edges_mm=[-1e150, 1e150], v_edges_mm=[-1e150, 1e150])
        response = self.client.post("/api/lt-validation/compare", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["cases"][0]
        self.assertIsNone(result["flux_deviation_pct"])
        json.dumps(response.json(), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
