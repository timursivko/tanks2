#!TANKP 1
# name: Кречет
# author: arena-d04f0868
# difficulty: 5
# color: #8fb8ff
# description: Соболь с доведённой до предела экономией выстрела: выстрел с
#   дистанции (>=130 px) разрешён, только когда пробитие держится при ВСЕХ
#   четырёх гипотезах курса цели, а не при трёх из четырёх. Точность выросла
#   с 68.8% до 81.0% при 3.6 выстрела за бой вместо 4.1. Турнир
#   tools/leaderboard.py (27 участников, 780 боёв): 727.5 очка, 93.3% — первое
#   место (Сапсан 91.3, Соболь 91.2, Барс 90.1); в круге сильнейших 82/120
#   против 69.5/120 у Соболя. Соболь 18-6-6, Барс 23-0-7, Рысь 22-0-8.
# tags: ракурс,экономия выстрела,потиковый скан,мёртвая зона,геометрия брони

"""Кречет — Соболь (arena-shturm) с одним изменённым решением о выстреле.

Навигация, ракурсная защита, потиковый скан в упор и вся геометрия брони
оставлены соболиными: на замерах это локальный оптимум. Любая переделка
(ракурс 33/37/39/41°, прогноз «враг держит угол» вместо веера гипотез,
дистанционный бой, маневрирование по чужому КД, стрельба «в гонке»,
заморозка качания ствола, шире/уже качание, мягче/жёстче гейт по запасу
угла) давала от -1% до -45%; числа ниже.

**Изменение: дальний выстрел — только при полном совпадении гипотез.**
У Соболя выстрел с дистанции (>= SCAN_D = 130 px) проходит, если
пробиваются GATE_NEED = 3 из 4 гипотез курса цели, и поверх этого стоит
ещё проверка по пяти курсам на +-HULL_RATE*tf. Здесь GATE_NEED = 4: если
хоть один из четырёх прогнозов курса даёт рикошет, патрон остаётся в
стволе. Смысл тот же, что и во всей доктрине Соболя: невыстрел стоит
одного патрона, рикошет — секунды перезарядки, которую видно через
`o.enemy.reload/cooldown`, и чужого рывка в упор. Побочный эффект виден в
статистике турнира: выстрелов за бой 3.6 против 4.1, точность 81.0%
против 68.8%, своих рикошетов 0.5 против 0.6.

Замеры. Основная сетка — 5 карт лидерборда, сиды 1-3, обе стороны,
программа грузится заново на бой, 18 программ pool = 540 боёв:

  Кречет 493.5 очка (91.4%)   Соболь 483.5 (89.5%)   парно: +27 боёв, -12
  Сапсан 16-0-14, Барс 23-0-7, Рысь 22-0-8, Соболь 18-6-6,
  Basilisk 21-0-9, Osa 27-0-3, Merlin 29-0-1, Saker 30-0-0,
  остальные девять программ pool 30-0-0.

Часть разницы в этой сетке — слот «против Соболя» (миррор 15 очков против
21 у Кречета), поэтому на общих 17 соперниках пула выигрыш скромнее:
472.5/510 (92.6%) против 468.5/510 (91.9%).

Официальный турнир `python tools/leaderboard.py` (нативное ядро, 27
участников, у каждого 780 боёв = 30 против каждого из 26 соперников):

  #1 Кречет 727.5 очка, 93.3%   (#2 Сапсан 91.3, #3 Соболь 91.2,
  #4 Барс 90.1, #5 Рысь 88.0). Круг сильнейших: Кречет 82/120,
  Соболь 69.5/120, Сапсан 62/120, Барс 60/120, Рысь 26.5/120.
  Точность 81.0% (лучшая в турнире), выстрелов за бой 3.6, свои
  рикошеты 0.5, чужие рикошеты на мне 3.0, ср. think 0.15 мс,
  пик 16.8 мс при бюджете 10 мс (у Соболя пик 12.3 мс).

Проверка на перенос: те же четыре сильнейших соперника на всех 18 картах
репозитория (сиды 1-2, обе стороны, 288 боёв) — 56.2% против 53.1% у
Соболя, то есть выигрыш не привязан к пяти лидербордным картам.

Что проверено и отвергнуто (та же сетка 540 боёв, парно к Соболю):
  * дальний огонь по наблюдаемой скорости корпуса цели: 13.8% в первой
    версии, в точной версии — ровно 0.0%;
  * прогноз «цель держит угол» вместо веера +-HULL_RATE*tf: -7.7...-17.7%;
  * инициатива по чужому КД (не влезать в убойную дистанцию заряженного
    врага, идти вперёд на его перезарядке): -1.6...-7.7%;
  * стрельба «в гонке», когда чужая башня уже смотрит на нас: -3.0...-18.2%;
  * выбор направления по максимальному запасу до порога рикошета: -2.8%;
  * DEAD_DEG 33/37/39/41/43 вместо 35: -9.6...-45.4%;
  * SWING_NEAR 0.45/0.65/0.70, SWING_RATE 5.5, SCAN_NEED 3, SCAN_MARGIN 0.3,
    FIRE_MARGIN 0.8/2.0, M_FAR 4.0, SCAN_D 180/220, TURN_LEAD 0.7/0.9,
    THREAT_LAT 35, ARR_TF 0.2, BRAWL_TURN 0.2/0.5, GATE_NEED 2: все хуже.

Вывод из перебора: в этом движке выигрывает не более точное упреждение, а
более редкий выстрел. Один рикошет по чужой броне оплачивает секунду, за
которую соперник успевает войти в упор и выстрелить первым: в 34 из 40
проигрышей Соболя первым пробивал именно соперник, и 33 из этих первых
пробоев случились на дистанции около 50 px.
"""
from math import atan2, cos, fabs, hypot, pi, radians, sin

