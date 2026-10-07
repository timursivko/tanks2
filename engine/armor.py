"""Модель брони: грань корпуса, угол удара, рикошет.

Броня не влияет на урон: пробитие стоит ровно ``base_damage`` в лоб, в борт и
в корму. Единственное её свойство — рикошет. Снаряд, прилетевший под большим
углом к нормали грани, скользит и не пробивает: у лба порог высокий, у борта
вдвое ниже, а корма не рикошетит никогда.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from config import Balance
from engine.geometry import ang_diff

FACE_FRONT = "front"
FACE_SIDE = "side"
FACE_REAR = "rear"

FACE_LABELS = {
    FACE_FRONT: "лоб",
    FACE_SIDE: "борт",
    FACE_REAR: "корма",
}


@dataclass(frozen=True)
class Impact:
    """Результат разбора попадания снаряда в танк."""

    damage: float
    face: str
    theta_deg: float
    ricochet: bool

    @property
    def face_label(self) -> str:
        return FACE_LABELS[self.face]


def ricochet_deg(face: str, bal: Balance) -> float | None:
    """С какого угла к нормали грани снаряд рикошетит, а не пробивает.

    ``None`` означает, что порога нет вовсе: так устроена корма, она не держит
    ничего и потому не рикошетит никогда.
    """
    return bal.ricochet_angles.get(face)


def grazing_limit_deg(face: str, bal: Balance) -> float:
    """Любой угол до 90 градусов достижим, если снаряд попадает не в центр грани, 
    а ближе к краю. Поэтому геометрического предела нет."""
    return 90.0


def classify_face(nx: float, ny: float, hull_angle: float, bal: Balance) -> str:
    """Определяет грань корпуса по нормали удара в системе координат танка.

    ``(nx, ny)`` — нормаль грани, направленная наружу от танка. Нормаль лобового
    листа смотрит по курсу, поэтому ``rel`` около нуля — это лоб.
    """
    rel = abs(math.degrees(ang_diff(math.atan2(ny, nx), hull_angle)))
    if rel <= bal.front_half_angle:
        return FACE_FRONT
    if rel >= 180.0 - bal.front_half_angle:
        return FACE_REAR
    return FACE_SIDE


def resolve_impact(nx: float, ny: float, dirx: float, diry: float,
                   hull_angle: float, bal: Balance) -> Impact:
    """Разбирает попадание снаряда, летящего в направлении ``(dirx, diry)``.

    ``(nx, ny)`` — нормаль грани в мировых координатах, направленная наружу.
    Урон от брони не зависит: он равен ``base_damage``, пока снаряд пробивает.
    """
    nlen = math.hypot(nx, ny) or 1.0
    nx, ny = nx / nlen, ny / nlen
    # Угол между направлением полёта снаряда и нормалью грани (0 — точно в лоб).
    cos_theta = -(dirx * nx + diry * ny)
    cos_theta = max(0.0, min(1.0, cos_theta))
    theta = math.degrees(math.acos(cos_theta))
    face = classify_face(nx, ny, hull_angle, bal)
    limit = ricochet_deg(face, bal)

    if limit is not None and theta >= limit:
        return Impact(bal.ricochet_damage, face, theta, True)
    return Impact(bal.base_damage, face, theta, False)


def armor_table(bal: Balance) -> list[dict]:
    """Справочная таблица «что сделает удар под таким углом» — для панели баланса в UI.

    Строки идут по граням и углам; в ячейке либо урон, либо пометка о рикошете.
    ``damage`` одинаков для всех пробитий, поэтому таблица показывает именно
    то, что броня действительно меняет, — порог рикошета по грани.

    В таблицу попадают сами пороги, чтобы переход «пробитие → рикошет» был виден.
    """
    rows = []
    base_angles = {0.0, 20.0, 35.0}
    for f in (FACE_FRONT, FACE_SIDE, FACE_REAR):
        l = ricochet_deg(f, bal)
        if l is not None:
            base_angles.add(l)

    for face in (FACE_FRONT, FACE_SIDE, FACE_REAR):
        limit = ricochet_deg(face, bal)
        reach = grazing_limit_deg(face, bal)
        for theta in sorted(base_angles):
            possible = True
            ricochet = limit is not None and theta >= limit
            damage = bal.ricochet_damage if ricochet else bal.base_damage
            rows.append({
                "face": face, "theta": theta, "damage": round(damage, 2),
                "ricochet": ricochet, "possible": possible,
                "threshold": limit, "reach": 90.0,
                "shots": None if ricochet else math.ceil(bal.hp / damage) if damage else None,
            })
    return rows