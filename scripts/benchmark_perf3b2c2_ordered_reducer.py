from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import math
import os
import platform
import statistics
import struct
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_perf3b2a_multibounce import build_depth_ten_case
from leakage_simulator.raytracer import run_direct_ray_trace


CONTRACT = "perf3b2c2_ordered_reducer_benchmark_v1"
OUTPUT_DIR = ROOT / "outputs" / "perf3b2c2_ordered_reducer"
REDUCERS = ("python_cpu", "numba_cpu")
TIMING_FIELDS = (
    "intersection_sec",
    "native_execute_sec",
    "wavefront_total_sec",
    "wavefront_plan_sec",
    "wavefront_commit_sec",
    "wavefront_event_tape_append_sec",
    "wavefront_event_tape_seal_sec",
    "wavefront_event_tape_validation_sec",
    "wavefront_reducer_preflight_sec",
    "wavefront_reducer_replay_sec",
    "wavefront_reducer_hydrate_sec",
    "wavefront_reducer_native_prepare_sec",
    "wavefront_reducer_native_dispatch_sec",
    "wavefront_reducer_native_jit_compile_sec",
    "wavefront_reducer_native_execute_sec",
    "wavefront_reducer_native_result_validation_sec",
    "wavefront_reducer_native_apply_sec",
    "wavefront_reducer_native_path_sec",
)
COUNTER_FIELDS = (
    "intersection_ray_count",
    "intersection_batch_count",
    "wavefront_chunk_count",
    "wavefront_primary_ray_count",
    "wavefront_event_count",
    "wavefront_event_tape_peak_bytes",
    "wavefront_event_tape_copy_bytes",
    "wavefront_path_materialized_count",
    "wavefront_path_materialization_skipped_count",
    "wavefront_reducer_logical_tape_count",
    "wavefront_reducer_logical_primary_count",
    "wavefront_reducer_logical_event_count",
    "wavefront_reducer_python_tape_count",
    "wavefront_reducer_python_primary_count",
    "wavefront_reducer_python_event_count",
    "wavefront_reducer_native_attempt_count",
    "wavefront_reducer_native_attempt_primary_count",
    "wavefront_reducer_native_attempt_event_count",
    "wavefront_reducer_native_success_count",
    "wavefront_reducer_native_success_primary_count",
    "wavefront_reducer_native_success_event_count",
    "wavefront_reducer_fallback_count",
    "wavefront_reducer_fallback_primary_count",
    "wavefront_reducer_fallback_event_count",
)
CONTRACT_FIELDS = (
    "wavefront_pipeline",
    "wavefront_state_layout",
    "wavefront_event_tape_contract",
    "wavefront_event_tape_validation_mode",
    "wavefront_event_tape_path_payload",
    "wavefront_event_tape_copy_contract",
    "wavefront_event_tape_peak_scope",
    "wavefront_reducer_contract",
    "wavefront_reducer_native_timing_scope",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _source_files() -> tuple[Path, ...]:
    return (
        ROOT / "src" / "leakage_simulator" / "raytracer.py",
        ROOT / "src" / "leakage_simulator" / "wavefront_event_tape.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_ordered_reducer.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_intersection.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_wavefront.py",
        ROOT / "scripts" / "benchmark_perf3b2a_multibounce.py",
        Path(__file__).resolve(),
    )


def _source_hashes() -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): _file_sha256(path)
        for path in _source_files()
    }


def _semantic_payload(result) -> dict:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload


