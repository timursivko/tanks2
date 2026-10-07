"""Арена: сетка тайлов, проходимость, поле «зазора», спавны и проверка симметрии."""

from __future__ import annotations

import json
import math
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

from config import (
    BLOCK_TILES,
    MAPS_DIR,
    OPAQUE_TILES,
    TILE_MUD,
)
from engine.geometry import obb_axis_radii, resolve_obb_aabb


class MapError(Exception):
    """Карта не прошла валидацию."""


@dataclass
class Spawn:
    x: float  # мировые координаты (пиксели)
    y: float
    angle: float  # радианы


@dataclass
class Arena:
    """Статичная геометрия боя.

    ``grid[y][x]`` — символ тайла. Все запросы принимают мировые координаты
    в пикселях.
    """

    name: str
    tile: int
    grid: list[list[str]]
    spawns: list[Spawn]
    clearance: list[list[float]]
    mud_factor: float = 0.55

    def __post_init__(self) -> None:
        """Считает размеры и быстрые таблицы тайлов один раз при сборке карты.

        Раньше ``width``/``height`` были свойствами (вызов функции на каждое
        обращение), а проходимость проверялась сравнением символов
        (``tile in frozenset``). Видимость и выталкивание из стен дёргают это
        десятки раз за тик, и доля выходила заметной. Здесь те же значения и
        те же ответы, только подготовленные заранее: длина/размер в пикселях
        и байтовые маски «непрозрачно / непроходимо / грязь».
        """
        grid = self.grid
        width = len(grid[0])
        height = len(grid)
        self.width = width
        self.height = height
        self.pixel_width = width * self.tile
        self.pixel_height = height * self.tile
        #: Тот же размер тайла float-ом: в DDA-обходе (``visibility``) он нужен
        #: каждый луч, а ``float(int)`` — это вызов на луч.
        self.tile_f = float(self.tile)
        opaque = []
        block = []
        mud = []
        for row in grid:
            opaque.append(bytearray(1 if ch in OPAQUE_TILES else 0 for ch in row))
            block.append(bytearray(1 if ch in BLOCK_TILES else 0 for ch in row))
            mud.append(bytearray(1 if ch == TILE_MUD else 0 for ch in row))
        #: Маски тайлов: 1 — свойство есть. Индекс ``[ty][tx]``, за границей
        #: карты подразумевается стена (см. ``opaque_px``/``blocked_px``).
        self.opaque = opaque
        self.block = block
        self.mud = mud
        #: Есть ли на карте грязь вообще. Если нет (а это большинство карт),
        #: множитель скорости гарантированно равен 1.0, и ``Tank.step`` может
        #: не считать тайл под танком каждый тик.
        self.has_mud = any(any(row) for row in mud)
        #: Маска непрозрачности с рамкой из стен шириной 2 тайла. Луч по
        #: сетке (DDA) ходит по ней без проверок границ: тайл ``(tx, ty)``
        #: лежит по индексу ``[ty + 2][tx + 2]``, а первый же шаг за карту
        #: попадает в рамку — то же «за картой стена», что и раньше.
        pad = []
        w4 = width + 4
        pad.append(bytearray(b"\x01" * w4))
        pad.append(bytearray(b"\x01" * w4))
        for row in opaque:
            pad.append(bytearray(b"\x01\x01") + row + bytearray(b"\x01\x01"))
        pad.append(bytearray(b"\x01" * w4))
        pad.append(bytearray(b"\x01" * w4))
        self.opaque_pad = pad
        #: Номера непроходимых тайлов по строкам (по возрастанию): перебор
        #: в ``push_out`` идёт по ним, а не по всей рамке AABB.
        self.block_cols = [[tx for tx, flag in enumerate(row) if flag] for row in block]
        #: Кандидаты на выталкивание: для каждого тайла — прямоугольники
        #: непроходимых тайлов из окрестности 3×3, по строкам и слева
        #: направо. Корпус танка (полудиагональ ~20 px при тайле 32) дальше
        #: соседних тайлов не достаёт, поэтому ``push_out`` перебирает этот
        #: готовый список: он же заменяет и проверку «стен рядом нет» —
        #: пустой список означает, что пересечения быть не может.
        near_blocks = []
        for ty in range(height):
            row_near = []
            y_from = ty - 1 if ty > 0 else 0
            y_to = ty + 2 if ty < height - 1 else height
            for tx in range(width):
                x_from = tx - 1 if tx > 0 else 0
                x_to = tx + 2 if tx < width - 1 else width
                boxes = []
                for yy in range(y_from, y_to):
                    cols = block[yy]
                    ay0 = yy * self.tile
                    ay1 = ay0 + self.tile
                    for xx in range(x_from, x_to):
                        if cols[xx]:
                            ax0 = xx * self.tile
                            boxes.append((ax0, ay0, ax0 + self.tile, ay1))
                row_near.append(tuple(boxes))
            near_blocks.append(row_near)
        self.near_blocks = near_blocks

    def to_dict(self) -> dict:
        """Компактное описание для браузера."""
        return {
            "name": self.name,
            "tile": self.tile,
            "rows": ["".join(row) for row in self.grid],
            "mud_factor": self.mud_factor,
            "spawns": [{"x": s.x, "y": s.y, "angle": s.angle} for s in self.spawns],
        }

    # --- запросы сетки ------------------------------------------------------

    def tile_at(self, tx: int, ty: int) -> str:
        if tx < 0 or ty < 0 or ty >= self.height or tx >= self.width:
            return "#"
        return self.grid[ty][tx]

    def tile_at_px(self, px: float, py: float) -> str:
        return self.tile_at(int(px // self.tile), int(py // self.tile))

    def opaque_px(self, px: float, py: float) -> bool:
        tile = self.tile
        tx = int(px // tile)
        ty = int(py // tile)
        if tx < 0 or ty < 0 or ty >= self.height or tx >= self.width:
            return True                     # за картой — глухая стена
        return self.opaque[ty][tx] != 0

    def blocked_px(self, px: float, py: float) -> bool:
        tile = self.tile
        tx = int(px // tile)
        ty = int(py // tile)
        if tx < 0 or ty < 0 or ty >= self.height or tx >= self.width:
            return True                     # за картой — стена
        return self.block[ty][tx] != 0

    def passable_px(self, px: float, py: float) -> bool:
        return not self.blocked_px(px, py)

    def speed_factor(self, px: float, py: float) -> float:
        """Множитель скорости на тайле (грязь тормозит)."""
        tile = self.tile
        tx = int(px // tile)
        ty = int(py // tile)
        # Обычный случай — точка внутри карты: два сравнения вместо четырёх.
        if 0 <= tx < self.width and 0 <= ty < self.height:
            return self.mud_factor if self.mud[ty][tx] else 1.0
        return 1.0

    def clearance_px(self, px: float, py: float) -> float:
        """Расстояние до ближайшего непроходимого тайла (в пикселях)."""
        tile = self.tile
        tx = int(px // tile)
        ty = int(py // tile)
        if tx < 0:
            tx = 0
        elif tx >= self.width:
            tx = self.width - 1
        if ty < 0:
            ty = 0
        elif ty >= self.height:
            ty = self.height - 1
        return self.clearance[ty][tx] * tile

    def nearest_wall_dir(self, px: float, py: float) -> float:
        """Направление от точки к ближайшей стене в градусах (для ИИ)."""
        best = None
        best_d = float("inf")
        r = int(self.clearance_px(px, py) // self.tile) + 1
        cx = int(px // self.tile)
        cy = int(py // self.tile)
        for oy in range(-r, r + 1):
            for ox in range(-r, r + 1):
                tx, ty = cx + ox, cy + oy
                if tx < 0 or ty < 0 or ty >= self.height or tx >= self.width:
                    continue
                if self.grid[ty][tx] not in BLOCK_TILES:
                    continue
                wxx = (tx + 0.5) * self.tile
                wyy = (ty + 0.5) * self.tile
                d = (wxx - px) ** 2 + (wyy - py) ** 2
                if d < best_d:
                    best_d = d
                    best = math.atan2(wyy - py, wxx - px)
        return math.degrees(best or 0.0)

    # --- проверки -----------------------------------------------------------

    def is_symmetric(self) -> bool:
        """Симметрия на 180° — баланс: у обоих танков одинаковая территория."""
        w, h = self.width, self.height
        for y in range(h):
            for x in range(w):
                if self.grid[y][x] != self.grid[h - 1 - y][w - 1 - x]:
                    return False
        return True

    def symmetry_report(self) -> dict:
        w, h = self.width, self.height
        diffs = 0
        for y in range(h):
            for x in range(w):
                if self.grid[y][x] != self.grid[h - 1 - y][w - 1 - x]:
                    diffs += 1
        return {
            "square": w == h,
            "symmetric": diffs == 0,
            "diffs": diffs,
            "size": f"{w}x{h}",
        }


# --- построение -------------------------------------------------------------


def _build_clearance(grid: list[list[str]], tile: int) -> list[list[float]]:
    """BFS от всех непроходимых тайлов: расстояние в тайлах до ближайшей стены."""
    h = len(grid)
    w = len(grid[0])
    dist = [[-1.0] * w for _ in range(h)]
    queue: list[tuple[int, int]] = []
    for y in range(h):
        for x in range(w):
            if grid[y][x] in BLOCK_TILES or x == 0 or y == 0 or x == w - 1 or y == h - 1:
                dist[y][x] = 0.0
                queue.append((x, y))
    head = 0
    while head < len(queue):
        x, y = queue[head]
        head += 1
        d = dist[y][x]
        for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + ox, y + oy
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            if dist[ny][nx] >= 0:
                continue
            dist[ny][nx] = d + 1.0
            queue.append((nx, ny))
    return dist


def _extract_spawns(grid: list[list[str]], tile: int) -> list[Spawn]:
    found: dict[str, Spawn] = {}
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            if ch in ("A", "B"):
                # Спавн смотрит в сторону центра карты.
                cx = (len(row) - 1) * tile / 2.0
                cy = (len(grid) - 1) * tile / 2.0
                ang = math.atan2(cy - (y + 0.5) * tile, cx - (x + 0.5) * tile)
                found[ch] = Spawn((x + 0.5) * tile, (y + 0.5) * tile, ang)
    return [found["A"], found["B"]] if "A" in found and "B" in found else []


def arena_from_rows(name: str, rows: list[str], tile: int = 32,
                    mud_factor: float = 0.55) -> Arena:
    """Строит арену из символьных строк с проверками."""
    rows = [r for r in rows if r.strip()]
    if not rows:
        raise MapError("Карта пустая")
    w = len(rows[0])
    if any(len(r) != w for r in rows):
        raise MapError("Строки карты разной длины")
    spawns = _extract_spawns([list(r) for r in rows], tile)
    # Спавны не считаются препятствиями.
    grid = [[("." if ch in ("A", "B") else ch) for ch in r] for r in rows]
    clearance = _build_clearance(grid, tile)
    arena = Arena(name=name, tile=tile, grid=grid, spawns=spawns, clearance=clearance,
                  mud_factor=mud_factor)
    if len(spawns) != 2:
        raise MapError("Нужно ровно два спавна: A и B")
    for i, sp in enumerate(spawns):
        if not arena.passable_px(sp.x, sp.y):
            raise MapError(f"Спавн {'AB'[i]} внутри стены")
    return arena


def push_out(arena: "Arena", cx: float, cy: float, half, angle: float,
             iterations: int = 4, ca: float = None,
             sa: float = None) -> tuple[float, float, bool]:
    """Выталкивает повёрнутый прямоугольник из стен арены.

    Возвращает ``(x, y, столкновение)``. Короткий шаг (менее 2 пикселей за тик)
    позволяет обойтись без swept-коллизий.

    ``ca``/``sa`` — заранее посчитанные ``cos(angle)``/``sin(angle)``: вызывающий
    из ``Tank.step`` уже знает курс корпуса и избавляет функцию от двух
    тригонометрических вызовов на каждый тик.
    """
    if ca is None:
        ca = math.cos(angle)
    if sa is None:
        sa = math.sin(angle)
    tile = arena.tile
    hx, hy = half
    aca = abs(ca)
    asa = abs(sa)
    # Полуразмеры AABB: по ним видно, влезает ли корпус в окрестность 3×3
    # тайлов (обычный случай: полудиагональ ~20 px против тайла 32).
    ex = hx * aca + hy * asa
    ey = hx * asa + hy * aca
    width = arena.width
    height = arena.height
    if ex < tile and ey < tile:
        ctx = int(cx // tile)
        cty = int(cy // tile)
        inside = 0 <= ctx < width and 0 <= cty < height
        # Центр за картой — только если AABB ещё её цепляет: тогда индексы
        # тайла прижимаются к границе ровно так же, как прижималась рамка
        # AABB в медленном пути. Если AABB целиком вне карты, пересечения
        # нет — это разбирает медленный путь.
        if inside or (cx + ex > 0.0 and cx - ex < arena.pixel_width
                      and cy + ey > 0.0 and cy - ey < arena.pixel_height):
            return _push_out_near(arena, cx, cy, half, angle, iterations,
                                  ca, sa, ctx, cty, ex)
    return _push_out_region(arena, cx, cy, half, angle, iterations, ca, sa)


def _push_out_near(arena: "Arena", cx: float, cy: float, half, angle: float,
                   iterations: int, ca: float, sa: float, ctx: int, cty: int,
                   ex: float) -> tuple[float, float, bool]:
    """Перебор кандидатов из окрестности 3×3 (``Arena.near_blocks``).

    Порядок тот же, что у перебора рамки AABB — по строкам, внутри строки
    слева направо, — поэтому при равной глубине выбирается та же плитка.
    Плитки, не пересекающиеся с корпусом, ``resolve_obb_aabb`` отбрасывает
    сам, так что лишние кандидаты ничего не меняют.
    """
    tile = arena.tile
    width = arena.width
    height = arena.height
    near = arena.near_blocks
    hit = False
    pre = None
    if not (0 <= ctx < width):
        ctx = 0 if ctx < 0 else width - 1
    if not (0 <= cty < height):
        cty = 0 if cty < 0 else height - 1
    for _ in range(iterations):
        boxes = near[cty][ctx]
        if not boxes:
            return cx, cy, hit
        if pre is None:
            pre = obb_axis_radii(half, ca, sa)
        # Плитки, чей прямоугольник не пересёкся с AABB корпуса, отбрасываются
        # прямо здесь: у ``resolve_obb_aabb`` это первые две оси SAT, и по ним
        # пересечение уже отсутствует — ответ был бы ``None`` при любом курсе.
        # Так на каждую плитку вместо вызова функции — четыре сравнения.
        r1 = pre[0]
        r2 = pre[1]
        aabb_x0 = cx - r1
        aabb_x1 = cx + r1
        aabb_y0 = cy - r2
        aabb_y1 = cy + r2
        best = None
        for bx0, by0, bx1, by1 in boxes:
            if bx0 >= aabb_x1 or bx1 <= aabb_x0 or by0 >= aabb_y1 or by1 <= aabb_y0:
                continue
            res = resolve_obb_aabb(cx, cy, half, angle, bx0, by0, bx1, by1,
                                   pre, ca, sa)
            if res and (best is None or res[0] > best[0]):
                best = res
        if best is None:
            return cx, cy, hit
        depth, nx, ny = best
        cx += nx * depth
        cy += ny * depth
        hit = True
        ctx = int(cx // tile)
        cty = int(cy // tile)
        if not (0 <= ctx < width):
            ctx = 0 if ctx < 0 else width - 1
        if not (0 <= cty < height):
            cty = 0 if cty < 0 else height - 1
    return cx, cy, hit


def _push_out_region(arena: "Arena", cx: float, cy: float, half, angle: float,
                     iterations: int, ca: float, sa: float) -> tuple[float, float, bool]:
    """Медленный путь: перебор всех непроходимых тайлов в рамке AABB.

    Нужен, когда корпус больше тайла (в окрестность 3×3 он не влезает) или
    когда AABB целиком вне карты — тогда перебирать нечего, и функция
    возвращает точку без изменений.
    """
    tile = arena.tile
    hx, hy = half
    aca = abs(ca)
    asa = abs(sa)
    ex = hx * aca + hy * asa
    ey = hx * asa + hy * aca
    pre = None
    width = arena.width
    height = arena.height
    cols_by_row = arena.block_cols
    hit = False
    for _ in range(iterations):
        tx0 = int(math.floor((cx - ex) / tile))
        if tx0 < 0:
            tx0 = 0
        tx1 = int(math.floor((cx + ex) / tile))
        if tx1 > width - 1:
            tx1 = width - 1
        ty0 = int(math.floor((cy - ey) / tile))
        if ty0 < 0:
            ty0 = 0
        ty1 = int(math.floor((cy + ey) / tile))
        if ty1 > height - 1:
            ty1 = height - 1
        best = None
        # Порядок перебора прежний — строка за строкой, внутри строки слева
        # направо, — значит и выбор плитки при равной глубине тот же.
        for ty in range(ty0, ty1 + 1):
            cols = cols_by_row[ty]
            if not cols:
                continue
            i = bisect_left(cols, tx0)
            n = len(cols)
            if i >= n:
                continue
            ay0 = ty * tile
            ay1 = ay0 + tile
            while i < n:
                tx = cols[i]
                if tx > tx1:
                    break
                i += 1
                if pre is None:
                    pre = obb_axis_radii(half, ca, sa)
                res = resolve_obb_aabb(cx, cy, half, angle,
                                       tx * tile, ay0, tx * tile + tile, ay1,
                                       pre, ca, sa)
                if res and (best is None or res[0] > best[0]):
                    best = res
        if best is None:
            return cx, cy, hit
        depth, nx, ny = best
        cx += nx * depth
        cy += ny * depth
        hit = True
    return cx, cy, hit


def load_map(name: str, maps_dir: Path | None = None) -> Arena:
    """Загружает карту по имени из JSON-файла."""
    maps_dir = maps_dir or MAPS_DIR
    path = maps_dir / f"{name}.json"
    if not path.exists():
        raise MapError(f"Карта «{name}» не найдена: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MapError(f"Карта «{name}» повреждена: {exc}") from exc
    return arena_from_rows(
        name=data.get("name", name),
        rows=data["rows"],
        tile=int(data.get("tile", 32)),
        mud_factor=float(data.get("mud_factor", 0.55)),
    )


def list_maps(maps_dir: Path | None = None) -> list[dict]:
    """Список доступных карт с метаданными для UI."""
    maps_dir = maps_dir or MAPS_DIR
    out = []
    for path in sorted(maps_dir.glob("*.json")):
        try:
            arena = load_map(path.stem, maps_dir)
        except Exception as exc:  # noqa: BLE001 — битая карта не должна ронять UI
            out.append({"id": path.stem, "name": path.stem, "error": str(exc)})
            continue
        rep = arena.symmetry_report()
        out.append({
            "id": path.stem,
            "name": arena.name,
            "size": rep["size"],
            "symmetric": rep["symmetric"],
            "square": rep["square"],
            "diffs": rep["diffs"],
            "width": arena.width,
            "height": arena.height,
            "pixel_width": arena.pixel_width,
            "pixel_height": arena.pixel_height,
        })
    return out