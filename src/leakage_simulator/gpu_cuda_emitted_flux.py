from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
import os
import threading
from typing import Any

import numpy as np

from . import gpu_cuda_receiver_moments as moments
from . import native_cpu_ordered_reducer as reducer

CONTRACT = 'device_primary_ordered_emitted_flux_v1'
DEFAULT_MODE = 'ordered'
TILE_WIDTH = 2048
_LOCK = threading.Lock()
_KERNEL = None


def uniform_metadata_words(depths):
    mode = os.environ.get('LEAKAGE_GPU_EMITTED_UNIFORM', 'auto')
    if mode not in ('off', 'auto'):
        raise ValueError('GPU uniform emission metadata must be auto or off')
    if mode == 'off' or os.environ.get('LEAKAGE_GPU_EMITTED_FLUX', DEFAULT_MODE) != 'ordered':
        return 0
    return 3 * (reducer.LOBE_COUNT + depths)


@dataclass(slots=True)
class EmittedFluxWorkspace:
    record_depth: int
    depth_count: int
    modes: Any

    @property
    def nbytes(self):
        return self.modes.nbytes


def workspace_layout(count, records, depths):
    if count < 0 or records < 1 or depths < records:
        raise ValueError('Emitted flux workspace dimensions are invalid')
    return reducer.LOBE_COUNT + depths, TILE_WIDTH, (count * records + TILE_WIDTH - 1) // TILE_WIDTH


def ensure_workspace(cuda, previous, count, records, depths):
    groups, _, _ = workspace_layout(count, records, depths)
    if previous is not None and previous.record_depth == records and previous.depth_count == depths:
        return previous
    return EmittedFluxWorkspace(records, depths, cuda.device_array((groups, 3), dtype=np.int64))


def make_repeat_cycle(cuda):
    libdevice = importlib.import_module('numba.cuda.libdevice')

    @cuda.jit(device=True, inline=True, fastmath=False)
    def repeat_cycle(total, repetitions, cycle_depths, metadata):
        remaining = repetitions
        while remaining > 0:
            previous = total
            for depth in range(1, cycle_depths + 1):
                value = libdevice.longlong_as_double(metadata[5 + 3 * (reducer.LOBE_COUNT + depth)] - np.int64(1))
                total = libdevice.dadd_rn(total, value)
            remaining -= 1
            if remaining == 0 or total == previous or not math.isfinite(total):
                return total
            current_bits = libdevice.double_as_longlong(total)
            exponent = current_bits >> 52
            first_probe = total
            for depth in range(1, cycle_depths + 1):
                value = libdevice.longlong_as_double(metadata[5 + 3 * (reducer.LOBE_COUNT + depth)] - np.int64(1))
                first_probe = libdevice.dadd_rn(first_probe, value)
            first_bits = libdevice.double_as_longlong(first_probe)
            if first_bits >> 52 != exponent:
                continue
            first_step = first_bits - current_bits
            if first_step == 0:
                return total
            second_probe = first_probe
            for depth in range(1, cycle_depths + 1):
                value = libdevice.longlong_as_double(metadata[5 + 3 * (reducer.LOBE_COUNT + depth)] - np.int64(1))
                second_probe = libdevice.dadd_rn(second_probe, value)
            second_bits = libdevice.double_as_longlong(second_probe)
            if second_bits >> 52 != exponent:
                continue
            second_step = second_bits - first_bits
            cycles = 1
            step = first_step
            if first_step != second_step:
                if (first_step & 1) != 1 or (second_step & 1) != 1:
                    continue
                cycles = 2
                step += second_step
            boundary = ((exponent + 1) << 52) - 1
            advance = (boundary - current_bits) // step
            if advance > remaining // cycles:
                advance = remaining // cycles
            total = libdevice.longlong_as_double(current_bits + advance * step)
            remaining -= advance * cycles
        return total

    return repeat_cycle


