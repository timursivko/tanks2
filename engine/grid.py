"""Арена: сетка тайлов, проходимость, поле «зазора», спавны и проверка симметрии."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from config import (
    BLOCK_TILES,
    MAPS_DIR,
    OPAQUE_TILES,
    TILE_MUD,
)


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

    # --- размеры ------------------------------------------------------------

    @property
    def width(self) -> int:
        return len(self.grid[0])

    @property
    def height(self) -> int:
        return len(self.grid)

    @property
    def pixel_width(self) -> int:
        return self.width * self.tile

    @property
    def pixel_height(self) -> int:
        return self.height * self.tile

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
        return self.tile_at_px(px, py) in OPAQUE_TILES

    def blocked_px(self, px: float, py: float) -> bool:
        return self.tile_at_px(px, py) in BLOCK_TILES

    def passable_px(self, px: float, py: float) -> bool:
        return not self.blocked_px(px, py)

    def speed_factor(self, px: float, py: float) -> float:
        """Множитель скорости на тайле (грязь тормозит)."""
        return self.mud_factor if self.tile_at_px(px, py) == TILE_MUD else 1.0

    def clearance_px(self, px: float, py: float) -> float:
        """Расстояние до ближайшего непроходимого тайла (в пикселях)."""
        tx = int(px // self.tile)
        ty = int(py // self.tile)
        tx = min(max(tx, 0), self.width - 1)
        ty = min(max(ty, 0), self.height - 1)
        return self.clearance[ty][tx] * self.tile

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
             iterations: int = 4) -> tuple[float, float, bool]:
    """Выталкивает повёрнутый прямоугольник из стен арены.

    Возвращает ``(x, y, столкновение)``. Короткий шаг (менее 2 пикселей за тик)
    позволяет обойтись без swept-коллизий.
    """
    from engine.geometry import obb_aabb, resolve_obb_aabb

    tile = arena.tile
    hit = False
    for _ in range(iterations):
        minx, miny, maxx, maxy = obb_aabb(cx, cy, half, angle)
        tx0 = max(0, int(math.floor(minx / tile)))
        tx1 = min(arena.width - 1, int(math.floor(maxx / tile)))
        ty0 = max(0, int(math.floor(miny / tile)))
        ty1 = min(arena.height - 1, int(math.floor(maxy / tile)))
        best = None
        for ty in range(ty0, ty1 + 1):
            row = arena.grid[ty]
            for tx in range(tx0, tx1 + 1):
                if row[tx] not in BLOCK_TILES:
                    continue
                res = resolve_obb_aabb(cx, cy, half, angle,
                                       tx * tile, ty * tile, (tx + 1) * tile, (ty + 1) * tile)
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