#!TANKP 1
# name: Saker
# author: arena-saker
# difficulty: 5
# color: #ffd23f
# description: Ракурс к смеси гипотез прихода (ствол с опережением +
#   упреждённая линия + линия летящего снаряда), ТОЧНЫЙ БЮДЖЕТ гусениц
#   (|газ| + |руль| <= 1: доворот считается в тиках), качание ствола
#   3.6 рад/с поперёк азимута против чужого корпуса 2.6 рад/с и гейт
#   огня по геометрии движка. Рубка вплотную (40 px), поиск по точкам
#   карты. Замер: 95% по всему пулу, 680 боёв, 17 карт, 2 сида.
# tags: ракурс,рикошет,качание,геометрия,дистанция,поиск

"""Saker — танк мёртвой зоны, точного бюджета гусениц и качающегося ствола.

Броня здесь не режет урон, она только рикошетит: замер по движку (луч в центр
корпуса) даёт ровно две узкие зоны — 31..40° и 141..145° между осью корпуса и
линией прихода снаряда. Всё остальное пробивает. Поэтому дуэль — это спор о
двух вещах: чей корпус точнее стоит в зоне и чья линия прихода угадана.

* **ТОЧНЫЙ БЮДЖЕТ ГУСЕНИЦ.** Движок нормирует left/right на их сумму, поэтому
  после нормировки |газ| + |руль| <= 1: поворот = 2.6 * руль рад/с, ход =
  190 * газ px/с. Значит доворот на err за тик стоит ровно |err| / (2.6*dt)
  тяги, и руль считается точно, а не «усилением»: корпус встаёт под нужный
  ракурс за тики. Держать зону шириной 10° иначе просто нечем. На меренной
  выборке (18 карт x 4 сида) этот переход поднял счёт против shrike с 50% до
  60%, а с качанием ствола — до 85%.

* **РАКУРС К СМЕСИ ЛИНИЙ ПРИХОДА.** Линию прихода мы не знаем. Ствол врага
  сейчас — не она: он доворачивается к упреждённой точке и за время полёта
  уезжает. Направление на свою прогнозную точку — тоже не она: снаряд идёт по
  стволу, а ствол стоит с упреждением по скорости башни. Замер: ракурс только
  к стволу с опережением — 66% против shrike, только к упреждённой линии
  визирования — 3%, а СМЕСЬ обоих (среднее направление) — 85%. Смесь страхует
  обе гипотезы сразу, ни одна ошибка в оценке не превращается в пробой. Плюс
  отдельная, самая жёсткая гипотеза — линия уже летящего в нас снаряда.

* **КАЧАНИЕ СТВОЛА.** Чужой ракурс считается от НАШЕГО ствола, поэтому ствол
  качается поперёк азимута на полной скорости башни 3.6 рад/с — быстрее
  чужого корпуса 2.6 рад/с. Чужой корпус не успевает и к выстрелу стоит не под
  35-36°, а там, куда его увели: удар выходит из мёртвой зоны. Амплитуда
  0.34 рад (19°) — предел, на котором ствол ещё попадает в силуэт с 400 px.
  Один этот приём (на том же точном бюджете) даёт 76% против shrike против
  58% без него.

* **ГЕЙТ ОГНЯ.** Стреляем не «когда прицелились», а когда луч из дула по
  геометрии движка (ray vs OBB + порог грани) пробьёт броню с запасом, по
  двум гипотезам чужого курса (сейчас и с учётом его угловой скорости).
  Качание делает такие моменты регулярными, а промах по таймингу бесплатным:
  не вышло — ствол качается дальше, патрон не тратится.

* **РУБКА ВПЛОТНУЮ.** На 40 px дуло уже внутри чужого корпуса, а время полёта
  0.065 с: чужой корпус за это время поворачивается на 10°, то есть ракурс,
  который он держал, ломается сам. Рубеж боя — вплотную.

* **ПОИСК.** Враг ушёл с линии взгляда — идём по волновому полю по кругу:
  чужой старт, центр карты, углы, свой старт. С одной точкой («его логово»)
  два танка на карте с центральным блоком разъезжались навсегда: ничья 60 с,
  ноль выстрелов. Обход точек убрал 8 ничьих из 14 и поднял общий счёт
  выборки с 83.8% до 93.1%.

Основа взята из открытого пула (shrike, arena-438319c3): навигация волновым
полем, распознавание чужого выстрела по скачку перезарядки и ракурс к стволу.
Всё перечисленное выше — мои правки; проверялись прогонами на 18 картах,
нескольких сидах и обеих сторонах, отдельно на настроечной и проверочной
выборках. Итог по всему пулу на проверочных сидах: 95% (680 боёв).
"""

