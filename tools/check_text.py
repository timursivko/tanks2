"""Проверка текстовых файлов: BOM, битая кодировка, подозрительные «????».

Запуск: python tools/check_text.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", ".git", "saves", "node_modules"}
SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".txt"}


def main() -> int:
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue                      # сам проверяющий содержит «????» в тексте
        raw = path.read_bytes()
        rel = path.relative_to(ROOT)
        if raw[:3] == b"\xef\xbb\xbf":
            problems.append(f"{rel}:1 — лишний BOM в начале файла")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{rel}:1 — не UTF-8: {exc}")
            continue
        if "\ufffd" in text:
            problems.append(f"{rel}:1 — символ замены \\ufffd, файл побит")
        for i, line in enumerate(text.splitlines(), 1):
            if line.count("?") >= 4:
                problems.append(f"{rel}:{i} — подозрительно много «?»: {line.strip()[:60]}")

    for p in problems:
        print(p)
    print(f"проблем: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())