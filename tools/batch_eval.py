"""Замер нескольких программ на одной сетке против всего pool.

    python tools/batch_eval.py --seeds 1,2 pool/arena-shturm/sobol.tankp.py /tmp/v/tl90.py

Печатает по строке на программу: очки (W + 0.5D), W-L-D, ошибки, максимум
времени решения. Для воспроизводимости вызывайте с PYTHONHASHSEED=0: без
фиксации хеша порядок обхода рёбер сетки меняется и очки плывут на ~1 пп.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--maps', default='arena,corridor,colonnade,fortress,meadow')
    ap.add_argument('--seeds', default='1,2')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--only', default='',
                    help='противники (ключи через запятую); по умолчанию — все '
                         'программы pool, включая одноимённую с кандидатом, '
                         'чтобы у всех кандидатов был ОДИН И ТОТ ЖЕ список')
    ap.add_argument('--out', default='/tmp/exp/batch')
    ap.add_argument('progs', nargs='+')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    #: Фиксированный список противников: все программы pool. Иначе кандидат
    #: из /tmp играет против собственной базы (её нет в его пути «я»), а
    #: база — нет, и сравнение перестаёт быть честным.
    FOES = args.only or ','.join(sorted(
        os.path.relpath(p, ROOT) for p in
        __import__('glob').glob(os.path.join(ROOT, 'pool', '*', '*.tankp.py'))))
    rows = []
    for prog in args.progs:
        name = os.path.basename(prog).replace('.tankp', '').replace('.py', '')
        js = os.path.join(args.out, name + '.json')
        cmd = [sys.executable, os.path.join(ROOT, 'tools', 'fast_pool_eval.py'),
               '--me', prog, '--maps', args.maps, '--seeds', args.seeds,
               '--jobs', str(args.jobs), '--quiet', '--json', js]
        cmd += ['--vs', args.only or FOES]
        subprocess.run(cmd, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONHASHSEED='0'))
        if not os.path.exists(js):
            print(f'{name:<14} ОШИБКА запуска')
            continue
        d = json.load(open(js))
        t = d['tot']
        n = max(1, sum(t))
        pts = (t[0] + 0.5 * t[2]) / n * 100.0
        rows.append((pts, name, t, d.get('errs', 0), d.get('think_max', 0.0)))
        print(f'{name:<14} pts={pts:6.2f}  W{t[0]:>3} L{t[1]:>3} D{t[2]:>3}  '
              f'errs={d.get("errs", 0)}  think={d.get("think_max", 0):.1f}ms', flush=True)
    if len(rows) > 1:
        print('\nлучшая:', max(rows)[1], f'{max(rows)[0]:.2f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
