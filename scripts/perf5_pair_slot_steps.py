from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import inspect
from unittest.mock import patch

import numpy as np

from benchmark_perf5_gpu_frontier import _function
from benchmark_perf5_gpu_persistent import STATE
from benchmark_perf5_gpu_specialization import kernel_resources
from perf5_pair_coherent import make_factory as coherent_factory
from leakage_simulator import gpu_cuda_intersection as gpu
from leakage_simulator import gpu_cuda_resident_wavefront as resident


STATE_LOADS = '''
origin_x = origins[ray_index, 0]
origin_y = origins[ray_index, 1]
origin_z = origins[ray_index, 2]
direction_x = directions[ray_index, 0]
direction_y = directions[ray_index, 1]
direction_z = directions[ray_index, 2]
current_power = powers[ray_index]
current_source_face = source_faces[ray_index]
current_ray_kind = terminal_ray_kind[ray_index]
key = reflection_seeds[ray_index]
'''

RECEIVER_LOADS = '''
if face_index < 0 and overflow == 0:
    best_receiver = terminal_receiver[ray_index]
    best_receiver_row = terminal_row[ray_index]
    best_receiver_column = terminal_column[ray_index]
    best_receiver_power = terminal_received_power[ray_index]
    if record_geometry and best_receiver >= 0:
        best_receiver_distance = terminal_distance[ray_index]
        best_receiver_point_x = terminal_point[ray_index, 0]
        best_receiver_point_y = terminal_point[ray_index, 1]
        best_receiver_point_z = terminal_point[ray_index, 2]
        best_receiver_normal_x = terminal_normal[ray_index, 0]
        best_receiver_normal_y = terminal_normal[ray_index, 1]
        best_receiver_normal_z = terminal_normal[ray_index, 2]
'''

RECEIVER_STORE = '''
if face_index < 0 and overflow == 0:
    terminal_receiver[ray_index] = best_receiver
    terminal_row[ray_index] = best_receiver_row
    terminal_column[ray_index] = best_receiver_column
    terminal_received_power[ray_index] = best_receiver_power
    if record_geometry and best_receiver >= 0:
        terminal_distance[ray_index] = best_receiver_distance
        terminal_point[ray_index, 0] = best_receiver_point_x
        terminal_point[ray_index, 1] = best_receiver_point_y
        terminal_point[ray_index, 2] = best_receiver_point_z
        terminal_normal[ray_index, 0] = best_receiver_normal_x
        terminal_normal[ray_index, 1] = best_receiver_normal_y
        terminal_normal[ray_index, 2] = best_receiver_normal_z
'''


