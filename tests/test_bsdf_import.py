from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.bsdf_import import BsdfImportError, import_lt_ascii_bsdf
from leakage_simulator.bitsam_package import extract_package, write_package
from tests.test_bitsam_package import project_fixture, scene_fixture


def fixture():
    directory = ROOT / "samples" / "bsdf" / "synthetic-lambertian"
    return {"files": [{"name": name, "text": (directory / name).read_text(encoding="utf-8")}
                      for name in ("master.txt", "scatter.txt", "rt.txt")]}


def replace(payload, file_name, old, new):
    item = next(item for item in payload["files"] if item["name"] == file_name)
    assert old in item["text"], old
    item["text"] = item["text"].replace(old, new)
    return payload


class BsdfImportTests(unittest.TestCase):
    def test_relative_distribution_is_not_mistaken_for_absolute_reflectance(self):
        payload = fixture()
        asset = import_lt_ascii_bsdf(payload)
        self.assertEqual(asset["data"]["rt"][0]["reflectance"], 0.3)
        self.assertEqual(asset["data"]["grids"][0]["values"][0][0], 1)
        self.assertEqual(asset["data"]["symmetry"], "unspecified")
        self.assertFalse(asset["simulationReady"])
        self.assertEqual(asset["data"]["incidenceAnglesDeg"], [0, 60])
        self.assertEqual(asset["files"], sorted(payload["files"], key=lambda item: item["name"]))
        scaled = replace(fixture(), "scatter.txt", "1 1 1 1 1", "10 10 10 10 10")
        self.assertEqual(import_lt_ascii_bsdf(scaled)["data"]["rt"], asset["data"]["rt"])

    def test_grid_order_matches_lt_max_to_min_latitude(self):
        payload = replace(fixture(), "scatter.txt", "1 1 1 1 1\n1 1 1 1 1\n1 1 1 1 1", "3 3 3 3 3\n2 2 2 2 2\n1 1 1 1 1")
        grid = import_lt_ascii_bsdf(payload)["data"]["grids"][0]
        self.assertEqual(grid["latitudesDeg"], [90, 45, 0])
        self.assertEqual(grid["longitudesDeg"], [0, 90, 180, 270, 360])
        self.assertEqual(grid["values"][0], [3] * 5)
        self.assertEqual(grid["values"][-1], [1] * 5)

    def test_rt_nonabsorbing_normalizes_without_changing_original_source(self):
        payload = replace(fixture(), "rt.txt", "ABSORBING: YES", "ABSORBING: NO")
        asset = import_lt_ascii_bsdf(payload)
        self.assertEqual(asset["data"]["rt"][0]["reflectance"], 1)
        self.assertIn("0.3", next(item["text"] for item in asset["files"] if item["name"] == "rt.txt"))
        self.assertTrue(any("정규화" in warning for warning in asset["warnings"]))

    def test_id_stable_across_selection_order_and_changes_with_content(self):
        payload = fixture()
        asset_id = import_lt_ascii_bsdf(payload)["id"]
        payload["files"].reverse()
        self.assertEqual(import_lt_ascii_bsdf(payload)["id"], asset_id)
        replace(payload, "rt.txt", "0.3", "0.4")
        self.assertNotEqual(import_lt_ascii_bsdf(payload)["id"], asset_id)

    def test_supplied_companions_only_no_embedded_path_reads(self):
        payload = replace(fixture(), "master.txt", "..\\scatter.txt", "Z:\\not-present\\scatter.txt")
        with patch("pathlib.Path.open", side_effect=AssertionError("No filesystem reads")):
            self.assertEqual(len(import_lt_ascii_bsdf(payload)["data"]["grids"]), 2)
        replace(payload, "master.txt", "scatter.txt", "missing.txt")
        with self.assertRaisesRegex(BsdfImportError, "누락"):
            import_lt_ascii_bsdf(payload)

    def test_unsupported_variants_fail_explicitly(self):
        for file_name, old, new, message in [
            ("master.txt", "AngGridMultiple", "AngGridSlice", "master"),
            ("master.txt", "Polar", "Cartesian", "Cartesian"),
            ("master.txt", "MeshDataType = BSDF", "MeshDataType = CCBSDF", "CCBSDF"),
            ("master.txt", "RAOI = 60", "AOI = 60", "반사"),
            ("master.txt", "RAOI = 60", "RAOI = 120", "back"),
            ("master.txt", "POI = 0", "POI = 30", "POI"),
            ("master.txt", "POI = 0", "POI = 0\nIndex = 1.5", "Index"),
            ("rt.txt", "0 0.3", "0.1 0.3", "반사 전용"),
            ("rt.txt", "TAVG RAVG", "RAVG TAVG", "열 순서"),
        ]:
            with self.subTest(new=new):
                with self.assertRaisesRegex(BsdfImportError, message):
                    import_lt_ascii_bsdf(replace(fixture(), file_name, old, new))
        with self.assertRaisesRegex(BsdfImportError, "단일 .bsdf"):
            import_lt_ascii_bsdf({"files": [{"name": "unknown.bsdf", "text": "unknown"}]})

    def test_bad_or_ambiguous_data_rejected(self):
        cases = [
            replace(fixture(), "scatter.txt", "1 1 1 1 1", "1 1 1 1"),
            replace(fixture(), "scatter.txt", "1 1 1 1 1", "-1 -1 -1 -1 -1"),
            replace(fixture(), "scatter.txt", "1 1 1 1 1", "nan nan nan nan nan"),
            replace(fixture(), "scatter.txt", "1 1 1 1 1", "0 0 0 0 0"),
            replace(fixture(), "scatter.txt", "1 1 1 1 1", "1 1 1 1 2"),
            replace(fixture(), "scatter.txt", "1 1 1 1 1", "1 2 1 1 1"),
            replace(fixture(), "scatter.txt", "5 3", "5 9999999"),
            replace(fixture(), "rt.txt", "0.3", "1.1"),
            replace(fixture(), "rt.txt", "wv 550", "wv 600"),
            replace(fixture(), "rt.txt", "AOI 0 0 0.3", "AOI 30 0 0.3"),
            replace(fixture(), "rt.txt", "AOI 60", "AOI 0"),
            replace(fixture(), "master.txt", "RAOI = 60", "RAOI = 0"),
        ]
        duplicate = fixture()
        duplicate["files"].append({"name": "SCATTER.TXT", "text": "test"})
        cases.append(duplicate)
        extra = fixture()
        extra["files"].append({"name": "extra.txt", "text": "not referenced"})
        cases.append(extra)
        for i, payload in enumerate(cases):
            with self.subTest(i=i), self.assertRaises(BsdfImportError):
                import_lt_ascii_bsdf(payload)

    def test_multiple_wavelengths_retained_as_data_not_ready_to_trace(self):
        payload = fixture()
        master = payload["files"][0]
        body = master["text"].split("FileName", 1)[1]
        master["text"] += "FileName" + body.replace("Lambda = 550", "Lambda = 650")
        payload["files"][2]["text"] += "wv 650\nAOI 0 0 0.2\nAOI 90 0 0.2\n"
        asset = import_lt_ascii_bsdf(payload)
        self.assertEqual(asset["data"]["wavelengthsNm"], [550, 650])
        self.assertFalse(asset["simulationReady"])

    def test_unpaired_unicode_in_file_name_is_a_validation_error(self):
        payload = fixture()
        payload["files"][2]["name"] = "rt" + chr(0xD800) + ".txt"
        with self.assertRaisesRegex(BsdfImportError, "UTF-8"):
            import_lt_ascii_bsdf(payload)
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_app(ApiRuntime(Path(directory))))
            # ASCII JSON escaping permits the invalid Unicode scalar on the wire.
            response = client.post("/api/bsdf/import", content=json.dumps(payload).encode("ascii"))
            self.assertEqual(response.status_code, 400)

    def test_size_limits_apply_before_parsing(self):
        with patch("leakage_simulator.bsdf_import.MAX_FILE_BYTES", 5):
            with self.assertRaisesRegex(BsdfImportError, "한도"):
                import_lt_ascii_bsdf(fixture())

    def test_portable_package_retains_sources_and_link_without_external_bsdf_files(self):
        asset = import_lt_ascii_bsdf(fixture())
        asset['transport'] = {'model': 'isotropic_no_shift', 'wavelengthNm': 550, 'aoiExtrapolation': 'clamp'}
        project = project_fixture()
        project["schema_version"] = "bitsam-project.v1-bsdf1"
        project["workspace"]["bsdfAssets"] = [asset]
        project["workspace"]["materialAssignments"][0]["bsdfAssetId"] = asset["id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / "part.step"
            source.write_text("test CAD bytes", encoding="utf-8")
            scene = scene_fixture(source)
            write_package(path / "test.bitsam", project, source, scene, scene["mesh"])
            extract_package(path / "test.bitsam", path / "restored")
            restored = json.loads((path / "restored" / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["workspace"]["bsdfAssets"], [asset])
            self.assertEqual(restored["workspace"]["materialAssignments"][0]["bsdfAssetId"], asset["id"])
            reparsed = import_lt_ascii_bsdf({"files": restored["workspace"]["bsdfAssets"][0]["files"]})
            self.assertEqual(reparsed, {k:v for k,v in asset.items() if k != 'transport'})
            from leakage_simulator.bsdf_transport import compile_asset
            self.assertAlmostEqual(compile_asset(restored['workspace']['bsdfAssets'][0]).reflectance_at(0), .3)
            runtime = ApiRuntime(path / "server")
            imported = runtime.import_project(path / "test.bitsam")
            self.assertEqual(imported["project"]["workspace"]["bsdfAssets"], [asset])
            project["schema_version"] = "bitsam-project.v1"
            with self.assertRaisesRegex(ValueError, "prevent older builds"):
                write_package(path / "unsafe.bitsam", project, source, scene, scene["mesh"])


class BsdfApiTests(unittest.TestCase):
    def test_import_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            client = TestClient(create_app(ApiRuntime(Path(directory))))
            response = client.post("/api/bsdf/import", json=fixture())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()["simulationReady"])
            self.assertEqual(client.post("/api/bsdf/import", content=b"not json").status_code, 400)
            self.assertEqual(client.post("/api/bsdf/import", json={"files": []}).status_code, 400)
            with patch("leakage_simulator.api.app.MAX_BSDF_REQUEST_BYTES", 10):
                self.assertEqual(client.post("/api/bsdf/import", json=fixture()).status_code, 413)


if __name__ == "__main__":
    unittest.main()
