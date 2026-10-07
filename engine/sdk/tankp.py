"""TANKP — SDK для танковых программ.

Единственная точка доступа к бою. Скрипт импортирует только этот модуль::

    from tankp import TankProgram

    class Brain(TankProgram):
        def on_tick(self, o):
            ...

    program = Brain()

или пишет функции ``on_start(ctx)`` / ``on_tick(o)`` прямо на уровне модуля.

Что доступно в наблюдении ``o``:

* ``o.me``      — свой танк: координаты, курс корпуса и башни, скорость, ХП, КД;
* ``o.enemy``   — противник **только если он сейчас виден**, иначе ``None``;
* ``o.sight``   — почему вы его видите или не видите (конус, дальность, LOS);
* ``o.map``     — карта: проходимость, «зазор» до стены, направление к стене;
* ``o.tick``, ``o.time``, ``o.dt``, ``o.tank``, ``o.budget_ms``.

Возвращайте ``Action(drive=…, turn=…, turret=…, fire=…)``:

* ``drive``  — ``-1`` задний ход, ``0`` стоп, ``1`` полный вперёд;
* ``turn``   — поворот корпуса, ``-1`` налево, ``+1`` направо (по часовой);
* ``turret`` — поворот башни с той же логикой;
* ``fire``   — ``True``, если КД готово и надо стрелять.

Скорость и манёвренность ограничены физикой движка: скрипт управляет
намерениями, а не телепортирует танк. На решение отводится ``budget_ms``
миллисекунд (по умолчанию 10) — уложиться нужно в каждый тик.
"""

from __future__ import annotations

import math

__all__ = [
    "TankProgram", "Observation", "Action", "TankView", "SensorView", "MapView", "SpawnPoint", "Point",
    "log", "PI", "TAU", "DEG", "RAD", "FORWARD", "REVERSE", "STOP", "LEFT", "RIGHT",
    "clamp", "wrap", "sign", "hypot", "Point",
]

PI = math.pi
TAU = math.pi * 2
DEG = math.degrees
RAD = math.radians

FORWARD = 1.0
REVERSE = -1.0
STOP = 0.0
LEFT = -1.0
RIGHT = 1.0

_LOG_BUFFER = []

#: От центра танка до конца ствола. Должно совпадать с
#: ``Balance.muzzle_offset`` — снаряд появляется именно здесь.
MUZZLE_LEN = 26.0
#: Полуразмеры корпуса по умолчанию (см. ``Balance.half``). Нужны, чтобы
#: переводить угловой допуск прицеливания в пиксели: ошибка в 5° на 400 px —
#: это 35 px промаха при цели шириной 34 px.
TARGET_HALF_LEN = 17.0
TARGET_HALF_WID = 11.0


def log(message) -> None:
    """Пишет строку в лог боя — её увидит человек в панели событий."""
    _LOG_BUFFER.append(str(message))


def _take_logs():
    out = list(_LOG_BUFFER)
    _LOG_BUFFER.clear()
    return out


def _xy(x, y=None):
    """Приводит цель к паре координат: Point, TankView или кортеж."""
    if y is None:
        if isinstance(x, (tuple, list)):
            x, y = x[0], x[1]
        else:
            x, y = x.x, x.y
    return float(x), float(y)


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def wrap(a):
    """Нормализует угол в диапазон (-pi, pi]."""
    a = math.fmod(a + PI, TAU)
    if a < 0:
        a += TAU
    return a - PI


def sign(v):
    return (v > 0) - (v < 0)


def hypot(x, y):
    return math.hypot(x, y)


class Point:
    """Простая точка для целей навигации (x, y)."""

    __slots__ = ("x", "y")

    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y

    def __repr__(self):
        return f"Point({self.x:.1f}, {self.y:.1f})"

    def __iter__(self):
        return iter((self.x, self.y))


