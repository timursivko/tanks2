import re, sys, os
base=open(ROOT+'/pool/arena-shturm/sobol.tankp.py').read()
for spec in sys.argv[1:]:
    name, rest = spec.split('=', 1)
    pairs = rest.split(',')
    s = base
    out = {}
    for pr in pairs:
        k, v = pr.split(':')
        s2, n = re.subn(r'^%s = ([^\s#]+)' % k, f'{k} = {v}', s, count=1, flags=re.M)
        assert n == 1, k
        s = s2; out[k] = v
    p = f'/tmp/exp/k_{name}.py'
    open(p, 'w').write(s)
    print(p, out)