from tankp import Action, TankProgram, clamp, wrap

INF = float("inf")
DEG = 180.0 / pi

# --- константы движка (копия config.Balance) --------------------------------
BULLET = 620.0  # скорость снаряда, px/с
MUZZLE = 26.0  # вылет ствола от центра, px
TURRET_RATE = 3.6  # рад/с
HULL_RATE = 2.6  # рад/с
SPEED_F = 190.0
SPEED_R = 120.0
HP_MAX = 10.0
VIEW = 760.0
THX = 20.0  # полуразмер коробки цели (корпус/2 + радиус снаряда)
THY = 14.0
FACE_HALF = 35.0  # ±35° от курса — лоб
RICO_FRONT = 30.0  # пороги рикошета: угол к нормали грани
RICO_SIDE = 50.0
RICO_REAR = 60.0

# --- настройки боя ----------------------------------------------------------
DEAD_DEG = 35.0
SCAN_D = 130.0
SCAN_NEED = 4
SCAN_MARGIN = 0.5
NEAR_W = 52.0
STEP_MAX_W = 1.6
M_FAR = 3.0
DEAD = radians(DEAD_DEG)
FIRE_MARGIN = 1.5  # запас до порога рикошета, градусы
TUR_NEAR = 0.62  # ствол считается наведённым на нас, рад

GATE_NEED = 4
STEP_MAX = 0.7  # потолок отрезка курса, рад (2.6 рад/с * 0.27 с)
ARR_TF = 0.3
LEAD_MAX = 0.35  # насколько вперёд экстраполируем чужую башню, с

