"""Vulnerability split by distance band + opportunity, exact engine geometry."""
import sys, math, types, glob, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); sys.path.insert(0,ROOT+'/engine/sdk'); sys.path.insert(0,ROOT+'/tools')
import tankp
from config import BALANCE
from engine.action import Action
from engine.grid import load_map
from engine.observation import build_both, map_payload
from engine.world import World
from engine.geometry import segment_obb
from engine import armor
from engine.visibility import ray_grid_detailed
BULLET=620.0; MUZZLE=26.0; THX=20.0; THY=14.0
BANDS=[(0,60),(60,120),(120,250),(250,500),(500,900)]
def load(path):
    mod = types.ModuleType("p"); mod.__file__=str(path)
    exec(compile(open(path).read(), str(path), "exec"), mod.__dict__)
    prog = mod.__dict__.get("program")
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
def probe(me_, foe_, arena):
    mx=foe_.x+math.cos(foe_.turret)*MUZZLE; my=foe_.y+math.sin(foe_.turret)*MUZZLE
    dx,dy=math.cos(foe_.turret), math.sin(foe_.turret)
    hit=segment_obb(mx,my,dx,dy, me_.x,me_.y,(THX,THY),me_.hull, 3000.0)
    if hit is None: return None
    t,nx,ny=hit
    if ray_grid_detailed(arena, mx,my,dx,dy,t) is not None: return None
    return armor.resolve_impact(nx,ny,dx,dy,me_.hull,BALANCE)
def main():
    me=resolve(sys.argv[1]); foes=[resolve(x) for x in sys.argv[2].split(',')]; maps=sys.argv[3].split(','); seeds=[int(x) for x in sys.argv[4].split(',')]
    nb=len(BANDS)
    vuln_n=[0]*nb; vuln_v=[0]*nb; opp_n=[0]*nb; opp_v=[0]*nb
    tw=tl=tdr=0
    for foe in foes:
        for mp in maps:
            for sd in seeds:
                for a,b in ((me,foe),(foe,me)):
                    fa,fb=load(a),load(b)
                    world=World.create(load_map(mp),BALANCE,seed=sd,max_seconds=60)
                    arena=world.arena; mv=tankp.MapView(map_payload(world))
                    obs_cls=tankp.Observation; clamp=Action.clamp
                    mine=(a==me)
                    while not world.over:
                        oa,ob=build_both(world,BALANCE.default_budget_ms)
                        acts=[None,None]
                        for i,(fn,raw) in enumerate(((fa,oa),(fb,ob))):
                            try: r=fn(obs_cls(raw,mv))
                            except Exception: r=tankp.Action()
                            acts[i]= r if r.__class__ is tankp.Action else clamp(r)
                        A,B=world.tanks; d=math.hypot(B.x-A.x,B.y-A.y)
                        bi=0
                        for k,(lo,hi) in enumerate(BANDS):
                            if lo<=d<hi: bi=k; break
                        i=0 if mine else 1
                        me_,foe_=world.tanks[i],world.tanks[1-i]
                        if foe_.ammo_ready and d<900:
                            vuln_n[bi]+=1
                            imp=probe(me_,foe_,arena)
                            if imp is not None and not imp.ricochet: vuln_v[bi]+=1
                        if me_.ammo_ready:
                            opp_n[bi]+=1
                            imp=probe(foe_,me_,arena)
                            if imp is not None and not imp.ricochet: opp_v[bi]+=1
                        world.step(acts)
                    o=world.outcome
                    if (o=='a_win')==mine and o in ('a_win','b_win'): tw+=1
                    elif o in ('a_win','b_win'): tl+=1
                    else: tdr+=1
    n=tw+tl+tdr
    print(f"{os.path.basename(os.path.dirname(me)):<14} pts={(tw+0.5*tdr)/n*100:5.1f}%  n={n}")
    for k,(lo,hi) in enumerate(BANDS):
        print(f"   d {lo:3d}-{hi:3d}: vuln {vuln_v[k]/max(1,vuln_n[k])*100:5.1f}% (n={vuln_n[k]:5d})  opp {opp_v[k]/max(1,opp_n[k])*100:5.1f}%")
    tot_n=sum(vuln_n); tot_v=sum(vuln_v); tot_on=sum(opp_n); tot_ov=sum(opp_v)
    print(f"   TOTAL vuln {tot_v/max(1,tot_n)*100:.1f}% opp {tot_ov/max(1,tot_on)*100:.1f}%")
main()
