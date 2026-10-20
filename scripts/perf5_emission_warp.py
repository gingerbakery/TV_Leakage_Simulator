from __future__ import annotations

import ast
import inspect

from leakage_simulator import gpu_cuda_emitted_flux as flux


REDUCE_WARP = '''
@cuda.jit(device=True, inline=True)
def reduce_warp(first_steps, second_steps, count_local, failed, minimum, maximum, width):
    for level in range(5):
        offset = 16 >> level
        if offset < width:
            first_steps += cuda.shfl_down_sync(0xffffffff, first_steps, offset)
            second_steps += cuda.shfl_down_sync(0xffffffff, second_steps, offset)
            count_local += cuda.shfl_down_sync(0xffffffff, count_local, offset)
            failed |= cuda.shfl_down_sync(0xffffffff, failed, offset)
            peer_minimum = cuda.shfl_down_sync(0xffffffff, minimum, offset)
            peer_maximum = cuda.shfl_down_sync(0xffffffff, maximum, offset)
            if peer_minimum < minimum:
                minimum = peer_minimum
            if peer_maximum > maximum:
                maximum = peer_maximum
    return first_steps, second_steps, count_local, failed, minimum, maximum
'''

REDUCE_BLOCK = '''
first_steps, second_steps, count_local, failed, minimum, maximum = reduce_warp(
    first_steps, second_steps, count_local, failed, minimum, maximum, 32)
warp = thread >> 5
lane = thread & 31
if lane == 0:
    steps[0, warp] = first_steps
    steps[1, warp] = second_steps
    counts[warp] = count_local
    failures[warp] = failed
    minimums[warp] = minimum
    maximums[warp] = maximum
cuda.syncthreads()
if thread < 32:
    first_steps = 0
    second_steps = 0
    count_local = 0
    failed = 0
    minimum = math.inf
    maximum = -math.inf
    if lane < 8:
        first_steps = steps[0, lane]
        second_steps = steps[1, lane]
        count_local = counts[lane]
        failed = failures[lane]
        minimum = minimums[lane]
        maximum = maximums[lane]
    first_steps, second_steps, count_local, failed, minimum, maximum = reduce_warp(
        first_steps, second_steps, count_local, failed, minimum, maximum, 8)
    cuda.syncwarp(0xffffffff)
    if thread == 0:
        steps[0, 0] = first_steps
        steps[1, 0] = second_steps
        counts[0] = count_local
        failures[0] = failed
        minimums[0] = minimum
        maximums[0] = maximum
'''


def make_factory():
    tree = ast.parse(inspect.getsource(flux.make_kernel))
    definition = tree.body[0]
    kernels = [node for node in definition.body
        if isinstance(node, ast.FunctionDef) and node.name == 'accumulate']
    if len(kernels) != 1:
        raise RuntimeError('Expected one ordered emitted-flux kernel')
    kernel = kernels[0]
    shared = 0
    for node in kernel.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and ast.unparse(node.value.func) == 'cuda.shared.array'):
            shape = node.value.args[0]
            for constant in ast.walk(shape):
                if isinstance(constant, ast.Constant) and constant.value == 256:
                    constant.value = 8
                    shared += 1
    if shared != 5:
        raise RuntimeError(f'Expected five cooperative scratch arrays, found {shared}')
    loops = [node for node in kernel.body if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name) and node.target.id == 'start']
    if len(loops) != 1:
        raise RuntimeError('Expected one tile loop')
    body = loops[0].body
    starts = [index for index, node in enumerate(body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'steps[0, thread]']
    ends = [index for index, node in enumerate(body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'first_available']
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError('Cooperative reduction boundaries changed')
    body[starts[0]:ends[0]] = ast.parse(REDUCE_BLOCK).body
    definition.body.insert(definition.body.index(kernel), ast.parse(REDUCE_WARP).body[0])
    namespace = dict(vars(flux))
    exec(compile(ast.fix_missing_locations(tree), '<perf5_warp_emission_factory>', 'exec'), namespace)
    return namespace['make_kernel']
