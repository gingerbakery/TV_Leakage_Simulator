from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


@contextmanager
def initial_event_window(depth=16):
    if depth not in (8, 16, 32):
        raise ValueError('The diagnostic initial event window must be 8, 16 or 32')
    original = raytracer._build_gpu_resident_wavefront_context

    def build(*arguments, **keywords):
        context = original(*arguments, **keywords)
        if context is not None:
            context._event_depth_hint = depth
        return context

    with patch.object(resident, 'INITIAL_EVENT_WINDOW_DEPTH', depth), patch.object(
        raytracer, '_build_gpu_resident_wavefront_context', new=build
    ):
        yield
