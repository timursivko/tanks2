"""Сколько тиков я подставляюсь бортом, пока враг заряжен и линия видна."""
import sys, math, types, glob, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); sys.path.insert(0,ROOT+'/engine/sdk'); sys.path.insert(0,ROOT+'/tools')
import tankp
from config import BALANCE
from engine.action import Action
from engine.grid import load_map
from engine.observation import build_both, map_payload
from engine.world import World
from engine.visibility import ray_grid_detailed
from math import atan2, hypot, degrees, pi
def wrap(a):
    while a> pi: a-=2*pi
    while a<-pi: a+=2*pi
    return a
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
BANDS=[(0,150),(150,300),(300,500),(500,900)]
me=resolve(sys.argv[1]); foes=[resolve(x) for x in sys.argv[2].split(',')]
maps=sys.argv[3].split(','); seeds=[int(x) for x in sys.argv[4].split(',')]
n=[0]*4; bad=[0]*4   # тики, когда враг заряжен; из них ракурс вне [28,44]
for foe in foes:
  for mp in maps:
    for sd in seeds:
      for a,b in ((me,foe),(foe,me)):
        fa,fb=load(a),load(b); world=World.create(load_map(mp),BALANCE,seed=sd,max_seconds=60)
        mv=tankp.MapView(map_payload(world)); oc=tankp.Observation; mine=(a==me)
        while not world.over:
            oa,ob=build_both(world,BALANCE.default_budget_ms); acts=[None,None]
            for i,(fn,raw) in enumerate(((fa,oa),(fb,ob))):
                try: r=fn(oc(raw,mv))
                except Exception: r=tankp.Action()
                acts[i]= r if r.__class__ is tankp.Action else Action.clamp(r)
            A,B=world.tanks; m,o_=(A,B) if mine else (B,A)
            d=hypot(o_.x-m.x,o_.y-m.y)
            if o_.ammo_ready and d<900 and ray_grid_detailed(world.arena,m.x,m.y,(o_.x-m.x)/max(1e-6,d),(o_.y-m.y)/max(1e-6,d),d) is None:
                beta=abs(degrees(wrap(atan2(o_.y-m.y,o_.x-m.x)-m.hull)))
                bi=0
                for k,(lo,hi) in enumerate(BANDS):
                    if lo<=d<hi: bi=k; break
                n[bi]+=1
                if not (28.0<=beta<=44.0): bad[bi]+=1
            world.step(acts)
print(f'{os.path.basename(me)}:')
for k,(lo,hi) in enumerate(BANDS):
    print(f'   d {lo:3d}-{hi:3d}: armed-ticks={n[k]:6d}  вне ракурса {100*bad[k]/max(1,n[k]):5.1f}%')
print(f'   ИТОГО вне ракурса {100*sum(bad)/max(1,sum(n)):.1f}%  (armed ticks {sum(n)})')
