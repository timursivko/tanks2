"""Отчёт турнира: что в нём зависит от прогона, а что нет.

Колонки времени решения меряются wall-clock, поэтому под нагрузкой
(``--jobs`` > 1) они завышены, а «Вне бюджета» показывает конкуренцию за ядра,
а не медленные программы. Чтобы читатель не принял это за свойство турнира,
отчёт честно помечает такие прогоны — и `tools/parity.py` эти пометки
игнорирует при сверке двух отчётов.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))
sys.path.insert(0, str(ROOT / "tools"))

from leaderboard import Fighter, Record, build_report  # noqa: E402
from parity import normalize_report  # noqa: E402


def _fighter(name: str) -> Fighter:
    return Fighter(key=f"ai/{name}.tankp.py", path=ROOT / "ai" / f"{name}.tankp.py",
                   label=name, name=name, author="—", group="Эталон")


def _report(tmp_path: Path, jobs: int) -> Path:
    fighters = [_fighter("01_chaser"), _fighter("02_flanker")]
    rec = {f.key: Record() for f in fighters}
    for f in fighters:
        r = rec[f.key]
        r.games, r.wins, r.losses = 6, 3, 3
        r.dealt, r.taken, r.hp_left, r.ticks = 30, 30, 30, 600
    text = build_report(fighters, rec, {}, maps=["arena"], seeds=[1],
                        max_seconds=60, done=6, planned=6, elapsed=12.5,
                        copies=[], mrec={}, jobs=jobs)
    out = tmp_path / f"board_{jobs}.md"
    out.write_text(text, encoding="utf-8")
    return out


def test_speed_is_per_battle(tmp_path: Path) -> None:
    """Скорость печатается на бой: только она сравнима между машинами."""
    text = _report(tmp_path, jobs=1).read_text(encoding="utf-8")
    assert "| Скорость | 2083 мс на бой" in text
    assert "сравнимо между машинами" in text


def test_parity_ignores_speed(tmp_path: Path) -> None:
    """Строка скорости (и пояснение про «Прогон») в сверке не участвуют."""
    norm = normalize_report(_report(tmp_path, jobs=1))
    assert not [x for x in norm if x.startswith("| Скорость |")]


def test_parallel_run_is_marked(tmp_path: Path) -> None:
    text = _report(tmp_path, jobs=4).read_text(encoding="utf-8")
    assert "| Процессов | 4 |" in text
    assert "Время решения (`Пик think`, `Ср. think`, `Вне бюджета`) мерилось" in text
    assert "--jobs 1" in text


def test_sequential_run_has_no_such_notes(tmp_path: Path) -> None:
    text = _report(tmp_path, jobs=1).read_text(encoding="utf-8")
    assert "| Процессов |" not in text
    assert "Время решения (`Пик think`" not in text


def test_parity_ignores_run_metadata(tmp_path: Path) -> None:
    """Отчёты параллельного и последовательного прогона сверяются как равные."""
    a = _report(tmp_path, jobs=1)
    b = _report(tmp_path, jobs=4)
    norm_a, norm_b = normalize_report(a), normalize_report(b)
    # различие только в строке «Прогон» (время) — нормализуется
    assert [x for x in norm_a if x.startswith("| Прогон")] == \
           [x for x in norm_b if x.startswith("| Прогон")]
    assert "| Процессов | 4 |" not in norm_b
    assert not [x for x in norm_b if x.startswith("- Время решения (")]
    assert [x for x in norm_a if not x.startswith("| Прогон")] == \
           [x for x in norm_b if not x.startswith("| Прогон")]
