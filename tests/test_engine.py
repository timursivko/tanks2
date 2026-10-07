"""Движок: броня, геометрия, границы карты, перезарядка, таран."""

from __future__ import annotations

import math

import pytest

from config import BALANCE
from engine.action import Action
from engine.armor import (classify_face, resolve_impact,
                           ricochet_deg)
from engine.geometry import obb_corners, segment_obb
from engine.grid import arena_from_rows, load_map
from engine.projectile import RICOCHET_SPEED_LOSS, Bullet, step_bullet
from engine.tank import Tank
from engine.world import World


@pytest.fixture()
def world() -> World:
    return World.create(load_map("arena"), BALANCE, seed=1)


def test_maps_are_symmetric_and_playable() -> None:
    for name in ("arena", "corridor", "colonnade", "fortress", "meadow"):
        arena = load_map(name)
        assert arena.is_symmetric(), f"{name}: карта не симметрична"
        assert len(arena.spawns) == 2
        assert arena.passable_px(arena.spawns[0].x, arena.spawns[0].y), "спавн A в стене"
        assert arena.passable_px(arena.spawns[1].x, arena.spawns[1].y), "спавн B в стене"
        assert len(arena.grid) == arena.height
        assert all(len(r) == arena.width for r in arena.grid), f"{name}: строки разной длины"


MAP_NAMES = ("arena", "corridor", "colonnade", "fortress", "meadow")


def drive_distance(name: str, index: int, seconds: float = 1.0) -> float:
    """Сколько пройдёт танк со спавна, если всю секунду держать газ."""
    arena = load_map(name)
    world = World.create(arena, BALANCE, seed=1)
    tank = world.tanks[index]
    x0, y0 = tank.x, tank.y
    for _ in range(round(seconds * BALANCE.tick_rate)):
        world.step([Action(drive=1.0), Action(drive=1.0)])
    return math.hypot(tank.x - x0, tank.y - y0)


@pytest.mark.parametrize("name", MAP_NAMES)
@pytest.mark.parametrize("index", (0, 1))
def test_spawn_is_not_jammed(name: str, index: int) -> None:
    """Со спавна можно уехать: газ двигает танк, а не упирается в укрытие.

    Раньше на «Арене» нос спавна смотрел в укрытие в полутора тайлах: газ
    давал 3-4 px за секунду, игрок считал, что клавиши не работают, а противник
    таранил стоящего на месте.
    """
    assert drive_distance(name, index) >= 40.0, f"{name}: спавн {index} зажат стенами"


@pytest.mark.parametrize("name", MAP_NAMES)
def test_spawn_heading_is_kept(name: str) -> None:
    """Защита спавна не должна крутить танк на исправных картах."""
    arena = load_map(name)
    world = World.create(arena, BALANCE, seed=1)
    for i, tank in enumerate(world.tanks):
        delta = (arena.spawns[i].angle - tank.hull + math.pi) % (2 * math.pi) - math.pi
        assert abs(delta) < 0.02, f"{name}: спавн {i} развернулся на {math.degrees(delta):.0f}°"


def test_spawn_inside_wall_is_rescued() -> None:
    """Своя карта со спавном в кладке: танк выталкивается и может уехать."""
    rows = ["#" * 24 for _ in range(12)]
    rows[5] = rows[6] = "#" + "." * 22 + "#"      # единственный проход
    rows[4] = "#" + "A" + "#" * 22               # спавн A внутри стены
    rows[7] = "#" + "B" + "." * 21 + "#"
    arena = arena_from_rows("test", rows)
    world = World.create(arena, BALANCE, seed=1)
    tank = world.tanks[0]
    assert arena.passable_px(tank.x, tank.y), "танк остался в стене"
    x0, y0 = tank.x, tank.y
    for _ in range(60):
        world.step([Action(drive=1.0), Action(drive=1.0)])
    assert math.hypot(tank.x - x0, tank.y - y0) >= 20.0, "со спавна в стене не уехать"


def dmg(nx, ny, dx, dy, hull=0.0) -> float:
    return resolve_impact(nx, ny, dx, dy, hull, BALANCE).damage


def test_armor_does_not_change_damage() -> None:
    """Броня не влияет на урон: пробитие стоит base_damage в любую грань."""
    front = dmg(1.0, 0.0, -1.0, 0.0)     # точно в лоб
    side = dmg(0.0, 1.0, 0.0, -1.0)
    rear = dmg(-1.0, 0.0, 1.0, 0.0)
    assert front == side == rear == BALANCE.base_damage, \
        f"лоб={front} борт={side} корма={rear}"


