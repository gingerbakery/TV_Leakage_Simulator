from __future__ import annotations

import ast
from contextlib import contextmanager
import importlib
import inspect
import math
import threading
from unittest.mock import patch

import numpy as np

from leakage_simulator import gpu_cuda_emitted_flux as emissions
from leakage_simulator import native_cpu_ordered_reducer as reducer

_KERNEL = None


def alias_from_metadata(count, records, depths, metadata, reflection_lobe,
                        reflection_depth, contribution_lobe, contribution_depth):
    if len(metadata) != 5 + 3 * (reducer.LOBE_COUNT + depths) or metadata[1] == 0 or metadata[4] != 0:
        return -1, -1
    selected_lobe = -1
    selected_depth = -1
    lobe_hits = 0
    depth_hits = 0
    for group in range(reducer.LOBE_COUNT + depths):
        encoded, varying, hits = (int(value) for value in metadata[5 + group * 3:8 + group * 3])
        if hits < 0 or varying not in (0, 1):
            return -1, -1
        if not hits:
            continue
        if encoded <= 0 or hits > count:
            return -1, -1
        if group < reducer.LOBE_COUNT:
            if selected_lobe >= 0:
                return -1, -1
            selected_lobe, lobe_hits = group, hits
        else:
            depth = group - reducer.LOBE_COUNT
            if selected_depth >= 0 or depth < 1 or depth > records:
                return -1, -1
            selected_depth, depth_hits = depth, hits
    if selected_lobe < 0 or selected_depth < 0 or lobe_hits != depth_hits:
        return -1, -1
    initial = (
        (reflection_lobe[selected_lobe, 0], reflection_depth[selected_depth, 0]),
        (contribution_lobe[selected_lobe, reducer.OUTCOME_EMITTED],
            contribution_depth[selected_depth, reducer.CONTRIBUTION_DEPTH_EMITTED]),
    )
    for first, second in initial:
        if not math.isfinite(first) or first < 0.0 or np.float64(first).view(np.uint64) != np.float64(second).view(np.uint64):
            return -1, -1
    return selected_lobe, selected_depth


def make_alias_stages(cuda):
    libdevice = importlib.import_module('numba.cuda.libdevice')

    @cuda.jit(fastmath=False)
    def classify(count, records, depths, metadata, reflection_lobe,
                 reflection_depth, contribution_lobe, contribution_depth, alias):
        if cuda.grid(1) != 0:
            return
        alias[0] = -1
        alias[1] = -1
        if metadata.size != 5 + 3 * (reducer.LOBE_COUNT + depths) or metadata[1] == 0 or metadata[4] != 0:
            return
        selected_lobe = -1
        selected_depth = -1
        lobe_hits = 0
        depth_hits = 0
        for group in range(reducer.LOBE_COUNT + depths):
            address = 5 + group * 3
            encoded = metadata[address]
            varying = metadata[address + 1]
            hits = metadata[address + 2]
            if hits < 0 or (varying != 0 and varying != 1):
                return
            if hits == 0:
                continue
            if encoded <= 0 or hits > count:
                return
            if group < reducer.LOBE_COUNT:
                if selected_lobe >= 0:
                    return
                selected_lobe = group
                lobe_hits = hits
            else:
                depth = group - reducer.LOBE_COUNT
                if selected_depth >= 0 or depth < 1 or depth > records:
                    return
                selected_depth = depth
                depth_hits = hits
        if selected_lobe < 0 or selected_depth < 0 or lobe_hits != depth_hits:
            return
        first = reflection_lobe[selected_lobe, 0]
        second = reflection_depth[selected_depth, 0]
        if not math.isfinite(first) or first < 0.0 or libdevice.double_as_longlong(first) != libdevice.double_as_longlong(second):
            return
        first = contribution_lobe[selected_lobe, reducer.OUTCOME_EMITTED]
        second = contribution_depth[selected_depth, reducer.CONTRIBUTION_DEPTH_EMITTED]
        if not math.isfinite(first) or first < 0.0 or libdevice.double_as_longlong(first) != libdevice.double_as_longlong(second):
            return
        alias[0] = selected_lobe
        alias[1] = selected_depth

    @cuda.jit(fastmath=False)
    def copy_alias(alias, reflection_lobe, reflection_depth, contribution_lobe, contribution_depth):
        if cuda.grid(1) != 0 or alias[0] < 0:
            return
        lobe = alias[0]
        depth = alias[1]
        reflection_lobe[lobe, 0] = reflection_depth[depth, 0]
        contribution_lobe[lobe, reducer.OUTCOME_EMITTED] = contribution_depth[depth, reducer.CONTRIBUTION_DEPTH_EMITTED]

    return classify, copy_alias


class AliasedEmissionKernel:
    def __init__(self, cuda, original, candidate, classify, copy_alias):
        self.cuda = cuda
        self.original = original
        self.candidate = candidate
        self.classify = classify
        self.copy_alias = copy_alias
        self.scratch = threading.local()

    def __getattr__(self, name):
        return getattr(self.candidate, name)

    def __getitem__(self, configuration):
        if not isinstance(configuration, tuple) or len(configuration) != 2:
            raise ValueError('The diagnostic emission alias supports the default stream only')
        def execute(*arguments):
            if len(arguments) != 15:
                raise ValueError('The ordered emission argument contract changed')
            count, records = arguments[:2]
            depths = arguments[8].shape[0]
            metadata = arguments[13]
            outputs = arguments[9:13]
            self.scratch.last_alias = None
            if metadata.size != 5 + 3 * (reducer.LOBE_COUNT + depths):
                return self.original[configuration](*arguments)
            device = int(self.cuda.current_context().device.id)
            cached = getattr(self.scratch, 'cached', None)
            if cached is None or cached[0] != device:
                cached = (device, self.cuda.device_array(2, dtype=np.int64))
                self.scratch.cached = cached
            alias = cached[1]
            self.scratch.last_alias = alias
            self.classify[1, 32](count, records, depths, metadata, *outputs, alias)
            self.candidate[configuration](*arguments, alias)
            self.copy_alias[1, 32](alias, *outputs)
        return execute


def make_kernel(cuda):
    tree = ast.parse(inspect.getsource(emissions.make_kernel))
    definition = tree.body[0]
    kernels = [node for node in definition.body if isinstance(node, ast.FunctionDef)
        and node.name == 'accumulate']
    if len(kernels) != 1:
        raise RuntimeError('The ordered emission factory structure changed')
    kernel = kernels[0]
    kernel.args.args.append(ast.arg(arg='alias'))
    positions = [index for index, node in enumerate(kernel.body) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'is_lobe' for target in node.targets)]
    if len(positions) != 1:
        raise RuntimeError('The ordered emission group guard changed')
    kernel.body[positions[0]:positions[0]] = ast.parse('if group == alias[0]:\n    return').body
    namespace = dict(vars(emissions))
    exec(compile(ast.fix_missing_locations(tree), '<perf5-identical-emission-stream-alias>', 'exec'), namespace)
    classify, copy_alias = make_alias_stages(cuda)
    return AliasedEmissionKernel(cuda, emissions.make_kernel(cuda),
        namespace['make_kernel'](cuda), classify, copy_alias)


@contextmanager
def emission_aliases(cuda):
    global _KERNEL
    if _KERNEL is None:
        _KERNEL = make_kernel(cuda)
    with patch.object(emissions, '_KERNEL', _KERNEL):
        yield _KERNEL
