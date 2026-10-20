from __future__ import annotations

import ast
import copy
import inspect
import textwrap

from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh


NAMES = ('inverse_x', 'inverse_y', 'inverse_z')


def inverse_assignments(definition):
    assignments = [node for node in definition.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in NAMES]
    if tuple(node.targets[0].id for node in assignments) != NAMES:
        raise RuntimeError('Expected the three original reciprocal operations')
    return assignments


def extend(definition):
    assignments = inverse_assignments(definition)
    definition.args.args[8:8] = [ast.arg(arg=name) for name in NAMES]
    definition.body = [node for node in definition.body if node not in assignments]
    return assignments


def shared_canonical(cuda, original):
    function = original.py_func
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = tree.body[0]
    extend(definition)
    definition.decorator_list = [ast.parse('cuda.jit(device=True, inline=False)', mode='eval').body]
    namespace = dict(function.__globals__)
    namespace.update(inspect.getclosurevars(function).nonlocals)
    namespace['cuda'] = cuda
    exec(compile(ast.fix_missing_locations(tree), '<perf5_shared_canonical_inverse>', 'exec'), namespace)
    return namespace[definition.name]


def make_factory():
    tree = ast.parse(inspect.getsource(pair_bvh.make_pair_intersector))
    definition = tree.body[0]
    helpers = {node.name: node for node in definition.body if isinstance(node, ast.FunctionDef)}
    inverse = extend(helpers['isolated_intersect_bvh'])
    wrapper = helpers['pair_intersect_bvh']
    wrapper.body[:0] = copy.deepcopy(inverse)
    changed = 0
    for node in ast.walk(wrapper):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in (
                'isolated_intersect_bvh', 'canonical_intersect_bvh'):
            node.args[8:8] = [ast.Name(id=name, ctx=ast.Load()) for name in NAMES]
            changed += 1
    if changed != 5:
        raise RuntimeError(f'Expected five canonical/isolated calls, found {changed}')
    namespace = dict(vars(pair_bvh))
    exec(compile(ast.fix_missing_locations(tree), '<perf5_shared_pair_inverse>', 'exec'), namespace)
    transformed = namespace['make_pair_intersector']

    def factory(cuda, canonical_intersect_bvh, ray_box_entry):
        return transformed(cuda, shared_canonical(cuda, canonical_intersect_bvh), ray_box_entry)

    return factory
