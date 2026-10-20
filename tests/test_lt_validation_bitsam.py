from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from leakage_simulator.lt_validation import bitsam
from leakage_simulator.lt_validation.bitsam import read_bitsam


def project_fixture() -> dict:
    receiver = {
        "receiver_id": "front", "display_name": "Front", "enabled": True,
        "center": [5.7, 9, 17.1], "u_axis": [-1, 0, 0], "v_axis": [0, -1, 0],
        "normal": [0, 0, 1], "normal_flip": True, "acceptance_angle_deg": 30,
        "width_mm": 2, "height_mm": 1, "resolution": [2, 2],
    }
    emitter = {
        "emitter_id": "source", "enabled": True, "emitter_type": "datum_plane",
        "center": [4, 1, 15], "u_axis": [1, 0, 0], "v_axis": [0, 0, -1],
        "normal_flip": False, "emission_direction": "forward", "direction_distribution": "lambertian",
        "width_mm": 10, "height_mm": 10, "surface_construction": "rectangular_fit",
        "power_mode": "total", "power_lumen": 100,
        "aim": {"enabled": True, "shape": "rectangle", "mode": "area",
                "power_reference": "aim_region", "distribution": "uniform_target_area",
                "center": [4, 2.7, 9.5], "u_axis": [1, 0, 0], "v_axis": [0, 1, 0],
                "width_mm": 10, "height_mm": 3},
    }
    return {
        "format": "tv-leakage-simulator-project", "schema_version": "bitsam-project.v1",
        "project_name": "test", "saved_at": "2026-10-15T00:00:00Z",
        "workspace": {"receivers": [{**receiver, "center": [999, 999, 999]}],
                      "emitters": [{**emitter, "power_lumen": 999}]},
        "analysis_result": {
            "run_id": "saved-run", "receivers": [receiver], "emitters": [emitter],
            "receiver_grids": [{"receiver_id": "front", "resolution": [2, 2],
                                "bin_area_mm2": 0.5, "flux_lumen": [[1e-4, 2e-4], [3e-4, 4e-4]],
                                "hit_count": 90000}],
            "metrics": {"front": {"total_flux_lumen": 0.001},
                        "_termination_summary": {"source_power_lumen": 100}},
        },
    }


def issue_codes(dataset: dict) -> set[str]:
    return {issue["code"] for issue in dataset["issues"]} | {
        issue["code"] for receiver in dataset["receivers"] for issue in receiver["issues"]
    }


class BitsamValidationReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = project_fixture()

    def tearDown(self):
        self.temporary.cleanup()

    def json_file(self, project=None):
        path = self.root / "legacy.bitsam"
        path.write_text(json.dumps(self.project if project is None else project), encoding="utf-8")
        return path

    def zip_file(self, *, mutate_manifest=None, extra_entry=None, compression=zipfile.ZIP_STORED):
        data = json.dumps(self.project).encode()
        payloads = {"project.json": data, "cad/original.step": b"not parsed CAD", "scene.bin": b"not parsed scene"}
        manifest = {"schema_version": "bitsam-project.v2", "cache_contract": "bitsam-lossless-scene.v1",
                    "source": "cad/original.step", "trace_cached": False,
                    "entries": {name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                                for name, value in payloads.items()}}
        if mutate_manifest:
            mutate_manifest(manifest)
        path = self.root / "portable.bitsam"
        with zipfile.ZipFile(path, "w", compression=compression) as archive:
            for name, value in payloads.items():
                with archive.open(name, "w", force_zip64=True) as stream:
                    stream.write(value)
            archive.writestr("manifest.json", json.dumps(manifest))
            if extra_entry:
                archive.writestr(*extra_entry)
        return path

    def test_reads_only_project_and_manifest_without_geometry_restore(self):
        path = self.zip_file()
        opened = []
        original = zipfile.ZipFile.open

        def record_open(archive, name, *args, **kwargs):
            entry_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
            opened.append(entry_name)
            self.assertIn(entry_name, {"manifest.json", "project.json"})
            return original(archive, name, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "open", record_open):
            dataset = read_bitsam(path, "comparison.bitsam")
        self.assertEqual(opened, ["manifest.json", "project.json"])
        self.assertEqual(dataset["filename"], "comparison.bitsam")
        self.assertEqual(dataset["provenance"]["integrity_scope"], "project_json_only")
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_legacy_and_deflated_zip_produce_identical_saved_results(self):
        legacy = read_bitsam(self.json_file())
        portable = read_bitsam(self.zip_file(compression=zipfile.ZIP_DEFLATED))
        self.assertEqual(legacy["receivers"], portable["receivers"])
        self.assertEqual(legacy["sources"], portable["sources"])

    def test_preserves_flux_order_and_uses_saved_snapshots_not_workspace(self):
        dataset = read_bitsam(self.json_file())
        receiver, source = dataset["receivers"][0], dataset["sources"][0]
        self.assertEqual(receiver["flux_lm"], [[1e-4, 2e-4], [3e-4, 4e-4]])
        self.assertEqual(receiver["u_edges_mm"], [-1, 0, 1])
        self.assertEqual(receiver["v_edges_mm"], [-0.5, 0, 0.5])
        self.assertEqual(receiver["center_mm"], [5.7, 9, 17.1])
        self.assertEqual(receiver["normal"], [0, 0, -1])
        self.assertAlmostEqual(receiver["total_flux_lm"], 0.001)
        self.assertNotEqual(receiver["total_flux_lm"], receiver["provenance"]["hit_count"])
        self.assertEqual(source["total_flux_lm"], 100)
        self.assertEqual(source["flux_reference"], "aim_region")
        self.assertEqual(source["angular_distribution"], "uniform_target_area")
        self.assertEqual(source["normal"], [0, 1, 0])
        self.assertEqual(issue_codes(dataset), set())

    def test_missing_analysis_is_blocked_without_using_workspace(self):
        del self.project["analysis_result"]
        dataset = read_bitsam(self.json_file())
        self.assertEqual(dataset["receivers"], [])
        self.assertEqual(dataset["sources"], [])
        self.assertIn("missing_analysis_result", issue_codes(dataset))

    def test_missing_saved_metadata_stays_unknown(self):
        receiver = self.project["analysis_result"]["receivers"][0]
        for key in ("center", "normal_flip", "u_axis", "v_axis"):
            del receiver[key]
        actual = read_bitsam(self.json_file())["receivers"][0]
        self.assertIsNone(actual["center_mm"])
        self.assertIsNone(actual["normal"])
        self.assertIsNone(actual["u_axis"])
        self.assertIsNone(actual["v_axis"])

    def test_checksum_rejects_corrupted_project(self):
        path = self.zip_file(mutate_manifest=lambda m: m["entries"]["project.json"].update(sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_bitsam(path)

    def test_manifest_metadata_and_extra_entries_are_rejected(self):
        mutations = [lambda m: m.update(schema_version="future"),
                     lambda m: m.update(source="../../secret"),
                     lambda m: m["entries"]["project.json"].update(size=1),
                     lambda m: m.update(trace_cached=1)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                read_bitsam(self.zip_file(mutate_manifest=mutation))
        with self.assertRaises(ValueError):
            read_bitsam(self.zip_file(extra_entry=("../../secret", b"bad")))

    def test_duplicate_zip_entries_and_oversized_json_are_rejected(self):
        with self.assertWarns(UserWarning):
            path = self.zip_file(extra_entry=("project.json", b"{}"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            read_bitsam(path)
        path = self.zip_file()
        with patch.object(bitsam, "MAX_JSON_BYTES", 8), self.assertRaisesRegex(ValueError, "oversized"):
            read_bitsam(path)

    def test_nonfinite_and_duplicate_json_values_are_not_coerced(self):
        for text in ('{"a":NaN}', '{"a":Infinity}', '{"a":1,"a":2}', '[]'):
            with self.subTest(text=text):
                path = self.root / "invalid.bitsam"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_bitsam(path)

    def test_invalid_flux_arrays_preserve_independent_saved_total(self):
        invalid = [[[1, 2]], [[1, 2, 3], [4, 5, 6]], [[-1, 2], [3, 4]],
                   [[True, 2], [3, 4]], [["0.1", 2], [3, 4]], None]
        for grid in invalid:
            with self.subTest(grid=grid):
                self.project["analysis_result"]["receiver_grids"][0]["flux_lumen"] = grid
                dataset = read_bitsam(self.json_file())
                self.assertIsNone(dataset["receivers"][0]["flux_lm"])
                self.assertEqual(dataset["receivers"][0]["total_flux_lm"], 0.001)
                self.assertIn("invalid_flux_grid", issue_codes(dataset))

    def test_grid_resolution_snapshot_mismatch_and_bin_area_mismatch(self):
        self.project["analysis_result"]["receivers"][0]["resolution"] = [4, 1]
        self.assertIn("grid_snapshot_mismatch", issue_codes(read_bitsam(self.json_file())))
        self.project = project_fixture()
        self.project["analysis_result"]["receiver_grids"][0]["bin_area_mm2"] = 0.25
        self.assertIn("bin_area_mismatch", issue_codes(read_bitsam(self.json_file())))

    def test_invalid_area_and_oversized_dimensions_do_not_make_plot_arrays(self):
        grid = self.project["analysis_result"]["receiver_grids"][0]
        grid["bin_area_mm2"] = 0
        self.assertIn("invalid_bin_area", issue_codes(read_bitsam(self.json_file())))
        grid["resolution"] = [2_000_000, 2_000_000]
        self.project["analysis_result"]["receivers"][0]["resolution"] = grid["resolution"]
        dataset = read_bitsam(self.json_file())
        self.assertIsNone(dataset["receivers"][0]["flux_lm"])
        self.assertIsNone(dataset["receivers"][0]["u_edges_mm"])

    def test_numeric_overflow_is_blocked_and_output_is_json_serializable(self):
        path = self.json_file()
        source = path.read_text(encoding="utf-8").replace('"hit_count": 90000', '"hit_count": 1e999')
        source = source.replace('"flux_lumen": [[0.0001', '"flux_lumen": [[1e999')
        path.write_text(source, encoding="utf-8")
        dataset = read_bitsam(path)
        self.assertIn("invalid_flux_grid", issue_codes(dataset))
        self.assertIsNone(dataset["receivers"][0]["provenance"]["hit_count"])
        json.dumps(dataset, allow_nan=False)

    def test_grid_sum_disagreement_is_not_rescaled(self):
        self.project["analysis_result"]["metrics"]["front"]["total_flux_lumen"] = 0.002
        dataset = read_bitsam(self.json_file())
        self.assertAlmostEqual(dataset["receivers"][0]["total_flux_lm"], 0.001)
        self.assertEqual(dataset["receivers"][0]["flux_lm"][0][0], 1e-4)
        self.assertIn("stored_flux_mismatch", issue_codes(dataset))

    def test_duplicate_and_unmatched_grid_ids_block_comparison(self):
        grid = self.project["analysis_result"]["receiver_grids"][0]
        self.project["analysis_result"]["receiver_grids"].append(copy.deepcopy(grid))
        self.assertIn("duplicate_receiver_grid", issue_codes(read_bitsam(self.json_file())))
        self.project["analysis_result"]["receiver_grids"][1]["receiver_id"] = "unmatched"
        self.assertIn("unmatched_receiver_grid", issue_codes(read_bitsam(self.json_file())))

    def test_inconsistent_frame_and_active_snapshots_block(self):
        self.project["analysis_result"]["receivers"][0]["normal"] = [1, 0, 0]
        self.assertIn("invalid_receiver_frame", issue_codes(read_bitsam(self.json_file())))
        self.project["analysis_result"]["receivers"][0]["enabled"] = False
        self.assertIn("disabled_receiver_grid", issue_codes(read_bitsam(self.json_file())))
        del self.project["analysis_result"]["emitters"][0]["enabled"]
        self.assertIn("invalid_source_snapshot", issue_codes(read_bitsam(self.json_file())))

    def test_source_flux_disagreement_and_invalid_power_block(self):
        self.project["analysis_result"]["emitters"][0]["power_lumen"] = 50
        self.assertIn("source_flux_mismatch", issue_codes(read_bitsam(self.json_file())))
        self.project["analysis_result"]["emitters"][0]["power_lumen"] = -1
        self.assertIn("invalid_source_flux", issue_codes(read_bitsam(self.json_file())))

    def test_single_source_can_use_independently_saved_flux_if_area_unknown(self):
        emitter = self.project["analysis_result"]["emitters"][0]
        emitter.update(emitter_type="face", power_mode="power_per_area", power_density_lm_per_m2=10)
        source = read_bitsam(self.json_file())["sources"][0]
        self.assertEqual(source["total_flux_lm"], 100)
        self.assertIsNone(source["normal"])

    def test_density_is_converted_only_with_known_rectangular_area(self):
        emitter = self.project["analysis_result"]["emitters"][0]
        emitter.update(power_mode="power_per_area", power_density_lm_per_m2=20, aim=None)
        del self.project["analysis_result"]["metrics"]["_termination_summary"]
        source = read_bitsam(self.json_file())["sources"][0]
        self.assertAlmostEqual(source["total_flux_lm"], 0.002)
        self.assertEqual(source["flux_reference"], "emitted")
        emitter.update(power_mode="set_luminance", luminance_nit=20)
        source = read_bitsam(self.json_file())["sources"][0]
        self.assertAlmostEqual(source["total_flux_lm"], 0.002 * math.pi)

    def test_complex_source_modes_fail_closed(self):
        emitter = self.project["analysis_result"]["emitters"][0]
        emitter["aim"]["mode"] = "sphere"
        self.assertIn("unsupported_aim_geometry", issue_codes(read_bitsam(self.json_file())))
        emitter["aim"]["mode"] = "area"
        emitter["aim"]["distribution"] = "gaussian"
        self.assertIn("unsupported_gaussian_width", issue_codes(read_bitsam(self.json_file())))
        emitter["emission_direction"] = "both"
        self.assertIsNone(read_bitsam(self.json_file())["sources"][0]["normal"])

    def test_face_source_cannot_masquerade_as_a_rectangular_plane(self):
        emitter = self.project["analysis_result"]["emitters"][0]
        emitter.update(emitter_type="face", normal_mode="custom", custom_normal=[0, 1, 0])
        result = read_bitsam(self.json_file())
        self.assertIn("unsupported_source_geometry", issue_codes(result))


if __name__ == "__main__":
    unittest.main()
