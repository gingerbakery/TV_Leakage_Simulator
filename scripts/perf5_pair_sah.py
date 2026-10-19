from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import time
from unittest.mock import patch

import numpy as np

from leakage_simulator import gpu_cuda_pair_scene as pair_scene


class SahSplitter:
    def __init__(self, triangles, bins, cutoff):
        if bins not in (8, 16, 32) or cutoff < 2:
            raise ValueError('Unsupported SAH bin count or cutoff')
        self.bins = bins
        self.cutoff = cutoff
        self.centers = np.asarray([triangle.centroid for triangle in triangles], dtype=np.float64)
        self.lower = np.asarray([triangle.bounds_min for triangle in triangles], dtype=np.float64)
        self.upper = np.asarray([triangle.bounds_max for triangle in triangles], dtype=np.float64)
        if (not all(np.all(np.isfinite(value)) for value in (self.centers, self.lower, self.upper))
            or np.any(self.lower > self.upper)):
            raise ValueError('SAH requires finite ordered triangle bounds')

    @staticmethod
    def areas(lower, upper):
        extent = np.maximum(upper - lower, 0.0)
        return 2.0 * (extent[:, 0] * extent[:, 1] + extent[:, 1] * extent[:, 2]
            + extent[:, 2] * extent[:, 0])

    def __call__(self, indices, depth):
        selected = np.asarray(indices, dtype=np.int64)
        centers = self.centers[selected]
        minimum = centers.min(axis=0)
        extent = centers.max(axis=0) - minimum
        best_cost = np.inf
        best_partition = None
        if len(selected) >= self.cutoff and depth < 48:
            lower, upper = self.lower[selected], self.upper[selected]
            for axis in range(3):
                if extent[axis] <= 0.0:
                    continue
                positions = np.minimum(((centers[:, axis] - minimum[axis])
                    / extent[axis] * self.bins).astype(np.int64), self.bins - 1)
                counts = np.bincount(positions, minlength=self.bins)
                bin_lower = np.full((self.bins, 3), np.inf)
                bin_upper = np.full((self.bins, 3), -np.inf)
                np.minimum.at(bin_lower, positions, lower)
                np.maximum.at(bin_upper, positions, upper)
                left_counts = np.cumsum(counts)[:-1]
                right_counts = np.cumsum(counts[::-1])[::-1][1:]
                valid = np.flatnonzero((left_counts > 0) & (right_counts > 0))
                if not len(valid):
                    continue
                left_lower = np.minimum.accumulate(bin_lower, axis=0)[:-1][valid]
                left_upper = np.maximum.accumulate(bin_upper, axis=0)[:-1][valid]
                right_lower = np.minimum.accumulate(bin_lower[::-1], axis=0)[::-1][1:][valid]
                right_upper = np.maximum.accumulate(bin_upper[::-1], axis=0)[::-1][1:][valid]
                costs = left_counts[valid] * self.areas(left_lower, left_upper)
                costs += right_counts[valid] * self.areas(right_lower, right_upper)
                choice = int(np.argmin(costs))
                if costs[choice] < best_cost:
                    best_cost = float(costs[choice])
                    best_partition = positions <= valid[choice]
        if best_partition is not None:
            return selected[best_partition].tolist(), selected[~best_partition].tolist()
        axis = int(np.argmax(extent))
        order = np.argsort(centers[:, axis], kind='stable')
        middle = len(selected) // 2
        return selected[order[:middle]].tolist(), selected[order[middle:]].tolist()


@lru_cache(maxsize=12)
def scene_builder(bins=16, cutoff=32):
    if bins not in (8, 16, 32) or cutoff not in (16, 32, 64, 128):
        raise ValueError('Unsupported experimental SAH configuration')
    def build(mesh, original):
        return pair_scene._build_pair_scene(mesh, original,
            splitter_factory=lambda triangles: SahSplitter(triangles, bins, cutoff))

    return build


@contextmanager
def scene_variant(bins, cutoff, states):
    builder = scene_builder(bins, cutoff)

    def prepare(mesh):
        mode = pair_scene.pair_mode()
        with mesh._gpu_cuda_scene_lock:
            original = mesh.prepare_gpu_cuda_scene()
            if mode == 'off' or len(original.node_count) < 2 or (
                mode == 'auto' and len(original.ordered_faces) < pair_scene.AUTO_MIN_FACES
            ):
                return original, 0.0, False
            key = (id(mesh), bins, cutoff)
            cached = states.get(key)
            if cached is not None and cached[0] is mesh and cached[1] is original:
                return cached[2], 0.0, True
            started = time.perf_counter()
            result = builder(mesh, original)
            states[key] = (mesh, original, result)
            return result, time.perf_counter() - started, False

    with patch.object(pair_scene, 'prepare_pair_scene', new=prepare):
        yield
