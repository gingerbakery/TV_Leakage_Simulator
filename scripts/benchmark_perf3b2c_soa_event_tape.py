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


OUTPUT_DIR = ROOT / "outputs" / "perf3b2c_soa_event_tape"
CONTRACT = "perf3b2c_soa_event_tape_v1"
PIPELINES = ("object_reference", "soa_event_tape")
DEFAULT_BATCH_SIZES = [256, 1024, 4096]
TIMING_FIELDS = (
    "intersection_sec",
    "native_execute_sec",
    "wavefront_total_sec",
    "wavefront_state_build_sec",
    "wavefront_state_init_sec",
    "wavefront_state_advance_sec",
    "wavefront_receiver_sec",
    "wavefront_geometry_sec",
    "wavefront_plan_sec",
    "wavefront_commit_sec",
    "wavefront_event_tape_append_sec",
    "wavefront_event_tape_seal_sec",
    "wavefront_event_tape_validation_sec",
    "wavefront_reducer_preflight_sec",
    "wavefront_reducer_replay_sec",
    "wavefront_reducer_hydrate_sec",
)
COUNTER_FIELDS = (
    "intersection_ray_count",
    "intersection_batch_count",
    "intersection_batch_max_size",
    "wavefront_chunk_count",
    "wavefront_primary_ray_count",
    "wavefront_depth_batch_count",
    "wavefront_max_active_ray_count",
    "wavefront_max_observed_depth",
    "wavefront_geometry_ray_count",
    "wavefront_geometry_hit_count",
    "wavefront_compacted_ray_count",
    "wavefront_path_materialized_count",
    "wavefront_path_materialization_skipped_count",
    "wavefront_event_count",
    "wavefront_event_tape_peak_bytes",
    "wavefront_event_tape_copy_bytes",
    "wavefront_reducer_logical_event_count",
    "wavefront_stochastic_primary_ray_count",
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
    "wavefront_reflection_rng",
    "wavefront_rng_scalar_parity",
)