def make_factory():
    captured = []
    original_compile = builtins.compile

    def capture(source, filename, mode, *values, **keywords):
        if filename == '<perf5_current_pair_coherent_frontier>':
            captured.append(copy.deepcopy(source))
        return original_compile(source, filename, mode, *values, **keywords)

    with patch.object(builtins, 'compile', side_effect=capture):
        coherent_factory(8)
    if len(captured) != 1:
        raise RuntimeError('Expected one current-pair coherent factory')
    tree = captured[0]
    factory = tree.body[0]
    original = ast.parse(inspect.getsource(resident._make_kernel)).body[0]
    original_kernel = next(node for node in original.body if isinstance(node, ast.FunctionDef)
        and node.name == 'resident_kernel')
    arguments = tuple(argument.arg for argument in original_kernel.args.args)
    depth_loop = next(node for node in original_kernel.body if isinstance(node, ast.For)
        and ast.unparse(node.iter) == 'range(depth_count)')
    extra = ('depth', 'query_distances', 'query_faces')
    helpers = {node.name: node for node in factory.body if isinstance(node, ast.FunctionDef)}
    advance = helpers['advance_frontier_ray']
    split = next(index for index, node in enumerate(advance.body) if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Tuple)
        and tuple(item.id for item in node.targets[0].elts) == ('distance', 'face_index', 'overflow'))
    advance_body = [advance.body[split], *ast.parse(RECEIVER_LOADS).body, *advance.body[split + 1:]]
    advance, advance_names = _function('advance_frontier_ray', advance_body,
        tuple(dict.fromkeys(('ray_index', 'depth', *STATE, 'key', *arguments, *extra))), device=True)
    init_body = ast.parse('''
ray_index = cuda.grid(1)
if ray_index >= ray_count:
    return
''').body + copy.deepcopy(original_kernel.body[2:original_kernel.body.index(depth_loop)])
    initialize, init_names = _function('slot_initialize', init_body, (*arguments, *extra))
    old_query = helpers['coherent_query'].body
    first_state = next(index for index, node in enumerate(old_query)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == 'origin_x')
    query_body = ast.parse('''
physical_index = cuda.grid(1)
if physical_index >= ray_count:
    return
ray_index = physical_index
if terminal_depth[ray_index] >= 0 or overflow_flags[ray_index] != 0:
    return
''').body + copy.deepcopy(old_query[first_state:])
    if not any(isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == 'coherent_pair_intersect_bvh' for node in query_body):
        raise RuntimeError('The query must retain the current exact pair intersector')
    query_body.extend(ast.parse(RECEIVER_STORE).body)
    query, query_names = _function('slot_query', query_body, (*arguments, *extra))
    shade_body = ast.parse(f'''
ray_index = cuda.grid(1)
if ray_index >= ray_count:
    return
if terminal_depth[ray_index] >= 0 or overflow_flags[ray_index] != 0:
    return
{STATE_LOADS}
survives, {', '.join(STATE)} = advance_frontier_ray({', '.join(advance_names)})
if survives:
    origins[ray_index, 0] = origin_x
    origins[ray_index, 1] = origin_y
    origins[ray_index, 2] = origin_z
    directions[ray_index, 0] = direction_x
    directions[ray_index, 1] = direction_y
    directions[ray_index, 2] = direction_z
    powers[ray_index] = current_power
    source_faces[ray_index] = current_source_face
    terminal_ray_kind[ray_index] = current_ray_kind
''').body
    shade, shade_names = _function('slot_shade', shade_body, (*arguments, *extra))
    factory.body = [node for node in factory.body if not isinstance(node, ast.Return)
        and not (isinstance(node, ast.FunctionDef) and node.name in (
            'advance_frontier_ray', 'coherent_reset', 'coherent_init', 'coherent_prefix',
            'coherent_scatter', 'coherent_query', 'coherent_shade'))]
    factory.body.extend((advance, initialize, query, shade))
    factory.body.append(ast.parse('return slot_initialize, slot_query, slot_shade').body[0])
    tree = ast.fix_missing_locations(tree)
    namespace = dict(vars(resident))
    from leakage_simulator import gpu_cuda_pair_bvh
    namespace['pair_bvh'] = gpu_cuda_pair_bvh
    exec(original_compile(tree, '<perf5_pair_primary_slot_steps>', 'exec'), namespace)

    def create():
        return SlotKernels(namespace['_make_kernel'](), arguments, (init_names, query_names, shade_names))

    return create, hashlib.sha256(ast.dump(tree).encode()).hexdigest()


class SlotKernels:
    def __init__(self, kernels, arguments, parameter_sets):
        self.kernels = kernels
        self.arguments = arguments
        self.parameter_sets = parameter_sets
        self.capacity = 0
        self.buffers = None
        self.bytes = 0

    def __getitem__(self, dimensions):
        def launch(*arguments):
            if len(arguments) != len(self.arguments):
                raise ValueError('Slot-step adapter must preserve the original transport ABI')
            values = dict(zip(self.arguments, arguments))
            if values['ray_count'] > self.capacity:
                self.capacity = values['ray_count']
                self.buffers = {'query_distances': gpu._CUDA.device_array(self.capacity, dtype=np.float64),
                    'query_faces': gpu._CUDA.device_array(self.capacity, dtype=np.int64)}
                self.bytes = sum(array.nbytes for array in self.buffers.values())
            values.update(self.buffers)
            self.kernels[0][dimensions](*(values[name] for name in self.parameter_sets[0]))
            for depth in range(min(values['depth_count'], values['event_faces'].shape[1] + 1)):
                values['depth'] = depth
                for stage in (1, 2):
                    self.kernels[stage][dimensions](*(values[name] for name in self.parameter_sets[stage]))

        return launch

    def resources(self):
        return {name: kernel_resources(kernel) for name, kernel in zip(
            ('initialize', 'query', 'shade'), self.kernels)}
