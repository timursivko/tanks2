#!TANKP 1
# name: Raptor
# author: arena-alpha
# difficulty: 5
# color: #00e5a0
# description: Карусель на 260 px, угол брони, уклонение от выстрела по стволу врага; огонь только когда снаряд точно пробьёт броню.
# tags: прицел,уклонение,дистанция,манёвр

import os
from math import acos, atan2, cos, degrees, fabs, hypot, pi, radians, sin
from heapq import heappush, heappop

from tankp import TankProgram, Action

DEBUG = os.environ.get("RAPTOR_DEBUG") == "1"

TAU = 6.283185307179586

# --- геометрия выстрела ------------------------------------------------------
# Пороги рикошета по граням (угол к нормали, градусы) — копия config.Balance:
# front 30, side 50, rear 60. Удар под большим углом к нормали не наносит
# урона, поэтому такой выстрел — потраченная перезарядка.
RICO = (30.0, 50.0, 60.0)
FACE_FRONT_HALF = 35.0
HL = 20.0                       # hull_len/2 + bullet_radius
HW = 14.0                       # hull_wid/2 + bullet_radius
MUZZLE = 26.0
BULLET_SPEED = 620.0
HULL_TURN = 2.6

# --- настройки боя (подобраны на локальном турнире против ai/) ----------------
NEAR = 140.0          # ближе — отходим
FAR = 520.0           # дальше — сближаемся
STANDOFF = 260.0      # радиус карусели
STRICT = 0.6         # доля поперечника цели, которую считаем «точно»
RICO_MARGIN = 3.0     # запас в градусах к порогу рикошета
WANDER_K = 0.0        # вес неопределённости хода цели при решении о выстреле
WANDER_MAX = 30.0     # предел этой неопределённости, px
RICO_GATE = True      # не стрелять, если удар по геометрии уйдёт в рикошет
DODGE = True          # уклоняться от выпущенного снаряда
TEMPO = True          # играть по перезарядке врага: давить, пока он пуст
HIDE_RANGE = 480.0    # при перезарядке разрываем контакт до этой дистанции
KILL_RANGE = 230.0    # с готовым патроном и пустым врагом подходим сюда
TRADE_RANGE = 340.0   # оба готовы: держим эту дистанцию
CLOSE_RANGE = 90.0   # ближе — особый режим вплотную
CLOSE_MODE = "back" # "circle" — кружим, "face" — доворачиваем нос, "back" — пятимся
JINK_TICKS = 38       # период смены стороны карусели
GUARD = 0.44           # «угол брони»: держим нос под таким углом к врагу (0 — выкл.)
GUARD_RANGE = 380.0   # ближе этого — переходим на угол брони
GUARD_STEP = 150.0    # насколько вперёд смотрит точка ведения при угле брони
THREAT_TICKS = 30     # сколько тиков «дрожать», когда враг навёл ствол
EVADE_TICKS = 40      # сколько тиков уходить вбок после его выстрела
HUNT_AFTER = 90       # тиков без цели до перехода на поиск


def wrap(a):
    a = (a + pi) % TAU
    return a - pi


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def entry_of(px, py, dx, dy, cx, cy, hull, hl=HL, hw=HW):
    """Первая грань чужого корпуса на пути луча — как segment_obb движка.

    Возвращает (t, face_index, theta_deg): face 0 — лоб, 1 — борт, 2 — корма,
    theta — угол между ходом снаряда и наружной нормалью грани. Маленький
    угол — пробитие, большой — рикошет.
    """
    c = cos(hull)
    s = sin(hull)
    rx = px - cx
    ry = py - cy
    lx = rx * c + ry * s
    ly = -rx * s + ry * c
    ldx = dx * c + dy * s
    ldy = -dx * s + dy * c

    tmin = 0.0
    axis = -1
    sign = 0.0
    for i, (p, q, h) in enumerate(((lx, ldx, hl), (ly, ldy, hw))):
        if fabs(q) < 1e-12:
            if fabs(p) > h:
                return None
            continue
        s_ = -1.0 if q > 0 else 1.0
        t1 = (-h - p) / q
        t2 = (h - p) / q
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = i
            sign = s_
    if axis < 0:
        if fabs(ldx) >= fabs(ldy):
            axis, sign = 0, (-1.0 if ldx > 0 else 1.0)
        else:
            axis, sign = 1, (-1.0 if ldy > 0 else 1.0)
    nx = sign if axis == 0 else 0.0
    ny = sign if axis == 1 else 0.0
    wx = nx * c - ny * s
    wy = nx * s + ny * c
    rel = degrees(wrap(atan2(wy, wx) - hull))
    arel = rel if rel >= 0.0 else -rel
    if arel <= FACE_FRONT_HALF:
        face = 0
    elif arel >= 180.0 - FACE_FRONT_HALF:
        face = 2
    else:
        face = 1
    dot = dx * wx + dy * wy
    cos_t = -dot if dot < 0.0 else 0.0
    if cos_t > 1.0:
        cos_t = 1.0
    return (tmin, face, degrees(acos(cos_t)))