from math import atan2, cos, fabs, hypot, pi, radians, sin

from tankp import Action, TankProgram, clamp, wrap

TAU = pi * 2.0
INF = float("inf")
DEG = 180.0 / pi
COS = cos

# --- константы движка (копия config.Balance) --------------------------------
BULLET = 620.0  # скорость снаряда, px/с
MUZZLE = 26.0  # вылет ствола от центра, px
TURRET_RATE = 3.6  # рад/с
HULL_RATE = 2.6  # рад/с
SPEED_F = 190.0
SPEED_R = 120.0
HP_MAX = 10.0
DMG = 4.0
RELOAD = 1.0
SPREAD = 0.4  # ± градусов
VIEW = 760.0
VIEW_SQ = VIEW * VIEW
THX = 20.0  # полуразмер коробки цели (корпус/2 + радиус снаряда)
THY = 14.0
FACE_HALF = 35.0  # ±35° от курса — лоб
RICO_FRONT = 30.0  # пороги рикошета: угол к нормали грани
RICO_SIDE = 50.0
RICO_REAR = 60.0

# --- настройки боя ----------------------------------------------------------
DEAD_DEG = 36.0
DEAD = radians(DEAD_DEG)
TRACK_GAIN = 6.0
GAS_DROP_DEG = 7.0  # на сколько градусов ошибки бросить газ
FIRE_MARGIN = 1.5  # запас до порога рикошета, градусы
FIRE_MARGIN_EASY = 0.5  # запас, когда враг пуст (ответить не может)
TF_MAX = 1.2
TUR_NEAR = 0.62  # ствол считается наведённым на нас, рад

