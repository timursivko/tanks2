#!TANKP 1
# name: Shrike
# author: arena-438319c3
# difficulty: 5
# color: #ff4d6d
# description: Держит мёртвую зону 35° к СТВОЛУ врага (снаряд летит ровно по
#   стволу, поэтому чужие упреждения и сканы полосы бесполезны), а стреляет на
#   рубеже 130-210 px, где угловой размер корпуса открывает полосу пробития за
#   краем мёртвой зоны врага.
# tags: ракурс,рикошет,дистанция,геометрия

"""Shrike — танк «мёртвой зоны к стволу».

Броня здесь не режет урон, она рикошетит: удар под 30..40° к нормали грани
отскакивает и от лба (порог 30°), и от борта (порог 50°). Поэтому вся защита —
держать корпус под 35° к линии, по которой придёт снаряд.

Линия прихода берётся от СТВОЛА противника, а не от линии визирования.
Снаряд вылетает из дула строго по стволу (±0.4° разброса), поэтому корпус,
стоящий под 35° к стволу, отбивает ЛЮБОЙ его выстрел: с упреждением, со
сканом пробивающей полосы, с промахом прогноза — угол удара всё равно 35°.
Ракурс к линии визирования так не умеет: как только враг целится не в нас,
а в упреждённую точку или в край силуэта, удар выходит из мёртвой зоны.

Стрельба — на 130..210 px. Корпус — коробка, и луч, отклонившийся от её оси
больше чем на 5°, ещё попадает в цель, но приходит уже за краем мёртвой зоны:
угловой полуразмер корпуса 20 px на дистанции d — это atan(20/d). На 400 px
это 2.9° (ракурс не пробить), на 180 px — 6.3° (полоса есть), на 130 px —
8.7°. Урон от дистанции не зависит, поэтому рубеж выбирается по геометрии:
своя защита непробиваема всегда, чужая — только издалека.
"""

from math import atan2, cos, sin, hypot, pi, radians, fabs
from tankp import TankProgram, Action, clamp, wrap

TAU = pi * 2.0
INF = float("inf")
DEG = 180.0 / pi
COS = cos

# --- константы движка (копия config.Balance) --------------------------------
BULLET = 620.0          # скорость снаряда, px/с
MUZZLE = 26.0           # вылет ствола от центра, px
TURRET_RATE = 3.6       # рад/с
HULL_RATE = 2.6         # рад/с
SPEED_F = 190.0
SPEED_R = 120.0
HP_MAX = 10.0
DMG = 4.0
RELOAD = 1.0
SPREAD = 0.4            # ± градусов
VIEW = 760.0
VIEW_SQ = VIEW * VIEW
THX = 20.0              # полуразмер коробки цели (корпус/2 + радиус снаряда)
THY = 14.0
FACE_HALF = 35.0        # ±35° от курса — лоб
RICO_FRONT = 30.0       # пороги рикошета: угол к нормали грани
RICO_SIDE = 50.0
RICO_REAR = 60.0

# --- настройки боя ----------------------------------------------------------
DEAD_DEG = 35.0
DEAD = radians(DEAD_DEG)
TRACK_GAIN = 6.0
GAS_DROP_DEG = 7.0              # на сколько градусов ошибки бросить газ
FIRE_MARGIN = 1.5               # запас до порога рикошета, градусы
FIRE_MARGIN_EASY = 0.5          # запас, когда враг пуст (ответить не может)
TF_MAX = 1.2
TUR_NEAR = 0.62                 # ствол считается наведённым на нас, рад

D_BRAWL = 40.0                  # рубеж боя: дуло внутри чужого корпуса
D_CLOSE = 46.0                  # дальше дуло снаружи — полосы пробития нет
MIN_BAND = 2                    # сколько лучей подряд нужно, чтобы стрелять издали
D_RETREAT_FAR = 620.0           # враг готов, а мы пусты — разрываем
GHOST_TIME = 2.0
HUNT_FRESH = 5.0
STUCK_WINDOW = 0.6
STUCK_DIST = 14.0


