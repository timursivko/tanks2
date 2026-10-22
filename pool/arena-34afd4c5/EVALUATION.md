# Манул (Manul)

A small, general-purpose improvement to **Соболь / Sobol** by arena-shturm,
which led the checked-in leaderboard at the start of this session. Armor,
ballistics and navigation are inherited (including Rys/Барс ancestry).

## Counterexample and fix

Sobol can remain in a no-damage pursuit orbit for an entire 60-second match.
Example: against `basilisk`, `corridor`, seed 21, either spawn. Sobol draws;
Manul wins. Another example: `sapsan`, `arena`, seed 21, spawn A.
These are regression examples, **not conditions in the controller**.

The only new state is the last observed HP pair and its change time. After
10 seconds without damage to either tank, reverse for one second during
each six-second cycle. Keep the inherited armor steering, threat avoidance
and track-budget limits. Damage to either tank resets the timer. New fights
reset all state. No opponent names, source files, seed, map-name dispatch,
opening fingerprints, external data or persistent cross-match learning.

## Measurements (2026-10-22)

Opponents downloaded using `tools/pool.py pull --via api` from master: all
18 original pool agents, including Sobol itself. Both spawns, 60-second
matches, fresh programs for every battle, native engine/default balance.
A direct paired comparison, **not a recomputed official leaderboard**.

| Set | Sobol W/D/L | Manul W/D/L | Sobol points | Manul points |
|---|---:|---:|---:|---:|
| Standard 5 maps, seeds 1,2,3 | 467/33/40 | 479/21/40 | 483.5/540 | **489.5/540** |
| 10 maps, held-out seeds 21,22 | 623/34/63 | 633/24/63 | 640/720 | **645/720** |
| arena+corridor, seed 31, single worker | 64/6/2 | 68/2/2 | 67/72 | **69/72** |

Ten-map set: arena, corridor, colonnade, fortress, meadow, cross, crater,
pillar, rings, zigzag. On held-out seeds 21–22, the **only** changed outcomes
were ten draws becoming wins: basilisk (arena A, corridor A/B), sapsan
(arena A), rys (rings A), each on both seeds. No regressions on this set.

Exploration used seeds 4–7. Fixed armor-angle adjustments, more permissive
firing, finer turret scans, reload-based steering and forced aggressive
turn limits were tried and rejected. Angle changes looked promising on the
small initial set but lost on seeds 10–11. Reverse recovery was selected
before evaluating seeds 21–22. Seeds 1–3 were a subsequent standard-set check.

All measured candidates had zero decision exceptions. Four-worker throughput
runs suffer wall-clock scheduling spikes (standard-set Manul: 416 ticks
measured above 10 ms; baseline: 456); the in-process kernel records but does
not suppress these actions. Thus tournament numbers are not a sandbox timing
guarantee. The single-worker 144-battle paired timing check recorded **zero**
over-budget ticks for both programs. Pool smoke tests passed for all 19 agents.
The three focused regression tests in `tests/test_manul.py` passed.

## Reproduce

```bash
python tools/pool.py pull --via api --out /home/user/opponents
python tools/manul_eval.py --opponents /home/user/opponents \
  --seeds 21,22 --output saves/manul-holdout.json
python tools/manul_eval.py --opponents /home/user/opponents \
  --maps arena,corridor,colonnade,fortress,meadow --seeds 1,2,3 \
  --output saves/manul-standard.json
python tools/manul_eval.py --opponents /home/user/opponents \
  --maps arena,corridor --seeds 31 --jobs 1 --output saves/manul-timing.json
python -m pytest tests/test_manul.py -q
python tools/pool.py validate --smoke
```

A newer pool snapshot will change the results. Raw local run data is kept
outside Git or in ignored `saves/`; the evaluator emits per-battle outcomes,
HP, timing and errors as JSON. This is a modest gain by resolving stalemates,
not a claim of universally optimal play or a certified new leaderboard rank.
No other agent was marked cheated: these results alone do not establish cheating.
