from __future__ import annotations

import ast
import inspect

from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh


def original_pair_tree():
    tree = ast.parse(inspect.getsource(pair_bvh.make_pair_intersector))
    factory = tree.body[0]
    factory.body = [node for node in factory.body if not (
        isinstance(node, ast.FunctionDef) and node.name in (
            'masked_ray_box_entry', 'affine_ray_box_entry', 'fast_ray_box_entry'))]
    fast = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'isolated_intersect_bvh')
    inverse_index = next(index for index, node in enumerate(fast.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'inverse_z')
    root_index = next(index for index, node in enumerate(fast.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'root_entry')
    fast.body[inverse_index + 1:root_index] = []
    calls = 0
    for statement in fast.body:
        if isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'metadata_start':
            break
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                'ray_box_entry', 'masked_ray_box_entry', 'affine_ray_box_entry', 'fast_ray_box_entry'):
                node.func.id = 'ray_box_entry'
                node.args = node.args[:14]
                calls += 1
    if calls != 3:
        raise ValueError('The original pair oracle requires exactly three auxiliary slab calls')
    return ast.fix_missing_locations(tree)
