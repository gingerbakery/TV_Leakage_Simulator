from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.append(str(ROOT / "src"))

import uvicorn

from leakage_simulator.api import API_VERSION, create_app


def _available_port(
    host: str,
    start_port: int,
    strict_port: bool,
) -> int:
    max_tries = 1 if strict_port else 24
    last_error: OSError | None = None
    for offset in range(max_tries):
        candidate = start_port + offset
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind((host, candidate))
            return candidate
        except OSError as exc:
            last_error = exc
            if offset + 1 < max_tries:
                print(
                    "port {} is unavailable; trying {}".format(
                        candidate,
                        candidate + 1,
                    ),
                    flush=True,
                )
    raise RuntimeError(
        "failed to bind ports {}~{} (last_errno={})".format(
            start_port,
            start_port + max_tries - 1,
            getattr(last_error, "errno", None),
        )
    )


def run_server(
    host: str = "127.0.0.1",
    port: int = 8788,
    strict_port: bool = False,
) -> None:
    selected_port = _available_port(host, port, strict_port)
    print(
        "TV Leakage Simulator API v{} at http://{}:{}".format(
            API_VERSION,
            host,
            selected_port,
        ),
        flush=True,
    )
    print(
        "health: http://{}:{}/health".format(host, selected_port),
        flush=True,
    )
    print(
        "OpenAPI: http://{}:{}/api/docs".format(
            host,
            selected_port,
        ),
        flush=True,
    )
    uvicorn.run(
        create_app(),
        host=host,
        port=selected_port,
        access_log=False,
        log_level="info",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the TV Leakage Simulator FastAPI service",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get("LEAKAGE_API_PORT")
            or os.environ.get("LEAKAGE_WEB_PORT")
            or "8788"
        ),
    )
    parser.add_argument(
        "--strict-port",
        action="store_true",
    )
    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        strict_port=args.strict_port,
    )


if __name__ == "__main__":
    main()
