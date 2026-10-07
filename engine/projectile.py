"""Снаряды: свип по стенам (DDA) и по корпусу (OBB), разбор попадания."""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import Balance
from engine import armor
from engine.geometry import dist, segment_obb
from engine.grid import Arena
from engine.visibility import ray_grid_detailed

#: Сколько скорости теряет снаряд при отскоке от брони.
RICOCHET_SPEED_LOSS = 1.0
#: Насколько вынести снаряд за точку касания, чтобы он сразу не попал
#: в ту же грань снова (толщина брони + небольшой запас).
RICOCHET_PUSH = 2.0
#: Сколько секунд после рикошета корпус не считается препятствием.
RICOCHET_SKIP_TIME = 0.08


@dataclass
class Bullet:
    """Снаряд в полёте."""

    owner: int
    x: float
    y: float
    dx: float
    dy: float
    life: float
    speed: float
    alive: bool = True
    track: list[tuple[float, float, int]] = None  # type: ignore[assignment]
    tick: int = 0                                  # тик последней точки трека
    # Корпус, от которого снаряд только что отскочил. Пока он не
    # пролетит мимо него целиком, повторно броня не срабатывает: иначе
    # отскок от ближней грани тут же превратился бы в новое попадание
    # в ту же броню, и рикошет выглядел бы как мгновенное исчезновение.
    skip: int = -1

    def __post_init__(self) -> None:
        if self.track is None:
            self.track = [(self.x, self.y, self.tick)]

    @property
    def tail(self) -> tuple[float, float]:
        return (self.x - self.dx * 14.0, self.y - self.dy * 14.0)

    def advance(self, dt: float) -> None:
        self.x += self.dx * self.speed * dt
        self.y += self.dy * self.speed * dt
        self.life -= dt
        if self.life <= 0:
            self.alive = False


def spawn(tank, bal: Balance, rng) -> Bullet | None:
    """Создаёт снаряд из ствола. ``None``, если выстрел невозможен."""
    if not tank.ammo_ready:
        return None
    mx, my = tank.muzzle(bal)
    angle = tank.turret + math.radians(bal.spread) * (rng.random() * 2.0 - 1.0)
    dx, dy = math.cos(angle), math.sin(angle)
    tank.cooldown = bal.reload
    tank.shots += 1
    return Bullet(owner=tank.id, x=mx, y=my, dx=dx, dy=dy,
                  life=bal.bullet_life, speed=bal.bullet_speed)


