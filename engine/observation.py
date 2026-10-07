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
        "clearance": arena.clearance,
        "spawns": [{"x": s.x, "y": s.y, "angle": s.angle} for s in arena.spawns],
        "pixel_width": arena.pixel_width,
        "pixel_height": arena.pixel_height,
    }


def build_observation(world: World, i: int, budget_ms: float) -> dict:
    """Наблюдение для танка ``i`` в текущем состоянии мира (до шага)."""
    me = world.tanks[i]
    foe = world.tanks[1 - i]
    s = me.sight_detail or {}
    return {
        "tick": world.tick,
        "time": round(world.tick * world.dt, 5),
        "dt": round(world.dt, 5),
        "tank": i,
        "budget_ms": budget_ms,
        "me": me.view(world.bal),
        "enemy": foe.view(world.bal) if (me.sees_enemy and foe.alive) else None,
        "bullet_speed": world.bal.bullet_speed,
        # Скорость поворота башни нужна скрипту, чтобы не стрелять, пока
        # башня ещё доворачивается: снаряд ушёл бы в сторону от цели.
        "turret_turn": world.bal.turret_turn,
        # Геометрия собственного корпуса: снаряд появляется на конце
        # ствола, а не в центре, и в цель надо попасть этим смещением.
        "muzzle_offset": world.bal.muzzle_offset,
        "half": list(world.tanks[i].half_size),
        # Разброс ствола нужен скрипту, чтобы вычесть его из допуска:
        # иначе «попадание по геометрии» систематически оказывается
        # промахом на дальних дистанциях.
        "spread": world.bal.spread,
        "sight": {
            "enemy_visible": bool(me.sees_enemy and foe.alive),
            "in_cone": bool(s.get("in_cone")),
            "in_range": bool(s.get("in_range")),
            "los_clear": bool(s.get("los_clear")),
            "bumper": bool(s.get("bumper")),
            "bearing": s.get("bearing"),
            "distance": s.get("distance"),
        },
    }


def build_both(world: World, budget_ms: float) -> tuple[dict, dict]:
    return build_observation(world, 0, budget_ms), build_observation(world, 1, budget_ms)


def deg(v: float) -> float:
    return math.degrees(v)


def rad(v: float) -> float:
    return math.radians(v)


def sign(v: float) -> float:
    return (v > 0) - (v < 0)