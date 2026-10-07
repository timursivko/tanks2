#!TANKP 1
# name: Karakurt
# author: arena-06cca649
# difficulty: 5
# color: #c04dff
# description: Держит ракурс рикошета 30–40° (чужой гейт сам запрещает выстрел), маятник на косой линии, три модели упреждения с онлайн-выбором, обучение уклонению врага и темп по перезарядке.
# tags: ракурс,темп,адаптация,уклонение, geometry

"""Karakurt — дуэльный танк «угла и темпа».

Три несущие идеи:

1. **Ракурс рикошета.** У брони есть сплошная мёртвая зона: снаряд,
   пришедший под 30–40° к нормали, рикошетит И по лбу (порог 30°), И по
   борту (порог 50°). Держим корпус под ~35° к линии на врага — и все
   противники с честным гейтом пробития (raptor, nova, tempest, vulkan)
   перестают стрелять вовсе: их собственная геометрия говорит «рикошет».

2. **Маятник на косой.** Газ меняется вперёд/назад со случайным
   полупериодом 0.3–0.8 с: скорость непредсказуема для чужого
   упреждения, а корпус при этом не разворачивается — ракурс держится.

3. **Адаптивный прицел.** Три модели движения цели (постоянная
   скорость, дуга по ω, скорость+ускорение) соревнуются онлайн: каждая
   оценка ошибки копится в EMA, для выстрела берётся лучшая. Отдельно
   учится реакция врага на МОИ выстрелы: если он систематически
   уклоняется вбок — следующий снаряд летит в точку после его уклона.

Плюс темп по перезарядке (враг пуст — давим; пусты мы — рвём линию под
укрытие), уход с линии выпущенного снаряда, A* с кэшем пути, дозор
застревания. Всё укладывается в бюджет 10 мс на тик: тяжёлые расчёты
кэшируются, сканы прицела — 11 лучей O(1) каждый.
"""

from math import (acos, atan2, cos, degrees, exp, fabs, hypot, pi,
                  radians, sin)

from tankp import TankProgram, Action

# --- константы движка (копия config.Balance, чтобы не зависеть от неё) -------

BULLET = 620.0          # скорость снаряда
MUZZLE = 26.0           # вылет ствола от центра
SPREAD = 0.4            # разброс, ± градусов
SPREAD_RAD = radians(SPREAD)
RELOAD = 1.0
HP_MAX = 10.0
DMG = 4.0
HULL_TURN = 2.6         # рад/с корпуса
TURRET_TURN = 3.6       # рад/с башни
ACCEL = 460.0
SPEED_FWD = 190.0
SPEED_REV = 120.0
VIEW_RANGE = 760.0

# пороги рикошета (угол к нормали, град): лоб / борт / корма
RICO = (30.0, 50.0, 60.0)
FACE_HALF = 35.0        # лоб: ±35° от курса корпуса

# целевой ракурс: середина сплошной мёртвой зоны 30..40°
ANGLE_LOCK = radians(35.5)
ANGLE_JIT = radians(1.8)     # медленный джиттер ракурса
TRACK_GAIN = 5.2             # усиление доворота корпуса на азимут
BEAR_LEAD = 1.0 / (TRACK_GAIN * HULL_TURN)   # ровно гасит стационарный лаг

# --- настройки боя -----------------------------------------------------------

D_MIN = 165.0           # ближе — выталкиваем
D_MAX = 430.0           # дальше — сближаемся
STANDOFF = 270.0        # кольцо маятника
PRESS_RANGE = 190.0     # враг пуст — подходим сюда
PRESS_MIN = 120.0       # но не вплотную (хаос тарана)
HIDE_RANGE = 460.0      # пусты мы и враг готов — ищем укрытие до этой дали
POINT_BLANK = 62.0      # в упор: углы не считаем, бьём в центр

GEAR_MIN = 0.26         # полупериод маятника, с
GEAR_MAX = 0.62
GEAR_BIAS = 0.78        # вероятность «нужной» передачи при коррекции дистанции

SIDE_MIN = 2.2          # мин. длительность стороны ракурса, с
SIDE_MAX = 5.0

DODGE_MISS = 42.0       # снаряд разминётся сам — не маневрируем
DODGE_CLEAR = 46.0      # проверка стены вбок от уклона

ERR_READY = 34.0        # допуск ошибки модели, px: враг заряжен
ERR_EMPTY = 58.0        # враг пуст — стреляем свободнее
TF_READY = 0.62         # допуск времени подлёта, с
TF_EMPTY = 0.95
FINISH_HP = 4.0         # добивание: одна наша пуля от смерти

HUNT_FRESH = 4.0        # сколько секунд идём по следу контакта
STUCK_WINDOW = 0.6
STUCK_MILES = 14.0

TAU = pi * 2.0


def wrap(a):
    a = (a + pi) % TAU
    return a - pi


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def unit(x, y):
    d = hypot(x, y)
    if d < 1e-9:
        return 0.0, 0.0
    return x / d, y / d


# --- геометрия выстрела -------------------------------------------------------


def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang):
    """Луч против повёрнутого прямоугольника — копия segment_obb движка.

    Возвращает (t, face, theta_deg): face 0 лоб / 1 борт / 2 корма, theta —
    угол между ходом снаряда и наружной нормалью грани. Луч изнутри корпуса
    (выстрел в упор) даёт t=0 и нормаль поперёк полёта: theta≈0, пробитие.
    """
    ca = cos(ang)
    sa = sin(ang)
    rx = ox - cx
    ry = oy - cy
    lx = rx * ca + ry * sa
    ly = -rx * sa + ry * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca

    tmin = 0.0
    axis = -1
    sgn = 0.0
    if fabs(ldx) < 1e-12:
        if fabs(lx) > hx:
            return None
    else:
        s = -1.0 if ldx > 0.0 else 1.0
        t1 = (-hx - lx) / ldx
        t2 = (hx - lx) / ldx
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin, axis, sgn = t1, 0, s
    if fabs(ldy) < 1e-12:
        if fabs(ly) > hy:
            return None
    else:
        s = -1.0 if ldy > 0.0 else 1.0
        t1 = (-hy - ly) / ldy
        t2 = (hy - ly) / ldy
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin, axis, sgn = t1, 1, s
    if axis < 0:
        # внутри коробки: грань, перпендикулярная полёту
        if fabs(ldx) >= fabs(ldy):
            axis, sgn = 0, (-1.0 if ldx > 0.0 else 1.0)
        else:
            axis, sgn = 1, (-1.0 if ldy > 0.0 else 1.0)
    nx = sgn if axis == 0 else 0.0
    ny = sgn if axis == 1 else 0.0
    wx = nx * ca - ny * sa
    wy = nx * sa + ny * ca
    rel = degrees(wrap(atan2(wy, wx) - ang))
    arel = rel if rel >= 0.0 else -rel
    if arel <= FACE_HALF:
        face = 0
    elif arel >= 180.0 - FACE_HALF:
        face = 2
    else:
        face = 1
    dot = dx * wx + dy * wy
    cos_t = -dot if dot < 0.0 else 0.0
    if cos_t > 1.0:
        cos_t = 1.0
    return (tmin, face, degrees(acos(cos_t)))


