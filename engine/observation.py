"""Сборка наблюдения: ровно те данные, которые «знает» танк в этом тике.

Скрипту отдаётся собственный танк всегда, противник — только если он попал
в зону видимости (конус + дальность + чистая линия взгляда).
Память о последней известной позиции движок не передаёт: держать её можно
внутри скрипта самостоятельно.
"""

from __future__ import annotations

import math

from engine.world import World


def map_payload(world: World) -> dict:
    """Описание карты, передаётся скрипту один раз при старте."""
    arena = world.arena
    return {
        "tile": arena.tile,
        "width": arena.width,
        "height": arena.height,
        "rows": ["".join(r) for r in arena.grid],
        # Копия: скрипт получает собственные данные и не может случайно
        # испортить арену (одна и та же арена переиспользуется между боями,
        # см. tools/matrix.py).
        "clearance": [list(row) for row in arena.clearance],
        "spawns": [{"x": s.x, "y": s.y, "angle": s.angle} for s in arena.spawns],
        "pixel_width": arena.pixel_width,
        "pixel_height": arena.pixel_height,
    }


def view_tuple(t, reload_value: float) -> tuple:
    """11 полей танка в порядке ``Tank.view`` — кортежем и без словаря.

    Округления те же, что отдаёт ``Tank.view``: значения, которые видит
    скрипт, не меняются. Один и тот же кортеж годится и для «своего» танка,
    и для противника: набор точностей у них совпадает, поэтому за тик он
    собирается один раз на танк (см. ``build_both``).
    """
    return (round(t.x, 2), round(t.y, 2), round(t.hull, 5), round(t.turret, 5),
            round(t.vx, 2), round(t.vy, 2), round(t.hp, 3), t.hp_max,
            t.ammo_ready, round(t.cooldown, 4), reload_value)


def payload(world: World, i: int, budget_ms: float, me_view, enemy_view,
            sight, half, time_rounded: float) -> dict:
    """Полезная нагрузка наблюдения: значение каждого поля — как раньше.

    ``time_rounded`` и ``dt`` считаются один раз на тик (в ``build_both`` их
    делят оба наблюдения, а ``dt`` вообще постоянен для боя): раунд с
    округлением — один из самых дорогих вызовов в тике, а значения те же.
    """
    bal = world.bal
    return {
        "tick": world.tick,
        "time": time_rounded,
        "dt": world.dt_rounded,
        "tank": i,
        "budget_ms": budget_ms,
        "me": me_view,
        "enemy": enemy_view,
        "bullet_speed": bal.bullet_speed,
        # Скорость поворота башни нужна скрипту, чтобы не стрелять, пока
        # башня ещё доворачивается: снаряд ушёл бы в сторону от цели.
        "turret_turn": bal.turret_turn,
        # Геометрия собственного корпуса: снаряд появляется на конце
        # ствола, а не в центре, и в цель надо попасть этим смещением.
        "muzzle_offset": bal.muzzle_offset,
        "half": half,
        # Разброс ствола нужен скрипту, чтобы вычесть его из допуска:
        # иначе «попадание по геометрии» систематически оказывается
        # промахом на дальних дистанциях.
        "spread": bal.spread,
        "sight": sight,
    }


def build_observation(world: World, i: int, budget_ms: float) -> dict:
    """Наблюдение для танка ``i`` в текущем состоянии мира (до шага).

    Словари «свой танк» и «противник» собираются здесь же: ``Tank.view``
    оставлен для других вызывающих, а на каждый тик лучше обойтись без
    лишнего вызова и повторных чтений атрибутов. Значения те же (включая
    округления), поэтому скрипты видят ровно то, что и раньше.
    """
    me = world.tanks[i]
    foe = world.tanks[1 - i]
    reload_value = world.bal.reload
    enemy_view = None
    if me.sees_enemy and foe.alive:
        enemy_view = view_tuple(foe, reload_value)
    sight = me.sight_detail
    if not sight:
        # Видимость ещё не считалась (танк собран в обход World.create).
        sight = (False, False, False, False, False, None, None)
    return payload(world, i, budget_ms, view_tuple(me, reload_value),
                   enemy_view, sight, me.half_size,
                   round(world.tick * world.dt, 5))


def build_both(world: World, budget_ms: float) -> tuple[dict, dict]:
    """Наблюдения обоих танков за один проход.

    Порядок тот же, что при двух вызовах ``build_observation``: сначала
    танк 0, потом танк 1, — а округлённые поля каждого танка считаются один
    раз, а не дважды (они не зависят от того, чьим наблюдением танк сейчас
    является: точности у «своего» и «врага» совпадают).
    """
    a, b = world.tanks
    reload_value = world.bal.reload
    # Время тика для обоих наблюдений одно и то же — округляем один раз.
    time_rounded = round(world.tick * world.dt, 5)
    view_a = view_tuple(a, reload_value)
    view_b = view_tuple(b, reload_value)
    alive_a = a.alive
    alive_b = b.alive
    sight_a = a.sight_detail
    sight_b = b.sight_detail
    if not sight_a:
        sight_a = (False, False, False, False, False, None, None)
    if not sight_b:
        sight_b = (False, False, False, False, False, None, None)
    return (payload(world, 0, budget_ms, view_a, view_b if (a.sees_enemy and alive_b) else None,
                    sight_a, a.half_size, time_rounded),
            payload(world, 1, budget_ms, view_b, view_a if (b.sees_enemy and alive_a) else None,
                    sight_b, b.half_size, time_rounded))


def deg(v: float) -> float:
    return math.degrees(v)


def rad(v: float) -> float:
    return math.radians(v)


def sign(v: float) -> float:
    return (v > 0) - (v < 0)