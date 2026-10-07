"""Генератор карт.

Карта задаётся левой половиной (24 символа на строку), правая получается
поворотом на 180°, поэтому симметрия гарантирована: у обоих танков
идентичная территория и равные позиции. Один спавн ``A`` на левой половине
автоматически даёт второй ``B`` на правой.

Условные обозначения: ``.`` пол, ``#`` стена, ``o`` колонна, ``:`` низкий
укрыт (мешает проезду, но не обзору), ``,`` грязь.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import MAPS_DIR  # noqa: E402
from engine.grid import push_out  # noqa: E402

HALF = 24  # ширина левой половины в символах
ROWS = 28  # высота
#: Символы, по которым можно ездить (грязь проходима, укрытия — нет)
WALKABLE = ".AB,"


def build_rows(half: list[str], spawn_xy: tuple[int, int]) -> list[str]:
    """Склеивает половину с её поворотом на 180°, ставит спавны A и B.

    По периметру добавляется сплошная стена: иначе танк упирается в пустоту
    за границей карты и «залипает» там наравне с настоящей стеной.
    """
    h = len(half)
    assert h == ROWS, f"ожидалось {ROWS} строк, получено {h}"
    for r in half:
        assert len(r) == HALF, f"строка должна быть длиной {HALF}: {r!r}"
    sx, sy = spawn_xy
    assert 1 <= sx < HALF - 1 and 1 <= sy < ROWS - 1, "спавн слишком близко к краю"
    full = []
    for y, left in enumerate(half):
        mirrored = half[h - 1 - y][::-1]
        cells = list(left + mirrored)
        if y == sy:
            cells[sx] = "A"
        if y == h - 1 - sy:
            cells[HALF * 2 - 1 - sx] = "B"
        if y in (0, h - 1):
            cells = ["#"] * (HALF * 2)
            if y == sy:
                cells[sx] = "A"
            if y == h - 1 - sy:
                cells[HALF * 2 - 1 - sx] = "B"
        else:
            cells[0] = cells[HALF * 2 - 1] = "#"
        full.append("".join(cells))
    return full


def reachable(rows: list[str], a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Проходим ли путь от спавна A к спавну B по свободным тайлам."""
    h, w = len(rows), len(rows[0])
    start, goal = a, b
    seen = {start}
    stack = [start]
    while stack:
        tx, ty = stack.pop()
        if (tx, ty) == goal:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = tx + dx, ty + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen:
                if rows[ny][nx] in WALKABLE:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
    return False


def check_playable(name: str, rows: list[str], sx: int, sy: int) -> None:
    """Проверки, без которых карта превращается в ловушку.

    Проходимы только ``.``, ``,`` и спавны: ``:`` и ``o`` — укрытия и колонны,
    в которые нельзя въехать (см. ``config.BLOCK_TILES``).
    """
    w = len(rows[0])
    ax, ay = sx, sy
    bx, by = w - 1 - sx, ROWS - 1 - sy
    if not reachable(rows, (ax, ay), (bx, by)):
        raise SystemExit(f"карта {name}: спавны не связаны проходимым путём")
    for tx, ty, who in ((ax, ay, "A"), (bx, by, "B")):
        free = sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                   if rows[ty + dy][tx + dx] in WALKABLE)
        if free < 3:
            raise SystemExit(f"карта {name}: спавн {who} зажат стенами ({free} выхода)")
    free = sum(1 for row in rows for ch in row if ch in WALKABLE)
    share = free / (len(rows) * w)
    if share < 0.75:
        raise SystemExit(f"карта {name}: слишком тесно, свободно только {share:.0%}")
    problem = spawn_check(name, rows)
    if problem:
        raise SystemExit(problem)