class TankView:
    """Наблюдаемый танк (свой или вражеский). Только чтение."""

    __slots__ = ("x", "y", "hull", "turret", "vx", "vy", "hp", "hp_max",
                 "ammo_ready", "cooldown", "reload")

    def __init__(self, d: dict):
        self.x = d["x"]
        self.y = d["y"]
        self.hull = d["hull"]
        self.turret = d["turret"]
        self.vx = d["vx"]
        self.vy = d["vy"]
        self.hp = d["hp"]
        self.hp_max = d["hp_max"]
        self.ammo_ready = d["ammo_ready"]
        self.cooldown = d["cooldown"]
        self.reload = d["reload"]

    # --- производные величины ---

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    @property
    def forward(self) -> tuple[float, float]:
        return (math.cos(self.hull), math.sin(self.hull))

    @property
    def barrel(self) -> tuple[float, float]:
        return (math.cos(self.turret), math.sin(self.turret))

    def pos(self) -> Point:
        return Point(self.x, self.y)

    def dist_to(self, other) -> float:
        """Расстояние до точки, танка или пары `(x, y)`."""
        x, y = _xy(other)
        return math.hypot(x - self.x, y - self.y)

    def angle_to(self, x, y=None) -> float:
        """Абсолютный угол на цель.

        Целью может быть Point, TankView или пара `(x, y)`.
        """
        x, y = _xy(x, y)
        return math.atan2(y - self.y, x - self.x)

    def hull_error(self, x, y=None) -> float:
        """Знаковая ошибка наведения по корпусу, в градусах."""
        return DEG(wrap(self.angle_to(x, y) - self.hull))

    def turret_error(self, x, y=None) -> float:
        """Знаковая ошибка наведения по башне, в градусах."""
        return DEG(wrap(self.angle_to(x, y) - self.turret))

    def rear_point(self, dist: float = 120.0) -> Point:
        """Точка за кормой танка — идеальная позиция для выстрела."""
        return Point(self.x - math.cos(self.hull) * dist,
                     self.y - math.sin(self.hull) * dist)

    def side_point(self, dist: float = 120.0, left: bool = True) -> Point:
        s = -1.0 if left else 1.0
        return Point(self.x + s * math.cos(self.hull + PI / 2) * dist,
                     self.y + s * math.sin(self.hull + PI / 2) * dist)

    def aim_time(self, target, speed: float = 620.0, lead: bool = False) -> float:
        """Время подлёта снаряда; при ``lead`` — с упреждением по скорости цели."""
        t = self.dist_to(target) / max(speed, 1.0)
        if lead:
            fx = target.x + target.vx * t
            fy = target.y + target.vy * t
            return math.hypot(fx - self.x, fy - self.y) / max(speed, 1.0)
        return t


class SpawnPoint(Point):
    """Точка спавна: координаты и направление корпуса."""

    __slots__ = ("angle",)

    def __init__(self, x=0.0, y=0.0, angle=0.0):
        super().__init__(x, y)
        self.angle = angle


class SensorView:
    """Состояние органов чувств: видно ли противника и почему."""

    __slots__ = ("enemy_visible", "in_cone", "in_range", "los_clear", "bumper",
                 "bearing", "distance")

    def __init__(self, d: dict):
        self.enemy_visible = d.get("enemy_visible", False)
        self.in_cone = d.get("in_cone", False)
        self.in_range = d.get("in_range", False)
        self.los_clear = d.get("los_clear", False)
        self.bumper = d.get("bumper", False)
        self.bearing = d.get("bearing")
        self.distance = d.get("distance")

    @property
    def bearing_deg(self):
        return None if self.bearing is None else DEG(self.bearing)

    def __repr__(self):
        vis = "ВИДЕН" if self.enemy_visible else "не виден"
        return f"<Sensor {vis} d={self.distance}>"


