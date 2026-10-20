from __future__ import annotations

import ast
from contextlib import contextmanager
import importlib
import inspect
import math
import struct
from unittest.mock import patch

import numpy as np

from leakage_simulator import gpu_cuda_receiver_flux as flux
from leakage_simulator import gpu_cuda_emitted_flux as emissions
from leakage_simulator import gpu_cuda_receiver_moments as moments
from leakage_simulator import native_cpu_ordered_reducer as reducer

BLOCK_WIDTH = 128
TILE_WIDTH = 2048
_CANDIDATE_KERNELS = None
_EMISSION_KERNEL = None


def float_bits(value):
    return struct.unpack('<Q', struct.pack('<d', value))[0]


def bits_float(value):
    return struct.unpack('<d', struct.pack('<Q', value))[0]


def compose_maps(first, second):
    return (first[0] + second[first[0] & 1],
        first[1] + second[(1 + first[1]) & 1])


def addition_map(initial, value):
    encoded = float_bits(initial)
    exponent = (encoded >> 52) & 2047
    if not (initial > 0.0 and exponent not in (0, 2047)
            and math.isfinite(value) and value >= 0.0):
        return None
    even = encoded & ~1
    results = tuple(float_bits(bits_float(even + parity) + value) for parity in (0, 1))
    if any(result >> 52 != exponent for result in results):
        return None
    steps = tuple(result - even - parity for parity, result in enumerate(results))
    if any(step < 0 or step > (1 << 50) for step in steps):
        return None
    return steps


def ordered_map_fold(initial, values, valid, *, chunk_width=16, square=False):
    if len(values) > TILE_WIDTH or len(valid) != len(values) or chunk_width < 1:
        raise ValueError('Invalid ordered parity-map packet')
    additions = [float(value) * float(value) if square else float(value)
        for value, selected in zip(values, valid, strict=True) if selected]
    maps = []
    eligible = True
    for start in range(0, len(additions), chunk_width):
        combined = (0, 0)
        for value in additions[start:start + chunk_width]:
            addition = addition_map(initial, value)
            if addition is None:
                eligible = False
                break
            combined = compose_maps(combined, addition)
        maps.append(combined)
        if not eligible:
            break
    if eligible and maps:
        while len(maps) > 1:
            maps = [compose_maps(maps[index], maps[index + 1])
                if index + 1 < len(maps) else maps[index] for index in range(0, len(maps), 2)]
        encoded = float_bits(initial)
        advance = maps[0][encoded & 1]
        if advance >= 0 and (encoded + advance) >> 52 == encoded >> 52:
            return bits_float(encoded + advance), True
    elif eligible:
        return initial, True
    total = float(initial)
    for value in additions:
        total += value
    return total, False