def test_classify_face_angles() -> None:
    assert classify_face(1.0, 0.0, 0.0, BALANCE) == "front"
    assert classify_face(-1.0, 0.0, 0.0, BALANCE) == "rear"
    assert classify_face(0.0, 1.0, 0.0, BALANCE) == "side"
    assert classify_face(0.0, -1.0, 0.0, BALANCE) == "side"


def test_grazing_hit_ricochets() -> None:
    imp = resolve_impact(0.0, 1.0, 1.0, 0.0, 0.0, BALANCE)
    assert imp.ricochet, "скользящий удар по борту должен рикошетить"
    assert imp.damage == BALANCE.ricochet_damage


def test_head_on_shot_hits_front_plate() -> None:
    """Встречный вылет бьёт в лоб и наносит полный урон.

    Лоб держит удар, но не гасит его: грань определяется верно, а урон равен
    base_damage — броня на урон не влияет.
    """
    w = World.create(load_map("meadow"), BALANCE, seed=1)
    a, b = w.tanks
    a.x, a.y, a.hull, a.turret = 700.0, 448.0, 0.0, 0.0
    b.x, b.y, b.hull, b.turret = 900.0, 448.0, math.pi, math.pi
    b.hp = b.hp_max = 1000.0
    events = w.step([Action(fire=True), Action()])
    for _ in range(40):
        events += w.step([Action(), Action()])
        if not w.bullets:
            break
    hits = [e for e in events if e["kind"] == "hit"]
    assert hits, "выстрел должен попасть в противника"
    assert hits[0]["face"] == "front", hits[0]
    assert not hits[0]["ricochet"], "выстрел в лоб по номиналу рикошетить не должен"
    assert hits[0]["damage"] == pytest.approx(BALANCE.base_damage)


def test_segment_obb_reports_normal() -> None:
    """Луч из (0,0) по +x пересекает прямоугольник с центром (100,0) и указывает
нормаль, смотрящую на луч."""
    res = segment_obb(0.0, 0.0, 1.0, 0.0, 100.0, 0.0, (10.0, 5.0), 0.0)
    assert res is not None
    t, nx, ny = res
    assert t == pytest.approx(90.0, abs=0.5)
    assert nx == pytest.approx(-1.0, abs=1e-6), "нормаль должна смотреть на луч"


def test_segment_obb_misses() -> None:
    assert segment_obb(0.0, 0.0, 1.0, 0.0, 100.0, 300.0, (10.0, 5.0), 0.0) is None


def test_segment_obb_respects_max_t() -> None:
    assert segment_obb(0.0, 0.0, 1.0, 0.0, 100.0, 0.0, (10.0, 5.0), 0.0,
                       max_t=10.0) is None, "луч не должен доходить до препятствия"


def test_segment_obb_hits_when_ray_starts_inside() -> None:
    """Луч, начавшийся ВНУТРИ корпуса, должен давать удар с t = 0.

    Иначе пуля, выпущенная вплотную (ствол 26 px внутри корпуса 17×11 px),
    улетает сквозь противника: обе грани остаются позади, грань входа не
    находится и пересечение считается отсутствующим.
    """
    half = (20.0, 14.0)
    inside = [
        ((710.0, 448.0), (1.0, 0.0)),
        ((690.0, 448.0), (-1.0, 0.0)),
        ((700.0, 455.0), (0.0, 1.0)),
        ((700.0, 441.0), (0.0, -1.0)),
    ]
    for (ox, oy), (dx, dy) in inside:
        res = segment_obb(ox, oy, dx, dy, 700.0, 448.0, half, 0.0, 10.33)
        assert res is not None, f"луч изнутри корпуса ({ox}, {oy}) пролетел насквозь"
        t, _nx, _ny = res
        assert t == pytest.approx(0.0, abs=1e-9), "удар должен быть в момент старта"


def test_segment_obb_inside_normal_opposes_flight() -> None:
    """Изнутри удар считается по грани, перпендикулярной полёту.

    Иначе на выходе ствола ровно на центр мишени ближайшей оказывается
    грань, совпадающая с направлением полёта, угол выходит 90°, и выстрел
    в упор рикошетит, не нанося урона.
    """
    res = segment_obb(726.0, 448.0, 1.0, 0.0, 720.0, 448.0, (20.0, 14.0),
                      0.0, 10.33)
    assert res is not None
    _t, nx, ny = res
    assert nx == pytest.approx(-1.0, abs=1e-6), "нормаль должна смотреть против полёта"