def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang, max_t=INF):
    """Луч против повёрнутой коробки: (t, nx, ny) или None.

    Копия engine.geometry.segment_obb: вход по ближней грани; если луч
    стартует внутри коробки (в упор дуло уже внутри чужого корпуса), удар
    регистрируется сразу, а нормаль берётся по ведущей оси полёта.
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
    tmax = max_t
    axis = -1
    sign = 0.0
    for i, p, q, h in ((0, lx, ldx, hx), (1, ly, ldy, hy)):
        if q < 1e-12 and q > -1e-12:
            if p > h or p < -h:
                return None
            continue
        s = -1.0 if q > 0.0 else 1.0
        t1 = (-h - p) / q
        t2 = (h - p) / q
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = i
            sign = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    if axis < 0:
        if tmax < 0.0:
            return None
        if fabs(ldx) >= fabs(ldy):
            nx = -1.0 if ldx > 0.0 else 1.0
            ny = 0.0
        else:
            nx = 0.0
            ny = -1.0 if ldy > 0.0 else 1.0
        return 0.0, nx * ca - ny * sa, nx * sa + ny * ca
    if tmin > max_t:
        return None
    if axis == 0:
        nx, ny = sign, 0.0
    else:
        nx, ny = 0.0, sign
    return tmin, nx * ca - ny * sa, nx * sa + ny * ca


def pen_ok(nx, ny, dx, dy, hull, margin):
    """Пробьёт ли удар с нормалью (nx, ny) при полёте по (dx, dy).

    ``margin`` — сколько градусов до порога рикошета должно остаться.
    """
    nlen = hypot(nx, ny)
    if nlen > 0.0:
        nx /= nlen
        ny /= nlen
    c = -(dx * nx + dy * ny)
    if c > 1.0:
        c = 1.0
    elif c < -1.0:
        c = -1.0
    rel = wrap(atan2(ny, nx) - hull) * DEG
    arel = rel if rel >= 0.0 else -rel
    if arel <= FACE_HALF:
        limit = RICO_FRONT
    elif arel >= 180.0 - FACE_HALF:
        limit = RICO_REAR
    else:
        limit = RICO_SIDE
    lim = limit - margin
    if lim <= 0.0:
        return False
    if lim >= 90.0:
        return True
    return c > cos(lim * (pi / 180.0))


class Brain(TankProgram):
    """Мёртвая зона к стволу + стрельба в полосу пробития на своём рубеже."""

    def on_start(self, ctx):
        self.t = 0.0
        self.last_tick = -1
        self.grid = None
        self._me_x = 0.0
        self._me_y = 0.0

        # враг
        self.seen = False
        self.et = -99.0
        self.ex = 0.0
        self.ey = 0.0
        self.evx = 0.0
        self.evy = 0.0
        self.ehull = 0.0
        self.etur = 0.0
        self.e_hom = 0.0
        self.e_tur_rate = 0.0
        self.ehp = HP_MAX
        self.e_ammo = True
        self.e_cd = 0.0
        self.e_cd_prev = 0.0
        self.shot_t = -99.0
        self.shot_dir = 0.0
        self.shot_x = 0.0
        self.shot_y = 0.0
        self.shot_due = -99.0
        self.shot_aimed = False

        # свой ракурс
        self.side = 1.0
        self.gear = 1.0
        self.gear_until = 0.0

        # маршрут
        self.flow = None
        self.flow_goal = None
        self.flow_t = -9.0

        # застревание
        self.px = 0.0
        self.py = 0.0
        self.mile = 0.0
        self.stuck_t = -9.0
        self.escape_until = -9.0
        self.escape_turn = 0.0

    # -------------------------------------------------------------- память

    def _remember(self, o, e):
        t = o.time
        if e is None:
            return
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.3:
                self.e_hom = self.e_hom * 0.65 + clamp(
                    wrap(e.hull - self.ehull) / dt, -HULL_RATE, HULL_RATE) * 0.35
                self.e_tur_rate = self.e_tur_rate * 0.6 + clamp(
                    wrap(e.turret - self.etur) / dt, -TURRET_RATE, TURRET_RATE) * 0.4
            elif dt > 0.35:
                self.e_hom *= 0.5
                self.e_tur_rate *= 0.5
        cd = e.cooldown
        if self.e_cd_prev < 0.45 < cd:
            self.shot_t = t
            self.shot_dir = e.turret
            self.shot_x = e.x + cos(e.turret) * MUZZLE
            self.shot_y = e.y + sin(e.turret) * MUZZLE
            self.shot_due = t + hypot(self._me_x - self.shot_x,
                                      self._me_y - self.shot_y) / BULLET
            sdx = cos(e.turret)
            sdy = sin(e.turret)
            self.shot_aimed = fabs((self._me_x - self.shot_x) * sdy
                                   - (self._me_y - self.shot_y) * sdx) < 60.0
        self.e_cd_prev = cd
        self.ex = e.x
        self.ey = e.y
        self.evx = e.vx
        self.evy = e.vy
        self.ehull = e.hull
        self.etur = e.turret
        self.ehp = e.hp
        self.e_ammo = e.ammo_ready
        self.e_cd = cd
        self.et = t
        self.seen = True

    # ----------------------------------------------------------------- тик

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"tank": o.tank})
        self.last_tick = o.tick
        t = self.t = o.time
        me = o.me
        self._me_x = me.x
        self._me_y = me.y
        if self.grid is None and o.map is not None:
            self.grid = o.map
        e = o.enemy
        self._remember(o, e)

        if e is not None:
            turret, fire = self._gun(o, me, e)
            turn, drive = self._fight(o, me, e.x, e.y)
        elif self.seen and (t - self.et) < HUNT_FRESH:
            # врага не видно, но свежая память есть: держим ракурс к его
            # последнему курсу и идём туда — он снова попадёт в конус обзора
            age = t - self.et
            gx = self.ex + self.evx * age
            gy = self.ey + self.evy * age
            turret, fire = self._gun_ghost(o, me, gx, gy)
            turn, drive = self._fight(o, me, gx, gy)
            if not self._line_free(me.x, me.y, gx, gy):
                wp = self._waypoint(gx, gy)
                if wp is not None:
                    turn = clamp(wrap(atan2(wp[1] - me.y, wp[0] - me.x)
                                      - me.hull) * 2.2, -1.0, 1.0)
                    drive = 1.0
        else:
            # ни следа: щупаем пространство вращением корпуса (обзор ±90° от
            # курса) и крадёмся к его логову
            turret, fire = self._gun_blind(o, me)
            turn, drive = self._hunt(o, me)
        turn, drive = self._unstuck(o, me, turn, drive)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # -------------------------------------------------------------- защита

    def _arrival(self, me, ex, ey):
        """Куда смотрит линия прихода снаряда (направление полёта к нам)."""
        back = atan2(ey - me.y, ex - me.x)       # мой азимут на врага
        # его ствол: снаряд полетит ровно по нему
        tur = self.etur + clamp(self.e_tur_rate * 0.033, -0.12, 0.12)
        if fabs(wrap(tur + pi - back)) < TUR_NEAR:
            return tur                            # ствол наведён на нас
        return back + pi                          # ствол уехал: по азимуту

    def _fight(self, o, me, ex, ey):
        dx = ex - me.x
        dy = ey - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d = 1e-6
        t = self.t
        back = atan2(dy, dx)

        # Броня почти вся — дуло: на 40 px ствол уже внутри чужого корпуса, и
        # удар разбирается по ведущей оси полёта. Поэтому и воевать надо вплотную.
        if d > 52.0:
            gear = 1.0
        else:
            gear = (d - D_BRAWL) * 0.09
            if gear > 1.0:
                gear = 1.0
            elif gear < -0.8:
                gear = -0.8

        if t < self.shot_due and self.shot_aimed and self.shot_due - t < 0.45:
            # по нам летит снаряд: он идёт строго по стволу в момент выстрела,
            # поэтому ракурс держим к замороженной линии, а не к живой
            arr = self.shot_dir
        else:
            arr = self._arrival(me, ex, ey)
        # корпус — под 35° к линии прихода: спереди-сбоку, где не рикошетит
        tgt = wrap(arr + pi + self.side * DEAD)
        if self.grid is not None and not self._free(
                me.x + cos(tgt) * 58.0, me.y + sin(tgt) * 58.0):
            alt = wrap(arr + pi - self.side * DEAD)
            if self._free(me.x + cos(alt) * 58.0, me.y + sin(alt) * 58.0):
                tgt = alt
                self.side = -self.side
        err = wrap(tgt - me.hull)
        # опережение: цель корпуса едет вместе с линией визирования
        rvx = me.vx - self.evx
        rvy = me.vy - self.evy
        rr = dx * dx + dy * dy
        om = (dx * rvy - dy * rvx) / rr if rr > 1.0 else 0.0
        ff = clamp(om / HULL_RATE, -0.9, 0.9)
        turn = clamp(err * 7.5 + ff, -1.0, 1.0)
        drive = self.gear * 0.96
        p = fabs(err) / radians(GAS_DROP_DEG)
        if p > 1.0:
            p = 1.0
        if p > 0.0:
            drive *= 1.0 - p
        return turn, drive

    # -------------------------------------------------------------- пушка

    def _predict(self, e, mx, my):
        t = hypot(e.x - mx, e.y - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        return e.x + e.vx * t, e.y + e.vy * t, t

    def _hulls(self, e, tf):
        """Гипотезы курса чужого корпуса к моменту удара: (курс, запас)."""
        ext = wrap(e.hull + self.e_hom * tf)
        back_e = atan2(self._me_y - e.y, self._me_x - e.x)   # его азимут на нас
        side = 1.0 if wrap(e.hull - back_e) >= 0.0 else -1.0
        law = wrap(back_e + side * DEAD)
        return ((e.hull, 0.0), (ext, 1.5), (law, 2.5))

    def _band(self, me, px, py, phull, d, margin):
        """Самая широкая пробивающая полоса направлений и её середина.

        Полоса узкая: у корпуса, стоящего в мёртвой зоне, пробивается только
        край силуэта — отклонение от его оси на 5-10°. Поэтому шаг веера
        около градуса, а не «девять лучей».
        """
        bearing = atan2(py - me.y, px - me.x)
        half = atan2(36.0, d if d > 40.0 else 40.0)
        if half > 0.62:
            half = 0.62
        n = 41
        good = [0.0] * n
        for k in range(n):
            a = bearing - half + 2.0 * half * k / (n - 1)
            dx = cos(a)
            dy = sin(a)
            hit = ray_obb(me.x + dx * MUZZLE, me.y + dy * MUZZLE, dx, dy,
                          px, py, THX, THY, phull)
            if hit is None:
                continue
            if pen_ok(hit[1], hit[2], dx, dy, phull, margin):
                good[k] = a
        mid = (n - 1) * 0.5
        best = None
        k = 0
        while k < n:
            if good[k] == 0.0:
                k += 1
                continue
            j = k
            while j + 1 < n and good[j + 1] != 0.0:
                j += 1
            m = (k + j) * 0.5
            key = (j - k, -fabs(m - mid))
            if best is None or key > best[0]:
                best = (key, k, j)
            k = j + 1
        if best is None:
            return None
        _, k, j = best
        a = good[int((k + j) * 0.5)]
        dx = cos(a)
        dy = sin(a)
        mzx = me.x + dx * MUZZLE
        mzy = me.y + dy * MUZZLE
        hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, phull)
        tt = hit[0] if hit is not None else d
        if tt < 20.0:
            return (me.x + dx * d, me.y + dy * d, j - k + 1)
        return (mzx + dx * tt, mzy + dy * tt, j - k + 1)

    def _gun(self, o, me, e):
        mx = me.x + cos(me.turret) * MUZZLE
        my = me.y + sin(me.turret) * MUZZLE
        px, py, tf = self._predict(e, mx, my)
        d = hypot(px - me.x, py - me.y)
        if d <= D_CLOSE:
            # вплотную снаряд доходит за 0.05-0.07 с: курс врага почти не меняется
            margin = FIRE_MARGIN_EASY if not self.e_ammo else FIRE_MARGIN
            aim = self._band(me, px, py, e.hull, d, margin)
        else:
            margin = FIRE_MARGIN_EASY if not self.e_ammo else FIRE_MARGIN
            aim = None
            for phull, extra in self._hulls(e, tf):
                aim = self._band(me, px, py, phull, d, margin + extra)
                if aim is not None:
                    break
        if aim is None:
            return self._aim_cmd(me, px, py), False
        aimx, aimy, band = aim
        if d > D_CLOSE and band < MIN_BAND:
            return self._aim_cmd(me, aimx, aimy), False
        # наводим ствол так, чтобы ЛУЧ из дула прошёл через выбранную точку
        a = atan2(aimy - me.y, aimx - me.x)
        a = atan2(aimy - (me.y + sin(a) * MUZZLE), aimx - (me.x + cos(a) * MUZZLE))
        step = TURRET_RATE * o.dt
        err = wrap(a - me.turret)
        cmd = clamp(err / step, -1.0, 1.0)
        residual = fabs(err - cmd * step)
        mzx = me.x + cos(a) * MUZZLE
        mzy = me.y + sin(a) * MUZZLE
        dd = hypot(aimx - mzx, aimy - mzy)
        tol = atan2(11.5, dd if dd > 40.0 else 40.0) - radians(SPREAD)
        if tol < 0.004:
            tol = 0.004
        if not me.ammo_ready or residual > tol or tf > TF_MAX:
            return cmd, False
        if not self._line_free(mzx, mzy, aimx, aimy):
            return cmd, False
        if self._blocked(mzx, mzy):
            return cmd, False
        return cmd, True

    def _aim_cmd(self, me, px, py):
        a = atan2(py - me.y, px - me.x)
        a = atan2(py - (me.y + sin(a) * MUZZLE), px - (me.x + cos(a) * MUZZLE))
        return clamp(wrap(a - me.turret) * 4.0, -1.0, 1.0)

    def _gun_ghost(self, o, me, gx, gy):
        return self._aim_cmd(me, gx, gy), False

    def _gun_blind(self, o, me):
        return self._aim_cmd(me, me.x + cos(me.hull) * 200.0,
                             me.y + sin(me.hull) * 200.0), False

    # -------------------------------------------------------------- поиск

    def _flow(self, tx, ty):
        """Волна расстояний по проходимым тайлам от цели (кирпич в стену — 0)."""
        g = self.grid
        w = g.width
        h = g.height
        rows = g.rows
        if tx < 0 or ty < 0 or tx >= w or ty >= h:
            return None
        if rows[ty][tx] in "#o:":
            near = None
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx = tx + dx
                    ny = ty + dy
                    if 0 <= nx < w and 0 <= ny < h and rows[ny][nx] not in "#o:":
                        d2 = dx * dx + dy * dy
                        if near is None or d2 < near[0]:
                            near = (d2, nx, ny)
            if near is None:
                return None
            tx = near[1]
            ty = near[2]
        inf = 1 << 30
        dist = [[inf] * w for _ in range(h)]
        dist[ty][tx] = 0
        q = [(tx, ty)]
        head = 0
        while head < len(q):
            x, y = q[head]
            head += 1
            d = dist[y][x] + 1
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and dist[ny][nx] > d \
                        and rows[ny][nx] not in "#o:":
                    dist[ny][nx] = d
                    q.append((nx, ny))
        self.flow = dist
        self.flow_goal = (tx, ty)
        self.flow_t = self.t
        return (tx, ty)

    def _waypoint(self, gx, gy):
        """Ближайший шаг маршрута к (gx, gy) — центр соседнего тайла."""
        g = self.grid
        if g is None:
            return (gx, gy)
        tile = g.tile_size
        tx = int(gx // tile)
        ty = int(gy // tile)
        if self.flow is None or self.flow_goal != (tx, ty) \
                or self.t - self.flow_t > 0.5:
            if self._flow(tx, ty) is None:
                return None
        mx = int(self._me_x // tile)
        my = int(self._me_y // tile)
        if 0 <= mx < g.width and 0 <= my < g.height:
            here = self.flow[my][mx]
            if here == 0:
                return None
            best = None
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx = mx + dx
                ny = my + dy
                if nx < 0 or ny < 0 or nx >= g.width or ny >= g.height:
                    continue
                if g.rows[ny][nx] in "#o:":
                    continue
                if dx and dy and (g.rows[my][mx + dx] in "#o:"
                                  or g.rows[my + dy][mx] in "#o:"):
                    continue
                d = self.flow[ny][nx]
                if best is None or d < best[0]:
                    best = (d, nx, ny)
            if best is not None and best[0] < here:
                return ((best[1] + 0.5) * tile, (best[2] + 0.5) * tile)
            if best is None or best[0] >= (1 << 30):
                return None
        return (gx, gy)

    def _hunt(self, o, me):
        """Враг потерян: идём к его логову по маршруту, посматривая по сторонам."""
        if o.map is not None and len(o.map.spawns) > 1:
            sp = o.map.spawns[1 - o.tank]
            gx = sp.x
            gy = sp.y
        else:
            gx = me.x + cos(me.hull) * 200.0
            gy = me.y + sin(me.hull) * 200.0
        wp = self._waypoint(gx, gy)
        if wp is None:
            wp = (gx, gy)
        want = atan2(wp[1] - me.y, wp[0] - me.x)
        err = wrap(want - me.hull)
        turn = clamp(err * 2.2, -1.0, 1.0)
        drive = 1.0 if fabs(err) < 1.2 else (0.3 if fabs(err) < 2.2 else -0.4)
        # раз в секунду-полторы крутим головой: сзади тоже кто-то есть
        pass
        return turn, drive

    # ------------------------------------------------------- карта

    def _free(self, x, y):
        g = self.grid
        tile = g.tile_size
        tx = int(x // tile)
        ty = int(y // tile)
        if tx < 0 or ty < 0 or ty >= g.height or tx >= g.width:
            return False
        return g.rows[ty][tx] not in "#o:"

    def _clear(self, x, y):
        g = self.grid
        if g is None:
            return 999.0
        tile = g.tile_size
        tx = int(x // tile)
        ty = int(y // tile)
        if tx < 0:
            tx = 0
        elif tx >= g.width:
            tx = g.width - 1
        if ty < 0:
            ty = 0
        elif ty >= g.height:
            ty = g.height - 1
        return g.clearance_grid[ty][tx] * tile

    def _blocked(self, x, y):
        g = self.grid
        if g is None:
            return False
        tile = g.tile_size
        tx = int(x // tile)
        ty = int(y // tile)
        if tx < 0 or ty < 0 or ty >= g.height or tx >= g.width:
            return True
        return g.rows[ty][tx] in "#o:"

    def _opaque(self, x, y):
        g = self.grid
        if g is None:
            return False
        tile = g.tile_size
        tx = int(x // tile)
        ty = int(y // tile)
        if tx < 0 or ty < 0 or ty >= g.height or tx >= g.width:
            return True
        return g.rows[ty][tx] in "#o"

    def _line_free(self, x0, y0, x1, y1):
        if self.grid is None:
            return True
        dx = x1 - x0
        dy = y1 - y0
        d = hypot(dx, dy)
        if d < 1e-6:
            return True
        n = int(d / 12.0) + 1
        for i in range(1, n):
            k = i / n
            if self._opaque(x0 + dx * k, y0 + dy * k):
                return False
        return True

    # --------------------------------------------------- застревание

    def _unstuck(self, o, me, turn, drive):
        t = self.t
        self.mile += hypot(me.x - self.px, me.y - self.py)
        self.px = me.x
        self.py = me.y
        if self.mile > STUCK_DIST:
            self.mile = 0.0
            self.stuck_t = t
        elif t - self.stuck_t > STUCK_WINDOW and self.mile < 3.0 and t > 2.0:
            self.escape_until = t + 0.85
            self.escape_turn = 1.0 if self.escape_turn <= 0.0 else -1.0
            self.mile = 0.0
            self.stuck_t = t
        if t < self.escape_until:
            return self.escape_turn, -1.0
        return turn, drive


program = Brain()
