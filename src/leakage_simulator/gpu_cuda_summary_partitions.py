from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import numpy as np


CONTRACT = 'partitioned_integer_global_fp64_summary_v1'
WORKSPACE_LIMIT_BYTES = 8 * 1024 * 1024
_UNPARTITIONED = frozenset({
    'profile_incoming_flux_lumen', 'profile_reflected_flux_lumen',
    'reflection_flux_lumen', 'reflection_lobe_flux_lumen',
    'reflection_depth_flux_lumen', 'contribution_receiver_flux_lumen',
    'contribution_lobe_flux_lumen', 'contribution_depth_flux_lumen',
    'receiver_flux_lumen', 'receiver_depth_flux_lumen', 'grid_flux_lumen',
    'grid_flux_squared_lumen2', 'grid_flux_squared_totals_lumen2',
})
_TOUCH_FIELDS = (
    'profile_touch_ordinals', 'reflection_touch_ordinals',
    'contribution_touch_ordinals', 'receiver_touch_ordinals',
)


@dataclass(slots=True)
class PartitionWorkspace:
    count: int
    arrays: dict[str, Any]
    storage: Any
    metadata: Any
    sentinel_ranges: Any
    logical_words: int
    nbytes: int


def choose_count(state, ray_count, *, requested=None, extra_words=0):
    if not isinstance(extra_words, int) or extra_words < 0:
        raise ValueError('GPU summary extra metadata size must be a nonnegative integer')
    mode = os.environ.get('LEAKAGE_GPU_SUMMARY_PARTITIONS', 'auto') if requested is None else requested
    if str(mode) == 'auto':
        if ray_count < 8192:
            return 1
        limit = 128
    else:
        try:
            limit = int(mode)
        except (TypeError, ValueError) as exc:
            raise ValueError('GPU summary partitions must be auto or a power of two') from exc
        if limit < 1 or limit > 128 or limit & (limit - 1):
            raise ValueError('GPU summary partitions must be within 1..128 and a power of two')
    words = sum(values.size for name, values in state.items() if name not in _UNPARTITIONED)
    words += state['profile_hit_counts'].size
    words += 2 * state['reflection_depth_counts'].shape[0]
    words += state['receiver_depth_counts'].size + 5 + extra_words
    available = (WORKSPACE_LIMIT_BYTES - 4096) // max(1, words * 8)
    blocks = max(1, (ray_count + 127) // 128)
    feasible = max(1, min(limit, available, blocks))
    return 1 << (feasible.bit_length() - 1)


def create_workspace(cuda, state, layout, count):
    names = [name for name in state if name not in _UNPARTITIONED]
    names.extend((*_TOUCH_FIELDS, 'chunk_counts'))
    metadata = []
    ranges = []
    arrays_layout = []
    logical_words = 0
    storage_words = 0
    for name in names:
        start, end, dtype, shape = layout[name]
        if dtype.itemsize != 8 or start % 8 or end % 8:
            raise ValueError('GPU summary partition fields must be aligned 64-bit arrays')
        length = (end - start) // 8
        if name in _TOUCH_FIELDS:
            merge_mode = 2
            ranges.append((storage_words, storage_words + count * length))
        elif name == 'reflection_counts':
            merge_mode = 3
        elif name == 'chunk_counts':
            merge_mode = 4
        else:
            merge_mode = 0
        metadata.append((logical_words, storage_words, start // 8, length, merge_mode))
        arrays_layout.append((name, storage_words, length, dtype, shape))
        logical_words += length
        storage_words += count * length
    metadata_host = np.asarray(metadata, dtype=np.int64)
    ranges_host = np.asarray(ranges, dtype=np.int64).reshape(-1, 2)
    storage = cuda.device_array(storage_words, dtype=np.int64)
    arrays = {
        name: storage[start:start + count * length].view(dtype).reshape((count, *shape))
        for name, start, length, dtype, shape in arrays_layout
    }
    nbytes = storage.nbytes + metadata_host.nbytes + ranges_host.nbytes
    if nbytes > WORKSPACE_LIMIT_BYTES:
        raise ValueError('GPU summary partition workspace exceeds its memory budget')
    return PartitionWorkspace(
        count, arrays, storage, cuda.to_device(metadata_host),
        cuda.to_device(ranges_host), logical_words, int(nbytes),
    )


def make_merge_kernels(cuda):
    @cuda.jit
    def reset(storage, sentinel_ranges):
        address = cuda.grid(1)
        if address >= storage.shape[0]:
            return
        value = 0
        for region in range(sentinel_ranges.shape[0]):
            if address >= sentinel_ranges[region, 0] and address < sentinel_ranges[region, 1]:
                value = 9223372036854775807
                break
        storage[address] = value

    @cuda.jit
    def merge(logical_words, partition_count, metadata, partial_int, global_int):
        logical_address = cuda.grid(1)
        if logical_address >= logical_words:
            return
        field = 0
        while logical_address >= metadata[field, 0] + metadata[field, 3]:
            field += 1
        position = logical_address - metadata[field, 0]
        partial_start = metadata[field, 1]
        global_address = metadata[field, 2] + position
        stride = metadata[field, 3]
        mode = metadata[field, 4]
        total_int = global_int[global_address]
        emission_reference = 0
        for partition in range(partition_count):
            value = partial_int[partial_start + partition * stride + position]
            if mode == 4 and position >= 5 and (position - 5) % 3 == 1:
                reference = partial_int[partial_start + partition * stride + position - 1]
                if reference != 0:
                    if emission_reference != 0 and emission_reference != reference:
                        total_int = 1
                    emission_reference = reference
            if mode == 2:
                if value < total_int:
                    total_int = value
            elif (mode == 3 and position == 0) or (mode == 4 and (
                position == 4 or (position >= 5 and (position - 5) % 3 < 2)
            )):
                if value > total_int:
                    total_int = value
            else:
                total_int += value
        global_int[global_address] = total_int

    return reset, merge
