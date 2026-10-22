"""Тесты нативного ядра: побитовое равенство с Python-движком.

Проверки короткие (пара секунд), поэтому их можно гонять вместе с остальными.
Полная сверка — ``python tools/parity.py --long``: она сравнивает состояние
каждый тик на всех картах и итоги реальных боёв.
"""

from __future__ import annotations

import random

import pytest

from config import BALANCE
from engine.action import Action
from engine.grid import load_map
from engine.native import available, load_error
from engine.native.api import _py_state

pytestmark = pytest.mark.skipif(not available(),
                                reason=f"нативное ядро недоступно: {load_error()}")


def test_round_and_hypot_match_python():
    """Примитивы обязаны совпадать с math/round бит в бит."""
    from engine.native.api import prim_hypot, prim_round

    rng = random.Random(2024)
    for _ in range(4000):
        x = rng.uniform(-2048.0, 2048.0)
        n = rng.choice((1, 2, 3, 4, 5))
        assert prim_round(x, n) == round(x, n), (x, n)
        y = rng.uniform(-2048.0, 2048.0)
        assert prim_hypot(x, y) == __import__("math").hypot(x, y), (x, y)


def test_observation_matches_python_every_tick():
    """Наблюдения и состояние мира совпадают на каждом тике."""
    from engine.native.api import NativeWorld
    from engine.observation import build_both
    from engine.world import World

    arena = load_map("arena")
    py = World.create(arena, BALANCE, seed=3, max_seconds=30)
    nat = NativeWorld(arena, BALANCE, seed=3, max_seconds=30)
    rng = random.Random(11)
    try:
        for _ in range(400):
            assert build_both(py, 10.0) == nat.observations(10.0)
            acts = [Action.clamp(Action(rng.uniform(-1, 1), rng.uniform(-1, 1),
                                        rng.uniform(-1, 1),
                                        rng.random() < 0.4)) for _ in range(2)]
            py.step(acts)
            nat.step(acts)
            assert _py_state(py) == nat.state()
            if py.over:
                assert py.outcome == nat.outcome
                break
    finally:
        nat.close()


def test_battle_matches_python():
    """Бой двух настоящих программ даёт тот же итог и ту же статистику."""
    from battle.kernel import load_program, run_battle_native, run_battle_py

    arena = load_map("corridor")
    # программы загружаются заново на каждый бой — как в лидербоарде:
    # состояние между боями не перетекает.
    wp, sp = run_battle_py(load_program("ai/01_chaser.tankp.py"),
                           load_program("ai/05_turtle.tankp.py"),
                           arena, 5, BALANCE, 30)
    wn, sn = run_battle_native(load_program("ai/01_chaser.tankp.py"),
                               load_program("ai/05_turtle.tankp.py"),
                               arena, 5, BALANCE, 30)
    assert (wp.tick, wp.outcome) == (wn.tick, wn.outcome)
    keys = ("shots", "hits", "ric", "ric_taken", "rams", "dealt", "taken",
            "hp_left")
    for i in (0, 1):
        for k in keys:
            assert sp[i][k] == sn[i][k], (i, k)


def test_python_engine_still_available():
    """Эталонный путь никуда не делся: --engine py работает без C."""
    from battle.kernel import resolve_engine, run_battle

    assert resolve_engine("py") == "py"
    arena = load_map("duel")
    from battle.kernel import load_program
    world, stats = run_battle(load_program("ai/06_berserk.tankp.py"),
                              load_program("ai/02_flanker.tankp.py"),
                              arena, 1, BALANCE, 20, engine="py")
    assert world.tick > 0
    assert len(stats) == 2
    assert world.outcome in ("a_win", "b_win", "draw")
