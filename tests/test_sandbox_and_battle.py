"""Песочница, SDK и расчёт боя целиком."""

from __future__ import annotations

import math
import time

import pytest

from config import BALANCE, BattleConfig, PlayerConfig
from battle.runner import BattleRun
from engine.grid import load_map
from engine.observation import build_observation, map_payload
from engine.program import load_program
from engine.replay import Replay
from engine.sdk.tankp import MapView, Observation
from engine.world import World

AI = [
    "ai/01_chaser.tankp.py",
    "ai/02_flanker.tankp.py",
    "ai/03_camper.tankp.py",
    "ai/04_predictor.tankp.py",
    "ai/05_turtle.tankp.py",
    "ai/06_berserk.tankp.py",
    "ai/07_phantom.tankp.py",
]


@pytest.mark.parametrize("path", AI)


def test_all_ai_are_valid(path: str) -> None:
    meta = load_program(path)
    assert meta.ok, f"{path}: {'; '.join(meta.errors)}"
    assert meta.name and meta.description, f"{path}: нужны имя и описание"
    assert meta.difficulty in (1, 2, 3, 4)


@pytest.mark.parametrize("path", AI)


def test_ai_respects_time_budget(path: str) -> None:
    """Каждый ИИ должен укладываться в бюджет и не сыпать ошибками."""
    meta = load_program(path)
    cfg = BattleConfig(
        a=PlayerConfig(kind="script", source=path, name=meta.name, color=meta.color),
        b=PlayerConfig(kind="script", source=AI[0], name="Погоня", color="#fff"),
        map_name="arena", budget_ms=10.0, seed=3, max_seconds=4)
    run = BattleRun(cfg, "test")
    run.run_sync()
    assert run.status == "done", run.error
    assert run.replay is not None
    stats = run.replay.summary["a"] if run.replay.summary["a"]["name"] == meta.name \
        else run.replay.summary["b"]
    assert stats["think_ms_max"] <= 10.0, f"{path}: решение дольше бюджета"
    assert stats["over_budget"] == 0, f"{path}: нарушений бюджета {stats['over_budget']}"
    assert stats["errors"] == 0 and stats["timeouts"] == 0, f"{path}: ошибки в песочнице"


def test_battle_produces_complete_replay() -> None:
    cfg = BattleConfig(
        a=PlayerConfig(kind="script", source=AI[0], name="Погоня", color="#f80"),
        b=PlayerConfig(kind="script", source=AI[5], name="Берсерк", color="#e33"),
        map_name="arena", budget_ms=10.0, seed=1, max_seconds=30)
    run = BattleRun(cfg, "test")
    run.run_sync()
    rep: Replay = run.replay
    assert rep.frames > 10
    assert rep.duration <= 30.0
    assert rep.summary["outcome"] in ("a_win", "b_win", "draw")
    assert rep.summary["reason"] in ("destroyed", "mutual", "timeout")
    assert rep.events, "в реплее должны быть события"
    # кадры должны покрывать всю длительность боя
    assert rep.t[-1] == pytest.approx(rep.duration, abs=0.01)
    # воспроизведение из payload не теряет кадры
    copy = Replay.from_payload(rep.to_payload())
    assert copy.frames == rep.frames
    assert copy.summary == rep.summary


def test_replay_payload_has_shots_and_trails() -> None:
    cfg = BattleConfig(
        a=PlayerConfig(kind="script", source=AI[0], name="A", color="#f80"),
        b=PlayerConfig(kind="script", source=AI[6], name="B", color="#08f"),
        map_name="arena", budget_ms=10.0, seed=2, max_seconds=25)
    run = BattleRun(cfg, "test")
    run.run_sync()
    payload = run.replay.to_payload()
    assert payload["shots"], "реплей должен хранить снаряды в полёте"
    assert payload["bullets"], "реплей должен хранить траектории"
    for tr in payload["bullets"]:
        assert len(tr["pts"]) >= 2, "траектория из одной точки — мусор"
        ticks = [p[2] for p in tr["pts"]]
        assert ticks == sorted(ticks), "тики в траектории должны идти по порядку"
    assert payload["map"]["name"], "карта обязана ехать вместе с реплеем"


def test_slow_program_gets_empty_action() -> None:
    """Программа, которая думает дольше бюджета, не должна ломать бой."""
    slow = "_slow_probe.tankp.py"
    from config import SCRIPTS_DIR

    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRIPTS_DIR / slow
    path.write_text(
        "#!TANKP 1\n"
        "import time\n"
        "from tankp import TankProgram, Action\n\n\n"
        "class Brain(TankProgram):\n"
        "    def on_tick(self, o):\n"
        "        end = time.perf_counter() + 0.015\n"
        "        while time.perf_counter() < end:\n"
        "            pass\n"
        "        return Action(drive=1.0, fire=True)\n\n\n"
        "program = Brain()\n",
        encoding="utf-8")
    try:
        cfg = BattleConfig(
            a=PlayerConfig(kind="script", source=str(path), name="Медленный", color="#888"),
            b=PlayerConfig(kind="script", source=AI[4], name="Черепаха", color="#0a0"),
            map_name="arena", budget_ms=5.0, seed=1, max_seconds=1.5)
        run = BattleRun(cfg, "test")
        run.run_sync()
        assert run.status == "done", run.error
        slow_stats = run.replay.summary["a"]
        assert slow_stats["over_budget"] > 0, "превышение бюджета должно фиксироваться"
        assert slow_stats["shots"] == 0, "за превышение бюджета команда не засчитывается"
    finally:
        path.unlink(missing_ok=True)


