from __future__ import annotations

"""Verify that a source checkout uses the exact pinned Python environment."""

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import struct
import sys
from typing import Sequence


PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


def normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def read_exact_pins(paths: Sequence[Path]) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    for path in paths:
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = PIN_PATTERN.fullmatch(line)
            if match is None:
                raise RuntimeError(
                    f"unpinned_requirement:{path.name}:{line_number}:{line}"
                )
            display_name, expected_version = match.groups()
            normalized_name = normalize_distribution_name(display_name)
            previous = pins.get(normalized_name)
            if previous is not None and previous[1] != expected_version:
                raise RuntimeError(
                    "conflicting_requirement:"
                    f"{display_name}:{previous[1]}:{expected_version}"
                )
            pins[normalized_name] = (display_name, expected_version)
    return pins


def verify_environment(requirement_paths: Sequence[Path]) -> dict[str, object]:
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError(
            f"unsupported_python:{platform.python_version()}:expected:3.13.x"
        )
    if struct.calcsize("P") * 8 != 64:
        raise RuntimeError("unsupported_python_architecture:expected:64-bit")

    pins = read_exact_pins(requirement_paths)
    mismatches: list[dict[str, str]] = []
    installed: dict[str, str] = {}
    for normalized_name in sorted(pins):
        display_name, expected_version = pins[normalized_name]
        try:
            actual_version = metadata.version(display_name)
        except metadata.PackageNotFoundError:
            actual_version = "missing"
        installed[display_name] = actual_version
        if actual_version != expected_version:
            mismatches.append(
                {
                    "package": display_name,
                    "expected": expected_version,
                    "actual": actual_version,
                }
            )

    if mismatches:
        details = ",".join(
            f"{item['package']}={item['actual']} (expected {item['expected']})"
            for item in mismatches
        )
        raise RuntimeError(f"dependency_pin_mismatch:{details}")

    return {
        "status": "ok",
        "python": platform.python_version(),
        "architecture_bits": 64,
        "requirements": [path.name for path in requirement_paths],
        "verified_pin_count": len(pins),
        "installed": installed,
    }


def format_human(result: dict[str, object]) -> str:
    return "\n".join(
        (
            "[PYTHON VERIFIED] Python "
            f"{result['python']} ({result['architecture_bits']}-bit)",
            "[PYTHON VERIFIED] Exact dependency pins: "
            f"{result['verified_pin_count']} packages",
            "[PYTHON VERIFIED] requirements-dev.txt + "
            "requirements-gpu-cuda.txt are synchronized.",
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        type=Path,
        action="append",
        required=True,
        help="requirements file containing exact name==version pins; repeatable",
    )
    parser.add_argument(
        "--human",
        action="store_true",
        help="print a concise operator-facing summary instead of JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = verify_environment(args.requirements)
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
        if args.human:
            print(f"[PYTHON FAILED] {error}", file=sys.stderr)
            print(
                "[ACTION] Rerun run_web_gpu.bat. It rebuilds .venv-gpu when "
                "requirement pins change.",
                file=sys.stderr,
            )
        else:
            print(
                json.dumps(
                    {"status": "error", "error": error},
                    sort_keys=True,
                    allow_nan=False,
                ),
                file=sys.stderr,
            )
        return 1

    if args.human:
        print(format_human(result))
    else:
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
