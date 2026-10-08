#!TANKP 1
# name: Mantis
# author: arena-mantis
# difficulty: 5
# color: #7cf03d
# description: Ракурс 35° (середина мёртвой зоны) к линии чужого снаряда, газ платит за доворот, борт не подставляется никогда, маятник переворачивает борт через 4 с, выстрел в окно пробития и «на грани» — прогноз корпуса на момент удара, три модели упреждения с онлайн-выбором. Локально 96.2% против пула (720 боёв, 18 карт).
# tags: ракурс,темп,прогноз,уклонение,геометрия

"""Mantis — дуэльный танк «ракурса и темпа».

Всё держится на одном свойстве движка: броня не снижает урон, она рикошетит.
Снаряд, пришедший под 30..40° к нормали (или 140..150°), отбивается И лбом
(порог 30°), И бортом (порог 50°) — это сплошная мёртвая зона. Отсюда три
несущие идеи:

1. **Ракурс к линии снаряда, а не к врагу.** Корпус держится под 35° —
   ровно в середине мёртвой зоны — к направлению, ОТКУДА придёт снаряд. За основу берётся не сегодняшний
   азимут на врага, а точка, где мы будем через время подлёта (свою
   позицию мы знаем точно — упреждение считаем сами). Замечен выстрел —
   ракурс доворачивается к ЛИНИИ снаряда: она зафиксирована в момент
   выстрела, поэтому на доворот есть весь полёт. Стационарный лаг
   П-регулятора (1/(усиление·скорость корпуса)) вычитается заранее
   доворотом цели на скорость азимута.

2. **Газ платит за ракурс.** При полном руле скорость разворота корпуса
   равна 2.6/(1+газ): мчать и держать угол физически нельзя. Как только
   корпус отстаёт или азимут уносится, газ режется вплоть до нуля —
   потерянный ход дешевле пробития. На ближней дистанции, где азимут
   крутится быстрее чужого привода, это решает бой.

3. **Темп вместо ожидания.** Стреляем каждый раз, когда ствол смотрит в
   упреждённую точку, а прогноз корпуса на момент удара даёт пробитие —
   либо почти даёт (запас 12°): противник, держащий ракурс, всё равно
   рыскает, и часть «рикошетных» выстрелов проходит. Прогноз позиции —
   три модели (ровный ход, дуга по ω, скорость+ускорение) с онлайн-выбором
   лучшей по EMA ошибки; направление выстрела ищется сканом по силуэту —
   берётся самая широкая пробивающая полоса.

4. **На марше корпус уже в бою.** Первые секунды танк идёт к чужому
   спавну, но корпус держит под ракурсом к направлению на спавн, а не
   «носом вперёд»: первый контакт случается именно на этом марше, и
   0.4 с на доворот ракурса под чужим выстрелом стоят 4 ХП.

5. **Борт не подставлять никогда.** Соблазн «довернуть корпус поперёк и
   рвануть вдоль ухода» обманчив: борт держит лишь 50°, а снаряд, летящий
   0.4 с, почти всегда успевает. Уход делается самим ракурсом — корпус под
   35° к линии снаряда даёт 80% хода вбок, так что с линии мы сходим, не
   открывая пробиваемой грани. Отказ от бортового разворота — самая
   дорогая правка: против пула +2.5 п.п., против «кайтера» +25 п.п.

Плюс: уход с линии выпущенного снаряда, маятник на косой (азимут на нас
дёргается, чужой регулятор ракурса отстаёт), A* с кэшем на поиске, дозор
застревания и добивание на последних секундах — ничья не победа.

Проверено локально (18 карт × 4 сида, 144 боя на каждого): против пула
96.2% — karakurt 89.6, tempest 96.5, raptor 97.2, nova 98.6, vulkan 99.3;
против «кайтера» (ai/nagibator, пятится под неизменным ракурсом) 79.2%.
Средний think ~0.1 мс, пик 2.7 мс при бюджете 10 мс.
"""

from heapq import heappop, heappush
from math import (acos, atan2, cos, degrees, fabs, hypot, pi, radians, sin,
                  sqrt)

from tankp import TankProgram, Action

# --- константы движка (копия config.Balance, чтобы не зависеть от неё) -------

TAU = pi * 2.0
BULLET = 620.0
MUZZLE = 26.0
SPREAD_DEG = 0.4
SPREAD_RAD = radians(SPREAD_DEG)
RELOAD = 1.0
HULL_TURN = 2.6
TURRET_TURN = 3.6
ACCEL = 460.0
SPEED_FWD = 190.0
SPEED_REV = 120.0
HP_MAX = 10.0
DMG = 4.0

