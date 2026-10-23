"""Генератор вариантов программы заменой констант.

    python tools/knob_variants.py --base pool/arena-timur/rys.tankp.py \
        --out /tmp/v a=DEAD_DEG:34,b=GATE_NEED:4
"""
import re, sys, os, argparse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser()
ap.add_argument('--base', default=ROOT + '/pool/arena-shturm/sobol.tankp.py')
ap.add_argument('--out', default='/tmp/exp')
ap.add_argument('specs', nargs='+')
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
base = open(args.base).read()
for spec in args.specs:
    name, rest = spec.split('=', 1)
    pairs = rest.split(',')
    s = base
    out = {}
    for pr in pairs:
        k, v = pr.split(':')
        s2, n = re.subn(r'^%s = ([^\s#]+)' % k, f'{k} = {v}', s, count=1, flags=re.M)
        assert n == 1, k
        s = s2; out[k] = v
    p = f'{args.out}/{name}.py'
    open(p, 'w').write(s)
    print(p, out)
