"""Координатный поиск по константам TANKP-программы.

    PYTHONHASHSEED=0 python tools/knob_search.py \
        --base pool/arena-a8803110/rosomakha.tankp.py \
        --out /tmp/search1 --seeds 1,2 --passes 2

Идея: замер стоит ~40 с на сетку 5 карт x 2 сида против всего pool, поэтому
перебираем по одной константе (все кандидаты), оставляем только заметный
прирост (``--accept``, по умолчанию 1.0 пп) и повторяем проход, пока он
что-то находит. Итог — ``<out>/best.py`` + ``<out>/best.json``; финальную
проверку обязательно делать на ДРУГИХ сидах и картах, иначе это подгонка
под сетку, а не улучшение.

Константы, которых нет в ``--knobs``, не трогаются. Меняется ровно одна
строка вида ``NAME = value`` — так же, как это делал бы человек руками.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Какие константы и какие значения пробуем. Значения подобраны вокруг
#: текущих: шаг достаточно крупный, чтобы разница была больше шума (SE на
#: сетке 5 карт x 2 сида ~1.5 пп).
DEFAULT_KNOBS = {
    'TURN_LEAD': ['0.88', '0.92'],
    'SIDE_STICK': ['0.18', '0.22'],
    'BRAWL_TURN': ['0.2', '0.5'],
    'BRAWL_T': ['0.9', '1.6'],
    'SCAN_MARGIN': ['0.3', '0.8'],
    'GATE_NEED': ['4'],
    'FIRE_MARGIN': ['1.0', '2.0'],
    'DEAD_DEG': ['34.0', '36.0'],
    'TUR_NEAR': ['0.5', '0.75'],
    'THREAT_T': ['0.45', '0.8'],
    'KEEP_D': ['100.0', '170.0'],
    'SWING_NEAR': ['0.45', '0.65'],
    'STEP_MAX_W': ['1.2', '2.0'],
    'M_FAR': ['2.0', '4.0'],
    'SCAN_D': ['110.0', '160.0'],
    'CONTACT_D': ['90.0', '140.0'],
}


def value_of(src: str, name: str):
    m = re.search(r'^%s = ([^\s#]+)' % re.escape(name), src, re.M)
    return m.group(1) if m else None


def set_value(src: str, name: str, val: str) -> str:
    pat = re.compile(r'^(%s = )([^\s#]+)' % re.escape(name), re.M)
    out, n = pat.subn(lambda m: m.group(1) + val, src, count=1)
    if n != 1:
        raise SystemExit(f'нет строки «{name} = ...» в программе')
    return out


def measure(path: str, out_dir: str, tag: str, args) -> tuple[float, list, dict]:
    js = os.path.join(out_dir, tag + '.json')
    cmd = [sys.executable, os.path.join(ROOT, 'tools', 'fast_pool_eval.py'),
           '--me', path, '--maps', args.maps, '--seeds', args.seeds,
           '--jobs', str(args.jobs), '--quiet', '--json', js]
    subprocess.run(cmd, check=False, capture_output=True, text=True,
                   env=dict(os.environ, PYTHONHASHSEED='0'))
    if not os.path.exists(js):
        return -1.0, [0, 0, 0], {}
    d = json.load(open(js))
    t = d['tot']
    n = max(1, sum(t))
    return (t[0] + 0.5 * t[2]) / n * 100.0, t, d['stats']


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--out', default='/tmp/knob_search')
    ap.add_argument('--maps', default='arena,corridor,colonnade,fortress,meadow')
    ap.add_argument('--seeds', default='1,2')
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--passes', type=int, default=2)
    ap.add_argument('--accept', type=float, default=1.0)
    ap.add_argument('--knobs', default='', help='JSON: {"NAME": ["1.0", "2.0"]}')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    knobs = json.loads(args.knobs) if args.knobs else DEFAULT_KNOBS

    best_src = open(args.base).read()
    best, tot, stats = measure(args.base, args.out, 'base', args)
    best_cfg = {}
    print(f'BASE {os.path.basename(args.base)} pts={best:.2f} '
          f'W{tot[0]} L{tot[1]} D{tot[2]}', flush=True)

    for p in range(args.passes):
        improved = False
        for name, vals in knobs.items():
            cur = value_of(best_src, name)
            if cur is None:
                continue
            for v in vals:
                if v == cur:
                    continue
                src = set_value(best_src, name, v)
                path = os.path.join(args.out, 'cand.py')
                open(path, 'w').write(src)
                sc, tt, st = measure(path, args.out, f'p{p}_{name}_{v}', args)
                mark = ' ***' if sc > best + args.accept else ''
                print(f'  {name}: {cur} -> {v}  pts={sc:.2f} '
                      f'W{tt[0]} L{tt[1]} D{tt[2]}{mark}', flush=True)
                if mark:
                    best, best_src, tot, stats = sc, src, tt, st
                    best_cfg[name] = v
                    improved = True
                    open(os.path.join(args.out, 'best.py'), 'w').write(best_src)
                    json.dump({'cfg': best_cfg, 'pts': best, 'tot': tot},
                              open(os.path.join(args.out, 'best.json'), 'w'),
                              indent=1)
        print(f'--- проход {p}: {best:.2f} {best_cfg}', flush=True)
        if not improved:
            break

    open(os.path.join(args.out, 'best.py'), 'w').write(best_src)
    weak = sorted(stats.items(),
                  key=lambda kv: (kv[1][0] + 0.5 * kv[1][2]) / max(1, sum(kv[1][:3])))
    print('слабые:', ', '.join(
        f'{k.split("/")[-2] if "/" in k else k}='
        f'{(v[0] + 0.5 * v[2]) / max(1, sum(v[:3])) * 100:.0f}' for k, v in weak[:6]))
    print(f'ИТОГО {best:.2f} W{tot[0]} L{tot[1]} D{tot[2]} {best_cfg}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
