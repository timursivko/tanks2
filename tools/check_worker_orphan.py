"""Воркер не должен жить дольше хозяина.

    python tools/check_worker_orphan.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    proc = subprocess.Popen([sys.executable, "-u", "-m", "engine.sandbox.worker"],
                            cwd=str(ROOT), stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc.stdin.write(b'{"op": "init", "map": {"rows": ["..."], "tile": 32}}\n')
    proc.stdin.flush()
    time.sleep(1.0)
    alive = proc.poll() is None
    print(f"через 1 с после последней команды: {'жив' if alive else 'вышел'}")

    # Ждём IDLE_TIMEOUT + запас: отец молчит, воркер обязан уйти сам.
    for _ in range(12):
        time.sleep(1.0)
        if proc.poll() is not None:
            break
    code = proc.poll()
    print(f"через {12} с: код возврата {code}")
    if code is None:
        proc.kill()
        print("не вышел — нужен kill")
        return 1
    return 0 if not alive or code == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())