def spawn_check(name: str, rows: list[str]) -> str | None:
    """Спавн должен быть не только свободен, но и пригоден для езды.

    Прошлые проверки смотрели на тайлы вокруг спавна и не замечали, что нос
    танка (34x22 px) упирается в укрытие в полутора тайлах: газ давал
    3-4 px за секунду, игрок думал, что управление сломано. Теперь проверка
    настоящая — моделируем World и жмём газ.

    Возвращает текст проблемы или ``None``, если спавн годен.
    """
    import math

    from config import BALANCE
    from engine.action import Action
    from engine.grid import arena_from_rows
    from engine.world import World

    arena = arena_from_rows(name, rows)
    for i, sp in enumerate(arena.spawns[:2]):
        who = "AB"[i]
        _, _, hit = push_out(arena, sp.x, sp.y, BALANCE.half, sp.angle)
        if hit:
            return f"карта {name}: спавн {who} ({sp.x:.0f},{sp.y:.0f}) внутри стены"
        world = World.create(arena, BALANCE, seed=1)
        tank = world.tanks[i]
        x0, y0 = tank.x, tank.y
        for _ in range(60):                       # одна секунда газа
            world.step([Action(drive=1.0), Action(drive=1.0)])
        moved = math.hypot(tank.x - x0, tank.y - y0)
        if moved < 40:
            return (f"карта {name}: спавн {who} зажат — за секунду газа только "
                    f"{moved:.0f} px (нужно от 40)")
    return None


def spawn_clearance(rows: list[str], sx: int, sy: int) -> int:
    """Сколько тайлов от спавна до ближайшей преграды (кап 6)."""
    h, w = len(rows), len(rows[0])
    for d in range(0, 7):
        for oy in range(-d, d + 1):
            for ox in range(-d, d + 1):
                if max(abs(ox), abs(oy)) != d:
                    continue
                tx, ty = sx + ox, sy + oy
                if tx < 0 or ty < 0 or ty >= h or tx >= w:
                    continue
                if rows[ty][tx] not in WALKABLE:
                    return d
    return 6


def best_spawn(half: list[str]) -> tuple[int, int]:
    """Ищем самый просторный спавн в левой половине.

    Вторая половина получается поворотом на 180°, поэтому достаточно смотреть
    только налево: у спавна B ровно такая же обстановка, как у A.
    """
    best, best_score = (1, 1), -1
    for sy in range(1, ROWS - 1):
        for sx in range(1, HALF - 1):
            rows = build_rows(half, (sx, sy))
            score = spawn_clearance(rows, sx, sy)
            if score > best_score:
                best, best_score = (sx, sy), score
    return best


def save_map(name: str, half: list[str], spawn: tuple[int, int], desc: str,
             title: str) -> dict:
    rows = build_rows(half, spawn)
    data = {
        "name": title,
        "tile": 32,
        "description": desc,
        "rows": rows,
    }
    path = MAPS_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data



# --- рисование половин -------------------------------------------------------

HALF_W = 24


def blank_half() -> list[list[str]]:
    return [["."] * HALF_W for _ in range(ROWS)]


def rect(rows: list[list[str]], x0: int, y0: int, x1: int, y1: int,
         ch: str = "#", fill: bool = True) -> None:
    """Прямоугольник в полукарте; `fill=False` — только рамка."""
    for y in range(max(0, y0), min(ROWS - 1, y1) + 1):
        for x in range(max(0, x0), min(HALF_W - 1, x1) + 1):
            edge = x in (x0, x1) or y in (y0, y1)
            if fill or edge:
                rows[y][x] = ch


def dot(rows: list[list[str]], x: int, y: int, ch: str) -> None:
    if 0 <= x < HALF_W and 0 <= y < ROWS:
        rows[y][x] = ch


def as_rows(rows: list[list[str]]) -> list[str]:
    out = ["".join(r) for r in rows]
    assert len(out) == ROWS and all(len(r) == HALF_W for r in out)
    return out


def carve(rows: list[list[str]], x0: int, y0: int, x1: int, y1: int,
          ch: str = ".") -> None:
    """Пробивает проход: затирает рамку стены внутри прямоугольника."""
    for y in range(max(0, y0), min(ROWS - 1, y1) + 1):
        for x in range(max(0, x0), min(HALF_W - 1, x1) + 1):
            rows[y][x] = ch

# --- карты ------------------------------------------------------------------


def arena_half() -> list[str]:
    """Открытая арена: пара колонн, бруствер в центре, низкие укрытия."""
    g = blank_half()
    rect(g, 8, 22, 11, 23)                      # центральный бруствер
    rect(g, 3, 4, 4, 5, "o")
    rect(g, 15, 4, 16, 5, "o")
    rect(g, 18, 8, 21, 9, "o")
    dot(g, 20, 10, ":")
    dot(g, 5, 20, ":")
    dot(g, 16, 20, ":")
    return as_rows(g)