def test_segment_obb_still_misses_outside() -> None:
    """Правка не должна превращать в попадания лучи снаружи корпуса."""
    assert segment_obb(700.0, 470.0, 1.0, 0.0, 700.0, 448.0, (20.0, 14.0),
                       0.0, 10.33) is None
    assert segment_obb(760.0, 448.0, -1.0, 0.0, 700.0, 448.0, (20.0, 14.0),
                       0.0, 10.33) is None, "луч, уходящий от корпуса, — промах"


def test_point_blank_shot_does_not_tunnel(world: World) -> None:
    """Выстрел в упор наносит урон, а не пролетает сквозь противника."""
    a, b = world.tanks
    a.x, a.y, a.hull, a.turret = 700.0, 448.0, 0.0, 0.0
    a.cooldown = 0.0
    b.x, b.y, b.hull, b.turret = 734.0, 448.0, 0.0, 0.0
    b.hp = b.hp_max
    mx, my = a.muzzle(BALANCE)
    assert abs(mx - b.x) <= BALANCE.half_far[0], \
        "ствол должен оказаться внутри корпуса противника"

    events = world.step([Action(fire=True), Action()])
    for _ in range(30):
        if not world.bullets:
            break
        events += world.step([Action(), Action()])

    hits = [e for e in events if e["kind"] == "hit"]
    assert hits, "пуля в упор прошла сквозь противника"
    assert hits[0]["damage"] > 0.0, "выстрел в упор обязан наносить урон"
    assert b.hp < b.hp_max, "урон не нанесён"
    assert not world.bullets, "пуля должна остановиться на броне"


def test_point_blank_shot_deals_damage_at_every_close_gap(world: World) -> None:
    """На всех дистанциях, где ствол внутри корпуса, урон одинаковый.

    Проверяет, что попадание не превращается в рикошет при выходе ствола
    ровно на центр мишени (там ближайшая грань совпадает с направлением
    полёта и угол удара равен 90°).
    """
    a, b = world.tanks
    a.hull, a.turret = 0.0, 0.0
    covered: list[float] = []
    for gap in (18.0, 26.0, 34.0, 40.0, 55.0):
        a.x, a.y = 700.0, 448.0
        a.cooldown = 0.0
        b.x, b.y = 700.0 + gap, 448.0
        b.hp = b.hp_max
        mx, my = a.muzzle(BALANCE)
        inside = abs(mx - b.x) <= BALANCE.half_far[0] and abs(my - b.y) <= BALANCE.half_far[1]
        events = world.step([Action(fire=True), Action()])
        for _ in range(30):
            if not world.bullets:
                break
            events += world.step([Action(), Action()])
        hits = [e for e in events if e["kind"] == "hit"]
        assert hits, f"зазор {gap}: пуля прошла сквозь противника"
        assert hits[0]["damage"] > 0.0, f"зазор {gap}: урон не нанесён"
        assert not hits[0]["ricochet"], f"зазор {gap}: выстрел в упор рикошетил"
        if inside:
            covered.append(gap)
    assert len(covered) >= 3, "тест не покрывает дистанции со стволом внутри корпуса"


def test_obb_corners_rotate() -> None:
    corners = obb_corners(0.0, 0.0, (5.0, 3.0), 0.0)
    assert len(corners) == 4
    assert max(abs(p[0]) for p in corners) == pytest.approx(5.0)
    assert max(abs(p[1]) for p in corners) == pytest.approx(3.0)


def test_cooldown_does_not_stick(world: World) -> None:
    """Перезарядка ровно одна секунда, и орудие не залипает из-за округления."""
    tank = world.tanks[0]
    tank.cooldown = 0.0
    world.step([Action(fire=True), Action()])
    assert tank.shots == 1 and not tank.ammo_ready
    for _ in range(BALANCE.tick_rate):
        world.step([Action(), Action()])
    assert tank.ammo_ready, "через секунду орудие должно быть готово"
    assert tank.cooldown <= 1e-6, "остаток перезарядки должен обнулиться"
    world.step([Action(fire=True), Action()])
    assert tank.shots == 2


def test_tank_cannot_leave_the_map(world: World) -> None:
    tank = world.tanks[0]
    for _ in range(600):
        world.step([Action(drive=1.0, turn=1.0), Action()])
        assert 0 <= tank.x <= world.arena.pixel_width
        assert 0 <= tank.y <= world.arena.pixel_height
    assert world.arena.passable_px(tank.x, tank.y), "танк не должен встать в стену"