def penetrates(theta, face, margin=2.0):
    return theta + margin < RICO[face]


def cross_half(dx, dy, hull, hl=HL, hw=HW):
    """Полуширина проекции корпуса на направление, перпендикулярное лучу."""
    px, py = -dy, dx
    c = cos(hull)
    s = sin(hull)
    return hl * fabs(px * c + py * s) + hw * fabs(-px * s + py * c)


def octile(dx, dy):
    ax = dx if dx >= 0 else -dx
    ay = dy if dy >= 0 else -dy
    if ax > ay:
        return ax + 0.414 * ay
    return ay + 0.414 * ax


class Grid:
    """Сетка карты и A* по тайлам: нужна, чтобы не застревать за стенами."""

    __slots__ = ("w", "h", "tile", "passable", "clear")

    def __init__(self, mv):
        self.w = mv.width
        self.h = mv.height
        self.tile = mv.tile_size
        rows = mv.rows
        self.passable = [c not in "#o:" for r in rows for c in r]
        cl = mv.clearance_grid
        self.clear = [cl[y][x] * mv.tile_size for y in range(self.h) for x in range(self.w)]

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

    def clearance(self, x, y):
        tx, ty = self.ti(x, y)
        return self.clear[ty * self.w + tx]

    def free(self, x, y):
        tx, ty = self.ti(x, y)
        return self.passable[ty * self.w + tx]

    def astar(self, sx, sy, gx, gy, max_nodes=3000):
        """Путь списком точек (тайловые центры), [] если пути нет."""
        s = self._nearest_free(*self.ti(sx, sy))
        g = self._nearest_free(*self.ti(gx, gy))
        if s is None or g is None:
            return []
        w = self.w
        start = s[1] * w + s[0]
        goal = g[1] * w + g[0]
        if start == goal:
            return [(gx, gy)]
        openv = [(0.0, start)]
        came = {}
        gs = {start: 0.0}
        closed = set()
        n = 0
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
                if nx < 0 or ny < 0 or nx >= w or ny >= self.h:
                    continue
                idx = ny * w + nx
                if not self.passable[idx]:
                    continue
                if ox and oy and not (self.passable[cy * w + nx] and self.passable[ny * w + cx]):
                    continue
                # Тесные клетки дороже: танк шириной с тайл застревает в углах.
                extra = 0.9 if self.clear[idx] < self.tile * 1.4 else 0.0
                ng = gs[cur] + cost + extra
                if ng < gs.get(idx, 1e18):
                    gs[idx] = ng
                    came[idx] = cur
                    heappush(openv, (ng + octile(nx - g[0], ny - g[1]), idx))
        if goal not in came and goal != start:
            return []
        path = []
        cur = goal
        while cur != start:
            path.append(((cur % w + 0.5) * self.tile, (cur // w + 0.5) * self.tile))
            cur = came.get(cur)
            if cur is None:
                return []
        path.reverse()
        if path:
            path[-1] = (gx, gy)
        return path

    def _nearest_free(self, tx, ty):
        if self.passable[ty * self.w + tx]:
            return (tx, ty)
        for r in (1, 2):
            best = None
            for oy in range(-r, r + 1):
                for ox in range(-r, r + 1):
                    nx = tx + ox
                    ny = ty + oy
                    if 0 <= nx < self.w and 0 <= ny < self.h and self.passable[ny * self.w + nx]:
                        d = ox * ox + oy * oy
                        if best is None or d < best[0]:
                            best = (d, nx, ny)
            if best:
                return (best[1], best[2])
        return None


class Brain(TankProgram):
    """Три режима: бой, погоня и выход из угла."""

    LEAD_FACTOR = 1.0

    def on_start(self, ctx):
        self.stat = {} if DEBUG else None
        self.grid = None
        self.lst = None            # (x, y) последний контакт
        self.lst_tick = -10000
        self.prev_foe = None       # (x, y, turret, ammo_ready)
        self.hist = []             # (tick, x, y, hull, vx, vy) врага
        self.orbit = 1.0
        self.orbit_tick = 0
        self.evade = 0.0
        self.evade_until = -1
        self.path = []
        self.path_goal = None
        self.stuck = 0
        self.last_me = None
        self.scan = 1.0
        self.fired_tick = -1000

    def _bump(self, k):
        if self.stat is not None:
            self.stat[k] = self.stat.get(k, 0) + 1

    # ------------------------------------------------------------------ тик
    def on_tick(self, o):
        me = o.me
        if self.grid is None:
            self.grid = Grid(o.map)
        foe = o.enemy
        if foe is not None:
            self.lst = (foe.x, foe.y)
            self.lst_tick = o.tick
            self._remember(o, foe)
            turret, fire = self._aim(o, me, foe)
            turn, drive = self._move(o, me, foe)
            self.prev_foe = (foe.x, foe.y, foe.turret, foe.ammo_ready)
        else:
            turret = self._scan_turret(o)
            turn, drive = self._hunt(o, me, o.tick)
            fire = False
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # ------------------------------------------------------------- память
    def _remember(self, o, foe):
        h = self.hist
        h.append((o.tick, foe.x, foe.y, foe.hull, foe.vx, foe.vy))
        while h and o.tick - h[0][0] > 45:
            h.pop(0)

    def _hull_rate(self, tick):
        """Скорость поворота корпуса цели, рад/с (по последним ~0.25 с)."""
        h = self.hist
        if len(h) < 4:
            return 0.0
        t0, _, _, h0, _, _ = h[0]
        t1, _, _, h1, _, _ = h[-1]
        dt = (t1 - t0) / 60.0
        if dt < 0.1:
            return 0.0
        r = wrap(h1 - h0) / dt
        return clamp(r, -HULL_TURN, HULL_TURN)

    def _accel(self, tick):
        """Оценка манёвра цели: насколько быстро меняется её скорость, px/с²."""
        h = self.hist
        if len(h) < 4:
            return 0.0
        t0, _, _, _, vx0, vy0 = h[0]
        t1, _, _, _, vx1, vy1 = h[-1]
        dt = (t1 - t0) / 60.0
        if dt < 0.1:
            return 0.0
        return hypot(vx1 - vx0, vy1 - vy0) / dt

    # ------------------------------------------------------------- стрельба
    def _solution(self, o, me, foe):
        """Предсказание попадания: (turret_cmd, fire, aim, hit).

        Точка прицеливания — упреждённая позиция центра цели. Грань и угол
        удара считаются по предсказанному на время полёта курсу корпуса цели:
        за 0.4 с боя она успевает повернуться на десятки градусов, и без
        этого предсказания «пробитие» в момент выстрела оборачивается
        рикошетом в момент удара.
        """
        aim = o.lead(foe, factor=self.LEAD_FACTOR)
        mx, my = o.muzzle()
        err = o.aim_error(aim)
        turret = clamp(err * 4.0, -1.0, 1.0)
        residual = fabs(err - turret * o.bullet_turn_rate * o.dt)
        if not me.ammo_ready:
            self._bump("reload")
            return turret, False, aim, None
        ddx = aim[0] - mx
        ddy = aim[1] - my
        dist = hypot(ddx, ddy)
        if dist < 1e-6:
            return turret, False, aim, None
        ddx /= dist
        ddy /= dist
        t_fly = dist / BULLET_SPEED
        phull = foe.hull + self._hull_rate(o.tick) * t_fly
        tx = foe.x + foe.vx * t_fly
        ty = foe.y + foe.vy * t_fly
        hit = entry_of(mx, my, ddx, ddy, tx, ty, phull)
        if hit is None:
            self._bump("no_hit")
            return turret, False, aim, None
        if RICO_GATE and not penetrates(hit[2], hit[1], RICO_MARGIN):
            self._bump("rico%d" % hit[1])
            return turret, False, aim, hit
        if o.map.blocked_between(mx, my, aim[0], aim[1]):
            self._bump("wall")
            return turret, False, aim, hit
        wander = WANDER_K * min(self._accel(o.tick), 460.0) * t_fly * t_fly
        if wander > WANDER_MAX:
            wander = WANDER_MAX
        budget = cross_half(ddx, ddy, phull) * STRICT
        budget -= radians(o.spread_deg) * dist
        budget -= wander
        if budget <= 0.0:
            self._bump("far")
            return turret, False, aim, hit
        if residual > atan2(budget, dist):
            self._bump("slew")
            return turret, False, aim, hit
        self._bump("fire")
        self.fired_tick = o.tick
        return turret, True, aim, hit

    def _aim(self, o, me, foe):
        turret, fire, aim, hit = self._solution(o, me, foe)
        return turret, fire

    # -------------------------------------------------------------- уклонение
    def _threat(self, o, me, foe):
        """Снаряд в нас: линия полёта известна по стволу врага.

        Враг выстрелил на прошлом такте — значит, прямо сейчас в мире уже
        летит снаряд, выпущенный из его ствола в том положении и с тем курсом,
        которые мы видим в этом наблюдении. Линия полёта известна с точностью
        до разброса ствола (±0.4°), поэтому уклоняться можно вбок от неё.
        """
        pf = self.prev_foe
        if DODGE and pf is not None and pf[3] and not foe.ammo_ready:
            mx = foe.x + cos(foe.turret) * MUZZLE
            my = foe.y + sin(foe.turret) * MUZZLE
            dx = cos(foe.turret)
            dy = sin(foe.turret)
            rx = me.x - mx
            ry = me.y - my
            along = rx * dx + ry * dy
            perp = -rx * dy + ry * dx
            t_hit = along / BULLET_SPEED
            if along > -10.0 and t_hit < 1.2 and fabs(perp) < 52.0:
                side = 1.0 if perp > 0.0 else -1.0
                self.evade = side
                self.evade_until = o.tick + int(60.0 * (t_hit + 0.18))
                self._bump("dodge")
                return True
        # Враг заряжен и башня смотрит на нас — не даём себя вести по прямой.
        if foe.ammo_ready and o.tick > self.evade_until:
            bearing = atan2(me.y - foe.y, me.x - foe.x)
            if fabs(wrap(bearing - foe.turret)) < 0.12:
                self.evade_until = o.tick + THREAT_TICKS
                self._bump("threat")
        return False

    # -------------------------------------------------------------- движение
    def _cover_point(self, o, me, foe):
        """Точка у рядом стоящего укрытия: там линия огня врага нас не видит."""
        best = None
        for k in range(12):
            ang = k * 0.5236
            for dist in (80.0, 150.0):
                px = me.x + cos(ang) * dist
                py = me.y + sin(ang) * dist
                if not self.grid.free(px, py) or self.grid.clearance(px, py) < 26.0:
                    continue
                if not o.map.blocked_between(px, py, foe.x, foe.y):
                    continue
                score = self.grid.clearance(px, py) - dist
                if best is None or score > best[0]:
                    best = (score, px, py)
        return best

    def _move(self, o, me, foe):
        tick = o.tick
        self._threat(o, me, foe)
        d = me.dist_to(foe)
        my_ready = me.ammo_ready
        foe_ready = foe.ammo_ready

        # Сторона карусели меняется — по ней цель не предскажет наш ход.
        if tick - self.orbit_tick > JINK_TICKS:
            self.orbit = -self.orbit
            self.orbit_tick = tick

        rev = False
        if tick < self.evade_until:
            # Уходим вбок от линии огня, а не по прямой на сближение.
            side = self.evade if self.evade else 1.0
            ang = foe.turret + side * 1.4
            gx = me.x + cos(ang) * 200.0
            gy = me.y + sin(ang) * 200.0
            speed = 1.0
            rev = False
        elif TEMPO and not my_ready and foe_ready and d < HIDE_RANGE:
            # Патрон в перезарядке, а враг готов стрелять: рвём линию огня.
            cover = self._cover_point(o, me, foe)
            if cover is not None:
                gx, gy = cover[1], cover[2]
            else:
                ang = atan2(me.y - foe.y, me.x - foe.x)
                gx = me.x + cos(ang) * 220.0
                gy = me.y + sin(ang) * 220.0
            speed = 1.0
        else:
            bearing = atan2(foe.y - me.y, foe.x - me.x)
            if d < CLOSE_RANGE and CLOSE_MODE == "face":
                # Вплотную: доворачиваем НОС на врага, чтобы не подставлять борт.
                ang = bearing + self.orbit * 0.35
                gx = me.x + cos(ang) * 150.0
                gy = me.y + sin(ang) * 150.0
                speed = 0.5
            elif d < CLOSE_RANGE and CLOSE_MODE == "back":
                # Вплотную: пятимся, не отворачивая носа от врага.
                self._closing_fire = True
                return self._back_off(o, me, foe)
            elif d < CLOSE_RANGE:
                # Вплотную: кружим на малом радиусе — нос всё время на врага.
                ang = atan2(me.y - foe.y, me.x - foe.x) + self.orbit * 1.2
                gx = me.x + cos(ang) * 200.0
                gy = me.y + sin(ang) * 200.0
                speed = 1.0
            elif GUARD > 0.0 and d < GUARD_RANGE and d > 60.0:
                # «Угол брони»: держим НОС под углом к линии на врага. Прямой
                # удар по центру под углом 30..40° рикошетит и по лбу, и по
                # борту, а карусель при этом не даёт вести нас по прямой.
                bearing2 = atan2(foe.y - me.y, foe.x - me.x)
                want = bearing2 + self.orbit * GUARD
                gx = me.x + cos(want) * GUARD_STEP
                gy = me.y + sin(want) * GUARD_STEP
                speed = 0.95
            elif d > FAR:
                gx, gy = foe.x, foe.y
                speed = 1.0
            elif TRADE_RANGE > 0.0 and my_ready and not foe_ready and d > KILL_RANGE:
                # Враг пуст — подходим на дистанцию верного удара.
                bearing2 = atan2(me.y - foe.y, me.x - foe.x)
                gx = foe.x + cos(bearing2) * KILL_RANGE
                gy = foe.y + sin(bearing2) * KILL_RANGE
                speed = 1.0
            elif d < NEAR:
                ang = atan2(me.y - foe.y, me.x - foe.x)
                gx = me.x + cos(ang) * 200.0
                gy = me.y + sin(ang) * 200.0
                speed = 0.8
            else:
                # Карусель: точка на окружности вокруг врага с учётом просторов.
                best = None
                for k in (0.55, 0.85, 1.15):
                    ang = atan2(me.y - foe.y, me.x - foe.x) + self.orbit * k
                    gx = foe.x + cos(ang) * STANDOFF
                    gy = foe.y + sin(ang) * STANDOFF
                    c = self.grid.clearance(gx, gy) if self.grid else 100.0
                    score = c - 30.0 * k
                    if best is None or score > best[0]:
                        best = (score, gx, gy)
                gx, gy = best[1], best[2]
                speed = 0.95
        turn, drive = o.steer_to(gx, gy, tol=50.0)
        self._watch_stuck(o, me, tick, drive)
        return turn * 0.9, drive * speed

    def _back_off(self, o, me, foe):
        """Пятимся от врага, держа нос на нём (медленно, но без разворота)."""
        ang = atan2(foe.y - me.y, foe.x - me.x)
        turn, drive = o.steer_to(me.x + cos(ang) * 200.0, me.y + sin(ang) * 200.0,
                                 tol=40.0, reverse=True)
        self._watch_stuck(o, me, o.tick, drive)
        return turn * 0.9, drive

    def _watch_stuck(self, o, me, tick, drive):
        if self.last_me is None:
            self.last_me = (me.x, me.y, tick)
            return
        px, py, pt = self.last_me
        if tick - pt >= 30:
            if hypot(me.x - px, me.y - py) < 20.0 and fabs(drive) > 0.3:
                self.stuck = 14
            self.last_me = (me.x, me.y, tick)

    # ------------------------------------------------------------- поиск
    def _scan_turret(self, o):
        if self.lst is not None:
            return o.aim_turret(self.lst[0], self.lst[1])
        return self.scan

    def _hunt(self, o, me, tick):
        gx = gy = None
        if self.lst is not None and tick - self.lst_tick < 900:
            gx, gy = self.lst
            if me.dist_to((gx, gy)) < 70.0:
                self.lst = None
                gx = gy = None
        if gx is None:
            sx, sy = o.map.spawns[1 - o.tank]
            gx, gy = sx, sy
        if self.stuck > 0:
            self.stuck -= 1
            return 0.55, 0.6
        goal = self._path_to(o, me, gx, gy)
        if goal is None:
            goal = (gx, gy)
        turn, drive = o.steer_to(goal[0], goal[1], tol=45.0)
        self._watch_stuck(o, me, tick, drive)
        return turn, drive * 0.9

    def _path_to(self, o, me, gx, gy):
        if self.path:
            wx, wy = self.path[0]
            if hypot(wx - me.x, wy - me.y) < 52.0:
                self.path.pop(0)
                if self.path:
                    return self.path[0]
        if self.path_goal is None or hypot(gx - self.path_goal[0], gy - self.path_goal[1]) > 90.0:
            self.path_goal = (gx, gy)
            if o.map.blocked_between(me.x, me.y, gx, gy):
                self.path = self.grid.astar(me.x, me.y, gx, gy)
                if not self.path:
                    self.path = []
            else:
                self.path = []
        if self.path:
            return self.path[0]
        return (gx, gy)


program = Brain()
