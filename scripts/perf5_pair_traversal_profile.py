from __future__ import annotations

import ast
import hashlib
import inspect

import numpy as np

from leakage_simulator import gpu_cuda_pair_bvh as pair_bvh
from leakage_simulator import gpu_cuda_resident_wavefront as resident


BASE_FIELDS = ('pair_queries', 'fast_queries', 'fast_stack_pops', 'fast_leaf_visits',
    'fast_triangle_tests', 'fast_internal_visits', 'canonical_queries', 'canonical_stack_pops',
    'canonical_leaf_visits', 'canonical_triangle_tests', 'canonical_internal_visits',
    'certified_queries', 'bypass_queries', 'fast_replays', 'pair_active_subgroups',
    'canonical_active_subgroups', 'uncertain_edge_events', 'uncertain_cap_events',
    'uncertain_nonfinite_events', 'uncertain_capacity_events', 'certificate_box_tests')


def increment(index):
    return ast.parse(f'profile_counters[{index}, ray_index] += 1').body[0]


def group_increment(index):
    return ast.parse(f'''
if (cuda.threadIdx.x & 31) == libdevice.ffs(np.int32(cuda.activemask())) - 1:
    profile_counters[{index}, ray_index] += 1
''').body[0]


def instrument_traversal(function, prefix, fields):
    indices = {name: fields.index(name) for name in fields}

    class Traversal(ast.NodeTransformer):
        covered = {'loop': 0, 'leaf': 0, 'triangle': 0, 'internal': 0}

        def visit_While(self, node):
            self.generic_visit(node)
            if ast.unparse(node.test) == 'stack_size > 0':
                node.body.insert(0, increment(indices[prefix + '_stack_pops']))
                self.covered['loop'] += 1
            return node

        def visit_If(self, node):
            self.generic_visit(node)
            if ast.unparse(node.test) == 'leaf_count > 0':
                node.body.insert(0, increment(indices[prefix + '_leaf_visits']))
                self.covered['leaf'] += 1
            return node

        def visit_Assign(self, node):
            self.generic_visit(node)
            if len(node.targets) != 1:
                return node
            target = ast.unparse(node.targets[0])
            if target == 'edge1_x':
                self.covered['triangle'] += 1
                return [increment(indices[prefix + '_triangle_tests']), node]
            if target == 'left_index':
                self.covered['internal'] += 1
                return [increment(indices[prefix + '_internal_visits']), node]
            return node

    transform = Traversal()
    transform.visit(function)
    if any(value != 1 for value in transform.covered.values()):
        raise RuntimeError(f'Unexpected traversal instrumentation sites: {transform.covered}')
    function.args.args.append(ast.arg(arg='profile_counters'))
    function.body.insert(0, increment(indices[prefix + '_queries']))


