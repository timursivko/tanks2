"""Отладочный трассировщик: один бой, события по секундам."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))

import tankp
from engine.action import Action
from engine.grid import load_map
from engine.observation import build_both, map_payload
from engine.world import World
from config import BALANCE
import importlib.util


def load_fresh(path, seed, tank):
    p = Path(path)
    m = importlib.util.module_from_spec(importlib.util.spec_from_file_location(
        f"tk_{p.stem}_{abs(hash(str(p))) % 100000}_{seed}_{tank}", p))
    exec(compile(p.read_text(encoding="utf-8"), str(p), "exec"), m.__dict__)
    prog = m.__dict__.get("program")
    if prog is not None and hasattr(prog, "on_start"):
        prog.on_start({"tank": tank, "seed": seed})
        return prog.on_tick
    return m.__dict__.get("on_tick")


def main():
    a_key = sys.argv[1]
    b_key = sys.argv[2]
    map_name = sys.argv[3] if len(sys.argv) > 3 else "arena"
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    fa, fb = load_fresh(a_key, seed, 0), load_fresh(b_key, seed, 1)
    arena = load_map(map_name)
    w = World.create(arena, BALANCE, seed=seed)
    mv = tankp.MapView(map_payload(w))
    fns = (fa, fb)
    clamp = Action.clamp
    acts = [None, None]
    last_sec = -1
    shots = [0, 0]
    import math

    def line(t):
        ta, tb = w.tanks
        d = math.hypot(ta.x - tb.x, ta.y - tb.y)
        A = math.atan2(tb.y - ta.y, tb.x - ta.x)
        Ae = math.atan2(ta.y - tb.y, ta.x - tb.x)
        ba = math.degrees(abs(wrap_(ta.hull - A)))
        bb = math.degrees(abs(wrap_(tb.hull - (A + math.pi))))
        print(f"t={t:5.1f} hp={ta.hp:4.1f}/{tb.hp:4.1f} d={d:6.0f} "
              f"mybeta={ba:5.1f} foebeta={bb:5.1f} cd={ta.cooldown:.2f}/{tb.cooldown:.2f} "
              f"shots={shots[0]}/{shots[1]}")

    def wrap_(a):
        a = (a + math.pi) % (2 * math.pi)
        return a - math.pi

    while not w.over:
        oa, ob = build_both(w, BALANCE.default_budget_ms)
        for i in (0, 1):
            obs = oa if i == 0 else ob
            raw = fns[i](tankp.Observation(obs, mv))
            acts[i] = raw if raw.__class__ is tankp.Action else clamp(raw)
            if acts[i].fire:
                shots[i] += 1
        w.step(acts)
        t = w.tick * w.dt
        if int(t) > last_sec and int(t) % 3 == 0:
            last_sec = int(t)
            line(t)
    ta, tb = w.tanks
    print(f"ФИНАЛ: A hp={ta.hp} shots={ta.shots} hits={ta.hits} ric={ta.ricochets} "
          f"ric_taken={ta.ricochets_taken} rams={ta.rams} | "
          f"B hp={tb.hp} shots={tb.shots} hits={tb.hits} ric={tb.ricochets} "
          f"ric_taken={tb.ricochets_taken} rams={tb.rams} | t={w.tick * w.dt:.1f}s")


if __name__ == "__main__":
    main()
