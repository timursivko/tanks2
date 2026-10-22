"""Per-battle trace summary: outcome, hp, min distance, time in bands."""
import sys, math, types, glob, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); sys.path.insert(0,ROOT+'/engine/sdk'); sys.path.insert(0,ROOT+'/tools')
import tankp
from config import BALANCE
from engine.action import Action
from engine.grid import load_map
from engine.observation import build_both, map_payload
from engine.world import World
def load(path):
    mod=types.ModuleType("p"); mod.__file__=str(path)
    exec(compile(open(path).read(),str(path),"exec"), mod.__dict__)
    prog=mod.__dict__.get("program")
    if prog is not None:
        if hasattr(prog,"on_start"): prog.on_start({"tank":0,"map":None})
        return prog.on_tick
    st=mod.__dict__.get("on_start")
    if callable(st): st({})
    return mod.__dict__["on_tick"]
def resolve(k):
    if os.path.isfile(k): return k
    stem=k[:-6] if k.endswith('.tankp') else k
    for c in (fROOT+'/pool/{k}/{stem}.tankp.py', fROOT+'/ai/{stem}.tankp.py'):
        if os.path.isfile(c): return c
    h=sorted(glob.glob(fROOT+'/pool/{k}/*.tankp.py'))
    return h[0] if h else k
me=resolve(sys.argv[1]); foes=[resolve(x) for x in sys.argv[2].split(',')]
maps=sys.argv[3].split(','); seeds=[int(x) for x in sys.argv[4].split(',')]
print(f"{'foe':<15}{'map':<11}{'sd':>3} {'out':<7}{'hp':>9} {'minD':>5}{'<200':>6}{'shots':>6}{'pen':>4}{'ric':>4}{'taken':>6}")
for foe in foes:
    for mp in maps:
        for sd in seeds:
            for swap in (0,1):
                a,b = (me,foe) if not swap else (foe,me)
                fa,fb=load(a),load(b); world=World.create(load_map(mp),BALANCE,seed=sd,max_seconds=60)
                mv=tankp.MapView(map_payload(world)); oc=tankp.Observation
                mine = (a==me)
                mind=1e9; near=0; shots=pen=ric=taken=0
                while not world.over:
                    oa,ob=build_both(world,BALANCE.default_budget_ms); acts=[None,None]
                    for i,(fn,raw) in enumerate(((fa,oa),(fb,ob))):
                        try: r=fn(oc(raw,mv))
                        except Exception: r=tankp.Action()
                        acts[i]= r if r.__class__ is tankp.Action else Action.clamp(r)
                    A,B=world.tanks; d=math.hypot(B.x-A.x,B.y-A.y)
                    mine_idx = 0 if mine else 1
                    if mine_idx==0 and acts[0].fire and A.ammo_ready: shots+=1
                    if mine_idx==1 and acts[1].fire and B.ammo_ready: shots+=1
                    mind=min(mind,d); near += (d<200)
                    for e in world.step(acts):
                        if e.get('kind')!='hit': continue
                        if ((e.get('shooter')==0)==mine):
                            if e.get('ricochet'): ric+=1
                            else: pen+=1
                        elif not e.get('ricochet'): taken+=1
                A,B=world.tanks
                hp = (A.hp,B.hp) if mine else (B.hp,A.hp)
                o=world.outcome
                label = 'WIN' if ((o=='a_win')==mine) else ('LOSS' if o=='a_win' or o=='b_win' else 'DRAW')
                print(f"{os.path.basename(os.path.dirname(foe))[:14]:<15}{mp:<11}{sd:>3} {label:<7}{hp[0]:.0f}-{hp[1]:<4.0f}{mind:>5.0f}{near:>6}{shots:>6}{pen:>4}{ric:>4}{taken:>6}")
