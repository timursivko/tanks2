"""Круговой турнир по явному списку программ (pool + кандидаты в /tmp).

    python tools/league.py --maps arena,corridor --seeds 1,2 --add /tmp/exp/v_k3h1.py
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import duel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--maps", default="arena,corridor,colonnade")
    ap.add_argument("--seeds", default="1,2")
    ap.add_argument("--only", default="pool/*/*.tankp.py")
    ap.add_argument("--add", default="")
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 2))
    ap.add_argument("--matrix", action="store_true")
    args = ap.parse_args()

    paths = sorted(glob.glob(str(ROOT / args.only)))
    for extra in args.add.split(","):
        if extra.strip():
            paths.append(str(Path(extra.strip()).resolve()))
    maps = [m.strip() for m in args.maps.split(",") if m.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    jobs = []
    for a in paths:
        for b in paths:
            if a == b:
                continue
            for m in maps:
                for s in seeds:
                    jobs.append((a, b, m, s, args.seconds))

    res = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        for r in ex.map(duel.work, jobs, chunksize=2):
            res.append(r)

    n = len(paths)
    idx = {p: i for i, p in enumerate(paths)}
    win = [[0] * n for _ in range(n)]
    draw = [[0] * n for _ in range(n)]
    for pa, pb, m, s, out, ticks, hpa, hpb, err, secs in res:
        a, b = idx[pa], idx[pb]
        if out == "a_win":
            win[a][b] += 1
        elif out == "b_win":
            win[b][a] += 1
        else:
            draw[a][b] += 1
            draw[b][a] += 1

    names = []
    for p in paths:
        q = Path(p)
        names.append(q.parent.name + "/" + q.name.replace(".tankp.py", ""))
    w = max(len(x) for x in names) + 2
    if args.matrix:
        print(f"{'':<{w}}" + "".join(f"{i:>4}" for i in range(n)))
        for i in range(n):
            cells = []
            for j in range(n):
                tot = win[i][j] + win[j][i] + draw[i][j]
                cells.append("   ." if i == j else f"{100 * (win[i][j] + 0.5 * draw[i][j]) / max(1, tot):>4.0f}")
            print(f"{names[i]:<{w}}" + "".join(cells))
        print()
    score = []
    for i in range(n):
        wsum = sum(win[i])
        lsum = sum(win[j][i] for j in range(n))
        dsum = sum(draw[i])
        tot = wsum + lsum + dsum
        score.append((100 * (wsum + 0.5 * dsum) / max(1, tot), wsum, lsum, dsum, names[i]))
    print(f"{'рейтинг (W + 0.5D)':<{w}} {'%':>6} {'W':>5} {'L':>5} {'D':>5}")
    for pct, wi, lo, dr, nm in sorted(score, reverse=True):
        print(f"{nm:<{w}} {pct:>6.1f} {wi:>5} {lo:>5} {dr:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
