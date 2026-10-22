"""Подробности попаданий: что движок реально засчитал по нашим выстрелам."""
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
maps = (sys.argv[3] if len(sys.argv) > 3 else "arena").split(",")
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
for mp in maps:
    mod = types.ModuleType("p"); mod.__file__ = me
    exec(compile(Path(me).read_text(), me, "exec"), mod.__dict__)
    brain = mod.program; brain.on_start({"tank": 0})
    LOG = mod.__dict__["LOG"]; LOG.clear()
    fo = load_program(opp)
    w = World.create(load_map(mp), BALANCE, seed=seed, max_seconds=60)
    mv = tankp.MapView(map_payload(w))
    fired, hits = {}, {}
    while not w.over:
        tick = w.tick
        oa, ob = build_both(w, BALANCE.default_budget_ms)
        acts = []
        for f, obs in ((brain.on_tick, oa), (fo, ob)):
            raw = f(tankp.Observation(obs, mv))
            acts.append(raw if raw.__class__ is tankp.Action else Action.clamp(raw))
        a, b = w.tanks
        for e in w.step(acts):
            if e["kind"] == "shot" and e.get("tank") == 0:
                fired[tick] = (a.x, a.y, a.turret, b.x, b.y, b.hull, math.hypot(a.x-b.x, a.y-b.y))
            elif e["kind"] == "hit" and e.get("shooter") == 0 and e.get("target") == 1:
                hits[tick] = (bool(e.get("ricochet")), e.get("theta"), e.get("face"))
    by = {rec[0]: rec for rec in LOG}
    print("==", mp, w.outcome)
    for t in sorted(fired):
        fx, fy, ft, ex, ey, eh, d = fired[t]
        rec = by.get(t)
        est = rec[5] if rec else None
        res = None
        for ht in range(t, t + 60):
            if ht in hits:
                res = hits[ht]
                break
        u = ft
        dh = math.degrees(math.atan2(math.sin(u - eh), math.cos(u - eh)))
        pred = f"p={rec[2]:.2f} br={rec[3]} смещ={rec[6]:+5.1f} лаг={rec[7]:+5.1f}" if rec else "нет прогноза"
        de = math.degrees(math.atan2(math.sin(u - est), math.cos(u - est))) if est is not None else 0.0
        out = (f"рикошет θ={res[1]:.0f} {res[2]}" if res and res[0] else
               f"пробило θ={res[1]:.0f} {res[2]}" if res else "мимо")
        print(f"  t={t:5d} d={d:5.1f} u-корпус_факт={dh:+6.1f}° u-корпус_оценка={de:+6.1f}°  {pred:16s} -> {out}")
