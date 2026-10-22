"""Сборка нативного ядра боя (engine/native/sim.c) в разделяемую библиотеку.

Ядро — обычный C без заголовков Python: оно подключается через ctypes, поэтому
для сборки нужен только компилятор (gcc/cc/clang), а не python3-dev. Если
компилятора нет, ``engine.native`` тихо возвращается к чистой Python-версии —
лидербоард и тесты продолжат работать, просто медленнее.

    python -m engine.native.build            # собрать (если нужно)
    python -m engine.native.build --force    # пересобрать
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "sim.c"
LIBNAME = "libsim.so" if sys.platform != "darwin" else "libsim.dylib"
TARGET = HERE / LIBNAME

#: Флаги намеренно «безопасные» для плавающей точки: без FMA-свёртки
#: (-ffp-contract=off) и без быстрой математики, иначе последние биты
#: разойдутся с Python-движком и бой пойдёт по-другому.
CFLAGS = ["-O2", "-std=c11", "-fPIC", "-shared", "-ffp-contract=off",
          "-fno-fast-math", "-fno-unsafe-math-optimizations"]


def find_compiler() -> str | None:
    """Первый доступный компилятор C."""
    for name in (os.environ.get("CC"), "cc", "gcc", "clang"):
        if not name:
            continue
        path = shutil.which(name)
        if path:
            return path
    return None


def needs_build() -> bool:
    if not TARGET.exists():
        return True
    try:
        return TARGET.stat().st_mtime < SOURCE.stat().st_mtime
    except OSError:
        return True


def build(force: bool = False, quiet: bool = False) -> Path | None:
    """Собирает ``libsim``. Возвращает путь или ``None``, если не вышло."""
    if not force and not needs_build():
        return TARGET
    cc = find_compiler()
    if cc is None:
        if not quiet:
            print("engine.native: компилятор C не найден, остаёмся на Python",
                  file=sys.stderr)
        return None
    tmp = TARGET.with_suffix(TARGET.suffix + ".tmp")
    cmd = [cc, *CFLAGS, "-o", str(tmp), str(SOURCE), "-lm"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        if not quiet:
            print(f"engine.native: сборка не запустилась ({exc})", file=sys.stderr)
        return None
    if proc.returncode != 0:
        if not quiet:
            print("engine.native: не удалось собрать sim.c", file=sys.stderr)
            print(proc.stderr[-4000:], file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(TARGET)
    if not quiet:
        print(f"engine.native: собрано {TARGET.name} ({cc})", file=sys.stderr)
    return TARGET


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    res = build(force="--force" in args, quiet=False)
    if res is None:
        return 1
    print(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
