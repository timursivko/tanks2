"""Разбор одного боя: дистанция, выстрелы, попадания, рикошеты во времени.

    python tools/diag.py shrike mongoose --map arena --seed 1
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))
sys.path.insert(0, str(ROOT / "tools"))

import duel  # noqa: E402
import tankp  # noqa: E402
from config import BALANCE  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.world import World  # noqa: E402


def main() -> int:
    a_key = sys.argv[1] if len(sys.argv) > 1 else "shrike"
    b_key = sys.argv[2] if len(sys.argv) > 2 else "mongoose"
    map_name = sys.argv[sys.argv.index("--map") + 1] if "--map" in sys.argv else "arena"
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 1
    verbose = "--v" in sys.argv

    pa, pb = duel.resolve(a_key), duel.resolve(b_key)
    fn_a, fn_b = duel.load_fresh(pa), duel.load_fresh(pb)
    world = World.create(duel.arena_for(map_name), BALANCE, seed=seed,
                         max_seconds=60)
    mv = tankp.MapView(map_payload(world))
    fns = (fn_a, fn_b)
    budget = BALANCE.default_budget_ms
    acts = [None, None]
    dists = []
    log = []
    nshots = [0, 0]
    nhits = [0, 0]
    nrico = [0, 0]
    nhit_taken = [0, 0]
    fire_dec = [0, 0]
    while not world.over:
        oa, ob = build_both(world, budget)
        for i, ro in ((0, oa), (1, ob)):
            try:
                raw = fns[i](tankp.Observation(ro, mv))
            except Exception as exc:  # noqa: BLE001
                raw = tankp.Action()
                log.append((world.tick, f"EXC {i} {exc!r}"))
            acts[i] = raw if raw.__class__ is tankp.Action else Action.clamp(raw)
            if acts[i].fire:
                fire_dec[i] += 1
        a, b = world.tanks
        d = ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5
        evs = world.step(acts)
        for ev in evs:
            k = ev["kind"]
            if k == "shot":
                nshots[ev["tank"]] += 1
                log.append((world.tick, f"shot {ev['tank']} d={d:.0f}"))
            elif k == "hit":
                sh, tg = ev["shooter"], ev["target"]
                if ev.get("ricochet"):
                    nrico[sh] += 1
                    log.append((world.tick, f"RICO {sh}->{tg} face={ev['face']} th={ev['theta']:.0f} d={d:.0f}"))
                else:
                    nhits[sh] += 1
                    nhit_taken[tg] += 1
                    log.append((world.tick, f"HIT  {sh}->{tg} dmg={ev['damage']} face={ev['face']} th={ev['theta']:.0f} d={d:.0f} hp={world.tanks[tg].hp:.0f}"))
        dists.append(d)
    a, b = world.tanks
    print(f"{a_key} (A) vs {b_key} (B) map={map_name} seed={seed}: {world.outcome} "
          f"ticks={world.tick} ({world.tick / 60:.1f}s) hp {a.hp:.0f}:{b.hp:.0f}")
    print(f"  выстрелов A/B: {nshots[0]}/{nshots[1]}  попаданий: {nhits[0]}/{nhits[1]}"
          f"  рикошетов: {nrico[0]}/{nrico[1]}  решений с огнём: {fire_dec[0]}/{fire_dec[1]}")
    if dists:
        near = sum(1 for d in dists if d < 60) / len(dists)
        near2 = sum(1 for d in dists if d < 130) / len(dists)
        print(f"  дистанция: средняя {sum(dists) / len(dists):.0f}, "
              f"доля <60px {100 * near:.0f}%, <130px {100 * near2:.0f}%")
    if verbose:
        for t, line in log:
            print(f"  {t / 60:6.2f}s {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
