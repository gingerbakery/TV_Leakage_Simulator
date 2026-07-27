from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable


ROOT = Path(__file__).resolve().parent
WATCH_DIRS = [ROOT / "src", ROOT / "docs"]
WATCH_FILES = [
    ROOT / "run_api.py",
    ROOT / "run_web.py",
    ROOT / "run_web_dev.py",
]
WATCH_SUFFIXES = {".py", ".md"}
PORT = os.environ.get("LEAKAGE_WEB_PORT", "8788").strip() or "8788"


def iter_watch_paths() -> Iterable[Path]:
    seen = set()
    for path in WATCH_FILES:
        if path.exists() and path not in seen:
            seen.add(path)
            yield path
    for base_dir in WATCH_DIRS:
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in WATCH_SUFFIXES and path not in seen:
                seen.add(path)
                yield path


def snapshot() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for path in iter_watch_paths():
        try:
            out[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return out


def start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["LEAKAGE_WEB_PORT"] = PORT
    print(
        "[DEV] starting FastAPI on http://127.0.0.1:{0}".format(
            PORT
        ),
        flush=True,
    )
    return subprocess.Popen(
        [sys.executable, "run_api.py"],
        cwd=str(ROOT),
        env=env,
    )


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> None:
    print("[DEV] watch mode enabled", flush=True)
    print("[DEV] edit Python/Markdown files and the server will restart automatically", flush=True)
    print("[DEV] browser reloads automatically after restart", flush=True)
    last = snapshot()
    process = start_server()
    try:
        while True:
            time.sleep(1.0)
            current = snapshot()
            if current != last:
                print("[DEV] change detected, restarting...", flush=True)
                stop_server(process)
                time.sleep(0.6)
                process = start_server()
                last = current
                continue
            if process.poll() is not None:
                print("[DEV] server stopped unexpectedly, restarting...", flush=True)
                time.sleep(0.6)
                process = start_server()
                last = current
    except KeyboardInterrupt:
        print("\n[DEV] stopping watch mode", flush=True)
    finally:
        stop_server(process)


if __name__ == "__main__":
    main()