def pen_ok(theta, face, margin):
    """Пробивает ли удар с запасом margin (град)."""
    return theta + margin < RICO[face]


# --- карта и навигация --------------------------------------------------------


class Nav:
    """Сетка карты: проходимость, зазор, LOS-DDA, A* с кэшем, патруль."""

    __slots__ = ("w", "h", "tile", "passable", "opaque", "clear", "patrol",
                 "path", "path_goal", "path_i")

    def __init__(self, mv):
        self.w = mv.width
        self.h = mv.height
        self.tile = mv.tile_size
        rows = mv.rows
        w = self.w
        h = self.h
        self.passable = [False] * (w * h)
        self.opaque = [True] * (w * h)
        cl = mv.clearance_grid
        self.clear = [0.0] * (w * h)
        for y in range(h):
            row = rows[y]
            crow = cl[y]
            base = y * w
            for x in range(w):
                c = row[x]
                i = base + x
                self.passable[i] = c not in "#o:"
                self.opaque[i] = c in "#o"
                self.clear[i] = crow[x] * self.tile
        self.path = None
        self.path_goal = None
        self.path_i = 0
        self.patrol = self._make_patrol()

    def ti(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0:
            tx = 0
        elif tx >= self.w:
            tx = self.w - 1
        if ty < 0:
            ty = 0
        elif ty >= self.h:
            ty = self.h - 1
        return tx, ty

    def idx(self, x, y):
        tx, ty = self.ti(x, y)
        return ty * self.w + tx

    def free(self, x, y):
        return self.passable[self.idx(x, y)]

    def clearance(self, x, y):
        return self.clear[self.idx(x, y)]

    def _make_patrol(self):
        """Маршрут обхода всей карты: жадная цепь по прореженным точкам.

        Точки — центры проходимых тайлов с зазором от стен, сеткой ~5 тайлов
        (кемперы жмутся к стенам, поэтому порог зазора низкий). Порядок —
        жадный «ближайший не посещённый»: получается серпантин, который
        гарантированно обходит карту, а не кружит у спавна.
        """
        step = max(4, min(self.w, self.h) // 7)
        best = {}
        for y in range(1, self.h - 1):
            for x in range(1, self.w - 1):
                i = y * self.w + x
                if not self.passable[i] or self.clear[i] < self.tile * 1.15:
                    continue
                k = (x // step, y // step)
                cur = best.get(k)
                if cur is None or self.clear[i] > self.clear[cur]:
                    best[k] = i
        pts = [(((i % self.w) + 0.5) * self.tile, ((i // self.w) + 0.5) * self.tile)
               for i in best.values()]
        if len(pts) < 2:
            return pts
        # жадная цепь от точки, ближайшей к центру карты
        cx = self.w * self.tile * 0.5
        cy = self.h * self.tile * 0.5
        rest = set(range(len(pts)))
        cur = min(rest, key=lambda i: hypot(pts[i][0] - cx, pts[i][1] - cy))
        chain = [cur]
        rest.discard(cur)
        while rest:
            nx = min(rest, key=lambda i: hypot(pts[i][0] - pts[cur][0],
                                               pts[i][1] - pts[cur][1]))
            chain.append(nx)
            rest.discard(nx)
            cur = nx
        return [pts[i] for i in chain]

    # --- LOS: честный DDA по маске непрозрачных тайлов ---------------------

    def los_blocked(self, x0, y0, x1, y1, pad=0.0):
        """Пересекает ли отрезок непрозрачный тайл (с запасом pad px)."""
        gx = x1 - x0
        gy = y1 - y0
        ln = hypot(gx, gy)
        if ln < 1e-6:
            return self.opaque[self.idx(x0, y0)]
        dx = gx / ln
        dy = gy / ln
        if pad > 0.0:
            # три параллельных луча: центр и ±pad по нормали
            nx = -dy * pad
            ny = dx * pad
            return (self._ray_opaque(x0, y0, dx, dy, ln)
                    or self._ray_opaque(x0 + nx, y0 + ny, dx, dy, ln)
                    or self._ray_opaque(x0 - nx, y0 - ny, dx, dy, ln))
        return self._ray_opaque(x0, y0, dx, dy, ln)

    def _ray_opaque(self, ox, oy, dx, dy, max_t):
        tile = float(self.tile)
        w = self.w
        h = self.h
        x = int(ox // tile)
        y = int(oy // tile)
        if x < 0 or y < 0 or x >= w or y >= h:
            return True
        step_x = 1 if dx > 0 else -1
        step_y = 1 if dy > 0 else -1
        tdx = fabs(tile / dx) if dx != 0 else float("inf")
        tdy = fabs(tile / dy) if dy != 0 else float("inf")
        if dx > 0:
            tmx = ((x + 1) * tile - ox) / dx
        elif dx < 0:
            tmx = (x * tile - ox) / dx
        else:
            tmx = float("inf")
        if dy > 0:
            tmy = ((y + 1) * tile - oy) / dy
        elif dy < 0:
            tmy = (y * tile - oy) / dy
        else:
            tmy = float("inf")
        opaque = self.opaque
        t = 0.0
        guard = 0
        lim = w + h + 4
        while t <= max_t and guard < lim * 4:
            guard += 1
            if tmx < tmy:
                t = tmx
                tmx += tdx
                x += step_x
            else:
                t = tmy
                tmy += tdy
                y += step_y
            if t > max_t:
                break
            if x < 0 or y < 0 or x >= w or y >= h:
                return True
            if opaque[y * w + x]:
                return True
        return False

    # --- A* -----------------------------------------------------------------

    def astar(self, sx, sy, gx, gy, max_nodes=1100):
        """Путь списком мировых точек; [] если не нашлось."""
        from heapq import heappop, heappush
        w = self.w
        h = self.h
        st = self._near_free(self.ti(sx, sy))
        gl = self._near_free(self.ti(gx, gy))
        if st is None or gl is None:
            return []
        start = st[1] * w + st[0]
        goal = gl[1] * w + gl[0]
        if start == goal:
            return [(gx, gy)]
        passable = self.passable
        clear = self.clear
        tile = self.tile
        openv = [(0.0, start)]
        came = {}
        gs = {start: 0.0}
        closed = set()
        n = 0
        gxy = (gl[0], gl[1])
        while openv and n < max_nodes:
            _, cur = heappop(openv)
            if cur in closed:
                continue
            closed.add(cur)
            n += 1
            if cur == goal:
                break
            cx = cur % w
            cy = cur // w
            for ox, oy, cost in ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                                 (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)):
                nx = cx + ox
                ny = cy + oy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                idx = ny * w + nx
                if not passable[idx]:
                    continue
                if ox and oy and not (passable[cy * w + nx] and passable[ny * w + cx]):
                    continue
                extra = 0.9 if clear[idx] < tile * 1.4 else 0.0
                ng = gs[cur] + cost + extra
                if ng < gs.get(idx, 1e18):
                    gs[idx] = ng
                    came[idx] = cur
                    ax = nx - gxy[0]
                    ay = ny - gxy[1]
                    heappush(openv, (ng + (hypot(ax, ay) if ax or ay else 0.0), idx))
        if goal not in came and goal != start:
            return []
        path = []
        cur = goal
        while cur != start:
            path.append(((cur % w + 0.5) * tile, (cur // w + 0.5) * tile))
            cur = came.get(cur)
            if cur is None:
                return []
        path.reverse()
        if path:
            path[-1] = (gx, gy)
        return path

    def _near_free(self, txy):
        tx, ty = txy
        w = self.w
        if self.passable[ty * w + tx]:
            return (tx, ty)
        for r in (1, 2):
            best = None
            for oy in range(-r, r + 1):
                for ox in range(-r, r + 1):
                    nx = tx + ox
                    ny = ty + oy
                    if 0 <= nx < w and 0 <= ny < self.h and self.passable[ny * w + nx]:
                        d = ox * ox + oy * oy
                        if best is None or d < best[0]:
                            best = (d, nx, ny)
            if best:
                return (best[1], best[2])
        return None

    def route_to(self, me_x, me_y, gx, gy):
        """Следующая путевая точка к цели с кэшем пути."""
        if self.path:
            wx, wy = self.path[self.path_i]
            if hypot(wx - me_x, wy - me_y) < 46.0:
                if self.path_i < len(self.path) - 1:
                    self.path_i += 1
                else:
                    self.path = None
        if self.path is None:
            goal_t = self.ti(gx, gy)
            if (self.path_goal != goal_t
                    or hypot(gx - me_x, gy - me_y) > 40.0
                    and self.los_blocked(me_x, me_y, gx, gy)):
                self.path_goal = goal_t
                if self.los_blocked(me_x, me_y, gx, gy):
                    self.path = self.astar(me_x, me_y, gx, gy)
                    self.path_i = 0
                    if not self.path:
                        self.path = None
                        return (gx, gy)
                else:
                    return (gx, gy)
            else:
                return (gx, gy)
            if self.path is None:
                return (gx, gy)
        # заглядываем на звено вперёд: руль не мечется в горлах
        look = self.path_i
        while (look + 1 < len(self.path)
               and hypot(self.path[look][0] - me_x, self.path[look][1] - me_y) < 80.0):
            look += 1
        return self.path[look]


# --- мозг ----------------------------------------------------------------------


class Brain(TankProgram):
    """Ракурс рикошета + маятник + адаптивный прицел + темп."""

    def on_start(self, ctx):
        seed = 0
        try:
            seed = int(ctx.get("seed", 0)) * 2 + int(ctx.get("tank", 0))
        except Exception:
            seed = 0
        self.rnd = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        if self.rnd == 0:
            self.rnd = 0x2545F491
        self.last_tick = -1

        self.nav = None
        self.t = 0.0

        # память о враге
        self.seen = False
        self.et = -99.0             # время последнего контакта
        self.etick = -9999
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.ehull = 0.0
        self.ehp = HP_MAX
        self.e_omega = 0.0          # EMA угловой скорости корпуса врага
        self.bear_prev = None       # последний азимут на врага
        self.bear_t = 0.0
        self.bear_omega = 0.0       # EMA скорости азимута
        self.e_ax = self.e_ay = 0.0  # EMA ускорения врага
        self.flips = []             # моменты смены знака боковой скорости
        self.flip_sign = 0
        # онлайн-оценка трёх моделей упреждения: EMA квадрата ошибки, px²
        self.m_err = [400.0, 400.0, 400.0]
        self.m_hist = []            # (tick_due, px_cv, py_cv, px_arc, py_arc, px_ac, py_ac)
        # обучение уклонению врага от МОИХ выстрелов
        self.shots_log = []         # (tick_due, mx, my, dx, dy, pred_x, pred_y, foe_hp)
        self.dodge_ema = 0.0        #signed, px, вдоль левой нормали моей линии
        self.dodge_n = 0
        self.hit_ema = 0.42         # моя результативность (адаптивный гейт)

        # бой
        self.side = 1.0             # сторона ракурса
        self.side_until = 0.0
        self.gear = 1
        self.gear_until = 0.0
        self.threat = None          # (bx, by, dx, dy, t0)
        self.threat_hot = False     # снаряд активно угрожает (гистерезис)
        self.prev_cd = None
        self.prev_enemy = None      # (x, y, turret, ammo_ready, tick)
        self.evade = None           # (ux, uy, until)
        self._cover_goal = None
        self._cover_until = -1.0
        self._patrol_t = 0.0

        # поиск
        self.patrol_order = None
        self.patrol_k = 0

        # застревание
        self.px = self.py = 0.0
        self.mile = 0.0
        self.stuck_t = 0.0
        self.cmd_drive = 0.0
        self.escape_until = -1.0
        self.escape_side = 1.0
        self.fired_tick = -9999

    # ------------------------------------------------------------------ утил
    def _rand(self):
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        return self.rnd / 2147483647.0

    # ------------------------------------------------------------------ тик
    def on_tick(self, o):
        if o.tick <= self.last_tick:
            # новый бой в том же процессе — состояние сбрасываем
            self.on_start({"seed": 0, "tank": o.tank})
        self.last_tick = o.tick
        me = o.me
        self.t = o.time
        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)
        enemy = o.enemy
        if enemy is not None:
            self._see(o, enemy)
        self._watch_stuck(o, me)
        self._resolve_shots(o, enemy)

        threat = self._threat(o, enemy, me)
        turret, fire = self._gun(o, enemy, me)

        if self.t < self.escape_until:
            # выход из клина: назад и вразворот
            self.cmd_drive = -0.9
            return Action(drive=-0.9, turn=self.escape_side * 0.9,
                          turret=turret, fire=fire)

        if threat is not None:
            turn, drive = self._dodge_move(o, me, enemy, threat)
        elif enemy is not None:
            turn, drive = self._engage(o, me, enemy)
        else:
            turn, drive = self._hunt(o, me)
        self.cmd_drive = drive
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # ------------------------------------------------------------- модель врага
    def _see(self, o, e):
        t = self.t
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.25:
                om = wrap(e.hull - self.ehull) / dt
                om = clamp(om, -HULL_TURN, HULL_TURN)
                self.e_omega = self.e_omega * 0.72 + om * 0.28
                ax = (e.vx - self.evx) / dt
                ay = (e.vy - self.evy) / dt
                alim = 900.0
                self.e_ax = self.e_ax * 0.78 + clamp(ax, -alim, alim) * 0.22
                self.e_ay = self.e_ay * 0.78 + clamp(ay, -alim, alim) * 0.22
                # знак боковой скорости относительно линии «враг -> мы»
                me = o.me
                ux, uy = unit(me.x - e.x, me.y - e.y)
                vp = ux * e.vy - uy * e.vx
                s = 1 if vp > 22.0 else (-1 if vp < -22.0 else 0)
                if s and self.flip_sign and s != self.flip_sign:
                    self.flips.append(t)
                    if len(self.flips) > 8:
                        del self.flips[0]
                if s:
                    self.flip_sign = s
            elif dt >= 0.5:
                self.flips = []
                self.flip_sign = 0
                self.e_omega *= 0.5
                self.e_ax *= 0.3
                self.e_ay *= 0.3
                self.bear_omega = 0.0
        # угловая скорость азимута врага (для упреждения ракурса)
        me = o.me
        bear = atan2(e.y - me.y, e.x - me.x)
        if self.bear_prev is not None and 0.0005 < (t - self.bear_t) < 0.25:
            om_b = wrap(bear - self.bear_prev) / (t - self.bear_t)
            self.bear_omega = self.bear_omega * 0.70 + clamp(om_b, -4.0, 4.0) * 0.30
        self.bear_prev = bear
        self.bear_t = t
        self.ex, self.ey = e.x, e.y
        self.evx, self.evy = e.vx, e.vy
        self.ehull = e.hull
        self.ehp = e.hp
        self.et = t
        self.etick = o.tick
        self.seen = True
        # планируем проверки моделей упреждения
        self._plan_predictions(o, e)

    def _plan_predictions(self, o, e):
        """Откладываем три прогноза позиции врага на +0.42 с вперёд."""
        if len(self.m_hist) > 6:
            return
        due = o.tick + 25
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        tf = 0.42
        # CV
        p_cv = (x + vx * tf, y + vy * tf)
        # ARC (вращение вектора скорости по ω)
        om = self.e_omega
        sp = hypot(vx, vy)
        if fabs(om) > 0.06 and sp > 30.0:
            a = atan2(vy, vx)
            da = om * tf
            r = sp / om
            p_arc = (x + r * (sin(a + da) - sin(a)),
                     y - r * (cos(a + da) - cos(a)))
        else:
            p_arc = p_cv
        # ACC
        p_ac = (x + vx * tf + 0.5 * self.e_ax * tf * tf,
                y + vy * tf + 0.5 * self.e_ay * tf * tf)
        self.m_hist.append((due, e.x, e.y, p_cv[0], p_cv[1],
                            p_arc[0], p_arc[1], p_ac[0], p_ac[1]))

    def _score_predictions(self, o, e):
        """Сверяем сбывшиеся прогнозы с фактом — EMA ошибки каждой модели."""
        h = self.m_hist
        while h and h[0][0] <= o.tick:
            due, ax0, ay0, cx, cy, arx, ary, acx, acy = h.pop(0)
            if o.tick - due > 3:
                continue    # цель пропадала — прогноз сверять поздно
            fx, fy = e.x, e.y
            self.m_err[0] = self.m_err[0] * 0.86 + ((fx - cx) ** 2 + (fy - cy) ** 2) * 0.14
            self.m_err[1] = self.m_err[1] * 0.86 + ((fx - arx) ** 2 + (fy - ary) ** 2) * 0.14
            self.m_err[2] = self.m_err[2] * 0.86 + ((fx - acx) ** 2 + (fy - acy) ** 2) * 0.14

    def _best_model(self):
        m = self.m_err
        if m[1] < m[0] and m[1] < m[2]:
            return 1
        if m[2] < m[0]:
            return 2
        return 0

    def _predict(self, e, mx, my, model=None):
        """Упреждённая точка по выбранной модели; итерация по времени полёта."""
        if model is None:
            model = self._best_model()
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        om = self.e_omega
        t = hypot(x - mx, y - my) / BULLET
        for _ in range(2):
            if model == 1 and fabs(om) > 0.06 and hypot(vx, vy) > 30.0:
                a = atan2(vy, vx)
                da = om * t
                r = hypot(vx, vy) / om
                nx = x + r * (sin(a + da) - sin(a))
                ny = y - r * (cos(a + da) - cos(a))
            elif model == 2:
                nx = x + vx * t + 0.5 * self.e_ax * t * t
                ny = y + vy * t + 0.5 * self.e_ay * t * t
            else:
                nx = x + vx * t
                ny = y + vy * t
            t = hypot(nx - mx, ny - my) / BULLET
        return nx, ny, t

    def _jink_period(self):
        f = self.flips
        if len(f) < 2:
            return 3.0
        gap = (f[-1] - f[0]) / (len(f) - 1)
        return clamp(gap, 0.2, 3.0)

    # ------------------------------------------------------------- выстрел
    def _gun(self, o, e, me):
        if e is None:
            return self._gun_blind(o, me), False
        self._score_predictions(o, e)
        d = hypot(e.x - me.x, e.y - me.y)
        model = self._best_model()
        mx0 = me.x + cos(me.turret) * MUZZLE
        my0 = me.y + sin(me.turret) * MUZZLE
        px, py, t_fly = self._predict(e, mx0, my0, model)

        # предсказанный курс корпуса врага на момент удара
        phull = e.hull + clamp(self.e_omega, -HULL_TURN, HULL_TURN) * min(t_fly, 0.7)

        if d < POINT_BLANK:
            err = o.aim_error(px, py)
            step = o.bullet_turn_rate * o.dt
            cmd = clamp(err / step, -1.0, 1.0)
            residual = fabs(err - cmd * step)
            return cmd, (me.ammo_ready and residual < 0.014)

        aim, band_lo, band_hi = self._pen_band(o, me, px, py, phull, d, t_fly)
        if aim is None:
            # пробития нет — держим башню на цели
            return o.aim_turret(px, py), False
        # целевой угол башни = направление выстрела: луч идёт из дула,
        # поэтому угол уточняем итерацией (дуло зависит от угла)
        a = atan2(aim[1] - me.y, aim[0] - me.x)
        for _ in range(2):
            mzx = me.x + cos(a) * MUZZLE
            mzy = me.y + sin(a) * MUZZLE
            a = atan2(aim[1] - mzy, aim[0] - mzx)
        err = wrap(a - me.turret)
        step = o.bullet_turn_rate * o.dt
        cmd = clamp(err / step * 1.0, -1.0, 1.0)
        residual = fabs(err - cmd * step)

        # допуск: половина пробивающей полосы и геометрия корпуса, минус разброс
        mzx = me.x + cos(a) * MUZZLE
        mzy = me.y + sin(a) * MUZZLE
        dd = hypot(aim[0] - mzx, aim[1] - mzy)
        tol_band = 0.55 * (band_hi - band_lo)
        tol_geom = atan2(13.5, dd) if dd > 40.0 else 0.3
        tol = min(tol_band, tol_geom) - SPREAD_RAD
        if tol < 0.0035:
            tol = 0.0035
        if not me.ammo_ready or residual > tol:
            return cmd, False
        if self.nav and (self.nav.los_blocked(mzx, mzy, aim[0], aim[1], pad=3.0)
                         or not self.nav.free(mzx, mzy)):
            return cmd, False

        # темповый гейт: насколько вероятен исход
        err_px = self.m_err[model] ** 0.5
        foe_empty = not e.ammo_ready
        err_ok = ERR_EMPTY if foe_empty else ERR_READY
        tf_ok = TF_EMPTY if foe_empty else TF_READY
        if e.hp <= FINISH_HP:
            err_ok += 16.0
            tf_ok += 0.18
        since_flip = (self.t - self.flips[-1]) if self.flips else 9.9
        if 0.12 < since_flip < 0.4:
            err_ok += 9.0        # цель только что сменила ход — окно стабильности
        elif since_flip < 0.07:
            err_ok -= 12.0       # переворот в процессе — модели врут
        # риск уклона: враг научился уходить с линии — требуем более близкий
        # выстрел, иначе его поперечный прыжок (½·a·t²) уводит корпус
        if self.dodge_n >= 3 and fabs(self.dodge_ema) > 26.0:
            err_px += 230.0 * t_fly * t_fly
        scale = clamp(0.72 + 0.62 * self.hit_ema, 0.8, 1.3)
        err_ok *= scale
        if t_fly > tf_ok or err_px > err_ok:
            return cmd, False
        # цель быстро вращается — за время полёта корпус уйдёт из-под удара
        if (degrees(fabs(self.e_omega)) * t_fly > 11.0
                and not foe_empty and e.hp > FINISH_HP):
            return cmd, False
        self._log_shot(o, me, a, px, py, e)
        self.fired_tick = o.tick
        return cmd, True

    def _pen_band(self, o, me, px, py, phull, d, t_fly):
        """Скан направлений через силуэт: самая широкая пробивающая полоса.

        Возвращает (точка_прицела, lo, hi) в абсолютных углах; None — пробития нет.
        """
        bearing = atan2(py - me.y, px - me.x)
        # запас на угловую неопределённость корпуса врага к моменту удара
        om_unc = 0.45 * fabs(self.e_omega) + 0.12
        margin = SPREAD + 1.6 + degrees(om_unc * min(t_fly, 0.7))
        # φ врага к моменту удара: если он у границы мёртвой зоны (30/40/140),
        # его манёвр может вытолкнуть удар в рикошет — добавляем запас
        phi = fabs(degrees(wrap(atan2(me.y - py, me.x - px) - phull)))
        if phi > 90.0:
            phi = 180.0 - phi
        bmin = min(fabs(phi - 30.0), fabs(phi - 40.0), fabs(phi - 55.0))
        if bmin < 12.0:
            margin += 4.5
        if margin > 14.0:
            margin = 14.0
        half_ang = atan2(20.0 + 7.0, max(d, 60.0))
        N = 11
        good = []
        for k in range(N):
            a = bearing - half_ang + 2.0 * half_ang * k / (N - 1)
            dx, dy = cos(a), sin(a)
            mzx = me.x + dx * MUZZLE
            mzy = me.y + dy * MUZZLE
            hit = ray_obb(mzx, mzy, dx, dy, px, py, 20.0, 14.0, phull)
            if hit is None:
                good.append(None)
                continue
            good.append(a if pen_ok(hit[2], hit[1], margin) else None)
        # самая широкая непрерывная полоса; середина ближе к центру
        mid_n = (N - 1) / 2.0
        best = None
        k = 0
        while k < N:
            if good[k] is None:
                k += 1
                continue
            j = k
            while j + 1 < N and good[j + 1] is not None:
                j += 1
            m = (k + j) // 2
            key = (j - k, -abs(m - mid_n))
            if best is None or key > best[0]:
                best = (key, k, j)
            k = j + 1
        if best is None:
            return None, 0.0, 0.0
        _, k, j = best
        a = good[(k + j) // 2]
        dx, dy = cos(a), sin(a)
        mzx = me.x + dx * MUZZLE
        mzy = me.y + dy * MUZZLE
        hit = ray_obb(mzx, mzy, dx, dy, px, py, 20.0, 14.0, phull)
        tt = hit[0] if hit is not None else d
        return (mzx + dx * tt, mzy + dy * tt), good[k], good[j]

    # ------------------------------------------------- обучение уклонению врага
    def _log_shot(self, o, me, a, cx, cy, e):
        """Записываем выстрел: линию, ЦЕНТРОВЫЙ прогноз (для учёта уклона)."""
        mx = me.x + cos(a) * MUZZLE
        my = me.y + sin(a) * MUZZLE
        dx, dy = cos(a), sin(a)
        dd = hypot(cx - mx, cy - my)
        due = o.tick + max(2, int(dd / BULLET * 60.0))
        self.shots_log.append((due, mx, my, dx, dy, cx, cy, e.hp))
        if len(self.shots_log) > 6:
            del self.shots_log[0]

    def _resolve_shots(self, o, e):
        """Что случилось с моим выстрелом: попал / враг увернулся / промах."""
        log = self.shots_log
        while log and log[0][0] <= o.tick:
            due, mx, my, dx, dy, cx, cy, hp0 = log.pop(0)
            if e is None or o.tick - due > 4:
                continue
            # боковое отклонение врага от линии выстрела против ЦЕНТРОВОГО
            # прогноза: это и есть его реакция уклона, кем бы он ни был
            rx = e.x - mx
            ry = e.y - my
            s_act = -rx * dy + ry * dx            # вдоль левой нормали
            s_pred = -(cx - mx) * dy + (cy - my) * dx
            dodge = s_act - s_pred
            if e.hp < hp0 - 0.5:
                # попали: враг не успел/не стал уклоняться
                self.hit_ema = self.hit_ema * 0.7 + 0.3
                sample = 0.0
            else:
                self.hit_ema = self.hit_ema * 0.7
                sample = clamp(dodge, -110.0, 110.0)
            if self.dodge_n < 2:
                self.dodge_ema = sample
                self.dodge_n += 1
            else:
                self.dodge_ema = self.dodge_ema * 0.55 + sample * 0.45
                self.dodge_n += 1

    # ------------------------------------------------------------- вслепую
    def _gun_blind(self, o, me):
        if self.seen and (self.t - self.et) < HUNT_FRESH:
            age = self.t - self.et
            px = self.ex + self.evx * age * 0.6
            py = self.ey + self.evy * age * 0.6
            return o.aim_turret(px, py)
        # башня по ходу движения: контакт спереди вероятнее
        return o.aim_turret(me.x + cos(me.hull) * 200.0,
                            me.y + sin(me.hull) * 200.0)

    # ------------------------------------------------------------- угроза
    def _threat(self, o, e, me):
        """Ловим выстрел врага и уводим себя с линии снаряда."""
        if e is not None:
            cd = e.cooldown
            jumped = self.prev_cd is not None and self.prev_cd < 0.45 and cd > 0.6
            blind = self.prev_cd is None and cd > 0.55
            if jumped or blind:
                # blind: враг выстрелил невидимым (из-за колонны) и показался
                # уже после — возраст снаряда оцениваем по его перезарядке
                a = e.turret
                age = 0.0 if jumped else (RELOAD - cd)
                self.threat = (e.x + cos(a) * MUZZLE, e.y + sin(a) * MUZZLE,
                               cos(a), sin(a), self.t - age)
            self.prev_cd = cd
            self.prev_enemy = (e.x, e.y, e.turret, e.ammo_ready, o.tick)
        else:
            self.prev_cd = None

        th = self.threat
        if th is None:
            return None
        bx, by, dx, dy, t0 = th
        age = self.t - t0
        if age > 1.6:
            self.threat = None
            return None
        bx += dx * BULLET * age
        by += dy * BULLET * age
        rx = bx - me.x
        ry = by - me.y
        vx = dx * BULLET - me.vx
        vy = dy * BULLET - me.vy
        vv = vx * vx + vy * vy
        if vv < 1.0:
            self.threat = None
            return None
        t_star = -(rx * vx + ry * vy) / vv
        if t_star <= 0.0:
            self.threat = None
            return None
        miss = hypot(rx + vx * t_star, ry + vy * t_star)
        # гистерезис: пока снаряд летит рядом, держим его активным
        lim = DODGE_MISS + 18.0 if self.threat_hot else DODGE_MISS
        if miss > lim:
            self.threat = None
            self.threat_hot = False
            return None
        # вбок от линии, в сторону где нет стены и куда уже несёт инерция
        perp = dx * (me.y - by) - dy * (me.x - bx)
        if perp > 2.0:
            side = 1.0
        elif perp < -2.0:
            side = -1.0
        else:
            lat = dx * me.vy - dy * me.vx
            side = 1.0 if lat >= 0.0 else -1.0
        ex, ey = -dy * side, dx * side
        if self.nav and self.nav.clearance(me.x + ex * DODGE_CLEAR,
                                           me.y + ey * DODGE_CLEAR) < 20.0:
            ex, ey = -ex, -ey
        # чуть добавим ухода вдоль линии «от снаряда»
        ux, uy = unit(ex - dx * 0.35, ey - dy * 0.35)
        self.threat_hot = True
        return (ux, uy, t_star, miss, dx, dy)

    # ------------------------------------------------------------- движение
    def _steer(self, o, me, ux, uy, hull_target, throttle):
        """Универсальный рулевой:желаемый ход (ux,uy), цель корпуса hull_target.

        Передачу выбираем по проекции желаемого хода на курс: вперёд, если
        нос смотрит куда надо, иначе задний ход (разворот корпуса дороже).
        """
        if hull_target is None:
            hull_target = atan2(uy, ux)
        # передача: по целевому курсу (корпус быстро его достигнет)
        c = ux * cos(hull_target) + uy * sin(hull_target)
        gear = 1.0 if c >= 0.0 else -1.0
        err = wrap(hull_target - me.hull)
        turn = clamp(err * 2.6, -1.0, 1.0)
        drive = gear * throttle
        return turn, drive

    def _engage(self, o, me, e):
        """Бой: ракурс рикошета + маятник + темп по перезарядке."""
        dx = e.x - me.x
        dy = e.y - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d = 1e-6
            dx, dy = 1.0, 0.0
        ux, uy = dx / d, dy / d
        bearing = atan2(dy, dx)
        me_ready = me.ammo_ready
        # враг считается заряженным и когда его патрон вот-вот поспеет:
        # доворот на ракурс занимает ~0.4 с, начинать надо заранее
        foe_ready = e.ammo_ready or e.cooldown < 0.32
        t = self.t
        los_clear = not (self.nav and self.nav.los_blocked(me.x, me.y, e.x, e.y))

        # --- смена стороны ракурса: только когда безопасно ----------------
        # (при перевороте корпус проходит через φ=0 — лоб под пробитие)
        if t > self.side_until:
            safe = (not foe_ready) or d > 520.0 or not los_clear
            forced = t > self.side_until + 8.0 and (not foe_ready or d > 340.0)
            if safe or forced:
                self.side = -self.side
                self.side_until = t + SIDE_MIN + self._rand() * (SIDE_MAX - SIDE_MIN)
            else:
                self.side_until = t + 0.35      # проверим позже

        # --- целевая дистанция по темпу ------------------------------------
        if not foe_ready:
            want_d = PRESS_RANGE if me_ready else STANDOFF
        elif not me_ready:
            want_d = HIDE_RANGE
        else:
            want_d = STANDOFF
        if e.hp <= FINISH_HP and me_ready:
            want_d = PRESS_MIN + 40.0
        if me.hp <= DMG and foe_ready and e.hp > FINISH_HP:
            want_d = HIDE_RANGE + 90.0       # нас убьёт одна пуля — дальше
        pressing = (not foe_ready) and me_ready

        # --- укрытие, когда мы пусты, а враг заряжен ------------------------
        if not me_ready and foe_ready and d < HIDE_RANGE and t > self._cover_until:
            spot = self._cover_spot(o, me, e, d)
            if spot is not None:
                self._cover_goal = spot
                self._cover_until = t + 1.0
        if t < self._cover_until and self._cover_goal is not None:
            gx, gy = self._cover_goal
            u2 = unit(gx - me.x, gy - me.y)
            if u2 != (0.0, 0.0):
                hull_target = (bearing + self.bear_omega * BEAR_LEAD
                               + self.side * ANGLE_LOCK)
                return self._angled_drive(o, me, hull_target, u2)

        # --- PRESS без линии огня: добираемся через A* свободным курсом -----
        if pressing and not los_clear and self.nav is not None:
            gx = e.x - ux * want_d
            gy = e.y - uy * want_d
            wx, wy = self.nav.route_to(me.x, me.y, gx, gy)
            u2 = unit(wx - me.x, wy - me.y)
            if u2 == (0.0, 0.0):
                u2 = (ux, uy)
            turn, drive = self._steer(o, me, u2[0], u2[1], None, 1.0)
            return turn, drive

        # --- маятник на косой: ракурс держим, газ по расписанию -------------
        if t > self.gear_until:
            u = self._rand()
            self.gear_until = t + GEAR_MIN + (GEAR_MAX - GEAR_MIN) * u
            if d > want_d + 45.0:
                self.gear = 1
            elif d < want_d - 45.0:
                self.gear = -1
            elif u > 0.5:
                self.gear = -self.gear
        if pressing and d > want_d:
            self.gear = 1                    # враг пуст — наваливаемся
        jit = ANGLE_JIT * sin(t * 2.1 + self.side)
        ang = ANGLE_LOCK + jit
        if d < 170.0:
            ang += 0.045      # ~2.6°: ближний лаг отслеживания съедает ракурс
        hull_target = bearing + self.bear_omega * BEAR_LEAD + self.side * ang
        # желаемая скорость — вдоль косой выбранной передачей
        vhx, vhy = cos(hull_target), sin(hull_target)
        u2 = (vhx * self.gear, vhy * self.gear)
        # стены: если ход упирается — пробуем другую передачу, затем доворот
        u2 = self._wall_fix(o, me, u2, hull_target)
        if u2 is None:
            # зажаты: крутимся на месте в сторону, где просторнее
            spin = self._open_spin(o, me)
            return spin, 0.2
        err = wrap(hull_target - me.hull)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        drive = self.gear * 0.96
        return turn, drive

    def _angled_drive(self, o, me, hull_target, u2):
        """Ход к точке u2, держа корпус по hull_target (ракурс не ломая)."""
        u2 = self._wall_fix(o, me, u2, hull_target)
        if u2 is None:
            return self._open_spin(o, me), 0.2
        turn = clamp(wrap(hull_target - me.hull) * TRACK_GAIN, -1.0, 1.0)
        hx, hy = cos(hull_target), sin(hull_target)
        gear = 1.0 if (u2[0] * hx + u2[1] * hy) >= 0.0 else -1.0
        return turn, gear * 0.98

    def _wall_fix(self, o, me, u2, hull_target):
        """Если желаемый ход упирается в стену — другая передача или None."""
        if self.nav is None:
            return u2
        px = me.x + u2[0] * 58.0
        py = me.y + u2[1] * 58.0
        if self.nav.free(px, py) and self.nav.clearance(px, py) >= 20.0:
            return u2
        # другая передача
        alt = (-u2[0], -u2[1])
        px = me.x + alt[0] * 58.0
        py = me.y + alt[1] * 58.0
        if self.nav.free(px, py) and self.nav.clearance(px, py) >= 20.0:
            self.gear = -self.gear
            return alt
        return None

    def _open_spin(self, o, me):
        """Крутиться в сторону, где просторнее (зажат в углу)."""
        best_a = me.hull
        best_c = -1.0
        for k in range(8):
            a = me.hull + k * (TAU / 8)
            c = self.nav.clearance(me.x + cos(a) * 46.0,
                                   me.y + sin(a) * 46.0) if self.nav else 50.0
            if c > best_c:
                best_c = c
                best_a = a
        return clamp(wrap(best_a - me.hull) * 2.0, -1.0, 1.0)

    def _dodge_move(self, o, me, e, threat):
        """Уход от летящего снаряда: борт с линии + ракурс к ЛИНии снаряда.

        Снаряд летит по прямой, зафиксированной в момент выстрела: пока он
        в пути, корпус держим под 35° именно к его линии (а не к текущему
        азимуту врага — тот успевает съехать вбок). Если уход не успеет,
        удар придёт в лоб под 35° и срикошетит.
        """
        ux, uy, t_star, miss, bdx, bdy = threat
        # направление ОТКУДА летит снаряд
        base = atan2(-bdy, -bdx) + self.side * ANGLE_LOCK
        hx, hy = cos(base), sin(base)
        dot = ux * hx + uy * hy
        if miss < 13.0 and t_star > 0.28 and fabs(dot) < 0.45:
            # смертельная линия и передача не даёт бокового хода — полный разворот
            err = wrap(atan2(uy, ux) - me.hull)
            return clamp(err * 2.6, -1.0, 1.0), 1.0
        err = wrap(base - me.hull)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        gear = 1.0 if dot >= 0.0 else -1.0
        # стена вбок от ухода важнее ракурса
        if self.nav is not None:
            px = me.x + ux * DODGE_CLEAR
            py = me.y + uy * DODGE_CLEAR
            if self.nav.clearance(px, py) < 18.0:
                err = wrap(atan2(uy, ux) - me.hull)
                return clamp(err * 2.6, -1.0, 1.0), 1.0
        return turn, gear * 1.0

    def _los_broken(self, me, e):
        return self.nav is not None and self.nav.los_blocked(me.x, me.y, e.x, e.y)

    def _cover_spot(self, o, me, e, d):
        """Точка, где линия огня врага нас не видит."""
        best = None
        nav = self.nav
        if nav is None:
            return None
        for k in range(14):
            ang = k * (TAU / 14) + 0.2
            for dist in (90.0, 170.0, 260.0):
                px = me.x + cos(ang) * dist
                py = me.y + sin(ang) * dist
                if not nav.free(px, py) or nav.clearance(px, py) < 26.0:
                    continue
                if not nav.los_blocked(px, py, e.x, e.y):
                    continue
                score = -dist + nav.clearance(px, py) * 0.5
                if best is None or score > best[0]:
                    best = (score, px, py)
        return None if best is None else (best[1], best[2])

    # ------------------------------------------------------------- поиск
    def _hunt(self, o, me):
        t = self.t
        gx = gy = None
        if self.seen and (t - self.et) < HUNT_FRESH:
            age = t - self.et
            gx = self.ex + self.evx * age * 0.7
            gy = self.ey + self.evy * age * 0.7
            if hypot(gx - me.x, gy - me.y) < 60.0:
                gx = gy = None
        if gx is None:
            gx, gy = self._patrol_goal(o, me)
        if self.nav is None:
            u = unit(gx - me.x, gy - me.y)
            return clamp(wrap(atan2(u[1], u[0]) - me.hull) * 2.6, -1.0, 1.0), 0.9
        wx, wy = self.nav.route_to(me.x, me.y, gx, gy)
        u = unit(wx - me.x, wy - me.y)
        if u == (0.0, 0.0):
            u = (cos(me.hull), sin(me.hull))
        want = atan2(u[1], u[0])
        # свежий след: противник мог нырнуть за колонну и стрелять вслепую —
        # держим ракурс рикошета к последнему азимуту, а не к маршруту
        fresh = self.seen and (t - self.et) < 2.6
        if fresh:
            last_bear = atan2(self.ey - me.y, self.ex - me.x)
            hull_target = last_bear + self.side * ANGLE_LOCK
            err = wrap(hull_target - me.hull)
            turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
            # передача — по ходу маршрута относительно корпуса
            gear = 1.0 if (u[0] * cos(hull_target) + u[1] * sin(hull_target)) >= 0.0 else -1.0
            return turn, gear * 0.95
        # охота: всегда носом вперёд — задний ход на поиске только жжёт время
        err = wrap(want - me.hull)
        turn = clamp(err * 2.4, -1.0, 1.0)
        drive = 0.95 if fabs(err) < 2.1 else -0.45
        return turn, drive

    def _patrol_goal(self, o, me):
        nav = self.nav
        spawns = o.map.spawns if o.map is not None else None
        if nav is None or not nav.patrol:
            sp = spawns[1 - o.tank] if spawns else None
            return (sp.x, sp.y) if sp else (me.x + cos(me.hull) * 200.0,
                                            me.y + sin(me.hull) * 200.0)
        # первые секунды — straight к вражескому спавну: большинство
        # противников тоже идёт навстречу, встречаемся быстрее
        if t_early := (self.t < 6.0 and spawns and not self.seen):
            sp = spawns[1 - o.tank]
            return (sp.x, sp.y)
        pts = nav.patrol
        n = len(pts)
        if self.patrol_order is None:
            # входим в цепь с ближайшего к нам звена
            self.patrol_k = min(range(n),
                                key=lambda i: hypot(pts[i][0] - me.x, pts[i][1] - me.y))
            self.patrol_order = 1
        g = pts[self.patrol_k % n]
        if hypot(g[0] - me.x, g[1] - me.y) < 74.0:
            self.patrol_k += 1
            g = pts[self.patrol_k % n]
        return g

    # ------------------------------------------------------------- застревание
    def _watch_stuck(self, o, me):
        self.mile += hypot(me.x - self.px, me.y - self.py)
        self.px, self.py = me.x, me.y
        self.stuck_t += o.dt
        if self.stuck_t >= STUCK_WINDOW:
            stalled = self.mile < STUCK_MILES and fabs(self.cmd_drive) > 0.3
            if stalled and self.t >= self.escape_until:
                self.escape_side = 1.0 if self._rand() > 0.5 else -1.0
                self.escape_until = self.t + 0.65
                self.gear = 1
                self.gear_until = self.t + 0.7
                if self.nav:
                    self.nav.path = None
                self.patrol_order = None
            self.mile = 0.0
            self.stuck_t = 0.0


program = Brain()
