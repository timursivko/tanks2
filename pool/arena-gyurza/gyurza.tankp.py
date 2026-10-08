#!TANKP 1
# name: Гюрза
# author: arena-gyurza
# difficulty: 5
# color: #b6ff3d
# description: Держит мёртвую зону 35.5° к азимуту врага с укороченным опережением, маятник газом со случайным полупериодом, четыре модели упреждения с онлайн-выбором, A*-навигация и прочёсывание карты (след -> чужой спавн -> свой спавн -> сетка). Локально 96.3% побед: 1512 боёв, 18 карт, 13 соперников (99.3% против всех, кроме Mantis).
# tags: ракурс,рикошет,упреждение,маятник,навигация,поиск

"""Гюрза — дуэльный танк «мёртвой зоны».

Несущая идея — сплошная мёртвая зона брони: при курсе корпуса 30–50° к
линии прихода снаряда удар рикошетит и от лба (порог 30°), и от борта
(порог 50°). Кто держит ракурс, того нельзя пробить; кто его теряет —
подставляется. Поэтому бой сводится к тому, у кого ракурс ровнее и кто
чаще ловит врага в развороте.

Поверх этого:

* четыре модели упреждения (CV, демпфированная CV, дуга по w, ускорение)
  соревнуются онлайн по фактической ошибке — какая точнее, та и целится;
* ракурс считается к азимуту на врага с укороченным опережением
  (BEAR_K < 1: полное опережение раскачивает корпус — и он же открывается);
* маятник: газ вперёд/назад меняется со случайным полупериодом, поэтому
  чужое упреждение промахивается, а ракурс не ломается;
* темп по перезарядке: враг пуст — давлю, я пуст — ухожу за укрытие;
* от стоящего врага не пятюсь: назад хода нет, пока враг не двигается
  или пока линия огня перекрыта — иначе бой вырождается в перебежки;
* поиск: свежий след -> последняя позиция -> чужой спавн -> свой спавн
  (там обычно и ждут) -> прочёсывание карты по сетке укрытий;
* маршруты — A* с оглядкой на несколько узлов (в расклине — на один),
  точки маршрута срезаются до последней видимой, узел под гусеницей
  считается пройденным (иначе танк упирается в него и залипает).
"""

from math import acos, atan2, cos, exp, fabs, hypot, pi, radians, degrees, sin
from heapq import heappush, heappop

from tankp import TankProgram, Action

# --- константы движка (копия config.Balance, чтобы не зависеть от неё) -------

BULLET = 620.0
MUZZLE = 26.0
SPREAD = 0.4
FIRE_GATE = 0                   # 1 — стреляем только по пробою (A/B: хуже)
RELAX_LOCK = 0                  # 1 — и в «расклине» держим ракурс
THR_MOD = 1                     # 1 — режем газ, когда корпус отстаёт от ракурса
THR_ERR_LO = 0.20               # рад: с какой ошибки доворота режем газ
THR_ERR_HI = 0.65               # рад: при такой ошибке газ режется максимально
THR_CUT = 0.7                   # доля, на которую режем газ
ESCAPE_DRIVE = 0.9              # задний ход при расклине
ESCAPE_VISIBLE = 0              # 1 — при виде врага расклин не включаем
FIRE_MARGIN = 6.0               # °, запас к порогу рикошета при гейте выстрела
FIRE_HOLD = 0.0                 # с: держим выстрел, пока враг перезаряжается (A/B: не рычаг)
OM_FIRE = 1.2                   # рад/с: доворот врага, при котором его корпус открыт
FIRE_WAIT = 0.0                 # с: ждём окно открытого корпуса (0 — стреляем сразу)
FIRE_HOLD_R = 300.0             # px: до какой дистанции ждём окно
SPREAD_R = radians(SPREAD)
RELOAD = 1.0
HP_MAX = 10.0
DMG = 4.0
HULL_TURN = 2.6
TURRET_TURN = 3.6
ACCEL = 460.0
SPEED_FWD = 190.0
SPEED_REV = 120.0
VIEW = 760.0

# пороги рикошета (угол к нормали, град): лоб / борт / корма
RICO = (30.0, 50.0, 60.0)
FACE_HALF = 35.0
# полуразмеры корпуса цели с учётом радиуса снаряда
HL = 20.0
HW = 14.0

ANGLE_LOCK = radians(35.5)      # середина мёртвой зоны 30..40
ANGLE_JIT = radians(1.6)
PROG_MODE = 3                   # 1 — только «стою», 2 — только «не приближаюсь», 3 — оба
REVERSE_GEAR = 0.6              # предел заднего хода в бою (0.95 и 0.42 хуже)
TRACK_GAIN = 4.2                 # усиление доворота корпуса (5.4 качало ракурс)
BEAR_K = 0.75                   # множитель опережения по азимуту (1.0 — хуже)
BEAR_LEAD = BEAR_K / (TRACK_GAIN * HULL_TURN)

D_MIN = 170.0
D_MAX = 430.0
STANDOFF = 272.0
PRESS_RANGE = 195.0
PRESS_MIN = 120.0
HIDE_RANGE = 470.0
POINT_BLANK = 70.0

GEAR_MIN = 0.24
GEAR_MAX = 0.60
SIDE_SWITCH = 0                 # 1 — менять борт ракурса (A/B: 28% — разворот открывает борт)
SIDE_MIN = 2.2
SIDE_MAX = 5.0

DODGE_MISS = 30.0               # насколько линия снаряда должна задеть
DODGE_HOLD = 0.42
DODGE_MODE = 0                  # 0 — не уклоняться, 1 — при сломанном ракурсе,
                                # 2 — только в последний момент, 3 — только точный
DODGE_LATE = 0.32               # порог «поздно чинить ракурс», с
PATH_AHEAD = 4                  # на сколько узлов A* заглядываем вперёд
LEAD_SAFE = 0.95               # с: учитываем упреждение врага в ракурсе (0 — выкл)
LEAD_SELF = 0.0                # доля учёта СВОЕГО хода в ракурсе (A/B: не рычаг)
DODGE_AIM_K = 1.0               # доля поправки на обученный уклон врага
TOL_K = 1.0                     # множитель допуска наведения при выстреле
FOE_STUCK_V = 16.0              # px/с: враг считается застрявшим
FOE_STUCK_R = 45.0              # px: насколько должен сместиться, чтобы не считаться стоящим
FOE_PRESS_STUCK = 0             # 1 — дожимаем застрявшего врага (A/B: 189L)
NO_RETREAT_STUCK = 1            # 1 — стоящего врага не пятимся (держим дистанцию)
FOE_STUCK_T = 1.2               # с: столько враг должен простоять, чтобы считаться стоящим
RETREAT_FLOOR = 0.0             # px: ниже этой дистанции назад не пятимся (0 — выкл)
NO_LOS_ROUTE = 0                # 1 — нет линии огня: идём обходом A* (A/B: 17L)
PEEK_MODE = 0                   # 1 — нет линии огня: идём к точке выстрела (хуже: 19L)

