"""Проверка: нативное ядро считает ровно то же, что Python-движок.

Две проверки:

1. **Потиковая** — бой со случайными командами (часто стреляем, тараним,
   катаемся по всем картам), на каждом тике сверяются наблюдения обоих танков
   и полный дамп состояния: позиции, скорости, HP, КД, паузы тарана, маска
   видимости, снаряды, внутреннее состояние MT19937. Любое расхождение в
   последнем бите — провал.
2. **Боевая** — реальные программы из ``ai/`` и ``pool/`` играют нативной
   версией, и итог сверяется с Python-версией: победитель, число тиков и вся
   статистика (выстрелы, попадания, рикошеты, тараны, урон, HP).

    python tools/parity.py                  # быстрый прогон (~1 минута)
    python tools/parity.py --long           # больше карт, сидов и тиков
    python tools/parity.py --battles 40     # сколько боёв сверять
    python tools/parity.py --reports A.md B.md   # сверить два отчёта турнира

Сверка отчётов нужна, чтобы показать главное свойство ускорения: два прогона
лидербоарда (медленный эталонный и быстрый нативный/многопроцессный) дают
один и тот же отчёт. Сравнивается всё, кроме трёх колонок времени решения
(«Пик think», «Ср. think», «Вне бюджета») и строки «Прогон»: это измерения
машины, они и в двух одинаковых прогонах не совпадают.
"""

from __future__ import annotations

import argparse
import itertools
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))
sys.path.insert(0, str(ROOT / "tools"))

from config import BALANCE  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.native import NativeWorld, available, load_error  # noqa: E402
from engine.native.api import _py_state  # noqa: E402
from engine.observation import build_both  # noqa: E402
from engine.world import World  # noqa: E402


def compare_tickwise(maps: list[str], seeds: list[int], ticks: int,
                     fire_rate: float = 0.35) -> int:
    """Сверяет состояние каждый тик. Возвращает число проверенных тиков."""
    checked = 0
    for name in maps:
        arena = load_map(name)
        for seed in seeds:
            py = World.create(arena, BALANCE, seed=seed, max_seconds=60)
            nat = NativeWorld(arena, BALANCE, seed=seed, max_seconds=60)
            rng = random.Random(f"{name}:{seed}")
            for step in range(ticks):
                acts = [Action(rng.uniform(-1, 1), rng.uniform(-1, 1),
                               rng.uniform(-1, 1), rng.random() < fire_rate)
                        for _ in range(2)]
                if build_both(py, 10.0) != nat.observations(10.0):
                    raise SystemExit(
                        f"РАСХОЖДЕНИЕ наблюдений: {name} seed={seed} "
                        f"тик={py.tick}")
                clamped = [Action.clamp(acts[0]), Action.clamp(acts[1])]
                py.step(clamped)
                nat.step(clamped)
                got = nat.state()
                want = _py_state(py)
                if want != got:
                    for i, (a, b) in enumerate(zip(want, got)):
                        if a != b:
                            raise SystemExit(
                                f"РАСХОЖДЕНИЕ состояния: {name} seed={seed} "
                                f"тик={py.tick} поле #{i}: py={a!r} nat={b!r}")
                    raise SystemExit(
                        f"РАСХОЖДЕНИЕ длины состояния: {name} seed={seed} "
                        f"тик={py.tick} {len(want)} != {len(got)}")
                checked += 1
                if py.over:
                    break
            nat.close()
    return checked


def compare_battles(n: int, maps: list[str], seconds: int) -> int:
    """Сверяет итоги реальных боёв: программы из ai/ и pool/."""
    from battle.kernel import (load_program, run_battle_native,  # noqa: E402
                               run_battle_py)
    from leaderboard import discover  # noqa: E402

    fighters, _ = discover()
    pairs = list(itertools.combinations(fighters, 2))
    random.Random(4).shuffle(pairs)
    arenas = {m: load_map(m) for m in maps}
    ok = 0
    for i in range(min(n, len(pairs))):
        a, b = pairs[i]
        name = maps[i % len(maps)]
        seed = (i % 3) + 1
        wa, sa = run_battle_py(load_program(str(a.path)),
                               load_program(str(b.path)),
                               arenas[name], seed, BALANCE, seconds)
        wb, sb = run_battle_native(load_program(str(a.path)),
                                   load_program(str(b.path)),
                                   arenas[name], seed, BALANCE, seconds)
        keys = ("shots", "hits", "ric", "ric_taken", "rams", "dealt", "taken",
                "hp_left")
        for side in (0, 1):
            for k in keys:
                if sa[side][k] != sb[side][k]:
                    raise SystemExit(
                        f"РАСХОЖДЕНИЕ статистики: {a.label} vs {b.label} "
                        f"карта {name} seed={seed} танк {side} {k}: "
                        f"py={sa[side][k]} nat={sb[side][k]}")
        if wa.tick != wb.tick or wa.outcome != wb.outcome:
            raise SystemExit(
                f"РАСХОЖДЕНИЕ итога: {a.label} vs {b.label} карта {name} "
                f"seed={seed}: py={wa.tick}/{wa.outcome} "
                f"nat={wb.tick}/{wb.outcome}")
        ok += 1
        print(f"  [{ok}/{n}] {a.label} vs {b.label} на {name}: "
              f"{wa.tick} тиков, {wa.outcome} — совпало", flush=True)
    return ok


