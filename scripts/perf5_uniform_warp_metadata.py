from __future__ import annotations

import ast
from contextlib import contextmanager
import inspect
from unittest.mock import patch

from leakage_simulator import gpu_cuda_summary_accumulator as summary


HELPER = '''
@cuda.jit(device=True, inline=True)
def record_uniform_emission(counts, group, encoded_bits, invalid):
    active = np.int32(cuda.activemask())
    matching = cuda.match_any_sync(active, np.int32(group))
    leader = libdevice.ffs(matching) - 1
    leader_bits = cuda.shfl_sync(matching, encoded_bits, leader)
    mismatching = cuda.ballot_sync(matching, invalid or encoded_bits != leader_bits)
    if (cuda.threadIdx.x & 31) == leader:
        address = 5 + group * 3
        if invalid:
            cuda.atomic.max(counts, address + 1, 1)
        elif counts[address + 1] == 0:
            previous = cuda.atomic.cas(counts, address, np.int64(0), encoded_bits)
            if previous != 0 and previous != encoded_bits:
                cuda.atomic.max(counts, address + 1, 1)
        if mismatching != 0:
            cuda.atomic.max(counts, address + 1, 1)
        cuda.atomic.add(counts, address + 2, cuda.popc(matching))
'''

FIELDS = ('_KERNEL', '_PROFILE_FACE_KERNEL', '_KERNEL_COMPILED',
    '_PARTITION_KERNELS', '_PARTITION_COMPILED', '_GRID_KERNEL_MODE')


def make_factory(strategy='all'):
    if strategy not in ('all', 'count'):
        raise ValueError('Unknown metadata aggregation strategy')
    tree = ast.parse(inspect.getsource(summary._make_kernels))
    body = tree.body[0].body
    matches = [index for index, node in enumerate(body)
        if isinstance(node, ast.FunctionDef) and node.name == 'record_uniform_emission']
    if len(matches) != 1:
        raise RuntimeError('Expected one uniform metadata helper')
    if strategy == 'all':
        body[matches[0]] = ast.parse(HELPER).body[0]
    else:
        helper = body[matches[0]]
        if ast.unparse(helper.body[-1]) != 'cuda.atomic.add(counts, address + 2, 1)':
            raise RuntimeError('Uniform metadata count operation changed')
        helper.body[-1:] = ast.parse('''
active = np.int32(cuda.activemask())
matching = cuda.match_any_sync(active, np.int32(group))
if (cuda.threadIdx.x & 31) == libdevice.ffs(matching) - 1:
    cuda.atomic.add(counts, address + 2, cuda.popc(matching))
''').body
    namespace = dict(vars(summary))
    exec(compile(ast.fix_missing_locations(tree), '<perf5_uniform_warp_metadata>', 'exec'), namespace)
    return namespace['_make_kernels']


@contextmanager
def kernel_variant(mode, factory, caches):
    saved = {name: getattr(summary, name) for name in FIELDS}
    state = caches.setdefault(mode, {name: False if name.endswith('COMPILED') else None for name in FIELDS})
    for name, value in state.items():
        setattr(summary, name, value)
    try:
        if mode == 'warp':
            with patch.object(summary, '_make_kernels', factory):
                yield
        else:
            yield
    finally:
        state.update({name: getattr(summary, name) for name in FIELDS})
        for name, value in saved.items():
            setattr(summary, name, value)