#: Пороги рикошета (угол к нормали, град): лоб / борт / корма.
RICO_FRONT = 30.0
RICO_SIDE = 50.0
RICO_REAR = 60.0
FACE_HALF = 35.0
#: Полуразмеры корпуса с радиусом снаряда (half_far движка).
HX = 20.0
HY = 14.0

# --- настройки ---------------------------------------------------------------

ANGLE_LOCK = radians(35.0)     # целевой ракурс: середина мёртвой зоны
ANGLE_JIT = radians(1.6)       # медленный джиттер ракурса
TRACK_GAIN = 5.0               # усиление доворота корпуса на азимут
BEAR_LEAD = 1.0 / (TRACK_GAIN * HULL_TURN)   # гасит стационарный лаг

STANDOFF = 145.0               # кольцо маятника
PRESS_RANGE = 120.0            # враг пуст — подходим
FAR_RANGE = 520.0              # пусты мы и враг готов — отходим
POINT_BLANK = 70.0             # в упор углы не считаем
RAM_SAFE = 84.0                # ближе — риск обоюдного тарана

GEAR_MIN = 0.22                # полупериод джиттера газом, с
GEAR_MAX = 0.55
SIDE_MIN = 2.0                 # мин. длительность стороны ракурса, с
SIDE_MAX = 4.5

ERR_READY = 40.0               # допуск ошибки модели, px: враг заряжен
ERR_EMPTY = 70.0               # враг пуст — стреляем свободнее
TF_READY = 0.66                # допуск времени подлёта, с
TF_EMPTY = 1.0
FINISH_HP = 4.0                # одна наша пуля от смерти врага

DODGE_MISS = 44.0              # снаряд разминётся сам — не маневрируем
HUNT_FRESH = 4.0
STUCK_WINDOW = 0.6
STUCK_MILES = 14.0
BATTLE_LEN = 60.0
URGENT_T = 0.0                # позже этого ничья дороже риска
END_SLACK = 12.0                # насколько «почти пробитие» годится в конце


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


# --- геометрия выстрела ------------------------------------------------------


def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang):
    """Луч против повёрнутого прямоугольника: (t, nx, ny) или None."""
    ca = cos(ang)
    sa = sin(ang)
    rx = ox - cx
    ry = oy - cy
    lx = rx * ca + ry * sa
    ly = -rx * sa + ry * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca

    tmin = 0.0
    tmax = 1e9
    axis = -1
    sign = 0.0
    # ось X корпуса
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
            tmin = t1
            axis = 0
            sign = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    # ось Y корпуса
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
            tmin = t1
            axis = 1
            sign = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    if axis < 0:
        if tmax < 0.0:
            return None
        if fabs(ldx) >= fabs(ldy):
            nx, ny = (-1.0 if ldx > 0.0 else 1.0), 0.0
        else:
            nx, ny = 0.0, (-1.0 if ldy > 0.0 else 1.0)
        return 0.0, nx * ca - ny * sa, nx * sa + ny * ca
    if axis == 0:
        nx, ny = sign, 0.0
    else:
        nx, ny = 0.0, sign
    return tmin, nx * ca - ny * sa, nx * sa + ny * ca


def face_limit(nx, ny, hull):
    """Порог рикошета для грани, чья нормаль (nx, ny) при курсе корпуса hull."""
    rel = fabs(degrees(wrap(atan2(ny, nx) - hull)))
    if rel <= FACE_HALF:
        return RICO_FRONT
    if rel >= 180.0 - FACE_HALF:
        return RICO_REAR
    return RICO_SIDE


def theta_of(nx, ny, dx, dy):
    c = -(dx * nx + dy * ny)
    c = 0.0 if c < 0.0 else (1.0 if c > 1.0 else c)
    return degrees(acos(c))


# --- карта -------------------------------------------------------------------


