"""Геометрия: углы, лучи по сетке и пересечение отрезка с повёрнутым прямоугольником."""

from __future__ import annotations

import math

TAU = math.pi * 2.0
INF = float("inf")


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def wrap_angle(a: float) -> float:
    """Приводит угол к диапазону (-pi, pi]."""
    a = math.fmod(a + math.pi, TAU)
    if a < 0:
        a += TAU
    return a - math.pi


def ang_diff(a: float, b: float) -> float:
    """Знаковая разность углов a - b в диапазоне (-pi, pi]."""
    return wrap_angle(a - b)


def dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(bx - ax, by - ay)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def norm_angle_deg(a: float) -> float:
    """Нормализует угол в градусах к [0, 360)."""
    return math.fmod(math.degrees(a), 360.0) + (360.0 if math.degrees(a) < 0 else 0.0)


def local_point(px: float, py: float, cx: float, cy: float, angle: float) -> tuple[float, float]:
    """Переводит мировую точку в локальную систему прямоугольника."""
    ca = math.cos(angle)
    sa = math.sin(angle)
    ox = px - cx
    oy = py - cy
    return (ox * ca + oy * sa, -ox * sa + oy * ca)


def point_in_obb(px: float, py: float, cx: float, cy: float, half, angle: float) -> bool:
    lx, ly = local_point(px, py, cx, cy, angle)
    return abs(lx) <= half[0] and abs(ly) <= half[1]


def obb_corners(cx: float, cy: float, half, angle: float) -> list[tuple[float, float]]:
    ca = math.cos(angle)
    sa = math.sin(angle)
    hx, hy = half
    pts = []
    for lx, ly in ((hx, hy), (-hx, hy), (-hx, -hy), (hx, -hy)):
        pts.append((cx + lx * ca - ly * sa, cy + lx * sa + ly * ca))
    return pts


def segment_obb(ox: float, oy: float, dx: float, dy: float,
                cx: float, cy: float, half, angle: float,
                max_t: float = INF) -> tuple[float, float, float] | None:
    """Пересечение луча (o, d) с повёрнутым прямоугольником.

    Возвращает ``(t, nx, ny)``, где ``t`` — параметр вдоль ``d`` (нормализованного),
    а ``(nx, ny)`` — нормаль грани в мировых координатах, направленная наружу.
    ``None``, если пересечения нет.

    Если луч СТАРТУЕТ ВНУТРИ прямоугольника, возвращается удар с ``t = 0``
    и нормалью той грани, которая перпендикулярна направлению полёта.
    Такой случай не экзотический: когда танки прижаты друг к другу, конец
    ствола (26 px от центра) оказывается внутри корпуса противника
    (17×11 px), и обычный вход по граням невозможен — обе грани «позади»,
    поэтому ``tmin`` остаётся нулевым, а грань входа не находится. Раньше
    это давало ``None``, и пуля улетала сквозь врага на расстоянии
    вытянутой руки.

    Именно перпендикулярная грань, а не ближайшая: когда ствол выходит
    ровно на центр мишени, ближайшей оказывается грань, СОВПАДАЮЩАЯ с
    направлением полёта, и угол удара выходил 90° — выстрел в упор
    рикошетил, не нанося урона.
    """
    ca = math.cos(angle)
    sa = math.sin(angle)
    rx = ox - cx
    ry = oy - cy
    lx = rx * ca + ry * sa
    ly = -rx * sa + ry * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca

    tmin = 0.0
    tmax = max_t
    axis = -1
    sign = 0.0
    for i, (p, q, h) in enumerate(((lx, ldx, half[0]), (ly, ldy, half[1]))):
        if abs(q) < 1e-12:
            if abs(p) > h:
                return None
            continue
        s = -1.0 if q > 0 else 1.0
        t1 = (-h - p) / q
        t2 = (h - p) / q
        if t1 > t2:
            # Входом всегда становится ближняя грань; нормаль смотрит наружу,
            # поэтому знак задаётся направлением движения, а не порядком граней.
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = i
            sign = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    if axis < 0:
        # Грани входа нет — луч уже внутри корпуса. Учитываем удар сразу.
        if tmax < 0.0:
            return None
        if abs(ldx) >= abs(ldy):
            n = (-1.0 if ldx > 0.0 else 1.0, 0.0)
        else:
            n = (0.0, -1.0 if ldy > 0.0 else 1.0)
        return 0.0, n[0] * ca - n[1] * sa, n[0] * sa + n[1] * ca
    if tmin > max_t:
        return None
    if axis == 0:
        n = (sign, 0.0)
    else:
        n = (0.0, sign)
    return tmin, n[0] * ca - n[1] * sa, n[0] * sa + n[1] * ca


def obb_aabb(cx: float, cy: float, half, angle: float) -> tuple[float, float, float, float]:
    """AABB повёрнутого прямоугольника: (minx, miny, maxx, maxy)."""
    ca = abs(math.cos(angle))
    sa = abs(math.sin(angle))
    ex = half[0] * ca + half[1] * sa
    ey = half[0] * sa + half[1] * ca
    return (cx - ex, cy - ey, cx + ex, cy + ey)


def resolve_obb_aabb(cx: float, cy: float, half, angle: float,
                     ax0: float, ay0: float, ax1: float, ay1: float) -> tuple[float, float, float] | None:
    """Выталкивание повёрнутого прямоугольника из AABB (SAT по 4 осям).

    Возвращает ``(depth, nx, ny)`` — минимальное смещение, которое убирает
    пересечение, либо ``None``, если пересечения нет.
    """
    ca = math.cos(angle)
    sa = math.sin(angle)
    axes = ((1.0, 0.0), (0.0, 1.0), (ca, sa), (-sa, ca))
    tcx = (ax0 + ax1) * 0.5
    tcy = (ay0 + ay1) * 0.5
    thx = (ax1 - ax0) * 0.5
    thy = (ay1 - ay0) * 0.5
    dx = cx - tcx
    dy = cy - tcy

    best = INF
    bnx = 0.0
    bny = 0.0
    for ux, uy in axes:
        r_a = half[0] * abs(ca * ux + sa * uy) + half[1] * abs(-sa * ux + ca * uy)
        r_b = thx * abs(ux) + thy * abs(uy)
        proj = dx * ux + dy * uy
        overlap = (r_a + r_b) - abs(proj)
        if overlap <= 0.0:
            return None
        if overlap < best:
            best = overlap
            if proj > 0:
                bnx, bny = ux, uy
            else:
                bnx, bny = -ux, -uy
    return best, bnx, bny


def ray_circle(ox: float, oy: float, dx: float, dy: float,
               cx: float, cy: float, r: float, max_t: float = INF) -> float | None:
    """Пересечение луча с окружностью: возвращает t входа или None."""
    mx = ox - cx
    my = oy - cy
    b = mx * dx + my * dy
    c = mx * mx + my * my - r * r
    if c > 0 and b > 0:
        return None
    disc = b * b - c
    if disc < 0:
        return None
    t = -b - math.sqrt(disc)
    if t < 0.0:
        t = 0.0
    if t > max_t:
        return None
    return t