from __future__ import annotations

import re
import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from leakage_simulator.raytrace_bridge import (
    PreparedTraceGeometry,
    build_direct_trace_input,
    build_prepared_trace_geometry,
)
from leakage_simulator.raytracer import run_direct_ray_trace
from leakage_simulator.roi import build_scene_payload


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
        self._scene_payload_cache: dict[str, dict[str, Any]] = {}
        self._trace_geometry_cache: dict[str, PreparedTraceGeometry] = {}
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
                payload = self._scene_loader(cad_path)
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
            while len(self._scene_mesh_cache) > self._max_cached_scenes:
                oldest_token = next(iter(self._scene_mesh_cache))
                self._scene_mesh_cache.pop(oldest_token, None)

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

    def save_upload(
        self,
        raw_name: str,
        content: bytes,
    ) -> dict[str, Any]:
        if not content:
            raise ValueError("Uploaded file is empty")
        with self._state_lock:
            target_path, display_name = self._prepare_upload_path(
                raw_name
            )
            target_path.write_bytes(content)
        return {
            "ok": True,
            "display_name": display_name,
            "path": str(target_path),
        }

    def start_raytrace_job(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
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
        scene_mesh = self._scene_mesh_for_request(request_payload)
        trace_input = self._build_trace_input_for_request(
            scene_mesh,
            request_payload,
        )
        return self._trace_runner(trace_input).to_dict()

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
        geometry_state = {
            "scene_token": str(request_payload.get("scene_token") or ""),
            "transform_rules": request_payload.get("transform_rules", []),
            "excluded_component_ids": excluded,
            "roi_faces": sorted(int(value) for value in request_payload.get("roi_faces", [])),
            "preserved_emitter_faces": preserved_emitter_faces,
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
    ) -> Any:
        scene_mesh = self._resolve_deferred_trace_mesh(scene_mesh)
        request_payload = self._map_viewer_faces_to_trace(
            scene_mesh,
            request_payload,
        )
        if self._trace_input_builder is not build_direct_trace_input:
            return self._trace_input_builder(scene_mesh, request_payload)
        cache_key = self._trace_geometry_cache_key(scene_mesh, request_payload)
        with self._state_lock:
            prepared = self._trace_geometry_cache.get(cache_key)
        cache_hit = prepared is not None
        if prepared is None:
            prepared = build_prepared_trace_geometry(scene_mesh, request_payload)
            with self._state_lock:
                self._trace_geometry_cache[cache_key] = prepared
                while len(self._trace_geometry_cache) > self._max_jobs:
                    oldest_key = next(iter(self._trace_geometry_cache))
                    self._trace_geometry_cache.pop(oldest_key, None)
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
            self._update_raytrace_job(
                job_id,
                status="running",
                phase="preparing",
            )
            trace_input = self._build_trace_input_for_request(
                scene_mesh,
                request_payload,
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
                processed_rays=0,
                total_rays=total_ray_count,
                progress=0.0,
                elapsed_sec=0.0,
                estimated_remaining_sec=None,
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
                self._update_raytrace_job(
                    job_id,
                    phase="tracing",
                    processed_rays=safe_processed,
                    total_rays=safe_total,
                    progress=progress,
                    elapsed_sec=elapsed_sec,
                    estimated_remaining_sec=estimated_remaining_sec,
                    rays_per_sec=ray_rate,
                )

            def should_stop() -> bool:
                with self._state_lock:
                    job = self._raytrace_jobs.get(job_id)
                    return bool(job and job.get("stop_requested"))

            result = self._trace_runner(
                trace_input,
                progress_callback=report_progress,
                should_stop=should_stop,
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
        except Exception as exc:
            self._update_raytrace_job(
                job_id,
                status="failed",
                phase="failed",
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
                if job.get("status") in {"completed", "failed"}
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
