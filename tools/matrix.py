"""Матрица прогонов: все пары ИИ на всех картах, без процессов.

Нужен для быстрой проверки баланса и отсутствия падений::

    python tools/matrix.py
    python tools/matrix.py --maps arena --seeds 1 2 3

Полный прогон занимает минуты, поэтому есть ``--deadline`` — столько секунд
wall-clock можно потратить на бои, а потом печатается то, что успело::

    python tools/matrix.py --deadline 20
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
from engine.observation import build_observation, map_payload  # noqa: E402
from engine.world import World  # noqa: E402
from probe import load_inproc  # noqa: E402


def run_one(fn_a, fn_b, map_name: str, seed: int, bal=BALANCE):
    world = World.create(load_map(map_name), bal, seed=seed)
    mv = tankp.MapView(map_payload(world))
    fns = [fn_a, fn_b]
    while not world.over:
        acts = []
        for i in range(2):
            obs = tankp.Observation(build_observation(world, i, bal.default_budget_ms), mv)
            acts.append(Action.clamp(fns[i](obs)))
        world.step(acts)
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
        i = args.index("--seeds")
        seeds = [int(x) for x in args[i + 1:i + 4]]
    if "--deadline" in args:
        deadline = time.time() + float(args[args.index("--deadline") + 1])
    if "--seconds" in args:
        seconds = float(args[args.index("--seconds") + 1])
    bal = replace(BALANCE, max_seconds=int(seconds)) if seconds else BALANCE

    fns = {k: load_inproc(k) for k in keys}
    wins = {k: 0 for k in keys}
    draws = 0
    total = 0
    pairs = list(itertools.combinations(keys, 2))
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
        world = run_one(fns[a], fns[b], mp, seed, bal)
        total += 1
        if world.outcome == "draw":
            draws += 1
        else:
            winner = a if world.outcome == "a_win" else b
            wins[winner] += 1
        rows.append((a, b, mp, seed, world.outcome, round(world.tick / 60.0, 1)))

    print(f"пар: {total}, ничьих: {draws} ({draws / max(1, total):.0%})")
    print("победы:")
    for k in sorted(keys, key=lambda x: -wins[x]):
        print(f"  {k:<12} {wins[k]:>3}  ({wins[k] / max(1, total - draws):.0%} решающих)")
    if "-v" in args:
        print("подробно:")
        for r in rows:
            print("  ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())