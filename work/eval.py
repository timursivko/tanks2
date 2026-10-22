"""Бенчмарк: python work/eval.py --me a.py --vs pool|ai|имя,... [--seeds 1,2] [--maps ...] [--detail]"""
import argparse
import glob
import os
import sys
import time
from multiprocessing import Pool

ROOT = "/home/user/tanks2"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine", "sdk"))

from battle.kernel import load_program, run_battle  # noqa: E402
from config import BALANCE  # noqa: E402
from engine.grid import load_map  # noqa: E402

MAPS = ("arena", "corridor", "colonnade", "fortress", "meadow")


def pool_paths():
    out = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "pool", "*", "*.tankp.py"))):
        out[os.path.basename(p)[:-len(".tankp.py")]] = p
    return out


def ai_paths():
    out = {}
    for p in sorted(glob.glob(os.path.join(ROOT, "ai", "*.py"))):
        n = os.path.basename(p)[:-3]
        if n not in ("__init__",):
            out[n] = p
    return out


def resolve(names):
    known = {}
    known.update(pool_paths())
    known.update(ai_paths())
    out = []
    for n in names:
        if n == "pool":
            out.extend(sorted(pool_paths().values()))
        elif n == "ai":
            out.extend(sorted(ai_paths().values()))
        elif n in known:
            out.append(known[n])
        elif os.path.exists(n):
            out.append(n)
        else:
            raise SystemExit("не найдено: " + n)
    seen, res = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            res.append(p)
    return res


def job(a):
    me, opp, mp, seed, seconds, side = a
    fa = load_program(me if side == 0 else opp)
    fb = load_program(opp if side == 0 else me)
    w, st = run_battle(fa, fb, load_map(mp), seed, BALANCE, seconds, "native")
    mine = st[0 if side == 0 else 1]
    other = st[1 if side == 0 else 0]
    oc = getattr(w, "outcome", "")
    if oc == "a_win":
        res = 1.0 if side == 0 else 0.0
    elif oc == "b_win":
        res = 0.0 if side == 0 else 1.0
    else:
        res = 0.5
    return (opp, res, mine["dealt"], other["dealt"], getattr(w, "tick", 0), mine["shots"],
            mine["hits"], mine["ric"], mine["errors"], mine["over_budget"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--me", required=True)
    ap.add_argument("--vs", default="pool")
    ap.add_argument("--maps", default=",".join(MAPS))
    ap.add_argument("--seeds", default="1,2")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--detail", action="store_true")
    a = ap.parse_args()
    me = a.me
    opps = resolve([s for s in a.vs.split(",") if s])
    maps = [m for m in a.maps.split(",") if m]
    seeds = [int(s) for s in a.seeds.split(",") if s]
    tasks = [(me, o, m, s, a.seconds, side)
             for o in opps for m in maps for s in seeds for side in (0, 1)]
    t0 = time.time()
    with Pool(a.jobs or min(2, os.cpu_count() or 1)) as p:
        rows = p.map(job, tasks, chunksize=4)
    n = len(rows)
    w = sum(1 for r in rows if r[1] == 1.0)
    d = sum(1 for r in rows if r[1] == 0.5)
    lo = sum(1 for r in rows if r[1] == 0.0)
    dm = sum(r[2] for r in rows) / n
    dt = sum(r[3] for r in rows) / n
    tk = sum(r[4] for r in rows) / n
    print(f"games {n}  W {w}  L {lo}  D {d}  score {100.0*(w+0.5*d)/n:.1f}%  "
          f"winrate {100.0*w/n:.1f}%  dmg {dm:.2f} / {dt:.2f}  ticks {tk:.0f}  "
          f"wall {time.time()-t0:.1f}s")
    er = sum(r[8] for r in rows)
    ov = sum(r[9] for r in rows)
    if er or ov:
        print(f"ВНИМАНИЕ: ошибок {er}, вне бюджета {ov}")
    if a.detail:
        per = {}
        for r in rows:
            per.setdefault(r[0], []).append(r)
        print(f"{'opponent':22s} {'W':>3s} {'D':>3s} {'L':>3s} {'%':>6s} {'dmg+':>6s} {'dmg-':>6s} {'sh':>5s} {'hit%':>5s} {'ric%':>5s}")
        for o, rr in sorted(per.items(), key=lambda kv: sum(x[1] for x in kv[1]) / len(kv[1])):
            n2 = len(rr)
            w2 = sum(1 for x in rr if x[1] == 1.0)
            d2 = sum(1 for x in rr if x[1] == 0.5)
            sh = sum(x[5] for x in rr) / n2
            hi = sum(x[6] for x in rr) / max(1e-9, sum(x[5] for x in rr))
            ri = sum(x[7] for x in rr) / max(1e-9, sum(x[5] for x in rr))
            print(f"{os.path.basename(o)[:-9]:22s} {w2:3d} {d2:3d} {n2-w2-d2:3d} "
                  f"{100.0*(w2+0.5*d2)/n2:5.1f}% {sum(x[2] for x in rr)/n2:6.2f} "
                  f"{sum(x[3] for x in rr)/n2:6.2f} {sh:5.1f} {100*hi:4.0f}% {100*ri:4.0f}%")


if __name__ == "__main__":
    main()
