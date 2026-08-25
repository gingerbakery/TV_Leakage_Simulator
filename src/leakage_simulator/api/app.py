from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from leakage_simulator.scene_binary import (
    MEDIA_TYPE,
    iter_scene_binary,
    prepare_scene_binary,
)

from .runtime import ApiRuntime

API_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parents[3]


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": message},
    )


def create_app(
    runtime: ApiRuntime | None = None,
) -> FastAPI:
    api_runtime = runtime or ApiRuntime(ROOT)
    boot_token = os.environ.get("LEAKAGE_BOOT_TOKEN")
    if boot_token is None:
        boot_token = str(time.time_ns())
    application = FastAPI(
        title="TV Leakage Simulator API",
        version=API_VERSION,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    application.state.api_runtime = api_runtime
    frontend_dist = api_runtime.root / "frontend" / "dist"
    frontend_index = frontend_dist / "index.html"
    frontend_assets = frontend_dist / "assets"
    if frontend_assets.is_dir():
        application.mount(
            "/assets",
            StaticFiles(directory=frontend_assets),
            name="frontend-assets",
        )

    @application.get("/")
    def root_status() -> Any:
        if frontend_index.is_file():
            return FileResponse(
                frontend_index,
                media_type="text/html",
            )
        return {
            "ok": True,
            "service": "tv-leakage-simulator-api",
            "api_version": API_VERSION,
            "docs": "/api/docs",
        }

    @application.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        return "ok api_version={}".format(API_VERSION)

    @application.get("/dev-status", response_class=JSONResponse)
    def dev_status() -> dict[str, Any]:
        return {
            "ok": True,
            "web_ui_version": "api-{}".format(API_VERSION),
            "api_version": API_VERSION,
            "boot_token": boot_token,
        }

    @application.get("/_ping", response_class=PlainTextResponse)
    def ping() -> str:
        return "pong"

    @application.get(
        "/api/gpu-cuda/status",
        response_class=JSONResponse,
    )
    def gpu_cuda_status(refresh: bool = False) -> dict[str, Any]:
        """Probe CUDA only when a user explicitly requests this endpoint."""

        # Keep this import inside the endpoint.  App startup, health checks,
        # and the default CPU trace path must not discover Numba or CUDA.
        from leakage_simulator.gpu_cuda_intersection import (
            PROVIDER_CONTRACT,
            preflight_gpu_cuda,
        )

        capability = preflight_gpu_cuda(refresh=refresh)
        return {
            "available": capability.available,
            "reason_code": capability.reason_code,
            "device_name": capability.device_name,
            "compute_capability": capability.compute_capability,
            "device_id": capability.device_id,
            "numba_version": capability.numba_version,
            "toolkit_layout": capability.toolkit_layout,
            "strict_float64": capability.strict_float64,
            "kernel_executed": capability.kernel_executed,
            "kernel_verified": capability.kernel_verified,
            "preflight_scope": capability.preflight_scope,
            "provider_contract": PROVIDER_CONTRACT,
        }

    @application.get("/api/scene")
    def scene(cad: str = "", format: str = "json") -> Any:
        if not cad.strip():
            return _error(400, "CAD file is required")
        try:
            scene_started_at = time.perf_counter()
            payload = api_runtime.load_scene(cad)
            if format.strip().lower() == "binary":
                binary_started_at = time.perf_counter()
                manifest, blocks = prepare_scene_binary(payload)
                binary_bytes = (
                    16
                    + len(manifest)
                    + int(json.loads(manifest)["binary"]["byte_length"])
                )
                print(
                    "[CAD] {:<24} {:>8.3f}s | {:.2f} MB".format(
                        "Binary manifest",
                        time.perf_counter() - binary_started_at,
                        binary_bytes / (1024.0 * 1024.0),
                    ),
                    flush=True,
                )
                return StreamingResponse(
                    iter_scene_binary(manifest, blocks),
                    media_type=MEDIA_TYPE,
                    headers={
                        "Content-Length": str(binary_bytes),
                        "X-Bitsam-Scene-Format": "mesh-scene.v2-binary",
                    },
                )
            serialization_started_at = time.perf_counter()
            print(
                "[CAD] {:<24} {:>8}".format(
                    "JSON serialization",
                    "START",
                ),
                flush=True,
            )
            response = JSONResponse(content=payload)
            serialization_sec = (
                time.perf_counter() - serialization_started_at
            )
            print(
                "[CAD] {:<24} {:>8.3f}s | {:.2f} MB".format(
                    "JSON serialization",
                    serialization_sec,
                    len(response.body) / (1024.0 * 1024.0),
                ),
                flush=True,
            )
            print(
                "[CAD] {:<24} {:>8.3f}s | request complete".format(
                    "API scene total",
                    time.perf_counter() - scene_started_at,
                ),
                flush=True,
            )
            return response
        except (TypeError, ValueError) as exc:
            return _error(400, str(exc))
        except Exception as exc:
            return _error(500, str(exc))

    @application.post("/api/upload", response_class=JSONResponse)
    async def upload(request: Request, filename: str = "") -> Any:
        try:
            upload_started_at = time.perf_counter()
            content = await request.body()
            body_sec = time.perf_counter() - upload_started_at
            write_started_at = time.perf_counter()
            result = api_runtime.save_upload(filename, content)
            write_sec = time.perf_counter() - write_started_at
            print(
                "[CAD] upload received          {:>8.3f}s | {:.2f} MB".format(
                    body_sec,
                    len(content) / (1024.0 * 1024.0),
                ),
                flush=True,
            )
            print(
                "[CAD] upload saved             {:>8.3f}s | {}".format(
                    write_sec,
                    result["display_name"],
                ),
                flush=True,
            )
            return result
        except Exception as exc:
            return PlainTextResponse(
                "Upload failed: {}".format(exc),
                status_code=400,
            )

    @application.post(
        "/api/raytrace/start",
        status_code=202,
        response_class=JSONResponse,
    )
    def start_raytrace(
        payload: dict[str, Any] = Body(...),
    ) -> Any:
        try:
            return api_runtime.start_raytrace_job(payload)
        except (TypeError, ValueError) as exc:
            return _error(400, str(exc))
        except Exception as exc:
            return _error(500, str(exc))

    @application.get(
        "/api/raytrace/status",
        response_class=JSONResponse,
    )
    def raytrace_status(job_id: str = "") -> Any:
        job = api_runtime.raytrace_job_snapshot(job_id)
        if job is None:
            return _error(404, "Ray tracing job was not found")
        return job

    @application.post(
        "/api/raytrace/stop",
        response_class=JSONResponse,
    )
    def stop_raytrace(job_id: str = "") -> Any:
        job = api_runtime.stop_raytrace_job(job_id)
        if job is None:
            return _error(404, "Ray tracing job was not found")
        return job

    @application.post(
        "/api/raytrace/direct",
        response_class=JSONResponse,
    )
    def direct_raytrace(
        payload: dict[str, Any] = Body(...),
    ) -> Any:
        try:
            return api_runtime.run_raytrace_direct(payload)
        except (TypeError, ValueError) as exc:
            return _error(400, str(exc))
        except Exception as exc:
            return _error(500, str(exc))

    @application.get("/outputs/{output_name}")
    def output_file(output_name: str) -> Any:
        try:
            path = api_runtime.resolve_output_file(output_name)
        except ValueError as exc:
            return PlainTextResponse(str(exc), status_code=400)
        if path is None:
            return PlainTextResponse("Not found", status_code=404)
        media_type, _ = mimetypes.guess_type(path.name)
        return FileResponse(
            path,
            media_type=media_type or "application/octet-stream",
            filename=path.name,
            content_disposition_type="inline",
        )

    @application.get(
        "/{spa_path:path}",
        include_in_schema=False,
    )
    def frontend_route(spa_path: str) -> Any:
        reserved_prefixes = (
            "api/",
            "outputs/",
        )
        reserved_paths = {
            "_ping",
            "dev-status",
            "health",
        }
        if (
            spa_path.startswith(reserved_prefixes)
            or spa_path in reserved_paths
        ):
            return PlainTextResponse("Not found", status_code=404)
        if not frontend_index.is_file():
            return PlainTextResponse("Not found", status_code=404)

        requested_path = (frontend_dist / spa_path).resolve()
        try:
            requested_path.relative_to(frontend_dist.resolve())
        except ValueError:
            return PlainTextResponse("Not found", status_code=404)
        if requested_path.is_file():
            return FileResponse(requested_path)
        return FileResponse(
            frontend_index,
            media_type="text/html",
        )

    return application


app = create_app()
