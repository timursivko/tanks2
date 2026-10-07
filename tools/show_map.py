"""Печатает карту символами — быстрый способ увидеть стены и проходы.

    python tools/show_map.py fortress
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / "maps"


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "arena"
    path = MAPS / (name if name.endswith(".json") else f"{name}.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"{data['name']}  {path.name}  {data['description']}")
    for row in data["rows"]:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())