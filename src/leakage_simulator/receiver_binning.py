from __future__ import annotations

import math

import numpy as np


CONTRACT = "receiver_internal_boundary_fp64_v1"
ROUNDOFF_FACTOR = 32.0 * float(np.finfo(np.float64).eps)
MAX_BOUNDARY_BAND_CELLS = 1e-7


def receiver_bin_index(local_value, half_extent, inverse_extent, cell_count, coordinate_scale):
    """Classify an accepted hit; only internal grid lines have a roundoff band."""
    scaled = (local_value + half_extent) * inverse_extent * cell_count
    boundary = math.floor(scaled + 0.5)
    if 0 < boundary < cell_count:
        band_scale = coordinate_scale * inverse_extent
        if band_scale < 1.0:
            band_scale = 1.0
        band = ROUNDOFF_FACTOR * cell_count * band_scale
        if band > MAX_BOUNDARY_BAND_CELLS:
            band = MAX_BOUNDARY_BAND_CELLS
        if abs(scaled - boundary) <= band:
            return boundary
    index = math.floor(scaled)
    if index < 0:
        return 0
    if index >= cell_count:
        return cell_count - 1
    return index


def receiver_bin_indices(local_values, half_extent, inverse_extent, cell_count, coordinate_scale):
    """Vectorized equivalent of receiver_bin_index, without changing hit acceptance."""
    scaled = (local_values + half_extent) * inverse_extent * cell_count
    boundaries = (scaled + 0.5).astype(np.int64)
    band = np.minimum(
        MAX_BOUNDARY_BAND_CELLS,
        ROUNDOFF_FACTOR * cell_count * np.maximum(1.0, coordinate_scale * inverse_extent),
    )
    snap = (boundaries > 0) & (boundaries < cell_count) & (np.abs(scaled - boundaries) <= band)
    return np.clip(np.where(snap, boundaries, scaled.astype(np.int64)), 0, cell_count - 1)
