"""Сверка модели с движком: угол удара, который предсказал танк, и который засчитал движок."""
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
rows = []
for mp in maps:
    mod = types.ModuleType("p"); mod.__file__ = me
    exec(compile(Path(me).read_text(), me, "exec"), mod.__dict__)
    b = mod.program; b.on_start({"tank": 0}); LOG = mod.__dict__["LOG"]; LOG.clear()
    fo = load_program(opp)
    w = World.create(load_map(mp), BALANCE, seed=1, max_seconds=60)
    mv = tankp.MapView(map_payload(w))
    fired, hits = {}, {}
    while not w.over:
        t = w.tick
        oa, ob = build_both(w, BALANCE.default_budget_ms)
        acts = []
        for f, obs in ((b.on_tick, oa), (fo, ob)):
            raw = f(tankp.Observation(obs, mv))
            acts.append(raw if raw.__class__ is tankp.Action else Action.clamp(raw))
        for e in w.step(acts):
            if e["kind"] == "shot" and e.get("tank") == 0:
                fired[t] = True
            elif e["kind"] == "hit" and e.get("shooter") == 0:
                hits[t] = (bool(e.get("ricochet")), e.get("theta"), e.get("face"))
    by = {}
    for rec in LOG:
        by.setdefault(rec[0], []).append(rec)
    for t in sorted(fired):
        recs = by.get(t)
        if not recs:
            continue
        rec = recs[-1]
        res = None
        for ht in range(t, t + 60):
            if ht in hits:
                res = hits[ht]; break
        if res is None:
            rows.append((mp, t, rec[1], rec[2], rec[3], None, None, None))
        else:
            rows.append((mp, t, rec[1], rec[2], rec[3], res[0], res[1], res[2]))
for r in rows:
    mp, t, d, m, p, ric, th, fc = r
    out = "мимо" if ric is None else (f"рикошет θ={th:.0f} {fc}" if ric else f"ПРОБИЛ θ={th:.0f} {fc}")
    print(f"{mp:9s} t={t:4d} d={d:5.1f} запас={m:+6.1f}° p={p:.2f} -> {out}")
n = len(rows)
pen = sum(1 for r in rows if r[5] is False)
ric = sum(1 for r in rows if r[5] is True)
miss = sum(1 for r in rows if r[5] is None)
print(f"итого выстрелов {n}: пробило {pen}, рикошет {ric}, мимо {miss}")