def corridor_half() -> list[str]:
    """Длинные стены с широкими проходами: коридоры для манёвра."""
    g = blank_half()
    rect(g, 2, 6, 10, 7)
    rect(g, 11, 12, 18, 13)
    rect(g, 14, 20, 16, 21)
    return as_rows(g)


def colonnade_half() -> list[str]:
    """Ряды колонн: удобно фланкировать, но просторно."""
    g = blank_half()
    for y in (3, 9, 15, 18):
        for x in (3, 8, 13, 18):
            rect(g, x, y, x, y, "o")
    dot(g, 14, 8, ":")
    dot(g, 14, 21, ":")
    return as_rows(g)


def fortress_half() -> list[str]:
    """Две глухие крепости: внутри можно, войти — только через вход."""
    g = blank_half()
    rect(g, 2, 6, 9, 11, fill=False)             # первая крепость
    rect(g, 6, 15, 11, 20, fill=False)           # вторая
    # Широкие боковые входы: рамка стены пробита, внутрь можно заехать.
    carve(g, 9, 8, 9, 10)
    carve(g, 6, 17, 7, 18)
    return as_rows(g)


def meadow_half() -> list[str]:
    """Пустое поле с редкими укрытиями и грязью: полигон для отладки."""
    g = blank_half()
    dot(g, 14, 2, ":")
    dot(g, 7, 8, ":")
    dot(g, 17, 8, ":")
    dot(g, 14, 15, ":")
    dot(g, 7, 21, ":")
    rect(g, 10, 25, 13, 26, ",")
    return as_rows(g)


def zigzag_half() -> list[str]:
    """Змейка: смещённые горизонтальные брустверы гонят танки змейкой."""
    g = blank_half()
    rect(g, 3, 5, 13, 6)
    rect(g, 11, 11, 21, 12)
    rect(g, 3, 17, 13, 18)
    dot(g, 16, 5, ":")
    dot(g, 8, 12, ":")
    dot(g, 16, 18, ":")
    rect(g, 19, 23, 22, 24, ",")
    return as_rows(g)


def cross_half() -> list[str]:
    """Крест: центральный блок со сквозным проходом, колонны по углам."""
    g = blank_half()
    rect(g, 20, 6, 23, 21)                 # левая половина центрального блока
    carve(g, 20, 12, 23, 13)               # сквозной проход посередине
    dot(g, 19, 12, ":")
    dot(g, 19, 13, ":")
    rect(g, 4, 4, 5, 5, "o")
    rect(g, 4, 22, 5, 23, "o")
    rect(g, 11, 3, 12, 4, "o")
    rect(g, 11, 23, 12, 24, "o")
    return as_rows(g)


def rings_half() -> list[str]:
    """Кольца: разомкнутое кольцо колонн, внутри — низкие укрытия и грязь."""
    g = blank_half()
    rect(g, 13, 6, 22, 19, "o", fill=False)  # кольцо, внутрь можно заехать
    carve(g, 13, 12, 13, 13)                 # западный вход в кольцо
    carve(g, 22, 12, 22, 13)                 # восточный вход в кольцо
    dot(g, 17, 10, ":")
    dot(g, 17, 15, ":")
    rect(g, 15, 12, 19, 13, ",")
    dot(g, 6, 13, ":")
    dot(g, 6, 14, ":")
    return as_rows(g)


def swamp_half() -> list[str]:
    """Топи: вязкая грязь пятнами, островки укрытий и пара колонн."""
    g = blank_half()
    rect(g, 2, 2, 9, 6, ",")
    rect(g, 14, 8, 21, 12, ",")
    rect(g, 4, 18, 11, 23, ",")
    rect(g, 16, 21, 22, 24, ",")
    dot(g, 10, 4, ":")
    dot(g, 17, 10, ":")
    dot(g, 7, 20, ":")
    dot(g, 19, 22, ":")
    dot(g, 12, 14, ":")
    rect(g, 11, 12, 12, 13, "o")
    rect(g, 2, 13, 3, 14, "o")
    return as_rows(g)


