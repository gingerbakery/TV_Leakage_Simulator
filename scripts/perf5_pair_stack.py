from __future__ import annotations

import ast
import copy
import inspect
from numbers import Integral

from benchmark_perf5_gpu_bvh_kernel import immediate_stack
from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_resident_wavefront as resident


class SharedPrefix(ast.NodeTransformer):
    def __init__(self, prefix):
        self.prefix = prefix
        self.loads = 0
        self.stores = 0

    def position(self, node):
        text = ast.unparse(node)
        if text == 'base':
            return ast.Constant(value=0)
        if text == 'base + stack_size * stack_stride':
            return ast.Name(id='stack_size', ctx=ast.Load())
        raise ValueError(f'Unexpected traversal stack index: {text}')

    def access(self, node):
        level = self.position(node.slice)
        shared = ast.Subscript(value=ast.Name(id='shared_' + node.value.id, ctx=ast.Load()),
            slice=ast.Tuple(elts=[copy.deepcopy(level), ast.parse('cuda.threadIdx.x', mode='eval').body],
                ctx=ast.Load()), ctx=copy.deepcopy(node.ctx))
        guard = ast.Compare(left=level, ops=[ast.Lt()], comparators=[ast.Constant(value=self.prefix)])
        return shared, guard

    def visit_Subscript(self, node):
        node = self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id in ('stack_entries', 'stack_nodes') and isinstance(node.ctx, ast.Load):
            shared, guard = self.access(node)
            self.loads += 1
            return ast.copy_location(ast.IfExp(test=guard, body=shared, orelse=node), node)
        return node

    def visit_Assign(self, node):
        node = self.generic_visit(node)
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Subscript):
            target = node.targets[0]
            if isinstance(target.value, ast.Name) and target.value.id in ('stack_entries', 'stack_nodes'):
                shared, guard = self.access(target)
                self.stores += 1
                return ast.copy_location(ast.If(test=guard,
                    body=[ast.Assign(targets=[shared], value=copy.deepcopy(node.value))], orelse=[node]), node)
        return node


def stack_tree(prefix=0, *, immediate=False, width=128):
    if prefix not in (0, 2, 4, 8, 16) or not isinstance(width, int) or width < 1:
        raise ValueError('Unsupported shared prefix or block width')
    tree = ast.parse(inspect.getsource(pair_bvh.make_pair_intersector))
    fast = next(node for node in tree.body[0].body if isinstance(node, ast.FunctionDef)
        and node.name == 'isolated_intersect_bvh')
    if immediate:
        root_store = next(node for node in fast.body if isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == 'stack_nodes[base]')
        root = copy.deepcopy(root_store.value)
        root_store.value = ast.Constant(value=0)
        loop = next(node for node in fast.body if isinstance(node, ast.While)
            and ast.unparse(node.test) == 'stack_size > 0')
        prune = next(node for node in loop.body if isinstance(node, ast.If)
            and ast.unparse(node.test) == 'entry > best_distance + 8e-10')
        predicate = copy.deepcopy(prune.test)
        prune.test = ast.parse('entry > best_distance', mode='eval').body
        immediate_stack(fast)
        initialization = [node for node in fast.body if isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == 'current_node']
        if len(initialization) != 1:
            raise RuntimeError('Expected one immediate traversal root')
        initialization[0].value = root
        prune.test = predicate
    if prefix:
        transform = SharedPrefix(prefix)
        transform.visit(fast)
        if transform.loads != 2 or not transform.stores:
            raise RuntimeError('Shared stack transform did not cover all traversal loads')
        fast.body[:0] = ast.parse(f'''
shared_stack_entries = cuda.shared.array(({prefix}, {width}), dtype=np.float64)
shared_stack_nodes = cuda.shared.array(({prefix}, {width}), dtype=np.int64)
''').body
    return ast.fix_missing_locations(tree)


def make_factory(prefix=0, *, immediate=False):
    namespace = dict(vars(pair_bvh))
    exec(compile(stack_tree(prefix, immediate=immediate, width=resident.THREADS_PER_BLOCK),
        '<perf5_fused_pair_stack>', 'exec'), namespace)
    return namespace['make_pair_intersector']


class WidthGuard:
    def __init__(self, kernel, width):
        self.kernel = kernel
        self.width = width

    def __getattr__(self, name):
        return getattr(self.kernel, name)

    def __getitem__(self, dimensions):
        if not isinstance(dimensions, tuple) or len(dimensions) < 2 or not isinstance(dimensions[1], Integral) or dimensions[1] < 1 or dimensions[1] > self.width:
            raise ValueError('Shared stack requires a one-dimensional block within its compiled width')
        return self.kernel[dimensions]


def make_kernel(prefix=0, *, immediate=False):
    kernel = resident._make_kernel(_intersector_factory=make_factory(prefix, immediate=immediate))
    return WidthGuard(kernel, resident.THREADS_PER_BLOCK) if prefix else kernel