class Nav:
    """Проходимость, зазор, линия огня и маршрут по сетке."""

    def __init__(self, mv):
        self.mv = mv
        self.tile = mv.tile_size
        self.rows = mv.rows
        self.w = mv.width
        self.h = mv.height
        self.cl = mv.clearance_grid
        #: плоская маска «сюда нельзя»: один индекс вместо двух и без срезов строк
        self.block = bytearray(1 if ch in "#o:" else 0
                               for row in self.rows for ch in row)
        self.path = None
        self.goal = None
        self.path_t = -99.0
        self.patrol = self._make_patrol()

    def ti(self, x, y):
        t = self.tile
        tx = int(x // t)
        ty = int(y // t)
        if tx < 0:
            tx = 0
        elif tx >= self.w:
            tx = self.w - 1
        if ty < 0:
            ty = 0
        elif ty >= self.h:
            ty = self.h - 1
        return tx, ty

    def free(self, x, y):
        tx, ty = self.ti(x, y)
        return self.rows[ty][tx] not in "#o:"

    def clearance(self, x, y):
        tx, ty = self.ti(x, y)
        return self.cl[ty][tx] * self.tile

    def blocked(self, x, y):
        tx, ty = self.ti(x, y)
        return self.rows[ty][tx] in "#o"

    def _make_patrol(self):
        """Разнесённые свободные точки по всей карте."""
        t = self.tile
        cand = []
        for ty in range(1, self.h - 1, 2):
            row = self.rows[ty]
            clrow = self.cl[ty]
            for tx in range(1, self.w - 1, 2):
                if row[tx] in "#o:":
                    continue
                if clrow[tx] < 1.0:
                    continue
                cand.append(((tx + 0.5) * t, (ty + 0.5) * t))
        pts = []
        for c in cand:
            if all((c[0] - p[0]) ** 2 + (c[1] - p[1]) ** 2 > 230.0 * 230.0
                   for p in pts):
                pts.append(c)
            if len(pts) >= 14:
                break
        return pts

    def los_blocked(self, x0, y0, x1, y1):
        """Грубая, но быстрая проверка: шаг вдвое меньше тайла."""
        dx = x1 - x0
        dy = y1 - y0
        n = int(max(fabs(dx), fabs(dy)) / (self.tile * 0.5)) + 1
        rows = self.rows
        t = self.tile
        w = self.w
        h = self.h
        for i in range(1, n):
            f = i / n
            tx = int((x0 + dx * f) // t)
            ty = int((y0 + dy * f) // t)
            if tx < 0 or ty < 0 or tx >= w or ty >= h:
                return True
            if rows[ty][tx] in "#o":
                return True
        return False

    # --- маршрут -------------------------------------------------------------

    def route(self, x, y, gx, gy, t):
        """Следующая точка маршрута к цели (A* с кэшем)."""
        if (self.path and self.goal is not None
                and (gx - self.goal[0]) ** 2 + (gy - self.goal[1]) ** 2 < 130.0 * 130.0
                and t - self.path_t < 0.45):
            return self._follow(x, y)
        path = self.astar(x, y, gx, gy)
        if not path:
            self.path = None
            return gx, gy
        self.path = path
        self.goal = (gx, gy)
        self.path_t = t
        return self._follow(x, y)

    def _follow(self, x, y):
        p = self.path
        while p and hypot(p[0][0] - x, p[0][1] - y) < 46.0:
            p.pop(0)
        if not p:
            return self.goal if self.goal else (x, y)
        return p[0]

    def astar(self, sx, sy, gx, gy, max_nodes=320):
        """A* по сетке: плоская маска, куча, без вложенных функций."""
        t = self.tile
        w = self.w
        h = self.h
        blk = self.block
        stx, sty = self.ti(sx, sy)
        gtx, gty = self.ti(gx, gy)
        start = sty * w + stx
        goal = gty * w + gtx
        if blk[goal]:
            # цель в стене — ближайшая свободная клетка вокруг
            best = -1
            bd = 99
            for r in range(1, 5):
                for oy in range(-r, r + 1):
                    for ox in range(-r, r + 1):
                        tx = gtx + ox
                        ty = gty + oy
                        if tx < 0 or ty < 0 or tx >= w or ty >= h:
                            continue
                        k = ty * w + tx
                        if not blk[k]:
                            d = ox * ox + oy * oy
                            if d < bd:
                                bd = d
                                best = k
                if best >= 0:
                    break
            if best < 0:
                return None
            goal = best
        gtx, gty = goal % w, goal // w
        open_heap = [(0.0, start)]
        came = {}
        g = {start: 0.0}
        cl = self.cl
        nodes = 0
        diag = 1.4142135
        while open_heap and nodes < max_nodes:
            _, cur = heappop(open_heap)
            if cur == goal:
                break
            nodes += 1
            cx = cur % w
            cy = cur // w
            gc = g[cur]
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx = cx + ox
                ny = cy + oy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                key = ny * w + nx
                if blk[key]:
                    continue
                if ox and oy and (blk[cy * w + nx] or blk[ny * w + cx]):
                    continue
                step = diag if (ox and oy) else 1.0
                if cl[ny][nx] < 1.0:
                    step += 0.6          # теснота штрафует
                ng = gc + step
                old = g.get(key)
                if old is None or ng < old - 1e-9:
                    g[key] = ng
                    came[key] = cur
                    heappush(open_heap,
                                   (ng + hypot(nx - gtx, ny - gty), key))
        if goal not in came and goal != start:
            return None
        cells = []
        cur = goal
        while cur in came:
            cells.append(cur)
            cur = came[cur]
            if len(cells) > 300:
                break
        cells.reverse()
        return [(((k % w) + 0.5) * t, ((k // w) + 0.5) * t) for k in cells]


# --- мозг --------------------------------------------------------------------


class Brain(TankProgram):

    def on_start(self, ctx):
        seed = 0
        try:
            seed = int(ctx.get("seed", 0) or 0)
        except Exception:
            seed = 0
        self._seed = (seed * 2654435761 + 12345) & 0xFFFFFFFF
        self.nav = None
        self.t = 0.0
        self.last_tick = -1
        # модель врага
        self.seen = False
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.ehull = 0.0
        self.et = -99.0
        self.e_omega = 0.0
        self.e_ax = self.e_ay = 0.0
        self.bear_omega = 0.0
        self.bear_prev = None
        self.bear_t = 0.0
        # модели упреждения: 0 — CV, 1 — дуга, 2 — с ускорением
        self.m_err = [400.0, 400.0, 400.0]
        self.m_hist = []
        # свои выстрелы (учимся на исходах)
        self.shots_log = []
        self.hit_ema = 0.3
        self.waste_n = 0
        self.waste_ema = 0.0
        self.fired_tick = -99
        # ракурс и ход
        self.side = 1.0 if self._rand() > 0.5 else -1.0
        self.side_until = 0.0
        self.gear = 1
        self.gear_until = 0.0
        self.threat = None
        self.threat_hot = False
        self.prev_cd = None
        # застревание
        self.px = self.py = 0.0
        self.mile = 0.0
        self.stuck_t = 0.0
        self.escape_until = -99.0
        self.escape_side = 1.0
        self.cmd_drive = 0.0
        self.patrol_k = None

    # --- служебное -----------------------------------------------------------

    def _rand(self):
        self._seed = (self._seed * 1103515245 + 12345) & 0x7FFFFFFF
        return (self._seed >> 8) / 8388608.0

    # ------------------------------------------------------------- модель врага

    def _see(self, o, e):
        t = self.t
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.3:
                om = wrap(e.hull - self.ehull) / dt
                self.e_omega = self.e_omega * 0.70 + clamp(om, -HULL_TURN, HULL_TURN) * 0.30
                ax = (e.vx - self.evx) / dt
                ay = (e.vy - self.evy) / dt
                self.e_ax = self.e_ax * 0.8 + clamp(ax, -900.0, 900.0) * 0.2
                self.e_ay = self.e_ay * 0.8 + clamp(ay, -900.0, 900.0) * 0.2
            elif dt >= 0.5:
                self.e_omega *= 0.5
                self.e_ax *= 0.3
                self.e_ay *= 0.3
                self.bear_omega = 0.0
        me = o.me
        bear = atan2(e.y - me.y, e.x - me.x)
        if self.bear_prev is not None and 0.0005 < (t - self.bear_t) < 0.3:
            om_b = wrap(bear - self.bear_prev) / (t - self.bear_t)
            self.bear_omega = self.bear_omega * 0.72 + clamp(om_b, -4.0, 4.0) * 0.28
        self.bear_prev = bear
        self.bear_t = t
        self.ex, self.ey = e.x, e.y
        self.evx, self.evy = e.vx, e.vy
        self.ehull = e.hull
        self.et = t
        self.seen = True
        self._plan_models(o, e)

    def _plan_models(self, o, e):
        if len(self.m_hist) > 6:
            return
        due = o.tick + 22
        x, y, vx, vy = e.x, e.y, e.vx, e.vy
        tf = 0.37
        p_cv = (x + vx * tf, y + vy * tf)
        om = self.e_omega
        sp = hypot(vx, vy)
        if fabs(om) > 0.08 and sp > 30.0:
            a = atan2(vy, vx)
            da = om * tf
            r = sp / om
            p_arc = (x + r * (sin(a + da) - sin(a)),
                     y - r * (cos(a + da) - cos(a)))
        else:
            p_arc = p_cv
        p_ac = (x + vx * tf + 0.5 * self.e_ax * tf * tf,
                y + vy * tf + 0.5 * self.e_ay * tf * tf)
        self.m_hist.append((due, p_cv[0], p_cv[1], p_arc[0], p_arc[1],
                            p_ac[0], p_ac[1]))

    def _score_models(self, o, e):
        h = self.m_hist
        while h and h[0][0] <= o.tick:
            due, cx, cy, arx, ary, acx, acy = h.pop(0)
            if o.tick - due > 3:
                continue
            fx, fy = e.x, e.y
            m = self.m_err
            m[0] = m[0] * 0.85 + ((fx - cx) ** 2 + (fy - cy) ** 2) * 0.15
            m[1] = m[1] * 0.85 + ((fx - arx) ** 2 + (fy - ary) ** 2) * 0.15
            m[2] = m[2] * 0.85 + ((fx - acx) ** 2 + (fy - acy) ** 2) * 0.15

    def _best_model(self):
        m = self.m_err
        if m[1] < m[0] and m[1] < m[2]:
            return 1
        if m[2] < m[0]:
            return 2
        return 0

    def _predict(self, e, mx, my, model=None):
        if model is None:
            model = self._best_model()
        x, y, vx, vy = e.x, e.y, e.vx, e.vy
        om = self.e_omega
        ax, ay = self.e_ax, self.e_ay
        t = hypot(x - mx, y - my) / BULLET
        nx, ny = x, y
        for _ in range(2):
            if model == 1 and fabs(om) > 0.08 and hypot(vx, vy) > 30.0:
                a = atan2(vy, vx)
                da = om * t
                r = hypot(vx, vy) / om
                nx = x + r * (sin(a + da) - sin(a))
                ny = y - r * (cos(a + da) - cos(a))
            elif model == 2:
                nx = x + vx * t + 0.5 * ax * t * t
                ny = y + vy * t + 0.5 * ay * t * t
            else:
                nx = x + vx * t
                ny = y + vy * t
            t = hypot(nx - mx, ny - my) / BULLET
        return nx, ny, t

    # ------------------------------------------------------------- выстрел

    def _gun(self, o, e, me):
        if e is None:
            return self._gun_blind(o, me), False
        d = hypot(e.x - me.x, e.y - me.y)
        model = self._best_model()
        mx0 = me.x + cos(me.turret) * MUZZLE
        my0 = me.y + sin(me.turret) * MUZZLE
        px, py, tf = self._predict(e, mx0, my0, model)
        phull = e.hull + clamp(self.e_omega, -HULL_TURN, HULL_TURN) * min(tf, 0.75)

        if d < POINT_BLANK:
            err = o.aim_error(px, py)
            step = o.bullet_turn_rate * o.dt
            cmd = clamp(err / step, -1.0, 1.0)
            return cmd, (me.ammo_ready and fabs(err - cmd * step) < 0.02)

        aim, lo, hi, marg_a, marg_over = self._pen_band(o, me, px, py, phull,
                                                        d, tf)
        slack = 0.0
        if e.hp <= FINISH_HP or o.time > URGENT_T:
            slack = END_SLACK        # ничья — не победа: бьём и «на грани»
        if aim is None:
            if marg_a is None or marg_over > slack:
                return o.aim_turret(px, py), False
            a = marg_a
            for _ in range(2):
                mzx = me.x + cos(a) * MUZZLE
                mzy = me.y + sin(a) * MUZZLE
                a = atan2(py - mzy, px - mzx)
            dx, dy = cos(a), sin(a)
            hit = ray_obb(mzx, mzy, dx, dy, px, py, HX, HY, phull)
            tt = hit[0] if hit is not None else d
            aim = (mzx + dx * tt, mzy + dy * tt)
            lo = hi = a
        a = atan2(aim[1] - me.y, aim[0] - me.x)
        for _ in range(2):
            mzx = me.x + cos(a) * MUZZLE
            mzy = me.y + sin(a) * MUZZLE
            a = atan2(aim[1] - mzy, aim[0] - mzx)
        err = wrap(a - me.turret)
        step = o.bullet_turn_rate * o.dt
        cmd = clamp(err / step, -1.0, 1.0)
        residual = fabs(err - cmd * step)

        mzx = me.x + cos(a) * MUZZLE
        mzy = me.y + sin(a) * MUZZLE
        dd = hypot(aim[0] - mzx, aim[1] - mzy)
        tol = min(0.55 * (hi - lo), atan2(13.0, dd) if dd > 40.0 else 0.3) - SPREAD_RAD
        if tol < 0.004:
            tol = 0.004
        if not me.ammo_ready or residual > tol:
            return cmd, False
        if self.nav is not None and (not self.nav.free(mzx, mzy)
                                     or self.nav.los_blocked(mzx, mzy, aim[0], aim[1])):
            return cmd, False

        # темповый гейт
        err_px = sqrt(self.m_err[model])
        foe_empty = not e.ammo_ready
        err_ok = ERR_EMPTY if foe_empty else ERR_READY
        tf_ok = TF_EMPTY if foe_empty else TF_READY
        if e.hp <= FINISH_HP:
            err_ok += 18.0
            tf_ok += 0.2
        if self.hp_low and not foe_empty:
            tf_ok -= 0.12
        # враг быстро крутится — корпус уйдёт из-под удара
        if degrees(fabs(self.e_omega)) * tf > 20.0 and not foe_empty \
                and e.hp > FINISH_HP:
            return cmd, False
        # если выстрелы систематически уходят в рикошет — строже
        err_ok *= 1.0 - 0.35 * self.waste_ema
        if tf > tf_ok or err_px > err_ok:
            return cmd, False
        self._log_shot(o, me, a, px, py, e)
        self.fired_tick = o.tick
        return cmd, True

    def _pen_band(self, o, me, px, py, phull, d, tf):
        """Скан направлений через силуэт: самая широкая пробивающая полоса."""
        bearing = atan2(py - me.y, px - me.x)
        margin = SPREAD_DEG + 1.4 + degrees(0.5 * fabs(self.e_omega)
                                            * min(tf, 0.75))
        phi = fabs(degrees(wrap(atan2(me.y - py, me.x - px) - phull)))
        if phi > 90.0:
            phi = 180.0 - phi
        # у границы мёртвой зоны манёвр цели легко выталкивает удар в рикошет
        bmin = min(fabs(phi - 30.0), fabs(phi - 40.0), fabs(phi - 50.0),
                   fabs(phi - 140.0))
        if bmin < 12.0:
            margin += 12.0
        if margin > 13.0:
            margin = 13.0
        half_ang = atan2(26.0, max(d, 60.0))
        N = 11
        good = []
        marg = None            # (превышение порога, угол) — лучший «почти»
        mid = (N - 1) / 2.0
        for k in range(N):
            a = bearing - half_ang + 2.0 * half_ang * k / (N - 1)
            dx, dy = cos(a), sin(a)
            hit = ray_obb(me.x + dx * MUZZLE, me.y + dy * MUZZLE, dx, dy,
                          px, py, HX, HY, phull)
            if hit is None:
                good.append(None)
                continue
            theta = theta_of(hit[1], hit[2], dx, dy)
            lim = face_limit(hit[1], hit[2], phull)
            over = theta + margin - lim
            if marg is None or over < marg[0]:
                marg = (over, a)
            good.append(a if over < 0.0 else None)
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
            key = (j - k, -abs(m - mid))
            if best is None or key > best[0]:
                best = (key, k, j)
            k = j + 1
        if best is None:
            if marg is None:
                return None, 0.0, 0.0, None, 99.0
            return None, 0.0, 0.0, marg[1], marg[0]
        _, k, j = best
        a = good[(k + j) // 2]
        dx, dy = cos(a), sin(a)
        hit = ray_obb(me.x + dx * MUZZLE, me.y + dy * MUZZLE, dx, dy,
                      px, py, HX, HY, phull)
        tt = hit[0] if hit is not None else d
        return ((me.x + dx * (MUZZLE + tt), me.y + dy * (MUZZLE + tt)),
                good[k], good[j], None, 0.0)

    def _log_shot(self, o, me, a, cx, cy, e):
        mx = me.x + cos(a) * MUZZLE
        my = me.y + sin(a) * MUZZLE
        dd = hypot(cx - mx, cy - my)
        due = o.tick + max(2, int(dd / BULLET * 60.0))
        self.shots_log.append((due, mx, my, cos(a), sin(a), cx, cy, e.hp))
        if len(self.shots_log) > 6:
            del self.shots_log[0]

    def _watch_shots(self, o, e):
        """Что случилось с моим выстрелом: попал / рикошет / промах."""
        log = self.shots_log
        while log and log[0][0] <= o.tick:
            due, mx, my, dx, dy, cx, cy, hp0 = log.pop(0)
            if e is None or o.tick - due > 4:
                continue
            if e.hp < hp0 - 0.5:
                self.hit_ema = self.hit_ema * 0.7 + 0.3
                self.waste_ema *= 0.6
            else:
                self.hit_ema *= 0.7
                self.waste_ema = self.waste_ema * 0.75 + 0.25

    def _gun_blind(self, o, me):
        if self.seen and (self.t - self.et) < HUNT_FRESH:
            age = self.t - self.et
            return o.aim_turret(self.ex + self.evx * age * 0.6,
                                self.ey + self.evy * age * 0.6)
        return o.aim_turret(me.x + cos(me.hull) * 200.0,
                            me.y + sin(me.hull) * 200.0)

    # ------------------------------------------------------------- угроза

    def _threat(self, o, e, me):
        """Ловим выстрел врага по скачку его перезарядки."""
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
        else:
            self.prev_cd = None

        th = self.threat
        if th is None:
            return None
        bx, by, dx, dy, t0 = th
        age = self.t - t0
        if age > 1.7:
            self.threat = None
            self.threat_hot = False
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
        lim = DODGE_MISS + 20.0 if self.threat_hot else DODGE_MISS
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
            side = 1.0 if (dx * me.vy - dy * me.vx) >= 0.0 else -1.0
        ex, ey = -dy * side, dx * side
        if self.nav is not None and self.nav.clearance(me.x + ex * 46.0,
                                                       me.y + ey * 46.0) < 20.0:
            ex, ey = -ex, -ey
        ux, uy = unit(ex - dx * 0.35, ey - dy * 0.35)
        self.threat_hot = True
        return (ux, uy, t_star, miss, dx, dy)

    # ------------------------------------------------------------- движение

    def _engage(self, o, me, e):
        dx = e.x - me.x
        dy = e.y - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d, dx, dy = 1e-6, 1.0, 0.0
        ux, uy = dx / d, dy / d
        bearing = atan2(dy, dx)
        me_ready = me.ammo_ready
        foe_ready = e.ammo_ready or e.cooldown < 0.32
        t = self.t
        los_clear = not (self.nav and self.nav.los_blocked(me.x, me.y, e.x, e.y))

        # --- смена стороны ракурса: только когда безопасно -----------------
        if t > self.side_until:
            safe = (not foe_ready) or d > 540.0 or not los_clear
            forced = t > self.side_until + 4.0 and (not foe_ready or d > 360.0)
            if safe or forced:
                self.side = -self.side
                self.side_until = t + SIDE_MIN + self._rand() * (SIDE_MAX - SIDE_MIN)
            else:
                self.side_until = t + 0.35

        # --- целевая дистанция --------------------------------------------
        if not foe_ready:
            want_d = PRESS_RANGE if me_ready else STANDOFF
        elif not me_ready:
            want_d = FAR_RANGE
        else:
            want_d = STANDOFF
        if e.hp <= FINISH_HP and me_ready:
            want_d = 150.0
        if self.hp_low and foe_ready and e.hp > FINISH_HP:
            want_d = FAR_RANGE + 60.0
        if d < RAM_SAFE and not (e.hp <= 0.4):
            want_d = max(want_d, RAM_SAFE + 40.0)

        # --- джиттер газом -------------------------------------------------
        if t > self.gear_until:
            u = self._rand()
            self.gear_until = t + GEAR_MIN + (GEAR_MAX - GEAR_MIN) * u
            if d > want_d + 40.0:
                self.gear = 1
            elif d < want_d - 40.0:
                self.gear = -1
            elif u > 0.5:
                self.gear = -self.gear
        if (not foe_ready) and me_ready and d > want_d:
            self.gear = 1

        jit = ANGLE_JIT * sin(t * 2.3 + self.side)
        ang = ANGLE_LOCK + jit
        if d < 200.0:
            ang += radians(2.5)      # ближний лаг отслеживания съедает ракурс
        hull_target = (self._armor_ref(o, me, e, bearing, d)
                       + self.bear_omega * BEAR_LEAD + self.side * ang)
        vhx, vhy = cos(hull_target), sin(hull_target)
        u2 = (vhx * self.gear, vhy * self.gear)
        u2 = self._wall_fix(o, me, u2)
        if u2 is None:
            return self._open_spin(o, me), 0.2
        err = wrap(hull_target - me.hull)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        return turn, self.gear * self._throttle(err)

    def _throttle(self, err):
        ae = fabs(err)
        thr = 0.96
        if ae > 0.09:
            thr = 0.96 - 2.4 * (ae - 0.09)
        om = fabs(self.bear_omega)
        cap = HULL_TURN / (om + 0.45) - 1.0
        if cap < thr:
            thr = cap
        return thr if thr > 0.0 else 0.0

    def _armor_ref(self, o, me, e, bearing, d):
        tf = d / BULLET
        return atan2(me.y + me.vy * tf - e.y, me.x + me.vx * tf - e.x) + pi

    def _dodge(self, o, me, threat):
        """Уход от снаряда: борт с линии + ракурс к линии снаряда."""
        ux, uy, t_star, miss, bdx, bdy = threat
        base = atan2(-bdy, -bdx) + self.side * ANGLE_LOCK
        hx, hy = cos(base), sin(base)
        dot = ux * hx + uy * hy
        err = wrap(base - me.hull)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        gear = 1.0 if dot >= 0.0 else -1.0
        return turn, gear

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
        wx, wy = self.nav.route(me.x, me.y, gx, gy, t)
        u = unit(wx - me.x, wy - me.y)
        if u == (0.0, 0.0):
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
        # первые секунды: идём к чужому спавну, но корпус держим под ракурсом
        # к направлению на спавн — контакт обычно случается на этом марше
        if not fresh and self.t < 6.0 and o.map is not None and o.map.spawns:
            sp = o.map.spawns[1 - o.tank]
            hb = atan2(sp.y - me.y, sp.x - me.x)
            hull_target = hb + self.side * ANGLE_LOCK
            err = wrap(hull_target - me.hull)
            turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
            gear = 1.0 if (u[0] * cos(hull_target) + u[1] * sin(hull_target)) >= 0.0 else -1.0
            return turn, gear * self._throttle(err)
        err = wrap(want - me.hull)
        return clamp(err * 2.4, -1.0, 1.0), (0.95 if fabs(err) < 2.1 else -0.45)

    def _patrol_goal(self, o, me):
        spawns = o.map.spawns if o.map is not None else None
        if self.t < 6.0 and spawns and not self.seen:
            sp = spawns[1 - o.tank]
            return sp.x, sp.y
        pts = self.nav.patrol if self.nav else None
        if not pts:
            if spawns:
                sp = spawns[1 - o.tank]
                return sp.x, sp.y
            return me.x + cos(me.hull) * 200.0, me.y + sin(me.hull) * 200.0
        if self.patrol_k is None:
            self.patrol_k = min(range(len(pts)),
                                key=lambda i: hypot(pts[i][0] - me.x,
                                                    pts[i][1] - me.y))
        g = pts[self.patrol_k % len(pts)]
        if hypot(g[0] - me.x, g[1] - me.y) < 80.0:
            self.patrol_k += 1
            g = pts[self.patrol_k % len(pts)]
        return g

    def _wall_fix(self, o, me, u2):
        if self.nav is None:
            return u2
        px = me.x + u2[0] * 56.0
        py = me.y + u2[1] * 56.0
        if self.nav.free(px, py) and self.nav.clearance(px, py) >= 20.0:
            return u2
        alt = (-u2[0], -u2[1])
        px = me.x + alt[0] * 56.0
        py = me.y + alt[1] * 56.0
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

    def _watch_stuck(self, o, me):
        self.mile += hypot(me.x - self.px, me.y - self.py)
        self.px, self.py = me.x, me.y
        self.stuck_t += o.dt
        if self.stuck_t >= STUCK_WINDOW:
            if self.mile < STUCK_MILES and fabs(self.cmd_drive) > 0.3 \
                    and self.t >= self.escape_until:
                self.escape_side = 1.0 if self._rand() > 0.5 else -1.0
                self.escape_until = self.t + 0.6
                self.gear = 1
                self.gear_until = self.t + 0.7
                if self.nav:
                    self.nav.path = None
                self.patrol_k = None
            self.mile = 0.0
            self.stuck_t = 0.0

    # ------------------------------------------------------------- такт

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"tank": o.tank, "seed": getattr(self, "_seed", 0)})
        self.last_tick = o.tick
        me = o.me
        self.t = o.time
        self.hp_low = me.hp <= DMG
        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)
        e = o.enemy
        if e is not None:
            self._see(o, e)
            self._score_models(o, e)
        self._watch_stuck(o, me)
        self._watch_shots(o, e)

        threat = self._threat(o, e, me)
        turret, fire = self._gun(o, e, me)

        if self.t < self.escape_until:
            self.cmd_drive = -0.9
            return Action(drive=-0.9, turn=self.escape_side * 0.9,
                          turret=turret, fire=fire)
        if threat is not None:
            turn, drive = self._dodge(o, me, threat)
        elif e is not None:
            turn, drive = self._engage(o, me, e)
        else:
            turn, drive = self._hunt(o, me)
        self.cmd_drive = drive
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)


program = Brain()