ERR_READY = 30.0                # допуск ошибки модели, px
ERR_EMPTY = 60.0
TF_READY = 0.62
TF_EMPTY = 0.95
FINISH_HP = 4.0

HUNT_PATROL_AFTER = 1e9         # с: столько не видя врага — обход карты (выкл)
HUNT_ARRIVE = 0.0               # 0 — «пройденную» точку не ищем (обход выключен)
HUNT_SWEEP_AFTER = 2.5          # с: стоим на точке сбора, врага нет — идём искать
HUNT_SWEEP_R = 90.0             # px: с какого расстояния считаем точку достигнутой
HUNT_HOME_AFTER = 8.0           # с: столько без следа — идём к своему спавну
HUNT_HOME_AGAIN = 20.0          # с: как часто возвращаться к своему спавну
SWEEP_HOLD = 8.0                # с: прочёсывание карты не отменяем раньше
HUNT_FRESH = 4.5              # след считается свежим, с
ESCAPE_GOAL = 0                 # 1 — доворачивать корпус к цели, 0 — случайно
               # 1 — доворачивать корпус к цели, 0 — случайно
ESCAPE_T = 0.5                  # с: сколько выбираемся из клина
STUCK_WINDOW = 0.6
STUCK_MILES = 12.0

TAU = pi * 2.0
INF = float("inf")


def wrap(a):
    a = (a + pi) % TAU
    return a - pi


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def unit(x, y):
    d = hypot(x, y)
    if d < 1e-9:
        return (0.0, 0.0)
    return (x / d, y / d)


def face_of(nx, ny, hull):
    """Грань корпуса по наружной нормали удара."""
    rel = degrees(fabs(wrap(atan2(ny, nx) - hull)))
    if rel <= FACE_HALF:
        return 0
    if rel >= 180.0 - FACE_HALF:
        return 2
    return 1


def ray_obb(ox, oy, dx, dy, cx, cy, hull, hl=HL, hw=HW):
    """Первое пересечение луча с корпусом.

    Возвращает ``(t, face, theta)`` или ``None``. ``theta`` — угол между
    ходом снаряда и наружной нормалью грани (0 — в упор, 90 — по касательной).
    Порядок вычислений — как в ``engine.geometry.segment_obb``.
    """
    ca = cos(hull)
    sa = sin(hull)
    rx = ox - cx
    ry = oy - cy
    lx = rx * ca + ry * sa
    ly = -rx * sa + ry * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca
    tmin = 0.0
    tmax = INF
    axis = -1
    sign = 0.0
    if fabs(ldx) < 1e-12:
        if fabs(lx) > hl:
            return None
    else:
        s = -1.0 if ldx > 0 else 1.0
        t1 = (-hl - lx) / ldx
        t2 = (hl - lx) / ldx
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = 0
            sign = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    if fabs(ldy) < 1e-12:
        if fabs(ly) > hw:
            return None
    else:
        s = -1.0 if ldy > 0 else 1.0
        t1 = (-hw - ly) / ldy
        t2 = (hw - ly) / ldy
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = 1
            sign = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    if axis < 0:
        return None
    if axis == 0:
        nxl, nyl = sign, 0.0
    else:
        nxl, nyl = 0.0, sign
    nx = nxl * ca - nyl * sa
    ny = nxl * sa + nyl * ca
    face = face_of(nx, ny, hull)
    ct = -(dx * nx + dy * ny)
    if ct < 0.0:
        ct = 0.0
    elif ct > 1.0:
        ct = 1.0
    return tmin, face, degrees(acos(ct))


def pen_ok(theta, face, margin):
    """Пробьёт ли удар грань с запасом ``margin`` градусов."""
    lim = RICO[face]
    return theta < lim - margin