def maze_half() -> list[str]:
    """Лабиринт: вертикальные стены с чередующимися проходами."""
    g = blank_half()
    rect(g, 5, 4, 6, 10)
    rect(g, 11, 12, 12, 18)
    rect(g, 17, 4, 18, 10)
    rect(g, 5, 18, 6, 24)
    rect(g, 17, 18, 18, 24)
    dot(g, 8, 8, ":")
    dot(g, 14, 15, ":")
    dot(g, 8, 20, ":")
    return as_rows(g)


def duel_half() -> list[str]:
    """Дуэль: тесная plaza с угловыми блоками и колоннами у центра."""
    g = blank_half()
    rect(g, 5, 5, 9, 8)
    rect(g, 5, 19, 9, 22)
    rect(g, 13, 4, 16, 6)
    rect(g, 13, 21, 16, 23)
    rect(g, 20, 12, 21, 15, "o")
    dot(g, 18, 10, ":")
    dot(g, 18, 17, ":")
    dot(g, 11, 13, ":")
    dot(g, 11, 14, ":")
    return as_rows(g)


def pillar_half() -> list[str]:
    """Ступени: у края — низкие ступени, к центру — глухие террасы."""
    g = blank_half()
    rect(g, 3, 10, 4, 17)
    rect(g, 8, 8, 9, 19)
    rect(g, 14, 6, 15, 21)
    dot(g, 11, 12, ":")
    dot(g, 11, 15, ":")
    rect(g, 18, 13, 19, 14, "o")
    rect(g, 20, 3, 21, 4, ",")
    return as_rows(g)


def crater_half() -> list[str]:
    """Кратер: круглая арена с бруствером посередине и зубцами по краю."""
    g = blank_half()
    rect(g, 9, 12, 14, 15)
    for x, y in ((5, 6), (5, 20), (11, 4), (11, 22), (18, 6), (18, 20)):
        rect(g, x, y, x + 1, y + 1, "o")
    dot(g, 7, 13, ":")
    dot(g, 16, 13, ":")
    rect(g, 3, 3, 6, 4, ",")
    return as_rows(g)


def spiral_half() -> list[str]:
    """Спираль: нить стен закручивается дважды, проходы широкие."""
    g = blank_half()
    rect(g, 4, 5, 20, 5)                     # внешняя нить
    rect(g, 4, 5, 4, 19)
    rect(g, 4, 20, 17, 20)
    rect(g, 21, 7, 21, 19)
    carve(g, 21, 12, 21, 13)                 # разрыв внешней нити
    rect(g, 9, 9, 17, 9)                     # вторая нить
    rect(g, 9, 9, 9, 16)
    carve(g, 9, 12, 9, 13)                   # и разрыв второй: иначе внутри глухо
    dot(g, 13, 12, ":")
    dot(g, 13, 15, ":")
    rect(g, 18, 24, 21, 25, ",")
    return as_rows(g)


def pit_half() -> list[str]:
    """Яма: кольцо-ров даёт круговой обстрел и негде спрятаться."""
    g = blank_half()
    rect(g, 6, 6, 17, 17, fill=False)
    carve(g, 6, 11, 6, 12)
    carve(g, 17, 11, 17, 12)
    rect(g, 11, 11, 12, 12, "o")
    rect(g, 10, 10, 13, 13, ",")
    rect(g, 3, 13, 4, 14, "o")
    rect(g, 19, 13, 20, 14, "o")
    return as_rows(g)


def gate_half() -> list[str]:
    """Врата: чередующиеся створки, между ними — открытые проходы."""
    g = blank_half()
    rect(g, 3, 5, 8, 6)
    rect(g, 3, 11, 8, 12)
    rect(g, 3, 17, 8, 18)
    rect(g, 3, 23, 8, 24)
    rect(g, 15, 8, 20, 9)
    rect(g, 15, 14, 20, 15)
    rect(g, 15, 20, 20, 21)
    dot(g, 11, 7, ":")
    dot(g, 11, 16, ":")
    dot(g, 13, 22, ":")
    rect(g, 11, 11, 12, 12, "o")
    return as_rows(g)


