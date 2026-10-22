"""Быстрый турнир между .tankp.py без песочницы и без записи реплеев.

Нужен, чтобы сравнивать своего танка с пулом на десятках боёв за минуты::

    python tools/arena_eval.py mine.tankp.py --vs pool --maps arena,corridor --seeds 1,2
    python tools/arena_eval.py mine.tankp.py --vs pool/arena-gyurza/gyurza.tankp.py -n 4

Каждый бой играется в отдельном процессе (``multiprocessing``), программы
компилируются заново на каждый бой: состояние из прошлого боя не переносится,
как и в настоящей песочнице. Каждая пара карта×сид играется дважды — танк
побывает на обоих спавнах, поэтому перекос карты не выдаётся за силу танка.

Счёт: победа 1.0, ничья 0.5, поражение 0. Отдельно печатается среднее время
решения — реальный бой живёт с бюджетом 10 мс на тик, и агент, который думает
дольше, в песочнице будет получать пустые тики.
"""

from __future__ import annotations

import argparse
import glob
import itertools
import sys
import time
import traceback
import types
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))

DEFAULT_MAPS = ["arena", "corridor", "colonnade", "fortress", "meadow",
                "cross", "crater", "pillar", "rings", "zigzag"]


# --- загрузка программы -------------------------------------------------------

def load_program(path: str, tank: int, map_name: str, seed: int):
    """Компилирует программу заново и возвращает её ``on_tick``."""
    src = Path(path).read_text(encoding="utf-8")
    mod = types.ModuleType("eval_program")
    mod.__file__ = str(Path(path).resolve())
    # Соседние модули пакета: у одиночного .tankp.py рядом лежат чужие файлы,
    # поэтому каталог в sys.path не кладём — он бы смешал импорты двух танков.
    exec(compile(src, str(path), "exec"), mod.__dict__)  # noqa: S102
    ctx = {"tank": tank, "map": map_name, "seed": seed, "budget_ms": 10.0,
           "hp": 10.0, "reload": 1.0}
    prog = mod.__dict__.get("program")
    if prog is not None and hasattr(prog, "on_tick"):
        if hasattr(prog, "on_start"):
            prog.on_start(ctx)
        return prog.on_tick
    fn = mod.__dict__.get("on_tick")
    if fn is None:
        raise SystemExit(f"{path}: нет ни program, ни on_tick")
    start = mod.__dict__.get("on_start")
    if callable(start):
        start(ctx)
    return fn


# --- один бой ----------------------------------------------------------------

def play(spec: tuple) -> dict:
    """полный бой; spec = (path_a, path_b, map_name, seed)"""
    import tankp
    from config import BALANCE
    from engine.action import Action
    from engine.grid import load_map
    from engine.observation import build_both, map_payload
    from engine.world import World

    path_a, path_b, map_name, seed = spec
    out = {"a": path_a, "b": path_b, "map": map_name, "seed": seed,
           "outcome": "error", "ticks": 0, "error": "", "ms": [0.0, 0.0],
           "hp": [0.0, 0.0], "hits": [0, 0], "shots": [0, 0]}
    try:
        fns = (load_program(path_a, 0, map_name, seed),
               load_program(path_b, 1, map_name, seed))
    except Exception:                                    # noqa: BLE001
        out["error"] = traceback.format_exc(limit=6)
        return out

    arena = load_map(map_name)
    world = World.create(arena, BALANCE, seed=seed,
                         names=(Path(path_a).stem, Path(path_b).stem))
    mv = tankp.MapView(map_payload(world))
    obs_cls = tankp.Observation
    acts = [None, None]
    total = [0.0, 0.0]
    worst = [0.0, 0.0]
    perf = time.perf_counter
    try:
        while not world.over:
            oa, ob = build_both(world, BALANCE.default_budget_ms)
            for i, raw_obs in ((0, oa), (1, ob)):
                t0 = perf()
                raw = fns[i](obs_cls(raw_obs, mv))
                dt = (perf() - t0) * 1000.0
                total[i] += dt
                if dt > worst[i]:
                    worst[i] = dt
                acts[i] = raw if raw.__class__ is tankp.Action else Action.clamp(raw)
            world.step(acts)
    except Exception:                                    # noqa: BLE001
        out["error"] = traceback.format_exc(limit=6)
        out.update(outcome="error", ticks=world.tick)
        return out

    ticks = max(1, world.tick)
    out.update(
        outcome=world.outcome, ticks=world.tick,
        ms=[round(total[i] / ticks, 3) for i in range(2)],
        worst_ms=[round(worst[i], 3) for i in range(2)],
        hp=[round(t.hp, 2) for t in world.tanks],
        hits=[t.hits for t in world.tanks],
        shots=[t.shots for t in world.tanks],
        reason=world.end_reason,
    )
    return out


