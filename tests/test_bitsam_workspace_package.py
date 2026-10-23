from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from leakage_simulator.api import ApiRuntime, create_app
from leakage_simulator.bitsam_package import (
    package_expanded_size,
    read_scene_cache,
    scene_geometry_signature,
    write_package,
    write_workspace_package,
)
from leakage_simulator.scene_binary import iter_scene_binary, prepare_scene_binary


def scene_fixture(path: str | Path) -> dict:
    return {
        "schema_version": "mesh-scene.v1",
        "units": {"length": "mm"},
        "coordinate_system": {
            "handedness": "right",
            "axes": {"x": "model_x", "y": "model_y", "z": "model_z"},
        },
        "mesh": {
            "vertices": [(-10.0, -10.0, 10.0), (10.0, -10.0, 10.0), (10.0, 10.0, 10.0), (-10.0, 10.0, 10.0)],
            "faces": [(0, 1, 2), (0, 2, 3)],
            "face_component_ids": [4, 4],
            "face_material_ids": ["mirror", "mirror"],
            "face_source_ids": [11, 11],
            "face_areas_mm2": [200.0, 200.0],
            "feature_edge_segments": [],
        },
        "components": [{
            "component_id": 4,
            "object_id": 4,
            "component_name": Path(path).stem,
            "face_indices": [0, 1],
            "face_count": 2,
            "bbox_min": [-10.0, -10.0, 10.0],
            "bbox_max": [10.0, 10.0, 10.0],
        }],
        "metadata": {
            "source_file": str(path),
            "face_count": 2,
            "vertex_count": 4,
            "component_count": 1,
        },
    }


def project_fixture(name: str, *, face_count: int = 2, vertex_count: int = 4, component_count: int = 1) -> dict:
    fingerprint_scene = {
        "components": [
            {
                "component_id": 4 + index,
                "object_id": 4 + index,
                "face_count": 2,
                "bbox_min": [-10.0, -10.0, 10.0],
                "bbox_max": [10.0, 10.0, 10.0],
            }
            for index in range(component_count)
        ],
    }
    return {
        "format": "tv-leakage-simulator-project",
        "schema_version": "bitsam-project.v1",
        "application_version": "1.0.0",
        "project_name": name,
        "saved_at": "2026-10-23T00:00:00Z",
        "cad": {
            "display_name": name + ".step",
            "file_extension": ".step",
            "fingerprint": {
                "scene_schema_version": "mesh-scene.v1",
                "face_count": face_count,
                "vertex_count": vertex_count,
                "component_count": component_count,
                "geometry_signature": scene_geometry_signature(fingerprint_scene),
            },
        },
        "workspace": {
            "hiddenComponentIds": [],
            "excludedComponentIds": [],
            "deletedComponentIds": [],
            "componentNameOverrides": {},
            "componentColorOverrides": {},
            "faceColorOverrides": [],
            "materialAssignments": [],
            "customOpticalProfiles": [],
            "bsdfAssets": [],
            "transformRules": [],
            "roiScopes": [],
            "roiScopeSequence": 0,
            "emitters": [],
            "receivers": [],
            "rayTraceConfig": {},
            "rayPathDisplayFilters": {},
        },
    }


class BitsamWorkspacePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = ApiRuntime(self.root / "export", scene_loader=scene_fixture)
        first = self.runtime.save_upload("case-one.step", b"first CAD source")
        second = self.runtime.save_upload("case-two.step", b"second CAD source")
        accessory = self.runtime.save_upload("accessory.step", b"accessory CAD source")
        self.first_path = first["path"]
        self.second_path = second["path"]
        self.accessory_path = accessory["path"]
        self.request = {
            "workspace_name": "TV leakage study",
            "active_case_id": "case-2",
            "cases": [
                {
                    "case_id": "case-1",
                    "order": 1,
                    "name": "Baseline",
                    "note": "Original structure",
                    "visible": False,
                    "cad_path": self.first_path,
                    "accessory_paths": [],
                    "project": project_fixture("case-one"),
                },
                {
                    "case_id": "case-2",
                    "order": 2,
                    "name": "Shield added",
                    "note": "Accessory remains independently editable",
                    "visible": True,
                    "cad_path": self.second_path,
                    "accessory_paths": [{
                        "accessoryId": "shield-cad",
                        "path": self.accessory_path,
                        "displayName": "Shield insert.step",
                        "visible": True,
                    }],
                    "project": project_fixture(
                        "case-two", face_count=4, vertex_count=8, component_count=2,
                    ),
                },
            ],
        }
        self.request["cases"][1]["project"]["analysis_result"] = {
            "summary": {"receiver_hits": 17},
            "saved_for_case": "case-2",
        }

    def exported_path(self) -> Path:
        exported = self.runtime.export_workspace(copy.deepcopy(self.request))
        self.assertEqual(exported["scope"], "workspace")
        self.assertEqual(exported["case_count"], 2)
        path, _ = self.runtime.project_export_file(exported["download_url"].split("/")[-1])
        return path

    def test_backend_geometry_signature_matches_browser_contract(self) -> None:
        self.assertEqual(
            scene_geometry_signature(scene_fixture("browser-contract.step")),
            "fnv1a32:e61bb014",
        )

    def test_round_trip_restores_all_cases_without_external_sources(self) -> None:
        package = self.exported_path()
        with zipfile.ZipFile(package) as archive:
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["schema_version"], "bitsam-workspace.v1")
            self.assertIn("assets/0002/accessory-0001.step", manifest["entries"])
        Path(self.first_path).unlink()
        Path(self.second_path).unlink()
        Path(self.accessory_path).unlink()

        loader = Mock(side_effect=AssertionError("portable workspace must not parse deleted CAD"))
        restored_runtime = ApiRuntime(self.root / "restore", scene_loader=loader)
        restored = restored_runtime.import_project(package)

        self.assertEqual(restored["scope"], "workspace")
        self.assertEqual(restored["workspace_name"], "TV leakage study")
        self.assertEqual(restored["active_case_id"], "case-2")
        self.assertEqual(
            [(item["case_id"], item["order"], item["name"], item["visible"]) for item in restored["cases"]],
            [("case-1", 1, "Baseline", False), ("case-2", 2, "Shield added", True)],
        )
        first_case, second_case = restored["cases"]
        self.assertEqual(second_case["project"], self.request["cases"][1]["project"])
        self.assertEqual(second_case["cad"]["displayName"], "case-two.step")
        self.assertEqual(second_case["accessory_cads"], [{
            "accessoryId": "shield-cad",
            "cad": {
                "path": second_case["accessory_cads"][0]["cad"]["path"],
                "displayName": "Shield insert.step",
            },
            "visible": True,
        }])
        restored_accessory = Path(second_case["accessory_cads"][0]["cad"]["path"])
        self.assertEqual(restored_accessory.read_bytes(), b"accessory CAD source")
        scenes = [
            restored_runtime.load_scene(first_case["cad"]["path"]),
            restored_runtime.load_composite_scene([
                second_case["cad"]["path"],
                str(restored_accessory),
            ]),
        ]
        self.assertEqual([scene["metadata"]["component_count"] for scene in scenes], [1, 2])
        self.assertTrue(all(item["trace_cached"] for item in restored["cases"]))
        self.assertEqual(
            list(restored_runtime.upload_dir.glob("project_workspace_*/cases/*.bitsam")),
            [],
        )
        loader.assert_not_called()

    def test_api_exports_downloads_and_imports_workspace(self) -> None:
        with TestClient(create_app(self.runtime)) as client:
            exported = client.post("/api/projects/export-workspace", json=self.request)
            self.assertEqual(exported.status_code, 200, exported.text)
            downloaded = client.get(exported.json()["download_url"])
            self.assertEqual(downloaded.status_code, 200)
            imported = client.post("/api/projects/import", content=downloaded.content)
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()["scope"], "workspace")
            self.assertEqual(len(imported.json()["cases"]), 2)
            self.assertEqual(
                imported.json()["cases"][1]["accessory_cads"][0]["accessoryId"],
                "shield-cad",
            )

    def test_hidden_accessory_is_preserved_and_can_be_enabled_after_restore(self) -> None:
        request = copy.deepcopy(self.request)
        request["active_case_id"] = "case-2"
        request["cases"] = [request["cases"][1]]
        request["cases"][0]["accessory_paths"][0]["visible"] = False
        request["cases"][0]["project"] = project_fixture("case-two")
        exported = self.runtime.export_workspace(request)
        package, _ = self.runtime.project_export_file(exported["download_url"].split("/")[-1])

        restored_runtime = ApiRuntime(self.root / "hidden-restore", scene_loader=scene_fixture)
        restored = restored_runtime.import_project(package)
        case = restored["cases"][0]
        self.assertFalse(case["accessory_cads"][0]["visible"])
        primary = restored_runtime.load_scene(case["cad"]["path"])
        self.assertEqual(primary["metadata"]["component_count"], 1)
        combined = restored_runtime.load_composite_scene([
            case["cad"]["path"],
            case["accessory_cads"][0]["cad"]["path"],
        ])
        self.assertEqual(combined["metadata"]["component_count"], 2)

    def test_duplicate_source_paths_preserve_each_workspace_entry(self) -> None:
        request = {
            "workspace_name": "Repeated source study",
            "active_case_id": "case-duplicate-source",
            "cases": [{
                "case_id": "case-duplicate-source",
                "order": 1,
                "name": "Repeated source",
                "note": "One CAD source is intentionally used for three entries",
                "visible": True,
                "cad_path": self.accessory_path,
                "accessory_paths": [
                    {
                        "accessoryId": "repeat-left",
                        "path": self.accessory_path,
                        "displayName": "Repeated left.step",
                        "visible": True,
                    },
                    {
                        "accessoryId": "repeat-right",
                        "path": self.accessory_path,
                        "displayName": "Repeated right.step",
                        "visible": True,
                    },
                ],
                "project": project_fixture(
                    "repeated-source", face_count=6, vertex_count=12, component_count=3,
                ),
            }],
        }
        exported = self.runtime.export_workspace(request)
        package, _ = self.runtime.project_export_file(exported["download_url"].split("/")[-1])
        Path(self.accessory_path).unlink()

        loader = Mock(side_effect=AssertionError("restored duplicate entries must use the packaged cache"))
        restored_runtime = ApiRuntime(self.root / "duplicate-source-restore", scene_loader=loader)
        restored = restored_runtime.import_project(package)
        case = restored["cases"][0]
        primary_path = Path(case["cad"]["path"])
        accessories = case["accessory_cads"]
        accessory_paths = [Path(item["cad"]["path"]) for item in accessories]

        self.assertEqual(
            [item["accessoryId"] for item in accessories],
            ["repeat-left", "repeat-right"],
        )
        self.assertEqual(
            [item["cad"]["displayName"] for item in accessories],
            ["Repeated left.step", "Repeated right.step"],
        )
        self.assertEqual([item["visible"] for item in accessories], [True, True])
        self.assertEqual(len({primary_path, *accessory_paths}), 3)
        restored_paths = [primary_path, *accessory_paths]
        self.assertTrue(all(
            path.read_bytes() == b"accessory CAD source"
            for path in restored_paths
        ))

        combined = restored_runtime.load_composite_scene([
            str(primary_path),
            *(str(path) for path in accessory_paths),
        ])
        self.assertEqual(combined["metadata"]["component_count"], 3)
        self.assertEqual(len(combined["metadata"]["source_files"]), 3)
        loader.assert_not_called()

    def test_string_accessory_path_gets_stable_default_metadata(self) -> None:
        request = copy.deepcopy(self.request)
        request["active_case_id"] = "case-2"
        request["cases"] = [request["cases"][1]]
        request["cases"][0]["accessory_paths"] = [self.accessory_path]
        exported = self.runtime.export_workspace(request)
        package, _ = self.runtime.project_export_file(exported["download_url"].split("/")[-1])
        restored = ApiRuntime(self.root / "string-restore", scene_loader=scene_fixture).import_project(package)
        accessory = restored["cases"][0]["accessory_cads"][0]
        self.assertEqual(accessory["accessoryId"], "accessory-1")
        self.assertEqual(accessory["cad"]["displayName"], "accessory.step")
        self.assertTrue(accessory["visible"])

    def test_rejects_duplicate_cases_and_unregistered_external_paths(self) -> None:
        duplicate = copy.deepcopy(self.request)
        duplicate["cases"][1]["case_id"] = "case-1"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.runtime.export_workspace(duplicate)

        outside = self.root / "outside.step"
        outside.write_bytes(b"not registered")
        invalid = copy.deepcopy(self.request)
        invalid["cases"][0]["cad_path"] = str(outside)
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.runtime.export_workspace(invalid)

        invalid_accessory = copy.deepcopy(self.request)
        invalid_accessory["cases"][0]["accessory_paths"] = [str(outside)]
        with self.assertRaisesRegex(ValueError, "not registered"):
            self.runtime.export_workspace(invalid_accessory)

        # A source outside _uploads is allowed only after this runtime has
        # loaded and registered it explicitly.
        self.runtime.load_scene(str(outside))
        one = copy.deepcopy(invalid)
        one["active_case_id"] = "case-1"
        one["cases"] = [one["cases"][0]]
        self.assertEqual(self.runtime.export_workspace(one)["case_count"], 1)

    def test_corrupt_inner_case_fails_without_registering_partial_workspace(self) -> None:
        package = self.exported_path()
        with zipfile.ZipFile(package) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        inner_name = "cases/0002.bitsam"
        damaged_output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(files[inner_name])) as original:
            inner_files = {name: original.read(name) for name in original.namelist()}
        damaged_project = bytearray(inner_files["project.json"])
        damaged_project[-2] ^= 0x01
        inner_files["project.json"] = bytes(damaged_project)
        with zipfile.ZipFile(damaged_output, "w", compression=zipfile.ZIP_STORED) as damaged_zip:
            for name, content in inner_files.items():
                damaged_zip.writestr(name, content)
        files[inner_name] = damaged_output.getvalue()
        manifest = json.loads(files["manifest.json"])
        manifest["entries"][inner_name] = {
            "size": len(files[inner_name]),
            "sha256": hashlib.sha256(files[inner_name]).hexdigest(),
        }
        files["manifest.json"] = json.dumps(manifest).encode()
        corrupted = self.root / "corrupted-workspace.bitsam"
        with zipfile.ZipFile(corrupted, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)

        target = ApiRuntime(self.root / "atomic", scene_loader=scene_fixture)
        with self.assertRaises(Exception):
            target.import_project(corrupted)
        self.assertEqual(target._portable_sources, {})
        self.assertEqual(target._portable_composite_sources, {})
        self.assertEqual(target._scene_payload_cache, {})
        self.assertEqual(target._known_scene_sources, set())
        self.assertEqual(list(target.upload_dir.glob("project_workspace_*")), [])

    def test_inactive_case_with_same_counts_but_different_bounds_is_rejected(self) -> None:
        package = self.exported_path()
        with zipfile.ZipFile(package) as archive:
            outer_files = {name: archive.read(name) for name in archive.namelist()}

        # Case 1 is inactive and invisible. Keep every aggregate count the
        # same, but alter one component bound in its embedded scene cache.
        inner_name = "cases/0001.bitsam"
        with zipfile.ZipFile(io.BytesIO(outer_files[inner_name])) as archive:
            inner_files = {name: archive.read(name) for name in archive.namelist()}
        scene_path = self.root / "tampered-scene.bin"
        scene_path.write_bytes(inner_files["scene.bin"])
        changed_scene = read_scene_cache(scene_path)
        changed_scene["components"][0]["bbox_max"][0] += 1.0
        header, blocks = prepare_scene_binary(changed_scene, coordinate_dtype="float64")
        inner_files["scene.bin"] = b"".join(iter_scene_binary(header, blocks))
        inner_manifest = json.loads(inner_files["manifest.json"])
        inner_manifest["entries"]["scene.bin"] = {
            "size": len(inner_files["scene.bin"]),
            "sha256": hashlib.sha256(inner_files["scene.bin"]).hexdigest(),
        }
        inner_files["manifest.json"] = json.dumps(inner_manifest).encode()
        changed_inner = io.BytesIO()
        with zipfile.ZipFile(changed_inner, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in inner_files.items():
                archive.writestr(name, content)
        outer_files[inner_name] = changed_inner.getvalue()
        outer_manifest = json.loads(outer_files["manifest.json"])
        outer_manifest["entries"][inner_name] = {
            "size": len(outer_files[inner_name]),
            "sha256": hashlib.sha256(outer_files[inner_name]).hexdigest(),
        }
        outer_files["manifest.json"] = json.dumps(outer_manifest).encode()
        tampered = self.root / "same-count-different-bounds.bitsam"
        with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in outer_files.items():
                archive.writestr(name, content)

        target = ApiRuntime(self.root / "geometry-integrity", scene_loader=scene_fixture)
        with self.assertRaisesRegex(ValueError, "geometry and settings fingerprint mismatch"):
            target.import_project(tampered)
        self.assertEqual(target._portable_sources, {})
        self.assertEqual(target._portable_composite_sources, {})
        self.assertEqual(target._scene_payload_cache, {})
        self.assertEqual(target._known_scene_sources, set())
        self.assertEqual(list(target.upload_dir.glob("project_workspace_*")), [])

    def test_corrupt_accessory_asset_fails_outer_integrity_check(self) -> None:
        package = self.exported_path()
        with zipfile.ZipFile(package) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        asset_name = next(name for name in files if name.startswith("assets/"))
        damaged = bytearray(files[asset_name])
        damaged[0] ^= 0xFF
        files[asset_name] = bytes(damaged)
        corrupted = self.root / "corrupted-accessory.bitsam"
        with zipfile.ZipFile(corrupted, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)

        target = ApiRuntime(self.root / "asset-integrity", scene_loader=scene_fixture)
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            target.import_project(corrupted)
        self.assertEqual(target._portable_sources, {})
        self.assertEqual(target._portable_composite_sources, {})
        self.assertEqual(target._known_scene_sources, set())
        self.assertEqual(list(target.upload_dir.glob("project_workspace_*")), [])

    def test_existing_v2_import_contract_is_unchanged(self) -> None:
        source = Path(self.first_path)
        scene = scene_fixture(source)
        package = self.root / "single.bitsam"
        project = project_fixture("single")
        write_package(package, project, source, scene, scene["mesh"])

        restored = ApiRuntime(self.root / "single-restore", scene_loader=scene_fixture).import_project(package)
        self.assertNotIn("scope", restored)
        self.assertEqual(restored["project"], project)
        self.assertEqual(restored["cad"]["displayName"], "single.step")

    def test_outer_writer_rejects_cumulative_case_size_before_creating_archive(self) -> None:
        case_files: dict[str, Path] = {}
        cases = []
        for index in range(2):
            entry = "cases/{:04d}.bitsam".format(index + 1)
            case_path = self.root / ("case-{}.bitsam".format(index + 1))
            case_path.write_bytes(b"x" * 700)
            case_files[entry] = case_path
            cases.append({
                "case_id": "case-{}".format(index + 1),
                "order": index,
                "name": "Case {}".format(index + 1),
                "note": "",
                "visible": True,
                "entry": entry,
                "source": {"display_name": "case-{}.step".format(index + 1)},
                "accessories": [],
            })
        workspace = {
            "format": "tv-leakage-simulator-workspace",
            "schema_version": "bitsam-workspace.v1",
            "workspace_name": "oversized",
            "saved_at": "2026-10-23T00:00:00Z",
            "active_case_id": "case-1",
            "cases": cases,
        }
        destination = self.root / "must-not-be-created.bitsam"

        with patch("leakage_simulator.bitsam_package.MAX_WORKSPACE_BYTES", 1_000):
            with self.assertRaisesRegex(ValueError, "cumulative size limit"):
                write_workspace_package(destination, workspace, case_files)
        self.assertFalse(destination.exists())

    def test_export_stops_cumulative_case_generation_before_outer_archive(self) -> None:
        def write_fixed_case(_token: str, _project: dict, destination: Path) -> dict:
            destination.write_bytes(b"x" * 700)
            return {"trace_cached": True}

        with (
            patch("leakage_simulator.api.runtime.MAX_WORKSPACE_BYTES", 1_000),
            patch.object(self.runtime, "_write_case_package", side_effect=write_fixed_case),
            patch(
                "leakage_simulator.api.runtime.write_workspace_package",
                side_effect=AssertionError("outer archive must not be started"),
            ) as outer_writer,
        ):
            with self.assertRaisesRegex(ValueError, "cumulative size limit"):
                self.runtime.export_workspace(copy.deepcopy(self.request))

        outer_writer.assert_not_called()
        self.assertEqual(list(self.runtime.upload_dir.glob("export_workspace_*.bitsam")), [])
        self.assertEqual(list(self.runtime.upload_dir.glob("export_workspace_*.writing")), [])

    def test_compressed_nested_cases_share_one_import_expansion_budget(self) -> None:
        # Make the inner package's declared expansion much larger than its ZIP
        # bytes so the outer container passes while the shared nested budget
        # must reject the aggregate before extracting any Case.
        Path(self.first_path).write_bytes(b"a" * (128 * 1024))
        Path(self.second_path).write_bytes(b"b" * (128 * 1024))
        package = self.exported_path()
        with zipfile.ZipFile(package) as archive:
            outer_files = {name: archive.read(name) for name in archive.namelist()}

        manifest = json.loads(outer_files["manifest.json"])
        inner_sizes: list[int] = []
        for name in sorted(value for value in outer_files if value.startswith("cases/")):
            compressed = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(outer_files[name])) as inner:
                inner_files = {entry: inner.read(entry) for entry in inner.namelist()}
            with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as rewritten:
                for entry, content in inner_files.items():
                    rewritten.writestr(entry, content)
            outer_files[name] = compressed.getvalue()
            manifest["entries"][name] = {
                "size": len(outer_files[name]),
                "sha256": hashlib.sha256(outer_files[name]).hexdigest(),
            }
            inner_path = self.root / Path(name).name
            inner_path.write_bytes(outer_files[name])
            inner_sizes.append(package_expanded_size(inner_path))
        outer_files["manifest.json"] = json.dumps(manifest).encode()

        compressed_workspace = self.root / "nested-expansion.bitsam"
        with zipfile.ZipFile(compressed_workspace, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, content in outer_files.items():
                archive.writestr(name, content)
        with zipfile.ZipFile(compressed_workspace) as archive:
            outer_expanded_size = sum(info.file_size for info in archive.infolist())

        asset_size = sum(
            metadata["size"]
            for name, metadata in manifest["entries"].items()
            if name.startswith("assets/")
        )
        final_expanded_size = sum(inner_sizes) + asset_size
        self.assertLess(outer_expanded_size, final_expanded_size)
        limit = (outer_expanded_size + final_expanded_size) // 2

        target = ApiRuntime(self.root / "nested-budget", scene_loader=scene_fixture)
        with (
            patch("leakage_simulator.bitsam_package.MAX_WORKSPACE_BYTES", limit),
            patch("leakage_simulator.api.runtime.MAX_WORKSPACE_BYTES", limit),
            patch(
                "leakage_simulator.api.runtime.extract_package",
                side_effect=AssertionError("inner extraction must not start"),
            ) as extractor,
        ):
            with self.assertRaisesRegex(ValueError, "cumulative size limit"):
                target.import_project(compressed_workspace)

        extractor.assert_not_called()
        self.assertEqual(list(target.upload_dir.glob("project_workspace_*")), [])

    def test_accessory_assets_share_the_nested_import_budget(self) -> None:
        request = copy.deepcopy(self.request)
        request["active_case_id"] = "case-2"
        request["cases"] = [request["cases"][1]]
        package_info = self.runtime.export_workspace(request)
        package, _ = self.runtime.project_export_file(
            package_info["download_url"].split("/")[-1],
        )
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            accessory_size = next(
                metadata["size"]
                for name, metadata in manifest["entries"].items()
                if name.startswith("assets/")
            )
            retained_metadata_size = (
                manifest["entries"]["workspace.json"]["size"]
                + archive.getinfo("manifest.json").file_size
            )
        self.assertGreater(accessory_size, 1)
        limit = retained_metadata_size + 100 + accessory_size - 1

        target = ApiRuntime(self.root / "asset-budget", scene_loader=scene_fixture)
        with (
            patch("leakage_simulator.api.runtime.MAX_WORKSPACE_BYTES", limit),
            patch("leakage_simulator.api.runtime.package_expanded_size", return_value=100),
            patch(
                "leakage_simulator.api.runtime.extract_package",
                side_effect=AssertionError("inner extraction must not start"),
            ) as extractor,
        ):
            with self.assertRaisesRegex(ValueError, "cumulative size limit"):
                target.import_project(package)

        extractor.assert_not_called()
        self.assertEqual(list(target.upload_dir.glob("project_workspace_*")), [])

    def test_imported_scene_metadata_cannot_register_an_arbitrary_local_path(self) -> None:
        source = Path(self.first_path)
        outside = self.root / "untrusted-local.step"
        outside.write_bytes(b"private CAD")
        scene = scene_fixture(source)
        scene["metadata"]["source_files"] = [str(outside)]
        package = self.root / "poisoned.bitsam"
        project = project_fixture("poisoned")
        write_package(package, project, source, scene, scene["mesh"])

        runtime = ApiRuntime(self.root / "poisoned-restore", scene_loader=scene_fixture)
        imported = runtime.import_project(package)
        runtime.load_scene(imported["cad"]["path"])
        request = {
            "workspace_name": "blocked",
            "active_case_id": "case-1",
            "cases": [{
                "case_id": "case-1",
                "order": 1,
                "name": "blocked",
                "note": "",
                "visible": True,
                "cad_path": str(outside),
                "accessory_paths": [],
                "project": project_fixture("outside"),
            }],
        }
        with self.assertRaisesRegex(ValueError, "not registered"):
            runtime.export_workspace(request)


if __name__ == "__main__":
    unittest.main()
