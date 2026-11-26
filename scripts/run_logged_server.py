"""Keep source-server output and native-crash stacks in a per-launch log."""
from __future__ import annotations

import argparse
import os
import subprocess
import uuid
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
        self.at_line_start = True

    def write(self, text):
        with self.lock:
            for part in text.splitlines(keepends=True):
                if self.at_line_start:
                    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    self.log.write(f"{stamp} ")
                self.log.write(part)
                self.at_line_start = part.endswith("\n")
            self.log.flush()
            return self.console.write(text)

    def flush(self):
        with self.lock:
            self.log.flush()
            self.console.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


def run_server(log_file, entrypoint, server_args):
    with log_file.open('a', encoding='utf-8', buffering=1) as log:
        stdout, stderr, argv, search_path = sys.stdout, sys.stderr, sys.argv, sys.path[:]
        fault_handler_enabled = faulthandler.is_enabled()
        lock = threading.RLock()
        sys.stdout = LoggedStream(stdout, log, lock)
        sys.stderr = LoggedStream(stderr, log, lock)
        # A real file remains usable even when Python's stream wrappers or
        # the native CUDA/OCP/LLVM caller cannot handle an exception normally.
        faulthandler.enable(file=log, all_threads=True)
        try:
            print(f'[SERVER LOG] UTC={datetime.now(timezone.utc).isoformat()} PID={os.getpid()} Python={sys.version.split()[0]} executable={sys.executable}', flush=True)
            sys.argv = [str(entrypoint), *server_args]
            sys.path.insert(0, str(entrypoint.parent))
            runpy.run_path(str(entrypoint), run_name='__main__')
        except SystemExit as exc:
            if exc.code:
                print(f'[SERVER EXIT] {exc.code}', file=sys.stderr, flush=True)
            raise
        except KeyboardInterrupt:
            print('[SERVER INTERRUPT] KeyboardInterrupt', file=sys.stderr, flush=True)
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


def main():
    if sys.argv[1:2] == ['--server-child']:
        _, _, log_file, entrypoint, *server_args = sys.argv
        run_server(Path(log_file), Path(entrypoint), server_args)
        return

    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument('--log-file', type=Path)
    destination.add_argument('--log-dir', type=Path)
    parser.add_argument('--entrypoint', type=Path, required=True)
    args, server_args = parser.parse_known_args()
    entrypoint = args.entrypoint.resolve(strict=True)
    log_file = (args.log_file or args.log_dir / f'server-{uuid.uuid4().hex}.log').resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def record(message, mode='a'):
        with log_file.open(mode, encoding='utf-8') as log:
            stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            log.write(f'{stamp} {message}\n')
        print(message, flush=True)

    # Reserve the log before launching the server. Only one process writes
    # during server execution, and the supervisor records the OS exit status.
    record(f'[SERVER LOG] {log_file} supervisor_pid={os.getpid()}', mode='x')
    try:
        child = subprocess.Popen([
            sys.executable, '-X', 'faulthandler', str(Path(__file__).resolve()),
            '--server-child', str(log_file), str(entrypoint), *server_args,
        ])
    except OSError as exc:
        record(f'[SERVER LAUNCH FAILED] {exc!r}')
        raise SystemExit(1) from exc

    interrupted = False
    while True:
        try:
            code = child.wait()
            break
        except KeyboardInterrupt:
            # The server shares the console and receives the same Ctrl+C.
            interrupted = True
    record(f'[SERVER PROCESS EXIT] subprocess_pid={child.pid} code={code} '
           f'hex=0x{code & 0xFFFFFFFF:08X} console_interrupt={str(interrupted).lower()}')
    if os.name == 'nt' and code > 0x7FFFFFFF:
        code -= 0x100000000
    elif os.name != 'nt' and code < 0:
        code = 128 - code
    raise SystemExit(code)


if __name__ == '__main__':
    main()
