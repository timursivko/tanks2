"""Отладочный стенд: гоняет бой без процессов и печатает решения танков.

Нужен, чтобы быстро понять, почему программа стреляет редко или едет в стену::

    python tools/probe.py 01_chaser 05_turtle --map arena --seed 1
"""

from __future__ import annotations

import sys
import types
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))

from config import BALANCE, BattleConfig, PlayerConfig  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.observation import build_observation, map_payload  # noqa: E402
from engine.program import load_program  # noqa: E402
from engine.world import World  # noqa: E402
import tankp  # noqa: E402


def load_inproc(key: str):
    """Загружает программу прямо в этом процессе (без песочницы)."""
    stem = key[:-6] if key.endswith(".tankp") else key
    for cand in (ROOT / "ai" / f"{stem}.tankp.py", ROOT / "ai" / stem,
                 ROOT / "examples" / f"{stem}.tankp.py", Path(key)):
        if cand.exists():
            break
    else:
        raise SystemExit(f"нет программы {key}")
    mod = types.ModuleType("probe_program")
    mod.__file__ = str(cand)
    exec(compile(cand.read_text(encoding="utf-8"), str(cand), "exec"), mod.__dict__)  # noqa: S102
    prog = mod.__dict__.get("program")
    if prog is not None and hasattr(prog, "on_start"):
        prog.on_start({"tank": 0})
        return prog.on_tick
    fn = mod.__dict__.get("on_tick")
    if fn is None:
        raise SystemExit(f"нет точки входа в {cand}")
    if hasattr(mod.__dict__.get("on_start"), "__call__"):
        mod.__dict__["on_start"]({})
    return fn


def main() -> int:
    a_key = sys.argv[1] if len(sys.argv) > 1 else "01_chaser"
    b_key = sys.argv[2] if len(sys.argv) > 2 else "05_turtle"
    map_name = sys.argv[sys.argv.index("--map") + 1] if "--map" in sys.argv else "arena"
    seed = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 1

    fns = [load_inproc(a_key), load_inproc(b_key)]
    arena = load_map(map_name)
    world = World.create(arena, BALANCE, seed=seed, names=(a_key, b_key))
    mp = map_payload(world)

    fire_count = [0, 0]
    vis_ticks = [0, 0]
    drive_sum = [0.0, 0.0]
    hp_lost_to_ram = [0.0, 0.0]

    while not world.over:
        acts = []
        for i in range(2):
            obs = build_observation(world, i, BALANCE.default_budget_ms)
            obs_obj = tankp.Observation(obs, tankp.MapView(mp))
            raw = fns[i](obs_obj)
            cmd = Action.clamp(raw)
            acts.append(cmd)
            if cmd.fire:
                fire_count[i] += 1
            drive_sum[i] += cmd.drive
            if obs_obj.enemy:
                vis_ticks[i] += 1
        world.step(acts)

    ticks = world.tick
    print(f"карта {map_name}, сид {seed}, тиков {ticks} ({ticks / 60:.1f} с)")
    print(f"итог: {world.outcome} ({world.end_reason})")
    for i, t in enumerate(world.tanks):
        s = t.stats()
        print(f"  {i} {a_key if i == 0 else b_key}: ХП {s['hp_left']}, потеряно {s['damage_taken']}, "
              f"выстрелов {s['shots']} (решение с огнём {fire_count[i]}), "
              f"попаданий {s['hits']}, рикошетов {s['ricochets']}, таранов {s['rams']}, "
              f"видел врага {vis_ticks[i]} тиков, средняя тяга {drive_sum[i] / max(1, ticks):.2f}")
    print(f"  урон от таранов: A {hp_lost_to_ram[0]:.1f}, B {hp_lost_to_ram[1]:.1f}")
    print("  первые события:")
    for e in world.__dict__.get("_last_events", [])[:5]:
        print("   ", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())