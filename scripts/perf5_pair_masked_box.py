from __future__ import annotations

import ast
import copy
import inspect

from perf5_pair_affine_box import affine_tree
from perf5_pair_source import original_pair_tree
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_resident_wavefront as resident


def masked_tree(*, affine=False, padded=False):
    tree = affine_tree(padded=padded) if affine else original_pair_tree()
    factory = tree.body[0]
    fast = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'isolated_intersect_bvh')
    old_name = 'affine_ray_box_entry' if affine else 'ray_box_entry'
    if affine:
        box = next(node for node in factory.body if isinstance(node, ast.FunctionDef) and node.name == old_name)
    else:
        source = ast.parse(inspect.getsource(resident._make_kernel))
        box = copy.deepcopy(next(node for node in source.body[0].body
            if isinstance(node, ast.FunctionDef) and node.name == old_name))
        factory.body.insert(factory.body.index(fast), box)
    box.name = 'masked_ray_box_entry'
    box.args.args.extend((ast.arg(arg='parallel_mask'), ast.arg(arg='negative_mask')))
    parallel_count = 0
    sign_count = 0
    for node in ast.walk(box):
        if not isinstance(node, ast.If):
            continue
        expression = ast.unparse(node.test)
        if expression == 'abs(direction_value) < 1e-12':
            node.test = ast.parse('(parallel_mask & (np.int32(1) << np.int32(axis))) != 0', mode='eval').body
            parallel_count += 1
        elif expression == ('inverse_value >= 0.0' if affine else 'axis_entry > axis_exit'):
            condition = '== 0' if affine else '!= 0'
            node.test = ast.parse(f'(negative_mask & (np.int32(1) << np.int32(axis))) {condition}', mode='eval').body
            sign_count += 1
    if parallel_count != 1 or sign_count != 1:
        raise ValueError('The original slab direction predicates must be recognizable')
    setup = 'parallel_mask = np.int32(0)\nnegative_mask = np.int32(0)\n'
    for index, axis in enumerate(('x', 'y', 'z')):
        setup += f'''
if abs(direction_{axis}) < 1e-12:
    parallel_mask |= np.int32({1 << index})
if inverse_{axis} < 0.0:
    negative_mask |= np.int32({1 << index})
'''
    insertion = next(index for index, node in enumerate(fast.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'root_entry')
    fast.body[insertion:insertion] = ast.parse(setup).body
    changed = 0
    for statement in fast.body:
        if isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'metadata_start':
            break
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == old_name:
                node.func.id = 'masked_ray_box_entry'
                node.args.extend((ast.Name(id='parallel_mask', ctx=ast.Load()), ast.Name(id='negative_mask', ctx=ast.Load())))
                changed += 1
    if changed != 3:
        raise ValueError('Only three fast-tree box calls may use cached direction predicates')
    return ast.fix_missing_locations(tree)


def masked_pair_factory(*, affine=False, padded=False):
    namespace = dict(pair_bvh.__dict__)
    exec(compile(masked_tree(affine=affine, padded=padded), '<perf5_cached_box_predicates>', 'exec'), namespace)
    return namespace['make_pair_intersector']
