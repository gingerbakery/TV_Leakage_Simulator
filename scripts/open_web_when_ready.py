from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable, Sequence
import urllib.request
import webbrowser


UrlOpen = Callable[..., Any]


def check_server_ready(
    url: str,
    expected_boot_token: str | None = None,
    *,
    open_url: UrlOpen = urllib.request.urlopen,
) -> bool:
    """Return true only for the expected server generation when one is given."""

    base_url = url.rstrip("/")
    status_url = (
        base_url + "/dev-status"
        if expected_boot_token is not None
        else base_url + "/health"
    )
    try:
        with open_url(status_url, timeout=1.0) as response:
            if response.status != 200:
                return False
            if expected_boot_token is None:
                return True
            payload = json.load(response)
    except Exception:
        return False

    return (
        isinstance(payload, dict)
        and payload.get("boot_token") == expected_boot_token
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "url",
        nargs="?",
        default="http://127.0.0.1:8788/",
    )
    parser.add_argument(
        "--expected-boot-token",
        help=(
            "open only when /dev-status reports this exact server boot token; "
            "omit for legacy health-only behavior"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for _ in range(120):
        if check_server_ready(args.url, args.expected_boot_token):
            webbrowser.open(args.url)
            return 0
        time.sleep(1.0)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