def test_ram_has_cooldown(world: World) -> None:
    """Разогнавшийся танк сбивает здоровье один раз, а не каждый тик."""
    a, b = world.tanks
    a.hull = a.turret = 0.0
    a.x, a.y = 400.0, 448.0
    b.x, b.y = 1200.0, 448.0
    for _ in range(30):
        world.step([Action(drive=1.0), Action()])
    b.x, b.y = a.x + 25.0, a.y          # подставляем противника под нос
    b.vx = b.vy = 0.0
    world.step([Action(drive=1.0), Action()])
    first = b.damage_taken
    assert first > 0, "таран должен снимать здоровье"
    for _ in range(5):
        world.step([Action(drive=1.0), Action()])
    assert b.damage_taken == pytest.approx(first), "таран нельзя повторять каждый тик"


def test_bullet_life_expires(world: World) -> None:
    world.step([Action(fire=True), Action()])
    assert world.bullets, "выстрел должен породить снаряд"
    for _ in range(650):
        world.step([Action(), Action()])
    assert not world.bullets, "снаряд должен исчезнуть"


def test_fire_needs_reload(world: World) -> None:
    a = world.tanks[0]
    for _ in range(180):
        world.step([Action(fire=True), Action()])
    assert 1 <= a.shots <= 4, f"слишком много выстрелов подряд: {a.shots}"


def test_walls_block_line_of_sight() -> None:
    arena = load_map("colonnade")
    spawn = arena.spawns[0]
    assert not arena.opaque_px(spawn.x, spawn.y), "спавн должен быть свободен"
    wall = None
    for ty in range(arena.height):
        for tx in range(arena.width):
            if arena.grid[ty][tx] == "#":
                wall = (tx * arena.tile + arena.tile / 2, ty * arena.tile + arena.tile / 2)
                break
        if wall:
            break
    assert wall, "на карте должна быть хотя бы одна стена"
    assert arena.opaque_px(*wall), "клетка стены непрозрачна"
    assert arena.blocked_px(*wall)

def _ricochet_setup():
    """Два танка в открытой зоне и снаряд, входящий в нижнюю грань по касательной."""
    a = Tank(id=0, x=500.0, y=300.0, hull=0.0, turret=0.0, name="A")
    b = Tank(id=1, x=700.0, y=440.0, hull=0.0, turret=0.0, name="B")
    a.configure(BALANCE)
    b.configure(BALANCE)
    slope = 0.1
    n = math.hypot(1.0, -slope)
    bl = Bullet(owner=0, x=600.0, y=465.0, dx=1.0 / n, dy=-slope / n,
                life=5.0, speed=BALANCE.bullet_speed)
    return bl, [a, b]


def test_ricochet_bounces_without_damage() -> None:
    """Отскок не наносит урона и продолжает полёт (раньше снаряд просто исчезал)."""
    bl, tanks = _ricochet_setup()
    ev = None
    for i in range(20):
        ev = step_bullet(bl, 1 / 60, load_map("arena"), tanks, BALANCE, i + 1)
        if ev:
            break
    assert ev and ev["kind"] == "hit", "скорость пули в 620 px/сек не долетела"
    assert ev["ricochet"], "скользящий удар должен рикошетить"
    assert ev["damage"] == 0.0 and tanks[1].hp == tanks[1].hp_max, "рикошет бьёт по HP"
    assert bl.alive, "после рикошета снаряд должен лететь дальше"
    assert tanks[1].ricochets_taken == 1


def test_ricochet_reflects_and_keeps_speed() -> None:
    """Вектор зеркалится от нормали грани, скорость сохраняется, броня не срабатывает дважды."""
    bl, tanks = _ricochet_setup()
    dx, dy = bl.dx, bl.dy
    for i in range(20):
        if step_bullet(bl, 1 / 60, load_map("arena"), tanks, BALANCE, i + 1):
            break
    assert abs(bl.dx - dx) < 1e-9, "отражается по X"
    assert abs(bl.dy + dy) < 1e-9, "отражается по Y"
    assert bl.speed == pytest.approx(BALANCE.bullet_speed * RICOCHET_SPEED_LOSS)
    assert bl.skip == 1, "сразу после отскока тот же корпус игнорируется"