def make_kernel(cuda):
    libdevice = importlib.import_module('numba.cuda.libdevice')
    repeat_identical = moments.make_kernels(cuda)[2]
    repeat_cycle = make_repeat_cycle(cuda)
    fold = moments.make_warp_ordered_fold(cuda)

    @cuda.jit(device=True, inline=True)
    def packet(group, ordinal, records, terminal_kind, terminal_depth, status, lobes, emitted):
        if group < reducer.LOBE_COUNT:
            primary = ordinal // records
            depth = ordinal - primary * records
        else:
            primary = ordinal
            depth = group - reducer.LOBE_COUNT - 1
        if depth < 0 or depth >= records:
            return False, 0.0
        visited = terminal_depth[primary]
        if terminal_kind[primary] == reducer.TERMINAL_BLOCKED:
            visited += 1
        if depth >= visited or not (status[primary, depth] & reducer.STATUS_EMITTED):
            return False, 0.0
        if group < reducer.LOBE_COUNT and lobes[primary, depth] != group:
            return False, 0.0
        return True, emitted[primary, depth]

    @cuda.jit(device=True, inline=True, fastmath=False)
    def quantize(total, bits, exponent, half_unit, value):
        if total <= 0.0 or exponent == 0 or exponent == 2047 or value < 0.0:
            return 0, False
        following = total + value
        next_bits = libdevice.double_as_longlong(following)
        if next_bits >> 52 != exponent:
            return 0, False
        step = next_bits - bits
        if step > (1 << 50):
            return 0, False
        if exponent > 1:
            residual = value - (following - total)
            if residual == half_unit or residual == -half_unit:
                return 0, False
        return step, True

    @cuda.jit(fastmath=False, lineinfo=True)
    def accumulate(count, records, kinds, depths, status, lobes, emitted, lobe_counts, depth_counts,
                   reflection_lobe, reflection_depth, contribution_lobe, contribution_depth, chunk_counts, modes):
        group = cuda.blockIdx.x
        thread = cuda.threadIdx.x
        if thread < 3:
            modes[group, thread] = 0
        if chunk_counts[1] == 0 or chunk_counts[4] != 0:
            return
        is_lobe = group < reducer.LOBE_COUNT
        depth = group - reducer.LOBE_COUNT
        if is_lobe:
            if lobe_counts[group, reducer.OUTCOME_EMITTED] == 0:
                return
        elif depth_counts[depth, reducer.REF_DEPTH_EMITTED] == 0:
            return
        totals = cuda.shared.array(2, dtype=np.float64)
        if thread == 0:
            if is_lobe:
                totals[0] = reflection_lobe[group, 0]
                totals[1] = contribution_lobe[group, reducer.OUTCOME_EMITTED]
            else:
                totals[0] = reflection_depth[depth, 0]
                totals[1] = contribution_depth[depth, reducer.CONTRIBUTION_DEPTH_EMITTED]
        cuda.syncthreads()
        metadata_words = 3 * (reducer.LOBE_COUNT + depth_counts.shape[0])
        if chunk_counts.shape[0] == 5 + metadata_words:
            address = 5 + group * 3
            encoded_bits = chunk_counts[address]
            different = chunk_counts[address + 1] != 0
            uniform_count = chunk_counts[address + 2]
            if uniform_count == 0:
                return
            stream_limit = count * records if is_lobe else count
            if uniform_count > 0 and uniform_count <= stream_limit and encoded_bits > 0 and not different:
                value = libdevice.longlong_as_double(encoded_bits - np.int64(1))
                first = totals[0]
                second = totals[1]
                if value > 0.0 and math.isfinite(value) and first >= 0.0 and second >= 0.0 and math.isfinite(first) and math.isfinite(second):
                    if thread == 0:
                        first = repeat_identical(first, value, uniform_count)
                        second = repeat_identical(second, value, uniform_count)
                        if is_lobe:
                            reflection_lobe[group, 0] = first
                            contribution_lobe[group, reducer.OUTCOME_EMITTED] = second
                        else:
                            reflection_depth[depth, 0] = first
                            contribution_depth[depth, reducer.CONTRIBUTION_DEPTH_EMITTED] = second
                        modes[group, 0] = 1
                    return
            cycle_length = 0
            cycle_valid = is_lobe
            if cycle_valid:
                for other_lobe in range(reducer.LOBE_COUNT):
                    if other_lobe != group and chunk_counts[5 + other_lobe * 3 + 2] != 0:
                        cycle_valid = False
                if cycle_valid:
                    available_depths = records
                    if available_depths >= depth_counts.shape[0]:
                        available_depths = depth_counts.shape[0] - 1
                    ended = False
                    for next_depth in range(1, available_depths + 1):
                        next_address = 5 + 3 * (reducer.LOBE_COUNT + next_depth)
                        next_count = chunk_counts[next_address + 2]
                        if next_count == 0:
                            ended = True
                            continue
                        next_encoded = chunk_counts[next_address]
                        next_different = chunk_counts[next_address + 1] != 0
                        next_value = libdevice.longlong_as_double(next_encoded - np.int64(1))
                        if ended or next_count != count or next_encoded <= 0 or next_different or next_value <= 0.0 or not math.isfinite(next_value):
                            cycle_valid = False
                            break
                        cycle_length += 1
                    if uniform_count != count * cycle_length:
                        cycle_valid = False
            if cycle_valid and cycle_length > 0:
                first = totals[0]
                second = totals[1]
                if first >= 0.0 and second >= 0.0 and math.isfinite(first) and math.isfinite(second):
                    if thread == 0:
                        first_result = repeat_cycle(first, count, cycle_length, chunk_counts)
                        if libdevice.double_as_longlong(first) == libdevice.double_as_longlong(second):
                            second_result = first_result
                        else:
                            second_result = repeat_cycle(second, count, cycle_length, chunk_counts)
                        reflection_lobe[group, 0] = first_result
                        contribution_lobe[group, reducer.OUTCOME_EMITTED] = second_result
                        modes[group, 0] = 1
                    return
        steps = cuda.shared.array((2, 256), dtype=np.int64)
        counts = cuda.shared.array(256, dtype=np.int64)
        failures = cuda.shared.array(256, dtype=np.int64)
        minimums = cuda.shared.array(256, dtype=np.float64)
        maximums = cuda.shared.array(256, dtype=np.float64)
        stream_size = count * records if is_lobe else count
        for start in range(0, stream_size, TILE_WIDTH):
            stop = start + TILE_WIDTH
            if stop > stream_size:
                stop = stream_size
            count_local = 0
            failed = 0
            first_steps = 0
            second_steps = 0
            minimum = math.inf
            maximum = -math.inf
            first_total = totals[0]
            second_total = totals[1]
            first_bits = libdevice.double_as_longlong(first_total)
            second_bits = libdevice.double_as_longlong(second_total)
            first_exponent = (first_bits >> 52) & 2047
            second_exponent = (second_bits >> 52) & 2047
            same_initial = first_bits == second_bits
            first_half_unit = 0.0
            second_half_unit = 0.0
            if first_exponent > 1 and first_exponent < 2047:
                first_half_unit = libdevice.ldexp(1.0, first_exponent - 1076)
            if same_initial:
                second_half_unit = first_half_unit
            elif second_exponent > 1 and second_exponent < 2047:
                second_half_unit = libdevice.ldexp(1.0, second_exponent - 1076)
            for ordinal in range(start + thread, stop, 256):
                valid, value = packet(group, ordinal, records, kinds, depths, status, lobes, emitted)
                if valid:
                    count_local += 1
                    if value < minimum:
                        minimum = value
                    if value > maximum:
                        maximum = value
                    first_step, first_valid = quantize(first_total, first_bits, first_exponent, first_half_unit, value)
                    second_step = first_step
                    second_valid = first_valid
                    if not same_initial:
                        second_step, second_valid = quantize(second_total, second_bits, second_exponent, second_half_unit, value)
                    first_steps += first_step
                    second_steps += second_step
                    if not first_valid or not second_valid:
                        failed |= 1
                    if not math.isfinite(value):
                        failed |= 2
            steps[0, thread] = first_steps
            steps[1, thread] = second_steps
            counts[thread] = count_local
            failures[thread] = failed
            minimums[thread] = minimum
            maximums[thread] = maximum
            cuda.syncthreads()
            for level in range(8):
                offset = 128 >> level
                if thread < offset:
                    steps[0, thread] += steps[0, thread + offset]
                    steps[1, thread] += steps[1, thread + offset]
                    counts[thread] += counts[thread + offset]
                    failures[thread] |= failures[thread + offset]
                    if minimums[thread + offset] < minimums[thread]:
                        minimums[thread] = minimums[thread + offset]
                    if maximums[thread + offset] > maximums[thread]:
                        maximums[thread] = maximums[thread + offset]
                cuda.syncthreads()
            first_available = (((first_bits >> 52) + 1) << 52) - first_bits
            second_available = (((second_bits >> 52) + 1) << 52) - second_bits
            cuda.syncthreads()
            if counts[0] == 0:
                pass
            elif minimums[0] == maximums[0] and minimums[0] > 0.0 and not (failures[0] & 2) and first_total >= 0.0 and second_total >= 0.0:
                if thread == 0:
                    totals[0] = repeat_identical(totals[0], minimums[0], counts[0])
                    if same_initial:
                        totals[1] = totals[0]
                    else:
                        totals[1] = repeat_identical(totals[1], minimums[0], counts[0])
                    modes[group, 0] += 1
            elif failures[0] == 0 and steps[0, 0] < first_available and steps[1, 0] < second_available:
                if thread == 0:
                    totals[0] = libdevice.longlong_as_double(first_bits + steps[0, 0])
                    totals[1] = libdevice.longlong_as_double(second_bits + steps[1, 0])
                    modes[group, 1] += 1
            else:
                if thread < 32:
                    first = totals[0]
                    second = totals[1]
                    for batch in range(start, stop, 32):
                        ordinal = batch + thread
                        valid = False
                        value = 0.0
                        if ordinal < stop:
                            valid, value = packet(group, ordinal, records, kinds, depths, status, lobes, emitted)
                        first, _ = fold(first, value, valid)
                        if same_initial:
                            second = first
                        else:
                            second, _ = fold(second, value, valid)
                    cuda.syncwarp(0xffffffff)
                    if thread == 0:
                        totals[0] = first
                        totals[1] = second
                        modes[group, 2] += 1
            cuda.syncthreads()
        if thread == 0:
            if is_lobe:
                reflection_lobe[group, 0] = totals[0]
                contribution_lobe[group, reducer.OUTCOME_EMITTED] = totals[1]
            else:
                reflection_depth[depth, 0] = totals[0]
                contribution_depth[depth, reducer.CONTRIBUTION_DEPTH_EMITTED] = totals[1]

    return accumulate


def accumulate_device_ordered_emissions(cuda, workspace, events, state, chunk_counts):
    global _KERNEL
    with _LOCK:
        if _KERNEL is None:
            _KERNEL = make_kernel(cuda)
        kernel = _KERNEL
    if not events.ray_count:
        return
    packets = (events.terminal_kind, events.terminal_depth, events.event_status, events.event_lobes, events.event_emitted_power)
    outputs = tuple(state[name] for name in ('reflection_lobe_flux_lumen', 'reflection_depth_flux_lumen',
        'contribution_lobe_flux_lumen', 'contribution_depth_flux_lumen'))
    kernel[reducer.LOBE_COUNT + workspace.depth_count, 256](events.ray_count, workspace.record_depth, *packets,
        state['reflection_lobe_counts'], state['reflection_depth_counts'], *outputs, chunk_counts, workspace.modes)
