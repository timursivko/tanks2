"""Reproducible paired evaluation, with fresh programs for every battle.

Download opponents with tools/pool.py pull --via api --out /home/user/opponents.
Then: python tools/manul_eval.py --opponents /home/user/opponents --output saves/manul.json
In-process/native measurements are not a sandbox timing guarantee.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from battle.kernel import load_program, resolve_engine, run_battle_native, run_battle_py
from config import BALANCE
from engine.grid import load_map


def play(spec):
    name, mine, foe, map_name, seed, side, engine = spec
    paths = (mine, foe) if side == 0 else (foe, mine)
    run = run_battle_native if engine == 'native' else run_battle_py
    world, stats = run(*[load_program(p) for p in paths], load_map(map_name), seed, BALANCE, 60)
    score = .5 if world.outcome == 'draw' else float(world.outcome == ('a_win' if side == 0 else 'b_win'))
    return dict(candidate=name, opponent=foe, map=map_name, seed=seed, side=side,
                score=score, outcome=world.outcome, ticks=world.tick, stats=stats[side])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--opponents', type=Path, default=ROOT / 'pool')
    p.add_argument('--candidate', default=str(ROOT / 'pool/arena-34afd4c5/manul.tankp.py'))
    p.add_argument('--baseline', default=str(ROOT / 'pool/arena-shturm/sobol.tankp.py'))
    p.add_argument('--seeds', default='21,22')
    p.add_argument('--maps', default='arena,corridor,colonnade,fortress,meadow,cross,crater,pillar,rings,zigzag')
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--engine', choices=['auto', 'native', 'py'], default='auto')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    engine = resolve_engine(args.engine)  # build/check before forking workers
    excluded = Path(args.candidate).resolve()
    foes = sorted(str(p.resolve()) for p in args.opponents.glob('*/*.tankp.py') if p.resolve() != excluded)
    if not foes:
        p.error('No opponents found')
    candidates = [('baseline', args.baseline), ('candidate', args.candidate)]
    specs = [(name, mine, foe, m, seed, side, engine)
             for (name, mine), foe, m, seed, side in itertools.product(
                 candidates, foes, args.maps.split(','), map(int, args.seeds.split(',')), [0, 1])]
    with ProcessPoolExecutor(args.jobs) as executor:
        results = list(executor.map(play, specs))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(engine=engine, results=results), indent=2) + '\n')
    for name, _ in candidates:
        rows = [r for r in results if r['candidate'] == name]
        counts = [sum(r['score'] == score for r in rows) for score in (1., .5, 0.)]
        print(f'{name}: {sum(r["score"] for r in rows):g}/{len(rows)}; W/D/L={counts}; '
              f'errors={sum(r["stats"]["errors"] for r in rows)}; '
              f'over_budget={sum(r["stats"]["over_budget"] for r in rows)}')


if __name__ == '__main__':
    main()
