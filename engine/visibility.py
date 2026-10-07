"""Видимость: зона обзора, дальность, «бамперы» и проверка линии взгляда.

Один источник истины — тот же набор правил повторяет браузер для отрисовки
тумана, поэтому картинка всегда совпадает с тем, что «знает» танк.
"""

from __future__ import annotations

import math

from config import Balance
from engine.geometry import INF, ang_diff, obb_corners
from engine.grid import Arena


def ray_grid_detailed(arena: Arena, ox: float, oy: float, dx: float, dy: float,
                      max_t: float, predicate=None) -> tuple[float, float, float] | None:
    """DDA-обход сетки: возвращает (t, nx, ny).

    Без своего ``predicate`` (самый частый случай — видимость и снаряды)
    уходит в ``_ray_grid_opaque``: та же арифметика, но ответ берётся из
    заранее собранной маски тайлов, без вызова функции на каждый шаг.
    """
    if predicate is None:
        return _ray_grid_opaque(arena, ox, oy, dx, dy, max_t)
    tile = float(arena.tile)
    x = int(ox // arena.tile)
    y = int(oy // arena.tile)

    step_x = 1 if dx > 0 else -1
    step_y = 1 if dy > 0 else -1
    t_delta_x = abs(tile / dx) if dx != 0 else INF
    t_delta_y = abs(tile / dy) if dy != 0 else INF
    if dx > 0:
        t_max_x = ((x + 1) * arena.tile - ox) / dx
    elif dx < 0:
        t_max_x = (x * arena.tile - ox) / dx
    else:
        t_max_x = INF
    if dy > 0:
        t_max_y = ((y + 1) * arena.tile - oy) / dy
    elif dy < 0:
        t_max_y = (y * arena.tile - oy) / dy
    else:
        t_max_y = INF

    t = 0.0
    guard = 0
    limit = arena.width + arena.height + 4
    nx, ny = 0.0, 0.0
    while t <= max_t and guard < limit * 4:
        guard += 1
        if t_max_x < t_max_y:
            t = t_max_x
            t_max_x += t_delta_x
            x += step_x
            nx, ny = -step_x, 0.0
        else:
            t = t_max_y
            t_max_y += t_delta_y
            y += step_y
            nx, ny = 0.0, -step_y
        if t > max_t:
            break
        if predicate((x + 0.5) * arena.tile, (y + 0.5) * arena.tile):
            return t, nx, ny
    return None


def _ray_grid_opaque(arena: Arena, ox: float, oy: float, dx: float, dy: float,
                     max_t: float) -> tuple[float, float, float] | None:
    """DDA-обход сетки по маске непрозрачных тайлов.

    Полная копия ``ray_grid_detailed`` с ``predicate = arena.opaque_px``:
    порядок шагов, формулы и границы те же, только вместо вызова предиката
    на каждый тайл — чтение байтовой маски. За границей карты стена, как и
    раньше в ``tile_at`` (``"#"``).
    """
    if dx != dx or dy != dy:
        # Вырожденный луч (nan в направлении) — стены на нём нет. Раньше это
        # ловил счётчик шагов, который сравнивался на каждой итерации цикла;
        # теперь проверка одна и до цикла. На обычных лучах счётчик не
        # срабатывал никогда: рамка из стен останавливает обход раньше.
        return None
    tile = arena.tile
    ftile = arena.tile_f
    x = int(ox // tile)
    y = int(oy // tile)

    if dx > 0:
        step_x = 1
        t_delta_x = abs(ftile / dx)
        t_max_x = ((x + 1) * tile - ox) / dx
    elif dx < 0:
        step_x = -1
        t_delta_x = abs(ftile / dx)
        t_max_x = (x * tile - ox) / dx
    else:
        step_x = -1
        t_delta_x = INF
        t_max_x = INF
    if dy > 0:
        step_y = 1
        t_delta_y = abs(ftile / dy)
        t_max_y = ((y + 1) * tile - oy) / dy
    elif dy < 0:
        step_y = -1
        t_delta_y = abs(ftile / dy)
        t_max_y = (y * tile - oy) / dy
    else:
        step_y = -1
        t_delta_y = INF
        t_max_y = INF

    t = 0.0
    if not max_t >= 0.0:
        # Отрицательный или nan предел: раньше такой луч не делал ни шага.
        return None
    # Дальше идём прямо по маске с рамкой из стен (2 тайла): проверок
    # границ не нужно — их роль играет сама рамка. Первый шаг за карту
    # всегда попадает в неё, потому что индекс тестируется на каждом шаге.
    pad = arena.opaque_pad
    px = x + 2
    py = y + 2
    # Строка маски берётся по ``py``: пока луч идёт вдоль неё, повторное
    # индексирование не нужно, а обновляется только на шаге по Y.
    row = pad[py]
    stepped_x = True
    while True:
        if t_max_x < t_max_y:
            t = t_max_x
            t_max_x += t_delta_x
            px += step_x
            stepped_x = True
        else:
            t = t_max_y
            t_max_y += t_delta_y
            py += step_y
            row = pad[py]
            stepped_x = False
        # Тест предела стоит только здесь: ``t`` на каждой итерации растёт,
        # поэтому отдельная проверка ``t <= max_t`` в заголовке цикла ничего
        # не добавляла, а исполнялась каждый шаг.
        if t > max_t:
            break
        if row[px]:
            if stepped_x:
                return t, -step_x, 0.0
            return t, 0.0, -step_y
    return None


def ray_grid(arena: Arena, ox: float, oy: float, dx: float, dy: float,
             max_t: float, predicate=None) -> float | None:
    """DDA-обход сетки: расстояние до первого тайла, удовлетворяющего предикату.

    ``dx, dy`` — единичный вектор. Возвращает ``t`` либо ``None``.
    """
    if predicate is None:
        res = _ray_grid_opaque(arena, ox, oy, dx, dy, max_t)
    else:
        res = ray_grid_detailed(arena, ox, oy, dx, dy, max_t, predicate)
    return res[0] if res else None


def line_clear(arena: Arena, ax: float, ay: float, bx: float, by: float) -> bool:
    """Чиста ли линия взгляда между двумя точками."""
    d = math.hypot(bx - ax, by - ay)
    if d < 1e-6:
        return not arena.opaque_px(ax, ay)
    if _ray_grid_opaque(arena, ax, ay, (bx - ax) / d, (by - ay) / d, d - 0.5) is not None:
        return False
    return True


def hull_los(arena: Arena, ox: float, oy: float, tx: float, ty: float,
             half_len: float, half_wid: float, angle: float) -> bool:
    """Виден ли корпус целиком хотя бы частично.

    Проверяются центр и оба борта — так танк «выглядывает» из-за угла
    естественно, а не по магическому правилу.
    """
    if line_clear(arena, ox, oy, tx, ty):
        return True
    ca = math.cos(angle)
    sa = math.sin(angle)
    for sgn in (1.0, -1.0):
        px = tx + sgn * half_len * ca
        py = ty + sgn * half_len * sa
        if line_clear(arena, ox, oy, px, py):
            return True
    for sgn in (1.0, -1.0):
        px = tx + sgn * half_wid * -sa
        py = ty + sgn * half_wid * ca
        if line_clear(arena, ox, oy, px, py):
            return True
    return False


def cone_check(bearing: float, facing: float, half_cone_deg: float) -> bool:
    """Вписывается ли направление на цель в сектор обзора.

    ``half_cone_deg`` — половина угла: 180 означает круговой обзор, тогда
    результат не зависит от направления корпуса.
    """
    if half_cone_deg >= 180.0:
        return True
    return abs(math.degrees(ang_diff(bearing, facing))) <= half_cone_deg


def can_see_tuple(arena: Arena, ox: float, oy: float, facing: float,
                  tx: float, ty: float, t_half, t_angle: float,
                  bal: Balance) -> tuple:
    """Ядро ``can_see`` без словаря: тот же расчёт, те же числа.

    Порядок полей — как в наблюдении скрипта: ``(visible, in_cone, in_range,
    los_clear, bumper, bearing, distance)``. На каждый тик видимость считается
    дважды, и словарь с шестью ключами тут дороже самих вычислений, поэтому
    горячий путь (``World.update_vision``) обходится кортежем, а ``can_see``
    остаётся обёрткой для UI и телеметрии.
    """
    d = math.hypot(tx - ox, ty - oy)
    bearing = math.atan2(ty - oy, tx - ox)
    in_range = d <= bal.view_range
    half_cone = bal.view_cone
    # Круговой обзор (180°) не зависит от курса — самый частый случай,
    # поэтому проверка сектора разворачивается без вызова.
    in_cone = True if half_cone >= 180.0 else abs(math.degrees(ang_diff(bearing, facing))) <= half_cone
    bumper = d <= bal.bumper_range
    if not in_range or not (in_cone or bumper):
        return (False, in_cone, in_range, False, bumper, bearing, d)
    los = hull_los(arena, ox, oy, tx, ty, t_half[0], t_half[1], t_angle)
    return (los, in_cone, in_range, los, bumper, bearing, d)


def can_see(arena: Arena, ox: float, oy: float, facing: float,
            tx: float, ty: float, t_half, t_angle: float,
            bal: Balance) -> dict:
    """Полная проверка видимости. Возвращает детали (для UI и телеметрии)."""
    visible, in_cone, in_range, los, bumper, bearing, d = can_see_tuple(
        arena, ox, oy, facing, tx, ty, t_half, t_angle, bal)
    return {"visible": visible, "distance": d, "bearing": bearing,
            "in_range": in_range, "in_cone": in_cone, "los_clear": los,
            "bumper": bumper}


def vision_polygon(arena: Arena, ox: float, oy: float, facing: float,
                   bal: Balance, rays: int = 160) -> list[tuple[float, float]]:
    """Многоугольник зоны обзора — используется для тумана в UI.

    Браузер считает его сам, функция оставлена для тестов и отладки.
    При ``view_cone == 180`` обход идёт по полному кругу.
    """
    pts: list[tuple[float, float]] = []
    half = math.radians(bal.view_cone)
    for i in range(rays + 1):
        a = facing - half + 2 * half * (i / rays)
        dx, dy = math.cos(a), math.sin(a)
        t = ray_grid(arena, ox, oy, dx, dy, bal.view_range) or bal.view_range
        pts.append((ox + dx * t, oy + dy * t))
    pts.append((ox, oy))
    return pts


def visible_tiles(arena: Arena, ox: float, oy: float, facing: float,
                  bal: Balance) -> set[tuple[int, int]]:
    """Множество видимых тайлов (для отладочной раскраски карты)."""
    poly = vision_polygon(arena, ox, oy, facing, bal)
    out: set[tuple[int, int]] = set()
    for py in range(arena.height):
        for px in range(arena.width):
            cx = (px + 0.5) * arena.tile
            cy = (py + 0.5) * arena.tile
            if _point_in_poly(cx, cy, poly):
                out.add((px, py))
    return out


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = xi + (y - yi) * (xj - xi) / (yj - yi)
            if xc > x:
                inside = not inside
        j = i
    return inside


def corners(c, half, angle) -> list[tuple[float, float]]:
    """Удобная обёртка (используется в тестах)."""
    return obb_corners(c[0], c[1], half, angle)