SIDE_STICK = 0.25  # рад: насколько другая сторона должна быть выгоднее
THREAT_LAT = 50.0  # px: снаряд считается нашим, если проходит ближе
THREAT_T = 0.6  # с: горизонт «успеет прилететь»
TURN_T = 0.5  # с: потолок крайнего срока для доворота корпуса
TURN_LEAD = 0.80  # к какой доле времени полёта ракурс уже должен стоять
SWING_AMP = 0.34  # амплитуда качания ствола, рад (19°)
SWING_NEAR = 0.55  # вплотную качаем шире: 0.34 на 50 px — всего 12 px фронта
SWING_NEAR_D = 150.0  # px: ближе этого — SWING_NEAR
SWING_FAR = 400.0
BRAWL_LO = 45.0  # px: ближняя свалка, где оба корпуса в мёртвой зоне
BRAWL_HI = 75.0
BRAWL_T = 1.25  # с: сначала свободно встать в ракурс, потом держать
BRAWL_TURN = 0.35  # предел |руль| после этого (0.35 * 2.6 = 0.9 рад/с)
KEEP_D = 130.0  # px: дистанция, которую стараемся держать в бою
KEEP_HYST = 40.0  # px: гистерезис, чтобы не дёргать тягу туда-сюда
SWING_RATE = 4.0
MODEL_MARGIN = 1.0
HUNT_FRESH = 5.0
HUNT_TIME = 4.5
STUCK_WINDOW = 1.0
STUCK_DIST = 16.0
CONTACT_D = 110.0  # px: ближе этого чужая корпус — это свалка, не клин


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
        self.e_cd = 0.0
        self.e_cd_prev = 0.0
        self.shot_t = -99.0
        self.shot_dir = 0.0
        self.shot_x = 0.0
        self.shot_y = 0.0
        self.shot_due = -99.0
        self.shot_aimed = False
        self.brawl_t = -99.0
        self.threats = []
        self.bear = None
        self.bear_rate = 0.0
        self.side_t = -99.0

        # свой ракурс
        self.swing = 0.0
        self.swing_dir = 1.0
        self.side = 1.0

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
        b = atan2(e.y - self._me_y, e.x - self._me_x)
        if self.bear is not None:
            r = wrap(b - self.bear) / max(1e-4, t - self.et) if t > self.et else 0.0
            if r > 7.0:
                r = 7.0
            elif r < -7.0:
                r = -7.0
            self.bear_rate = self.bear_rate * 0.55 + r * 0.45
        self.bear = b
        if self.e_cd_prev < 0.45 < cd:
            self.shot_t = t
            self.shot_dir = e.turret
            self.shot_x = e.x + cos(e.turret) * MUZZLE
            self.shot_y = e.y + sin(e.turret) * MUZZLE
            # линия каждого выпущенного снаряда живёт в мире 2 с: пока он летит,
            # ракурс считается по НЕЙ, а не по живой оценке
            self.threats.append((self.shot_x, self.shot_y,
                                 cos(e.turret), sin(e.turret), t))
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
        if tf > ARR_TF:
            tf = ARR_TF
        back = atan2(ey - me.y, ex - me.x)
        tur = self.etur + clamp(self.e_tur_rate * tf, -LEAD_MAX, LEAD_MAX)
        if fabs(wrap(tur + pi - back)) > TUR_NEAR:
            tur = back + pi
        ax = me.x + me.vx * tf
        ay = me.y + me.vy * tf
        los = atan2(ay - ey, ax - ex)
        return atan2(1.5 * sin(tur) + sin(los), 1.5 * cos(tur) + cos(los))

    def _threat(self, o, me):
        """Ближайший снаряд, который реально летит в нас: (dx, dy, t_impact)."""
        best = None
        live = []
        for ox, oy, dx, dy, t0 in self.threats:
            if o.time - t0 > 2.0:
                continue
            live.append((ox, oy, dx, dy, t0))
            age = o.time - t0
            bx = ox + dx * BULLET * age
            by = oy + dy * BULLET * age
            rx = me.x - bx
            ry = me.y - by
            along = dx * rx + dy * ry
            if along <= -5.0:
                continue
            lat = -dy * rx + dx * ry
            if lat > THREAT_LAT or lat < -THREAT_LAT:
                continue
            ti = along / BULLET
            if ti < 0.0:
                ti = 0.0
            if ti > THREAT_T:
                continue
            if best is None or ti < best[2]:
                best = (dx, dy, ti)
        self.threats = live
        return best

    def _fire_line(self, o, me, ex, ey):
        """Откуда придёт снаряд: (направление ОТ меня к стрелку, время).

        Снаряд вылетает из дула строго по стволу и летит по прямой, поэтому
        линия прихода — это либо линия уже выпущенного снаряда (она
        зафиксирована в мире и известна точно), либо ствол врага с опережением
        и упреждённая линия визирования, если выстрела ещё не было.
        """
        th = self._threat(o, me)
        if th is not None:
            return atan2(-th[1], -th[0]), th[2]
        d = hypot(ex - me.x, ey - me.y)
        return wrap(self._arrival(me, ex, ey) + pi), d / BULLET

    def _fight(self, o, me, ex, ey):
        t = self.t
        d = hypot(ex - me.x, ey - me.y)
        aim, ti = self._fire_line(o, me, ex, ey)
        if ti < o.dt:
            ti = o.dt

        # Ракурс держим к линии прихода К МОМЕНТУ УДАРА, а не «за тик»: пока
        # снаряд летит, срок известен, и доворот стоит тяги ровно столько,
        # сколько нужно, чтобы успеть. Иначе корпус отдаёт довороту всю тягу,
        # стоит на месте и подставляется под упреждение.
        c1 = wrap(aim + self.side * DEAD)
        c2 = wrap(aim - self.side * DEAD)
        e1 = fabs(wrap(c1 - me.hull))
        e2 = fabs(wrap(c2 - me.hull))
        if e2 + SIDE_STICK < e1:
            self.side = -self.side
            c1 = c2
            err = e2
        else:
            err = e1
        err = wrap(c1 - me.hull)
        # Броня должна встать под ракурс не «к моменту удара», а раньше:
        # последние 10% подлёта доворачивать уже поздно, снаряд летит в
        # центр корпуса.
        ti_c = ti * TURN_LEAD
        if ti_c > TURN_T:
            ti_c = TURN_T
        w = err / ti_c
        if w > HULL_RATE:
            w = HULL_RATE
        elif w < -HULL_RATE:
            w = -HULL_RATE
        t_cmd = w / HULL_RATE

        # Ход всё время вперёд: движение сдвигает нас поперёк чужого расчёта
        # упреждения и не даёт застрять в «холодной войне» на дистанции.
        gear = 1.0
        th = self._threat(o, me)
        if th is not None:
            # Уклонение: линия снаряда в мире зафиксирована, а смена хода
            # сдвигает корпус поперёк неё на v*t. На 0.3 с это 35-57 px —
            # больше полуширины корпуса.
            sdx = th[0]
            sdy = th[1]
            age = t - self.shot_t
            bx = self.shot_x + sdx * BULLET * age
            by = self.shot_y + sdy * BULLET * age
            cur = fabs(-sdy * (me.x - bx) + sdx * (me.y - by))
            best = None
            ti2 = th[2] if th[2] > 0.02 else 0.02
            for sg in (1.0, -1.0):
                sp = SPEED_F if sg > 0.0 else SPEED_R
                px = me.x + cos(me.hull) * sp * ti2 * sg
                py = me.y + sin(me.hull) * sp * ti2 * sg
                lat = fabs(-sdy * (px - bx) + sdx * (py - by))
                if best is None or lat > best[0]:
                    best = (lat, sg)
            if best[0] > cur + 6.0:
                gear = best[1] * 0.9

        # Свалка 45-75 px: оба корпуса стоят под 36°, и каждый выстрел уходит
        # в броню. Доворот здесь нужен микроскопический, а точный бюджет
        # отдаёт рулю всю тягу на каждый дрожь ошибки — корпус мотается и сам
        # въезжает в чужое упреждение. Через BRAWL_T секунд после входа в
        # полосу руль прижимается: ракурс держится ровно, и мы перестаём
        # подставляться. Пока BRAWL_T не истёк, руль свободен: в первые
        # полсекунды боя доворот важнее ровного ракурса.
        if BRAWL_LO < d < BRAWL_HI:
            if self.brawl_t < 0.0:
                self.brawl_t = t
        else:
            self.brawl_t = -99.0
        if self.brawl_t > 0.0 and t - self.brawl_t > BRAWL_T and fabs(t_cmd) > BRAWL_TURN:
            t_cmd = BRAWL_TURN if t_cmd > 0.0 else -BRAWL_TURN

        d_max = 1.0 - fabs(t_cmd)
        drive = gear if gear < d_max else d_max
        if drive < -d_max:
            drive = -d_max
        return t_cmd, drive

    # -------------------------------------------------------------- пушка

    def _predict(self, e, mx, my):
        t = hypot(e.x - mx, e.y - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        return e.x + e.vx * t, e.y + e.vy * t, t

    def _hulls(self, e, tf, dx, dy):
        """Курс цели к моменту удара — отрезок, а не точка.

        Корпус поворачивается не быстрее HULL_RATE, поэтому за время полёта tf
        цель успевает увести курс на +-HULL_RATE*tf (на 200 px это +-25°, на
        пределе — STEP_MAX). Четвёртая гипотеза — экстраполяция по её угловой
        скорости. Гейт требует, чтобы пробивали GATE_NEED из четырёх: одна
        удобная гипотеза — это и есть выстрел в броню. Здесь GATE_NEED = 4,
        то есть с дистанции стреляем только при полном совпадении прогнозов.
        """
        step = HULL_RATE * tf
        if step > STEP_MAX:
            step = STEP_MAX
        return (
            (e.hull, 0.0),
            (wrap(e.hull + step), 1.0),
            (wrap(e.hull - step), 1.0),
            (wrap(e.hull + self.e_hom * tf), 1.0),
        )

    def _gun(self, o, me, e):
        """Ствол качается, плюс поиск выгодного смещения ствола вблизи."""
        mx = me.x + cos(me.turret) * MUZZLE
        my = me.y + sin(me.turret) * MUZZLE
        px, py, tf = self._predict(e, mx, my)
        d = hypot(px - me.x, py - me.y)
        bearing = atan2(py - me.y, px - me.x)
        if d >= SWING_FAR:
            amp = 0.0
        elif d > SWING_NEAR_D:
            amp = SWING_AMP
        else:
            amp = SWING_NEAR
        if amp > 0.0:
            ph = self.swing + self.swing_dir * SWING_RATE * o.dt
            if ph > amp:
                ph = amp - (ph - amp)
                self.swing_dir = -1.0
            elif ph < -amp:
                ph = -amp + (-amp - ph)
                self.swing_dir = 1.0
            self.swing = ph
            base = wrap(bearing + ph)
        else:
            self.swing = 0.0
            base = wrap(bearing)
        step = TURRET_RATE * o.dt
        # Перебор направлений идёт на всех дистанциях: окно пробития
        # сдвинуто к одному из бортов, и «луч в центр» идёт по броне
        # вскользь даже с 300 px. Шаг 0.05 рад — 2.9°, на 200 px это
        # около 10 px поперёк силуэта (корпус 40x28).
        offs = (0.0, 0.05, -0.05, 0.10, -0.10, 0.15, -0.15, 0.20, -0.20, 0.25, -0.25, 0.30, -0.30, 0.35, -0.35, 0.40, -0.40, 0.45, -0.45, 0.50, -0.50)
        best = None
        for off in offs:
            want = wrap(base + off)
            cmd = clamp(wrap(want - me.turret) / step, -1.0, 1.0)
            tur = me.turret + cmd * step
            dx = cos(tur)
            dy = sin(tur)
            mzx = me.x + dx * MUZZLE
            mzy = me.y + dy * MUZZLE
            if self._blocked(mzx, mzy):
                continue
            if not self._line_free(mzx, mzy, px, py):
                continue
            good = 0
            total = 0
            for phull, extra in self._hulls(e, tf, dx, dy):
                total += 1
                hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, phull)
                if hit is None:
                    continue
                if pen_ok(hit[1], hit[2], dx, dy, phull, FIRE_MARGIN + extra):
                    good += 1
            if total and (best is None or good > best[0]):
                best = (good, cmd)
                if good >= total:
                    break
        if best is None:
            self.swing = 0.0
            return self._aim_cmd(me, px, py), False
        if me.ammo_ready and d < SCAN_D:
            # Вплотную ведём снаряд по тикам, а не «лучом в прогнозный центр»:
            # корпус цели за время полёта поворачивается, и первая же грань,
            # которую задевает снаряд, решает исход. Перебираем достижимые
            # положения ствола и четыре скорости доворота цели; выстрел только
            # если пробитие держится во всех четырёх сценариях.
            dt = o.dt
            tstep = TURRET_RATE * dt
            rates = (0.0, self.e_hom, HULL_RATE, -HULL_RATE)
            span = int(d / BULLET / dt) + 3
            pick = None
            for c in (best[1], 0.0, 0.5, -0.5, 1.0, -1.0):
                a = me.turret + c * tstep
                dx = cos(a)
                dy = sin(a)
                ox = me.x + me.vx * dt + dx * MUZZLE
                oy = me.y + me.vy * dt + dy * MUZZLE
                if self._blocked(ox, oy):
                    continue
                if not self._line_free(ox, oy, px, py):
                    continue
                ok = 0
                left = len(rates)
                for rate in rates:
                    left -= 1
                    hit = None
                    for k in range(1, span):
                        tt = k * dt
                        hh = e.hull + rate * tt
                        hit = ray_obb(ox + dx * BULLET * (tt - dt),
                                      oy + dy * BULLET * (tt - dt), dx, dy,
                                      e.x + e.vx * tt, e.y + e.vy * tt,
                                      THX, THY, hh, BULLET * dt)
                        if hit is not None:
                            if pen_ok(hit[1], hit[2], dx, dy, hh, SCAN_MARGIN):
                                ok += 1
                            break
                    if ok + left < SCAN_NEED:
                        break
                if ok >= SCAN_NEED and (pick is None or ok > pick[0]):
                    pick = (ok, c)
            if pick is not None:
                return pick[1], True
            return best[1], False

        fire = me.ammo_ready and best[0] >= GATE_NEED
        if fire and d > NEAR_W:
            tur = me.turret + best[1] * step
            dx = cos(tur)
            dy = sin(tur)
            mzx = me.x + dx * MUZZLE
            mzy = me.y + dy * MUZZLE
            sw = HULL_RATE * tf
            if sw > STEP_MAX_W:
                sw = STEP_MAX_W
            ok = True
            for k in (-1.0, -0.6, 0.0, 0.6, 1.0):
                hm = wrap(e.hull + sw * k)
                hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, hm)
                if hit is None or not pen_ok(hit[1], hit[2], dx, dy, hm, FIRE_MARGIN + M_FAR):
                    ok = False
                    break
            fire = ok
        return best[1], fire

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
        """Сдвинулся ли танк с места за окно наблюдения.

        Считать пройденный путь нельзя: уткнувшись в угол, корпус дрожит
        вперёд-назад и «наматывает» мили, хотя центр стоит на месте. Поэтому
        сравниваются сами координаты: если за STUCK_WINDOW центр ушёл меньше
        чем на STUCK_DIST при поданной тяге, танк заклинен — выходим назад с
        разворотом, и стороны разворота чередуются.
        """
        t = self.t
        if t - self.stuck_t > STUCK_WINDOW:
            moved = hypot(me.x - self.px, me.y - self.py)
            e = o.enemy
            # Клинит по-разному. В стене (или в её углу) выхода нет — надо
            # пятиться назад и разворачиваться. А упереться в чужой корпус —
            # это не поломка, а свалка: там спасает не разворот, а выстрел,
            # и убегать из контакта значит отдать бой.
            jammed = e is None or hypot(e.x - me.x, e.y - me.y) > CONTACT_D
            if t > 2.0 and moved < STUCK_DIST and fabs(drive) > 0.3 and jammed:
                self.escape_until = t + 1.0
                self.escape_turn = 1.0 if self.escape_turn <= 0.0 else -1.0
            self.px = me.x
            self.py = me.y
            self.stuck_t = t
        if t < self.escape_until:
            return self.escape_turn, -1.0
        return turn, drive


program = Brain()
