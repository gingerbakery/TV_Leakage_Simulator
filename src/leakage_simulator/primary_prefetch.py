from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from functools import wraps
import threading
import time

from . import native_cpu_sampling


CONTRACT = 'ordered_primary_one_batch_ahead_v1'
_LOCAL = threading.local()


@dataclass
class PrimaryPrefetchStats:
    emitter_count: int = 0
    consumed_batches: int = 0
    wait_sec: float = 0.0

    def to_dict(self):
        return {
            'primary_prefetch_contract': CONTRACT,
            'primary_prefetch_emitter_count': self.emitter_count,
            'primary_prefetch_consumed_batches': self.consumed_batches,
            'primary_prefetch_wait_sec': self.wait_sec,
            'primary_prefetch_max_pending_batches': 1 if self.emitter_count else 0,
        }


def managed_primary_prefetch(function):
    @wraps(function)
    def managed(*args, **kwargs):
        previous = getattr(_LOCAL, 'resources', None)
        with ExitStack() as resources:
            _LOCAL.resources = resources
            try:
                return function(*args, **kwargs)
            finally:
                _LOCAL.resources = previous

    return managed


class _PrefetchedBatches:
    def __init__(self, batches, stats):
        self.batches = iter(batches)
        self.stats = stats
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='bitsam-primary')
        self.future = None
        self.closed = False
        try:
            self.future = self.executor.submit(self._advance)
        except BaseException:
            self.executor.shutdown(wait=True, cancel_futures=True)
            raise

    def _advance(self):
        baseline = native_cpu_sampling.snapshot()
        try:
            batch = next(self.batches)
            available = True
        except StopIteration:
            batch, available = None, False
        current = native_cpu_sampling.snapshot()
        metrics = {name: value - baseline.get(name, 0) for name, value in current.items()}
        return available, batch, metrics

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            raise StopIteration
        started = time.perf_counter()
        available, batch, metrics = self.future.result()
        self.stats.wait_sec += time.perf_counter() - started
        native_cpu_sampling.merge_stats(metrics)
        if not available:
            self.close()
            raise StopIteration
        self.stats.consumed_batches += 1
        self.future = self.executor.submit(self._advance)
        return batch

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.future is not None:
            self.future.cancel()
        self.executor.shutdown(wait=True, cancel_futures=True)
        close = getattr(self.batches, 'close', None)
        if close is not None:
            close()
        self.future = None


def prefetch_primary_batches(batches, stats, *, enabled):
    resources = getattr(_LOCAL, 'resources', None)
    if not enabled or resources is None:
        return batches
    prefetched = _PrefetchedBatches(batches, stats)
    resources.callback(prefetched.close)
    stats.emitter_count += 1
    return prefetched