def step_bullet(b: Bullet, dt: float, arena: Arena, tanks, bal: Balance,
                tick: int = 0) -> dict | None:
    """Продвигает снаряд на ``dt`` и разбирает столкновения.

    ``tick`` — номер такта: он кладётся в трек, чтобы реплей знал, когда
    была пройдена каждая точка. Без этого траекторию нельзя обрезать по
    времени и она рисуется целиком до конца боя.
    """
    b.tick = tick
    b.advance(dt)
    b.track.append((b.x, b.y, tick))

    step = b.speed * dt
    res_wall = ray_grid_detailed(arena, b.x - b.dx * step, b.y - b.dy * step, b.dx, b.dy, step)
    t_wall = res_wall[0] if res_wall else None

    hit_t = None
    target = None
    normal = None
    for t in tanks:
        if not t.alive or (t.id == b.owner and not bal.self_damage):
            continue
        if t.id == b.skip:
            continue
        res = segment_obb(b.x - b.dx * step, b.y - b.dy * step, b.dx, b.dy,
                          t.x, t.y, bal.half_far, t.hull, step)
        if res and (hit_t is None or res[0] < hit_t):
            hit_t, target, normal = res[0], t, (res[1], res[2])

    if target is not None and (t_wall is None or hit_t < t_wall):
        # Точка касания. Свип идёт от (x - dx*step), поэтому и координата
        # удара считается от той же точки: раньше здесь прибавляли step,
        # и искры с последним point трека уезжали внутрь брони на 10 px.
        hx = b.x - b.dx * step + b.dx * hit_t
        hy = b.y - b.dy * step + b.dy * hit_t
        imp = armor.resolve_impact(normal[0], normal[1], b.dx, b.dy, target.hull, bal)
        if imp.ricochet:
            # Отскок без урона: зеркально отражаем вектор от нормали
            # грани, отбрасываем снаряд на толщину брони и сохраняем
            # скорость. Раньше здесь стояло b.alive = False до
            # resolve_impact, то есть «рикошет» был только флагом в
            # статистике: снаряд просто исчезал у борта.
            nx, ny = normal
            dot = b.dx * nx + b.dy * ny
            b.dx -= 2.0 * dot * nx
            b.dy -= 2.0 * dot * ny
            b.speed *= RICOCHET_SPEED_LOSS
            b.x = hx + b.dx * RICOCHET_PUSH
            b.y = hy + b.dy * RICOCHET_PUSH
            b.skip = target.id
            target.ricochets_taken += 1
            return {"kind": "hit", "owner": b.owner, "target": target.id,
                    "damage": imp.damage, "face": imp.face, "theta": imp.theta_deg,
                    "ricochet": True, "x": round(hx, 1), "y": round(hy, 1)}
        b.alive = False
        target.hp = max(0.0, round(target.hp - imp.damage, 4))
        target.damage_taken += imp.damage
        return {"kind": "hit", "owner": b.owner, "target": target.id,
                "damage": imp.damage, "face": imp.face, "theta": imp.theta_deg,
                "ricochet": False, "x": round(hx, 1), "y": round(hy, 1)}

    if t_wall is not None:
        wx = b.x - b.dx * step + b.dx * t_wall
        wy = b.y - b.dy * step + b.dy * t_wall
        nx, ny = res_wall[1], res_wall[2]

        cos_theta = -(b.dx * nx + b.dy * ny)
        cos_theta = max(0.0, min(1.0, cos_theta))
        theta = math.degrees(math.acos(cos_theta))

        if theta >= bal.wall_ricochet_angle:
            dot = b.dx * nx + b.dy * ny
            b.dx -= 2.0 * dot * nx
            b.dy -= 2.0 * dot * ny
            b.speed *= RICOCHET_SPEED_LOSS
            b.x = wx + b.dx * RICOCHET_PUSH
            b.y = wy + b.dy * RICOCHET_PUSH
            return {"kind": "spark", "owner": b.owner,
                    "x": round(wx, 1), "y": round(wy, 1)}

        b.alive = False
        return {"kind": "wall", "owner": b.owner,
                "x": round(wx, 1), "y": round(wy, 1)}

    # Пролетели мимо корпуса, от которого отскакивали: снова можно
    # с ним столкнуться (снаряд мог разлететься в другую сторону).
    if b.skip >= 0 and not _near_tank(b, tanks[b.skip], bal):
        b.skip = -1
    return None


def _near_tank(b: Bullet, tank, bal: Balance) -> bool:
    """Находится ли снаряд ещё вплотную к корпусу (после рикошета)."""
    if tank is None or not tank.alive:
        return False
    hx, hy = bal.half_far
    pad = max(hx, hy) + b.speed * RICOCHET_SKIP_TIME
    return dist(b.x, b.y, tank.x, tank.y) < pad


def barrel_blocked(tank, arena: Arena, bal: Balance) -> bool:
    """Ствол упирается в стену — выстрел невозможен (снаряд был бы внутри стены)."""
    mx, my = tank.muzzle(bal)
    return arena.opaque_px(mx, my) or arena.blocked_px(mx, my)


def aim_error(a, b) -> float:
    """Вспомогательное: ошибка наведения (градусы) — используется в тестах ИИ."""
    d = dist(a.x, a.y, b.x, b.y)
    if d < 1e-6:
        return 0.0
    want = math.atan2(b.y - a.y, b.x - a.x)
    return abs(math.degrees(math.atan2(math.sin(want - a.turret),
                                      math.cos(want - a.turret))))