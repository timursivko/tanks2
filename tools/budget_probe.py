"""Сколько тиков программа не укладывается в бюджет решения (по умолчанию 10 мс).

    python tools/budget_probe.py pool/arena-a8803110/rosomakha.tankp.py \
        --vs pool/arena-timur/rys.tankp.py,pool/arena-b43521c5/bars.tankp.py \
        --maps arena,corridor --seeds 1

Считает ``over_budget`` из статистики боя — ровно тот счётчик, что видит
``battle/runner.py``: превысил бюджет — тик уходит пустым, команда теряется.
Поэтому замер надо делать на ПУСТОЙ машине: параллельные замеры съедают
процессор и рисуют превышения, которых в реальном турнире не будет.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'engine', 'sdk'))

from battle.kernel import load_program, run_battle  # noqa: E402
from config import BALANCE  # noqa: E402
from engine.grid import load_map  # noqa: E402


def resolve(key: str) -> str:
    if os.path.isfile(key):
        return os.path.abspath(key)
    stem = key[:-6] if key.endswith('.tankp') else key
    for cand in (os.path.join(ROOT, 'pool', key, stem + '.tankp.py'),
                 os.path.join(ROOT, 'ai', stem + '.tankp.py')):
        if os.path.isfile(cand):
            return cand
    raise SystemExit('не найдено: ' + key)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('me')
    ap.add_argument('--vs', default='pool/arena-timur/rys.tankp.py')
    ap.add_argument('--maps', default='arena,corridor,colonnade,fortress,meadow')
    ap.add_argument('--seeds', default='1')
    ap.add_argument('--seconds', type=int, default=60)
    args = ap.parse_args()
    me = resolve(args.me)
    foes = [resolve(x.strip()) for x in args.vs.split(',') if x.strip()]
    maps = [m.strip() for m in args.maps.split(',') if m.strip()]
    seeds = [int(s) for s in args.seeds.split(',') if s.strip()]
    ticks = over_b = calls = 0
    think_max = 0.0
    for foe in foes:
        for mp in maps:
            for sd in seeds:
                fa, fb = load_program(me), load_program(foe)
                _w, st = run_battle(fa, fb, load_map(mp), sd, BALANCE, args.seconds)
                s = st[0]
                over_b += s['over_budget']
                calls += s['think_calls']
                ticks += s['think_calls']
                think_max = max(think_max, s['think_max'])
    pct = 100.0 * over_b / max(1, calls)
    print(f'{os.path.basename(me)}: тиков {ticks}, превышений бюджета {over_b} '
          f'({pct:.3f}%), максимум решения {think_max:.2f} мс')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
