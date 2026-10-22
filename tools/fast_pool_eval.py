"""Fast evaluation harness: candidate(s) vs pool, native engine, multiprocess."""
import sys, os, json, time, glob, argparse
from concurrent.futures import ProcessPoolExecutor
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); sys.path.insert(0,ROOT+'/engine/sdk'); sys.path.insert(0,ROOT+'/tools')
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPS=['arena','corridor','colonnade','fortress','meadow']
SEEDS=[1,2,3]

def work(job):
    import sys
    pa, pb, mp, seed, secs = job
    sys.path.insert(0,ROOT); sys.path.insert(0,ROOT+'/engine/sdk'); sys.path.insert(0,ROOT+'/tools')
    from battle.kernel import load_program, run_battle
    from config import BALANCE
    from engine.grid import load_map
    try:
        fa = load_program(pa); fb = load_program(pb)
        w, st = run_battle(fa, fb, load_map(mp), seed, BALANCE, secs)
        return (pa, pb, mp, seed, w.outcome, w.tick, st[0]['hp_left'], st[1]['hp_left'],
                st[0]['errors'], st[1]['errors'], st[0]['think_max'], st[1]['think_max'])
    except Exception as e:
        return (pa, pb, mp, seed, 'error', 0, 0, 0, 0, 0, 0, 0)

def resolve(key):
    p = key
    if os.path.isfile(p): return os.path.abspath(p)
    stem = key[:-6] if key.endswith('.tankp') else key
    for c in [f'{ROOT}/pool/{key}/{stem}.tankp.py', f'{ROOT}/ai/{stem}.tankp.py',
              f'{ROOT}/pool/{stem}/{stem}.tankp.py']:
        if os.path.isfile(c): return c
    hits = sorted(glob.glob(f'{ROOT}/pool/*/{stem}.tankp.py'))
    if hits: return hits[0]
    hits = sorted(glob.glob(f'{ROOT}/pool/{key}/*.tankp.py'))
    if hits: return hits[0]
    raise SystemExit('not found: '+key)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--me', required=True)
    ap.add_argument('--vs', default='pool')
    ap.add_argument('--maps', default=','.join(MAPS))
    ap.add_argument('--seeds', default=','.join(map(str,SEEDS)))
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--seconds', type=int, default=60)
    ap.add_argument('--one-side', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--json', default='')
    args = ap.parse_args()
    me = resolve(args.me)
    if args.vs == 'pool':
        foes = sorted(glob.glob(f'{ROOT}/pool/*/*.tankp.py'))
        foes = [f for f in foes if os.path.abspath(f) != me]
    else:
        foes = [resolve(x.strip()) for x in args.vs.split(',') if x.strip()]
    maps=[m.strip() for m in args.maps.split(',') if m.strip()]
    seeds=[int(s) for s in args.seeds.split(',') if s.strip()]
    jobs=[]
    for foe in foes:
        for m in maps:
            for s in seeds:
                jobs.append((me, foe, m, s, args.seconds))
                if not args.one_side:
                    jobs.append((foe, me, m, s, args.seconds))
    t0=time.perf_counter()
    res=[]
    if args.jobs<=1:
        for j in jobs: res.append(work(j))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            for r in ex.map(work, jobs, chunksize=1): res.append(r)
    dt=time.perf_counter()-t0
    stats={}; tot=[0,0,0]; bad=[]
    think=[0.0,0]
    for (pa,pb,m,s,out,ticks,hpa,hpb,ea,eb,tma,tmb) in res:
        foe = os.path.basename(os.path.dirname(pb if pa==me else pa))
        st = stats.setdefault(foe,[0,0,0,0.0,0])
        if out=='error':
            bad.append((pa,pb,m,s)); st[4]+=1; continue
        mine = pa==me
        my_win = (out=='a_win' and mine) or (out=='b_win' and not mine)
        my_loss = (out=='a_win' and not mine) or (out=='b_win' and mine)
        if my_win: st[0]+=1; tot[0]+=1
        elif my_loss: st[1]+=1; tot[1]+=1
        else: st[2]+=1; tot[2]+=1
        think[0]=max(think[0], tma if mine else tmb); think[1]+=1
    n=sum(tot)
    if not args.quiet:
        print(f"{'foe':<18}{'W':>4}{'L':>4}{'D':>4}{'pts%':>8}{'n':>5}")
        for foe,st in sorted(stats.items(), key=lambda kv: -(kv[1][0]+0.5*kv[1][2])/max(1,sum(kv[1][:3]))):
            g=sum(st[:3]); print(f"{foe:<18}{st[0]:>4}{st[1]:>4}{st[2]:>4}{(st[0]+0.5*st[2])/max(1,g)*100:>8.1f}{g:>5}")
        print(f"TOTAL W{tot[0]} L{tot[1]} D{tot[2]}  pts={ (tot[0]+0.5*tot[2])/max(1,n)*100:.2f}%  n={n}  {dt:.0f}s  {dt/max(1,n)*1000:.0f}ms/battle  thinkmax={think[0]:.2f}ms")
        if bad: print("ERRORS:", bad[:5])
    nerr=sum(v[4] for v in stats.values())
    if nerr:
        print(f'ERRORS in {nerr} battles: {bad[:3]}', flush=True)
    if args.json:
        json.dump({'tot':tot,'stats':stats,'secs':dt,'think_max':think[0],'errs':nerr}, open(args.json,'w'))
    return tot
if __name__=='__main__': main()