def sandbox_half() -> list[str]:
    """Песочница: разное покрытие пятнами, скорость зависит от поверхности."""
    g = blank_half()
    rect(g, 3, 3, 8, 8, ",")
    rect(g, 14, 4, 19, 7, ":")
    rect(g, 4, 15, 7, 19, ":")
    rect(g, 15, 16, 20, 21, ",")
    rect(g, 10, 11, 13, 16)
    rect(g, 10, 13, 13, 14, ",")
    rect(g, 2, 24, 5, 25, ":")
    rect(g, 18, 24, 21, 25, ",")
    return as_rows(g)


MAPS = [
    ("arena", arena_half(), (4, 21), "Открытая арена с колоннами и центральным бруствером.",
     "Арена"),
    ("corridor", corridor_half(), (4, 4), "Длинные стены с широкими проходами: коридоры для манёвра.",
     "Коридор"),
    ("colonnade", colonnade_half(), (5, 24), "Ряды колонн и низкие укрытия — удобно фланкировать.",
     "Колоннада"),
    ("fortress", fortress_half(), (3, 24), "Две глухие крепости: заходишь через широкий вход.",
     "Крепость"),
    ("meadow", meadow_half(), (1, 2), "Пустое поле с редкими укрытиями. Для отладки и разведки.",
     "Лужайка"),
    ("zigzag", zigzag_half(), (3, 24), "Смещённые брустверы: танки идут змейкой от укрытия к укрытию.",
     "Змейка"),
    ("cross", cross_half(), (3, 24), "Центральный блок со сквозным проходом и колоннами по углам.",
     "Крест"),
    ("rings", rings_half(), (3, 3), "Разомкнутое кольцо колонн: внутри низкие укрытия и грязь.",
     "Кольца"),
    ("swamp", swamp_half(), (5, 13), "Вязкие топи пятнами: островки укрытий среди грязи.",
     "Топи"),
    ("maze", maze_half(), (2, 13), "Вертикальные стены с чередующимися проходами.",
     "Лабиринт"),
    ("duel", duel_half(), (2, 13), "Тесная площадь: угловые блоки и колонны у самого центра.",
     "Дуэль"),
    ("pillar", pillar_half(), (6, 2), "Три глухие террасы ступенями: негде замереть.",
     "Ступени"),
    ("crater", crater_half(), (3, 2), "Круглая арена с бруствером в центре и зубцами по краю.",
     "Кратер"),
    ("spiral", spiral_half(), (2, 13), "Загнутая лента стен: один вход внутрь и круговой обстрел.",
     "Спираль"),
    ("pit", pit_half(), (2, 3), "Ров по периметру арены: спрятаться некуда, бей по кругу.",
     "Яма"),
    ("gate", gate_half(), (2, 2), "Чередующиеся створки ворот и открытые проходы между ними.",
     "Врата"),
    ("sandbox", sandbox_half(), (1, 1), "Пятна грязи и укрытий: скорость зависит от поверхности.",
     "Песочница"),
]


def main() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    saved: dict[str, dict] = {}
    for name, half, spawn, desc, title in MAPS:
        data = save_map(name, half, spawn, desc, title)
        problem = spawn_check(name, data["rows"])
        if problem:
            # Спавн из MAPS оказался непроезжим — ищем самый просторный сами.
            spawn = best_spawn(half)
            data = save_map(name, half, spawn, desc, title)
            problem = spawn_check(name, data["rows"])
            print(f"  {name}: спавн заменён на {spawn} ({problem or 'проезд свободен'})")
        if problem:
            raise SystemExit(problem)
        saved[name] = data
        print(f"{name:10} {len(data['rows'])}x{len(data['rows'][0])}  {desc}")
    # Проверка загрузки, симметрии и проходимости.
    from engine.grid import load_map

    for name, *_ in MAPS:
        arena = load_map(name)
        rep = arena.symmetry_report()
        print(f"  проверка {name}: симметрия={rep['symmetric']} "
              f"спавны={[f'{s.x:.0f},{s.y:.0f}' for s in arena.spawns]}")
        if not rep["symmetric"]:
            raise SystemExit(f"карта {name} несимметрична!")
        check_playable(name, saved[name]["rows"],
                       int(arena.spawns[0].x // arena.tile),
                       int(arena.spawns[0].y // arena.tile))
        print(f"  проверка {name}: связность и проходимость ок")


if __name__ == "__main__":
    main()