def test_manual_session_runs_in_real_time() -> None:
    """Ручная сессия: кадры идут, танк едет, бой заканчивается."""
    from server.manual import ManualSession

    sess = ManualSession("pytest", "a", AI[0], "arena", 10.0, 4, "Игрок")
    sess.start()
    try:
        time.sleep(0.6)
        assert not sess.error, sess.error
        assert sess.world.tick > 20, "сессия не тикает"
        start_x = sess.world.tanks[0].x
        sess.set_input(["w"], [1200.0, 200.0], True)
        time.sleep(0.8)
        assert sess.world.tanks[0].x != start_x, "танк должен двигаться"
        assert sess.frames, "кадры не приходят"
        frame = sess.frames[-1]
        assert "me" in frame and "enemy_seen" in frame
    finally:
        sess.stop()
    assert sess.runner is not None and sess.runner.proc is None, "процесс не закрыт"


def test_manual_finish_saves_replay() -> None:
    """Бой, дошедший до конца сам, тоже обязан сохранить реплей."""
    from dataclasses import replace

    from engine.replay import replay_path
    from server.app import _finish_manual
    from server.manual import ManualSession

    sess = ManualSession("pytest-finish", "a", AI[4], "arena", 10.0, 9, "Игрок",
                         replace(BALANCE, max_seconds=1))
    sess.start()
    try:
        deadline = time.time() + 8.0
        while not sess.over and time.time() < deadline:
            time.sleep(0.1)
        assert sess.over, "короткий бой должен закончиться сам"
        assert sess.result["outcome"] == "draw"

        body = _finish_manual(sess)
        assert body["replay_id"], "реплей должен сохраниться"
        path = replay_path(body["replay_id"])
        assert path.exists()
    finally:
        sess.stop()
        path = replay_path(body["replay_id"]) if body.get("replay_id") else None
        if path is not None:
            path.unlink(missing_ok=True)


def _obs(world=None):
    """Наблюдение для первого танка в удобной позиции."""
    w = world or World.create(load_map("arena"), BALANCE, seed=1)
    a = w.tanks[0]
    a.x, a.y, a.hull, a.turret = 700.0, 448.0, 0.0, 0.0
    return w, a, Observation(build_observation(w, 0, 10.0),
                              MapView(map_payload(w)))


def test_aim_ray_passes_through_target_not_center() -> None:
    """Башня целит лучом ИЗ СТВОЛА, а не из центра танка.

    Разница постоянная (26 px смещения ствола): на 400 px это 3.7°,
    больше любого разумного допуска, поэтому «наводим ствол на цель»
    систематически мазало.
    """
    _, a, o = _obs()
    tgt = (900.0, 448.0)
    err = o.aim_error(tgt)
    ang = a.turret + err
    mx = a.x + math.cos(ang) * o.muzzle_len
    my = a.y + math.sin(ang) * o.muzzle_len
    cross = abs((tgt[0] - mx) * math.sin(ang) - (tgt[1] - my) * math.cos(ang))
    assert cross < 0.01, f"луч из ствола не проходит через цель: {cross:.3f} px"


def test_turret_deadzone_does_not_swallow_tolerance() -> None:
    """Деадзон башни не может быть шире допуска стрельбы.

    Раньше деадзон стоял 0.02 рад (1.15°), а геометрический допуск на
    750 px — около 0.67°. Башня переставала доводиться за полградуса ДО
    попадания в допуск, и ИИ стояли с наведённым стволом без единого
    выстрела. Теперь деадзон нулевой, джиттер гасит сам закон.
    """
    _, _, o = _obs()
    assert o.aim_turret((900.0, 448.0)) == 0.0, "деадзон должен быть нулевым"
    tol = o.hit_tolerance(750.0, 0.8)
    assert tol > 0.0, "допуск не должен схлопнуться"


def test_hit_tolerance_subtracts_spread() -> None:
    """Разброс ствола вычитается из геометрического запаса.

    Допуск 0.67° плюс разброс ±0.4° давали 1.07° бокового ухода, то
    есть 14 px при полуширине корпуса 8.8 px: треть выстрелов улетала
    в стену, хотя по геометрии «попадала».
    """
    _, _, o = _obs()
    hx, hy = o.target_half
    raw = math.atan2(min(hy, hx * 0.8) * 0.8, 750.0)
    tol = o.hit_tolerance(750.0, 0.8)
    assert math.degrees(tol) < math.degrees(raw), "разброс должен уменьшать допуск"
    assert abs(math.degrees(tol) - (math.degrees(raw) - o.spread_deg)) < 1e-6


def test_observation_carries_aiming_geometry() -> None:
    """В observation есть всё, что нужно для честного наведения."""
    _, _, o = _obs()
    assert o.muzzle_len == BALANCE.muzzle_offset
    assert o.spread_deg == BALANCE.spread
    assert o.bullet_turn_rate == BALANCE.turret_turn
    assert o.target_half == (17.0, 11.0)


def test_every_ai_pair_has_shots() -> None:
    """Ни одна пара ИИ не должна провести бой без единого выстрела."""
    import itertools

    from config import ROOT

    zeros = []
    for path_a, path_b in itertools.combinations(AI, 2):
        cfg = BattleConfig(
            a=PlayerConfig(kind="script", source=str(ROOT / path_a)),
            b=PlayerConfig(kind="script", source=str(ROOT / path_b)),
            map_name="arena", budget_ms=10.0, seed=2, max_seconds=25)
        run = BattleRun(cfg, "probe")
        run.run_sync()
        total = sum(run.replay.summary[k]["shots"] for k in ("a", "b"))
        if total == 0:
            zeros.append((path_a, path_b))
    assert not zeros, f"пары без выстрелов: {zeros}"