def profile_trees():
    fields = list(BASE_FIELDS)
    pair_tree = ast.parse(inspect.getsource(pair_bvh.make_pair_intersector))
    pair_factory = pair_tree.body[0]
    fast = next(node for node in pair_factory.body if isinstance(node, ast.FunctionDef)
        and node.name == 'isolated_intersect_bvh')
    wrapper = next(node for node in pair_factory.body if isinstance(node, ast.FunctionDef)
        and node.name == 'pair_intersect_bvh')
    rejection_sites = {}

    class Rejections(ast.NodeTransformer):
        def visit_If(self, node):
            for position, statement in reversed(list(enumerate(node.body))):
                if (isinstance(statement, ast.Return) and isinstance(statement.value, ast.Tuple)
                    and isinstance(statement.value.elts[-1], ast.Constant) and statement.value.elts[-1].value == 2):
                    name = f'fast_reject_{len(rejection_sites)}'
                    rejection_sites[name] = ast.unparse(node.test)
                    fields.append(name)
                    node.body.insert(position, increment(fields.index(name)))
            return self.generic_visit(node)

    Rejections().visit(fast)
    if len(rejection_sites) != 11:
        raise RuntimeError(f'Expected eleven current fast rejection sites, got {len(rejection_sites)}')

    class Uncertainty(ast.NodeTransformer):
        covered = 0

        def visit_If(self, node):
            self.generic_visit(node)
            assignments = [statement for statement in node.body if isinstance(statement, ast.Assign)
                and len(statement.targets) == 1 and ast.unparse(statement.targets[0]) == 'uncertain'
                and isinstance(statement.value, ast.Constant) and statement.value.value == 1]
            if not assignments:
                return node
            condition = ast.unparse(node.test)
            name = ('uncertain_nonfinite_events' if 'not math.isfinite(distance)' == condition
                else 'uncertain_edge_events' if 'u_value <' in condition
                else 'uncertain_cap_events' if 'abs(distance - minimum_t)' in condition else None)
            if name is None:
                raise RuntimeError(f'Unknown uncertainty predicate: {condition}')
            node.body.insert(node.body.index(assignments[0]), increment(fields.index(name)))
            self.covered += 1
            return node

    uncertainty = Uncertainty()
    uncertainty.visit(fast)
    if uncertainty.covered != 3:
        raise RuntimeError('Expected three predicate-based uncertainty sites')
    capacity = [node for node in ast.walk(fast) if isinstance(node, ast.If)
        and ast.unparse(node.test) == 'candidate_count == 1']
    insertion = [node for node in capacity if node.orelse
        and any(isinstance(statement, ast.Assign) and ast.unparse(statement.targets[0]) == 'uncertain'
            for statement in node.orelse)]
    if len(insertion) != 1:
        raise RuntimeError('Expected one candidate capacity uncertainty branch')
    insertion[0].orelse.insert(0, increment(fields.index('uncertain_capacity_events')))
    instrument_traversal(fast, 'fast', fields)
    certificates = 0
    for node in ast.walk(fast):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name) and node.value.func.id == 'ray_box_entry'):
            certificates += 1

    class Certificates(ast.NodeTransformer):
        def visit_Assign(self, node):
            if (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                and node.value.func.id == 'ray_box_entry'):
                return [increment(fields.index('certificate_box_tests')), node]
            return self.generic_visit(node)

    if certificates != 4:
        raise RuntimeError('Expected four canonical certificate box call sites')
    Certificates().visit(fast)
    wrapper.args.args.append(ast.arg(arg='profile_counters'))
    wrapper.body[:0] = [increment(fields.index('pair_queries')),
        group_increment(fields.index('pair_active_subgroups'))]
    bypasses = 0
    replay_branches = 0
    for statement in wrapper.body:
        if not isinstance(statement, ast.If):
            continue
        calls = [node for node in ast.walk(statement) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == 'canonical_intersect_bvh']
        if not calls:
            continue
        if ast.unparse(statement.test) == 'status != 0':
            statement.body.insert(0, increment(fields.index('fast_replays')))
            replay_branches += 1
        else:
            statement.body.insert(0, increment(fields.index('bypass_queries')))
            bypasses += 1
    if bypasses != 3 or replay_branches != 1:
        raise RuntimeError('Unexpected current pair dispatch branches')
    wrapper.body.insert(-1, increment(fields.index('certified_queries')))
    calls = 0
    for node in ast.walk(wrapper):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ('canonical_intersect_bvh', 'isolated_intersect_bvh')):
            node.args.append(ast.Name(id='profile_counters', ctx=ast.Load()))
            calls += 1
    if calls != 5:
        raise RuntimeError('Expected all five pair intersector calls')
    resident_tree = ast.parse(inspect.getsource(resident._make_kernel))
    factory = resident_tree.body[0]
    canonical = next(node for node in factory.body if isinstance(node, ast.FunctionDef)
        and node.name == 'intersect_bvh')
    instrument_traversal(canonical, 'canonical', fields)
    canonical.body.insert(1, group_increment(fields.index('canonical_active_subgroups')))
    factory.body.insert(0, ast.parse('from numba.cuda import libdevice').body[0])
    kernel = next(node for node in factory.body if isinstance(node, ast.FunctionDef)
        and node.name == 'resident_kernel')
    kernel.args.args.append(ast.arg(arg='profile_counters'))
    queries = [node for node in ast.walk(kernel) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'intersect_bvh']
    if len(queries) != 1:
        raise RuntimeError('Expected one fused kernel geometry query site')
    queries[0].args.append(ast.Name(id='profile_counters', ctx=ast.Load()))
    return ast.fix_missing_locations(resident_tree), ast.fix_missing_locations(pair_tree), fields, rejection_sites


def make_kernel():
    resident_tree, pair_tree, fields, rejections = profile_trees()
    pair_namespace = dict(vars(pair_bvh))
    exec(compile(pair_tree, '<perf5_current_pair_query_counts>', 'exec'), pair_namespace)
    namespace = dict(vars(resident))
    exec(compile(resident_tree, '<perf5_current_resident_query_counts>', 'exec'), namespace)
    kernel = namespace['_make_kernel'](_intersector_factory=pair_namespace['make_pair_intersector'])
    fingerprint = hashlib.sha256((ast.dump(resident_tree) + ast.dump(pair_tree)).encode()).hexdigest()
    return kernel, fields, rejections, fingerprint


class CounterLaunch:
    def __init__(self, kernel, counters):
        self.kernel = kernel
        self.counters = counters

    def __getattr__(self, name):
        return getattr(self.kernel, name)

    def __getitem__(self, dimensions):
        configured = self.kernel[dimensions]

        def launch(*arguments):
            return configured(*arguments, self.counters)

        return launch


class CountingKernel:
    def __init__(self, kernel, cuda, fields):
        self.kernel = kernel
        self.cuda = cuda
        self.fields = tuple(fields)
        self.samples = []

    def __getattr__(self, name):
        return getattr(self.kernel, name)

    def __getitem__(self, dimensions):
        def launch(*arguments):
            counters = self.cuda.to_device(np.zeros((len(self.fields), int(arguments[0])), dtype=np.int64))
            result = CounterLaunch(self.kernel, counters)[dimensions](*arguments)
            self.samples.append(counters.copy_to_host().sum(axis=1, dtype=np.int64))
            return result

        return launch

    def totals(self):
        summed = np.sum(self.samples, axis=0, dtype=np.int64) if self.samples else np.zeros(len(self.fields), dtype=np.int64)
        return {name: int(value) for name, value in zip(self.fields, summed, strict=True)}


def counts_valid(counts):
    rejections = sum(value for name, value in counts.items() if name.startswith('fast_reject_'))
    return (counts['pair_queries'] == counts['fast_queries'] + counts['bypass_queries']
        and counts['canonical_queries'] == counts['fast_replays'] + counts['bypass_queries']
        and counts['fast_queries'] == counts['certified_queries'] + counts['fast_replays']
        and rejections == counts['fast_replays']
        and all(value >= 0 for value in counts.values()))
