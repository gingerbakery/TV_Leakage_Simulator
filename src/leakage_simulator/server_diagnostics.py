"""Structured events written to the simulator server log."""

import json
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id = ContextVar("diagnostic_request_id", default=None)


def log_event(event, **fields):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "request_id": request_id.get(),
        **fields,
    }
    print("[DIAG] " + json.dumps(record, ensure_ascii=True, separators=(",", ":")), flush=True)


class DiagnosticRequests:
    paths = frozenset({
        "/api/upload", "/api/scene", "/api/scene/refresh", "/api/scene/section-cap",
        "/api/projects/import", "/api/projects/export", "/api/projects/export-workspace",
        "/api/bsdf/import", "/api/gpu-cuda/status", "/api/raytrace/start",
        "/api/raytrace/direct", "/api/raytrace/stop", "/api/raytrace/validate-observer",
        "/api/raytrace/compare-sensor/start", "/api/raytrace/compare-sensor/stop",
    })

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path")
        if scope["type"] != "http" or path not in self.paths:
            await self.app(scope, receive, send)
            return

        token = request_id.set(uuid.uuid4().hex)
        started = time.perf_counter()
        status = None
        response_bytes = 0
        response_complete = False
        error_type = None

        async def observed_send(message):
            nonlocal status, response_bytes, response_complete
            await send(message)
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
                response_complete = not message.get("more_body", False)

        try:
            log_event("request.start", path=path, method=scope["method"])
            await self.app(scope, receive, observed_send)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            try:
                log_event("request.end", path=path, http_status=status,
                          response_complete=response_complete, response_bytes=response_bytes,
                          elapsed_sec=time.perf_counter() - started, error_type=error_type)
            finally:
                request_id.reset(token)