def _provider_evidence(
    performance: dict,
    *,
    requested_intersection_provider: str,
    requested_wavefront_planner: str,
) -> dict:
    intersection = {
        "requested": performance["requested_intersection_provider"],
        "effective": performance["intersection_provider"],
        "native_available": performance["native_available"],
        "native_used": performance["native_used"],
        "native_attempt_count": performance["native_attempt_count"],
        "native_attempt_ray_count": performance["native_attempt_ray_count"],
        "native_success_count": performance["native_success_count"],
        "native_success_ray_count": performance["native_success_ray_count"],
        "fallback_count": performance["intersection_fallback_count"],
        "fallback_ray_count": performance["intersection_fallback_ray_count"],
        "fallback_phase": performance["intersection_fallback_phase"],
        "fallback_reason": performance["intersection_fallback_reason"],
        "unavailable_reason": performance[
            "intersection_provider_unavailable_reason"
        ],
    }
    planner = {
        "requested": performance["requested_wavefront_planner"],
        "effective": performance["wavefront_planner"],
        "logical_row_count": performance["wavefront_planner_logical_row_count"],
        "python_sidecar_row_count": performance[
            "wavefront_planner_python_sidecar_row_count"
        ],
        "native_available": performance["wavefront_planner_native_available"],
        "native_used": performance["wavefront_planner_native_used"],
        "native_attempt_count": performance[
            "wavefront_planner_native_attempt_count"
        ],
        "native_attempt_row_count": performance[
            "wavefront_planner_native_attempt_row_count"
        ],
        "native_success_count": performance[
            "wavefront_planner_native_success_count"
        ],
        "native_success_row_count": performance[
            "wavefront_planner_native_success_row_count"
        ],
        "fallback_count": performance["wavefront_planner_fallback_count"],
        "fallback_row_count": performance[
            "wavefront_planner_fallback_row_count"
        ],
        "fallback_phase": performance["wavefront_planner_fallback_phase"],
        "fallback_reason": performance["wavefront_planner_fallback_reason"],
        "unavailable_reason": performance[
            "wavefront_planner_unavailable_reason"
        ],
    }
    errors: list[str] = []
    if intersection["requested"] != requested_intersection_provider:
        errors.append("intersection_requested_provider_mismatch")
    if requested_intersection_provider == "numba_cpu":
        if intersection["native_available"] is True:
            if not intersection["native_used"]:
                errors.append("intersection_available_native_not_used")
            if intersection["native_attempt_count"] <= 0:
                errors.append("intersection_available_without_attempt")
            if intersection["native_success_count"] <= 0:
                errors.append("intersection_available_without_success")
            if intersection["effective"] not in {"numba_cpu", "mixed"}:
                errors.append("intersection_available_silent_python_fallback")
            if intersection["fallback_count"]:
                errors.append("intersection_available_native_fallback_recorded")
        elif intersection["native_available"] is False:
            if not intersection["unavailable_reason"]:
                errors.append("intersection_unavailable_without_reason")
        else:
            errors.append("intersection_numba_capability_not_probed")
    elif intersection["effective"] not in {"python_cpu", "not_used"}:
        errors.append("intersection_python_request_effective_mismatch")

    if planner["requested"] != requested_wavefront_planner:
        errors.append("wavefront_planner_requested_provider_mismatch")
    if requested_wavefront_planner == "numba_cpu" and planner[
        "logical_row_count"
    ]:
        if planner["native_available"] is True:
            if not planner["native_used"]:
                errors.append("wavefront_planner_available_native_not_used")
            if planner["native_attempt_count"] <= 0:
                errors.append("wavefront_planner_available_without_attempt")
            if planner["native_success_count"] <= 0:
                errors.append("wavefront_planner_available_without_success")
            if planner["effective"] not in {"numba_cpu", "mixed"}:
                errors.append("wavefront_planner_available_silent_python_fallback")
            if planner["fallback_count"]:
                errors.append("wavefront_planner_available_native_fallback_recorded")
        elif planner["native_available"] is False:
            if not planner["unavailable_reason"]:
                errors.append("wavefront_planner_unavailable_without_reason")
        else:
            errors.append("wavefront_planner_numba_capability_not_probed")
    elif requested_wavefront_planner == "python_cpu" and planner[
        "effective"
    ] not in {"python_cpu", "not_used"}:
        errors.append("wavefront_planner_python_request_effective_mismatch")

    evidence = {
        "intersection": intersection,
        "wavefront_planner": planner,
        "validation": {
            "passed": not errors,
            "errors": errors,
            "policy": (
                "requested numba_cpu may use Python only when native_available "
                "is false with an unavailable_reason; an available provider "
                "must record attempts and successes without fallback"
            ),
        },
    }
    if errors:
        raise RuntimeError(
            "provider capability validation failed: " + ", ".join(errors)
        )
    return evidence


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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _semantic_json(result) -> str:
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _semantic_sha256(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


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
        fields = statm.read_text(encoding="ascii").split()
        return int(fields[1]) * page_size

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


def _build_case(
    ray_count: int,
    contribution_mode: str,
    store_ray_paths: bool,
    max_stored_paths: int,
):
    trace_input = build_depth_ten_case(ray_count)
    trace_input.config.contribution_mode = contribution_mode
    trace_input.config.store_ray_paths = store_ray_paths
    trace_input.config.max_stored_paths = max_stored_paths
    trace_input.mesh.prepare_acceleration()
    return trace_input


def _run_once(
    trace_input,
    *,
    pipeline: str,
    batch_size: int,
    provider: str,
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
            intersection_provider=provider,
            wavefront_planner=planner_provider,
            wavefront_pipeline=pipeline,
        )
        wall_sec = time.perf_counter() - started
    performance = result.metrics["_performance_summary"]
    providers = _provider_evidence(
        performance,
        requested_intersection_provider=provider,
        requested_wavefront_planner=planner_provider,
    )
    semantic_json = _semantic_json(result)
    timing = {}
    for key in TIMING_FIELDS:
        value = performance.get(key)
        if value is None:
            continue
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            raise RuntimeError(f"{key} must be a finite non-negative timing")
        timing[key] = value
    record = {
        "phase": phase,
        "sequence_index": sequence_index,
        "pipeline_requested": pipeline,
        "pipeline_effective": performance["wavefront_pipeline"],
        "wall_sec": wall_sec,
        "reported_runtime_sec": float(result.runtime_sec),
        "primary_rays_per_sec": result.total_rays / wall_sec,
        "semantic_sha256": _semantic_sha256(semantic_json),
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
        "timings_sec": timing,
        "counters": {
            key: performance[key]
            for key in COUNTER_FIELDS
            if key in performance
        },
        "contracts": {
            key: performance[key]
            for key in CONTRACT_FIELDS
            if key in performance
        },
        "providers": providers,
        "memory": {
            **rss.to_dict(),
            "event_tape_peak_bytes": performance[
                "wavefront_event_tape_peak_bytes"
            ],
            "measurement": (
                "sampled_process_rss_and_pipeline_reported_tape_peak"
                if RSS_READER is not None
                else "pipeline_reported_tape_peak_only"
            ),
        },
        "_semantic_json": semantic_json,
    }
    del result
    gc.collect()
    return record


def _public_record(record: dict, reference_semantic: str) -> dict:
    return {
        key: value
        for key, value in record.items()
        if key != "_semantic_json"
    } | {
        "semantic_match_reference": (
            record["_semantic_json"] == reference_semantic
        )
    }


def _summarize_pipeline(
    records: list[dict],
    reference_semantic: str,
) -> dict:
    wall_values = [record["wall_sec"] for record in records]
    runtime_values = [record["reported_runtime_sec"] for record in records]
    timing_keys = sorted(
        set.intersection(
            *(set(record["timings_sec"]) for record in records)
        )
    )
    rss_delta_values = [
        record["memory"]["peak_delta_bytes"]
        for record in records
        if record["memory"]["peak_delta_bytes"] is not None
    ]
    signatures = [
        {
            "result_counts": record["result_counts"],
            "counters": record["counters"],
            "contracts": record["contracts"],
            "providers": record["providers"],
        }
        for record in records
    ]
    return {
        "repeat_count": len(records),
        "wall_time_sec": _distribution(wall_values),
        "reported_runtime_sec": _distribution(runtime_values),
        "primary_rays_per_sec_p50": (
            records[0]["result_counts"]["total_rays"]
            / statistics.median(wall_values)
        ),
        "timings_sec": {
            key: _distribution(
                [record["timings_sec"][key] for record in records]
            )
            for key in timing_keys
        },
        "memory": {
            "rss_peak_delta_bytes": (
                _distribution(rss_delta_values) if rss_delta_values else None
            ),
            "event_tape_peak_bytes_max": max(
                record["memory"]["event_tape_peak_bytes"]
                for record in records
            ),
            "event_tape_copy_bytes_max": max(
                record["counters"]["wavefront_event_tape_copy_bytes"]
                for record in records
            ),
        },
        "semantic_sha256": sorted(
            {record["semantic_sha256"] for record in records}
        ),
        "semantic_mismatch_count": sum(
            record["_semantic_json"] != reference_semantic
            for record in records
        ),
        "repeat_signature_mismatch_count": sum(
            signature != signatures[0] for signature in signatures[1:]
        ),
        "representative": signatures[0],
        "raw_runs": [
            _public_record(record, reference_semantic) for record in records
        ],
    }


def _benchmark_matrix_cell(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_size: int,
    provider: str,
    planner_provider: str,
    contribution_mode: str,
    store_ray_paths: bool,
    max_stored_paths: int,
) -> dict:
    trace_input = _build_case(
        ray_count,
        contribution_mode,
        store_ray_paths,
        max_stored_paths,
    )
    warmup_records: list[dict] = []
    measured_records: list[dict] = []
    sequence_index = 0
    for _ in range(warmups):
        for pipeline in PIPELINES:
            sequence_index += 1
            warmup_records.append(
                _run_once(
                    trace_input,
                    pipeline=pipeline,
                    batch_size=batch_size,
                    provider=provider,
                    planner_provider=planner_provider,
                    phase="warmup",
                    sequence_index=sequence_index,
                )
            )
    for repeat_index in range(repeats):
        order = PIPELINES if repeat_index % 2 == 0 else tuple(reversed(PIPELINES))
        for pipeline in order:
            sequence_index += 1
            measured_records.append(
                _run_once(
                    trace_input,
                    pipeline=pipeline,
                    batch_size=batch_size,
                    provider=provider,
                    planner_provider=planner_provider,
                    phase="measured",
                    sequence_index=sequence_index,
                )
            )
    all_records = warmup_records + measured_records
    reference_record = next(
        record
        for record in all_records
        if record["pipeline_requested"] == "object_reference"
    )
    reference_semantic = reference_record["_semantic_json"]
    grouped = {
        pipeline: [
            record
            for record in measured_records
            if record["pipeline_requested"] == pipeline
        ]
        for pipeline in PIPELINES
    }
    summaries = {
        pipeline: _summarize_pipeline(records, reference_semantic)
        for pipeline, records in grouped.items()
    }
    reference_p50 = summaries["object_reference"]["wall_time_sec"]["p50"]
    soa_p50 = summaries["soa_event_tape"]["wall_time_sec"]["p50"]
    mismatch_count = sum(
        record["_semantic_json"] != reference_semantic
        for record in all_records
    )
    return {
        "name": (
            f"depth10_{contribution_mode}_paths_"
            f"{'on' if store_ray_paths else 'off'}_chunk_{batch_size}"
        ),
        "batch_size": batch_size,
        "contribution_mode": contribution_mode,
        "store_ray_paths": store_ray_paths,
        "max_stored_paths": max_stored_paths,
        "warmups_per_pipeline": warmups,
        "measured_repeats_per_pipeline": repeats,
        "reference_semantic_sha256": _semantic_sha256(reference_semantic),
        "semantic_mismatch_count": mismatch_count,
        "warmups": [
            _public_record(record, reference_semantic)
            for record in warmup_records
        ],
        "pipelines": summaries,
        "comparison": {
            "object_over_soa_wall_speedup": reference_p50 / soa_p50,
            "soa_wall_reduction_percent": (1.0 - soa_p50 / reference_p50)
            * 100.0,
            "soa_minus_object_wall_sec": soa_p50 - reference_p50,
        },
    }


def benchmark(
    *,
    ray_count: int,
    repeats: int,
    warmups: int,
    batch_sizes: list[int],
    provider: str,
    planner_provider: str,
    max_stored_paths: int,
) -> dict:
    cells = [
        _benchmark_matrix_cell(
            ray_count=ray_count,
            repeats=repeats,
            warmups=warmups,
            batch_size=batch_size,
            provider=provider,
            planner_provider=planner_provider,
            contribution_mode=contribution_mode,
            store_ray_paths=store_ray_paths,
            max_stored_paths=max_stored_paths,
        )
        for contribution_mode in ("summary", "detailed")
        for store_ray_paths in (False, True)
        for batch_size in batch_sizes
    ]
    source_files = (
        ROOT / "src" / "leakage_simulator" / "raytracer.py",
        ROOT / "src" / "leakage_simulator" / "wavefront_event_tape.py",
        ROOT / "src" / "leakage_simulator" / "geometry.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_intersection.py",
        ROOT / "src" / "leakage_simulator" / "native_cpu_wavefront.py",
        Path(__file__).resolve(),
    )
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "source_sha256": {
            str(path.relative_to(ROOT)): _file_sha256(path)
            for path in source_files
        },
        "scene": "committed_deterministic_depth10_specular_corridor",
        "ray_count": ray_count,
        "max_depth": 10,
        "repeats_per_pipeline": repeats,
        "warmups_per_pipeline": warmups,
        "batch_sizes": batch_sizes,
        "requested_intersection_provider": provider,
        "requested_wavefront_planner": planner_provider,
        "pipelines": list(PIPELINES),
        "contribution_modes": ["summary", "detailed"],
        "path_modes": [False, True],
        "max_stored_paths": max_stored_paths,
        "native_cold_start_excluded": warmups > 0,
        "semantic_comparison": (
            "exact ordered JSON excluding run_id/runtime/performance summary"
        ),
        "memory_measurement": (
            "5ms process RSS sampling plus event-tape peak bytes"
            if RSS_READER is not None
            else "event-tape peak bytes; process RSS unavailable"
        ),
        "matrix": cells,
        "semantic_mismatch_count": sum(
            cell["semantic_mismatch_count"] for cell in cells
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark PERF-3B-2C object-reference versus explicit SoA "
            "event-tape pipelines on the committed deterministic depth-10 "
            "corridor."
        )
    )
    parser.add_argument(
        "--rays",
        type=int,
        default=10_000,
        help="Primary rays per matrix cell; use a small value for smoke runs.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Measured warm repeats per pipeline and matrix cell.",
    )
    parser.add_argument(
        "--warmups",
        type=int,
        default=1,
        help="Unmeasured warmups per pipeline and matrix cell.",
    )
    parser.add_argument(
        "--batch-sizes",
        type=int,
        nargs="+",
        default=DEFAULT_BATCH_SIZES,
    )
    parser.add_argument(
        "--provider",
        choices=("python_cpu", "numba_cpu"),
        default="numba_cpu",
    )
    parser.add_argument(
        "--planner-provider",
        choices=("auto", "python_cpu", "numba_cpu"),
        default="auto",
    )
    parser.add_argument("--max-stored-paths", type=int, default=500)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    if args.rays <= 0 or args.repeats <= 0:
        raise SystemExit("rays and repeats must be positive")
    if args.warmups < 0:
        raise SystemExit("warmups must be non-negative")
    if not args.batch_sizes or any(size <= 0 for size in args.batch_sizes):
        raise SystemExit("batch sizes must be positive")
    if args.max_stored_paths <= 0:
        raise SystemExit("max stored paths must be positive")

    summary = benchmark(
        ray_count=args.rays,
        repeats=args.repeats,
        warmups=args.warmups,
        batch_sizes=list(dict.fromkeys(args.batch_sizes)),
        provider=args.provider,
        planner_provider=args.planner_provider,
        max_stored_paths=args.max_stored_paths,
    )
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
    )
    print(encoded)
    if not args.no_write:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = OUTPUT_DIR / "summary.json"
        summary_path.write_text(f"{encoded}\n", encoding="utf-8")
        print(f"summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
