import sys, os
sys.path.insert(0, "/home/user/tanks2"); sys.path.insert(0, "/home/user/tanks2/engine/sdk")
from battle.kernel import load_program, run_battle
from config import BALANCE
from engine.grid import load_map
me, opp = sys.argv[1], sys.argv[2]
for mp in ("arena", "meadow", "corridor"):
    w, st = run_battle(load_program(me), load_program(opp), load_map(mp), 1, BALANCE, 60, "native")
    print(mp, w.outcome, w.tick, "shots", st[0]["shots"], "hits", st[0]["hits"])
