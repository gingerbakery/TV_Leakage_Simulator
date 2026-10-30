"""Keep source-server output and native-crash stacks in a per-launch log."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import faulthandler
from pathlib import Path
import runpy
import sys
import threading
import traceback


class LoggedStream:
    def __init__(self, console, log, lock):
        self.console, self.log, self.lock = console, log, lock

    def write(self, text):
        with self.lock:
            self.log.write(text)
            self.log.flush()
            return self.console.write(text)

    def flush(self):
        with self.lock:
            self.log.flush()
            self.console.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-file', type=Path, required=True)
    parser.add_argument('--entrypoint', type=Path, required=True)
    args, server_args = parser.parse_known_args()
    entrypoint = args.entrypoint.resolve(strict=True)
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents a second launcher from overwriting evidence.
    with args.log_file.open('x', encoding='utf-8', buffering=1) as log:
        stdout, stderr, argv, search_path = sys.stdout, sys.stderr, sys.argv, sys.path[:]
        fault_handler_enabled = faulthandler.is_enabled()
        lock = threading.RLock()
        sys.stdout = LoggedStream(stdout, log, lock)
        sys.stderr = LoggedStream(stderr, log, lock)
        # A real file remains usable even when Python's stream wrappers or
        # the native CUDA/OCP/LLVM caller cannot handle an exception normally.
        faulthandler.enable(file=log, all_threads=True)
        try:
            print(f'[SERVER LOG] {args.log_file.resolve()}', flush=True)
            print(f'[SERVER LOG] UTC={datetime.now(timezone.utc).isoformat()} Python={sys.version.split()[0]} executable={sys.executable}', flush=True)
            sys.argv = [str(entrypoint), *server_args]
            sys.path.insert(0, str(entrypoint.parent))
            runpy.run_path(str(entrypoint), run_name='__main__')
        except SystemExit as exc:
            if exc.code:
                print(f'[SERVER EXIT] {exc.code}', file=sys.stderr, flush=True)
            raise
        except BaseException:
            traceback.print_exc()
            raise SystemExit(1)
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            faulthandler.disable()
            sys.stdout, sys.stderr, sys.argv = stdout, stderr, argv
            sys.path[:] = search_path
            if fault_handler_enabled:
                faulthandler.enable(file=stderr, all_threads=True)


if __name__ == '__main__':
    main()
