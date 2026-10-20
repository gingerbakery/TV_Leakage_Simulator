from __future__ import annotations

from dataclasses import dataclass
import importlib
import threading
from typing import Any

import numpy as np

from . import gpu_cuda_receiver_moments as moments
from . import native_cpu_ordered_reducer as reducer

CONTRACT = 'device_primary_ordered_receiver_flux_moments_v1'
GLOBAL_DEPTH_OFFSET = 2 + reducer.LOBE_COUNT
_LOCK = threading.Lock()
_KERNELS = None
_UNIFORM_KERNELS = None


@dataclass(slots=True)
class ReceiverFluxWorkspace:
    receiver_count: int
    depth_count: int
    tile_width: int
    tile_count: int
    counts: Any
    powers: Any
    varying: Any

    @property
    def nbytes(self):
        return sum(values.nbytes for values in (self.counts, self.powers, self.varying))


def workspace_layout(count, receivers, depths):
    if count < 0 or receivers < 0 or depths < 1:
        raise ValueError('Receiver flux workspace dimensions are invalid')
    groups = receivers * (reducer.RECEIVER_FIELD_SIZE + depths) + GLOBAL_DEPTH_OFFSET + depths
    width = 128
    limit = max(moments.METADATA_SLOT_LIMIT, groups)
    while groups * ((count + width - 1) // width) > limit:
        width *= 2
    return groups, width, (count + width - 1) // width


def ensure_workspace(cuda, previous, count, receivers, depths):
    groups, width, tiles = workspace_layout(count, receivers, depths)
    if previous is not None and previous.receiver_count == receivers and previous.depth_count == depths and previous.tile_width == width and previous.tile_count >= tiles:
        return previous
    shape = (groups, tiles)
    return ReceiverFluxWorkspace(
        receivers, depths, width, tiles,
        cuda.device_array(shape, dtype=np.int64),
        cuda.device_array(shape, dtype=np.float64),
        cuda.device_array(shape, dtype=np.uint8),
    )


def make_kernels(cuda, *, scalar_only=False):
    repeat_identical = moments.make_kernels(cuda)[2]
    if scalar_only:
        @cuda.jit(device=True, inline=True, fastmath=False)
        def fold(total, value, valid):
            lane = cuda.threadIdx.x & 31
            for source_lane in range(32):
                selected = cuda.shfl_sync(0xffffffff, int(valid), source_lane)
                addition = cuda.shfl_sync(0xffffffff, value, source_lane)
                if lane == 0 and selected:
                    total = total + addition
            return total, False
    else:
        fold = moments.make_warp_ordered_fold(cuda)
    libdevice = importlib.import_module('numba.cuda.libdevice')

    @cuda.jit(device=True, inline=True)
    def same_bits(first, second):
        return libdevice.double_as_longlong(first) == libdevice.double_as_longlong(second)

    @cuda.jit(device=True, inline=True)
    def local_source(group, depth_count, receiver_counts, receiver_depth_counts, receiver_flux, receiver_depth_flux):
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        receiver = group // stride
        field = group - receiver * stride
        if field == reducer.RECEIVER_TOTAL:
            return group
        if field < reducer.RECEIVER_FIELD_SIZE:
            hits = receiver_counts[receiver, field]
            initial = receiver_flux[receiver, field]
        else:
            hits = receiver_depth_counts[receiver, field - reducer.RECEIVER_FIELD_SIZE]
            initial = receiver_depth_flux[receiver, field - reducer.RECEIVER_FIELD_SIZE]
        if hits == receiver_counts[receiver, reducer.RECEIVER_TOTAL] and same_bits(initial, receiver_flux[receiver, reducer.RECEIVER_TOTAL]):
            return receiver * stride + reducer.RECEIVER_TOTAL
        return group

    @cuda.jit(device=True, inline=True)
    def alias_source(group, receiver_count, depth_count, receiver_counts, receiver_depth_counts,
                     receiver_flux, receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                     reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux):
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        if group < receiver_count * stride:
            return local_source(group, depth_count, receiver_counts, receiver_depth_counts, receiver_flux, receiver_depth_flux)
        if receiver_count != 1:
            return group
        field = group - stride
        if field < 2:
            source = field
            initial = reflection_flux[field]
            companion = contribution_receiver_flux[field]
        elif field < GLOBAL_DEPTH_OFFSET:
            source = reducer.RECEIVER_SPECULAR + field - 2
            initial = reflection_lobe_flux[field - 2, 1]
            companion = contribution_lobe_flux[field - 2, reducer.OUTCOME_RECEIVER]
        else:
            depth = field - GLOBAL_DEPTH_OFFSET
            source = reducer.RECEIVER_FIELD_SIZE + depth
            initial = reflection_depth_flux[depth, 1]
            companion = contribution_depth_flux[depth, reducer.CONTRIBUTION_DEPTH_RECEIVER]
        source = local_source(source, depth_count, receiver_counts, receiver_depth_counts, receiver_flux, receiver_depth_flux)
        if source < reducer.RECEIVER_FIELD_SIZE:
            source_initial = receiver_flux[0, source]
        else:
            source_initial = receiver_depth_flux[0, source - reducer.RECEIVER_FIELD_SIZE]
        if not same_bits(companion, source_initial):
            return group
        if field == GLOBAL_DEPTH_OFFSET or same_bits(initial, source_initial):
            return source
        return group

    @cuda.jit(device=True, inline=True)
    def write_totals(group, receiver_count, depth_count, total, companion, receiver_flux,
                     receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                     reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux):
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        if group < receiver_count * stride:
            receiver = group // stride
            field = group - receiver * stride
            if field < reducer.RECEIVER_FIELD_SIZE:
                receiver_flux[receiver, field] = total
            else:
                receiver_depth_flux[receiver, field - reducer.RECEIVER_FIELD_SIZE] = total
        else:
            field = group - receiver_count * stride
            if field < 2:
                reflection_flux[field] = total
                contribution_receiver_flux[field] = companion
            elif field < GLOBAL_DEPTH_OFFSET:
                reflection_lobe_flux[field - 2, 1] = total
                contribution_lobe_flux[field - 2, reducer.OUTCOME_RECEIVER] = companion
            else:
                depth = field - GLOBAL_DEPTH_OFFSET
                if depth > 0:
                    reflection_depth_flux[depth, 1] = total
                contribution_depth_flux[depth, reducer.CONTRIBUTION_DEPTH_RECEIVER] = companion

    @cuda.jit(device=True, inline=True)
    def group_has_hits(group, receiver_count, depth_count, receiver_counts, receiver_depth_counts,
                       contribution_receiver_counts, reflection_lobe_counts, contribution_depth_counts):
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        if group < receiver_count * stride:
            receiver = group // stride
            field = group - receiver * stride
            if field < reducer.RECEIVER_FIELD_SIZE:
                return receiver_counts[receiver, field] > 0
            return receiver_depth_counts[receiver, field - reducer.RECEIVER_FIELD_SIZE] > 0
        field = group - receiver_count * stride
        if field < 2:
            return contribution_receiver_counts[field] > 0
        if field < GLOBAL_DEPTH_OFFSET:
            return reflection_lobe_counts[field - 2, reducer.OUTCOME_RECEIVER] > 0
        return contribution_depth_counts[field - GLOBAL_DEPTH_OFFSET, reducer.CONTRIBUTION_DEPTH_RECEIVER] > 0

    @cuda.jit(device=True, inline=True)
    def matches(group, receiver_count, depth_count, primary, kinds, receivers, depths, lobes):
        if kinds[primary] != reducer.TERMINAL_RECEIVER:
            return False
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        depth = depths[primary]
        if group < receiver_count * stride:
            receiver = group // stride
            field = group - receiver * stride
            if receivers[primary] != receiver:
                return False
            if field == reducer.RECEIVER_TOTAL:
                return True
            if field == reducer.RECEIVER_DIRECT:
                return depth == 0
            if field == reducer.RECEIVER_REFLECTED:
                return depth > 0
            if field < reducer.RECEIVER_FIELD_SIZE:
                return depth > 0 and lobes[primary, depth - 1] == field - reducer.RECEIVER_SPECULAR
            return depth == field - reducer.RECEIVER_FIELD_SIZE
        field = group - receiver_count * stride
        if field == 0:
            return depth == 0
        if field == 1:
            return depth > 0
        if field < GLOBAL_DEPTH_OFFSET:
            return depth > 0 and lobes[primary, depth - 1] == field - 2
        return depth == field - GLOBAL_DEPTH_OFFSET

    @cuda.jit(fastmath=False)
    def classify(count, receiver_count, depth_count, tile_width, tile_count, kinds, receivers,
                 depths, lobes, received, counts, powers, varying, receiver_counts,
                 receiver_depth_counts, contribution_receiver_counts, reflection_lobe_counts,
                 contribution_depth_counts, receiver_flux, receiver_depth_flux, reflection_flux,
                 contribution_receiver_flux, reflection_lobe_flux, contribution_lobe_flux,
                 reflection_depth_flux, contribution_depth_flux, chunk_counts):
        if chunk_counts[0] == 0 or (chunk_counts.size > 4 and chunk_counts[4] != 0):
            return
        slot = cuda.grid(1)
        group = slot // tile_count
        tile = slot - group * tile_count
        if group >= counts.shape[0]:
            return
        hits = 0
        power = 0.0
        different = False
        if group_has_hits(group, receiver_count, depth_count, receiver_counts, receiver_depth_counts,
                          contribution_receiver_counts, reflection_lobe_counts, contribution_depth_counts):
            source = alias_source(group, receiver_count, depth_count, receiver_counts, receiver_depth_counts,
                                  receiver_flux, receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                                  reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux)
            if source != group:
                counts[group, tile] = source
                varying[group, tile] = 2
                return
            start = tile * tile_width
            stop = start + tile_width
            if stop > count:
                stop = count
            for primary in range(start, stop):
                if not matches(group, receiver_count, depth_count, primary, kinds, receivers, depths, lobes):
                    continue
                value = received[primary]
                if hits == 0:
                    power = value
                elif value != power:
                    different = True
                hits += 1
        counts[group, tile] = hits
        powers[group, tile] = power
        varying[group, tile] = int(different)

    @cuda.jit(fastmath=False)
    def accumulate(count, receiver_count, depth_count, tile_width, tile_count, kinds, receivers,
                   depths, lobes, received, counts, powers, varying, receiver_flux,
                   receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                   reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux,
                   contribution_depth_flux, squared_totals, receiver_counts, receiver_depth_counts,
                   contribution_receiver_counts, reflection_lobe_counts, contribution_depth_counts,
                   chunk_counts):
        if chunk_counts[0] == 0 or (chunk_counts.size > 4 and chunk_counts[4] != 0):
            return
        thread = cuda.grid(1)
        group = thread // 32
        lane = thread & 31
        if group >= counts.shape[0]:
            return
        if varying[group, 0] == 2:
            return
        if not group_has_hits(group, receiver_count, depth_count, receiver_counts, receiver_depth_counts,
                              contribution_receiver_counts, reflection_lobe_counts, contribution_depth_counts):
            return
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        local = group < receiver_count * stride
        receiver = group // stride
        field = group - receiver * stride if local else group - receiver_count * stride
        total = 0.0
        companion = 0.0
        squared_total = 0.0
        with_square = local and field == reducer.RECEIVER_TOTAL
        if with_square:
            squared_total = squared_totals[receiver]
        if local:
            if field < reducer.RECEIVER_FIELD_SIZE:
                total = receiver_flux[receiver, field]
            else:
                total = receiver_depth_flux[receiver, field - reducer.RECEIVER_FIELD_SIZE]
        elif field < 2:
            total = reflection_flux[field]
            companion = contribution_receiver_flux[field]
        elif field < GLOBAL_DEPTH_OFFSET:
            total = reflection_lobe_flux[field - 2, 1]
            companion = contribution_lobe_flux[field - 2, reducer.OUTCOME_RECEIVER]
        else:
            depth = field - GLOBAL_DEPTH_OFFSET
            if depth > 0:
                total = reflection_depth_flux[depth, 1]
            companion = contribution_depth_flux[depth, reducer.CONTRIBUTION_DEPTH_RECEIVER]
        for tile in range(tile_count):
            if varying[group, tile] == 0:
                if lane == 0:
                    total = repeat_identical(total, powers[group, tile], counts[group, tile])
                    if with_square:
                        square = libdevice.dmul_rn(powers[group, tile], powers[group, tile])
                        squared_total = repeat_identical(squared_total, square, counts[group, tile])
                    if not local:
                        companion = repeat_identical(companion, powers[group, tile], counts[group, tile])
            else:
                start = tile * tile_width
                stop = start + tile_width
                if stop > count:
                    stop = count
                for batch in range(start, stop, 32):
                    primary = batch + lane
                    valid = 0
                    value = 0.0
                    square = 0.0
                    if primary < stop and matches(group, receiver_count, depth_count, primary, kinds, receivers, depths, lobes):
                        valid = 1
                        value = received[primary]
                        if with_square:
                            square = libdevice.dmul_rn(value, value)
                    total, _ = fold(total, value, valid != 0)
                    if with_square:
                        squared_total, _ = fold(squared_total, square, valid != 0)
                    if not local:
                        companion, _ = fold(companion, value, valid != 0)
        if lane != 0:
            return
        if with_square:
            squared_totals[receiver] = squared_total
        write_totals(group, receiver_count, depth_count, total, companion, receiver_flux,
                     receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                     reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux)

    @cuda.jit(fastmath=False)
    def copy_aliases(receiver_count, depth_count, counts, varying, receiver_flux, receiver_depth_flux,
                     reflection_flux, contribution_receiver_flux, reflection_lobe_flux,
                     contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux, chunk_counts):
        if chunk_counts[0] == 0 or (chunk_counts.size > 4 and chunk_counts[4] != 0):
            return
        group = cuda.grid(1)
        if group >= counts.shape[0] or varying[group, 0] != 2:
            return
        source = counts[group, 0]
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        receiver = source // stride
        field = source - receiver * stride
        if field < reducer.RECEIVER_FIELD_SIZE:
            value = receiver_flux[receiver, field]
        else:
            value = receiver_depth_flux[receiver, field - reducer.RECEIVER_FIELD_SIZE]
        write_totals(group, receiver_count, depth_count, value, value, receiver_flux,
                     receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                     reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux)

    return classify, accumulate, copy_aliases


def accumulate_device_ordered_flux(cuda, workspace, events, state, chunk_counts):
    global _KERNELS, _UNIFORM_KERNELS
    with _LOCK:
        if getattr(events, 'receiver_power_uniform', False):
            if _UNIFORM_KERNELS is None:
                _UNIFORM_KERNELS = make_kernels(cuda, scalar_only=True)
            classify, accumulate, copy_aliases = _UNIFORM_KERNELS
        else:
            if _KERNELS is None:
                _KERNELS = make_kernels(cuda)
            classify, accumulate, copy_aliases = _KERNELS
    count = events.ray_count
    groups, _, tiles = workspace_layout(count, workspace.receiver_count, workspace.depth_count)
    if not count:
        return
    common = (
        count, workspace.receiver_count, workspace.depth_count, workspace.tile_width, tiles,
        events.terminal_kind, events.terminal_receiver, events.terminal_depth, events.event_lobes,
        events.terminal_received_power, workspace.counts, workspace.powers, workspace.varying,
    )
    counters = tuple(state[name] for name in (
        'receiver_counts', 'receiver_depth_counts', 'contribution_receiver_counts',
        'reflection_lobe_counts', 'contribution_depth_counts',
    ))
    flux = tuple(state[name] for name in (
        'receiver_flux_lumen', 'receiver_depth_flux_lumen', 'reflection_flux_lumen',
        'contribution_receiver_flux_lumen', 'reflection_lobe_flux_lumen',
        'contribution_lobe_flux_lumen', 'reflection_depth_flux_lumen', 'contribution_depth_flux_lumen',
    ))
    classify[(groups * tiles + 127) // 128, 128](*common, *counters, *flux, chunk_counts)
    accumulate[(groups + 3) // 4, 128](*common, *flux, state['grid_flux_squared_totals_lumen2'], *counters, chunk_counts)
    copy_aliases[(groups + 127) // 128, 128](workspace.receiver_count, workspace.depth_count,
        workspace.counts, workspace.varying, *flux, chunk_counts)