class Nav:
    """Карта: проходимость, зазор, линия взгляда, A* по тайлам."""

    __slots__ = ("ts", "w", "h", "free", "opq", "clear", "spawns", "path",
                 "path_i", "goal", "goal_t", "ahead")

    def __init__(self, mv):
        self.ts = ts = mv.tile_size
        self.w = w = mv.width
        self.h = h = mv.height
        free = bytearray(w * h)
        opq = bytearray(w * h)
        for y, row in enumerate(mv.rows):
            base = y * w
            for x, ch in enumerate(row):
                if ch in "#o":
                    opq[base + x] = 1
                elif ch not in ":":
                    free[base + x] = 1
        self.free = free
        self.opq = opq
        self.clear = mv.clearance_grid
        self.spawns = mv.spawns
        self.path = []
        self.path_i = 0
        self.goal = (0.0, 0.0)
        self.goal_t = -99.0
        self.ahead = 6

    # --- запросы по координатам ---

    def tile_free(self, x, y):
        ts = self.ts
        tx = int(x // ts)
        ty = int(y // ts)
        if tx < 0 or ty < 0 or tx >= self.w or ty >= self.h:
            return False
        return self.free[ty * self.w + tx] == 1

    def clearance(self, x, y):
        ts = self.ts
        tx = int(x // ts)
        ty = int(y // ts)
        if tx < 0:
            tx = 0
        elif tx >= self.w:
            tx = self.w - 1
        if ty < 0:
            ty = 0
        elif ty >= self.h:
            ty = self.h - 1
        return self.clear[ty][tx] * ts

    def los(self, x0, y0, x1, y1):
        """Прямая видимость (как DDA движка: непрозрачные тайлы # и o)."""
        dx = x1 - x0
        dy = y1 - y0
        dist = hypot(dx, dy)
        if dist < 1e-9:
            return True
        ts = self.ts
        dx /= dist
        dy /= dist
        tx = int(x0 // ts)
        ty = int(y0 // ts)
        opq = self.opq
        w = self.w
        h = self.h
        if dx > 0:
            stepx = 1
            tdx = ts / dx
            tmx = ((tx + 1) * ts - x0) / dx
        elif dx < 0:
            stepx = -1
            tdx = -ts / dx
            tmx = (tx * ts - x0) / dx
        else:
            stepx = -1
            tdx = INF
            tmx = INF
        if dy > 0:
            stepy = 1
            tdy = ts / dy
            tmy = ((ty + 1) * ts - y0) / dy
        elif dy < 0:
            stepy = -1
            tdy = -ts / dy
            tmy = (ty * ts - y0) / dy
        else:
            stepy = -1
            tdy = INF
            tmy = INF
        t = 0.0
        while True:
            if tmx < tmy:
                t = tmx
                tmx += tdx
                tx += stepx
            else:
                t = tmy
                tmy += tdy
                ty += stepy
            if t > dist:
                return True
            if tx < 0 or ty < 0 or tx >= w or ty >= h:
                return False
            if opq[ty * w + tx]:
                return False

    def path_clear(self, x0, y0, x1, y1, margin=20.0):
        """Проедет ли корпус по прямой (с запасом ``margin`` от стен)."""
        dx = x1 - x0
        dy = y1 - y0
        dist = hypot(dx, dy)
        if dist < 1e-6:
            return True
        step = self.ts * 0.5
        n = int(dist / step) + 1
        inv = 1.0 / n
        for i in range(n + 1):
            x = x0 + dx * i * inv
            y = y0 + dy * i * inv
            if not self.tile_free(x, y):
                return False
            if self.clearance(x, y) < margin:
                return False
        return True

    # --- A* по тайлам ---

    def astar(self, sx, sy, gx, gy, max_nodes=1400):
        ts = self.ts
        w = self.w
        h = self.h
        free = self.free
        sx_i = int(sx // ts)
        sy_i = int(sy // ts)
        gx_i = int(gx // ts)
        gy_i = int(gy // ts)
        if gx_i < 0 or gy_i < 0 or gx_i >= w or gy_i >= h:
            return []
        start = sy_i * w + sx_i
        goal = gy_i * w + gx_i
        if free[goal] == 0:
            goal = self._near_free(gx_i, gy_i)
            if goal < 0:
                return []
        if start == goal:
            return [(gx, gy)]
        opens = [(0.0, start)]
        came = {start: -1}
        gscore = {start: 0.0}
        closed = set()
        nodes = 0
        while opens and nodes < max_nodes:
            _, cur = heappop(opens)
            if cur == goal:
                break
            if cur in closed:
                continue
            closed.add(cur)
            nodes += 1
            cx = cur % w
            cy = cur // w
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx = cx + ox
                ny = cy + oy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                ni = ny * w + nx
                if free[ni] == 0 or ni in closed:
                    continue
                if ox and oy and (free[cy * w + nx] == 0 or free[ny * w + cx] == 0):
                    continue
                step = 1.41421356 if (ox and oy) else 1.0
                ng = gscore[cur] + step
                old = gscore.get(ni)
                if old is None or ng < old:
                    gscore[ni] = ng
                    came[ni] = cur
                    hh = abs(nx - gx_i)
                    vv = abs(ny - gy_i)
                    f = ng + (hh if hh > vv else vv) + 0.41421356 * (vv if hh > vv else hh)
                    heappush(opens, (f, ni))
        if goal not in came:
            return []
        path = []
        cur = goal
        while cur != -1:
            path.append(((cur % w + 0.5) * ts, (cur // w + 0.5) * ts))
            cur = came[cur]
        path.reverse()
        return path

    def _near_free(self, tx, ty):
        w = self.w
        h = self.h
        free = self.free
        for r in range(1, 6):
            for oy in range(-r, r + 1):
                for ox in range(-r, r + 1):
                    x = tx + ox
                    y = ty + oy
                    if 0 <= x < w and 0 <= y < h and free[y * w + x]:
                        return y * w + x
        return -1

    def route_to(self, me_x, me_y, gx, gy, t_now):
        """Точка маршрута к цели: следующий видимый поворот пути."""
        if (hypot(gx - self.goal[0], gy - self.goal[1]) > 90.0
                or t_now - self.goal_t > 0.8 or self.path_i >= len(self.path)):
            path = self.astar(me_x, me_y, gx, gy)
            if not path:
                # цель недостижима: не подменяем путь прямой линией в стену
                self.path = []
                self.path_i = 0
                self.goal = (gx, gy)
                self.goal_t = t_now
                return None
            self.path = path
            self.path_i = 0
            self.goal = (gx, gy)
            self.goal_t = t_now
        # срезаем путь: идём к самой дальней точке, куда есть прямая
        best = None
        bi = self.path_i
        n = len(self.path)
        i = self.path_i if self.path_i < n else n - 1
        limit = min(n, i + self.ahead)
        while i < limit:
            p = self.path[i]
            if self.path_clear(me_x, me_y, p[0], p[1], 22.0):
                best = p
                bi = i
                i += 1
            else:
                break
        if best is None:
            # ни одна точка не видна по прямой — идём к ближайшему узлу пути
            bi = self.path_i if self.path_i < n else n - 1
            best = self.path[bi]
        # узел под нами: считаем его пройденным, иначе танк в него упрётся
        if hypot(best[0] - me_x, best[1] - me_y) < 26.0 and bi + 1 < n:
            bi += 1
            best = self.path[bi]
        self.path_i = bi
        return best


class Brain(TankProgram):
    """Ракурс мёртвой зоны + маятник + давление выстрелами + засада."""

    def on_start(self, ctx):
        seed = 0
        try:
            seed = int(ctx.get("seed", 0)) * 2 + int(ctx.get("tank", 0))
        except Exception:
            seed = 0
        self.rnd = (seed * 1103515245 + 12345) & 0x7FFFFFFF or 0x2545F491
        self.last_tick = -1
        self.nav = None
        self.t = 0.0

        # враг
        self.seen = False
        self.et = -99.0
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.ehull = 0.0
        self.ehp = HP_MAX
        self.e_omega = 0.0
        self.e_ax = self.e_ay = 0.0
        self.e_ready = True
        self.e_cd = 0.0
        self.prev_cd = None
        self.bear_prev = None
        self.bear_t = 0.0
        self.bear_omega = 0.0
        # модели упреждения: EMA квадрата ошибки и отложенные прогнозы
        self.m_err = [300.0, 300.0, 300.0, 300.0]
        self.m_hist = []
        self.tau_damp = 0.30
        self.flips = []
        self.flip_sign = 0
        # выстрелы врага (для уклонения) и обучение его уклону
        self.threat = None
        self.shots_log = []
        self.dodge_ema = 0.0
        self.dodge_n = 0
        self.fired_tick = -9999
        self.hold_t = -1.0
        self.foe_stuck = False
        self.still_p = (0.0, 0.0)
        self.still_t = 0.0
        self.foe_shot_t = -99.0

        # бой
        self.side = 1.0
        self.side_until = 0.0
        self.jit_ph = 0.0
        self.gear = 1
        self.gear_until = 0.0
        self.evade = None
        self._cover_goal = None
        self._cover_until = -1.0
        self.patrol = None
        self.patrol_i = 0
        self.px = self.py = 0.0
        self.mile = 0.0
        self.stuck_t = 0.0
        self.cmd_drive = 0.0
        self.escape_until = -1.0
        self.escape_side = 1.0
        self.phi = 90.0          # мой ракурс к врагу (0 — лоб в лоб)
        self.jit_ph = self._rand() * TAU
        self.mode = "start"
        self.prog_x = self.prog_y = 0.0
        self.prog_t = 0.0
        self.home = None        # где я стою на старте (для выбора чужого спавна)
        self.home_t = -99.0     # когда последний раз был у своего спавна
        self.cover = None       # точки обхода карты (когда врага нет)
        self.visit = {}         # когда в последний раз был рядом
        self.patrol_t = -99.0
        self.patrol_gi = -1
        self.prog_gx = self.prog_gy = 0.0
        self.prog_d = INF
        self.hunt_g = (0.0, 0.0)
        self.hunt_d = INF
        self.sweep_until = -1.0
        self.peek_t = -1.0
        self.peek_p = (0.0, 0.0)
        self.relax_until = -1.0

    # ------------------------------------------------------------------ утил

    def _rand(self):
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        return self.rnd / 2147483647.0

    # ------------------------------------------------------------------ тик

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"seed": 0, "tank": o.tank})
        self.last_tick = o.tick
        me = o.me
        self.t = o.time
        if self.home is None:
            self.home = (me.x, me.y)
            self.home_t = self.t
        elif hypot(me.x - self.home[0], me.y - self.home[1]) < 150.0:
            self.home_t = self.t
        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)
        if self.nav is not None:
            # застряли — идём по узлам маршрута вплотную, иначе широко
            self.nav.ahead = 1 if self.t < self.relax_until else PATH_AHEAD
        enemy = o.enemy
        if SIDE_SWITCH and enemy is not None and self.t > self.side_until:
            # держать ракурс всегда с одного борта — рассказывать врагу
            # о своём следующем шаге; борт меняем вслепую
            self.side_until = self.t + SIDE_MIN + (SIDE_MAX - SIDE_MIN) * self._rand()
            if self.t > 1.5:
                self.side = -self.side
        if enemy is not None:
            self._see(o, enemy)
            bear = atan2(enemy.y - me.y, enemy.x - me.x)
            self.phi = degrees(fabs(wrap(me.hull - bear)))
        elif self.t - self.et > 0.15:
            self.prev_cd = None
        self._watch_stuck(o, me)
        self._progress(me, self.t)
        self._resolve_shots(o, enemy)

        threat = self._threat(o, enemy, me)
        turret, fire = self._gun(o, enemy, me)

        if self.t < self.escape_until and not (ESCAPE_VISIBLE and enemy is not None):
            self.mode = "escape"
            if enemy is not None:
                # враг рядом: выходим из клина, не ломая ракурс — назад по косой
                bear = atan2(enemy.y - me.y, enemy.x - me.x)
                hull_target = bear + self.bear_omega * BEAR_LEAD + self.side * ANGLE_LOCK
                turn = clamp(wrap(hull_target - me.hull) * TRACK_GAIN)
                self.cmd_drive = -ESCAPE_DRIVE
                return Action(drive=-ESCAPE_DRIVE, turn=turn,
                              turret=turret, fire=fire)
            self.cmd_drive = -ESCAPE_DRIVE
            return Action(drive=-ESCAPE_DRIVE, turn=self.escape_side * 0.9,
                          turret=turret, fire=fire)
        if threat is not None and self._lock_bad() and self._want_dodge(threat):
            self.mode = "dodge"
            turn, drive = self._dodge_move(o, me, threat)
        elif enemy is not None:
            self.mode = "engage"
            turn, drive = self._engage(o, me, enemy, threat)
        else:
            self.mode = "hunt"
            turn, drive = self._hunt(o, me)
        self.cmd_drive = drive
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    def _want_dodge(self, threat):
        if DODGE_MODE == 0:
            return False
        if DODGE_MODE == 2:
            return threat[5] < DODGE_LATE
        if DODGE_MODE == 3:
            return threat[6] < 22.0
        if DODGE_MODE == 4:
            return threat[5] < 0.26 and self._phi_err() > 55.0
        return True

    def _phi_err(self):
        return fabs(self.phi - 35.5)

    def _lock_bad(self):
        """Сломан ли ракурс: если да, снаряд врага может пробить — уклоняемся."""
        return self.phi < 30.5 or self.phi > 40.5

    # ------------------------------------------------------------- память

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
            if self.prev_cd is not None and e.cooldown > self.prev_cd + 0.4:
                self._enemy_fired(o, e)
        me = o.me
        bear = atan2(e.y - me.y, e.x - me.x)
        if self.bear_prev is not None and 0.0005 < (t - self.bear_t) < 0.25:
            om_b = wrap(bear - self.bear_prev) / (t - self.bear_t)
            self.bear_omega = self.bear_omega * 0.7 + clamp(om_b, -4.0, 4.0) * 0.3
        self.bear_prev = bear
        self.bear_t = t
        self.ex = e.x
        self.ey = e.y
        self.evx = e.vx
        self.evy = e.vy
        self.ehull = e.hull
        self.ehp = e.hp
        self.e_ready = e.ammo_ready
        self.e_cd = e.cooldown
        self.prev_cd = e.cooldown
        self.et = t
        self.seen = True
        self._plan_predictions(o, e)

    def _enemy_fired(self, o, e):
        """Враг выстрелил: запоминаем линию снаряда (ствол и курс известны)."""
        ang = e.turret
        mx = e.x + cos(ang) * MUZZLE
        my = e.y + sin(ang) * MUZZLE
        self.threat = (mx, my, cos(ang), sin(ang), self.t)
        self.foe_shot_t = self.t

    def _note_shot(self, o, e):
        """Сверяем прошлые свои выстрелы с фактом: куда ушёл враг от линии."""
        keep = []
        for due, mx, my, dx, dy, px, py, hp0 in self.shots_log:
            if o.tick < due:
                keep.append((due, mx, my, dx, dy, px, py, hp0))
                continue
            if o.tick - due > 8 or e.hp < hp0:
                continue        # попал — учить нечего
            rx = e.x - mx
            ry = e.y - my
            lat = -dy * rx + dx * ry          # знаковое боковое смещение
            self.dodge_ema = self.dodge_ema * 0.7 + lat * 0.3
            self.dodge_n += 1
        self.shots_log = keep[-6:]

    def _plan_predictions(self, o, e):
        if len(self.m_hist) > 6:
            return
        due = o.tick + 25
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        tf = 0.42
        tau = self.tau_damp
        k = tau * (1.0 - exp(-tf / tau)) / tf if tau > 0.02 else 1.0
        p_cv = (x + vx * tf, y + vy * tf)
        p_dmp = (x + vx * tf * k, y + vy * tf * k)
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
        p_ac = (x + vx * tf + 0.5 * self.e_ax * tf * tf,
                y + vy * tf + 0.5 * self.e_ay * tf * tf)
        self.m_hist.append((due, e.x, e.y, p_cv[0], p_cv[1], p_dmp[0], p_dmp[1],
                            p_arc[0], p_arc[1], p_ac[0], p_ac[1]))

    def _score_predictions(self, o, e):
        h = self.m_hist
        while h and h[0][0] <= o.tick:
            due, ax0, ay0, cx, cy, dx_, dy_, arx, ary, acx, acy = h.pop(0)
            if o.tick - due > 3:
                continue
            fx = e.x
            fy = e.y
            m = self.m_err
            m[0] = m[0] * 0.86 + ((fx - cx) ** 2 + (fy - cy) ** 2) * 0.14
            m[1] = m[1] * 0.86 + ((fx - dx_) ** 2 + (fy - dy_) ** 2) * 0.14
            m[2] = m[2] * 0.86 + ((fx - arx) ** 2 + (fy - ary) ** 2) * 0.14
            m[3] = m[3] * 0.86 + ((fx - acx) ** 2 + (fy - acy) ** 2) * 0.14

    def _best_model(self):
        m = self.m_err
        best = 0
        bv = m[0]
        if m[1] < bv:
            best, bv = 1, m[1]
        if m[2] < bv:
            best, bv = 2, m[2]
        if m[3] < bv:
            best, bv = 3, m[3]
        return best

    def _predict(self, x, y, vx, vy, mx, my, t_fly):
        model = self._best_model()
        om = self.e_omega
        t = t_fly
        tau = self.tau_damp
        nx = x + vx * t
        ny = y + vy * t
        for _ in range(2):
            if model == 1:
                k = tau * (1.0 - exp(-t / tau)) if tau > 0.02 else 1.0
                nx = x + vx * t * k
                ny = y + vy * t * k
            elif model == 2 and fabs(om) > 0.06 and hypot(vx, vy) > 30.0:
                a = atan2(vy, vx)
                da = om * t
                r = hypot(vx, vy) / om
                nx = x + r * (sin(a + da) - sin(a))
                ny = y - r * (cos(a + da) - cos(a))
            elif model == 3:
                nx = x + vx * t + 0.5 * self.e_ax * t * t
                ny = y + vy * t + 0.5 * self.e_ay * t * t
            else:
                nx = x + vx * t
                ny = y + vy * t
            t = hypot(nx - mx, ny - my) / BULLET
        return nx, ny, t

    # ------------------------------------------------------------------ ствол

    def _gun(self, o, e, me):
        """Доворот башни; стреляем, как только ствол сошёлся с упреждением.

        Выстрел — не только урон: враг видит его и уклоняется, а уклонение
        ломает его ракурс. Поэтому стреляем часто, а не ждём «идеального»
        угла.
        """
        if e is None:
            return self._gun_blind(o, me), False
        self._score_predictions(o, e)
        d = hypot(e.x - me.x, e.y - me.y)
        mx = me.x + cos(me.turret) * MUZZLE
        my = me.y + sin(me.turret) * MUZZLE
        t_fly = hypot(e.x - mx, e.y - my) / BULLET
        px, py, t_fly = self._predict(e.x, e.y, e.vx, e.vy, mx, my, t_fly)
        # поправка на обученный уклон врага: он систематически уходит вбок
        if self.dodge_n >= 3 and fabs(self.dodge_ema) > 8.0:
            ux, uy = unit(px - mx, py - my)
            k = clamp(fabs(self.dodge_ema) / 45.0, 0.0, 0.75)
            off = self.dodge_ema * k * DODGE_AIM_K
            px += -uy * off
            py += ux * off
        err = o.aim_error(px, py)
        step = o.bullet_turn_rate * o.dt
        cmd = clamp(err / step, -1.0, 1.0)
        residual = fabs(err - cmd * step)
        if not me.ammo_ready:
            return cmd, False
        dd = hypot(px - me.x, py - me.y)
        tol = (atan2(13.5, dd) * TOL_K - SPREAD_R) if dd > 40.0 else 0.3
        if tol < 0.0035:
            tol = 0.0035
        if residual > tol:
            return cmd, False
        tur_new = me.turret + cmd * o.bullet_turn_rate * o.dt
        mzx = me.x + cos(tur_new) * MUZZLE
        mzy = me.y + sin(tur_new) * MUZZLE
        if self.nav is not None:
            if not self.nav.tile_free(mzx, mzy) or not self.nav.los(mzx, mzy, px, py):
                return cmd, False
        if FIRE_GATE and e.hp > FINISH_HP:
            # снаряд, пришедший под углом рикошета, только тратит выстрел:
            # ждём окна, когда корпус врага повёрнут под пробитие
            ux2, uy2 = unit(px - mzx, py - mzy)
            hull_at = self.ehull + self.e_omega * t_fly
            hit = ray_obb(mzx, mzy, ux2, uy2, px, py, hull_at)
            if hit is None:
                return cmd, False
            if not pen_ok(hit[2], hit[1], FIRE_MARGIN):
                return cmd, False
        if (FIRE_WAIT > 0.0 and e.hp > FINISH_HP and self.t < 52.0
                and fabs(self.e_omega) < OM_FIRE):
            # корпус врага стоит в мёртвой зоне — такой выстрел рикошетит:
            # подождём окно, когда он доворачивается (там пробьёт)
            if self.hold_t < 0.0:
                self.hold_t = self.t
            if self.t - self.hold_t < FIRE_WAIT:
                return cmd, False
        if FIRE_HOLD > 0.0 and not e.ammo_ready and d < FIRE_HOLD_R:
            # враг только что выстрелил — в это время его корпус открыт:
            # подождём немного, чтобы попасть в это окно
            if self.hold_t < 0.0:
                self.hold_t = self.t
            if self.t - self.hold_t < FIRE_HOLD:
                return cmd, False
        self.hold_t = -1.0
        self.foe_stuck = False
        self.still_p = (0.0, 0.0)
        self.still_t = 0.0
        self.shots_log.append((o.tick + max(2, int(hypot(px - mzx, py - mzy) / BULLET * 60.0)),
                               mzx, mzy, cos(tur_new), sin(tur_new), px, py, e.hp))
        if len(self.shots_log) > 6:
            del self.shots_log[0]
        self.fired_tick = o.tick
        return cmd, True

    def _gun_blind(self, o, me):
        """Врага не видно: башня на упреждённой точке последнего контакта."""
        if not self.seen:
            return 0.0
        age = self.t - self.et
        k = 0.65 if age < HUNT_FRESH else 0.0
        return o.aim_turret(self.ex + self.evx * age * k,
                            self.ey + self.evy * age * k)

    # ------------------------------------------------------------------ угроза

    def _threat(self, o, enemy, me):
        th = self.threat
        if th is None:
            return None
        mx, my, dx, dy, t0 = th
        age = self.t - t0
        if age > 1.15:
            self.threat = None
            return None
        bx = mx + dx * BULLET * age
        by = my + dy * BULLET * age
        rx = me.x - mx
        ry = me.y - my
        along = dx * rx + dy * ry
        if along < -25.0:
            self.threat = None
            return None
        lat = -dy * rx + dx * ry
        t_hit = along / BULLET
        if t_hit > 0.95 or fabs(lat) > DODGE_MISS + 55.0:
            self.threat = None
            return None
        return (bx, by, dx, dy, (1.0 if lat >= 0.0 else -1.0), t_hit, fabs(lat))

    def _dodge_move(self, o, me, threat):
        bx, by, dx, dy, side, t_hit, lat = threat
        px = -dy * side
        py = dx * side
        nav = self.nav
        if nav is not None:
            nx2 = me.x + px * 70.0
            ny2 = me.y + py * 70.0
            if not nav.tile_free(nx2, ny2) or nav.clearance(nx2, ny2) < 18.0:
                px = -px
                py = -py
                nx2 = me.x + px * 70.0
                ny2 = me.y + py * 70.0
                if not nav.tile_free(nx2, ny2) or nav.clearance(nx2, ny2) < 18.0:
                    px, py = -dy, dx
        want = atan2(py, px)
        err = wrap(want - me.hull)
        return clamp(err * 5.0), (1.0 if fabs(err) < 1.5 else 0.5)

    def _resolve_shots(self, o, enemy):
        if enemy is not None and self.shots_log:
            self._note_shot(o, enemy)

    # ------------------------------------------------------------------ бой

    def _peek(self, me, e):
        """Точка около врага, откуда его видно: обойти укрытие, а не пятиться."""
        nav = self.nav
        if nav is None:
            return None
        now = self.t
        px0, py0 = self.peek_p
        if (self.peek_t > 0.0 and now - self.peek_t < 0.3
                and hypot(px0 - e.x, py0 - e.y) < 120.0):
            return self.peek_p
        best = None
        best_d = INF
        for k in range(12):
            a = k * (pi / 6.0)
            for r in (200.0, 260.0, 330.0):
                px = e.x + cos(a) * r
                py = e.y + sin(a) * r
                if not nav.tile_free(px, py) or not nav.los(px, py, e.x, e.y):
                    continue
                dd = hypot(px - me.x, py - me.y)
                if dd < best_d:
                    best_d = dd
                    best = (px, py)
        self.peek_t = now
        self.peek_p = best if best is not None else (0.0, 0.0)
        return best

    def _engage(self, o, me, e, threat=None):
        dx = e.x - me.x
        dy = e.y - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d = 1e-6
            dx, dy = 1.0, 0.0
        ux = dx / d
        uy = dy / d
        bearing = atan2(dy, dx)
        me_ready = me.ammo_ready
        foe_ready = e.ammo_ready or e.cooldown < 0.32
        t = self.t
        los_clear = True
        if self.nav is not None:
            los_clear = self.nav.los(me.x, me.y, e.x, e.y)

        foe_stuck = self.foe_stuck
        # --- дистанция по темпу ---
        if not foe_ready:
            want_d = PRESS_RANGE if me_ready else STANDOFF
        elif not me_ready:
            want_d = STANDOFF
        else:
            want_d = STANDOFF
        if e.hp <= FINISH_HP and me_ready:
            want_d = PRESS_MIN + 40.0
        if FOE_PRESS_STUCK and foe_stuck and me_ready:
            # враг застрял (ткнулся в стену) — карусель бесполезна, дожимаем
            want_d = min(want_d, PRESS_MIN + 40.0)
        if me.hp <= DMG and foe_ready and e.hp > FINISH_HP:
            want_d = HIDE_RANGE + 90.0
        pressing = (not foe_ready) and me_ready

        # --- нет линии огня: идём к точке, откуда врага видно ---
        if not los_clear and PEEK_MODE:
            pk = self._peek(me, e)
            if pk is not None:
                wp = self.nav.route_to(me.x, me.y, pk[0], pk[1], t)
                if wp is not None and not self.nav.path_clear(me.x, me.y, wp[0], wp[1], 20.0):
                    wp = None
                tx, ty = wp if wp is not None else pk
                u2 = unit(tx - me.x, ty - me.y)
                if u2 == (0.0, 0.0):
                    u2 = (ux, uy)
                return self._steer(o, me, u2[0], u2[1], None, 1.0)

        # --- нет линии огня: обходим стену, а не пятимся от неё ---
        if (not los_clear and self.nav is not None
                and (NO_LOS_ROUTE or pressing)):
            gx = e.x - ux * want_d
            gy = e.y - uy * want_d
            wp = self.nav.route_to(me.x, me.y, gx, gy, t)
            if wp is None:
                wp = (gx, gy)
            u2 = unit(wp[0] - me.x, wp[1] - me.y)
            if u2 == (0.0, 0.0):
                u2 = (ux, uy)
            return self._steer(o, me, u2[0], u2[1], None, 1.0)

        # --- маятник на косой ---
        foe_stuck = self.foe_stuck
        if t > self.gear_until:
            u = self._rand()
            self.gear_until = t + GEAR_MIN + (GEAR_MAX - GEAR_MIN) * u
            if d > want_d + 45.0:
                self.gear = 1
            elif (d < want_d - 45.0 and d > RETREAT_FLOOR
                  and not (NO_RETREAT_STUCK and (foe_stuck or not los_clear))):
                self.gear = -1
            elif u > 0.5:
                self.gear = -self.gear
        if pressing and d > want_d:
            self.gear = 1
        jit = ANGLE_JIT * sin(t * 2.1 + self.side + self.jit_ph)
        ang = ANGLE_LOCK + jit
        if d < 170.0:
            ang += 0.045
        # враг целится с упреждением, и снаряд приходит под углом меньше
        # ракурса: пока его снаряд в полёте, меряем ракурс по линии снаряда
        ref = bearing
        if LEAD_SELF > 0.0:
            # враг целится с упреждением: снаряд придёт по линии на мою
            # будущую позицию. Держим мёртвую зону к этой линии, а не к
            # сегодняшнему азимуту на врага.
            t_fly = d / BULLET
            fx = me.x + me.vx * t_fly * LEAD_SELF
            fy = me.y + me.vy * t_fly * LEAD_SELF
            if hypot(fx - e.x, fy - e.y) > 1.0:
                ref = atan2(fy - e.y, fx - e.x) + pi
        if LEAD_SAFE > 0.0 and threat is not None and threat[5] < LEAD_SAFE:
            bx, by = threat[0], threat[1]
            if hypot(bx - me.x, by - me.y) > 60.0:
                ta = atan2(by - me.y, bx - me.x)
                if fabs(wrap(ta - bearing)) < 0.6:
                    ref = ta
        hull_target = ref + self.bear_omega * BEAR_LEAD + self.side * ang
        u2 = (cos(hull_target) * self.gear, sin(hull_target) * self.gear)
        u2 = self._wall_fix(o, me, u2, hull_target)
        if u2 is None:
            spin = self._open_spin(o, me)
            return spin, 0.2
        err = wrap(hull_target - me.hull)
        thr = 0.96
        if THR_MOD:
            ae = fabs(err)
            if ae > THR_ERR_LO:
                k = (ae - THR_ERR_LO) / max(1e-6, THR_ERR_HI - THR_ERR_LO)
                thr *= 1.0 - clamp(k) * THR_CUT
        return clamp(err * TRACK_GAIN), self.gear * thr

    def _angled_drive(self, o, me, hull_target, u2):
        """Едем к точке u2, не ломая ракурс корпуса."""
        u2 = self._wall_fix(o, me, u2, hull_target)
        if u2 is None:
            return self._open_spin(o, me), 0.2
        turn = clamp(wrap(hull_target - me.hull) * TRACK_GAIN)
        hx = cos(hull_target)
        hy = sin(hull_target)
        gear = 1.0 if (u2[0] * hx + u2[1] * hy) >= 0.0 else -1.0
        # задний ход только половинной скоростью: пятясь, танк «убегает»
        # от цели и теряет время
        return turn, 0.95 if gear > 0 else -REVERSE_GEAR

    def _steer(self, o, me, ux, uy, hull_target, throttle):
        if hull_target is None:
            hull_target = atan2(uy, ux)
        err = wrap(hull_target - me.hull)
        turn = clamp(err * TRACK_GAIN)
        u2 = self._wall_fix(o, me, (ux, uy), hull_target)
        if u2 is None:
            return self._open_spin(o, me), 0.2
        hx = cos(hull_target)
        hy = sin(hull_target)
        gear = 1.0 if (u2[0] * hx + u2[1] * hy) >= 0.0 else -1.0
        return turn, throttle if gear > 0 else -REVERSE_GEAR * throttle

    def _wall_fix(self, o, me, u2, hull_target):
        """Не даём ехать в стену.

        Берём ближайшее к желаемому направление, где впереди есть место:
        раньше при препятствии мы разворачивались назад, и танк начинал
        дёргаться у стены, не приближаясь к цели.
        """
        if self.nav is None:
            return u2
        base = atan2(u2[1], u2[0])
        step = pi / 8.0
        for k in range(0, 9):
            for sgn in ((0.0,) if k == 0 else (1.0, -1.0)):
                a = base + sgn * k * step
                cx = me.x + cos(a) * 58.0
                cy = me.y + sin(a) * 58.0
                if self.nav.tile_free(cx, cy) and self.nav.clearance(cx, cy) >= 20.0:
                    return (cos(a), sin(a))
        best = None
        best_c = -1.0
        for k in range(16):
            a = base + k * step
            cx = me.x + cos(a) * 58.0
            cy = me.y + sin(a) * 58.0
            if not self.nav.tile_free(cx, cy):
                continue
            c = self.nav.clearance(cx, cy) - 0.15 * degrees(fabs(wrap(a - base)))
            if c > best_c:
                best_c = c
                best = (cos(a), sin(a))
        return best

    def _open_spin(self, o, me):
        if self.nav is None:
            return 1.0
        best = 1.0
        best_c = -1.0
        for k in range(8):
            a = k * pi / 4.0
            cx = me.x + cos(a) * 50.0
            cy = me.y + sin(a) * 50.0
            if not self.nav.tile_free(cx, cy):
                continue
            c = self.nav.clearance(cx, cy)
            if c > best_c:
                best_c = c
                best = 1.0 if wrap(a - me.hull) > 0 else -1.0
        return best

    def _hunt(self, o, me):
        """Врага не видно.

        Идём к лучшей оценке его позиции (свежий след → последняя позиция →
        его спавн), а когда прямая линия упирается в стену — обходим её
        маршрутом A*. Ракурс мёртвой зоны держим всё время: тогда первый же
        контакт встречается лбом в зоне рикошета, а не бортом.
        """
        t = self.t
        age = t - self.et if self.seen else 99.0
        fresh = age < HUNT_FRESH
        ex = ey = None
        gx = gy = None
        if fresh:
            self.sweep_until = -1.0      # след свежий — прочёсывание отменяется
            ex = self.ex + self.evx * age * 0.65
            ey = self.ey + self.evy * age * 0.65
            gx, gy = ex, ey
            if hypot(gx - me.x, gy - me.y) < POINT_BLANK:
                gx = gy = None
        elif self.seen:
            ex, ey = self.ex, self.ey
        else:
            sp = self._foe_spawn(o)
            if sp is not None:
                ex, ey = sp
        if (not fresh and self.home is not None
                and t - self.et > HUNT_HOME_AFTER
                and t - self.home_t > HUNT_HOME_AGAIN):
            # врага долго нет: он, скорее всего, ждёт меня у моего спавна
            hx, hy = self.home
            if hypot(hx - me.x, hy - me.y) > 150.0:
                ex, ey = hx, hy
        lock = False
        if gx is None and ex is not None and t >= self.sweep_until:
            gx, gy = ex, ey
            if hypot(gx - me.x, gy - me.y) < POINT_BLANK:
                gx = gy = None
        if gx is not None and t < self.sweep_until:
            gx = gy = None           # прочёсывание идёт — назад не разворачиваемся
        if gx is not None and not fresh:
            dist = hypot(gx - me.x, gy - me.y)
            if age > HUNT_SWEEP_AFTER and dist < HUNT_SWEEP_R:
                # точка сбора достигнута, а врага нет: прочёсываем карту
                self.sweep_until = t + SWEEP_HOLD
                gx = gy = None
        if gx is None:
            gx, gy = self._patrol_goal(o, me)   # обход карты: ракурс не держим,
        else:                                   # иначе едем к укрытию задом
            lock = True
        if lock and self.nav is not None and not self.nav.path_clear(me.x, me.y, gx, gy, 20.0):
            wp = self.nav.route_to(me.x, me.y, gx, gy, t)
            if wp is None:
                self.sweep_until = t + SWEEP_HOLD
                gx, gy = self._patrol_goal(o, me)   # цель закрыта — идём в обход
                lock = False
            else:
                gx, gy = wp
        self.hunt_g = (gx, gy)
        self.hunt_d = hypot(gx - me.x, gy - me.y)
        u2 = unit(gx - me.x, gy - me.y)
        if u2 == (0.0, 0.0):
            u2 = (cos(me.hull), sin(me.hull))
        if t < self.relax_until or ex is None or not lock:
            if RELAX_LOCK and ex is not None and t < self.relax_until:
                bear = atan2(ey - me.y, ex - me.x)
                hull_target = (bear + self.bear_omega * BEAR_LEAD
                               + self.side * ANGLE_LOCK)
                return self._angled_drive(o, me, hull_target, u2)
            return self._steer(o, me, u2[0], u2[1], None, 1.0)
        bear = atan2(ey - me.y, ex - me.x)
        hull_target = bear + self.bear_omega * BEAR_LEAD + self.side * ANGLE_LOCK
        return self._angled_drive(o, me, hull_target, u2)

    def _foe_spawn(self, o):
        """Где искать врага, если он ни разу не попал в обзор.

        Спавны движок раздаёт не по номеру танка, поэтому свой старт мы
        запоминаем сами, а целью берём самый далёкий от него спавн.
        """
        if o.map is None or not o.map.spawns:
            return None
        sp = o.map.spawns
        if self.home is None:
            s = sp[1 - o.tank] if len(sp) > 1 else sp[0]
            return (s.x, s.y)
        best = None
        bd = -1.0
        for s in sp:
            dd = hypot(s.x - self.home[0], s.y - self.home[1])
            if dd > bd:
                bd = dd
                best = (s.x, s.y)
        return best

    def _make_patrol(self, me):
        """Точки обхода: чужие спавны + сетка открытых мест карты.

        Сетка с шагом 6 тайлов и зазором не меньше 1.6 тайла: по узким
        проходам танк всё равно не проедет, а торчать в них бесполезно.
        """
        nav = self.nav
        if nav is None:
            return []
        ts = nav.ts
        pts = [(s.x, s.y) for s in nav.spawns]
        need = 1.6 * ts
        for ty in range(2, nav.h - 2, 6):
            for tx in range(2, nav.w - 2, 6):
                x = (tx + 0.5) * ts
                y = (ty + 0.5) * ts
                if not nav.tile_free(x, y) or nav.clearance(x, y) < need:
                    continue
                near = False
                for px, py in pts:
                    if hypot(x - px, y - py) < 90.0:
                        near = True
                        break
                if not near:
                    pts.append((x, y))
        return pts

    def _ensure_cover(self, me):
        if self.cover is None:
            self.cover = self._make_patrol(me)

    def _pick_cover(self, me, t):
        """Самая дальняя давно не посещённая точка обхода.

        Именно дальняя: обход должен прочёсывать карту насквозь, а не
        топтаться в соседних узлах — враг идёт нам навстречу.
        """
        best = None
        best_d = 0.0
        for i, (px, py) in enumerate(self.cover):
            if t - self.visit.get(i, -1e9) < 14.0:
                continue
            dd = hypot(px - me.x, py - me.y)
            if dd > best_d:
                best_d = dd
                best = i
        if best is None:
            for i, (px, py) in enumerate(self.cover):
                dd = hypot(px - me.x, py - me.y)
                if dd > best_d:
                    best_d = dd
                    best = i
        return best

    def _patrol_goal(self, o, me):
        t = self.t
        self._ensure_cover(me)
        if not self.cover:
            return (me.x + cos(me.hull) * 200.0, me.y + sin(me.hull) * 200.0)
        gi = self.patrol_gi
        gx = gy = None
        if gi >= 0:
            gx, gy = self.cover[gi]
            if hypot(gx - me.x, gy - me.y) < 90.0:
                self.visit[gi] = t      # точка пройдена
                gi = -1
            elif t - self.patrol_t > 18.0:
                gi = -1                 # цель не поддалась — возьмём другую
        if gi < 0:
            gi = self._pick_cover(me, t)
            if gi is None:
                gi = 0
            self.patrol_gi = gi
            self.patrol_t = t
            gx, gy = self.cover[gi]
        if self.nav is not None:
            wp = self.nav.route_to(me.x, me.y, gx, gy, t)
            if wp is None or self.nav.path_clear(me.x, me.y, wp[0], wp[1], 20.0):
                return wp or (gx, gy)
            return wp
        return (gx, gy)

    def _progress(self, me, t):
        """Сторож прогресса.

        Ракурс мёртвой зоны мешает движению, когда цель за стеной: танк
        топчется на месте или кружит, не приближаясь. Тогда на время снимаем
        ракурс и едем напрямую.
        """
        if t - self.prog_t < 1.0:
            return
        if (t - self.prog_t) < 1.4 and self.mode == "hunt":
            stuck = hypot(me.x - self.prog_x, me.y - self.prog_y) < 38.0
            same = hypot(self.hunt_g[0] - self.prog_gx,
                         self.hunt_g[1] - self.prog_gy) < 70.0
            no_gain = same and self.hunt_d > self.prog_d - 24.0
            if (fabs(self.cmd_drive) > 0.25
                    and ((PROG_MODE == 1 and stuck)
                         or (PROG_MODE == 2 and no_gain)
                         or (PROG_MODE == 3 and (stuck or no_gain)))):
                self.relax_until = t + 1.7
                if self.patrol_gi >= 0:     # сменить цель обхода
                    self.visit[self.patrol_gi] = t
                    self.patrol_t = -99.0
                if self.nav is not None:
                    self.nav.path_i = 10 ** 9
        self.prog_x = me.x
        self.prog_y = me.y
        self.prog_gx, self.prog_gy = self.hunt_g
        self.prog_d = self.hunt_d
        self.prog_t = t

    def _watch_stuck(self, o, me):
        d = hypot(me.x - self.px, me.y - self.py)
        self.mile += d
        self.px = me.x
        self.py = me.y
        t = self.t
        if self.mile < STUCK_MILES:
            if t - self.stuck_t > STUCK_WINDOW:
                if fabs(self.cmd_drive) > 0.3:
                    self.escape_until = t + ESCAPE_T
                    # пятимся и доворачиваем корпус в сторону цели
                    if ESCAPE_GOAL:
                        gx, gy = self.hunt_g
                        want = atan2(gy - me.y, gx - me.x)
                        self.escape_side = (1.0 if wrap(want - me.hull) >= 0.0
                                            else -1.0)
                    else:
                        self.escape_side = 1.0 if self._rand() > 0.5 else -1.0
                self.stuck_t = t
                self.mile = 0.0
        else:
            self.stuck_t = t
            self.mile = 0.0


program = Brain()
