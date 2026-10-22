"""Карта пробитий: где и под каким углом стрелять в ракурсного врага.

Считает ровно так, как движок: снаряд стартует из дула (26 px), свип длиной
один шаг снаряда, удар разбирается engine.armor.resolve_impact.
"""
import math
import sys

ROOT = "/home/user/tanks2"
sys.path.insert(0, ROOT)
from config import BALANCE as B
from engine.armor import resolve_impact
from engine.geometry import segment_obb

STEP = B.bullet_speed / 60.0
HALF = B.half_far


def shot(d, rak, off, me_hull=0.0):
    """d — дистанция, rak — ракурс врага к линии визирования (град),
    off — смещение ствола от линии визирования (град). True — пробитие."""
    mx, my = 0.0, 0.0
    ex, ey = d, 0.0
    # нос врага смотрит на нас и повёрнут на rak
    eh = math.pi + math.radians(rak)
    u = math.radians(off)
    # дуло
    dx, dy = math.cos(me_hull + u), math.sin(me_hull + u)
    bx, by = mx + dx * B.muzzle_offset, my + dy * B.muzzle_offset
    res = segment_obb(bx, by, dx, dy, ex, ey, HALF, eh, STEP)
    if res is None:
        res = segment_obb(bx, by, dx, dy, ex, ey, HALF, eh, d + 60.0)
        if res is None:
            return None, None
    imp = resolve_impact(res[1], res[2], dx, dy, eh, B)
    return (not imp.ricochet), imp


print("строки: дистанция; столбцы: смещение ствола от линии визирования, град")
offs = list(range(-40, 41, 5))
print("d\\off " + " ".join(f"{o:+4d}" for o in offs))
for d in (30, 34, 38, 42, 46, 50, 60, 70, 90, 110, 130, 160, 200):
    cells = []
    for o in offs:
        ok, imp = shot(d, 35.0, o)
        if ok is None:
            cells.append("   .")
        elif ok:
            cells.append(f"{imp.face[0]}{imp.theta_deg:2.0f}"[:4].rjust(4))
        else:
            cells.append("   -")
    print(f"{d:5d} " + " ".join(cells))
print()
print("f/s/r — грань (лоб/борт/корма), число — угол удара; '-' рикошет; '.' мимо")
