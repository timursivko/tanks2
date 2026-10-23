"""Где на самом деле «мёртвая зона» и где на самом деле стою я.

    python tools/safe_window.py pool/arena-a8803110/rosomakha.tankp.py \
        --vs pool/arena-b43521c5/bars.tankp.py --maps arena,fortress --seeds 1

Для каждого тика, когда линия от чужого дула до меня чистая, считается
настоящая геометрия «если бы он выстрелил сейчас»: луч из его
дула по его стволу, первая задеваемая грань моего корпуса и вердикт
``armor.resolve_impact``. Результат раскладывается по β — углу между моим
курсом и осью прихода снаряда (β берётся по модулю 180°, потому что корпус
симметричен).

Печатаются две вещи:

* **prob** — доля тиков, на которых выстрел врага ПРОБИЛ бы меня при данном
  β. Минимум этой кривой и есть настоящая мёртвая зона.
* **share** — где я реально стою: распределение β по тикам.

Если минимум prob и пик share не совпадают — агент держит ракурс мимо окна,
и это чинится одной константой (``DEAD_DEG``), а не перебором.
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'engine', 'sdk'))

import tankp  # noqa: E402
from battle.kernel import load_program  # noqa: E402
from config import BALANCE  # noqa: E402
from engine import armor  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.geometry import segment_obb  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.visibility import ray_grid_detailed  # noqa: E402
from engine.world import World  # noqa: E402

MUZZLE = 26.0
THX, THY = 20.0, 14.0


def resolve(key: str) -> str:
    if os.path.isfile(key):
        return os.path.abspath(key)
    stem = key[:-6] if key.endswith('.tankp') else key
    for cand in (os.path.join(ROOT, 'pool', key, stem + '.tankp.py'),
                 os.path.join(ROOT, 'ai', stem + '.tankp.py')):
        if os.path.isfile(cand):
            return cand
    hits = sorted(glob.glob(os.path.join(ROOT, 'pool', '*', stem + '.tankp.py')))
    if hits:
        return hits[0]
    raise SystemExit('не найдено: ' + key)


def wrap(a: float) -> float:
    while a > math.pi:
        a -= 2.0 * math.pi
    while a < -math.pi:
        a += 2.0 * math.pi
    return a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('me')
    ap.add_argument('--vs', default='pool/arena-b43521c5/bars.tankp.py')
    ap.add_argument('--maps', default='arena,fortress')
    ap.add_argument('--seeds', default='1')
    ap.add_argument('--bin', type=float, default=2.0)
    ap.add_argument('--max', type=float, default=90.0)
    args = ap.parse_args()

    me_path = resolve(args.me)
    foes = [resolve(x.strip()) for x in args.vs.split(',') if x.strip()]
    maps = [m.strip() for m in args.maps.split(',') if m.strip()]
    seeds = [int(s) for s in args.seeds.split(',') if s.strip()]
    nb = int(args.max / args.bin)
    hit = [0] * nb
    seen = [0] * nb

    for foe_path in foes:
        for mp in maps:
            for sd in seeds:
                for a, b in ((me_path, foe_path), (foe_path, me_path)):
                    fa, fb = load_program(a), load_program(b)
                    world = World.create(load_map(mp), BALANCE, seed=sd, max_seconds=60)
                    mv = tankp.MapView(map_payload(world))
                    mine = (a == me_path)
                    while not world.over:
                        oa, ob = build_both(world, BALANCE.default_budget_ms)
                        acts = [None, None]
                        for i, (fn, raw) in enumerate(((fa, oa), (fb, ob))):
                            try:
                                r = fn(tankp.Observation(raw, mv))
                            except Exception:  # noqa: BLE001
                                r = tankp.Action()
                            acts[i] = r if r.__class__ is tankp.Action else Action.clamp(r)
                        A, B = world.tanks
                        me_, foe = (A, B) if mine else (B, A)
                        d = math.hypot(foe.x - me_.x, foe.y - me_.y)
                        if d < 900.0:
                            dx, dy = math.cos(foe.turret), math.sin(foe.turret)
                            mx = foe.x + dx * MUZZLE
                            my = foe.y + dy * MUZZLE
                            res = segment_obb(mx, my, dx, dy, me_.x, me_.y,
                                              (THX, THY), me_.hull, 3000.0)
                            if res is not None:
                                t, nx, ny = res
                                if ray_grid_detailed(world.arena, mx, my, dx, dy, t) is None:
                                    imp = armor.resolve_impact(nx, ny, dx, dy,
                                                               me_.hull, BALANCE)
                                    beta = abs(wrap(math.atan2(dy, dx) - me_.hull))
                                    beta = math.degrees(beta)
                                    if beta > 180.0:
                                        beta -= 180.0
                                    if beta > 180.0 - beta:
                                        beta = 180.0 - beta
                                    k = int(beta // args.bin)
                                    if 0 <= k < nb:
                                        seen[k] += 1
                                        if not imp.ricochet:
                                            hit[k] += 1
                        world.step(acts)

    total = max(1, sum(seen))
    print(f'{os.path.basename(me_path)}: тиков «враг заряжен и видит» {total}\n')
    print(f"{'β,°':>10}{'prob':>8}{'share':>8}")
    best = None
    for k in range(nb):
        lo = k * args.bin
        hi = lo + args.bin
        if seen[k] < 30:
            continue
        p = hit[k] / seen[k]
        s = seen[k] / total
        bar = '#' * int(round(p * 40))
        print(f'{lo:4.0f}-{hi:2.0f}{p * 100:7.1f}%{s * 100:7.1f}%  {bar}')
        if best is None or p < best[1]:
            best = (lo, p, s)
    if best:
        print(f'\nминимум пробоя: β≈{best[0]:.0f}° (prob {best[1] * 100:.1f}%, '
              f'доля тиков {best[2] * 100:.1f}%)')
    peak = max(range(nb), key=lambda k: seen[k])
    print(f'пик, где я реально стою: β≈{peak * args.bin:.0f}° '
          f'({seen[peak] / total * 100:.1f}% тиков)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
