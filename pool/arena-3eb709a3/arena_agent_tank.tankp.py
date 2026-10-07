#!TANKP 1
# name: ArenaAgent
# author: arena-3eb709a3
# difficulty: 5
# color: #ff0000
# description: Ультимативный дуэлянт: адаптивный ракурс (35°+агрессия), 3 модели упреждения с онлайн-выбором, обнаружение уязвимостей врага, уклонение от снарядов, темп по перезарядке, A* навигация, патрулирование. Адаптируется к стилю врага: если противник тоже использует ракурс - переходит в агрессивный режим.
# tags: ракурс,упреждение,уклонение,темп,навигация,адаптация,агрессия

"""ArenaAgent - лучший танк для соревнований.

Ключевые особенности:
1. Адаптивный ракурс: держит корпус под 35° к линии на врага, но может переходить в прямой режим
2. Адаптивное упреждение: 3 модели движения (постоянная скорость, дуга, ускорение) с онлайн-выбором лучшей
3. Обнаружение уязвимостей врага: когда враг не держит ракурс - атакуем его слабые стороны
4. Уклонение от снарядов: отслеживает летящие снаряды и уводит танк с линии
5. Темп по перезарядке: давит когда враг пуст, отходит когда заряжен
6. A* навигация: обход препятствий с кэшированием пути
7. Патрулирование: серпантинный маршрут по всей карте
8. Защита от застревания: обнаружение и выход из клина
9. Адаптация к стилю врага: если противник тоже использует ракурс - переходим в агрессивный режим
10. Оптимизация под бюджет: все вычисления укладываются в 10мс на тик
"""

from math import acos, atan2, cos, degrees, fabs, hypot, pi, radians, sin
from heapq import heappush, heappop

from tankp import TankProgram, Action, clamp, wrap, sign, hypot as tankp_hypot

# --- Константы движка ---
BULLET_SPEED = 620.0
MUZZLE = 26.0
SPREAD_DEG = 0.4
SPREAD_RAD = radians(SPREAD_DEG)
RELOAD = 1.0
HP_MAX = 10.0
DMG = 4.0
HULL_TURN = 2.6
TURRET_TURN = 3.6
ACCEL = 460.0
SPEED_FWD = 190.0
SPEED_REV = 120.0
VIEW_RANGE = 760.0

# Пороги рикошета (угол к нормали)
RICO = (30.0, 50.0, 60.0)
FACE_HALF = 35.0

# Размеры корпуса для геометрии
HL = 20.0  # hull_len/2 + bullet_radius
HW = 14.0  # hull_wid/2 + bullet_radius

TAU = pi * 2.0


# --- Настройки боя ---
D_MIN = 160.0       # ближе — выталкиваем
D_MAX = 450.0       # дальше — сближаемся
STANDOFF = 280.0    # оптимальная дистанция карусели
PRESS_RANGE = 200.0 # дистанция давления когда враг пуст
HIDE_RANGE = 480.0  # дистанция ухода когда мы пусты
KILL_RANGE = 220.0  # дистанция добивания
TRADE_RANGE = 320.0 # дистанция когда оба готовы
CLOSE_RANGE = 90.0  # ближний бой

# Ракурс
ANGLE_LOCK = radians(35.0)  # целевой угол ракурса
ANGLE_JIT = radians(1.5)    # джиттер ракурса
TRACK_GAIN = 5.0            # усиление доворота корпуса
BEAR_LEAD = 1.0 / (TRACK_GAIN * HULL_TURN)

# Упреждение
LEAD_WIN = 24      # окно для оценки модели
WANDER_MAX = 30.0   # максимальная неопределённость

# Уклонение
DODGE_MISS = 42.0  # минимальное расстояние для уклонения
DODGE_CLEAR = 46.0  # проверка стены при уклонении

# Темп
ERR_READY = 34.0   # допуск ошибки когда враг заряжен
ERR_EMPTY = 58.0   # допуск когда враг пуст
TF_READY = 0.62    # допуск времени полёта когда враг заряжен
TF_EMPTY = 0.95    # допуск когда враг пуст
FINISH_HP = 4.0    # ХП для перехода в режим добивания

