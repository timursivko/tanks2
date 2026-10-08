"""Турнирный прогон: свежие бои пар танков без процессов.

Каждый бой загружает программы заново (свежий on_start) — как в песочнице:
состояние не мигрирует из боя в бой.

    python tools/eval_pool.py mytank --maps arena,duel --seeds 1 2 3
    python tools/eval_pool.py mytank --against mongoose,gyurza --both-sides
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))

import tankp  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.world import World  # noqa: E402
from config import BALANCE  # noqa: E402
import importlib.util  # noqa: E402

_ARENAS: dict = {}


def load_arena(name):
    a = _ARENAS.get(name)
    if a is None:
        a = load_map(name)
        _ARENAS[name] = a
    return a


def load_fresh(path: str):
    """Загружает .tankp.py как отдельный модуль — свежий экземпляр."""
    p = Path(path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_file_location(
        f"tankp_prog_{p.stem}_{id(p)}", p))
    code = compile(p.read_text(encoding="utf-8"), str(p), "exec")
    exec(code, mod.__dict__)  # noqa: S102
    prog = mod.__dict__.get("program")
    if prog is not None and hasattr(prog, "on_start"):
        prog.on_start({"tank": 0, "seed": 0, "time": 0.0})
        return prog.on_tick
    fn = mod.__dict__.get("on_tick")
    if fn is None:
        raise SystemExit(f"нет точки входа в {path}")
    return fn


def run_one(fn_a, fn_b, map_name: str, seed: int, arena=None, max_seconds=None):
    if arena is None:
        arena = load_arena(map_name)
    bal = BALANCE
    if max_seconds is not None and max_seconds != bal.max_seconds:
        from dataclasses import replace
        bal = replace(bal, max_seconds=max_seconds)
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
    errors = []
    while not world.over:
        obs_a, obs_b = build(world, budget)
        for i in (0, 1):
            obs = obs_a if i == 0 else obs_b
            try:
                raw = fns[i](obs_cls(obs, mv))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tank{i}: {exc!r}")
                raw = None
            acts[i] = raw if (raw is not None and raw.__class__ is sdk_action) \
                else clamp(raw)
        step(acts)
    return world, errors


def result_of(world):
    """'A' | 'B' | '=' по исходу боя."""
    w = world
    if w.outcome == "a_win" if hasattr(w, "outcome") else False:
        pass
    # World хранит финал в replay-совместимых полях; разбираем по HP.
    hp_a, hp_b = w.tanks[0].hp, w.tanks[1].hp
    if hp_a > 0 and hp_b <= 0:
        return "A"
    if hp_b > 0 and hp_a <= 0:
        return "B"
    return "="


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    me_key = args[0]
    maps = ["arena", "duel", "fortress", "maze", "corridor", "crossroads",
            "colonnade", "spiral", "crater", "rings", "meadow", "pit",
            "sandbox", "swamp", "zigzag", "gate", "pillar", "cross"]
    seeds = [1, 2, 3]
    against = None
    both = "--both-sides" in args
    if "--maps" in args:
        maps = args[args.index("--maps") + 1].split(",")
    if "--seeds" in args:
        i = args.index("--seeds") + 1
        got = []
        while i < len(args) and args[i].replace(".", "", 1).isdigit():
            got.append(int(args[i]))
            i += 1
        seeds = got
    if "--against" in args:
        against = args[args.index("--against") + 1].split(",")

    def find(key):
        cand = Path(key)
        if cand.exists():
            return cand
        for base in (ROOT / "pool", ROOT / "ai"):
            hits = list(base.glob(f"**/{key}.tankp.py"))
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                hits.sort()
                return hits[0]
        raise SystemExit(f"не найден танк: {key}")

    me_path = find(me_key)
    if against is None:
        opps = [p for p in sorted((ROOT / "pool").glob("*/[a-z]*.tankp.py"))
                if p.parent.name not in ("README",) ]
    else:
        opps = [find(k) for k in against]
    print(f"мой танк: {me_path}")
    print(f"карты: {len(maps)}, сиды: {seeds}")

    score = {}   # opp -> [wins, losses, draws, timeouts]
    t0 = time.time()
    for opp in opps:
        name = opp.stem
        w = l = d = 0
        n = 0
        for m in maps:
            arena = load_arena(m)
            for s in seeds:
                pairs = [(me_path, opp), (opp, me_path)] if both else [(me_path, opp)]
                for pa, pb in pairs:
                    fa, fb = load_fresh(str(pa)), load_fresh(str(pb))
                    world, errs = run_one(fa, fb, m, s, arena)
                    res = result_of(world)
                    n += 1
                    # я всегда на стороне A в (me,opp) и B в (opp,me)
                    my = "A" if pa == me_path else "B"
                    if res == my:
                        w += 1
                    elif res == "=":
                        d += 1
                    else:
                        l += 1
                    if errs:
                        print(f"  !! {name} на {m} s{s}: {errs[:2]}")
        total = w + l + d
        wr = 100.0 * w / total if total else 0.0
        score[name] = (w, l, d, n)
        print(f"{name:14s} побед {w:3d}  пораж {l:3d}  ничьих {d:3d}   {wr:5.1f}%   ({n} боёв)")
    tw = sum(v[0] for v in score.values())
    tl = sum(v[1] for v in score.values())
    td = sum(v[2] for v in score.values())
    tn = sum(v[3] for v in score.values())
    wr = 100.0 * tw / (tw + tl) if (tw + tl) else 0.0
    print(f"ИТОГО: {tw} побед, {tl} пораж, {td} ничьих из {tn} боёв — "
          f"винрейт {wr:.1f}% (без ничьих), за {time.time() - t0:.0f} с")


if __name__ == "__main__":
    main()
