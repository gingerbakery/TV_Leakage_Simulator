from __future__ import annotations

from dataclasses import dataclass
import importlib
import threading
from typing import Any

import numpy as np

from . import gpu_cuda_receiver_moments as moments
from .wavefront_event_tape import TERMINAL_RECEIVER

CONTRACT = 'device_primary_ordered_receiver_grid_v1'
WORKSPACE_LIMIT_BYTES = 64 * 1024 * 1024
_LOCK = threading.Lock()
_KERNELS = None


@dataclass(slots=True)
class ReceiverGridWorkspace:
    cells: int
    count: int
    tile_width: int
    tile_count: int
    counts: Any
    initial_flux: Any
    initial_squared: Any
    minimum_bits: Any
    maximum_bits: Any

    @property
    def nbytes(self):
        return sum(values.nbytes for values in (self.counts, self.initial_flux,
            self.initial_squared, self.minimum_bits, self.maximum_bits))


def workspace_layout(count, cells):
    if count < 0 or count > np.iinfo(np.int32).max or cells < 0 or cells * 36 > WORKSPACE_LIMIT_BYTES:
        raise ValueError('Receiver grid workspace dimensions exceed the recording budget')
    width = 128
    tiles = (count + width - 1) // width
    while cells * (32 + tiles * 4) > WORKSPACE_LIMIT_BYTES:
        width *= 2
        tiles = (count + width - 1) // width
    return width, tiles


def ensure_workspace(cuda, previous, count, cells):
    width, tiles = workspace_layout(count, cells)
    if previous is not None and previous.cells == cells and previous.count == count:
        return previous
    return ReceiverGridWorkspace(cells, count, width, tiles,
        cuda.device_array((cells, tiles), dtype=np.int32),
        cuda.device_array(cells, dtype=np.float64), cuda.device_array(cells, dtype=np.float64),
        cuda.device_array(cells, dtype=np.int64), cuda.device_array(cells, dtype=np.int64))


def make_kernels(cuda):
    libdevice = importlib.import_module('numba.cuda.libdevice')
    fold = moments.make_warp_ordered_fold(cuda)
    repeat_identical = moments.make_kernels(cuda)[2]

    @cuda.jit
    def snapshot(flux, squared, counts, initial_flux, initial_squared, minimum_bits, maximum_bits):
        address = cuda.grid(1)
        if address < flux.size:
            initial_flux[address] = flux[address]
            initial_squared[address] = squared[address]
            minimum_bits[address] = np.int64(9223372036854775807)
            maximum_bits[address] = np.int64(-9223372036854775807 - 1)
        if address < counts.size:
            counts.flat[address] = 0

    @cuda.jit
    def classify(count, width, kinds, receivers, rows, columns, powers, receiver_columns, offsets,
                 counts, minimum_bits, maximum_bits, chunk_counts):
        if chunk_counts[4] != 0:
            return
        primary = cuda.grid(1)
        if primary >= count or kinds[primary] != TERMINAL_RECEIVER:
            return
        receiver = receivers[primary]
        cell = offsets[receiver] + rows[primary] * receiver_columns[receiver] + columns[primary]
        cuda.atomic.add(counts, (cell, primary // width), 1)
        bits = libdevice.double_as_longlong(powers[primary])
        if bits < minimum_bits[cell]:
            cuda.atomic.min(minimum_bits, cell, bits)
        if bits > maximum_bits[cell]:
            cuda.atomic.max(maximum_bits, cell, bits)

    @cuda.jit(fastmath=False)
    def accumulate(count, width, tiles, kinds, receivers, rows, columns, powers, receiver_columns, offsets,
                   counts, initial_flux, initial_squared, minimum_bits, maximum_bits, flux, squared, chunk_counts):
        if chunk_counts[4] != 0:
            return
        address = cuda.grid(1)
        cell = address // 32
        lane = address & 31
        if cell >= flux.size:
            return
        total = initial_flux[cell]
        total_squared = initial_squared[cell]
        if minimum_bits[cell] == maximum_bits[cell]:
            if lane == 0:
                hits = 0
                for tile in range(tiles):
                    hits += counts[cell, tile]
                power = libdevice.longlong_as_double(minimum_bits[cell])
                flux[cell] = repeat_identical(total, power, hits)
                square = libdevice.dmul_rn(power, power)
                squared[cell] = repeat_identical(total_squared, square, hits)
            return
        for tile in range(tiles):
            if counts[cell, tile] == 0:
                continue
            stop = (tile + 1) * width
            if stop > count:
                stop = count
            for start in range(tile * width, stop, 32):
                primary = start + lane
                valid = False
                power = 0.0
                if primary < stop and kinds[primary] == TERMINAL_RECEIVER:
                    receiver = receivers[primary]
                    selected = offsets[receiver] + rows[primary] * receiver_columns[receiver] + columns[primary]
                    valid = selected == cell
                    if valid:
                        power = powers[primary]
                mask = cuda.ballot_sync(0xffffffff, valid)
                if mask == 0:
                    continue
                if mask & (mask - 1) == 0:
                    source = libdevice.ffs(np.int32(mask)) - 1
                    value = cuda.shfl_sync(0xffffffff, power, source)
                    if lane == 0:
                        total = libdevice.dadd_rn(total, value)
                        total_squared = libdevice.dadd_rn(total_squared, libdevice.dmul_rn(value, value))
                    total = cuda.shfl_sync(0xffffffff, total, 0)
                    total_squared = cuda.shfl_sync(0xffffffff, total_squared, 0)
                else:
                    total, _ = fold(total, power, valid)
                    square = libdevice.dmul_rn(power, power)
                    total_squared, _ = fold(total_squared, square, valid)
        if lane == 0:
            flux[cell] = total
            squared[cell] = total_squared

    return snapshot, classify, accumulate


def kernels(cuda):
    global _KERNELS
    with _LOCK:
        if _KERNELS is None:
            _KERNELS = make_kernels(cuda)
        return _KERNELS


def snapshot_device_grid(cuda, workspace, state):
    snapshot, _, _ = kernels(cuda)
    size = max(workspace.counts.size, workspace.cells)
    if size:
        snapshot[(size + 127) // 128, 128](state['grid_flux_lumen'], state['grid_flux_squared_lumen2'],
            workspace.counts, workspace.initial_flux, workspace.initial_squared,
            workspace.minimum_bits, workspace.maximum_bits)


def accumulate_device_ordered_grid(cuda, workspace, events, session):
    if not workspace.count or not workspace.cells:
        return
    _, classify, accumulate = kernels(cuda)
    packet = (events.terminal_kind, events.terminal_receiver, events.terminal_row, events.terminal_column)
    classify[(workspace.count + 127) // 128, 128](workspace.count, workspace.tile_width, *packet,
        events.terminal_received_power, session.device_receiver_columns, session.device_grid_offsets,
        workspace.counts, workspace.minimum_bits, workspace.maximum_bits, session.chunk_counts)
    accumulate[(workspace.cells + 3) // 4, 128](workspace.count, workspace.tile_width, workspace.tile_count,
        *packet, events.terminal_received_power, session.device_receiver_columns, session.device_grid_offsets,
        workspace.counts, workspace.initial_flux, workspace.initial_squared,
        workspace.minimum_bits, workspace.maximum_bits,
        session.device_state['grid_flux_lumen'], session.device_state['grid_flux_squared_lumen2'], session.chunk_counts)
