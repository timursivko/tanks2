"""Доля тиков в мёртвой зоне 30..40° к линии прихода снаряда."""
import sys, math
ROOT = "/home/user/tanks2"
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/engine/sdk")
import tankp
from config import BALANCE
from engine.action import Action
from engine.grid import load_map
from engine.observation import build_both, map_payload
from engine.world import World
from battle.kernel import load_program
DEG = 180.0 / math.pi
me, opp, maps = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
res = {}
for mp in maps:
    fa, fb = load_program(me), load_program(opp)
    w = World.create(load_map(mp), BALANCE, seed=seed, max_seconds=60)
    mv = tankp.MapView(map_payload(w))
    while not w.over:
        oa, ob = build_both(w, BALANCE.default_budget_ms)
        acts = []
        for f, obs in ((fa, oa), (fb, ob)):
            raw = f(tankp.Observation(obs, mv))
            acts.append(raw if raw.__class__ is tankp.Action else Action.clamp(raw))
        w.step(acts)
        a, b = w.tanks
        if not (a.alive and b.alive):
            break
        d = math.hypot(a.x - b.x, a.y - b.y)
        if d > 260:
            continue
        for tag, t, other in (("A", a, b), ("B", b, a)):
            mx = other.x + math.cos(other.turret) * 26.0
            my = other.y + math.sin(other.turret) * 26.0
            arr = math.atan2(my - t.y, mx - t.x)
            rel = abs((t.hull - arr + math.pi) % (2.0 * math.pi) - math.pi) * DEG
            r = res.setdefault(tag, [0, 0, 0, 0.0])
            r[0] += 1
            r[1] += 1 if 30.0 <= rel <= 40.0 else 0
            r[2] += 1 if rel < 30.0 else 0
            r[3] += rel
for tag in sorted(res):
    n, inside, low, srel = res[tag]
    print(f"{tag}: в полосе={100.0*inside/n:3.0f}% ниже30={100.0*low/n:3.0f}% средний={srel/n:5.1f}°")