#: Колонки отчёта, которые являются измерением, а не результатом турнира.
TIMING_HEADER = "Пик think, мс"
TIMING_COLUMNS = 3


def normalize_report(path: str | Path) -> list[str]:
    """Отчёт без колонок времени решения и без строки «Прогон».

    Всё остальное — места, очки, счёт друг против друга, матрицы и вся
    статистика боя — обязано совпадать у любых двух прогонов.
    """
    out: list[str] = []
    in_stats = False
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw
        if line.startswith("_Сгенерировано "):
            out.append("_Сгенерировано (время генерации не сравнивается)._")
            continue
        if line.startswith("| Прогон |"):
            out.append("| Прогон | (время не сравнивается) |")
            continue
        if line.startswith("| Процессов |"):
            # сколько было процессов — свойство прогона, а не турнира
            continue
        if line.startswith("- Время решения ("):
            # пояснение к тайминговым колонкам, появляется только при --jobs > 1
            continue
        if TIMING_HEADER in line:
            in_stats = True
        elif in_stats and not line.startswith("|"):
            in_stats = False
        if in_stats and line.startswith("|"):
            cells = line.split("|")
            if len(cells) > TIMING_COLUMNS + 2:
                line = "|".join(cells[:-TIMING_COLUMNS - 1]) + "|"
        out.append(line)
    return out


def compare_reports(a: str, b: str) -> int:
    """Сверяет два отчёта лидербоарда. Возвращает 0, если они эквивалентны."""
    left, right = normalize_report(a), normalize_report(b)
    if left == right:
        print(f"ОТЧЁТЫ СОВПАДАЮТ: {a} ≡ {b} "
              f"(кроме колонок времени решения)")
        return 0
    diffs = 0
    for i, (x, y) in enumerate(zip(left, right), 1):
        if x != y:
            diffs += 1
            if diffs <= 10:
                print(f"строка {i}:")
                print(f"  {a}: {x}")
                print(f"  {b}: {y}")
    if len(left) != len(right):
        print(f"разная длина: {len(left)} и {len(right)} строк")
    print(f"РАСХОЖДЕНИЙ: {diffs}")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parity")
    ap.add_argument("--maps", nargs="+", default=None)
    ap.add_argument("--seeds", nargs="+", type=int, default=None)
    ap.add_argument("--ticks", type=int, default=600)
    ap.add_argument("--battles", type=int, default=12)
    ap.add_argument("--long", action="store_true", help="дольше и шире")
    ap.add_argument("--no-battles", action="store_true")
    ap.add_argument("--reports", nargs=2, metavar=("A", "B"),
                    help="сверить два готовых отчёта лидербоарда")
    args = ap.parse_args(argv)

    if args.reports:
        return compare_reports(args.reports[0], args.reports[1])

    if not available():
        print(f"нативное ядро недоступно: {load_error()}", file=sys.stderr)
        return 2

    maps = args.maps or (["arena", "corridor", "colonnade", "fortress",
                          "meadow", "maze", "swamp", "rings", "crossroads"]
                         if args.long else
                         ["arena", "corridor", "colonnade", "fortress", "meadow"])
    seeds = args.seeds or ([1, 2, 3, 7, 11] if args.long else [1, 2, 3])
    ticks = args.ticks * (3 if args.long else 1)

    print(f"потиковая сверка: {len(maps)} карт × {len(seeds)} сидов × "
          f"{ticks} тиков", flush=True)
    checked = compare_tickwise(maps, seeds, ticks)
    print(f"  ок: {checked} тиков совпало до последнего бита")

    if not args.no_battles:
        print(f"сверка боёв: {args.battles} пар", flush=True)
        n = compare_battles(args.battles, maps[:3], 20 if args.long else 10)
        print(f"  ок: {n} боёв совпало")

    print("PARITY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