def test_impact_point_sits_on_armor_edge() -> None:
    """Точка удара лежит на поверхности брони, а не на step дальше (был сдвиг 10 px)."""
    a = Tank(id=0, x=500.0, y=300.0, hull=0.0, turret=0.0, name="A")
    b = Tank(id=1, x=700.0, y=440.0, hull=0.0, turret=0.0, name="B")
    a.configure(BALANCE)
    b.configure(BALANCE)
    bl = Bullet(owner=0, x=700.0, y=520.0, dx=0.0, dy=-1.0,
                life=5.0, speed=BALANCE.bullet_speed)
    ev = None
    for i in range(20):
        ev = step_bullet(bl, 1 / 60, load_map("arena"), [a, b], BALANCE, i + 1)
        if ev:
            break
    assert ev and ev["kind"] == "hit"
    assert abs(ev["y"] - (b.y + BALANCE.half_far[1])) < 0.01, (
        f"удар смещён внутрь корпуса: {ev['y']} вместо {b.y + BALANCE.half_far[1]}")


def test_reverse_drive_actually_goes_backwards(world: World) -> None:
    """Отрицательная тяга должна везти назад, а не вперёд.

    Раньше модуль скорости брался как `-speed_rev`, что переворачивало знак:
    задний ход разгонял танк вперёд. Заметно и в ручном бою (S вёл вперёд),
    и в ИИ, где отступление упиралось в противника вместо отхода.
    """
    a, _b = world.tanks
    a.x, a.y, a.hull, a.vx, a.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    for _ in range(90):
        world.step([Action(drive=-1.0), Action()])
    assert a.vx < -BALANCE.speed_rev * 0.6, (
        f"задний ход не разогнал танк назад: vx={a.vx:.1f}")
    assert a.x < 700.0, "танк должен сместиться назад по курсу"


def test_forward_and_reverse_use_their_own_speed_limits(world: World) -> None:
    """Вперёд — speed_fwd, назад — speed_rev, и нигде не перепутано."""
    a, _b = world.tanks
    a.x, a.y, a.hull, a.vx, a.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    for _ in range(180):
        world.step([Action(drive=1.0), Action()])
    assert a.speed == pytest.approx(BALANCE.speed_fwd, rel=0.05), a.speed
    for _ in range(180):
        world.step([Action(drive=-1.0), Action()])
    assert -a.vx == pytest.approx(BALANCE.speed_rev, rel=0.05), a.vx


def test_tracks_swapping_turns_stands_the_tank_around(world: World) -> None:
    """Гусеницы вразнобой разворачивают танк на месте.

    Модель движения выводится из скоростей двух траков, поэтому разворот на
    месте — это просто предел `drive=0`: поступательная скорость нулевая,
    а угловая полная.
    """
    a, _b = world.tanks
    a.x, a.y, a.hull, a.vx, a.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    for _ in range(60):
        world.step([Action(turn=1.0), Action()])
    assert a.speed == pytest.approx(0.0, abs=1.0), a.speed
    assert a.hull == pytest.approx(
        60 * BALANCE.hull_turn * world.dt, rel=0.05), math.degrees(a.hull)


def test_throttle_and_steering_share_the_same_tracks() -> None:
    """Газ с рулением идёт по дуге и теряет половину скорости.

    Скорость тут перестала быть `drive` с поправочным штрафом, а стала
    средним по тракам: полный газ с полным ручением даёт `speed_fwd / 2`,
    а угловая скорость падает так же.
    """
    w = World.create(load_map("arena"), BALANCE, seed=3)
    a, b = w.tanks
    a.x, a.y, a.hull, a.vx, a.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    b.x, b.y = -900.0, -900.0
    for _ in range(180):
        w.step([Action(drive=1.0, turn=1.0), Action()])
    assert a.speed == pytest.approx(BALANCE.speed_fwd * 0.5, rel=0.05), a.speed

    # Полный газ без ручения даёт полный потолок: поворот не должен его резать.
    w2 = World.create(load_map("arena"), BALANCE, seed=3)
    c, _d = w2.tanks
    c.x, c.y, c.hull, c.vx, c.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    for _ in range(180):
        w2.step([Action(drive=1.0), Action()])
    assert c.speed == pytest.approx(BALANCE.speed_fwd, rel=0.05), c.speed


