"""Матрица прогонов: все пары ИИ на всех картах, без процессов.

Нужен для быстрой проверки баланса и отсутствия падений::

    python tools/matrix.py
    python tools/matrix.py --maps arena --seeds 1 2 3

Полный прогон занимает минуты, поэтому есть ``--deadline`` — столько секунд
wall-clock можно потратить на бои, а потом печатается то, что успело::

    python tools/matrix.py --deadline 20

Почему без процессов (``multiprocessing``/``ProcessPoolExecutor``): скрипты
загружаются один раз на весь прогон и живут между боями. Загрузчик
(``probe.load_inproc``) вызывает ``on_start`` ровно один раз, и внутреннее
состояние программ переходит из боя в бой — исход зависит от порядка пар.
Если разложить бои по процессам, у каждой программы окажется своя история
состояния, и числа в отчёте поедут (ничьи, время боёв, распределение побед
считаются по-другому). Поэтому прогон остаётся однопоточным: ускоряется сам
счёт боя (см. engine/), а не раскладка по ядрам.
"""

from __future__ import annotations

import itertools
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))
sys.path.insert(0, str(ROOT / "tools"))

import tankp  # noqa: E402
from config import BALANCE  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.world import World  # noqa: E402
from probe import load_inproc  # noqa: E402

#: Загруженные арены по имени карты. Арена во время боя не меняется (движок
#: её только читает), а её сборка — разбор JSON и BFS «зазора» до стен —
#: самая дорогая часть подготовки. Держим по одной на карту: полный прогон
#: играет одну и ту же карту 63 раза.
_ARENAS: dict[str, object] = {}


def load_arena(map_name: str):
    """Арена карты из кэша (грузится один раз на прогон)."""
    arena = _ARENAS.get(map_name)
    if arena is None:
        arena = load_map(map_name)
        _ARENAS[map_name] = arena
    return arena


def run_one(fn_a, fn_b, map_name: str, seed: int, bal=BALANCE, arena=None):
    """Один бой без процессов. ``arena`` — уже загруженная карта.

    Горячий цикл написан «в лоб»: имена, которые нужны каждый тик
    (наблюдение, команда, шаг мира), берутся в локальные переменные, а
    список команд переиспользуется. Значения те же, что и при вызове
    через модули: это тот же код, только без повторного поиска имён.
    """
    if arena is None:
        arena = load_arena(map_name)
    world = World.create(arena, bal, seed=seed)
    mv = tankp.MapView(map_payload(world))
    fns = (fn_a, fn_b)
    budget = bal.default_budget_ms
    obs_cls = tankp.Observation
    sdk_action = tankp.Action
    build = build_both
    clamp = Action.clamp
    step = world.step
    acts = [None, None]
    while not world.over:
        # Оба наблюдения собираются до решений: мир между ними не меняется,
        # а округлённые поля каждого танка считаются один раз (build_both).
        obs_a, obs_b = build(world, budget)
        raw = fns[0](obs_cls(obs_a, mv))
        # Команда SDK уже нормализована ровно так же, как это делает
        # Action.clamp (диапазоны -1..1 и bool у fire), поэтому в обычном
        # случае она уходит в мир как есть — без копии в Action движка.
        # Любой другой ответ скрипта (кортеж, словарь, None, свой объект)
        # разбирается прежним путём.
        acts[0] = raw if raw.__class__ is sdk_action else clamp(raw)
        raw = fns[1](obs_cls(obs_b, mv))
        acts[1] = raw if raw.__class__ is sdk_action else clamp(raw)
        step(acts)
    return world


def main() -> int:
    maps = ["arena", "corridor", "colonnade", "fortress", "meadow"]
    seeds = [1, 2, 3]
    names = [p.stem for p in sorted((ROOT / "ai").glob("*.tankp.py"))]
    keys = [n.replace(".tankp", "") for n in names]

    args = sys.argv[1:]
    deadline = None
    seconds = None
    if "--maps" in args:
        maps = [args[args.index("--maps") + 1]]
    if "--seeds" in args:
        i = args.index("--seeds") + 1
        seeds = []
        # Флаг сразу после списка сидов («--seeds 1 -v») раньше попадал в
        # int() и ронял разбор: список заканчивается на первом аргументе,
        # который не является числом. Работавшие сочетания не меняются.
        while i < len(args) and len(seeds) < 3:
            try:
                seeds.append(int(args[i]))
            except ValueError:
                break
            i += 1
    if "--deadline" in args:
        deadline = time.time() + float(args[args.index("--deadline") + 1])
    if "--seconds" in args:
        seconds = float(args[args.index("--seconds") + 1])
    bal = replace(BALANCE, max_seconds=int(seconds)) if seconds else BALANCE

    fns = {k: load_inproc(k) for k in keys}
    arenas = {mp: load_arena(mp) for mp in maps}
    wins = {k: 0 for k in keys}
    draws = 0
    total = 0
    pairs = list(itertools.combinations(keys, 2))
    # Счёт каждой пары «a против b» (порядок пары — как в keys): победы a,
    # победы b, ничьи. Для таблицы «каждый с каждым» нужен именно он, а не
    # суммарные победы: они не говорят, кто кого обыграл.
    h2h = {(a, b): [0, 0, 0] for a, b in pairs}
    # Порядок «карта, сид, пара»: при --deadline успевают пройти все пары
    # хотя бы на одной карте, а не только первые программы против первых.
    matches = [(mp, seed, a, b)
               for mp in maps for seed in seeds for a, b in pairs]
    rows = []
    for mp, seed, a, b in matches:
        if deadline is not None and time.time() >= deadline:
            print(f"остановлено по --deadline: {total} из {len(matches)} боёв",
                  flush=True)
            break
        world = run_one(fns[a], fns[b], mp, seed, bal, arenas[mp])
        total += 1
        if world.outcome == "draw":
            draws += 1
            h2h[(a, b)][2] += 1
        else:
            winner = a if world.outcome == "a_win" else b
            wins[winner] += 1
            h2h[(a, b)][0 if winner == a else 1] += 1
        rows.append((a, b, mp, seed, world.outcome, round(world.tick / 60.0, 1)))

    print(f"пар: {total}, ничьих: {draws} ({draws / max(1, total):.0%})")
    print("победы:")
    for k in sorted(keys, key=lambda x: -wins[x]):
        print(f"  {k:<12} {wins[k]:>3}  ({wins[k] / max(1, total - draws):.0%} решающих)")
    if total:
        # Каждая ячейка — счёт строки против столбца: победы-ничьи-поражения.
        pos = {k: i for i, k in enumerate(keys)}
        grid = {}
        for r in keys:
            for c in keys:
                if r == c:
                    grid[(r, c)] = "—"
                    continue
                a, b = (r, c) if pos[r] < pos[c] else (c, r)
                w, l, d = h2h[(a, b)]
                if r != a:
                    w, l = l, w
                grid[(r, c)] = f"{w}-{d}-{l}"
        width = max(len(s) for s in [*keys, *grid.values()])
        print("матрица (строка против столбца, победы-ничьи-поражения):")
        print(" " * (width + 1) + "".join(f"{k:>{width + 2}}" for k in keys))
        for r in keys:
            line = f"{r:<{width + 1}}"
            for c in keys:
                line += f"{grid[(r, c)]:>{width + 2}}"
            print(line)
    if "-v" in args:
        print("подробно:")
        for r in rows:
            print("  ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
