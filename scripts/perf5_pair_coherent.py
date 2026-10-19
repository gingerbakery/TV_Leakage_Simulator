from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import inspect
from unittest.mock import patch

from benchmark_perf5_gpu_coherent import coherent_factory
from benchmark_perf5_gpu_spatial import spatial_factory
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def make_factory(bucket_count, *, spatial=False):
    captured = []
    original_compile = builtins.compile
    target = '<perf5_device_spatial_direction_coherent_frontier>' if spatial else '<perf5_device_direction_coherent_frontier>'

    def capture(source, filename, mode, *values, **keywords):
        if filename == target:
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=capture):
        previous, _ = spatial_factory(bucket_count) if spatial else coherent_factory(bucket_count)
    if len(captured) != 1:
        raise RuntimeError('Expected one previously verified coherent scheduling factory')
    tree = captured[0]
    factory = tree.body[0]
    query = next(node for node in factory.body if isinstance(node, ast.FunctionDef)
        and node.name == 'coherent_query')
    calls = [node for node in ast.walk(query) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'canonical_intersect_bvh']
    if len(calls) != 1:
        raise RuntimeError('Expected one canonical query in the step kernel')
    calls[0].func.id = 'coherent_pair_intersect_bvh'
    factory.body.insert(factory.body.index(query), ast.parse(
        'coherent_pair_intersect_bvh = pair_bvh.make_pair_intersector(cuda, canonical_intersect_bvh, ray_box_entry)').body[0])
    tree = ast.fix_missing_locations(tree)
    namespace = dict(vars(resident))
    namespace['pair_bvh'] = pair_bvh
    exec(original_compile(tree, '<perf5_current_pair_coherent_frontier>', 'exec'), namespace)
    closure = inspect.getclosurevars(previous).nonlocals
    closure['namespace']['_make_kernel'] = namespace['_make_kernel']
    return previous, hashlib.sha256(ast.dump(tree).encode()).hexdigest()