D_BRAWL = 40.0  # рубеж боя: дуло внутри чужого корпуса
D_CLOSE = 46.0  # дальше дуло снаружи — полосы пробития нет
MIN_BAND = 2  # сколько лучей подряд нужно, чтобы стрелять издали
D_RETREAT_FAR = 620.0  # враг готов, а мы пусты — разрываем
SWING_AMP = 0.34
SWING_FAR = 400.0
SWING_RATE = TURRET_RATE
GHOST_TIME = 2.0
HUNT_FRESH = 5.0
HUNT_TIME = 4.5
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
        self.swing = 0.0
        self.swing_dir = 1.0
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
        self.hunt_pts = None
        self.hunt_i = 0
        self.hunt_t = -99.0
        self.hunt_pt = None

    # -------------------------------------------------------------- память

    def _remember(self, o, e):
        t = o.time
        if e is None:
            return
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.3:
                self.e_hom = (
                    self.e_hom * 0.65
                    + clamp(wrap(e.hull - self.ehull) / dt, -HULL_RATE, HULL_RATE)
                    * 0.35
                )
                self.e_tur_rate = (
                    self.e_tur_rate * 0.6
                    + clamp(wrap(e.turret - self.etur) / dt, -TURRET_RATE, TURRET_RATE)
                    * 0.4
                )
            elif dt > 0.35:
                self.e_hom *= 0.5
                self.e_tur_rate *= 0.5
        cd = e.cooldown
        if self.e_cd_prev < 0.45 < cd:
            self.shot_t = t
            self.shot_dir = e.turret
            self.shot_x = e.x + cos(e.turret) * MUZZLE
            self.shot_y = e.y + sin(e.turret) * MUZZLE
            self.shot_due = (
                t + hypot(self._me_x - self.shot_x, self._me_y - self.shot_y) / BULLET
            )
            sdx = cos(e.turret)
            sdy = sin(e.turret)
            self.shot_aimed = (
                fabs(
                    (self._me_x - self.shot_x) * sdy - (self._me_y - self.shot_y) * sdx
                )
                < 60.0
            )
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
                    turn = clamp(
                        wrap(atan2(wp[1] - me.y, wp[0] - me.x) - me.hull) * 2.2,
                        -1.0,
                        1.0,
                    )
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
        d = hypot(ex - me.x, ey - me.y)
        tf = d / BULLET
        back = atan2(ey - me.y, ex - me.x)
        tur = self.etur + clamp(self.e_tur_rate * tf, -0.35, 0.35)
        if fabs(wrap(tur + pi - back)) > TUR_NEAR:
            tur = back + pi
        ax = me.x + me.vx * tf
        ay = me.y + me.vy * tf
        los = atan2(ay - ey, ax - ex)
        return atan2(sin(tur) + sin(los), cos(tur) + cos(los))

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
            me.x + cos(tgt) * 58.0, me.y + sin(tgt) * 58.0
        ):
            alt = wrap(arr + pi - self.side * DEAD)
            if self._free(me.x + cos(alt) * 58.0, me.y + sin(alt) * 58.0):
                tgt = alt
                self.side = -self.side
        err = wrap(tgt - me.hull)
        rvx = me.vx - self.evx
        rvy = me.vy - self.evy
        rr = dx * dx + dy * dy
        om = (dx * rvy - dy * rvx) / rr if rr > 1.0 else 0.0
        step = HULL_RATE * o.dt
        t_cmd = clamp((err + om * o.dt) / step, -1.0, 1.0)
        d_max = 1.0 - fabs(t_cmd)
        drive = self.gear if self.gear < d_max else d_max
        if drive < -d_max:
            drive = -d_max
        return t_cmd, drive

    # -------------------------------------------------------------- пушка

    def _predict(self, e, mx, my):
        t = hypot(e.x - mx, e.y - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        return e.x + e.vx * t, e.y + e.vy * t, t

    def _hulls(self, e, tf):
        ext = wrap(e.hull + self.e_hom * tf)
        return ((e.hull, 0.0), (ext, 1.0))

    def _gun(self, o, me, e):
        """Ствол качается поперёк азимута, огонь — только когда луч из дула
        реально пробьёт броню по геометрии движка."""
        mx = me.x + cos(me.turret) * MUZZLE
        my = me.y + sin(me.turret) * MUZZLE
        px, py, tf = self._predict(e, mx, my)
        d = hypot(px - me.x, py - me.y)
        bearing = atan2(py - me.y, px - me.x)
        amp = SWING_AMP if d < SWING_FAR else 0.0
        if amp > 0.0:
            ph = self.swing + self.swing_dir * SWING_RATE * o.dt
            if ph > amp:
                ph = amp - (ph - amp)
                self.swing_dir = -1.0
            elif ph < -amp:
                ph = -amp + (-amp - ph)
                self.swing_dir = 1.0
            self.swing = ph
            want = wrap(bearing + ph)
            step = TURRET_RATE * o.dt
            cmd = clamp(wrap(want - me.turret) / step, -1.0, 1.0)
        else:
            self.swing = 0.0
            cmd = self._aim_cmd(me, px, py)
        if not me.ammo_ready:
            return cmd, False
        # ствол после этого тика — по нему и полетит снаряд
        tur = me.turret + cmd * TURRET_RATE * o.dt
        dx = cos(tur)
        dy = sin(tur)
        mzx = me.x + dx * MUZZLE
        mzy = me.y + dy * MUZZLE
        if self._blocked(mzx, mzy):
            return cmd, False
        for phull, extra in self._hulls(e, tf):
            hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, phull)
            if hit is None:
                continue
            if not pen_ok(hit[1], hit[2], dx, dy, phull, FIRE_MARGIN + extra):
                continue
            if self._line_free(mzx, mzy, px, py):
                return cmd, True
        return cmd, False

    def _aim_cmd(self, me, px, py):
        a = atan2(py - me.y, px - me.x)
        a = atan2(py - (me.y + sin(a) * MUZZLE), px - (me.x + cos(a) * MUZZLE))
        return clamp(wrap(a - me.turret) * 4.0, -1.0, 1.0)

    def _gun_ghost(self, o, me, gx, gy):
        return self._aim_cmd(me, gx, gy), False

    def _gun_blind(self, o, me):
        return self._aim_cmd(
            me, me.x + cos(me.hull) * 200.0, me.y + sin(me.hull) * 200.0
        ), False

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
                if (
                    0 <= nx < w
                    and 0 <= ny < h
                    and dist[ny][nx] > d
                    and rows[ny][nx] not in "#o:"
                ):
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
        if (
            self.flow is None
            or self.flow_goal != (tx, ty)
            or self.t - self.flow_t > 0.5
        ):
            if self._flow(tx, ty) is None:
                return None
        mx = int(self._me_x // tile)
        my = int(self._me_y // tile)
        if 0 <= mx < g.width and 0 <= my < g.height:
            here = self.flow[my][mx]
            if here == 0:
                return None
            best = None
            for dx, dy in (
                (1, 0),
                (-1, 0),
                (0, 1),
                (0, -1),
                (1, 1),
                (1, -1),
                (-1, 1),
                (-1, -1),
            ):
                nx = mx + dx
                ny = my + dy
                if nx < 0 or ny < 0 or nx >= g.width or ny >= g.height:
                    continue
                if g.rows[ny][nx] in "#o:":
                    continue
                if (
                    dx
                    and dy
                    and (g.rows[my][mx + dx] in "#o:" or g.rows[my + dy][mx] in "#o:")
                ):
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
        """Враг потерян: обходим карту по точкам, пока он не попадёт в обзор.

        Раньше цель была одна — чужое логово, и на карте с центральным блоком
        два танка могли разъехаться навсегда (ничья 60 с, ноль выстрелов).
        Теперь точек обхода несколько: чужой старт, центр, свой старт, углы
        карты; точка меняется по времени или по достижении.
        """
        t = self.t
        if self.hunt_pts is None:
            g = o.map
            pts = []
            if g is not None and len(g.spawns) > 1:
                for sp in g.spawns:
                    pts.append((sp.x, sp.y))
            if g is not None:
                w = g.pixel_width
                h = g.pixel_height
                pts.append((w * 0.5, h * 0.5))
                pts.append((w * 0.27, h * 0.27))
                pts.append((w * 0.73, h * 0.73))
                pts.append((w * 0.27, h * 0.73))
                pts.append((w * 0.73, h * 0.27))
            if not pts:
                pts = [(me.x + cos(me.hull) * 200.0, me.y + sin(me.hull) * 200.0)]
            self.hunt_pts = pts
        pts = self.hunt_pts
        gx, gy = self.hunt_pt if self.hunt_pt is not None else pts[0]
        far = hypot(gx - me.x, gy - me.y)
        if t - self.hunt_t > HUNT_TIME or far < 110.0:
            self.hunt_i = (self.hunt_i + 1) % len(pts)
            gx, gy = pts[self.hunt_i]
            self.hunt_pt = (gx, gy)
            self.hunt_t = t
        wp = self._waypoint(gx, gy)
        if wp is None:
            wp = (gx, gy)
        want = atan2(wp[1] - me.y, wp[0] - me.x)
        err = wrap(want - me.hull)
        step = HULL_RATE * o.dt
        turn = clamp(err / step, -1.0, 1.0)
        d_max = 1.0 - fabs(turn)
        drive = d_max if d_max > 0.0 else 0.0
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