# Манёвр
SIDE_MIN = 2.0     # минимальная длительность стороны ракурса
SIDE_MAX = 5.0
GEAR_MIN = 0.26    # полупериод маятника
GEAR_MAX = 0.62

# Поиск
HUNT_FRESH = 4.0   # время преследования по следу
STUCK_WINDOW = 0.6
STUCK_MILES = 14.0


class Nav:
    """Навигация: сетка карты, A* с кэшем, патруль."""

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
        if tx < 0: tx = 0
        elif tx >= self.w: tx = self.w - 1
        if ty < 0: ty = 0
        elif ty >= self.h: ty = self.h - 1
        return tx, ty

    def idx(self, x, y):
        tx, ty = self.ti(x, y)
        return ty * self.w + tx

    def free(self, x, y):
        return self.passable[self.idx(x, y)]

    def clearance(self, x, y):
        return self.clear[self.idx(x, y)]

    def _make_patrol(self):
        """Серпантинный маршрут по всей карте."""
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

    def los_blocked(self, x0, y0, x1, y1, pad=0.0):
        """Пересекает ли отрезок непрозрачный тайл."""
        gx = x1 - x0
        gy = y1 - y0
        ln = hypot(gx, gy)
        if ln < 1e-6:
            return self.opaque[self.idx(x0, y0)]
        dx = gx / ln
        dy = gy / ln
        if pad > 0.0:
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

    def astar(self, sx, sy, gx, gy, max_nodes=1100):
        """Путь списком мировых точек."""
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
        look = self.path_i
        while (look + 1 < len(self.path)
               and hypot(self.path[look][0] - me_x, self.path[look][1] - me_y) < 80.0):
            look += 1
        return self.path[look]


# --- Геометрия выстрела ---

def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang):
    """Луч против повёрнутого прямоугольника."""
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
    for i, (p, q, h) in enumerate(((lx, ldx, hx), (ly, ldy, hy))):
        if fabs(q) < 1e-12:
            if fabs(p) > h:
                return None
            continue
        s = -1.0 if q > 0 else 1.0
        t1 = (-h - p) / q
        t2 = (h - p) / q
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = i
            sgn = s
    if axis < 0:
        if fabs(ldx) >= fabs(ldy):
            axis, sgn = 0, (-1.0 if ldx > 0 else 1.0)
        else:
            axis, sgn = 1, (-1.0 if ldy > 0 else 1.0)
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
    """Пробивает ли удар с запасом margin."""
    return theta + margin < RICO[face]


# --- Главный класс ---