def test_steering_direction_does_not_flip_with_the_gear(world: World) -> None:
    """Передача на разворот не влияет: D уводит нос в одну и ту же сторону.

    Так ведут себя аркадные танки, и на этом завязаны ручной ввод и ИИ.
    """
    a, _b = world.tanks
    a.x, a.y, a.hull, a.vx, a.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    for _ in range(90):
        world.step([Action(drive=1.0, turn=1.0), Action()])
    ahead = a.hull
    a.x, a.y, a.hull, a.vx, a.vy = 700.0, 448.0, 0.0, 0.0, 0.0
    for _ in range(90):
        world.step([Action(drive=-1.0, turn=1.0), Action()])
    assert a.hull == pytest.approx(ahead, abs=1e-6), (ahead, a.hull)


def test_ram_pushes_attacker_backwards() -> None:
    """Таран срывает скорость атакующего, а не разгоняет его."""
    w = World.create(load_map("arena"), BALANCE, seed=3)
    a, b = w.tanks
    a.x, a.y, a.hull, a.turret = 700.0, 448.0, 0.0, 0.0
    b.x, b.y, b.hull, b.turret = 760.0, 448.0, math.pi, math.pi
    # Защитник идёт лобовой таранкой: drive=+1 — это вперёд по курсу корпуса,
    # который у него развёрнут на пи (в сторону атакующего).
    for _ in range(90):
        w.step([Action(drive=1.0), Action(drive=1.0)])
        if a.rams:
            break
    assert a.rams, "танки не столкнулись"
    assert a.vx <= 0.0, f"атакующий разогнался назад от тарана: vx={a.vx:.1f}"


def test_engine_payload_field_order() -> None:
    """Порядок полей нагрузки — контракт с SDK (см. tankp.ENGINE_PAYLOAD_FIELDS).

    Быстрый путь ``tankp.Observation`` распаковывает словарь движка одним
    проходом, поэтому поля обязаны идти ровно в этом порядке: если он
    поменяется, значения уедут в чужие поля молча.
    """
    import sys as _sys
    from pathlib import Path as _Path

    sdk = _Path(__file__).resolve().parents[1] / "engine" / "sdk"
    if str(sdk) not in _sys.path:
        _sys.path.insert(0, str(sdk))
    import tankp

    from engine.observation import build_observation

    w = World.create(load_map("arena"), BALANCE, seed=1)
    payload = build_observation(w, 0, BALANCE.default_budget_ms)
    assert tuple(payload) == tankp.ENGINE_PAYLOAD_FIELDS

def test_engine_tuple_payload_matches_dict() -> None:
    """Кортежная нагрузка (``build_both``) — то же наблюдение, что словарная.

    Горячий путь ``tools/matrix.py`` получает наблюдения кортежем (см.
    ``payload_tuple``), а сервер и песочница — словарём (``build_observation``).
    Оба обязаны давать SDK одно и то же: сверяем все поля, включая видимость
    противника и оба представления танков.
    """
    import sys as _sys
    from pathlib import Path as _Path

    sdk = _Path(__file__).resolve().parents[1] / "engine" / "sdk"
    if str(sdk) not in _sys.path:
        _sys.path.insert(0, str(sdk))
    import tankp

    from engine.observation import build_both, build_observation

    w = World.create(load_map("arena"), BALANCE, seed=1)
    a, b = w.tanks
    # Подводим противника вплотную: интересен и случай «видит», и «не видит».
    b.x = a.x + 60.0
    b.y = a.y
    w.update_vision()
    assert a.sees_enemy, "тест рассчитывал на видимого противника"

    for index in (0, 1):
        d = build_observation(w, index, BALANCE.default_budget_ms)
        t = build_both(w, BALANCE.default_budget_ms)[index]
        assert len(t) == len(tankp.ENGINE_PAYLOAD_FIELDS)
        o_dict = tankp.Observation(d, None)
        o_tup = tankp.Observation(t, None)
        for field in ("tick", "time", "dt", "tank", "budget_ms", "bullet_speed",
                      "bullet_turn_rate", "muzzle_len", "spread_deg", "target_half"):
            assert getattr(o_dict, field) == getattr(o_tup, field), field
        for view_name in ("me", "enemy"):
            left, right = getattr(o_dict, view_name), getattr(o_tup, view_name)
            assert (left is None) == (right is None), view_name
            if left is not None:
                for attr in ("x", "y", "hull", "turret", "vx", "vy", "hp", "hp_max",
                             "ammo_ready", "cooldown", "reload"):
                    assert getattr(left, attr) == getattr(right, attr), (view_name, attr)
        for attr in ("enemy_visible", "in_cone", "in_range", "los_clear", "bumper",
                     "bearing", "distance"):
            assert getattr(o_dict.sight, attr) == getattr(o_tup.sight, attr), attr
