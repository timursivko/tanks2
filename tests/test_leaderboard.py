"""Детерминизм и ускоренные пути кругового лидербоарда."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from pathlib import Path

from config import BALANCE
from engine.grid import load_map
from tools import leaderboard as lb
from tools.probe import load_inproc


def _without_wall_clock(report: str) -> str:
    """Убирает только измерения wall-clock, которые недетерминированы по смыслу.

    Все места, очки, исходы, HP, урон, выстрелы, матрицы и остальные значения
    остаются в сравнении как есть, включая их форматирование.
    """
    out = []
    details = False
    for line in report.splitlines():
        if line.startswith("_Сгенерировано ") or line.startswith("| Прогон |"):
            continue
        if line == "## Подробная статистика":
            details = True
        elif details and line.startswith("## "):
            details = False
        if details and re.match(r"^\| \d+ \|", line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            # Пик think, средний think и вне бюджета — последние три колонки.
            cells[-3:] = ["<wall-clock>"] * 3
            line = "| " + " | ".join(cells) + " |"
        out.append(line)
    return "\n".join(out)


def _stable_result(result) -> tuple:
    index, (ticks, outcome, stats) = result
    stable_stats = [
        {key: value for key, value in side.items()
         if key not in {"think_total", "think_max", "over_budget"}}
        for side in stats
    ]
    return index, ticks, outcome, stable_stats


def test_parallel_and_serial_reports_match(monkeypatch, tmp_path: Path, capsys):
    fighters, _ = lb.discover()
    fighters = fighters[:5]
    monkeypatch.setattr(lb, "discover", lambda: (fighters, []))
    common = ["--maps", "arena", "--seeds", "1", "2", "--seconds", "5"]
    serial_path = tmp_path / "serial.md"
    parallel_path = tmp_path / "parallel.md"

    assert lb.main([*common, "--jobs", "1", "--out", str(serial_path)]) == 0
    assert lb.main([*common, "--jobs", "2", "--out", str(parallel_path)]) == 0
    capsys.readouterr()

    assert _without_wall_clock(parallel_path.read_text(encoding="utf-8")) == \
        _without_wall_clock(serial_path.read_text(encoding="utf-8"))


def test_spawn_pool_preserves_order_and_battle_results():
    fighters, _ = lb.discover()
    a, b = fighters[:2]
    matches = [(a, b, "arena", seed, home)
               for seed in (1, 2) for home in (0, 1)]
    arenas = {"arena": load_map("arena")}
    bal = replace(BALANCE, max_seconds=5)

    serial = list(lb.iter_match_results(matches, arenas, bal, 5, jobs=1))
    parallel = list(lb.iter_match_results(
        matches, arenas, bal, 5, jobs=2, start_method="spawn"))

    assert [index for index, _ in parallel] == list(range(len(matches)))
    assert [_stable_result(row) for row in parallel] == \
        [_stable_result(row) for row in serial]


def test_compile_cache_never_reuses_program_state(tmp_path: Path):
    path = tmp_path / "stateful.tankp.py"
    source = """#!TANKP 1
from tankp import Action

class Brain:
    def on_start(self, ctx):
        self.calls = 0

    def on_tick(self, o):
        self.calls += 1
        return Action(drive=self.calls / 10.0)

program = Brain()
"""
    path.write_text(source, encoding="utf-8")
    first = load_inproc(str(path), cache=True)
    second = load_inproc(str(path), cache=True)

    assert first(None).drive == 0.1
    assert first(None).drive == 0.2
    # Другой бой начинается с нуля, хотя code object взят из кэша.
    assert second(None).drive == 0.1

    # Исходник входит в ключ кэша: изменение файла не возвращает старый код.
    path.write_text(source.replace("self.calls / 10.0", "0.25"), encoding="utf-8")
    changed = load_inproc(str(path), cache=True)
    assert changed(None).drive == 0.25


def test_expired_deadline_does_not_start_another_match(monkeypatch):
    fighters, _ = lb.discover()
    a, b = fighters[:2]
    matches = [(a, b, "arena", 1, 0)]

    def unexpected(*args, **kwargs):
        raise AssertionError("бой запущен после истечения deadline")

    monkeypatch.setattr(lb, "_run_match", unexpected)
    monkeypatch.setattr(lb.mp, "get_context", unexpected)
    rows = list(lb.iter_match_results(
        matches, {"arena": load_map("arena")}, BALANCE, 60,
        jobs=2, deadline_at=time.perf_counter() - 1.0))
    assert rows == []
