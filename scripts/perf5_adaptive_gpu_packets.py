from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from benchmark_perf5_gpu_batching import coalesce
from leakage_simulator import gpu_cuda_resident_wavefront as resident
from leakage_simulator import raytracer


def adaptive_batch_request(trace_input, *, minimum_rays=1000000, target_rays=131072):
    requested = sum(emitter.ray_count for emitter in trace_input.emitters if emitter.enabled)
    if requested < minimum_rays:
        return None
    context = SimpleNamespace(_event_depth_hint=resident.INITIAL_EVENT_WINDOW_DEPTH)
    target = resident.event_window_batch_size(context, target_rays, trace_input.config.max_depth)
    return target_rays if target >= target_rays else None


@contextmanager
def adaptive_packets(trace_input, *, minimum_rays=1000000, target_rays=131072):
    original_context = raytracer._build_gpu_resident_wavefront_context
    original_prefetch = raytracer.prefetch_primary_batches
    contexts = []
    decisions = []
    requested = sum(emitter.ray_count for emitter in trace_input.emitters if emitter.enabled)

    def context(*values, **keywords):
        result = original_context(*values, **keywords)
        contexts[:] = [result]
        return result

    def prefetch(batches, stats, *, enabled):
        target = resident.event_window_batch_size(contexts[0], target_rays,
            trace_input.config.max_depth) if contexts and requested >= minimum_rays else 0
        selected = target >= target_rays
        decisions.append({'requested_rays': requested, 'planned_target': target,
            'coalesced': selected, 'target_rays': target_rays if selected else 65536})
        return original_prefetch(coalesce(batches, target_rays) if selected else batches,
            stats, enabled=enabled)

    with patch.object(raytracer, '_build_gpu_resident_wavefront_context', new=context), patch.object(
        raytracer, 'prefetch_primary_batches', new=prefetch
    ):
        yield decisions
