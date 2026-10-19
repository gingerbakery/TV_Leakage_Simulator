from __future__ import annotations

import ast
from contextlib import contextmanager
import subprocess
from unittest.mock import patch

from leakage_simulator import native_cpu_counter_wavefront as counter


def load_counter_factory(revision, root):
    source = subprocess.check_output(
        ['git', 'show', f'{revision}:src/leakage_simulator/native_cpu_counter_wavefront.py'],
        cwd=root, text=True, encoding='utf-8',
    )
    tree = ast.parse(source)
    factory = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == '_make_kernel'
    )
    namespace = dict(vars(counter))
    exec(compile(ast.Module(body=[factory], type_ignores=[]), f'{revision}:counter', 'exec'), namespace)
    return namespace['_make_kernel']


@contextmanager
def counter_reference(factory, state):
    saved_kernel, saved_compiled = counter._KERNEL, counter._KERNEL_COMPILED
    counter._KERNEL = state.get('kernel')
    counter._KERNEL_COMPILED = state.get('compiled', False)
    try:
        with patch.object(counter, '_make_kernel', factory):
            yield
    finally:
        state.update(kernel=counter._KERNEL, compiled=counter._KERNEL_COMPILED)
        counter._KERNEL, counter._KERNEL_COMPILED = saved_kernel, saved_compiled
