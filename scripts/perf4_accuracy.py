from __future__ import annotations

"""Deterministic CPU/GPU semantic comparison helpers for PERF-4.

Discrete simulation decisions must match exactly.  Floating-point values may
differ by a few representable float64 values because CUDA and CPU
transcendental implementations are not bit-identical.
"""

from dataclasses import asdict, dataclass
import math
import struct
from typing import Any


CONTRACT = "discrete_exact_strict_float64_v1"
DEFAULT_ABSOLUTE_TOLERANCE = 1e-12
DEFAULT_RELATIVE_TOLERANCE = 1e-12
DEFAULT_MAX_ULP_DISTANCE = 8
DEFAULT_SAMPLE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class SemanticParityReport:
    contract: str
    passed: bool
    semantic_exact: bool
    discrete_exact: bool
    float64_tolerance_passed: bool
    difference_count: int
    discrete_difference_count: int
    numeric_difference_count: int
    max_absolute_error: float
    max_absolute_error_path: str | None
    max_relative_error: float
    max_relative_error_path: str | None
    max_ulp_distance: int
    max_ulp_distance_path: str | None
    absolute_tolerance: float
    relative_tolerance: float
    allowed_max_ulp_distance: int
    samples: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ordered_float64_bits(value: float) -> int:
    signed = struct.unpack(">q", struct.pack(">d", float(value)))[0]
    return 0x8000000000000000 - signed if signed < 0 else signed


def _ulp_distance(left: float, right: float) -> int:
    if not math.isfinite(left) or not math.isfinite(right):
        return 0 if left == right else 0xFFFFFFFFFFFFFFFF
    return abs(_ordered_float64_bits(left) - _ordered_float64_bits(right))


def compare_semantic_payloads(
    reference: Any,
    candidate: Any,
    *,
    absolute_tolerance: float = DEFAULT_ABSOLUTE_TOLERANCE,
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    max_ulp_distance: int = DEFAULT_MAX_ULP_DISTANCE,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> SemanticParityReport:
    if absolute_tolerance < 0.0 or relative_tolerance < 0.0:
        raise ValueError("floating-point tolerances must be non-negative")
    if max_ulp_distance < 0:
        raise ValueError("max_ulp_distance must be non-negative")
    if sample_limit < 0:
        raise ValueError("sample_limit must be non-negative")

    difference_count = 0
    discrete_difference_count = 0
    numeric_difference_count = 0
    numeric_tolerance_failed = False
    maximum_absolute = 0.0
    maximum_absolute_path: str | None = None
    maximum_relative = 0.0
    maximum_relative_path: str | None = None
    maximum_ulp = 0
    maximum_ulp_path: str | None = None
    samples: list[dict[str, Any]] = []

    def record_sample(
        path: str,
        left: Any,
        right: Any,
        kind: str,
        **extra: Any,
    ) -> None:
        if len(samples) >= sample_limit:
            return
        samples.append(
            {
                "path": path,
                "reference": left,
                "candidate": right,
                "kind": kind,
                **extra,
            }
        )

    def walk(left: Any, right: Any, path: str) -> None:
        nonlocal difference_count
        nonlocal discrete_difference_count
        nonlocal numeric_difference_count
        nonlocal numeric_tolerance_failed
        nonlocal maximum_absolute
        nonlocal maximum_absolute_path
        nonlocal maximum_relative
        nonlocal maximum_relative_path
        nonlocal maximum_ulp
        nonlocal maximum_ulp_path

        if isinstance(left, dict) and isinstance(right, dict):
            left_keys = set(left)
            right_keys = set(right)
            for key in sorted(left_keys | right_keys, key=str):
                child_path = f"{path}.{key}"
                if key not in left or key not in right:
                    difference_count += 1
                    discrete_difference_count += 1
                    record_sample(
                        child_path,
                        left.get(key, "<missing>"),
                        right.get(key, "<missing>"),
                        "missing_key",
                    )
                    continue
                walk(left[key], right[key], child_path)
            return

        if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
            if len(left) != len(right):
                difference_count += 1
                discrete_difference_count += 1
                record_sample(
                    f"{path}.length",
                    len(left),
                    len(right),
                    "length",
                )
            for index, (left_value, right_value) in enumerate(zip(left, right)):
                walk(left_value, right_value, f"{path}[{index}]")
            return

        if type(left) is float and type(right) is float:
            if left == right:
                return
            difference_count += 1
            numeric_difference_count += 1
            absolute_error = abs(left - right)
            denominator = max(abs(left), abs(right), 1e-300)
            relative_error = absolute_error / denominator
            ulp_error = _ulp_distance(left, right)
            within_tolerance = (
                math.isclose(
                    left,
                    right,
                    rel_tol=relative_tolerance,
                    abs_tol=absolute_tolerance,
                )
                and ulp_error <= max_ulp_distance
            )
            numeric_tolerance_failed |= not within_tolerance
            if absolute_error > maximum_absolute:
                maximum_absolute = absolute_error
                maximum_absolute_path = path
            if relative_error > maximum_relative:
                maximum_relative = relative_error
                maximum_relative_path = path
            if ulp_error > maximum_ulp:
                maximum_ulp = ulp_error
                maximum_ulp_path = path
            record_sample(
                path,
                left,
                right,
                "float64",
                absolute_error=absolute_error,
                relative_error=relative_error,
                ulp_distance=ulp_error,
                within_tolerance=within_tolerance,
            )
            return

        if type(left) is not type(right) or left != right:
            difference_count += 1
            discrete_difference_count += 1
            record_sample(path, left, right, "discrete")

    walk(reference, candidate, "root")
    discrete_exact = discrete_difference_count == 0
    float64_tolerance_passed = not numeric_tolerance_failed
    return SemanticParityReport(
        contract=CONTRACT,
        passed=discrete_exact and float64_tolerance_passed,
        semantic_exact=difference_count == 0,
        discrete_exact=discrete_exact,
        float64_tolerance_passed=float64_tolerance_passed,
        difference_count=difference_count,
        discrete_difference_count=discrete_difference_count,
        numeric_difference_count=numeric_difference_count,
        max_absolute_error=maximum_absolute,
        max_absolute_error_path=maximum_absolute_path,
        max_relative_error=maximum_relative,
        max_relative_error_path=maximum_relative_path,
        max_ulp_distance=maximum_ulp,
        max_ulp_distance_path=maximum_ulp_path,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
        allowed_max_ulp_distance=max_ulp_distance,
        samples=tuple(samples),
    )


__all__ = [
    "CONTRACT",
    "DEFAULT_ABSOLUTE_TOLERANCE",
    "DEFAULT_MAX_ULP_DISTANCE",
    "DEFAULT_RELATIVE_TOLERANCE",
    "SemanticParityReport",
    "compare_semantic_payloads",
]