class MapView:
    """Карта боя: сетка, проходимость, «зазор» до ближайшей стены."""

    def __init__(self, payload: dict):
        self.tile_size = payload["tile"]        # размер тайла в пикселях
        self.width = payload["width"]          # тайлов по X
        self.height = payload["height"]        # тайлов по Y
        self.pixel_width = payload["pixel_width"]
        self.pixel_height = payload["pixel_height"]
        self.rows = payload["rows"]            # символы тайлов
        self.clearance_grid = payload["clearance"]
        self.spawns = [SpawnPoint(s["x"], s["y"], s.get("angle", 0.0))
                       for s in payload["spawns"]]

    # --- запросы ---

    def tile(self, tx: int, ty: int) -> str:
        if tx < 0 or ty < 0 or ty >= self.height or tx >= self.width:
            return "#"
        return self.rows[ty][tx]

    def passable(self, x: float, y: float) -> bool:
        """Проезжает ли точка (мировые координаты)."""
        return self.tile(int(x // self.tile_size), int(y // self.tile_size)) not in "#o:"

    def opaque(self, x: float, y: float) -> bool:
        return self.tile(int(x // self.tile_size), int(y // self.tile_size)) in "#o"

    def clearance(self, x: float, y: float) -> float:
        """Пикселей до ближайшей непроезжей клетки — «свободный коридор»."""
        tx = min(max(int(x // self.tile_size), 0), self.width - 1)
        ty = min(max(int(y // self.tile_size), 0), self.height - 1)
        return self.clearance_grid[ty][tx] * self.tile_size

    def wall_dir(self, x: float, y: float) -> float:
        """Направление к ближайшей стене в градусах — «куда упираться спиной»."""
        best = None
        best_d = None
        r = int(self.clearance(x, y) // self.tile_size) + 1
        cx, cy = int(x // self.tile_size), int(y // self.tile_size)
        for oy in range(-r, r + 1):
            for ox in range(-r, r + 1):
                tx, ty = cx + ox, cy + oy
                if tx < 0 or ty < 0 or ty >= self.height or tx >= self.width:
                    continue
                if self.rows[ty][tx] not in "#o:":
                    continue
                wx, wy = (tx + 0.5) * self.tile_size, (ty + 0.5) * self.tile_size
                d = (wx - x) ** 2 + (wy - y) ** 2
                if best_d is None or d < best_d:
                    best_d = d
                    best = math.atan2(wy - y, wx - x)
        return DEG(best or 0.0)

    def blocked_between(self, x0, y0, x1, y1) -> bool:
        """Есть ли стена на отрезке — грубая проверка прямой видимости."""
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) / (self.tile_size * 0.4)) + 1
        for i in range(1, steps):
            t = i / steps
            if self.opaque(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t):
                return True
        return False

    def free_area(self, x: float, y: float, radius: float) -> bool:
        """Есть ли вокруг точки свободное место заданного радиуса."""
        return self.clearance(x, y) >= radius


class Action:
    """Команда движку. Намерения, а не координаты — их ограничит физика."""

    __slots__ = ("drive", "turn", "turret", "fire")

    def __init__(self, drive=0.0, turn=0.0, turret=0.0, fire=False):
        self.drive = clamp(_num(drive))
        self.turn = clamp(_num(turn))
        self.turret = clamp(_num(turret))
        self.fire = bool(fire)

    def to_dict(self) -> dict:
        return {"drive": self.drive, "turn": self.turn,
                "turret": self.turret, "fire": self.fire}

    def __repr__(self):
        return (f"Action(drive={self.drive:.2f}, turn={self.turn:.2f}, "
                f"turret={self.turret:.2f}, fire={self.fire})")


def _num(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):
        return default
    return f


class Observation:
    """Всё, что танк знает о мире в конкретном тике."""

    __slots__ = ("tick", "time", "dt", "tank", "budget_ms", "bullet_speed",
                 "bullet_turn_rate", "muzzle_len", "spread_deg", "me", "enemy",
                 "sight", "map", "target_half", "_last_seen")

    def __init__(self, payload: dict, mapview: MapView | None):
        self.tick = payload["tick"]
        self.time = payload["time"]
        self.dt = payload["dt"]
        self.tank = payload["tank"]
        self.budget_ms = payload["budget_ms"]
        self.bullet_speed = float(payload.get("bullet_speed", 620.0))
        self.bullet_turn_rate = float(payload.get("turret_turn", 3.6))
        self.muzzle_len = float(payload.get("muzzle_offset", MUZZLE_LEN))
        self.spread_deg = float(payload.get("spread", 0.0))
        half = payload.get("half") or (TARGET_HALF_LEN, TARGET_HALF_WID)
        self.target_half = (float(half[0]), float(half[1]))
        self.me = TankView(payload["me"])
        self.enemy = TankView(payload["enemy"]) if payload.get("enemy") else None
        self.sight = SensorView(payload["sight"])
        self.map = mapview
        # Куда башня была направлена, когда цель была видна в последний
        # раз: башня доезжает до этой точки и стоит, вместо меандра.
        self._last_seen = Point(self.me.x + math.cos(self.me.turret) * self.muzzle_len,
                                self.me.y + math.sin(self.me.turret) * self.muzzle_len)

    # --- часто нужные величины ---

    @property
    def enemy_visible(self) -> bool:
        return self.enemy is not None

    def dist_to_enemy(self):
        return self.me.dist_to(self.enemy) if self.enemy else None

    def bearing_to_enemy(self):
        """Знаковая ошибка наведения по башне, градусы."""
        return self.me.turret_error(self.enemy) if self.enemy else None

    def hull_error_to_enemy(self):
        return self.me.hull_error(self.enemy) if self.enemy else None

    def sign(self, v):
        return sign(v)

    def deg(self, v):
        return DEG(v)

    def rad(self, v):
        return RAD(v)

    def wrap(self, v):
        return wrap(v)

    # --- навигационные помощники ---

    def lead(self, target, extra: float = 0.0, factor: float = 1.0):
        """Упреждение: куда наводить башню, чтобы попасть по движущейся цели.

        Расстояние считается от ствола, а не от центра танка, и уточняется
        двумя итерациями: первая даёт время полёта до текущей позиции цели,
        вторая — до позиции, куда цель сместится за это время. Без второй
        итерации на встречном ходу упреждение систематически недобивает.

        `extra` — добавка к времени полёта (например, на доворот башни),
        `factor` — насколько верить скорости цели.

        Вблизи упреждение работает плохо (у ``berserk`` на 23 выстрела
        в упор — 3 попадания), но гасить его масштабированием нельзя:
        попытка ограничить смещение ломала все скрипты сразу.
        """
        x, y = _xy(target)
        vx = getattr(target, "vx", 0.0)
        vy = getattr(target, "vy", 0.0)
        speed = max(1.0, self.bullet_speed)
        t = self.bullet_time_to(x, y) + extra
        for _ in range(2):
            x, y = _xy(target)
            t = self.bullet_time_to(x + vx * t * factor,
                                    y + vy * t * factor) + extra
        return (x + vx * t * factor, y + vy * t * factor)

    def bullet_time_to(self, x, y) -> float:
        """Время полёта снаряда до точки, отсчитанное от ствола."""
        mx, my = self.muzzle()
        return math.hypot(x - mx, y - my) / max(1.0, self.bullet_speed)

    def muzzle(self, angle: float = None) -> tuple[float, float]:
        """Мировая позиция конца ствола — от неё реально летит снаряд.

        ``angle`` — курс ствола, если он отличается от текущего. Это важно:
        наводить надо так, чтобы ЛУЧ из конца ствола прошёл через цель, а
        не так, чтобы ствол смотрел на цель из центра танка. Разница
        постоянная и равна смещению ствола (26 px) — на 400 px это
        3.7°, больше любого разумного допуска.
        """
        a = self.me.turret if angle is None else angle
        return (self.me.x + math.cos(a) * self.muzzle_len,
                self.me.y + math.sin(a) * self.muzzle_len)

    def intercept_time(self, target) -> float:
        """Сколько секунд летит снаряд до текущей позиции цели."""
        return self.bullet_time_to(*_xy(target))

    def steer_to(self, x, y, tol: float = 40.0, reverse: bool = False,
                 avoid: bool = True):
        """Подсказка ``(turn, drive)`` к точке — можно, но не обязательно.

        По умолчанию учитывает стены: если прямо по курсу близко, танк
        подруливает в сторону, где свободнее, и соскальзывает вдоль стены.
        ``avoid=False`` — голый курс на цель.
        """
        x, y = _xy(x, y)
        d = math.hypot(x - self.me.x, y - self.me.y)
        if d < 1e-6:
            return 0.0, 0.0
        want = math.atan2(y - self.me.y, x - self.me.x)
        err = wrap(want - self.me.hull)
        turn = err * 2.0
        if avoid and self.map is not None and d > tol:
            turn = self._slide_off_walls(turn)
        turn = clamp(turn, -1.0, 1.0)
        if abs(err) > 1.9:            # цель строго за кормой — едем назад
            drive = -0.7
        elif abs(err) > 1.2:
            drive = 0.25 if not reverse else -0.5
        else:
            drive = 0.0 if d < tol else (1.0 if not reverse else -0.6)
        return turn, drive

    PROBE = 60.0            # насколько вперёд смотрим при поиске обхода

    def _slide_off_walls(self, turn: float) -> float:
        """Добавляет к повороту уход от стены, если она прямо по курсу."""
        m = self.map
        fx, fy = math.cos(self.me.hull), math.sin(self.me.hull)
        lx, ly = -fy, fx
        p = self.PROBE
        ahead = m.clearance(self.me.x + fx * p, self.me.y + fy * p)
        if ahead > p - 5.0:
            return turn                      # прямо по курсу чисто
        q = p * 0.75
        left = m.clearance(self.me.x + fx * q + lx * q, self.me.y + fy * q + ly * q)
        right = m.clearance(self.me.x + fx * q - lx * q, self.me.y + fy * q - ly * q)
        if left > right:
            return turn + 0.85               # соскальзываем влево
        return turn - 0.85

    def aim_turret(self, x, y=None, deadzone: float = 0.0):
        """Команда доворота башни к точке в диапазоне -1..1.

        Пропорциональный доворот: команда тем меньше, чем ближе цель к
        стволу. Раньше ИИ вели башню через ``sign(err)``, то есть всегда
        полной командой, и она перелетала цель и джиттерила на ±3.4°
        (3.6 рад/с ÷ 60 Гц).

        Деадзон по умолчанию нулевой, и это не украшение: раньше он стоял
        0.02 рад (1.15°), а геометрический допуск попадания на 700 px —
        около 0.6°. Башня переставала доводиться за полградуса ДО того,
        как цель попадала в допуск, и ИИ с одинаковым `fire` годами
        стояли с наведённым стволом, не сделав ни одного выстрела.
        Джиттер теперь гасит сам пропорциональный закон: на малой ошибке
        команда меньше шага за тик, то есть башня просто не двигается.
        """
        err = self.aim_error(x, y)
        if abs(err) < deadzone:
            return 0.0
        # При большой ошибке даём полную команду, при малой — мягко
        # подтормаживаем, иначе башня всё равно проскочит мимо.
        return clamp(err * 4.0, -1.0, 1.0)

    def aim_error(self, x, y=None) -> float:
        """Угловая ошибка башни: насколько луч из ствола не проходит через точку.

        Считается итеративно: позиция ствола зависит от его угла, а угол
        — от позиции ствола. Три итерации дают точность лучше 0.01°.
        Ошибка берётся от центра танка, поэтому башня реально
        прицеливается, а не просто поворачивается «в сторону цели».
        """
        x, y = _xy(x, y)
        a = self.me.turret
        for _ in range(3):
            mx = self.me.x + math.cos(a) * self.muzzle_len
            my = self.me.y + math.sin(a) * self.muzzle_len
            a = math.atan2(y - my, x - mx)
        return wrap(a - self.me.turret)

    def aim_and_fire(self, target=None, strict: float = 0.55,
                     factor: float = 1.0):
        """Доводит башню на упреждённую точку и стреляет, когда попадёт.

        Возвращает ``(turret, fire)`` — готовые значения для ``Action``.
        Стрельба разрешена только когда башня уже смотрит в упреждённую точку
        с учётом собственной скорости поворота: пока башня ещё доворачивает,
        снаряд ушёл бы в сторону.

        Так заодно уходит меандр при поиске: когда цели нет, ``aim_turret``
        ведёт башню к последней точке и останавливается, а не крутит
        «туда-обратно» каждые 25 тиков.
        """
        if target is None:
            return self.aim_turret(self._last_seen), False
        # Точка цели берётся по её текущим координатам: упреждение пока
        # не нужно, важно наводить башню туда, где цель сейчас.
        target_x, target_y = _xy(target)
        aim = self.lead(target, factor=factor)
        # Запоминаем, куда целиться: если противник спрячется, башня
        # доедет до этой точки и остановится, а не будет крутить меандр.
        self._last_seen = Point(*aim)
        turret = self.aim_turret(aim)
        err = self.aim_error(aim)
        # Ствол выстрелит уже после поворота башни в этом тике, поэтому
        # считаем остаточную ошибку после доворота, а не текущую:
        # turret = clamp(err * 4) повёрнет башню ровно на turret * turn.
        residual = abs(err - turret * self.bullet_turn_rate * self.dt)
        d = math.hypot(target_x - self.me.x, target_y - self.me.y)
        tol = self.hit_tolerance(d, strict)
        return turret, residual < tol and self.me.ammo_ready

    def hit_tolerance(self, dist: float, strict: float = 0.55) -> float:
        """Допуск наведения в радианах, при котором снаряд попадёт в цель.

        Считается по геометрии, а не «на глазок в градусах»: снаряд летит
        по прямой, поэтому попадёт, если боковое отклонение луча меньше
        половины минимальной ширины корпуса. Допуск масштабируется
        ``strict``: 1.0 — максимально строго, меньше — ленивее. Так
        черепаха остаётся трудной целью, но никто не стреляет в пустоту.

        Дополнительно учитывается разброс ствола: снаряд может уйти на
        ``spread`` градусов в любую сторону, поэтому из запаса вычитается
        и он — иначе «попадание по геометрии» окажется промахом.
        """
        hx, hy = self.target_half
        half = min(hy, hx * 0.8)
        raw = math.atan2(half * strict, max(1.0, dist))
        # Из геометрического запаса вычитаем разброс ствола: снаряд может
        # уйти на ±spread градусов в любую сторону, и «точное» попадание
        # по геометрии тогда превращается в промах. На 750 px допуск был
        # 0.67°, разброс 0.4°, в сумме 1.07° — это 14 px бокового ухода
        # при полуширине корпуса 8.8 px, то есть треть выстрелов в пустоту.
        spread = math.radians(self.spread_deg)
        return clamp(raw - spread, 0.002, 0.25)

    def enemy_rear_point(self, dist: float = 130.0) -> Point:
        return self.enemy.rear_point(dist) if self.enemy else Point(self.me.x, self.me.y)

    def wall_behind(self, dist: float = 60.0) -> Point:
        """Точка «за спиной», у ближайшей стены — позиция для кампера."""
        a = RAD(self.map.wall_dir(self.me.x, self.me.y))
        return Point(self.me.x - math.cos(a) * dist, self.me.y - math.sin(a) * dist)

    def __repr__(self):
        who = f"{self.me.x:.0f},{self.me.y:.0f} hp={self.me.hp}"
        foe = f"враг {self.enemy.x:.0f},{self.enemy.y:.0f}" if self.enemy else "врага не видно"
        return f"<Obs t={self.tick} {who}; {foe}>"


class TankProgram:
    """Базовый класс для программ. Переопределите ``on_tick``."""

    def on_start(self, ctx) -> None:
        """Вызывается один раз перед первым тиком."""

    def on_tick(self, o: Observation) -> Action:
        """Вызывается 60 раз в секунду. Здесь и живёт ваша стратегия."""
        return Action()


def build_observation(payload: dict, mapview: MapView | None) -> Observation:
    return Observation(payload, mapview)