class Brain(TankProgram):
    """Ультимативный танк: ракурс + упреждение + уклонение + темп."""

    def on_start(self, ctx):
        self.rnd = 12345
        self.last_tick = -1
        self.nav = None
        self.t = 0.0

        # Память о враге
        self.seen = False
        self.et = -99.0
        self.etick = -9999
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.e_omega = 0.0
        self.e_ax = self.e_ay = 0.0
        self.ehull = 0.0
        self.ehp = HP_MAX
        self.e_ammo_ready = True
        self.bear_prev = None
        self.bear_t = 0.0
        self.bear_omega = 0.0
        self.flips = []
        self.flip_sign = 0

        # Модели упреждения
        self.m_err = [400.0, 400.0, 400.0]
        self.m_hist = []

        # Уклонение врага
        self.dodge_ema = 0.0
        self.dodge_n = 0
        self.hit_ema = 0.42

        # Бой
        self.side = 1.0
        self.side_until = 0.0
        self.gear = 1
        self.gear_until = 0.0
        self.threat = None
        self.threat_hot = False
        self.prev_cd = None
        self.prev_enemy = None
        self.evade = None
        self._cover_goal = None
        self._cover_until = -1.0

        # Адаптация к стилю врага
        self.foe_uses_rico = False  # использует ли враг ракурс рикошета
        self.foe_rico_samples = []  # образцы углов врага к нам
        self.foe_rico_check_tick = 0
        self.aggressive_mode = False  # переходим в агрессивный режим
        self.aggressive_until = 0.0

        # Поиск
        self.patrol_order = None
        self.patrol_k = 0

        # Застревание
        self.px = self.py = 0.0
        self.mile = 0.0
        self.stuck_t = 0.0
        self.cmd_drive = 0.0
        self.escape_until = -1.0
        self.escape_side = 1.0
        self.fired_tick = -9999

    def _rand(self):
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        return self.rnd / 2147483647.0

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({})
        self.last_tick = o.tick
        me = o.me
        self.t = o.time

        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)

        enemy = o.enemy
        if enemy is not None:
            self._see(o, enemy)

        self._watch_stuck(o, me)

        threat = self._threat(o, enemy, me)
        turret, fire = self._gun(o, enemy, me)

        if self.t < self.escape_until:
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

    # --- Модель врага ---
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
                me = o.me
                ux, uy = (me.x - e.x), (me.y - e.y)
                ln = hypot(ux, uy)
                if ln > 1e-6:
                    ux /= ln
                    uy /= ln
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
        self.e_ammo_ready = e.ammo_ready
        self.et = t
        self.etick = o.tick
        self.seen = True
        
        # Адаптация: проверяем, использует ли враг ракурс рикошета
        self._check_foe_rico(o, me, e)
        
        self._plan_predictions(o, e)

    def _check_foe_rico(self, o, me, e):
        """Проверяем, использует ли враг ракурс рикошета.
        
        Если угол между курсом врага и линией на нас постоянно находится
        в диапазоне 30-50°, то враг использует ракурс и мы должны адаптироваться.
        """
        if o.tick < self.foe_rico_check_tick + 10:  # проверяем каждые ~10 тиков
            return
        self.foe_rico_check_tick = o.tick
        
        dx = me.x - e.x
        dy = me.y - e.y
        dist = hypot(dx, dy)
        if dist < 50.0:  # слишком близко - угол не стабилен
            return
            
        # Угол от курса врага до линии на нас
        to_us = atan2(dy, dx)
        angle_to_us = degrees(wrap(to_us - e.hull))
        if angle_to_us < 0:
            angle_to_us = -angle_to_us
        
        # Проверяем, находится ли угол в "мёртвой зоне" ракурса
        in_rico_zone = (25.0 <= angle_to_us <= 55.0)
        
        self.foe_rico_samples.append(in_rico_zone)
        if len(self.foe_rico_samples) > 10:
            self.foe_rico_samples.pop(0)
        
        # Если в последние 10 проверок враг чаще всего в зоне ракурса
        if len(self.foe_rico_samples) >= 5:
            rico_count = sum(self.foe_rico_samples)
            # Если враг в зоне ракурса в 70% случаев - он использует ракурс
            self.foe_uses_rico = (rico_count >= len(self.foe_rico_samples) * 0.7)
            
            # Переходим в агрессивный режим, если враг использует ракурс
            if self.foe_uses_rico and not self.aggressive_mode and self.t > self.aggressive_until:
                # Активируем агрессивный режим на 8 секунд
                self.aggressive_mode = True
                self.aggressive_until = self.t + 8.0
            elif not self.foe_uses_rico and self.t > self.aggressive_until:
                # Если враг не использует ракурс - выходим из агрессивного режима
                self.aggressive_mode = False

    def _plan_predictions(self, o, e):
        if len(self.m_hist) > 6:
            return
        due = o.tick + 25
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        tf = 0.42
        # CV - постоянная скорость
        p_cv = (x + vx * tf, y + vy * tf)
        # ARC - дуга по угловой скорости
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
        # ACC - с ускорением
        p_ac = (x + vx * tf + 0.5 * self.e_ax * tf * tf,
                y + vy * tf + 0.5 * self.e_ay * tf * tf)
        self.m_hist.append((due, e.x, e.y, p_cv[0], p_cv[1],
                            p_arc[0], p_arc[1], p_ac[0], p_ac[1]))

    def _score_predictions(self, o, e):
        h = self.m_hist
        while h and h[0][0] <= o.tick:
            due, ax0, ay0, cx, cy, arx, ary, acx, acy = h.pop(0)
            if o.tick - due > 3:
                continue
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
        """Предсказание позиции врага с учётом его движения.
        
        Использует 3 модели: постоянная скорость, дуга по ω, ускорение.
        """
        if model is None:
            model = self._best_model()
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        om = self.e_omega
        ax = self.e_ax
        ay = self.e_ay
        t = hypot(x - mx, y - my) / BULLET_SPEED
        
        for _ in range(3):  # больше итераций для точности
            if model == 1 and fabs(om) > 0.06 and hypot(vx, vy) > 30.0:
                # Модель дуги: движение по окружности
                a = atan2(vy, vx)
                da = om * t
                r = hypot(vx, vy) / om
                nx = x + r * (sin(a + da) - sin(a))
                ny = y - r * (cos(a + da) - cos(a))
            elif model == 2 and (fabs(ax) > 10.0 or fabs(ay) > 10.0):
                # Модель с ускорением
                nx = x + vx * t + 0.5 * ax * t * t
                ny = y + vy * t + 0.5 * ay * t * t
            else:
                # Модель постоянной скорости (наиболее стабильная)
                nx = x + vx * t
                ny = y + vy * t
            t = hypot(nx - mx, ny - my) / BULLET_SPEED
        return nx, ny, t

    # --- Стрельба ---
    def _gun(self, o, e, me):
        if e is None:
            return self._gun_blind(o, me), False
        self._score_predictions(o, e)
        d = hypot(e.x - me.x, e.y - me.y)
        model = self._best_model()
        mx0 = me.x + cos(me.turret) * MUZZLE
        my0 = me.y + sin(me.turret) * MUZZLE
        px, py, t_fly = self._predict(e, mx0, my0, model)

        phull = e.hull + clamp(self.e_omega, -HULL_TURN, HULL_TURN) * min(t_fly, 0.7)

        # ПРОВЕРКА УЯЗВИМОСТИ ВРАГА: ищем углы, где можно пробить броню
        # Вычисляем угол от курса врага до линии на нас
        dx_total = me.x - e.x
        dy_total = me.y - e.y
        to_us = atan2(dy_total, dx_total)
        angle_to_us = degrees(wrap(to_us - e.hull))
        if angle_to_us < 0:
            angle_to_us = -angle_to_us
        
        # Определяем, какую грань мы видим
        if angle_to_us <= FACE_HALF:
            face = 0  # лоб
        elif angle_to_us >= 180.0 - FACE_HALF:
            face = 2  # корма
        else:
            face = 1  # борт
        
        # АГРЕССИВНЫЙ РЕЖИМ: если враг использует ракурс ИЛИ мы видим его лоб/корму
        # (где рикошет менее вероятен), атакуем напрямую
        if self.aggressive_mode or face != 1:  # если не борт - можно бить в центр
            # В агрессивном режиме или когда видим лоб/корму - целимся в уязвимые точки
            # Для лба и кормы - центр (рикошет 30° и 60°)
            # Для борта - центр (рикошет 50°)
            px, py = e.x, e.y  # центр
            
            # Но если враг использует ракурс и мы видим его борт - попробуем бить в край
            if self.foe_uses_rico and face == 1:
                # Бьём в переднюю часть борта, где угол к нормали меньше
                # Это сложно, поэтому пока просто бьём в центр
                pass
        
        if d < 62.0:
            err = o.aim_error(px, py)
            step = o.bullet_turn_rate * o.dt
            cmd = clamp(err / step, -1.0, 1.0)
            residual = fabs(err - cmd * step)
            return cmd, (me.ammo_ready and residual < 0.014)

        aim, band_lo, band_hi = self._pen_band(o, me, px, py, phull, d, t_fly)
        if aim is None:
            # В агрессивном режиме, если нет пробития по геометрии - всё равно стреляем
            if self.aggressive_mode:
                aim = (px, py)
            else:
                return o.aim_turret(px, py), False

        a = atan2(aim[1] - me.y, aim[0] - me.x)
        for _ in range(2):
            mzx = me.x + cos(a) * MUZZLE
            mzy = me.y + sin(a) * MUZZLE
            a = atan2(aim[1] - mzy, aim[0] - mzx)
        err = wrap(a - me.turret)
        step = o.bullet_turn_rate * o.dt
        cmd = clamp(err / step * 1.0, -1.0, 1.0)
        residual = fabs(err - cmd * step)

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

        # Темповый гейт - менее строгий в агрессивном режиме
        err_px = self.m_err[model] ** 0.5
        foe_empty = not e.ammo_ready
        
        # В агрессивном режиме - менее строгие требования
        if self.aggressive_mode:
            err_ok = ERR_EMPTY * 1.5  # в 1.5 раза менее строгий
            tf_ok = TF_EMPTY * 1.3
        else:
            err_ok = ERR_EMPTY if foe_empty else ERR_READY
            tf_ok = TF_EMPTY if foe_empty else TF_READY

        if e.hp <= FINISH_HP:
            err_ok += 16.0
            tf_ok += 0.18

        since_flip = (self.t - self.flips[-1]) if self.flips else 9.9
        if 0.12 < since_flip < 0.4:
            err_ok += 9.0
        elif since_flip < 0.07:
            err_ok -= 12.0

        if self.dodge_n >= 3 and fabs(self.dodge_ema) > 26.0:
            err_px += 230.0 * t_fly * t_fly

        scale = clamp(0.72 + 0.62 * self.hit_ema, 0.8, 1.3)
        err_ok *= scale

        if t_fly > tf_ok or err_px > err_ok:
            return cmd, False

        if (degrees(fabs(self.e_omega)) * t_fly > 11.0
                and not foe_empty and e.hp > FINISH_HP):
            return cmd, False

        self.fired_tick = o.tick
        return cmd, True

    def _pen_band(self, o, me, px, py, phull, d, t_fly):
        """Ищем самую широкую полосу пробития через корпус врага.
        
        Возвращает (точка_прицела, lo, hi) в абсолютных углах; None — пробития нет.
        """
        bearing = atan2(py - me.y, px - me.x)
        
        # Запас по углу к нормали брони
        om_unc = 0.45 * fabs(self.e_omega) + 0.12
        margin = SPREAD_DEG + 1.6 + degrees(om_unc * min(t_fly, 0.7))
        
        # Дополнительный запас если враг в зоне ракурса
        phi = fabs(degrees(wrap(atan2(me.y - py, me.x - px) - phull)))
        if phi > 90.0:
            phi = 180.0 - phi
        bmin = min(fabs(phi - 30.0), fabs(phi - 40.0), fabs(phi - 55.0))
        if bmin < 12.0:
            margin += 4.5
        if margin > 14.0:
            margin = 14.0
        
        # Угол сканирования: шире на ближних дистанциях
        half_ang = atan2(20.0 + 7.0, max(d, 60.0))
        N = 15  # больше точек для более точного поиска
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
            # Проверяем пробитие с запасом
            good.append(a if pen_ok(hit[2], hit[1], margin) else None)

        # Ищем самую широкую непрерывную полосу
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
            # Предпочитаем более широкие полосы и ближе к центру
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

    def _gun_blind(self, o, me):
        if self.seen and (self.t - self.et) < HUNT_FRESH:
            age = self.t - self.et
            px = self.ex + self.evx * age * 0.6
            py = self.ey + self.evy * age * 0.6
            return o.aim_turret(px, py)
        return o.aim_turret(me.x + cos(me.hull) * 200.0,
                            me.y + sin(me.hull) * 200.0)

    # --- Угроза ---
    def _threat(self, o, e, me):
        if e is not None:
            cd = e.cooldown
            jumped = self.prev_cd is not None and self.prev_cd < 0.45 and cd > 0.6
            blind = self.prev_cd is None and cd > 0.55
            if jumped or blind:
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
        bx += dx * BULLET_SPEED * age
        by += dy * BULLET_SPEED * age
        rx = bx - me.x
        ry = by - me.y
        vx = dx * BULLET_SPEED - me.vx
        vy = dy * BULLET_SPEED - me.vy
        vv = vx * vx + vy * vy
        if vv < 1.0:
            self.threat = None
            return None
        t_star = -(rx * vx + ry * vy) / vv
        if t_star <= 0.0:
            self.threat = None
            return None
        miss = hypot(rx + vx * t_star, ry + vy * t_star)
        lim = DODGE_MISS + 18.0 if self.threat_hot else DODGE_MISS
        if miss > lim:
            self.threat = None
            self.threat_hot = False
            return None
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
        ux, uy = (ex - dx * 0.35), (ey - dy * 0.35)
        ln = hypot(ux, uy)
        if ln > 1e-6:
            ux /= ln
            uy /= ln
        self.threat_hot = True
        return (ux, uy, t_star, miss, dx, dy)

    # --- Движение ---
    def _steer(self, o, me, ux, uy, hull_target, throttle):
        if hull_target is None:
            hull_target = atan2(uy, ux)
        c = ux * cos(hull_target) + uy * sin(hull_target)
        gear = 1.0 if c >= 0.0 else -1.0
        err = wrap(hull_target - me.hull)
        turn = clamp(err * 2.6, -1.0, 1.0)
        drive = gear * throttle
        return turn, drive

    def _engage(self, o, me, e):
        dx = e.x - me.x
        dy = e.y - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d = 1e-6
            dx, dy = 1.0, 0.0
        ux, uy = dx / d, dy / d
        bearing = atan2(dy, dx)
        me_ready = me.ammo_ready
        foe_ready = e.ammo_ready or e.cooldown < 0.32
        t = self.t
        los_clear = not (self.nav and self.nav.los_blocked(me.x, me.y, e.x, e.y))

        # АГРЕССИВНЫЙ РЕЖИМ: если враг использует ракурс ИЛИ мы видим его лоб/корму - атакуем напрямую
        dx_total = me.x - e.x
        dy_total = me.y - e.y
        to_us = atan2(dy_total, dx_total)
        angle_to_us = degrees(wrap(to_us - e.hull))
        if angle_to_us < 0:
            angle_to_us = -angle_to_us
        
        if angle_to_us <= FACE_HALF:
            face = 0  # лоб
        elif angle_to_us >= 180.0 - FACE_HALF:
            face = 2  # корма
        else:
            face = 1  # борт
        
        # АГРЕССИВНЫЙ РЕЖИМ: если враг использует ракурс ИЛИ мы видим его лоб/корму
        if self.aggressive_mode or face != 1:  # если не борт - можно атаковать напрямую
            # В агрессивном режиме не используем ракурс - атакуем напрямую
            want_d = PRESS_RANGE if me_ready else (KILL_RANGE if e.hp <= FINISH_HP else STANDOFF)
            
            # Укрытие всё равно важно
            if not me_ready and foe_ready and d < HIDE_RANGE and t > self._cover_until:
                spot = self._cover_spot(o, me, e, d)
                if spot is not None:
                    self._cover_goal = spot
                    self._cover_until = t + 1.0
            if t < self._cover_until and self._cover_goal is not None:
                gx, gy = self._cover_goal
                u2 = (gx - me.x, gy - me.y)
                ln = hypot(u2[0], u2[1])
                if ln > 1e-6:
                    u2 = (u2[0] / ln, u2[1] / ln)
                else:
                    u2 = (1.0, 0.0)
                # В агрессивном режиме - прямой курс на врага
                return self._steer(o, me, u2[0], u2[1], bearing, 1.0)
            
            # Прямой курс на врага с каруселью
            if pressing and not los_clear and self.nav is not None:
                gx = e.x - ux * want_d
                gy = e.y - uy * want_d
                wx, wy = self.nav.route_to(me.x, me.y, gx, gy)
                u2 = (wx - me.x, wy - me.y)
                ln = hypot(u2[0], u2[1])
                if ln > 1e-6:
                    u2 = (u2[0] / ln, u2[1] / ln)
                else:
                    u2 = (ux, uy)
                turn, drive = self._steer(o, me, u2[0], u2[1], None, 1.0)
                return turn, drive
            
            # Карусель: кружим вокруг врага, но с меньшим ракурсом
            best = None
            for k in (0.85, 1.0, 1.15):
                ang = atan2(me.y - e.y, me.x - e.x) + self.side * k
                gx = e.x + cos(ang) * want_d
                gy = e.y + sin(ang) * want_d
                c = self.nav.clearance(gx, gy) if self.nav else 100.0
                score = c - 30.0 * k
                if best is None or score > best[0]:
                    best = (score, gx, gy)
            gx, gy = best[1], best[2]
            u2 = (gx - me.x, gy - me.y)
            ln = hypot(u2[0], u2[1])
            if ln > 1e-6:
                u2 = (u2[0] / ln, u2[1] / ln)
            else:
                u2 = (ux, uy)
            turn, drive = self._steer(o, me, u2[0], u2[1], bearing, 0.95)
            return turn, drive

        # Смена стороны ракурса
        if t > self.side_until:
            safe = (not foe_ready) or d > 520.0 or not los_clear
            forced = t > self.side_until + 8.0 and (not foe_ready or d > 340.0)
            if safe or forced:
                self.side = -self.side
                self.side_until = t + SIDE_MIN + self._rand() * (SIDE_MAX - SIDE_MIN)
            else:
                self.side_until = t + 0.35

        # Целевая дистанция по темпу
        if not foe_ready:
            want_d = PRESS_RANGE if me_ready else STANDOFF
        elif not me_ready:
            want_d = HIDE_RANGE
        else:
            want_d = STANDOFF
        if e.hp <= FINISH_HP and me_ready:
            want_d = KILL_RANGE
        if me.hp <= DMG and foe_ready and e.hp > FINISH_HP:
            want_d = HIDE_RANGE + 90.0
        pressing = (not foe_ready) and me_ready
        
        # АГРЕССИВНЫЙ ПОДХОД: если враг пуст - давим независимо от ракурса
        if pressing:
            # Враг пуст и мы заряжены - это наше время атаковать!
            # Не используем ракурс, а атакуем напрямую
            want_d = PRESS_RANGE
            # Увеличиваем агрессивность
            if self.t > self.aggressive_until:
                self.aggressive_mode = True
                self.aggressive_until = self.t + 3.0

        # Укрытие
        if not me_ready and foe_ready and d < HIDE_RANGE and t > self._cover_until:
            spot = self._cover_spot(o, me, e, d)
            if spot is not None:
                self._cover_goal = spot
                self._cover_until = t + 1.0
        if t < self._cover_until and self._cover_goal is not None:
            gx, gy = self._cover_goal
            u2 = (gx - me.x, gy - me.y)
            ln = hypot(u2[0], u2[1])
            if ln > 1e-6:
                u2 = (u2[0] / ln, u2[1] / ln)
            else:
                u2 = (1.0, 0.0)
            hull_target = (bearing + self.bear_omega * BEAR_LEAD
                           + self.side * ANGLE_LOCK)
            return self._angled_drive(o, me, hull_target, u2)

        # PRESS без линии огня
        if pressing and not los_clear and self.nav is not None:
            gx = e.x - ux * want_d
            gy = e.y - uy * want_d
            wx, wy = self.nav.route_to(me.x, me.y, gx, gy)
            u2 = (wx - me.x, wy - me.y)
            ln = hypot(u2[0], u2[1])
            if ln > 1e-6:
                u2 = (u2[0] / ln, u2[1] / ln)
            else:
                u2 = (ux, uy)
            turn, drive = self._steer(o, me, u2[0], u2[1], None, 1.0)
            return turn, drive

        # Маятник на косой
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
            self.gear = 1

        jit = ANGLE_JIT * sin(t * 2.1 + self.side)
        ang = ANGLE_LOCK + jit
        if d < 170.0:
            ang += 0.045
        hull_target = bearing + self.bear_omega * BEAR_LEAD + self.side * ang
        vhx, vhy = cos(hull_target), sin(hull_target)
        u2 = (vhx * self.gear, vhy * self.gear)
        u2 = self._wall_fix(o, me, u2, hull_target)
        if u2 is None:
            spin = self._open_spin(o, me)
            return spin, 0.2
        err = wrap(hull_target - me.hull)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        drive = self.gear * 0.96
        return turn, drive

    def _angled_drive(self, o, me, hull_target, u2):
        u2 = self._wall_fix(o, me, u2, hull_target)
        if u2 is None:
            return self._open_spin(o, me), 0.2
        turn = clamp(wrap(hull_target - me.hull) * TRACK_GAIN, -1.0, 1.0)
        hx, hy = cos(hull_target), sin(hull_target)
        gear = 1.0 if (u2[0] * hx + u2[1] * hy) >= 0.0 else -1.0
        return turn, gear * 0.98

    def _wall_fix(self, o, me, u2, hull_target):
        if self.nav is None:
            return u2
        px = me.x + u2[0] * 58.0
        py = me.y + u2[1] * 58.0
        if self.nav.free(px, py) and self.nav.clearance(px, py) >= 20.0:
            return u2
        alt = (-u2[0], -u2[1])
        px = me.x + alt[0] * 58.0
        py = me.y + alt[1] * 58.0
        if self.nav.free(px, py) and self.nav.clearance(px, py) >= 20.0:
            self.gear = -self.gear
            return alt
        return None

    def _open_spin(self, o, me):
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
        ux, uy, t_star, miss, bdx, bdy = threat
        base = atan2(-bdy, -bdx) + self.side * ANGLE_LOCK
        hx, hy = cos(base), sin(base)
        dot = ux * hx + uy * hy
        if miss < 13.0 and t_star > 0.28 and fabs(dot) < 0.45:
            err = wrap(atan2(uy, ux) - me.hull)
            return clamp(err * 2.6, -1.0, 1.0), 1.0
        err = wrap(base - me.hull)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        gear = 1.0 if dot >= 0.0 else -1.0
        if self.nav is not None:
            px = me.x + ux * DODGE_CLEAR
            py = me.y + uy * DODGE_CLEAR
            if self.nav.clearance(px, py) < 18.0:
                err = wrap(atan2(uy, ux) - me.hull)
                return clamp(err * 2.6, -1.0, 1.0), 1.0
        return turn, gear * 1.0

    def _cover_spot(self, o, me, e, d):
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

    # --- Поиск ---
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
            u = (gx - me.x, gy - me.y)
            ln = hypot(u[0], u[1])
            if ln > 1e-6:
                u = (u[0] / ln, u[1] / ln)
            else:
                u = (1.0, 0.0)
            return clamp(wrap(atan2(u[1], u[0]) - me.hull) * 2.6, -1.0, 1.0), 0.9
        wx, wy = self.nav.route_to(me.x, me.y, gx, gy)
        u = (wx - me.x, wy - me.y)
        ln = hypot(u[0], u[1])
        if ln > 1e-6:
            u = (u[0] / ln, u[1] / ln)
        else:
            u = (cos(me.hull), sin(me.hull))
        want = atan2(u[1], u[0])
        fresh = self.seen and (t - self.et) < 2.6
        if fresh:
            last_bear = atan2(self.ey - me.y, self.ex - me.x)
            hull_target = last_bear + self.side * ANGLE_LOCK
            err = wrap(hull_target - me.hull)
            turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
            gear = 1.0 if (u[0] * cos(hull_target) + u[1] * sin(hull_target)) >= 0.0 else -1.0
            return turn, gear * 0.95
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
        if self.t < 6.0 and spawns and not self.seen:
            sp = spawns[1 - o.tank]
            return (sp.x, sp.y)
        pts = nav.patrol
        n = len(pts)
        if self.patrol_order is None:
            self.patrol_k = min(range(n),
                                key=lambda i: hypot(pts[i][0] - me.x, pts[i][1] - me.y))
            self.patrol_order = 1
        g = pts[self.patrol_k % n]
        if hypot(g[0] - me.x, g[1] - me.y) < 74.0:
            self.patrol_k += 1
            g = pts[self.patrol_k % n]
        return g

    # --- Застревание ---
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