# --- список противников -------------------------------------------------------

def expand(targets: list[str]) -> list[str]:
    paths: list[str] = []
    for t in targets:
        if t == "pool":
            found = sorted(glob.glob(str(ROOT / "pool" / "*" / "*.tankp.py")))
        elif t == "ai":
            found = sorted(glob.glob(str(ROOT / "ai" / "*.tankp.py")))
        elif any(c in t for c in "*?["):
            found = sorted(glob.glob(t))
        else:
            found = [t]
        for p in found:
            if p not in paths:
                paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mine")
    ap.add_argument("--vs", nargs="+", default=["pool"])
    ap.add_argument("--maps", default=",".join(DEFAULT_MAPS))
    ap.add_argument("--seeds", default="1,2")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--max-seconds", type=int, default=None,
                    help="ограничение длительности боя (по умолчанию из конфига)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    maps = [m for m in args.maps.split(",") if m]
    seeds = [int(x) for x in args.seeds.split(",") if x]
    foes = expand(args.vs)
    mine = str(Path(args.mine))

    if args.max_seconds:
        import config
        object.__setattr__(config.BALANCE, "max_seconds", args.max_seconds)

    specs = []
    for foe in foes:
        for map_name, seed in itertools.product(maps, seeds):
            specs.append((mine, foe, map_name, seed))
            specs.append((foe, mine, map_name, seed))

    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for res in pool.map(play, specs, chunksize=1):
            results.append(res)

    # --- сводка по противникам ---
    by_foe: dict[str, list[dict]] = {}
    for res in results:
        mine_is_a = res["a"] == mine
        foe = res["b"] if mine_is_a else res["a"]
        by_foe.setdefault(foe, []).append(res)

    total_score = 0.0
    total_games = 0
    print(f"\n{'противник':<46} {'игр':>4} {'побед':>6} {'ничьих':>7} {'пораж':>6} {'счёт':>7}  ХП+/-  мс/тик  max")
    for foe in sorted(by_foe, key=lambda f: -len(by_foe[f])):
        rows = by_foe[foe]
        w = d = l = err = 0
        hp_for = hp_against = 0.0
        ms_sum = 0.0
        ms_n = 0
        worst = 0.0
        for res in rows:
            mine_is_a = res["a"] == mine
            if res["outcome"] == "error":
                err += 1
                if not args.quiet:
                    who = "мой" if mine_is_a else "чужой"
                    print(f"  ! ошибка ({who}) {res['map']}/{res['seed']}: "
                          f"{res['error'].strip().splitlines()[-1][:160]}")
                continue
            persp = "a" if mine_is_a else "b"
            oc = res["outcome"]
            if oc == "draw":
                d += 1
            elif (oc == "a_win") == mine_is_a:
                w += 1
            else:
                l += 1
            my_i = 0 if mine_is_a else 1
            hp_for += res["hp"][my_i]
            hp_against += res["hp"][1 - my_i]
            ms_sum += res["ms"][my_i]
            ms_n += 1
            worst = max(worst, res.get("worst_ms", [0, 0])[my_i])
        n = w + d + l
        score = (w + 0.5 * d) / n if n else 0.0
        total_score += w + 0.5 * d
        total_games += n
        name = Path(foe).parent.name + "/" + Path(foe).name if "/pool/" in foe else Path(foe).name
        print(f"{name:<46} {n:>4} {w:>6} {d:>7} {l:>6} {score:>6.0%}  "
              f"{hp_for / max(1, n):>3.1f}/{hp_against / max(1, n):<3.1f} "
              f"{ms_sum / max(1, ms_n):>6.2f} {worst:>5.1f}"
              + (f"  ошибок {err}" if err else ""))

    print(f"\nИТОГО: {total_score / max(1, total_games):.1%} "
          f"за {total_games} боёв ({time.time() - t0:.0f} c)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
