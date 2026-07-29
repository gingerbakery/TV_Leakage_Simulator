from __future__ import annotations

import sys
import time
import urllib.request
import webbrowser


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788/"
    health_url = url.rstrip("/") + "/health"
    for _ in range(120):
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return 0
        except Exception:
            time.sleep(1.0)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
