from __future__ import annotations

from contextlib import contextmanager
import math
import threading
import time
from unittest.mock import patch

import numpy as np

from leakage_simulator import raytracer
from leakage_simulator.geometry import RayBatch, RayHitBatch
from tools.perf5e_native.probe import HIT_DTYPE, pack_rays
from tools.perf5e_native.transport_validation import (
    OptixTransportObserver, _OBSERVATION_LOCK,
)


CONTRACT = "experimental_optix_selective_replay_oracle_checked_v1"
_CPU_REFINE = None
_GPU_REFINE = None


def replay_mask(candidate, face_mask, rays):
    if candidate.dtype != HIT_DTYPE or candidate.shape != (len(rays),):
        raise ValueError("Invalid candidate layout")
    faces = candidate["face"]
    flags = candidate["flags"]
    if np.any((flags < 0) | (flags > 7)):
        raise ValueError("Unknown candidate flags")
    if np.any((faces < -1) | (faces >= len(face_mask))):
        raise ValueError("Invalid candidate face")
    present = faces >= 0
    if np.any(~face_mask[faces[present]]):
        raise ValueError("Candidate face is excluded")
    if np.any(present & (faces == rays.ignore_faces)):
        raise ValueError("Candidate returned an ignored face")
    if np.any(present & (~np.isfinite(candidate["distance"]) |
                         (candidate["distance"] <= rays.min_t) |
                         (candidate["distance"] > rays.max_t))):
        raise ValueError("Invalid candidate distance")
    if np.any(~present & ~np.isposinf(candidate["distance"])):
        raise ValueError("Invalid candidate miss")
    return (flags != 0) | ~present


def select_rays(rays, rows):
    return RayBatch(rays.origins[rows], rays.directions[rows], rays.min_t[rows],
                    rays.max_t[rows], rays.ignore_faces[rows])


def _winner_distance(row, origins, directions, minimum, maximum, faces, vertices, edges1, edges2):
    face = faces[row]
    origin_x, origin_y, origin_z = origins[row, 0], origins[row, 1], origins[row, 2]
    direction_x, direction_y, direction_z = directions[row, 0], directions[row, 1], directions[row, 2]
    edge1_x, edge1_y, edge1_z = edges1[face, 0], edges1[face, 1], edges1[face, 2]
    edge2_x, edge2_y, edge2_z = edges2[face, 0], edges2[face, 1], edges2[face, 2]
    cross_x = direction_y * edge2_z - direction_z * edge2_y
    cross_y = direction_z * edge2_x - direction_x * edge2_z
    cross_z = direction_x * edge2_y - direction_y * edge2_x
    determinant = edge1_x * cross_x + edge1_y * cross_y + edge1_z * cross_z
    if -1e-8 < determinant < 1e-8:
        return math.inf
    inverse_determinant = 1.0 / determinant
    offset_x = origin_x - vertices[face, 0]
    offset_y = origin_y - vertices[face, 1]
    offset_z = origin_z - vertices[face, 2]
    barycentric_u = (offset_x * cross_x + offset_y * cross_y + offset_z * cross_z) * inverse_determinant
    if barycentric_u < 0.0 or barycentric_u > 1.0:
        return math.inf
    offset_cross_x = offset_y * edge1_z - offset_z * edge1_y
    offset_cross_y = offset_z * edge1_x - offset_x * edge1_z
    offset_cross_z = offset_x * edge1_y - offset_y * edge1_x
    barycentric_v = (direction_x * offset_cross_x + direction_y * offset_cross_y + direction_z * offset_cross_z) * inverse_determinant
    if barycentric_v < 0.0 or barycentric_u + barycentric_v > 1.0:
        return math.inf
    distance = (edge2_x * offset_cross_x + edge2_y * offset_cross_y + edge2_z * offset_cross_z) * inverse_determinant
    if distance <= minimum[row] or distance > maximum[row]:
        return math.inf
    return distance


def refine_winners(mesh, rays, faces, backend):
    global _CPU_REFINE, _GPU_REFINE
    from numba import njit, cuda

    if backend == "cpu":
        if _CPU_REFINE is None:
            scalar = njit(inline="always", fastmath=False)(_winner_distance)

            @njit(nogil=True, fastmath=False)
            def kernel(origins, directions, minimum, maximum, winners, vertices, edges1, edges2, output):
                for row in range(len(winners)):
                    output[row] = scalar(row, origins, directions, minimum, maximum, winners, vertices, edges1, edges2)

            _CPU_REFINE = kernel
        scene = mesh.prepare_native_cpu_scene()
        output = np.empty(len(rays), dtype=np.float64)
        _CPU_REFINE(rays.origins, rays.directions, rays.min_t, rays.max_t, faces,
                    scene.triangle_v0, scene.triangle_edge1, scene.triangle_edge2, output)
        return output
    if backend != "gpu_cuda":
        raise ValueError("Unsupported refinement backend")
    from leakage_simulator import gpu_cuda_intersection as gpu

    if _GPU_REFINE is None:
        scalar = cuda.jit(device=True, inline=True, fastmath=False)(_winner_distance)

        @cuda.jit(fastmath=False)
        def kernel(origins, directions, minimum, maximum, winners, vertices, edges1, edges2, output):
            row = cuda.grid(1)
            if row < len(winners):
                output[row] = scalar(row, origins, directions, minimum, maximum, winners, vertices, edges1, edges2)

        _GPU_REFINE = kernel
    scene = mesh.prepare_gpu_cuda_scene()
    device, _ = gpu._ensure_device_scene(scene, gpu.probe_gpu_cuda())
    inputs = [cuda.to_device(array) for array in (rays.origins, rays.directions, rays.min_t, rays.max_t, faces)]
    output = cuda.device_array(len(rays), dtype=np.float64)
    _GPU_REFINE[(len(rays) + 127) // 128, 128](
        *inputs, device.triangle_v0, device.triangle_edge1, device.triangle_edge2, output,
    )
    cuda.synchronize()
    return output.copy_to_host()


