"""Сверка модели выстрела с движком: работаем в чистом мире, чтобы видеть события."""
import sys, math, types
ROOT = "/home/user/tanks2"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/engine/sdk")
import tankp
from config import BALANCE
from engine.action import Action
from engine.grid import load_map
from engine.observation import build_both, map_payload
from engine.world import World
from battle.kernel import load_program
from pathlib import Path

me, opp = sys.argv[1], sys.argv[2]
maps = (sys.argv[3] if len(sys.argv) > 3 else "arena,meadow,corridor").split(",")
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
buckets = {}
nshot = 0
for mp in maps:
    mod = types.ModuleType("p"); mod.__file__ = me
    exec(compile(Path(me).read_text(), me, "exec"), mod.__dict__)
    brain = mod.program; brain.on_start({"tank": 0})
    LOG = mod.__dict__["LOG"]; LOG.clear()
    fo = load_program(opp)
    w = World.create(load_map(mp), BALANCE, seed=seed, max_seconds=60)
    mv = tankp.MapView(map_payload(w))
    fired, hits = {}, []
    while not w.over:
        tick = w.tick
        oa, ob = build_both(w, BALANCE.default_budget_ms)
        acts = []
        for f, obs in ((brain.on_tick, oa), (fo, ob)):
            raw = f(tankp.Observation(obs, mv))
            acts.append(raw if raw.__class__ is tankp.Action else Action.clamp(raw))
        for e in w.step(acts):
            if e["kind"] == "shot" and e.get("tank") == 0:
                fired[tick] = True
            elif e["kind"] == "hit" and e.get("shooter") == 0 and e.get("target") == 1:
                hits.append((tick, bool(e.get("ricochet")), e.get("theta"), e.get("face"), e.get("x"), e.get("y")))
    by = {}
    for rec in LOG:
        by[rec[0]] = rec
    for t in sorted(fired):
        rec = by.get(t)
        if rec is None:
            continue
        _, d, p, br, barrel, ehull, ex, ey = rec
        pen = any(t - 2 <= ht <= t + 40 and not ric for ht, ric, th, fc, hx, hy in hits)
        ric = any(t - 2 <= ht <= t + 40 and ric for ht, ric, th, fc, hx, hy in hits)
        key = (br, round(p, 1))
        b = buckets.setdefault(key, [0, 0, 0, []])
        b[0] += 1
        b[1] += 1 if pen else 0
        b[2] += 1 if (not pen and not ric) else 0
        b[3].append(d)
        nshot += 1
for k in sorted(buckets):
    n, pen, miss, ds = buckets[k]
    br = {0: "центр", 1: "полоса/центр", 2: "полоса"}[k[0]]
    print(f"{br:13s} p={k[1]:.1f}: n={n:3d} пробило={pen:3d} ({100*pen/max(n,1):3.0f}%) "
          f"рикошет={n-pen-miss:3d} мимо={miss:3d} d_мед={sorted(ds)[len(ds)//2]:.0f}")
print("всего выстрелов", nshot)
