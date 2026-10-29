from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from leakage_simulator.raytrace_bridge import (
    PreparedTraceGeometry,
    build_direct_trace_input,
    build_prepared_trace_geometry,
)
from leakage_simulator.bsdf_guard import validate_bsdf_request
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import build_scene_payload
from leakage_simulator.section_cap import build_section_cap_contours
from leakage_simulator.composite_scene import merge_scene_meshes, merge_scene_payloads
from leakage_simulator.bitsam_package import (
    CumulativeByteBudget,
    MAX_WORKSPACE_BYTES,
    MAX_WORKSPACE_CASES,
    PACKAGE_SCHEMA,
    WORKSPACE_FORMAT,
    WORKSPACE_SCHEMA,
    extract_package,
    extract_workspace_package,
    mesh_identity,
    package_expanded_size,
    package_schema,
    read_json,
    read_scene_cache,
    scene_geometry_signature,
    write_package,
    write_workspace_package,
)


class TraceResult(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


SceneLoader = Callable[[str], dict[str, Any]]
TraceInputBuilder = Callable[
    [dict[str, Any], dict[str, Any]],
    Any,
]
TraceRunner = Callable[..., TraceResult]


class _SceneLoadState:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.payload: Optional[dict[str, Any]] = None
        self.error: Optional[Exception] = None


class _TraceGeometryBuildState:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.prepared: Optional[PreparedTraceGeometry] = None
        self.error: Optional[Exception] = None


class ApiRuntime:
    """Owns the short-lived state required by the local simulation API."""

    def __init__(
        self,
        root: Path,
        *,
        scene_loader: SceneLoader = build_scene_payload,
        trace_input_builder: TraceInputBuilder = build_direct_trace_input,
        trace_runner: TraceRunner = run_direct_ray_trace,
        max_cached_scenes: int = 3,
        max_jobs: int = 8,
    ) -> None:
        self.root = root.resolve()
        self.upload_dir = self.root / "_uploads"
        self.output_dir = self.root / "outputs"
        self._scene_loader = scene_loader
        self._trace_input_builder = trace_input_builder
        self._trace_runner = trace_runner
        self._max_cached_scenes = max(1, max_cached_scenes)
        self._max_jobs = max(1, max_jobs)
        self._scene_mesh_cache: dict[str, dict[str, Any]] = {}
        self._scene_viewer_mesh_cache: dict[str, dict[str, Any]] = {}
        self._scene_payload_cache: dict[str, dict[str, Any]] = {}
        self._scene_records: dict[str, tuple[dict[str, Any], Path]] = {}
        self._known_scene_sources: set[Path] = set()
        self._source_display_names: dict[Path, str] = {}
        self._portable_sources: dict[str, Path] = {}
        self._portable_composite_sources: dict[tuple[str, ...], Path] = {}
        self._project_exports: dict[str, tuple[Path, str, float]] = {}
        self._trace_geometry_cache: dict[str, PreparedTraceGeometry] = {}
        self._trace_geometry_builds: dict[str, _TraceGeometryBuildState] = {}
        self._scene_loads: dict[str, _SceneLoadState] = {}
        self._raytrace_jobs: dict[str, dict[str, Any]] = {}
        self._output_file_index: dict[str, Path] = {}
        self._state_lock = threading.RLock()

    def load_scene(self, cad_path: str) -> dict[str, Any]:
        cad_path = cad_path.strip()
        if not cad_path:
            raise ValueError("CAD file is required")

        cache_key = self._scene_cache_key(cad_path)
        with self._state_lock:
            payload = self._scene_payload_cache.get(cache_key)
            load_state = self._scene_loads.get(cache_key)
            is_loader = payload is None and load_state is None
            if is_loader:
                load_state = _SceneLoadState()
                self._scene_loads[cache_key] = load_state

        if payload is not None:
            print(
                "[CAD] scene payload cache hit  | {}".format(
                    Path(cad_path).name,
                ),
                flush=True,
            )
        elif not is_loader:
            print(
                "[CAD] scene load coalesced     | waiting for active import",
                flush=True,
            )
            if load_state is None:
                raise RuntimeError("CAD scene load state is unavailable")
            load_state.event.wait()
            if load_state.error is not None:
                raise RuntimeError(str(load_state.error)) from load_state.error
            payload = load_state.payload
            if payload is None:
                raise RuntimeError("CAD scene import returned no payload")
        else:
            if load_state is None:
                raise RuntimeError("CAD scene load state is unavailable")
            try:
                package_root = self._portable_sources.get(str(Path(cad_path).resolve()))
                payload = (
                    self._load_portable_scene(package_root)
                    if package_root is not None
                    else self._scene_loader(cad_path)
                )
            except Exception as exc:
                load_state.error = exc
                with self._state_lock:
                    self._scene_loads.pop(cache_key, None)
                load_state.event.set()
                raise
            load_state.payload = payload
            with self._state_lock:
                self._scene_payload_cache[cache_key] = payload
                while (
                    len(self._scene_payload_cache)
                    > self._max_cached_scenes
                ):
                    oldest_key = next(iter(self._scene_payload_cache))
                    self._scene_payload_cache.pop(oldest_key, None)
                self._scene_loads.pop(cache_key, None)
            load_state.event.set()

        return self._register_scene(payload, Path(cad_path))

    def load_composite_scene(self, cad_paths: list[str]) -> dict[str, Any]:
        paths = [str(value).strip() for value in cad_paths if str(value).strip()]
        if not paths:
            raise ValueError("CAD file is required")
        source_key = tuple(str(Path(value).resolve()) for value in paths)
        with self._state_lock:
            package_root = self._portable_composite_sources.get(source_key)
        if package_root is not None:
            payload = self._load_portable_scene(package_root)
            metadata = dict(payload.get("metadata") or {})
            metadata["source_file"] = paths[0]
            metadata["source_files"] = paths
            payload["metadata"] = metadata
            return self._register_scene(payload, Path(paths[0]))
        if len(paths) == 1:
            return self.load_scene(paths[0])
        payloads: list[dict[str, Any]] = []
        trace_meshes: list[dict[str, Any]] = []
        for path in paths:
            registered = self.load_scene(path)
            token = str((registered.get("metadata") or {}).get("scene_token") or "")
            with self._state_lock:
                record = self._scene_records.get(token)
                trace = self._scene_mesh_cache.get(token)
            if record is None or trace is None:
                raise ValueError("CAD scene cache expired while composing Accessory CAD")
            payloads.append(record[0])
            trace_meshes.append(trace)
        merged, component_maps, source_offsets = merge_scene_payloads(payloads, paths)

        def load_trace_mesh() -> dict[str, Any]:
            resolved: list[dict[str, Any]] = []
            for trace in trace_meshes:
                loader = trace.get("_deferred_trace_loader")
                value = loader() if callable(loader) else trace
                if not isinstance(value, dict):
                    raise ValueError("Accessory CAD trace mesh is unavailable")
                resolved.append(value)
            return merge_scene_meshes(resolved, component_maps, source_offsets)

        merged["_trace_mesh_loader"] = load_trace_mesh
        return self._register_scene(merged, Path(paths[0]))

    def _register_scene(self, payload: dict[str, Any], source: Path) -> dict[str, Any]:
        viewer_mesh = payload.get("mesh")
        if not isinstance(viewer_mesh, dict):
            raise ValueError("Scene payload is missing mesh data")
        trace_loader = payload.get("_trace_mesh_loader")
        trace_mesh = payload.get("_trace_mesh") or viewer_mesh
        if callable(trace_loader):
            trace_mesh = {
                "_deferred_trace_loader": trace_loader,
                "_deferred_trace_lock": threading.Lock(),
                "_viewer_face_source_ids": viewer_mesh.get(
                    "face_source_ids"
                ) or [],
            }
        elif not isinstance(trace_mesh, dict):
            raise ValueError("Scene payload trace mesh must be an object")
        elif trace_mesh is not viewer_mesh:
            trace_mesh = dict(trace_mesh)
            trace_mesh["_viewer_face_source_ids"] = viewer_mesh.get(
                "face_source_ids"
            ) or []

        scene_token = "scene_{}".format(time.time_ns())
        with self._state_lock:
            self._scene_mesh_cache[scene_token] = trace_mesh
            self._scene_viewer_mesh_cache[scene_token] = viewer_mesh
            self._scene_records[scene_token] = (payload, source.resolve())
            self._known_scene_sources.add(source.resolve())
            while len(self._scene_mesh_cache) > self._max_cached_scenes:
                oldest_token = next(iter(self._scene_mesh_cache))
                self._scene_mesh_cache.pop(oldest_token, None)
                self._scene_viewer_mesh_cache.pop(oldest_token, None)
                self._scene_records.pop(oldest_token, None)

        response_payload = dict(payload)
        # Trace tessellation can be hundreds of MB and must never be serialized
        # to the browser. It remains in the short-lived server scene cache.
        response_payload.pop("_trace_mesh", None)
        response_payload.pop("_trace_mesh_loader", None)
        raw_metadata = payload.get("metadata")
        if not isinstance(raw_metadata, dict):
            raise ValueError("Scene payload metadata must be an object")
        metadata = dict(raw_metadata)
        metadata["scene_token"] = scene_token
        response_payload["metadata"] = metadata
        return response_payload

    @staticmethod
    def _validate_project_fingerprint(
        project: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        cad = project.get("cad")
        if not isinstance(cad, dict):
            raise ValueError("BITSAM CAD settings are required")
        fingerprint = cad.get("fingerprint") or {}
        if not isinstance(fingerprint, dict):
            raise ValueError("BITSAM CAD fingerprint is invalid")
        metadata = payload.get("metadata") or {}
        for key in ("face_count", "vertex_count", "component_count"):
            if fingerprint.get(key) != metadata.get(key):
                raise ValueError("BITSAM settings do not match the active CAD scene")

    def _write_case_package(
        self,
        token: str,
        project: dict[str, Any],
        destination: Path,
    ) -> dict[str, Any]:
        if not isinstance(project, dict):
            raise ValueError("BITSAM project settings are required")
        with self._state_lock:
            record = self._scene_records.get(token)
            trace = self._scene_mesh_cache.get(token)
        if record is None or trace is None:
            raise ValueError("CAD scene cache expired. Reload the CAD before saving BITSAM.")
        payload, source = record
        self._validate_project_fingerprint(project, payload)
        lock = trace.get("_deferred_trace_lock")
        if lock is not None:
            with lock:
                deferred_loader = trace.get("_deferred_trace_loader")
                cached_trace = None if callable(deferred_loader) else trace
                # A composite project cannot rebuild its trace geometry from
                # the main CAD alone. Embed the resolved combined trace mesh.
                if cached_trace is None and len((payload.get("metadata") or {}).get("source_files") or []) > 1:
                    cached_trace = deferred_loader()
                    if not isinstance(cached_trace, dict):
                        raise ValueError("Accessory CAD trace mesh is unavailable")
                    viewer_sources = trace.get("_viewer_face_source_ids") or []
                    if viewer_sources:
                        cached_trace = dict(cached_trace)
                        cached_trace["_viewer_face_source_ids"] = viewer_sources
                    with self._state_lock:
                        self._scene_mesh_cache[token] = cached_trace
        else:
            cached_trace = trace
        return write_package(destination, project, source, payload, cached_trace)

    @staticmethod
    def _download_name(value: object, fallback: str = "simulation") -> str:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or fallback)).strip(". ")
        if name.lower().endswith(".bitsam"):
            name = name[:-7].rstrip(". ")
        return (name or fallback) + ".bitsam"

    def _discard_stale_project_exports(self) -> None:
        with self._state_lock:
            stale = [
                key
                for key, (_, _, created) in self._project_exports.items()
                if time.time() - created > 3600
            ]
        for key in stale:
            self.discard_project_export(key)

    def export_project(self, request: dict[str, Any]) -> dict[str, Any]:
        token = str(request.get("scene_token") or "")
        project = request.get("project")
        if not isinstance(project, dict):
            raise ValueError("BITSAM project settings are required")
        self._discard_stale_project_exports()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        export_id = uuid.uuid4().hex
        path = self.upload_dir / ("export_" + export_id + ".bitsam")
        temporary = path.with_suffix(".writing")
        started = time.perf_counter()
        try:
            manifest = self._write_case_package(token, project, temporary)
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise
        name = self._download_name(project.get("project_name"))
        with self._state_lock:
            self._project_exports[export_id] = (path, name, time.time())
        return {
            "download_url": "/api/projects/download/" + export_id,
            "filename": name,
            "size_bytes": path.stat().st_size,
            "trace_cached": manifest["trace_cached"],
            "elapsed_sec": time.perf_counter() - started,
        }

    def _validate_workspace_source_path(self, value: object) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Workspace CAD path is required")
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError("Workspace CAD source is unavailable")
        upload_root = self.upload_dir.resolve()
        with self._state_lock:
            known = path in self._known_scene_sources or str(path) in self._portable_sources
        try:
            path.relative_to(upload_root)
            in_uploads = True
        except ValueError:
            in_uploads = False
        if not in_uploads and not known:
            raise ValueError("Workspace CAD source was not registered by this runtime")
        return path

    def export_workspace(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("BITSAM workspace request is required")
        workspace_name = request.get("workspace_name")
        active_case_id = request.get("active_case_id")
        raw_cases = request.get("cases")
        if not isinstance(workspace_name, str) or not workspace_name.strip():
            raise ValueError("Workspace name is required")
        if len(workspace_name) > 256:
            raise ValueError("Workspace name is too long")
        if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_WORKSPACE_CASES:
            raise ValueError("BITSAM workspace requires 1 to 64 cases")

        normalized: list[dict[str, Any]] = []
        case_ids: set[str] = set()
        orders: set[int] = set()
        for index, item in enumerate(raw_cases):
            if not isinstance(item, dict):
                raise ValueError("Invalid BITSAM workspace case")
            case_id = item.get("case_id")
            order = item.get("order")
            name = item.get("name", "")
            note = item.get("note", "")
            visible = item.get("visible", True)
            project = item.get("project")
            accessory_paths = item.get("accessory_paths", [])
            if (
                not isinstance(case_id, str)
                or not case_id
                or len(case_id) > 128
                or case_id in case_ids
                or type(order) is not int
                or order < 0
                or order in orders
                or not isinstance(name, str)
                or not isinstance(note, str)
                or type(visible) is not bool
                or not isinstance(project, dict)
                or not isinstance(accessory_paths, list)
                or len(accessory_paths) > 64
            ):
                raise ValueError("Invalid or duplicate BITSAM workspace case")
            primary_path = self._validate_workspace_source_path(item.get("cad_path"))
            project_cad = project.get("cad")
            if not isinstance(project_cad, dict):
                raise ValueError("BITSAM CAD settings are required")
            primary_display_name = project_cad.get("display_name")
            if not isinstance(primary_display_name, str) or not primary_display_name.strip():
                primary_display_name = primary_path.name
            accessories: list[dict[str, Any]] = []
            accessory_ids: set[str] = set()
            for accessory_index, raw_accessory in enumerate(accessory_paths):
                if isinstance(raw_accessory, str):
                    raw_path: object = raw_accessory
                    accessory_id = "accessory-{}".format(accessory_index + 1)
                    resolved_raw_path = Path(raw_accessory).resolve()
                    with self._state_lock:
                        display_name = self._source_display_names.get(
                            resolved_raw_path,
                            resolved_raw_path.name,
                        )
                    accessory_visible = True
                elif isinstance(raw_accessory, dict):
                    raw_path = raw_accessory.get("path")
                    accessory_id = raw_accessory.get(
                        "accessory_id",
                        raw_accessory.get("accessoryId", "accessory-{}".format(accessory_index + 1)),
                    )
                    display_name = raw_accessory.get(
                        "display_name",
                        raw_accessory.get("displayName"),
                    )
                    accessory_visible = raw_accessory.get("visible", True)
                else:
                    raise ValueError("Invalid Workspace Accessory CAD")
                accessory_path = self._validate_workspace_source_path(raw_path)
                if display_name is None:
                    display_name = accessory_path.name
                if (
                    not isinstance(accessory_id, str)
                    or not accessory_id
                    or len(accessory_id) > 128
                    or accessory_id in accessory_ids
                    or not isinstance(display_name, str)
                    or not display_name.strip()
                    or len(display_name) > 512
                    or type(accessory_visible) is not bool
                    or accessory_path.suffix.lower() not in {".stp", ".step", ".obj", ".stl"}
                ):
                    raise ValueError("Invalid or duplicate Workspace Accessory CAD")
                accessory_ids.add(accessory_id)
                accessories.append({
                    "accessory_id": accessory_id,
                    "display_name": display_name.strip(),
                    "visible": accessory_visible,
                    "path": accessory_path,
                    "entry": "assets/{:04d}/accessory-{:04d}{}".format(
                        index + 1,
                        accessory_index + 1,
                        accessory_path.suffix.lower(),
                    ),
                })
            case_ids.add(case_id)
            orders.add(order)
            normalized.append({
                "case_id": case_id,
                "order": order,
                "name": name,
                "note": note,
                "visible": visible,
                "project": project,
                "primary_path": primary_path,
                "primary_display_name": primary_display_name.strip(),
                "accessories": accessories,
                "entry": "cases/{:04d}.bitsam".format(index + 1),
            })
        if not isinstance(active_case_id, str) or active_case_id not in case_ids:
            raise ValueError("Active workspace Case is invalid")

        # Original primary CADs are embedded in their Case packages and every
        # Accessory CAD is stored as an outer asset. Their aggregate size is a
        # cheap fail-closed lower bound before any geometry/package generation.
        minimum_content_bytes = sum(
            item["primary_path"].stat().st_size
            + sum(accessory["path"].stat().st_size for accessory in item["accessories"])
            for item in normalized
        )
        if minimum_content_bytes > MAX_WORKSPACE_BYTES:
            raise ValueError("BITSAM workspace exceeds the cumulative size limit")

        self._discard_stale_project_exports()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        export_id = uuid.uuid4().hex
        path = self.upload_dir / ("export_workspace_" + export_id + ".bitsam")
        temporary = path.with_suffix(".writing")
        started = time.perf_counter()
        trace_cached: dict[str, bool] = {}
        try:
            with tempfile.TemporaryDirectory(prefix="bitsam_workspace_export_", dir=self.upload_dir) as temporary_dir:
                case_files: dict[str, Path] = {}
                asset_files: dict[str, Path] = {}
                workspace_cases: list[dict[str, Any]] = []
                generation_budget = CumulativeByteBudget(MAX_WORKSPACE_BYTES)
                generation_budget.reserve(sum(
                    accessory["path"].stat().st_size
                    for item in normalized
                    for accessory in item["accessories"]
                ))
                for item in normalized:
                    scene_paths = [
                        item["primary_path"],
                        *(
                            accessory["path"]
                            for accessory in item["accessories"]
                            if accessory["visible"]
                        ),
                    ]
                    scene = self.load_composite_scene([str(value) for value in scene_paths])
                    token = str((scene.get("metadata") or {}).get("scene_token") or "")
                    case_path = Path(temporary_dir) / Path(item["entry"]).name
                    inner_manifest = self._write_case_package(token, item["project"], case_path)
                    generation_budget.reserve(case_path.stat().st_size)
                    trace_cached[item["case_id"]] = bool(inner_manifest["trace_cached"])
                    case_files[item["entry"]] = case_path
                    workspace_cases.append({
                        "case_id": item["case_id"],
                        "order": item["order"],
                        "name": item["name"],
                        "note": item["note"],
                        "visible": item["visible"],
                        "entry": item["entry"],
                        "source": {"display_name": item["primary_display_name"]},
                        "accessories": [
                            {
                                "accessory_id": accessory["accessory_id"],
                                "display_name": accessory["display_name"],
                                "visible": accessory["visible"],
                                "entry": accessory["entry"],
                            }
                            for accessory in item["accessories"]
                        ],
                    })
                    for accessory in item["accessories"]:
                        asset_files[accessory["entry"]] = accessory["path"]
                workspace = {
                    "format": WORKSPACE_FORMAT,
                    "schema_version": WORKSPACE_SCHEMA,
                    "workspace_name": workspace_name.strip(),
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "active_case_id": active_case_id,
                    "cases": workspace_cases,
                }
                write_workspace_package(temporary, workspace, case_files, asset_files)
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            raise

        name = self._download_name(workspace_name, "workspace")
        with self._state_lock:
            self._project_exports[export_id] = (path, name, time.time())
        return {
            "scope": "workspace",
            "download_url": "/api/projects/download/" + export_id,
            "filename": name,
            "size_bytes": path.stat().st_size,
            "case_count": len(normalized),
            "trace_cached": all(trace_cached.values()),
            "case_trace_cached": trace_cached,
            "elapsed_sec": time.perf_counter() - started,
        }

    def project_export_file(self, export_id: str) -> tuple[Path, str]:
        with self._state_lock:
            record = self._project_exports.get(export_id)
        if record is None:
            raise ValueError("BITSAM download expired. Save again.")
        return record[0], record[1]

    def discard_project_export(self, export_id: str) -> None:
        with self._state_lock:
            record = self._project_exports.pop(export_id, None)
        if record is not None:
            record[0].unlink(missing_ok=True)

    def import_project(self, package: Path) -> dict[str, Any]:
        schema = package_schema(package)
        if schema == PACKAGE_SCHEMA:
            return self._import_case_project(package)
        if schema == WORKSPACE_SCHEMA:
            return self._import_workspace_project(package)
        raise ValueError("Unsupported BITSAM package version")

    @staticmethod
    def _validate_imported_project(
        project: dict[str, Any],
        payload: dict[str, Any],
        *,
        require_geometry_signature: bool = False,
    ) -> None:
        if (
            project.get("schema_version") not in {
                "bitsam-project.v1",
                "bitsam-project.v1-bsdf1",
                "bitsam-project.v1-preview1",
                "bitsam-project.v1-escape1",
            }
            or not isinstance(project.get("workspace"), dict)
        ):
            raise ValueError("Invalid BITSAM settings")
        has_preview = isinstance(project.get("leak_preview"), dict)
        if (
            project.get("schema_version") in {"bitsam-project.v1-preview1", "bitsam-project.v1-escape1"}
        ) != has_preview:
            raise ValueError("Invalid BITSAM Leak Preview settings")
        if (project.get("schema_version") == "bitsam-project.v1-escape1") != (
            has_preview and project["leak_preview"].get("detectionMode") == "escape_directions"
        ):
            raise ValueError("Directional escape settings require bitsam-project.v1-escape1")
        cad = project.get("cad")
        if not isinstance(cad, dict):
            raise ValueError("Invalid BITSAM CAD settings")
        fingerprint = cad.get("fingerprint") or {}
        if not isinstance(fingerprint, dict):
            raise ValueError("Invalid BITSAM CAD fingerprint")
        metadata = payload.get("metadata") or {}
        for key in ("face_count", "vertex_count", "component_count"):
            if fingerprint.get(key) != metadata.get(key):
                raise ValueError("BITSAM geometry and settings fingerprint mismatch")
        expected_schema = fingerprint.get("scene_schema_version")
        if expected_schema is not None and expected_schema != payload.get("schema_version"):
            raise ValueError("BITSAM geometry and settings fingerprint mismatch")
        expected_signature = fingerprint.get("geometry_signature")
        if require_geometry_signature and not isinstance(expected_signature, str):
            raise ValueError("BITSAM Workspace Case geometry signature is missing")
        if expected_signature is not None:
            if (
                not isinstance(expected_signature, str)
                or expected_signature != scene_geometry_signature(payload)
            ):
                raise ValueError("BITSAM geometry and settings fingerprint mismatch")

    def _commit_portable_cases(
        self,
        cases: list[tuple[Path, Path, dict[str, Any]]],
        *,
        composites: list[tuple[tuple[str, ...], Path]] | None = None,
        additional_sources: list[Path] | None = None,
        display_names: dict[Path, str] | None = None,
    ) -> None:
        """Publish already validated portable sources as one atomic state update."""
        with self._state_lock:
            for source, directory, payload in cases:
                self._portable_sources[str(source)] = directory
                self._known_scene_sources.add(source)
                self._scene_payload_cache[self._scene_cache_key(str(source))] = payload
            for source_key, directory in composites or []:
                self._portable_composite_sources[source_key] = directory
            self._known_scene_sources.update(additional_sources or [])
            self._source_display_names.update(display_names or {})
            while len(self._scene_payload_cache) > self._max_cached_scenes:
                self._scene_payload_cache.pop(next(iter(self._scene_payload_cache)), None)

    def _import_case_project(self, package: Path) -> dict[str, Any]:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        destination = self.upload_dir / ("project_" + uuid.uuid4().hex)
        try:
            with tempfile.TemporaryDirectory(prefix="bitsam_", dir=self.upload_dir) as temporary:
                staging = Path(temporary)
                manifest = extract_package(package, staging)
                project = read_json(staging / "project.json")
                self._validate_imported_project(project, self._load_portable_scene(staging))
                staging.rename(destination)
            # Re-open after the atomic move so a deferred trace loader captures
            # the permanent embedded CAD path rather than the staging path.
            payload = self._load_portable_scene(destination)
            source = (destination / manifest["source"]).resolve()
            self._commit_portable_cases(
                [(source, destination, payload)],
                display_names={source: project["cad"]["display_name"]},
            )
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        elapsed = time.perf_counter() - started
        return {
            "project": project,
            "cad": {"path": str(source), "displayName": project["cad"]["display_name"]},
            "trace_cached": manifest["trace_cached"],
            "elapsed_sec": elapsed,
        }

    def _import_workspace_project(self, package: Path) -> dict[str, Any]:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        destination = self.upload_dir / ("project_workspace_" + uuid.uuid4().hex)
        validated: list[dict[str, Any]] = []
        try:
            with tempfile.TemporaryDirectory(prefix="bitsam_workspace_", dir=self.upload_dir) as temporary:
                staging = Path(temporary)
                outer = extract_workspace_package(package, staging)
                workspace = outer["workspace"]
                entry_metadata = outer["manifest"]["entries"]
                accessory_entries = {
                    accessory["entry"]
                    for case in workspace["cases"]
                    for accessory in case["accessories"]
                }
                accessory_bytes = sum(
                    entry_metadata[entry]["size"] for entry in accessory_entries
                )
                retained_outer_bytes = (
                    accessory_bytes
                    + entry_metadata["workspace.json"]["size"]
                    + (staging / "manifest.json").stat().st_size
                )

                # Validate every nested archive and the shared final-size
                # budget before extracting the first Case. Each V2 archive may
                # be valid below 8 GiB on its own while their sum is not.
                preflight_budget = CumulativeByteBudget(MAX_WORKSPACE_BYTES)
                preflight_budget.reserve(retained_outer_bytes)
                for case in workspace["cases"]:
                    preflight_budget.reserve(package_expanded_size(staging / case["entry"]))

                extraction_budget = CumulativeByteBudget(MAX_WORKSPACE_BYTES)
                extraction_budget.reserve(retained_outer_bytes)
                for index, case in enumerate(workspace["cases"]):
                    entry = staging / case["entry"]
                    relative_directory = Path("restored") / "{:04d}".format(index + 1)
                    case_directory = staging / relative_directory
                    manifest = extract_package(
                        entry,
                        case_directory,
                        byte_budget=extraction_budget,
                    )
                    project = read_json(case_directory / "project.json")
                    payload = self._load_portable_scene(case_directory)
                    self._validate_imported_project(
                        project,
                        payload,
                        require_geometry_signature=True,
                    )
                    visible_accessories = [
                        accessory
                        for accessory in case["accessories"]
                        if accessory["visible"]
                    ]
                    saved_sources = (payload.get("metadata") or {}).get("source_files")
                    saved_source_count = len(saved_sources) if isinstance(saved_sources, list) else 1
                    if saved_source_count != 1 + len(visible_accessories):
                        raise ValueError("Workspace Accessory CAD composition does not match the saved Case")
                    if visible_accessories and not manifest["trace_cached"]:
                        raise ValueError("Workspace Accessory CAD Case is missing its combined trace cache")
                    validated.append({
                        "metadata": case,
                        "relative_directory": relative_directory,
                        "manifest": manifest,
                        "project": project,
                    })
                # The extracted, validated V2 contents are the runtime copy.
                # Keeping the nested ZIPs would nearly double restored storage.
                for item in validated:
                    (staging / item["metadata"]["entry"]).unlink()
                staging.rename(destination)

            portable_cases: list[tuple[Path, Path, dict[str, Any]]] = []
            portable_composites: list[tuple[tuple[str, ...], Path]] = []
            trusted_sources: list[Path] = []
            trusted_display_names: dict[Path, str] = {}
            response_cases: list[dict[str, Any]] = []
            for item in validated:
                case_directory = destination / item["relative_directory"]
                payload = self._load_portable_scene(case_directory)
                source = (case_directory / item["manifest"]["source"]).resolve()
                metadata = item["metadata"]
                project = item["project"]
                accessory_cads: list[dict[str, Any]] = []
                visible_paths: list[Path] = []
                for accessory in metadata["accessories"]:
                    accessory_path = (destination / accessory["entry"]).resolve()
                    accessory_cads.append({
                        "accessoryId": accessory["accessory_id"],
                        "cad": {
                            "path": str(accessory_path),
                            "displayName": accessory["display_name"],
                        },
                        "visible": accessory["visible"],
                    })
                    trusted_sources.append(accessory_path)
                    trusted_display_names[accessory_path] = accessory["display_name"]
                    if accessory["visible"]:
                        visible_paths.append(accessory_path)
                trusted_sources.append(source)
                trusted_display_names[source] = metadata["source"]["display_name"]
                if visible_paths:
                    portable_composites.append((
                        tuple(str(value) for value in [source, *visible_paths]),
                        case_directory,
                    ))
                else:
                    portable_cases.append((source, case_directory, payload))
                response_cases.append({
                    "case_id": metadata["case_id"],
                    "order": metadata["order"],
                    "name": metadata["name"],
                    "note": metadata["note"],
                    "visible": metadata["visible"],
                    "project": project,
                    "cad": {
                        "path": str(source),
                        "displayName": metadata["source"]["display_name"],
                    },
                    "accessory_cads": accessory_cads,
                    "trace_cached": item["manifest"]["trace_cached"],
                })
            self._commit_portable_cases(
                portable_cases,
                composites=portable_composites,
                additional_sources=trusted_sources,
                display_names=trusted_display_names,
            )
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise

        return {
            "scope": "workspace",
            "workspace_name": workspace["workspace_name"],
            "active_case_id": workspace["active_case_id"],
            "cases": response_cases,
            "elapsed_sec": time.perf_counter() - started,
        }

    def _portable_trace_loader(self, source: Path, viewer: dict[str, Any]) -> Callable[[], dict[str, Any]]:
        def load() -> dict[str, Any]:
            rebuilt = self._scene_loader(str(source))
            if mesh_identity(rebuilt["mesh"]) != mesh_identity(viewer):
                raise ValueError(
                    "Embedded CAD tessellation changed. Face bindings cannot be reused safely; "
                    "re-import the model and review face assignments."
                )
            loader = rebuilt.get("_trace_mesh_loader")
            return loader() if callable(loader) else rebuilt.get("_trace_mesh") or rebuilt["mesh"]
        return load

    def _load_portable_scene(self, directory: Path) -> dict[str, Any]:
        started = time.perf_counter()
        manifest = read_json(directory / "manifest.json")
        payload = read_scene_cache(directory / "scene.bin")
        source = directory / manifest["source"]
        payload["metadata"]["source_file"] = str(source)
        payload["metadata"]["portable_project"] = True
        payload["metadata"]["trace_mesh_deferred"] = not manifest["trace_cached"]
        if manifest["trace_cached"]:
            payload["_trace_mesh"] = read_scene_cache(directory / "trace.bin")["mesh"]
        else:
            payload["_trace_mesh_loader"] = self._portable_trace_loader(source, payload["mesh"])
        elapsed = time.perf_counter() - started
        payload["metadata"]["import_timings_sec"] = {"package_restore": elapsed, "scene_payload_total": elapsed}
        return payload

    def save_upload(
        self,
        raw_name: str,
        content: bytes,
    ) -> dict[str, Any]:
        if not content:
            raise ValueError("Uploaded file is empty")
        safe_name = self._safe_upload_filename(raw_name)
        digest = hashlib.sha256(content).hexdigest()
        with self._state_lock:
            self.upload_dir.mkdir(parents=True, exist_ok=True)
            target_path = self.upload_dir / "{}{}".format(
                digest,
                Path(safe_name).suffix.lower(),
            )
            cache_hit = target_path.exists()
            if not cache_hit:
                temporary_path = target_path.with_suffix(
                    target_path.suffix + ".uploading"
                )
                temporary_path.write_bytes(content)
                temporary_path.replace(target_path)
            self._source_display_names[target_path.resolve()] = safe_name
        return {
            "ok": True,
            "display_name": safe_name,
            "path": str(target_path),
            "content_hash": digest,
            "cache_hit": cache_hit,
        }

    def start_raytrace_job(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        validate_bsdf_request(request_payload)
        scene_mesh = self._scene_mesh_for_request(request_payload)
        requested_ray_count = sum(
            max(0, int(emitter.get("ray_count", 0)))
            for emitter in request_payload.get("emitters", [])
            if isinstance(emitter, dict) and emitter.get("enabled", True)
        )
        job_id = uuid.uuid4().hex
        job = {
            "job_id": job_id,
            "status": "queued",
            "phase": "queued",
            "phase_detail": "실행 대기 중",
            "processed_rays": 0,
            "total_rays": requested_ray_count,
            "progress": 0.0,
            "elapsed_sec": 0.0,
            "estimated_remaining_sec": None,
            "rays_per_sec": 0.0,
            "stop_requested": False,
            "stopped_early": False,
            "created_at": time.time(),
        }
        with self._state_lock:
            superseded_job_count = 0
            for existing_job in self._raytrace_jobs.values():
                if existing_job.get("status") not in {"queued", "running"}:
                    continue
                existing_job["stop_requested"] = True
                existing_job["phase"] = "stopping"
                superseded_job_count += 1
            self._raytrace_jobs[job_id] = job
            self._prune_raytrace_jobs_locked()

        config = request_payload.get("config")
        if not isinstance(config, dict):
            config = {}
        print(
            "[RAY] job start | {} | rays={} depth={} backend={} | stopped_previous={}".format(
                job_id[:8],
                requested_ray_count,
                config.get("max_depth", "?"),
                config.get("intersection_backend", "auto"),
                superseded_job_count,
            ),
            flush=True,
        )

        worker = threading.Thread(
            target=self._run_raytrace_job,
            args=(job_id, scene_mesh, request_payload),
            daemon=True,
            name="raytrace-{}".format(job_id[:8]),
        )
        worker.start()
        with self._state_lock:
            return dict(self._raytrace_jobs[job_id])

    def raytrace_job_snapshot(
        self,
        job_id: str,
    ) -> Optional[dict[str, Any]]:
        with self._state_lock:
            job = self._raytrace_jobs.get(job_id)
            return dict(job) if job is not None else None

    def stop_raytrace_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._state_lock:
            job = self._raytrace_jobs.get(job_id)
            if job is None:
                return None
            if job.get("status") in {"queued", "running"}:
                job["stop_requested"] = True
                job["phase"] = "stopping"
            return dict(job)

    def run_raytrace_direct(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        validate_bsdf_request(request_payload)
        scene_mesh = self._scene_mesh_for_request(request_payload)
        trace_input = self._build_trace_input_for_request(
            scene_mesh,
            request_payload,
        )
        return self._trace_runner(trace_input).to_dict()

    def build_section_cap(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        scene_token = str(request_payload.get("scene_token") or "")
        with self._state_lock:
            viewer_mesh = self._scene_viewer_mesh_cache.get(scene_token)
        if viewer_mesh is None:
            raise ValueError(
                "CAD scene cache expired. Reload the CAD model and try again"
            )
        return build_section_cap_contours(
            viewer_mesh,
            axis=str(request_payload.get("axis") or ""),
            position=float(request_payload.get("position", 0.0)),
            hidden_component_ids=[
                int(value)
                for value in request_payload.get("hidden_component_ids", [])
            ],
            transform_rules=[
                value
                for value in request_payload.get("transform_rules", [])
                if isinstance(value, dict)
            ],
        )

    def register_output_file(self, path_text: Optional[str]) -> None:
        if not path_text:
            return
        path = Path(path_text).resolve()
        if path.exists():
            with self._state_lock:
                self._output_file_index[path.name] = path

    def resolve_output_file(self, output_name: str) -> Optional[Path]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", output_name):
            raise ValueError("Invalid file name")
        with self._state_lock:
            registered = self._output_file_index.get(output_name)
        path = registered or (self.output_dir / output_name)
        return path if path.is_file() else None

    def _scene_mesh_for_request(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        scene_token = str(request_payload.get("scene_token") or "")
        with self._state_lock:
            if request_payload.get("geometry_mode") == "preview":
                scene_mesh = self._scene_viewer_mesh_cache.get(scene_token)
            else:
                scene_mesh = self._scene_mesh_cache.get(scene_token)
        if scene_mesh is None:
            raise ValueError(
                "CAD scene cache expired. Reload the CAD model and run again"
            )
        return scene_mesh

    def _trace_geometry_cache_key(
        self,
        scene_mesh: dict[str, Any],
        request_payload: dict[str, Any],
    ) -> str:
        excluded = sorted(
            int(value)
            for value in request_payload.get("excluded_component_ids", [])
        )
        excluded_set = set(excluded)
        component_ids = scene_mesh.get("face_component_ids") or []
        preserved_emitter_faces = sorted({
            int(face_index)
            for emitter in request_payload.get("emitters", [])
            if str(emitter.get("emitter_type") or "face") == "face"
            for face_index in emitter.get("face_indices", [])
            if 0 <= int(face_index) < len(component_ids)
            and component_ids[int(face_index)] is not None
            and int(component_ids[int(face_index)]) in excluded_set
        })
        preserved_emitter_components = sorted({
            int(component_id)
            for emitter in request_payload.get("emitters", [])
            if str(emitter.get("emitter_type") or "face") == "face"
            for component_id in emitter.get("source_component_ids", [])
            if int(component_id) in excluded_set
        })
        geometry_state = {
            "scene_token": str(request_payload.get("scene_token") or ""),
            "geometry_mode": str(
                request_payload.get("geometry_mode") or "precision"
            ),
            "transform_rules": request_payload.get("transform_rules", []),
            "excluded_component_ids": excluded,
            "roi_faces": sorted(int(value) for value in request_payload.get("roi_faces", [])),
            "roi_clip_boxes": request_payload.get("roi_clip_boxes", []),
            "analysis_roi": request_payload.get("analysis_roi"),
            "roi_emitter_geometry": [{key: item.get(key) for key in (
                "emitter_id", "emitter_type", "face_indices", "source_component_ids", "center", "u_axis", "v_axis",
                "width_mm", "height_mm", "surface_construction", "polygon_vertices")}
                for item in request_payload.get("emitters", [])] if request_payload.get("analysis_roi") or request_payload.get("roi_faces") else [],
            "preserved_emitter_faces": preserved_emitter_faces,
            "preserved_emitter_components": preserved_emitter_components,
            "preview_blockers": request_payload.get("preview_blockers", []),
        }
        encoded = json.dumps(
            geometry_state,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_trace_input_for_request(
        self,
        scene_mesh: dict[str, Any],
        request_payload: dict[str, Any],
        should_stop: Optional[Callable[[], bool]] = None,
        report_phase: Optional[Callable[[str], None]] = None,
    ) -> Any:
        def phase(detail: str) -> None:
            if report_phase is not None:
                report_phase(detail)

        if should_stop is not None and should_stop():
            raise InterruptedError("Ray trace preparation stopped")
        phase("정밀 CAD Mesh 확인 중")
        scene_mesh = self._resolve_deferred_trace_mesh(scene_mesh)
        if should_stop is not None and should_stop():
            raise InterruptedError("Ray trace preparation stopped")
        phase("Emitter 및 ROI Surface 매핑 중")
        request_payload = self._map_viewer_faces_to_trace(
            scene_mesh,
            request_payload,
            should_stop,
        )
        if self._trace_input_builder is not build_direct_trace_input:
            phase("Ray Tracing 입력 구성 중")
            trace_input = self._trace_input_builder(scene_mesh, request_payload)
            if should_stop is not None and should_stop():
                raise InterruptedError("Ray trace preparation stopped")
            return trace_input
        phase("Geometry cache 확인 중")
        cache_key = self._trace_geometry_cache_key(scene_mesh, request_payload)
        with self._state_lock:
            # Refresh cache insertion order on a hit so the repeatedly used
            # Preview geometry survives experiments with several alternatives.
            prepared = self._trace_geometry_cache.pop(cache_key, None)
            if prepared is not None:
                self._trace_geometry_cache[cache_key] = prepared
            build_state = self._trace_geometry_builds.get(cache_key)
            is_builder = prepared is None and build_state is None
            if is_builder:
                build_state = _TraceGeometryBuildState()
                self._trace_geometry_builds[cache_key] = build_state
        cache_hit = prepared is not None
        if prepared is None:
            assert build_state is not None
            if not is_builder:
                # A superseded/restarted Preview may request the same geometry
                # while the first worker is still preparing it. Reuse that
                # build instead of creating a second multi-million-face BVH.
                while not build_state.event.wait(timeout=0.05):
                    if should_stop is not None and should_stop():
                        raise InterruptedError("Ray trace preparation stopped")
                if build_state.error is not None:
                    if (
                        isinstance(build_state.error, InterruptedError)
                        and not (should_stop is not None and should_stop())
                    ):
                        return self._build_trace_input_for_request(
                            scene_mesh,
                            request_payload,
                            should_stop,
                            report_phase,
                        )
                    raise build_state.error
                prepared = build_state.prepared
                if prepared is None:
                    raise RuntimeError("Prepared geometry build completed without a result")
                cache_hit = True
            else:
                try:
                    phase("Trace Mesh 및 BVH 생성 중")
                    prepared = build_prepared_trace_geometry(
                        scene_mesh,
                        request_payload,
                        should_stop=should_stop,
                    )
                except Exception as exc:
                    with self._state_lock:
                        build_state.error = exc
                        self._trace_geometry_builds.pop(cache_key, None)
                        build_state.event.set()
                    raise
                with self._state_lock:
                    self._trace_geometry_cache[cache_key] = prepared
                    while len(self._trace_geometry_cache) > self._max_jobs:
                        oldest_key = next(iter(self._trace_geometry_cache))
                        self._trace_geometry_cache.pop(oldest_key, None)
                    build_state.prepared = prepared
                    self._trace_geometry_builds.pop(cache_key, None)
                    build_state.event.set()
        if cache_hit:
            phase("기존 Trace Mesh 및 BVH 불러오는 중")
        phase("Emitter 및 Receiver 계산 입력 구성 중")
        return build_direct_trace_input(
            scene_mesh,
            request_payload,
            prepared_geometry=prepared,
            geometry_cache_hit=cache_hit,
        )

    @staticmethod
    def _resolve_deferred_trace_mesh(
        scene_mesh: dict[str, Any],
    ) -> dict[str, Any]:
        loader = scene_mesh.get("_deferred_trace_loader")
        if not callable(loader):
            return scene_mesh
        lock = scene_mesh.get("_deferred_trace_lock")
        if lock is None:
            raise RuntimeError("Deferred trace mesh lock is unavailable")
        with lock:
            loader = scene_mesh.get("_deferred_trace_loader")
            if not callable(loader):
                return scene_mesh
            viewer_sources = scene_mesh.get("_viewer_face_source_ids") or []
            print("[CAD] deferred trace mesh      START", flush=True)
            started_at = time.perf_counter()
            resolved = loader()
            if not isinstance(resolved, dict):
                raise RuntimeError("Deferred trace mesh loader returned invalid data")
            scene_mesh.clear()
            scene_mesh.update(resolved)
            scene_mesh["_viewer_face_source_ids"] = viewer_sources
            print(
                "[CAD] deferred trace mesh   {:>8.3f}s | {} faces".format(
                    time.perf_counter() - started_at,
                    len(scene_mesh.get("faces") or []),
                ),
                flush=True,
            )
            return scene_mesh

    @staticmethod
    def _map_viewer_faces_to_trace(
        scene_mesh: dict[str, Any],
        request_payload: dict[str, Any],
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> dict[str, Any]:
        """Expand display-face references to precision trace triangles.

        Both tessellations carry the same authored B-rep source-face ID. This
        keeps ROI, face emitters and face-level material assignments exact even
        when one display triangle represents many simulation triangles.
        """
        viewer_sources = scene_mesh.get("_viewer_face_source_ids")
        trace_sources = scene_mesh.get("face_source_ids")
        if not viewer_sources or not trace_sources:
            return request_payload

        normalized = dict(request_payload)
        emitters = [
            dict(item) if isinstance(item, dict) else item
            for item in request_payload.get("emitters", [])
        ]
        assignments = [
            dict(item) if isinstance(item, dict) else item
            for item in request_payload.get("optical_assignments", [])
        ]

        referenced_viewer_faces: set[int] = set()
        for emitter in emitters:
            if (
                isinstance(emitter, dict)
                and str(emitter.get("emitter_type") or "face") == "face"
            ):
                referenced_viewer_faces.update(
                    int(value) for value in emitter.get("face_indices", [])
                )
        referenced_viewer_faces.update(
            int(value) for value in request_payload.get("roi_faces", [])
        )
        for assignment in assignments:
            if (
                isinstance(assignment, dict)
                and assignment.get("target_type") == "faces"
            ):
                referenced_viewer_faces.update(
                    int(value) for value in assignment.get("face_indices", [])
                )
        if not referenced_viewer_faces:
            return request_payload

        requested_sources = {
            int(viewer_sources[face_index])
            for face_index in referenced_viewer_faces
            if 0 <= face_index < len(viewer_sources)
        }
        source_to_trace: dict[int, list[int]] = {
            source_id: [] for source_id in requested_sources
        }
        for trace_face_index, source_id in enumerate(trace_sources):
            if (
                trace_face_index % 4096 == 0
                and should_stop is not None
                and should_stop()
            ):
                raise InterruptedError("Ray trace preparation stopped")
            target = source_to_trace.get(int(source_id))
            if target is not None:
                target.append(trace_face_index)

        def expand(face_indices: Any) -> list[int]:
            source_ids = {
                int(viewer_sources[int(face_index)])
                for face_index in (face_indices or [])
                if 0 <= int(face_index) < len(viewer_sources)
            }
            return [
                trace_face_index
                for source_id in source_ids
                for trace_face_index in source_to_trace.get(source_id, [])
            ]

        for emitter in emitters:
            if (
                isinstance(emitter, dict)
                and str(emitter.get("emitter_type") or "face") == "face"
            ):
                emitter["face_indices"] = expand(emitter.get("face_indices"))
        for assignment in assignments:
            if (
                isinstance(assignment, dict)
                and assignment.get("target_type") == "faces"
            ):
                assignment["face_indices"] = expand(
                    assignment.get("face_indices")
                )

        normalized["emitters"] = emitters
        normalized["optical_assignments"] = assignments
        if request_payload.get("roi_faces"):
            normalized["roi_faces"] = expand(request_payload.get("roi_faces"))
        return normalized

    @staticmethod
    def _scene_cache_key(cad_path: str) -> str:
        path = Path(cad_path)
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            return cad_path
        return "{}|{}|{}".format(
            resolved,
            stat.st_size,
            stat.st_mtime_ns,
        )

    def _update_raytrace_job(
        self,
        job_id: str,
        **updates: Any,
    ) -> None:
        with self._state_lock:
            job = self._raytrace_jobs.get(job_id)
            if job is not None:
                job.update(updates)

    def _run_raytrace_job(
        self,
        job_id: str,
        scene_mesh: dict[str, Any],
        request_payload: dict[str, Any],
    ) -> None:
        try:
            def should_stop() -> bool:
                with self._state_lock:
                    job = self._raytrace_jobs.get(job_id)
                    return bool(job and job.get("stop_requested"))

            preparation_started_at = time.time()
            def report_preparation_phase(detail: str) -> None:
                self._update_raytrace_job(
                    job_id,
                    status="running",
                    phase="preparing",
                    phase_detail=detail,
                    elapsed_sec=max(0.0, time.time() - preparation_started_at),
                )

            self._update_raytrace_job(
                job_id,
                status="running",
                phase="preparing",
                phase_detail="Ray Tracing 준비 시작",
            )
            trace_input = self._build_trace_input_for_request(
                scene_mesh,
                request_payload,
                should_stop,
                report_preparation_phase,
            )
            preparation_elapsed_sec = max(0.0, time.time() - preparation_started_at)
            geometry_cache_hit = bool(
                getattr(trace_input, "geometry_cache_hit", False)
            )
            print(
                "[RAY] geometry ready | {} | cache={} | {:.3f}s".format(
                    job_id[:8],
                    "hit" if geometry_cache_hit else "rebuilt",
                    preparation_elapsed_sec,
                ),
                flush=True,
            )
            total_ray_count = sum(
                emitter.ray_count
                for emitter in trace_input.emitters
                if emitter.enabled
            )
            trace_started_at = time.time()
            self._update_raytrace_job(
                job_id,
                phase="tracing",
                phase_detail="Ray 계산 진행 중",
                processed_rays=0,
                total_rays=total_ray_count,
                progress=0.0,
                elapsed_sec=0.0,
                estimated_remaining_sec=None,
                geometry_cache_hit=geometry_cache_hit,
                preparation_elapsed_sec=preparation_elapsed_sec,
            )

            def report_progress(
                processed_rays: int,
                total_rays: int,
            ) -> None:
                elapsed_sec = max(
                    0.0,
                    time.time() - trace_started_at,
                )
                safe_total = max(0, int(total_rays))
                safe_processed = max(
                    0,
                    min(int(processed_rays), safe_total),
                )
                progress = (
                    safe_processed / safe_total
                    if safe_total > 0
                    else 0.0
                )
                ray_rate = (
                    safe_processed / elapsed_sec
                    if safe_processed > 0 and elapsed_sec > 0.0
                    else 0.0
                )
                estimated_remaining_sec = (
                    max(0.0, safe_total - safe_processed) / ray_rate
                    if ray_rate > 0.0
                    else None
                )
                ray_dispatch_complete = (
                    safe_total > 0 and safe_processed >= safe_total
                )
                self._update_raytrace_job(
                    job_id,
                    phase=(
                        "stopping"
                        if should_stop()
                        else "aggregating"
                        if ray_dispatch_complete
                        else "tracing"
                    ),
                    phase_detail=(
                        "중단 요청 처리 및 부분 결과 정리 중"
                        if should_stop()
                        else "Ray 계산 완료 · Receiver·기여도 집계 중"
                        if ray_dispatch_complete
                        else "Ray 계산 진행 중"
                    ),
                    processed_rays=safe_processed,
                    total_rays=safe_total,
                    # 100% is reserved for a completed, downloadable result.
                    # The tracer may still be reducing Receiver grids and path
                    # contributions after dispatching the final Ray batch.
                    progress=min(progress, 0.999),
                    elapsed_sec=elapsed_sec,
                    estimated_remaining_sec=(
                        None if ray_dispatch_complete else estimated_remaining_sec
                    ),
                    rays_per_sec=ray_rate,
                )

            result = self._trace_runner(
                trace_input,
                progress_callback=report_progress,
                should_stop=should_stop,
            )
            if not should_stop():
                self._update_raytrace_job(
                    job_id,
                    status="running",
                    phase="serializing",
                    phase_detail="분석 결과·Stored Path 정리 중",
                    progress=0.999,
                    estimated_remaining_sec=None,
                )
            result_payload = result.to_dict()
            processed_ray_count = max(
                0,
                min(int(result_payload.get("total_rays", 0)), total_ray_count),
            )
            stopped_early = should_stop() and processed_ray_count < total_ray_count
            self._update_raytrace_job(
                job_id,
                status="completed",
                phase="stopped" if stopped_early else "completed",
                phase_detail=(
                    "부분 결과 정리 완료"
                    if stopped_early
                    else "Ray Tracing 완료"
                ),
                processed_rays=processed_ray_count,
                total_rays=total_ray_count,
                progress=(
                    processed_ray_count / total_ray_count
                    if total_ray_count > 0
                    else 0.0
                ),
                elapsed_sec=max(
                    0.0,
                    time.time() - trace_started_at,
                ),
                estimated_remaining_sec=0.0,
                result=result_payload,
                stopped_early=stopped_early,
                completed_at=time.time(),
            )
        except InterruptedError:
            self._update_raytrace_job(
                job_id,
                status="cancelled",
                phase="stopped",
                phase_detail="준비 작업 중단 완료",
                stopped_early=True,
                estimated_remaining_sec=0.0,
                completed_at=time.time(),
            )
        except Exception as exc:
            self._update_raytrace_job(
                job_id,
                status="failed",
                phase="failed",
                phase_detail="Ray Tracing 오류 발생",
                error=str(exc),
                estimated_remaining_sec=None,
                completed_at=time.time(),
            )

    def _prune_raytrace_jobs_locked(self) -> None:
        if len(self._raytrace_jobs) <= self._max_jobs:
            return
        removable = sorted(
            (
                (job_id, float(job.get("created_at", 0.0)))
                for job_id, job in self._raytrace_jobs.items()
                if job.get("status") in {"completed", "cancelled", "failed"}
            ),
            key=lambda item: item[1],
        )
        for job_id, _ in removable:
            if len(self._raytrace_jobs) <= self._max_jobs:
                break
            self._raytrace_jobs.pop(job_id, None)

    def _prepare_upload_path(
        self,
        raw_name: str,
    ) -> tuple[Path, str]:
        safe_name = self._safe_upload_filename(raw_name)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        candidate = self.upload_dir / safe_name
        stem = candidate.stem
        suffix = candidate.suffix
        index = 1
        while candidate.exists():
            candidate = self.upload_dir / "{}_{}{}".format(
                stem,
                index,
                suffix,
            )
            index += 1
        return candidate, safe_name

    @staticmethod
    def _safe_upload_filename(raw_name: str) -> str:
        base_name = Path(raw_name or "").name.strip()
        if not base_name:
            raise ValueError("CAD filename is required")
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name)
        allowed_suffixes = (
            ".obj",
            ".stl",
            ".stp",
            ".step",
        )
        if not normalized.lower().endswith(allowed_suffixes):
            raise ValueError(
                "Supported CAD formats: .obj, .stl, .stp, .step. "
                "For component names/colors, export STEP AP214 or AP242. "
                "Parasolid .x_t is not supported by the current runtime."
            )
        return normalized
