"""Трасса дуэли: дистанция, ракурсы, выстрелы обеих сторон."""
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
me, opp, mp, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
every = float(sys.argv[5]) if len(sys.argv) > 5 else 0.5
fa, fb = load_program(me), load_program(opp)
w = World.create(load_map(mp), BALANCE, seed=seed, max_seconds=60)
mv = tankp.MapView(map_payload(w))
last = -9.0
while not w.over:
    oa, ob = build_both(w, BALANCE.default_budget_ms)
    acts = []
    for f, obs in ((fa, oa), (fb, ob)):
        raw = f(tankp.Observation(obs, mv))
        acts.append(raw if raw.__class__ is tankp.Action else Action.clamp(raw))
    ev = w.step(acts)
    a, b = w.tanks
    if a.alive and b.alive and w.tick / 60.0 - last >= every:
        last = w.tick / 60.0
        d = math.hypot(a.x - b.x, a.y - b.y)
        def rak(t, other):
            mx = other.x + math.cos(other.turret) * 26
            my = other.y + math.sin(other.turret) * 26
            arr = math.atan2(my - t.y, mx - t.x)
            return abs((t.hull - arr + math.pi) % (2*math.pi) - math.pi) * DEG
        print(f"t={w.tick/60:5.2f} d={d:5.1f} мой_ракурс={rak(a,b):5.1f} его_ракурс={rak(b,a):5.1f} "
              f"cd={a.cooldown:4.2f}/{b.cooldown:4.2f} hp={a.hp:4.1f}/{b.hp:4.1f}")
    for e in ev:
        if e["kind"] == "hit":
            who = "мы" if e.get("shooter") == 0 else "он"
            tgt = "он" if e.get("target") == 1 else "мы"
            print(f"   {who} -> {tgt}: {'рикошет' if e.get('ricochet') else 'ПРОБИЛ'} θ={e.get('theta',0):.0f} {e.get('face','')}")
print(w.outcome, "tick", w.tick)
