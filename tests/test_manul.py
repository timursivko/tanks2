"""Regression tests for the observation-only recovery addition."""
from pathlib import Path
from types import SimpleNamespace

from battle.kernel import load_program

PATH = Path(__file__).resolve().parents[1] / 'pool/arena-34afd4c5/manul.tankp.py'


def fresh():
    return load_program(str(PATH)).__self__


def test_recovery_keeps_armor_and_track_budget():
    brain = fresh()
    brain._fire_line = lambda *args: (0., .3)
    brain._threat = lambda *args: None
    me = SimpleNamespace(x=0., y=0., hull=35 * 3.141592653589793 / 180.)
    o = SimpleNamespace(dt=1 / 60)
    brain.t = 12.5
    turn, drive = brain._fight(o, me, 200., 0.)
    assert abs(turn) < 1e-12
    assert drive == -1.
    assert abs(turn) + abs(drive) <= 1.0000001
    brain.t = 13.5
    assert brain._fight(o, me, 200., 0.)[1] == 1.
    brain.progress_t = 12.
    brain.t = 12.5
    assert brain._fight(o, me, 200., 0.)[1] == 1.


def test_hp_progress_resets_recovery_but_motion_does_not():
    brain = fresh()
    brain._remember = lambda *args: None
    brain._gun = lambda *args: (0., False)
    brain._fight = lambda *args: (0., 1.)
    brain._unstuck = lambda o, me, turn, drive: (turn, drive)
    me = SimpleNamespace(x=0., y=0., hp=10.)
    enemy = SimpleNamespace(x=100., y=0., hp=10.)
    o = SimpleNamespace(tick=1, time=1., me=me, enemy=enemy, map=None)
    brain.on_tick(o)
    assert brain.progress_t == 1.
    o.tick, o.time = 900, 15.
    enemy.x = 200.
    brain.on_tick(o)
    assert brain.progress_t == 1.
    enemy.hp = 6.
    o.tick, o.time = 901, 15.1
    brain.on_tick(o)
    assert brain.progress_t == 15.1
    me.hp = 6.
    o.tick, o.time = 902, 15.2
    brain.on_tick(o)
    assert brain.progress_t == 15.2


def test_new_battle_resets_progress():
    brain = fresh()
    brain.progress_t, brain.prev_hp = 40., (2., 6.)
    brain.on_start({'tank': 1})
    assert brain.progress_t == 0.
    assert brain.prev_hp is None
