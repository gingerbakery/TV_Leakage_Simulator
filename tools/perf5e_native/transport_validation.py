from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
import time
from unittest.mock import patch

import numpy as np

from leakage_simulator import raytracer
from tools.perf5e_native.probe import HIT_DTYPE, OptixCandidateProbe, pack_rays


CONTRACT = "experimental_optix_transport_shadow_v1"
_OBSERVATION_LOCK = threading.Lock()


def production_preflight_gate(result):
    return all(result.get(field) is True for field in (
        "available", "strict_float64", "kernel_executed", "kernel_verified",
    )) and result.get("preflight_scope") == "production_ray_bvh" and (
        result.get("provider_contract") == "strict_float64_bvh_v1"
    )


def compare_query(candidate, visited, reference):
    count = len(reference)
    if candidate.dtype != HIT_DTYPE or candidate.shape != (count,):
        raise ValueError("Invalid candidate layout")
    if visited.dtype != np.uint8 or visited.shape != (count,) or np.any(visited > 3):
        raise ValueError("Invalid visitation mask")
    flags = candidate["flags"]
    if np.any(flags < 0) or np.any(flags > 7):
        raise ValueError("Invalid candidate flags")
    guarded = (flags & 4) != 0
    if np.any(guarded & (visited != 0)):
        raise ValueError("Guarded rays must not report candidate visits")
    hit = reference.face_indices >= 0
    seen = (visited & 1) != 0
    if np.any(((visited & 2) != 0) != seen):
        raise ValueError("Duplicate reference visitation disagrees")
    if np.any(~hit & seen):
        raise ValueError("Miss sentinel reported as a visited face")
    same_face = candidate["face"] == reference.face_indices
    same_distance = np.isclose(candidate["distance"], reference.t, rtol=1e-12, atol=1e-12)
    same_distance &= ~np.isnan(candidate["distance"])
    mismatch = ~(same_face & same_distance)
    return {
        "queries": count,
        "reference_hits": int(hit.sum()),
        "guarded_queries": int(guarded.sum()),
        "guarded_reference_hits": int((guarded & hit).sum()),
        "eligible_reference_hits": int((hit & ~guarded).sum()),
        "missing_reference_visits": int((hit & ~guarded & ~seen).sum()),
        "raw_face_mismatches": int((~same_face).sum()),
        "raw_distance_mismatches": int((~same_distance).sum()),
        "unflagged_mismatches": int((mismatch & (flags == 0)).sum()),
        "candidate_visits": int(candidate["candidates"].sum()),
    }


class OptixTransportObserver:
    production_enabled = False
    contract = CONTRACT

    def __init__(self, directory: Path, *, factory=OptixCandidateProbe, capture=None):
        self.directory = Path(directory)
        self.factory = factory
        self.capture = capture
        self.scene = None
        self.probe = None
        self.closed = False
        self.build_count = 0
        self.build_seconds = 0.0
        self.attempts = 0
        self.successes = 0
        self.empty_queries = 0
        self.records = []

    def observe(self, mesh, rays, reference):
        if self.closed:
            raise RuntimeError("Observer is closed")
        scene = mesh.prepare_native_cpu_scene()
        if scene is not self.scene:
            if self.probe is not None:
                self.probe.close()
            self.probe = None
            self.scene = None
            if np.any(scene.traceable_face_mask):
                started = time.perf_counter()
                self.probe = self.factory(scene, self.directory)
                self.build_seconds += time.perf_counter() - started
                self.build_count += 1
            self.scene = scene
        if self.probe is None or len(rays) == 0:
            self.empty_queries += len(rays)
            if np.any(reference.face_indices >= 0):
                raise ValueError("Empty scene returned a reference hit")
            return reference
        packed = pack_rays(rays.origins, rays.directions, rays.min_t, rays.max_t, rays.ignore_faces)
        expected = np.repeat(reference.face_indices[:, None], 2, axis=1)
        self.attempts += 1
        started = time.perf_counter()
        candidate, visited, timings = self.probe.query_coverage(packed, expected)
        record = compare_query(candidate, visited, reference)
        record.update(seconds=time.perf_counter() - started, timings=timings)
        if self.capture is not None:
            record["artifact"] = self.capture(
                len(self.records), packed, candidate, visited, reference, scene,
            )
        self.records.append(record)
        self.successes += 1
        return reference

    def summary(self):
        totals = {key: sum(record[key] for record in self.records) for key in (
            "queries", "reference_hits", "guarded_queries", "guarded_reference_hits",
            "eligible_reference_hits", "missing_reference_visits", "raw_face_mismatches",
            "raw_distance_mismatches", "unflagged_mismatches", "candidate_visits",
        )}
        return {
            "contract": self.contract, "production_enabled": False,
            "authoritative_provider": "unchanged_canonical_dispatch",
            "candidate_results_used": 0, "attempts": self.attempts, "successes": self.successes,
            "build_count": self.build_count, "build_seconds": self.build_seconds,
            "empty_scene_queries": self.empty_queries, **totals,
            "observed_coverage_passed": totals["eligible_reference_hits"] > 0
            and totals["missing_reference_visits"] == 0,
            "records": self.records,
        }

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.probe is not None:
            self.probe.close()
        self.probe = None
        self.scene = None


@contextmanager
def observe_dispatch(observer):
    if not _OBSERVATION_LOCK.acquire(blocking=False):
        raise RuntimeError("Concurrent or nested transport observation is unsupported")
    owner = threading.get_ident()
    original = raytracer._IntersectionDispatchStats.intersect_batch

    def observed(dispatch, mesh, rays, **kwargs):
        if threading.get_ident() != owner:
            raise RuntimeError("Transport observation must run in an isolated process")
        reference = original(dispatch, mesh, rays, **kwargs)
        return observer.observe(mesh, rays, reference)

    try:
        with patch.object(raytracer._IntersectionDispatchStats, "intersect_batch", observed):
            yield observer
    finally:
        try:
            observer.close()
        finally:
            _OBSERVATION_LOCK.release()


def run_diagnostic_transport(trace_input, *, observer=None, should_stop=None, observation_context=observe_dispatch):
    options = dict(
        intersection_dispatch="batch",
        intersection_batch_size=8192,
        intersection_provider="gpu_cuda" if trace_input.config.compute_backend == "gpu_cuda" else "numba_cpu",
        wavefront_pipeline="soa_event_tape",
        wavefront_rng="counter_rng_v2",
        wavefront_residency="host_roundtrip",
        gpu_accumulator="host",
    )
    with patch.dict("os.environ", {"LEAKAGE_CPU_TRANSPORT": "wavefront"}):
        if observer is None:
            return raytracer.run_direct_ray_trace(trace_input, should_stop=should_stop, **options)
        with observation_context(observer):
            return raytracer.run_direct_ray_trace(trace_input, should_stop=should_stop, **options)


def semantic_payload(result):
    payload = result.to_dict()
    payload.pop("run_id", None)
    payload.pop("runtime_sec", None)
    payload["metrics"].pop("_performance_summary", None)
    return payload
