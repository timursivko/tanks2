"""Локальный дуэльный стенд: много боёв между программами, без песочницы.

Задача стенда — быстро сравнить своего танка с чужими (pool/, ai/, любой путь)
по сетке «карта x сид x сторона» и напечатать таблицу побед.

    python tools/duel.py --me my.tankp.py --vs mongoose,gyurza --maps arena,meadow --seeds 1
    python tools/duel.py --me my.tankp.py --vs pool/*/*.tankp.py --jobs 2

Каждый бой — свежий экземпляр программы (модуль исполняется заново, on_start
вызывается один раз), поэтому состояние не перетекает из боя в бой.
Бои раздаются пулу процессов: `--jobs N`.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
import types
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))

import tankp  # noqa: E402
from config import BALANCE  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.world import World  # noqa: E402

_ARENAS: dict = {}
_COUNTER = [0]


def resolve(key: str) -> Path:
    p = Path(key)
    if p.is_file():
        return p
    stem = key[:-6] if key.endswith(".tankp") else key
    cands = [ROOT / "pool" / key / f"{stem}.tankp.py",
             ROOT / "ai" / f"{stem}.tankp.py",
             ROOT / "examples" / f"{stem}.tankp.py",
             ROOT / "pool" / stem / f"{stem}.tankp.py"]
    for c in cands:
        if c.is_file():
            return c
    hits = sorted(glob.glob(str(ROOT / "pool" / "*" / f"{stem}.tankp.py")))
    if hits:
        return Path(hits[0])
    raise SystemExit(f"нет программы: {key}")


def load_fresh(path: Path):
    """Свежий экземпляр программы. Возвращает on_tick-функцию."""
    _COUNTER[0] += 1
    name = f"duel_prog_{_COUNTER[0]}"
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    src = path.read_text(encoding="utf-8")
    exec(compile(src, str(path), "exec"), mod.__dict__)  # noqa: S102
    prog = mod.__dict__.get("program")
    if prog is not None:
        if hasattr(prog, "on_start"):
            try:
                prog.on_start({"tank": 0, "map": None})
            except Exception:
                pass
        return prog.on_tick
    fn = mod.__dict__.get("on_tick")
    if fn is None:
        raise SystemExit(f"нет точки входа: {path}")
    st = mod.__dict__.get("on_start")
    if callable(st):
        try:
            st({})
        except Exception:
            pass
    return fn


def arena_for(map_name: str):
    a = _ARENAS.get(map_name)
    if a is None:
        a = load_map(map_name)
        _ARENAS[map_name] = a
    return a


def play(path_a: str, path_b: str, map_name: str, seed: int,
         max_seconds: int = 60, symmetric_seed: bool = False):
    """Один бой. Возвращает (result, ticks, hp_left, hp_right, err)."""
    arena = arena_for(map_name)
    fn_a = load_fresh(Path(path_a))
    fn_b = load_fresh(Path(path_b))
    world = World.create(arena, BALANCE, seed=seed, max_seconds=max_seconds)
    mv = tankp.MapView(map_payload(world))
    fns = (fn_a, fn_b)
    budget = BALANCE.default_budget_ms
    obs_cls = tankp.Observation
    sdk_action = tankp.Action
    build = build_both
    clamp = Action.clamp
    step = world.step
    acts = [None, None]
    err = 0
    while not world.over:
        oa, ob = build(world, budget)
        for i, raw_obs in ((0, oa), (1, ob)):
            try:
                raw = fns[i](obs_cls(raw_obs, mv))
            except Exception:
                err += 1
                raw = sdk_action()
            acts[i] = raw if raw.__class__ is sdk_action else clamp(raw)
        step(acts)
    a, b = world.tanks
    return (world.outcome, world.tick, round(a.hp, 3), round(b.hp, 3), err)


def work(job):
    path_a, path_b, map_name, seed, max_seconds = job
    t0 = time.perf_counter()
    try:
        out = play(path_a, path_b, map_name, seed, max_seconds)
    except Exception as exc:  # noqa: BLE001
        return (path_a, path_b, map_name, seed, "error", 0, 0.0, 0.0, str(exc),
                time.perf_counter() - t0)
    return (path_a, path_b, map_name, seed) + out + (time.perf_counter() - t0,)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--me", required=True, help="мой танк (путь или имя)")
    ap.add_argument("--vs", required=True, help="список через запятую")
    ap.add_argument("--maps", default="arena,corridor,colonnade")
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--one-side", action="store_true", help="только A=me, B=vrag")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    me = str(resolve(args.me))
    vs = [str(resolve(x.strip())) for x in args.vs.split(",") if x.strip()]
    maps = [m.strip() for m in args.maps.split(",") if m.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    jobs = []
    for foe in vs:
        for m in maps:
            for s in seeds:
                jobs.append((me, foe, m, s, args.seconds))
                if not args.one_side:
                    jobs.append((foe, me, m, s, args.seconds))

    t0 = time.perf_counter()
    results = []
    if args.jobs <= 1:
        for j in jobs:
            results.append(work(j))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            for r in ex.map(work, jobs, chunksize=1):
                results.append(r)
    dt = time.perf_counter() - t0

    stats: dict[str, list[int]] = {}
    total = [0, 0, 0]  # win, loss, draw (для me)
    errs = []
    for (pa, pb, m, s, outcome, ticks, hp_a, hp_b, err, secs) in results:
        mine = pa == me
        foe = Path(pb if mine else pa).parent.name
        st = stats.setdefault(foe, [0, 0, 0, 0, 0.0])
        st[4] += secs
        if outcome == "error":
            errs.append((pa, pb, m, s, err))
            continue
        my_win = (outcome == "a_win" and mine) or (outcome == "b_win" and not mine)
        my_loss = (outcome == "a_win" and not mine) or (outcome == "b_win" and mine)
        if my_win:
            st[0] += 1
            total[0] += 1
        elif my_loss:
            st[1] += 1
            total[1] += 1
        else:
            st[2] += 1
            total[2] += 1
        if err:
            errs.append((pa, pb, m, s, err))

    if not args.quiet:
        print(f"{'противник':<22} {'W':>4} {'L':>4} {'D':>4} {'win%':>7} {'боёв':>6} {'сек':>7}")
        for foe, st in sorted(stats.items(), key=lambda kv: -(kv[1][0] / max(1, sum(kv[1][:3])))):
            n = sum(st[:3])
            rate = 100.0 * st[0] / max(1, n)
            print(f"{foe:<22} {st[0]:>4} {st[1]:>4} {st[2]:>4} {rate:>6.1f}% {n:>6} {st[4]:>7.1f}")
        n = sum(total)
        rate = 100.0 * total[0] / max(1, n)
        print(f"{'ИТОГО':<22} {total[0]:>4} {total[1]:>4} {total[2]:>4} "
              f"{rate:>6.1f}% {n:>6} {dt:>7.1f}")
    if errs:
        print(f"ошибок/исключений: {len(errs)} (напр. {errs[:2]})")
    if args.json:
        Path(args.json).write_text(
            "\n".join(f"{pa}\t{pb}\t{m}\t{s}\t{o}\t{t}\t{ha}\t{hb}" for
                      (pa, pb, m, s, o, t, ha, hb, _e, _sec) in results),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
