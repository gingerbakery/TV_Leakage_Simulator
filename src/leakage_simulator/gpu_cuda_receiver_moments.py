from __future__ import annotations

from dataclasses import dataclass
import importlib
import threading
from typing import Any

import numpy as np

from .wavefront_event_tape import TERMINAL_RECEIVER

CONTRACT = 'device_primary_ordered_receiver_second_moment_v1'
METADATA_SLOT_LIMIT = 131072
_LOCK = threading.Lock()
_KERNELS = None


@dataclass(slots=True)
class ReceiverMomentWorkspace:
    kinds: Any
    indices: Any
    powers: Any
    capacity: int
    tile_width: int
    tile_count: int
    receiver_count: int
    counts: Any
    squares: Any
    varying: Any

    @property
    def nbytes(self):
        return sum(values.nbytes for values in (self.counts, self.squares, self.varying))


def workspace_layout(count, receivers):
    if count < 0 or receivers < 0 or receivers > METADATA_SLOT_LIMIT:
        raise ValueError('Receiver moment workspace dimensions are invalid')
    width = 128
    while receivers * ((count + width - 1) // width) > METADATA_SLOT_LIMIT:
        width *= 2
    return width, (count + width - 1) // width


def ensure_workspace(cuda, previous, count, receivers, kinds, indices, powers):
    width, tiles = workspace_layout(count, receivers)
    capacity = 1 << (count - 1).bit_length() if count else 0
    if previous is not None and previous.capacity >= capacity and previous.tile_count >= tiles and previous.receiver_count == receivers and previous.tile_width == width:
        previous.kinds, previous.indices, previous.powers = kinds, indices, powers
        return previous
    shape = (receivers, tiles)
    return ReceiverMomentWorkspace(
        kinds, indices, powers, capacity, width, tiles, receivers,
        cuda.device_array(shape, dtype=np.int64),
        cuda.device_array(shape, dtype=np.float64),
        cuda.device_array(shape, dtype=np.uint8),
    )


def make_kernels(cuda):
    libdevice = importlib.import_module('numba.cuda.libdevice')

    @cuda.jit(device=True, inline=True, fastmath=False)
    def repeat_identical(total, addition, count):
        remaining = count
        while remaining > 0:
            previous_bits = libdevice.double_as_longlong(total)
            following = libdevice.dadd_rn(total, addition)
            remaining -= 1
            if remaining == 0 or following == total:
                return following
            current_bits = libdevice.double_as_longlong(following)
            total = following
            if current_bits >> 52 != previous_bits >> 52:
                continue
            probe = libdevice.dadd_rn(total, addition)
            probe_bits = libdevice.double_as_longlong(probe)
            if probe_bits >> 52 != current_bits >> 52:
                continue
            step = probe_bits - current_bits
            if step == 0:
                return total
            boundary = ((current_bits >> 52) + 1) << 52
            available = boundary - 1 - current_bits
            if remaining <= 1024:
                if remaining * step <= available:
                    advance = remaining
                else:
                    advance = available // step
            else:
                advance = available // step
            if advance > remaining:
                advance = remaining
            total = libdevice.longlong_as_double(current_bits + advance * step)
            remaining -= advance
        return total

    @cuda.jit(fastmath=False)
    def classify(count, tile_width, tile_count, receiver_count, kinds, indices, powers, counts, squares, varying, chunk_counts):
        if len(chunk_counts) > 0 and chunk_counts[0] == 0:
            return
        slot = cuda.grid(1)
        receiver = slot // tile_count
        tile = slot - receiver * tile_count
        if receiver >= receiver_count:
            return
        start = tile * tile_width
        stop = start + tile_width
        if stop > count:
            stop = count
        hits = 0
        first_square = 0.0
        different = False
        for primary in range(start, stop):
            address = indices[primary]
            if kinds[primary] != TERMINAL_RECEIVER or address != receiver:
                continue
            power = powers[primary]
            square = power * power
            if hits == 0:
                first_square = square
            elif square != first_square:
                different = True
            hits += 1
        counts[receiver, tile] = hits
        squares[receiver, tile] = first_square
        varying[receiver, tile] = int(different)

    @cuda.jit(fastmath=False)
    def accumulate(count, tile_width, tile_count, kinds, indices, powers, counts, squares, varying, totals, chunk_counts):
        if len(chunk_counts) > 0 and chunk_counts[0] == 0:
            return
        thread = cuda.grid(1)
        receiver = thread // 32
        lane = thread & 31
        if receiver >= len(totals):
            return
        total = totals[receiver]
        for tile in range(tile_count):
            if varying[receiver, tile] == 0:
                square = squares[receiver, tile]
                if lane == 0:
                    total = repeat_identical(total, square, counts[receiver, tile])
            else:
                start = tile * tile_width
                stop = start + tile_width
                if stop > count:
                    stop = count
                for group in range(start, stop, 32):
                    primary = group + lane
                    valid = 0
                    square = 0.0
                    if primary < stop:
                        address = indices[primary]
                        if kinds[primary] == TERMINAL_RECEIVER and address == receiver:
                            valid = 1
                            power = powers[primary]
                            square = power * power
                    for source_lane in range(32):
                        selected = cuda.shfl_sync(0xffffffff, valid, source_lane)
                        addition = cuda.shfl_sync(0xffffffff, square, source_lane)
                        if lane == 0 and selected:
                            total = total + addition
        if lane == 0:
            totals[receiver] = total

    return classify, accumulate, repeat_identical


def make_warp_ordered_fold(cuda):
    libdevice = importlib.import_module('numba.cuda.libdevice')

    @cuda.jit(device=True, inline=True, fastmath=False)
    def fold(total, value, valid):
        lane = cuda.threadIdx.x & 31
        total = cuda.shfl_sync(0xffffffff, total, 0)
        bits = libdevice.double_as_longlong(total)
        exponent = (bits >> 52) & 2047
        if total > 0.0 and exponent > 0 and exponent < 2047:
            addition = value if valid else 0.0
            following = libdevice.dadd_rn(total, addition)
            following_bits = libdevice.double_as_longlong(following)
            allowed = addition >= 0.0 and following_bits >> 52 == exponent
            if cuda.all_sync(0xffffffff, allowed):
                step = following_bits - bits
                residual = addition - (following - total)
                half_unit = 0.0
                if exponent > 1:
                    half_unit = libdevice.ldexp(1.0, exponent - 1076)
                tie = half_unit > 0.0 and (residual == half_unit or residual == -half_unit)
                ties = cuda.ballot_sync(0xffffffff, tie)
                if ties != 0:
                    parity = 0 if tie else step & 1
                    for level in range(5):
                        offset = 1 << level
                        preceding = cuda.shfl_up_sync(0xffffffff, parity, offset)
                        if lane >= offset:
                            parity ^= preceding
                    prior_ties = ties & ((1 << lane) - 1)
                    last_tie = 0
                    if prior_ties != 0:
                        last_tie = 31 - libdevice.clz(np.int32(prior_ties))
                    previous_parity = cuda.shfl_sync(0xffffffff, parity, last_tie)
                    if tie:
                        floor_step = step - int(residual < 0.0)
                        incoming_parity = parity ^ previous_parity if prior_ties != 0 else parity ^ (bits & 1)
                        step = floor_step + ((incoming_parity ^ (floor_step & 1)) & 1)
                for level in range(5):
                    offset = 16 >> level
                    following_step = cuda.shfl_down_sync(0xffffffff, step, offset)
                    if lane < 32 - offset:
                        step += following_step
                advance = cuda.shfl_sync(0xffffffff, step, 0)
                available = ((exponent + 1) << 52) - bits
                if advance < available:
                    return libdevice.longlong_as_double(bits + advance), True
        for source_lane in range(32):
            selected = cuda.shfl_sync(0xffffffff, int(valid), source_lane)
            addition = cuda.shfl_sync(0xffffffff, value, source_lane)
            if lane == 0 and selected:
                total = total + addition
        return cuda.shfl_sync(0xffffffff, total, 0), False

    return fold


def accumulate_device_ordered_totals(cuda, workspace, count, totals, chunk_counts=None):
    global _KERNELS
    with _LOCK:
        if _KERNELS is None:
            _KERNELS = make_kernels(cuda)
        classify, accumulate, _ = _KERNELS
    tiles = (count + workspace.tile_width - 1) // workspace.tile_width
    slots = workspace.receiver_count * tiles
    if not slots:
        return
    if chunk_counts is None:
        chunk_counts = workspace.counts.reshape(-1)[:0]
    classify[(slots + 127) // 128, 128](
        count, workspace.tile_width, tiles, workspace.receiver_count, workspace.kinds, workspace.indices, workspace.powers,
        workspace.counts, workspace.squares, workspace.varying, chunk_counts,
    )
    accumulate[(workspace.receiver_count + 3) // 4, 128](
        count, workspace.tile_width, tiles, workspace.kinds, workspace.indices, workspace.powers,
        workspace.counts, workspace.squares, workspace.varying, totals, chunk_counts,
    )
