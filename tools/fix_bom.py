"""Убирает BOM из указанных файлов: место, где PowerShell испортил кодировку.

    python tools/fix_bom.py engine/sdk/tankp.py tests/test_sandbox_and_battle.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"


def main(argv: list[str]) -> int:
    if not argv:
        print("укажите файлы: python tools/fix_bom.py <путь> ...")
        return 2
    for name in argv:
        path = Path(name)
        if not path.is_absolute():
            path = ROOT / path
        raw = path.read_bytes()
        if not raw.startswith(BOM):
            print(f"{path.relative_to(ROOT)}: BOM не найден")
            continue
        path.write_bytes(raw[len(BOM):])
        print(f"{path.relative_to(ROOT)}: BOM убран")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))