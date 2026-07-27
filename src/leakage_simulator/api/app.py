from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

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

    @application.get("/api/scene", response_class=JSONResponse)
    def scene(cad: str = "") -> Any:
        if not cad.strip():
            return _error(400, "CAD file is required")
        try:
            return api_runtime.load_scene(cad)
        except (TypeError, ValueError) as exc:
            return _error(400, str(exc))
        except Exception as exc:
            return _error(500, str(exc))

    @application.post("/api/upload", response_class=JSONResponse)
    async def upload(request: Request, filename: str = "") -> Any:
        try:
            content = await request.body()
            return api_runtime.save_upload(filename, content)
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