def selective_query(mesh, rays, candidate, backend, *, refine=refine_winners):
    if backend not in ("cpu", "gpu_cuda"):
        raise ValueError("Unsupported replay backend")
    replay = replay_mask(candidate, mesh.prepare_native_cpu_scene().traceable_face_mask, rays)
    faces = candidate["face"].copy()
    distance = candidate["distance"].copy()
    winners = np.flatnonzero(~replay)
    rejected = np.zeros(len(rays), dtype=np.bool_)
    if len(winners):
        refined = refine(mesh, select_rays(rays, winners), faces[winners], backend)
        if refined.dtype != np.float64 or refined.shape != (len(winners),):
            raise ValueError("Invalid refinement output")
        accepted = np.isfinite(refined) & (refined > rays.min_t[winners]) & (refined <= rays.max_t[winners])
        distance[winners[accepted]] = refined[accepted]
        rejected[winners[~accepted]] = True
        replay |= rejected
    rows = np.flatnonzero(replay)
    if len(rows):
        query = mesh.intersect_rays_gpu_cuda if backend == "gpu_cuda" else mesh.intersect_rays_native_cpu
        corrected, _ = query(select_rays(rays, rows), backend="bvh")
        distance[rows], faces[rows] = corrected.t, corrected.face_indices
    return RayHitBatch(distance, faces), replay, rejected


def assert_exact_query(actual, expected):
    if actual.t.shape != expected.t.shape or actual.face_indices.shape != expected.face_indices.shape:
        raise AssertionError("Selective replay cardinality differs from oracle")
    face_mismatches = int(np.count_nonzero(actual.face_indices != expected.face_indices))
    distance_mismatches = int(np.count_nonzero(actual.t.view(np.uint64) != expected.t.view(np.uint64)))
    if face_mismatches or distance_mismatches:
        raise AssertionError(f"Selective replay differs from oracle: faces={face_mismatches}, distance_bits={distance_mismatches}")


class SelectiveReplayObserver(OptixTransportObserver):
    contract = CONTRACT

    def __init__(self, directory, *, capture=None, selective_capture=None, **kwargs):
        super().__init__(directory, capture=capture, **kwargs)
        self.selective_capture = selective_capture

    def observe_with_backend(self, mesh, rays, reference, backend):
        super().observe(mesh, rays, reference)
        if self.probe is None or not len(rays):
            return reference
        packed = pack_rays(rays.origins, rays.directions, rays.min_t, rays.max_t, rays.ignore_faces)
        started = time.perf_counter()
        candidate, timings = self.probe.query(packed)
        corrected, replay, rejected = selective_query(mesh, rays, candidate, backend)
        elapsed = time.perf_counter() - started
        record = self.records[-1]
        record.update(
            refinement_backend=backend, selective_seconds=elapsed,
            selective_timings=timings, replay_rows=int(replay.sum()),
            winner_only_rows=int((~replay).sum()), refinement_rejected_rows=int(rejected.sum()),
            corrected_face_mismatches=int(np.count_nonzero(corrected.face_indices != reference.face_indices)),
            corrected_distance_bit_mismatches=int(np.count_nonzero(corrected.t.view(np.uint64) != reference.t.view(np.uint64))),
        )
        if self.selective_capture is not None:
            record["selective_artifact"] = self.selective_capture(
                len(self.records) - 1, packed, candidate, corrected, replay, rejected, reference, backend,
            )
        assert_exact_query(corrected, reference)
        return corrected

    def summary(self):
        summary = super().summary()
        summary.update(
            candidate_results_used=sum(record.get("winner_only_rows", 0) for record in self.records),
            authoritative_provider="experimental_candidate_checked_against_full_canonical_oracle",
            production_coverage_proven=False,
        )
        for key in ("replay_rows", "winner_only_rows", "refinement_rejected_rows", "corrected_face_mismatches",
                    "corrected_distance_bit_mismatches", "selective_seconds"):
            summary[key] = sum(record.get(key, 0) for record in self.records)
        return summary


@contextmanager
def selective_dispatch(observer):
    if not _OBSERVATION_LOCK.acquire(blocking=False):
        raise RuntimeError("Concurrent or nested transport observation is unsupported")
    owner = threading.get_ident()
    original = raytracer._IntersectionDispatchStats.intersect_batch

    def observed(dispatch, mesh, rays, **kwargs):
        if threading.get_ident() != owner:
            raise RuntimeError("Selective replay requires an isolated process")
        cuda_before = dispatch.gpu_cuda_gpu_success_count
        native_before = dispatch.native_success_count
        reference = original(dispatch, mesh, rays, **kwargs)
        if dispatch.native_success_count <= native_before:
            raise RuntimeError("Canonical native dispatch failed; replay cannot be certified")
        backend = "gpu_cuda" if dispatch.gpu_cuda_gpu_success_count > cuda_before else "cpu"
        return observer.observe_with_backend(mesh, rays, reference, backend)

    try:
        with patch.object(raytracer._IntersectionDispatchStats, "intersect_batch", observed):
            yield observer
    finally:
        try:
            observer.close()
        finally:
            _OBSERVATION_LOCK.release()