def make_block_ordered_fold(cuda, *, block_width=BLOCK_WIDTH):
    if block_width not in (128, 256):
        raise ValueError('Ordered parity-map blocks must contain 128 or 256 threads')
    libdevice = importlib.import_module('numba.cuda.libdevice')
    warp_fold = moments.make_warp_ordered_fold(cuda)

    @cuda.jit(device=True, inline=True, fastmath=False)
    def quantize(even, exponent, value):
        if exponent == 0 or exponent >= 2047 or not math.isfinite(value) or value < 0.0:
            return np.int64(0), np.int64(0), False
        even_result = libdevice.double_as_longlong(libdevice.dadd_rn(
            libdevice.longlong_as_double(even), value))
        odd_result = libdevice.double_as_longlong(libdevice.dadd_rn(
            libdevice.longlong_as_double(even + np.int64(1)), value))
        if even_result >> 52 != exponent or odd_result >> 52 != exponent:
            return np.int64(0), np.int64(0), False
        even_step = even_result - even
        odd_step = odd_result - even - np.int64(1)
        if even_step < 0 or odd_step < 0 or even_step > (1 << 50) or odd_step > (1 << 50):
            return np.int64(0), np.int64(0), False
        return even_step, odd_step, True

    @cuda.jit(device=True, fastmath=False)
    def fold(initial, values, valid, count, square):
        thread = cuda.threadIdx.x
        transforms = cuda.shared.array((2, block_width), dtype=np.int64)
        failures = cuda.shared.array(block_width, dtype=np.int32)
        result = cuda.shared.array(1, dtype=np.float64)
        accelerated = cuda.shared.array(1, dtype=np.int32)
        encoded = libdevice.double_as_longlong(initial)
        exponent = (encoded >> 52) & 2047
        anchor = encoded & np.int64(-2)
        even_advance = np.int64(0)
        odd_advance = np.int64(0)
        failed = int(not (initial > 0.0 and exponent > 0 and exponent < 2047))
        start = thread * (TILE_WIDTH // block_width)
        stop = start + TILE_WIDTH // block_width
        if stop > count:
            stop = count
        for ordinal in range(start, stop):
            if valid[ordinal] != 0:
                value = values[ordinal]
                if square:
                    value = libdevice.dmul_rn(value, value)
                even_step, odd_step, accepted = quantize(anchor, exponent, value)
                if not accepted:
                    failed = 1
                even_advance += odd_step if even_advance & 1 else even_step
                odd_advance += odd_step if (np.int64(1) + odd_advance) & 1 else even_step
        transforms[0, thread] = even_advance
        transforms[1, thread] = odd_advance
        failures[thread] = failed
        cuda.syncthreads()
        distance = 1
        while distance < block_width:
            if thread % (distance * 2) == 0:
                first_even = transforms[0, thread]
                first_odd = transforms[1, thread]
                peer = thread + distance
                transforms[0, thread] = first_even + transforms[first_even & 1, peer]
                transforms[1, thread] = first_odd + transforms[(np.int64(1) + first_odd) & 1, peer]
                failures[thread] |= failures[peer]
            cuda.syncthreads()
            distance *= 2
        if thread == 0:
            advance = transforms[encoded & 1, 0]
            accepted = failures[0] == 0 and advance >= 0 and (encoded + advance) >> 52 == exponent
            accelerated[0] = int(accepted)
            result[0] = libdevice.longlong_as_double(encoded + advance) if accepted else initial
        cuda.syncthreads()
        if accelerated[0] == 0 and thread < 32:
            total = initial
            for batch in range(0, count, 32):
                ordinal = batch + thread
                selected = ordinal < count and valid[ordinal] != 0
                value = values[ordinal] if selected else 0.0
                if square:
                    value = libdevice.dmul_rn(value, value)
                total, _ = warp_fold(total, value, selected)
            if thread == 0:
                result[0] = total
        cuda.syncthreads()
        return result[0], accelerated[0] != 0

    return fold


def make_accumulator(cuda, matches, group_has_hits, write_totals, repeat_identical):
    block_fold = make_block_ordered_fold(cuda)
    libdevice = importlib.import_module('numba.cuda.libdevice')

    @cuda.jit(fastmath=False, lineinfo=True)
    def accumulate(count, receiver_count, depth_count, tile_width, tile_count, kinds, receivers,
                   depths, lobes, received, counts, powers, varying, receiver_flux,
                   receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                   reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux,
                   contribution_depth_flux, squared_totals, receiver_counts, receiver_depth_counts,
                   contribution_receiver_counts, reflection_lobe_counts, contribution_depth_counts,
                   chunk_counts):
        group = cuda.blockIdx.x
        thread = cuda.threadIdx.x
        if chunk_counts[0] == 0 or (chunk_counts.size > 4 and chunk_counts[4] != 0):
            return
        if group >= counts.shape[0] or varying[group, 0] == 2:
            return
        if not group_has_hits(group, receiver_count, depth_count, receiver_counts, receiver_depth_counts,
                              contribution_receiver_counts, reflection_lobe_counts, contribution_depth_counts):
            return
        stride = reducer.RECEIVER_FIELD_SIZE + depth_count
        local = group < receiver_count * stride
        receiver = group // stride
        field = group - receiver * stride if local else group - receiver_count * stride
        with_square = local and field == reducer.RECEIVER_TOTAL
        totals = cuda.shared.array(3, dtype=np.float64)
        values = cuda.shared.array(TILE_WIDTH, dtype=np.float64)
        valid = cuda.shared.array(TILE_WIDTH, dtype=np.uint8)
        same_companion = cuda.shared.array(1, dtype=np.uint8)
        if thread == 0:
            total = 0.0
            companion = 0.0
            if local:
                if field < reducer.RECEIVER_FIELD_SIZE:
                    total = receiver_flux[receiver, field]
                else:
                    total = receiver_depth_flux[receiver, field - reducer.RECEIVER_FIELD_SIZE]
            elif field < 2:
                total = reflection_flux[field]
                companion = contribution_receiver_flux[field]
            elif field < flux.GLOBAL_DEPTH_OFFSET:
                total = reflection_lobe_flux[field - 2, 1]
                companion = contribution_lobe_flux[field - 2, reducer.OUTCOME_RECEIVER]
            else:
                depth = field - flux.GLOBAL_DEPTH_OFFSET
                if depth > 0:
                    total = reflection_depth_flux[depth, 1]
                companion = contribution_depth_flux[depth, reducer.CONTRIBUTION_DEPTH_RECEIVER]
            totals[0] = total
            totals[1] = squared_totals[receiver] if with_square else 0.0
            totals[2] = companion
        cuda.syncthreads()
        for start in range(0, count, TILE_WIDTH):
            available = count - start
            if available > TILE_WIDTH:
                available = TILE_WIDTH
            for ordinal in range(thread, available, BLOCK_WIDTH):
                primary = start + ordinal
                selected = matches(group, receiver_count, depth_count, primary, kinds, receivers, depths, lobes)
                valid[ordinal] = int(selected)
                values[ordinal] = received[primary] if selected else 0.0
            cuda.syncthreads()
            initial = totals[0]
            total, _ = block_fold(initial, values, valid, available, False)
            if thread == 0:
                totals[0] = total
            cuda.syncthreads()
            if with_square:
                squared, _ = block_fold(totals[1], values, valid, available, True)
                if thread == 0:
                    totals[1] = squared
                cuda.syncthreads()
            if not local:
                if thread == 0:
                    same_companion[0] = int(libdevice.double_as_longlong(initial)
                        == libdevice.double_as_longlong(totals[2]))
                cuda.syncthreads()
                if same_companion[0] != 0:
                    if thread == 0:
                        totals[2] = total
                else:
                    companion, _ = block_fold(totals[2], values, valid, available, False)
                    if thread == 0:
                        totals[2] = companion
                cuda.syncthreads()
        if thread == 0:
            if with_square:
                squared_totals[receiver] = totals[1]
            write_totals(group, receiver_count, depth_count, totals[0], totals[2], receiver_flux,
                receiver_depth_flux, reflection_flux, contribution_receiver_flux,
                reflection_lobe_flux, contribution_lobe_flux, reflection_depth_flux, contribution_depth_flux)

    return accumulate


class BlockLaunch:
    def __init__(self, kernel):
        self.kernel = kernel

    def __getattr__(self, name):
        return getattr(self.kernel, name)

    def __getitem__(self, configuration):
        def execute(*arguments):
            return self.kernel[arguments[10].shape[0], BLOCK_WIDTH](*arguments)
        return execute


def make_receiver_kernels(cuda):
    tree = ast.parse(inspect.getsource(flux.make_kernels))
    definition = tree.body[0]
    accumulators = [index for index, node in enumerate(definition.body)
        if isinstance(node, ast.FunctionDef) and node.name == 'accumulate']
    if len(accumulators) != 1:
        raise RuntimeError('The ordered Receiver kernel structure changed')
    definition.body[accumulators[0]] = ast.parse('accumulate = _candidate_accumulator('
        'cuda, matches, group_has_hits, write_totals, repeat_identical)').body[0]
    namespace = dict(vars(flux), _candidate_accumulator=make_accumulator)
    exec(compile(ast.fix_missing_locations(tree), '<perf5-parity-map-receiver>', 'exec'), namespace)
    classify, accumulate, aliases = namespace['make_kernels'](cuda)
    return classify, BlockLaunch(accumulate), aliases


@contextmanager
def receiver_parity_maps(cuda):
    global _CANDIDATE_KERNELS
    if _CANDIDATE_KERNELS is None:
        _CANDIDATE_KERNELS = make_receiver_kernels(cuda)
    with patch.object(flux, '_KERNELS', _CANDIDATE_KERNELS):
        yield


EMISSION_TILES = '''
values = cuda.shared.array(TILE_WIDTH, dtype=np.float64)
selected = cuda.shared.array(TILE_WIDTH, dtype=np.uint8)
equal_initial = cuda.shared.array(1, dtype=np.uint8)
stream_size = count * records if is_lobe else count
for start in range(0, stream_size, TILE_WIDTH):
    available = stream_size - start
    if available > TILE_WIDTH:
        available = TILE_WIDTH
    for offset in range(thread, available, 256):
        accepted, value = packet(group, start + offset, records, kinds, depths, status, lobes, emitted)
        selected[offset] = int(accepted)
        values[offset] = value if accepted else 0.0
    if thread == 0:
        equal_initial[0] = int(libdevice.double_as_longlong(totals[0])
            == libdevice.double_as_longlong(totals[1]))
    cuda.syncthreads()
    first, fast_first = block_fold(totals[0], values, selected, available, False)
    if equal_initial[0] != 0:
        second = first
        fast_second = fast_first
    else:
        second, fast_second = block_fold(totals[1], values, selected, available, False)
    if thread == 0:
        totals[0] = first
        totals[1] = second
        if fast_first and fast_second:
            modes[group, 1] += 1
        else:
            modes[group, 2] += 1
    cuda.syncthreads()
'''


def make_emission_kernel(cuda):
    tree = ast.parse(inspect.getsource(emissions.make_kernel))
    definition = tree.body[0]
    kernels = [node for node in definition.body if isinstance(node, ast.FunctionDef)
        and node.name == 'accumulate']
    if len(kernels) != 1:
        raise RuntimeError('The ordered emission kernel structure changed')
    kernel = kernels[0]
    starts = [index for index, node in enumerate(kernel.body) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'steps' for target in node.targets)]
    stops = [index for index, node in enumerate(kernel.body) if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name) and node.target.id == 'start']
    if len(starts) != 1 or len(stops) != 1 or starts[0] >= stops[0]:
        raise RuntimeError('The ordered emission tile boundaries changed')
    kernel.body[starts[0]:stops[0] + 1] = ast.parse(EMISSION_TILES).body
    definition.body.insert(definition.body.index(kernel), ast.parse(
        'block_fold = _make_block_ordered_fold(cuda, block_width=256)').body[0])
    namespace = dict(vars(emissions), _make_block_ordered_fold=make_block_ordered_fold)
    exec(compile(ast.fix_missing_locations(tree), '<perf5-parity-map-emission>', 'exec'), namespace)
    return namespace['make_kernel'](cuda)


@contextmanager
def emission_parity_maps(cuda):
    global _EMISSION_KERNEL
    if _EMISSION_KERNEL is None:
        _EMISSION_KERNEL = make_emission_kernel(cuda)
    with patch.object(emissions, '_KERNEL', _EMISSION_KERNEL):
        yield
