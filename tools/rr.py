"""Круговой турнир между программами: матрица побед и рейтинг.

    python tools/rr.py --maps arena,corridor,colonnade --seeds 1,2 --jobs 2
    python tools/rr.py --only pool/*/*.tankp.py --maps arena --seeds 1
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import duel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="pool/*/*.tankp.py")
    ap.add_argument("--maps", default="arena,corridor,colonnade")
    ap.add_argument("--seeds", default="1")
    ap.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 2))
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--matrix", action="store_true")
    args = ap.parse_args()

    paths = sorted(glob.glob(str(ROOT / args.only)))
    if not paths:
        raise SystemExit(f"пусто: {args.only}")
    maps = [m.strip() for m in args.maps.split(",") if m.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    jobs = []
    for i, a in enumerate(paths):
        for j, b in enumerate(paths):
            if i == j:
                continue
            for m in maps:
                for s in seeds:
                    jobs.append((a, b, m, s, args.seconds))

    res = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for r in ex.map(duel.work, jobs, chunksize=2):
            res.append(r)

    idx = {p: k for k, p in enumerate(paths)}
    n = len(paths)
    win = [[0] * n for _ in range(n)]
    loss = [[0] * n for _ in range(n)]
    draw = [[0] * n for _ in range(n)]
    for pa, pb, m, s, out, ticks, hpa, hpb, err, secs in res:
        a, b = idx[pa], idx[pb]
        if out == "a_win":
            win[a][b] += 1
            loss[b][a] += 1
        elif out == "b_win":
            win[b][a] += 1
            loss[a][b] += 1
        else:
            draw[a][b] += 1
            draw[b][a] += 1

    names = [Path(p).parent.name + "/" + Path(p).name.replace(".tankp.py", "") for p in paths]
    w = max(len(x) for x in names) + 2
    print(f"{'':<{w}}" + "".join(f"{i:>4}" for i in range(n)))
    for i in range(n):
        print(f"{names[i]:<{w}}" + "".join(
            "   ." if i == j else f"{100 * win[i][j] / max(1, win[i][j] + loss[i][j] + draw[i][j]):>4.0f}"
            for j in range(n)))

    print()
    score = []
    for i in range(n):
        tot = sum(win[i]) + sum(loss[i]) + sum(draw[i])
        score.append((100 * (sum(win[i]) + 0.5 * sum(draw[i])) / max(1, tot), sum(win[i]),
                      sum(loss[i]), sum(draw[i]), names[i]))
    print(f"{'рейтинг (W + 0.5D)':<{w}} {'%':>6} {'W':>5} {'L':>5} {'D':>5}")
    for pct, wi, lo, dr, nm in sorted(score, reverse=True):
        print(f"{nm:<{w}} {pct:>6.1f} {wi:>5} {lo:>5} {dr:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
