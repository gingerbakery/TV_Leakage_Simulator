from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
import os
import threading
from typing import Callable


CONTRACT = 'strict_fp64_disjoint_cpu_ranges_v1'
_LOCK = threading.Lock()
_POOL: ThreadPoolExecutor | None = None
_LOCAL = threading.local()


def cpu_worker_limit(*, default_limit: int = 4) -> int:
    available = max(1, os.cpu_count() or 1)
    default = min(default_limit, max(1, available // 2))
    value = os.environ.get('LEAKAGE_CPU_WORKERS', '').strip()
    if not value:
        return default
    try:
        requested = int(value)
    except ValueError:
        return 1
    return min(available, 32, max(1, requested))


def run_cpu_ranges(row_count: int, operation: Callable[[int, int], None], *,
                   minimum_rows: int, enabled: bool = True,
                   default_worker_limit: int = 4) -> int:
    workers = min(cpu_worker_limit(default_limit=default_worker_limit), max(1, row_count // minimum_rows)) if enabled else 1
    if workers == 1 or getattr(_LOCAL, 'active', False):
        operation(0, row_count)
        return 1
    global _POOL
    with _LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=min(32, max(1, os.cpu_count() or 1)),
                                       thread_name_prefix='bitsam-cpu')
        pool = _POOL

    def execute(start: int, end: int) -> None:
        _LOCAL.active = True
        try:
            operation(start, end)
        finally:
            _LOCAL.active = False

    futures = [pool.submit(execute, row_count * index // workers,
                           row_count * (index + 1) // workers) for index in range(workers)]
    wait(futures)
    for future in futures:
        future.result()
    return workers