def _semantic_json(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _bitwise_value(value):
    """Represent every public float by bits while preserving dict order."""

    if isinstance(value, dict):
        return [
            [key, _bitwise_value(item)]
            for key, item in value.items()
        ]
    if isinstance(value, (list, tuple)):
        return [_bitwise_value(item) for item in value]
    if isinstance(value, (float, np.floating)):
        return ["float64", struct.pack(">d", float(value)).hex()]
    return value


def _bitwise_semantic_sha256(payload: dict) -> str:
    encoded = json.dumps(
        _bitwise_value(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _percentile_95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _distribution(values: list[float]) -> dict:
    return {
        "samples": values,
        "p50": statistics.median(values),
        "p95": _percentile_95(values),
        "min": min(values),
        "max": max(values),
    }


def _windows_rss_reader() -> Optional[Callable[[], int]]:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    )
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def read() -> int:
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(counters.WorkingSetSize)

    return read


def _procfs_rss_reader() -> Optional[Callable[[], int]]:
    statm = Path("/proc/self/statm")
    if not statm.is_file():
        return None
    page_size = int(os.sysconf("SC_PAGE_SIZE"))

    def read() -> int:
        return int(statm.read_text(encoding="ascii").split()[1]) * page_size

    return read


RSS_READER = _windows_rss_reader() or _procfs_rss_reader()


class _RssSampler:
    def __init__(self, interval_sec: float = 0.005) -> None:
        self.interval_sec = interval_sec
        self.stop_event = threading.Event()
        self.samples: list[int] = []
        self.thread: Optional[threading.Thread] = None
        self.start_bytes: Optional[int] = None
        self.peak_bytes: Optional[int] = None
        self.end_bytes: Optional[int] = None

    def _sample(self) -> None:
        assert RSS_READER is not None
        while not self.stop_event.is_set():
            self.samples.append(RSS_READER())
            self.stop_event.wait(self.interval_sec)

    def __enter__(self):
        if RSS_READER is None:
            return self
        self.start_bytes = RSS_READER()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        if RSS_READER is None:
            return
        self.samples.append(RSS_READER())
        self.stop_event.set()
        assert self.thread is not None
        self.thread.join()
        self.peak_bytes = max(self.samples, default=self.start_bytes)
        self.end_bytes = self.samples[-1] if self.samples else self.start_bytes

    def to_dict(self) -> dict:
        return {
            "start_bytes": self.start_bytes,
            "peak_bytes": self.peak_bytes,
            "end_bytes": self.end_bytes,
            "peak_delta_bytes": (
                self.peak_bytes - self.start_bytes
                if self.peak_bytes is not None and self.start_bytes is not None
                else None
            ),
        }


def _provider_evidence(
    performance: dict,
    *,
    requested_intersection: str,
    requested_planner: str,
    requested_reducer: str,
) -> dict:
    intersection = {
        "requested": performance["requested_intersection_provider"],
        "effective": performance["intersection_provider"],
        "native_available": performance["native_available"],
        "native_used": performance["native_used"],
        "attempt_count": performance["native_attempt_count"],
        "success_count": performance["native_success_count"],
        "fallback_count": performance["intersection_fallback_count"],
        "fallback_reason": performance["intersection_fallback_reason"],
        "unavailable_reason": performance[
            "intersection_provider_unavailable_reason"
        ],
    }
    planner = {
        "requested": performance["requested_wavefront_planner"],
        "effective": performance["wavefront_planner"],
        "native_available": performance["wavefront_planner_native_available"],
        "native_used": performance["wavefront_planner_native_used"],
        "attempt_count": performance["wavefront_planner_native_attempt_count"],
        "success_count": performance["wavefront_planner_native_success_count"],
        "fallback_count": performance["wavefront_planner_fallback_count"],
        "fallback_reason": performance["wavefront_planner_fallback_reason"],
        "logical_row_count": performance[
            "wavefront_planner_logical_row_count"
        ],
    }
    reducer = {
        "requested": performance["requested_wavefront_reducer"],
        "effective": performance["wavefront_reducer"],
        "contract": performance["wavefront_reducer_contract"],
        "selection_reason": performance["wavefront_reducer_selection_reason"],
        "native_available": performance["wavefront_reducer_native_available"],
        "native_used": performance["wavefront_reducer_native_used"],
        "provider_version": performance[
            "wavefront_reducer_native_provider_version"
        ],
        "provider_disabled": performance[
            "wavefront_reducer_native_provider_disabled"
        ],
        "attempt_count": performance["wavefront_reducer_native_attempt_count"],
        "attempt_primary_count": performance[
            "wavefront_reducer_native_attempt_primary_count"
        ],
        "attempt_event_count": performance[
            "wavefront_reducer_native_attempt_event_count"
        ],
        "success_count": performance["wavefront_reducer_native_success_count"],
        "success_primary_count": performance[
            "wavefront_reducer_native_success_primary_count"
        ],
        "success_event_count": performance[
            "wavefront_reducer_native_success_event_count"
        ],
        "python_tape_count": performance["wavefront_reducer_python_tape_count"],
        "logical_tape_count": performance[
            "wavefront_reducer_logical_tape_count"
        ],
        "logical_primary_count": performance[
            "wavefront_reducer_logical_primary_count"
        ],
        "logical_event_count": performance[
            "wavefront_reducer_logical_event_count"
        ],
        "fallback_count": performance["wavefront_reducer_fallback_count"],
        "fallback_phase": performance["wavefront_reducer_fallback_phase"],
        "fallback_reason": performance["wavefront_reducer_fallback_reason"],
        "unavailable_reason": performance[
            "wavefront_reducer_unavailable_reason"
        ],
    }
    errors: list[str] = []
    if intersection["requested"] != requested_intersection:
        errors.append("intersection_request_mismatch")
    if requested_intersection == "numba_cpu":
        if intersection["native_available"] is True:
            if not intersection["native_used"] or not intersection["success_count"]:
                errors.append("intersection_available_but_not_used")
            if intersection["fallback_count"]:
                errors.append("intersection_fallback_recorded")
        elif intersection["native_available"] is False:
            if not intersection["unavailable_reason"]:
                errors.append("intersection_unavailable_without_reason")
        else:
            errors.append("intersection_capability_missing")
    elif intersection["effective"] not in {"python_cpu", "not_used"}:
        errors.append("intersection_python_effective_mismatch")

    if planner["requested"] != requested_planner:
        errors.append("planner_request_mismatch")
    if requested_planner in {"auto", "python_cpu"} and planner[
        "effective"
    ] not in {"python_cpu", "not_used"}:
        errors.append("planner_python_effective_mismatch")

    if reducer["requested"] != requested_reducer:
        errors.append("reducer_request_mismatch")
    if requested_reducer == "python_cpu":
        if reducer["effective"] != "python_cpu":
            errors.append("reducer_python_effective_mismatch")
        if reducer["attempt_count"] or reducer["fallback_count"]:
            errors.append("reducer_python_native_activity")
        if reducer["contract"] != "python_ordered_v1":
            errors.append("reducer_python_contract_mismatch")
    elif reducer["native_available"] is True:
        if not reducer["native_used"] or reducer["effective"] != "numba_cpu":
            errors.append("reducer_available_but_not_used")
        if reducer["contract"] != "ordered_summary_reducer_v1":
            errors.append("reducer_native_contract_mismatch")
        if not reducer["attempt_count"] or reducer["attempt_count"] != reducer[
            "success_count"
        ]:
            errors.append("reducer_attempt_success_mismatch")
        if reducer["attempt_primary_count"] != reducer["logical_primary_count"]:
            errors.append("reducer_primary_count_mismatch")
        if reducer["attempt_event_count"] != reducer["logical_event_count"]:
            errors.append("reducer_event_count_mismatch")
        if reducer["fallback_count"] or reducer["python_tape_count"]:
            errors.append("reducer_available_fallback_recorded")
    elif reducer["native_available"] is False:
        if not reducer["unavailable_reason"]:
            errors.append("reducer_unavailable_without_reason")
        if reducer["effective"] != "python_cpu":
            errors.append("reducer_unavailable_effective_mismatch")
    else:
        errors.append("reducer_capability_missing")

    if performance["wavefront_pipeline"] != "soa_event_tape":
        errors.append("soa_pipeline_not_used")
    if performance["wavefront_event_tape_contract"] != (
        "ordered_primary_event_tape_v2"
    ):
        errors.append("event_tape_contract_mismatch")
    if performance["wavefront_event_tape_validation_mode"] != "strict_v1":
        errors.append("event_tape_not_strict")
    if errors:
        raise RuntimeError("provider evidence failed: " + ", ".join(errors))
    return {
        "intersection": intersection,
        "wavefront_planner": planner,
        "wavefront_reducer": reducer,
        "validation": {
            "passed": True,
            "errors": [],
            "policy": (
                "an available explicit native reducer must process every "
                "logical tape exactly once without Python fallback; an "
                "unavailable reducer must expose a reason and use Python"
            ),
        },
    }


def _build_case(ray_count: int, *, paths: bool, max_stored_paths: int):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.contribution_mode = "summary"
    trace_input.config.store_ray_paths = paths
    trace_input.config.max_stored_paths = max_stored_paths
    trace_input.mesh.prepare_acceleration()
    return trace_input


def _run_once(
    trace_input,
    *,
    reducer: str,
    batch_size: int,
    intersection_provider: str,
    planner_provider: str,
    phase: str,
    sequence_index: int,
) -> dict:
    gc.collect()
    with _RssSampler() as rss:
        started = time.perf_counter()
        result = run_direct_ray_trace(
            trace_input,
            intersection_dispatch="batch",
            intersection_batch_size=batch_size,
            intersection_provider=intersection_provider,
            wavefront_planner=planner_provider,
            wavefront_pipeline="soa_event_tape",
            wavefront_reducer=reducer,
        )
        wall_sec = time.perf_counter() - started
    performance = result.metrics["_performance_summary"]
    providers = _provider_evidence(
        performance,
        requested_intersection=intersection_provider,
        requested_planner=planner_provider,
        requested_reducer=reducer,
    )
    payload = _semantic_payload(result)
    semantic_json = _semantic_json(payload)
    timings = {}
    for name in TIMING_FIELDS:
        value = float(performance[name])
        if not math.isfinite(value) or value < 0.0:
            raise RuntimeError(f"{name} must be finite and non-negative")
        timings[name] = value
    record = {
        "phase": phase,
        "sequence_index": sequence_index,
        "reducer_requested": reducer,
        "reducer_effective": performance["wavefront_reducer"],
        "wall_sec": wall_sec,
        "reported_runtime_sec": float(result.runtime_sec),
        "primary_rays_per_sec": result.total_rays / wall_sec,
        "semantic_json_sha256": _sha256_bytes(semantic_json.encode("utf-8")),
        "semantic_float_bits_and_order_sha256": _bitwise_semantic_sha256(payload),
        "result_counts": {
            "total_rays": result.total_rays,
            "receiver_hit_count": result.receiver_hit_count,
            "surface_hit_count": result.surface_hit_count,
            "terminated_ray_count": result.terminated_ray_count,
            "stored_path_count": len(result.stored_paths),
            "receiver_flux_lumen": sum(
                sum(row)
                for grid in result.receiver_grids
                for row in grid.flux_lumen
            ),
        },
        "timings_sec": timings,
        "counters": {
            name: performance[name]
            for name in COUNTER_FIELDS
        },
        "contracts": {
            name: performance[name]
            for name in CONTRACT_FIELDS
        },
        "providers": providers,
        "memory": {
            **rss.to_dict(),
            "event_tape_peak_bytes": performance[
                "wavefront_event_tape_peak_bytes"
            ],
            "event_tape_copy_bytes": performance[
                "wavefront_event_tape_copy_bytes"
            ],
            "scope": (
                "5ms sampled process RSS plus pipeline tape-owned estimates"
                if RSS_READER is not None
                else "pipeline tape-owned estimates; process RSS unavailable"
            ),
        },
        "_semantic_json": semantic_json,
        "_bitwise_hash": _bitwise_semantic_sha256(payload),
    }
    del result
    gc.collect()
    return record


def _public_record(record: dict, reference: dict) -> dict:
    return {
        key: value
        for key, value in record.items()
        if not key.startswith("_")
    } | {
        "semantic_json_match_reference": (
            record["_semantic_json"] == reference["_semantic_json"]
        ),
        "semantic_float_bits_and_order_match_reference": (
            record["_bitwise_hash"] == reference["_bitwise_hash"]
        ),
    }


def _summarize(records: list[dict], reference: dict) -> dict:
    wall = [record["wall_sec"] for record in records]
    rss_delta = [
        record["memory"]["peak_delta_bytes"]
        for record in records
        if record["memory"]["peak_delta_bytes"] is not None
    ]
    return {
        "repeat_count": len(records),
        "wall_time_sec": _distribution(wall),
        "primary_rays_per_sec_p50": (
            records[0]["result_counts"]["total_rays"] / statistics.median(wall)
        ),
        "timings_sec": {
            name: _distribution(
                [record["timings_sec"][name] for record in records]
            )
            for name in TIMING_FIELDS
        },
        "memory": {
            "rss_peak_delta_bytes": (
                _distribution(rss_delta) if rss_delta else None
            ),
            "event_tape_peak_bytes_max": max(
                record["memory"]["event_tape_peak_bytes"] for record in records
            ),
            "event_tape_copy_bytes_max": max(
                record["memory"]["event_tape_copy_bytes"] for record in records
            ),
        },
        "semantic_json_mismatch_count": sum(
            record["_semantic_json"] != reference["_semantic_json"]
            for record in records
        ),
        "semantic_float_bits_and_order_mismatch_count": sum(
            record["_bitwise_hash"] != reference["_bitwise_hash"]
            for record in records
        ),
        "representative": {
            "result_counts": records[0]["result_counts"],
            "counters": records[0]["counters"],
            "contracts": records[0]["contracts"],
            "providers": records[0]["providers"],
        },
        "raw_runs": [
            _public_record(record, reference) for record in records
        ],
    }


def _benchmark_cell(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_size: int,
    intersection_provider: str,
    planner_provider: str,
    paths: bool,
    max_stored_paths: int,
) -> dict:
    trace_input = _build_case(
        ray_count,
        paths=paths,
        max_stored_paths=max_stored_paths,
    )
    warmup_records: list[dict] = []
    measured_records: list[dict] = []
    sequence_index = 0
    for _ in range(warmups):
        for reducer in REDUCERS:
            sequence_index += 1
            warmup_records.append(
                _run_once(
                    trace_input,
                    reducer=reducer,
                    batch_size=batch_size,
                    intersection_provider=intersection_provider,
                    planner_provider=planner_provider,
                    phase="warmup",
                    sequence_index=sequence_index,
                )
            )
    for repeat_index in range(repeats):
        order = REDUCERS if repeat_index % 2 == 0 else tuple(reversed(REDUCERS))
        for reducer in order:
            sequence_index += 1
            measured_records.append(
                _run_once(
                    trace_input,
                    reducer=reducer,
                    batch_size=batch_size,
                    intersection_provider=intersection_provider,
                    planner_provider=planner_provider,
                    phase="measured",
                    sequence_index=sequence_index,
                )
            )
    all_records = warmup_records + measured_records
    reference = next(
        record for record in all_records if record["reducer_requested"] == "python_cpu"
    )
    grouped = {
        reducer: [
            record
            for record in measured_records
            if record["reducer_requested"] == reducer
        ]
        for reducer in REDUCERS
    }
    summaries = {
        reducer: _summarize(records, reference)
        for reducer, records in grouped.items()
    }
    python_p50 = summaries["python_cpu"]["wall_time_sec"]["p50"]
    native_p50 = summaries["numba_cpu"]["wall_time_sec"]["p50"]
    return {
        "name": f"depth10_summary_paths_{'on' if paths else 'off'}",
        "store_ray_paths": paths,
        "max_stored_paths": max_stored_paths,
        "warmups_per_reducer": warmups,
        "measured_repeats_per_reducer": repeats,
        "reference_semantic_json_sha256": reference["semantic_json_sha256"],
        "reference_semantic_float_bits_and_order_sha256": reference[
            "semantic_float_bits_and_order_sha256"
        ],
        "warmups": [
            _public_record(record, reference) for record in warmup_records
        ],
        "reducers": summaries,
        "comparison": {
            "python_over_numba_wall_speedup": python_p50 / native_p50,
            "numba_wall_reduction_percent": (1.0 - native_p50 / python_p50)
            * 100.0,
            "numba_minus_python_wall_sec": native_p50 - python_p50,
        },
        "semantic_json_mismatch_count": sum(
            record["_semantic_json"] != reference["_semantic_json"]
            for record in all_records
        ),
        "semantic_float_bits_and_order_mismatch_count": sum(
            record["_bitwise_hash"] != reference["_bitwise_hash"]
            for record in all_records
        ),
    }


def benchmark(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_size: int,
    intersection_provider: str,
    planner_provider: str,
    path_modes: list[bool],
    max_stored_paths: int,
) -> dict:
    source_before = _source_hashes()
    cells = [
        _benchmark_cell(
            ray_count=ray_count,
            repeats=repeats,
            warmups=warmups,
            batch_size=batch_size,
            intersection_provider=intersection_provider,
            planner_provider=planner_provider,
            paths=paths,
            max_stored_paths=max_stored_paths,
        )
        for paths in path_modes
    ]
    source_after = _source_hashes()
    if source_after != source_before:
        raise RuntimeError("source changed during benchmark")
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "source_sha256_before": source_before,
        "source_sha256_after": source_after,
        "source_stable_during_benchmark": True,
        "scene": "committed_deterministic_depth10_specular_corridor",
        "ray_count": ray_count,
        "max_depth": 10,
        "contribution_mode": "summary",
        "batch_size": batch_size,
        "repeats_per_reducer": repeats,
        "warmups_per_reducer": warmups,
        "requested_intersection_provider": intersection_provider,
        "requested_wavefront_planner": planner_provider,
        "wavefront_pipeline": "soa_event_tape",
        "reducers": list(REDUCERS),
        "path_modes": path_modes,
        "native_cold_start_excluded": warmups > 0,
        "semantic_comparison": (
            "exact ordered JSON plus every float64 bit; excludes only "
            "run_id, runtime_sec, and _performance_summary"
        ),
        "memory_measurement": (
            "5ms sampled process RSS plus pipeline tape-owned estimates"
            if RSS_READER is not None
            else "pipeline tape-owned estimates; process RSS unavailable"
        ),
        "unit_test_policy": (
            "wall-time thresholds are intentionally absent from unit tests; "
            "promotion decisions use canonical artifacts"
        ),
        "cells": cells,
        "semantic_json_mismatch_count": sum(
            cell["semantic_json_mismatch_count"] for cell in cells
        ),
        "semantic_float_bits_and_order_mismatch_count": sum(
            cell["semantic_float_bits_and_order_mismatch_count"]
            for cell in cells
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark PERF-3B-2C-2 Python versus explicit Numba ordered "
            "reducers on the same strict SoA event tape."
        )
    )
    parser.add_argument("--rays", type=int, default=10_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument(
        "--intersection-provider",
        choices=("python_cpu", "numba_cpu"),
        default="numba_cpu",
    )
    parser.add_argument(
        "--planner-provider",
        choices=("auto", "python_cpu", "numba_cpu"),
        default="auto",
    )
    parser.add_argument(
        "--path-modes",
        choices=("off", "on"),
        nargs="+",
        default=("off", "on"),
    )
    parser.add_argument("--max-stored-paths", type=int, default=500)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    if args.rays <= 0 or args.repeats <= 0 or args.batch_size <= 0:
        raise SystemExit("rays, repeats, and batch size must be positive")
    if args.warmups < 0:
        raise SystemExit("warmups must be non-negative")
    if args.max_stored_paths <= 0:
        raise SystemExit("max stored paths must be positive")
    path_modes = [value == "on" for value in dict.fromkeys(args.path_modes)]
    summary = benchmark(
        ray_count=args.rays,
        repeats=args.repeats,
        warmups=args.warmups,
        batch_size=args.batch_size,
        intersection_provider=args.intersection_provider,
        planner_provider=args.planner_provider,
        path_modes=path_modes,
        max_stored_paths=args.max_stored_paths,
    )
    encoded = json.dumps(summary, ensure_ascii=False, allow_nan=False, indent=2)
    print(encoded)
    if not args.no_write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = OUTPUT_DIR / "summary.json"
        summary_path.write_text(f"{encoded}\n", encoding="utf-8")
        print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
