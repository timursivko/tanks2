"""Одноразовая починка карт: выравнивает строки до 24 символов.

Запускать вручную, если поправите ASCII-арт в ``gen_maps.py``.
"""

from __future__ import annotations

import ast
import io
import re
from pathlib import Path

SRC = Path(__file__).with_name("gen_maps.py")
HALF = 24
ROWS = 28


def normalize(rows: list[str]) -> list[str]:
    out = []
    for r in rows:
        while len(r) > HALF and r[-1] == ".":
            r = r[:-1]
        if len(r) > HALF:
            raise SystemExit(f"строка слишком длинная и не из точек: {r!r}")
        out.append(r.ljust(HALF, "."))
    while len(out) > ROWS and not out[-1].strip("."):
        out.pop()
    while len(out) < ROWS:
        out.insert(0, "." * HALF)
    return out


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    tree = ast.parse(text)
    spans = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.endswith("_HALF")):
            spans[node.targets[0].id] = (node.lineno, node.end_lineno,
                                         ast.literal_eval(node.value))
    for name, (start, end, rows) in spans.items():
        fixed = normalize(list(rows))
        if fixed == list(rows):
            print(f"{name}: без изменений")
            continue
        block = f'{name} = [\n' + "".join(f'    "{r}",\n' for r in fixed) + "]"
        lines = text.splitlines()
        text = "\n".join(lines[:start - 1] + block.splitlines() + lines[end:]) + "\n"
        print(f"{name}: строк было {len(rows)}, стало {len(fixed)}")
    SRC.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()