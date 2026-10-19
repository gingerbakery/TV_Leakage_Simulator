from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import replace
import inspect
import time
from unittest.mock import patch

from perf5_pair_source import original_pair_tree
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_pair_scene as pair_scene
from leakage_simulator import gpu_cuda_resident_wavefront as resident

EXTRA_PADDING_MM = 1e-6


def affine_tree(*, padded=False):
    tree = original_pair_tree()
    factory = tree.body[0]
    fast = next(node for node in factory.body
        if isinstance(node, ast.FunctionDef) and node.name == 'isolated_intersect_bvh')
    source = ast.parse(inspect.getsource(resident._make_kernel))
    box = next(node for node in source.body[0].body
        if isinstance(node, ast.FunctionDef) and node.name == 'ray_box_entry')
    box.name = 'affine_ray_box_entry'
    bias_names = tuple(f'bias_{axis}_{side}' for axis in ('x', 'y', 'z') for side in ('lower', 'upper'))
    box.args.args.extend(ast.arg(arg=name) for name in bias_names)
    axis_loop = next(node for node in box.body if isinstance(node, ast.For))
    selector = axis_loop.body[0]
    selector.body.extend(ast.parse('lower_bias = bias_x_lower\nupper_bias = bias_x_upper').body)
    selector.orelse[0].body.extend(ast.parse('lower_bias = bias_y_lower\nupper_bias = bias_y_upper').body)
    selector.orelse[0].orelse.extend(ast.parse('lower_bias = bias_z_lower\nupper_bias = bias_z_upper').body)
    entry_index = next(index for index, node in enumerate(axis_loop.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'axis_entry')
    if not ast.unparse(axis_loop.body[entry_index + 2].test) == 'axis_entry > axis_exit':
        raise ValueError('Original slab order must match the checked transformation')
    axis_loop.body[entry_index:entry_index + 3] = ast.parse('''
if inverse_value >= 0.0:
    near_bound = bounds_min[node_index, axis]
    far_bound = bounds_max[node_index, axis]
else:
    near_bound = bounds_max[node_index, axis]
    far_bound = bounds_min[node_index, axis]
axis_entry = libdevice.fma_rn(near_bound, inverse_value, lower_bias)
axis_exit = libdevice.fma_rn(far_bound, inverse_value, upper_bias)
''').body
    factory.body.insert(factory.body.index(fast), box)
    insertion = next(index for index, node in enumerate(fast.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'root_entry')
    setup = 'root_index = node_start[0]\n'
    for index, axis in enumerate(('x', 'y', 'z')):
        if padded:
            setup += f'''
center_bias_{axis} = -origin_{axis} * inverse_{axis}
bias_{axis}_lower = center_bias_{axis}
bias_{axis}_upper = center_bias_{axis}
'''
        else:
            setup += f'''
bound_{axis} = abs(node_bounds_min[root_index, {index}]) + abs(node_bounds_max[root_index, {index}]) + 2.0
margin_{axis} = (abs(origin_{axis}) + bound_{axis}) * abs(inverse_{axis}) * 2.842170943040401e-14
center_bias_{axis} = -origin_{axis} * inverse_{axis}
bias_{axis}_lower = center_bias_{axis} - margin_{axis}
bias_{axis}_upper = center_bias_{axis} + margin_{axis}
'''
    fast.body[insertion:insertion] = ast.parse(setup).body
    calls = 0
    for statement in fast.body:
        if isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'metadata_start':
            break
        for node in ast.walk(statement):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'ray_box_entry':
                node.func.id = 'affine_ray_box_entry'
                node.args.extend(ast.Name(id=name, ctx=ast.Load()) for name in bias_names)
                calls += 1
    if calls != 3:
        raise ValueError('Only the three fast-tree slab calls may change')
    return ast.fix_missing_locations(tree)


def affine_pair_factory(*, padded=False):
    namespace = dict(pair_bvh.__dict__)
    exec(compile(affine_tree(padded=padded), '<perf5_fp64_affine_interval_box>', 'exec'), namespace)
    return namespace['make_pair_intersector']


def expanded_scene(scene):
    if not pair_scene.has_pair_scene(scene):
        return scene
    root = int(scene.node_start[0])
    lower = scene.node_bounds_min.copy()
    upper = scene.node_bounds_max.copy()
    lower[root:] -= EXTRA_PADDING_MM
    upper[root:] += EXTRA_PADDING_MM
    lower.setflags(write=False)
    upper.setflags(write=False)
    return replace(scene, node_bounds_min=lower, node_bounds_max=upper)


@contextmanager
def padded_scene_variant(states):
    original_prepare = pair_scene.prepare_pair_scene
    def prepare(mesh):
        scene, elapsed, reused = original_prepare(mesh)
        if not pair_scene.has_pair_scene(scene):
            return scene, elapsed, reused
        cached = states.get(id(mesh))
        if cached is not None and cached[0] is scene:
            return cached[1], elapsed, reused
        started = time.perf_counter()
        prepared = expanded_scene(scene)
        states[id(mesh)] = (scene, prepared)
        return prepared, elapsed + time.perf_counter() - started, False
    with patch.object(pair_scene, 'prepare_pair_scene', new=prepare):
        yield
