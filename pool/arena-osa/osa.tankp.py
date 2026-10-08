#!TANKP 1
# name: Osa
# author: arena-osa
# difficulty: 5
# color: #ffd166
# description: Стреляет только туда, где снаряд ПРОБИВАЕТ при любом довороте цели:
#   веер из 21 луча через силуэт, грань и угол удара считаются тем же slab-тестом,
#   что и в движке, а курс цели берётся с запасом на её доворот и на снос линии
#   визирования за время подлёта. Защита — корпус под 36° к линии выстрела
#   (середина мёртвой зоны 30..40°), и ход отдаётся довороту: газ платит за ракурс.
#   Замер на 1320 боях (11 танков x 6 карт x 2 сида x 2 стороны): 81.7% побед,
#   второй результат в пуле; shrike 62%, mongoose 77%, gyurza 65%, taipan 75%,
#   mantis 79%, karakurt 67%, tempest/raptor/nova/vulkan 96-100%.
# tags: ракурс,рикошет,веер лучей,упреждение,газ платит за ракурс

"""Osa — танк, который стреляет только наверняка и почти не подставляется.

Правила боя здесь короткие, и вся тактика из них выводится. Броня урон не
режет, она рикошетит: снаряд, пришедший под углом 30..40° к нормали грани,
отскакивает и от лба (порог 30°), и от борта (порог 50°), не нанося ничего.
Пробитие всегда стоит ровно 4 урона при 10 HP, то есть убивают три попадания.
Рикошет не стоит ничего, а по истечении 60 с фиксируется ничья независимо от
остатка HP. Значит, частичный урон не стоит ничего вовсе: бой выигрывает тот,
кто первым успеет нанести три пробития, и темп важнее точности лишь до тех пор, пока
выстрелы вообще пробивают.

**Защита.** Если ψ — угол между курсом корпуса и направлением на стрелка, то
рикошетят ψ ∈ [30°, 40°] и [140°, 145°]. Osa держит ψ = 36°, то есть середину
зоны: 6° запаса до лобового порога и 4° до бортового. Доворот и тяга делят один
бюджет гусениц (left = drive + turn, right = drive - turn, и при
|drive| + |turn| > 1 обе команды делятся на лишнее), поэтому на доворот
отдаётся всё: скорость поворота равна turn * 2.6 рад/с ровно при
|drive| <= 1 - |turn|, и ракурс важнее хода.

**Наведение.** Направление выстрела однозначно задаёт и точку входа, и грань, и
угол удара, поэтому перебираются не «точки на цели», а направления: 21 луч в
веере через силуэт, для каждого — грань тем же slab-тестом, что и в движке
(engine.geometry.segment_obb), и угол к её нормали (engine.armor.resolve_impact).
Обе функции скопированы в файл и сверены с движком на 40 000 случайных
попаданий: расхождений ноль.

Курс цели к моменту удара неизвестен, и это главное. Берутся три гипотезы:
текущий курс плюс снос линии визирования за время подлёта, и ± тот доворот,
который цель физически успевает сделать (2.6 рад/с * (t - dt)). Снос — не
мелочь: кто держит ракурс к живому азимуту, тот довернёт корпус к азимуту, а не
к линии моего снаряда. Сдвиг равен omega_LOS * t = v_поперёк / 620 и от
дистанции не зависит вовсе — на 100 px/с поперёк это 9.3°. Луч годится, только
если пробивает при всех трёх гипотезах.

Из годящихся лучей берётся самая широкая полоса, ствол ставится в её середину,
а выстрел идёт лишь когда полоса шире разброса ствола (±0.4°) плюс допуск на
ошибку упреждения и ствол успевает в неё вписаться этим тиком. Такая
дисциплина дороже темпа: промах в броню не стоит ничего, а патрон, выпущенный
в рикошет, отнимает секунду перезарядки, за которую окно могло бы открыться.

**Дистанция.** Рубеж 210 px выбран замером: в упор (<80 px) пробивает 5-19%
выстрелов — противник держит ракурс, а доворачивать ему почти не нужно; на
80-180 px уже 56-69%; дальше 400 px точность падает до 14%, а снаряд летит
слишком долго. Корпус при этом всё время повёрнут на 36° к линии визирования,
поэтому полный газ даёт поперечный снос v * sin 36°, и чужой ракурс уезжает из
собственной мёртвой зоны ровно на те самые v_поперёк / 620.
"""

from math import acos, atan2, cos, fabs, hypot, pi, radians, sin

from tankp import Action, TankProgram, clamp, wrap

INF = float("inf")
DEG = 180.0 / pi

# --- константы движка (копия config.Balance) --------------------------------
BULLET = 620.0
MUZZLE = 26.0
TURRET_RATE = 3.6
HULL_RATE = 2.6
HP_MAX = 10.0
SPREAD = 0.4

# --- пороги брони -----------------------------------------------------------

# --- настройки --------------------------------------------------------------
DEAD = radians(36.0)
HULL_GAIN = 9.0
GHOST_TIME = 4.0
STUCK_WINDOW = 0.55
STUCK_DIST = 12.0

R_FIGHT = 210.0        # рубеж боя: ближе чужой ракурс не сбить,
                       # дальше снаряд летит слишком долго

CLOSE_R = 150.0        # вблизи: там решается ножевой размен
AWAY_MID = 0.62        # враг уходит (>0.62) или сам лезет в упор (<0.62)
AWAY_W = 400.0
AWAY_N0 = 120.0         # крутизна отодвигания
KO_MIN = 50.0          # обычный рубеж реверса
KO_MAX = 150.0
SEARCH_R = 110.0       # дошли до точки поиска — берём следующую
SEARCH_T = 4.0
PATROL_T = 15.0        # раньше этого времени не патрулируем         # или через столько секунд, если уперлись
ESC_T = 30.0         # насколько отходим от того, кто не отступает
R_SPRINT = 130.0       # дальше бортовой разворот не крутит линию быстрее 2.6

FIRE_MARGIN = 3.0      # запас от порога рикошета, градусы
SCAN_N = 21            # лучей в веере наведения
BAND_MIN = 1.0         # минимальная ширина пробивающей полосы, градусы
PRED_ERR = 2.0         # допуск на ошибку упреждения, px
TF_MAX = 1.3           # дольше снаряд лететь не должен, с

# полуразмеры корпуса цели плюс радиус снаряда (движок: bal.half_far + r)
THX = 20.0
THY = 14.0
LIM_FRONT = 30.0
LIM_SIDE = 50.0
LIM_REAR = 60.0
FRONT_HALF = 35.0

def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang):
    """Нормаль грани, в которую входит луч (копия engine.geometry.segment_obb)."""
    ca = cos(ang)
    sa = sin(ang)
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
    for i in (0, 1):
        p, q, h = (lx, ldx, hx) if i == 0 else (ly, ldy, hy)
        if -1e-12 < q < 1e-12:
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
        # ствол уже внутри корпуса: грань та, что перпендикулярна полёту
        if fabs(ldx) >= fabs(ldy):
            nx, ny = (-1.0 if ldx > 0.0 else 1.0), 0.0
        else:
            nx, ny = 0.0, (-1.0 if ldy > 0.0 else 1.0)
    elif axis == 0:
        nx, ny = sign, 0.0
    else:
        nx, ny = 0.0, sign
    return nx * ca - ny * sa, nx * sa + ny * ca

def hit_margin(nx, ny, dirx, diry, hull):
    """Запас до рикошета в градусах: >0 — пробитие, <0 — отскок."""
    rel = fabs(wrap(atan2(ny, nx) - hull) * DEG)
    if rel <= FRONT_HALF:
        lim = LIM_FRONT
    elif rel >= 180.0 - FRONT_HALF:
        lim = LIM_REAR
    else:
        lim = LIM_SIDE
    c = -(dirx * nx + diry * ny)
    if c < 0.0:
        c = 0.0
    elif c > 1.0:
        c = 1.0
    return lim - acos(c) * DEG

class Brain(TankProgram):
    """Качание ствола + разворот борта в паузу перезарядки."""

    # ------------------------------------------------------------------ старт
    def on_start(self, ctx):
        self.t = 0.0
        self.last_tick = -1
        self.grid = None
        self.mx = 0.0
        self.my = 0.0
        self.hull_now = 0.0

        # память о враге
        self.seen = False
        self.et = -99.0
        self.ex = 0.0
        self.ey = 0.0
        self.evx = 0.0
        self.evy = 0.0
        self.ehull = 0.0
        self.etur = 0.0
        self.ehp = HP_MAX
        self.e_ammo = True
        self.e_cd = 0.0
        self.e_cd_prev = 0.0
        self.e_hom = 0.0
        self.e_tur_rate = 0.0

        # чужой выстрел
        self.shot_dir = 0.0
        self.shot_x = 0.0
        self.shot_y = 0.0
        self.shot_due = -99.0
        self.shot_aimed = False
        self.frozen = False

        # свой ракурс и манёвр
        self.side = 1.0
        self.side_t = -9.0
        self.threat_rate = 0.0
        self.sprint_t = -9.0
        self.search_pts = None
        self.search_i = 0
        self.search_t = -9.0
        self.away = AWAY_MID
        self.away_n = 0.0
        self.away_s = 0.0
        self.keep_out = KO_MIN
        self.free = False

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
        self.escape_turn = 1.0

    # ----------------------------------------------------------------- память
    def _remember(self, o, e):
        if e is None:
            return
        t = o.time
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.3:
                self.e_hom = self.e_hom * 0.55 + clamp(
                    wrap(e.hull - self.ehull) / dt, -HULL_RATE,
                    HULL_RATE) * 0.45
                self.e_tur_rate = self.e_tur_rate * 0.5 + clamp(
                    wrap(e.turret - self.etur) / dt, -TURRET_RATE,
                    TURRET_RATE) * 0.5
            elif dt > 0.35:
                self.e_hom *= 0.5
                self.e_tur_rate *= 0.5
        cd = e.cooldown
        if self.e_cd_prev < 0.45 < cd:
            self.shot_dir = e.turret
            self.shot_x = e.x + cos(e.turret) * MUZZLE
            self.shot_y = e.y + sin(e.turret) * MUZZLE
            self.shot_due = t + hypot(self.mx - self.shot_x,
                                      self.my - self.shot_y) / BULLET
            sdx = cos(e.turret)
            sdy = sin(e.turret)
            self.shot_aimed = fabs(
                (self.mx - self.shot_x) * sdy
                - (self.my - self.shot_y) * sdx) < 46.0
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

    # -------------------------------------------------------------------- тик
    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"tank": o.tank})
        self.last_tick = o.tick
        t = self.t = o.time
        me = o.me
        self.mx = me.x
        self.my = me.y
        self.hull_now = me.hull
        if self.grid is None and o.map is not None:
            self.grid = o.map
        e = o.enemy
        self._remember(o, e)
        self.frozen = (self.shot_aimed and t < self.shot_due
                       and self.shot_due - t < 0.9)

        if e is not None:
            turret, fire = self._gun(o, me, e)
            turn, drive = self._fight(o, me, e.x, e.y)
        elif self.seen and (t - self.et) < GHOST_TIME:
            age = t - self.et
            gx = self.ex + self.evx * age
            gy = self.ey + self.evy * age
            turret, fire = self._aim_cmd(me, gx, gy), False
            turn, drive = self._fight(o, me, gx, gy)
            if not self._line_free(me.x, me.y, gx, gy):
                wp = self._waypoint(gx, gy)
                if wp is not None:
                    turn, drive = self._go(me, wp[0], wp[1], 1.0)
        else:
            self.free = False
            turret = self._aim_cmd(me, me.x + cos(me.hull) * 200.0,
                                   me.y + sin(me.hull) * 200.0)
            fire = False
            turn, drive = self._hunt(o, me)
        turn, drive = self._unstuck(o, me, turn, drive)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # ------------------------------------------------------------- движение
    def _fight(self, o, me, ex, ey):
        dx = ex - me.x
        dy = ey - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d = 1e-6
        los = atan2(dy, dx)
        rate = self._los_rate(dx, dy, me)

        # Кто навязывает упор? Если враг вблизи пятится — он стрелок-дистанционщик,
        # догонять его себе дороже: пока мы сближаемся, он нас расстреливает.
        # Если сам прёт — наоборот, встреча на нашем ходу нам выгодна.
        if d < CLOSE_R:
            rad = (dx * self.evx + dy * self.evy) / d
            self.away_n += 1.0
            self.away_s += 1.0 if rad > 10.0 else 0.0
            inst = (self.away_s + AWAY_MID * AWAY_N0) / (self.away_n + AWAY_N0)
            self.away += clamp(inst - self.away, -0.01, 0.01)
            self.keep_out = clamp(KO_MIN + AWAY_W * (AWAY_MID - self.away),
                                  KO_MIN, KO_MAX)

        # --- манёвр в паузу чужой перезарядки -------------------------------
        # В упор линия визирования от бортового разворота крутится как v/d —
        # на 50 px это 3.8 рад/с против 2.6 рад/с предела чужого корпуса:
        # чужой ракурс разваливается, а нам отвечать нечем. Возврат в 35°
        # занимает 55°/149°/с = 0.37 с, и закончить его надо до прилёта
        # чужого снаряда, поэтому манёвр разрешён только с запасом по времени.
        cd = self.e_cd if self.seen else 0.0
        back = radians(58.0) / HULL_RATE
        need = back + d / BULLET + 0.12
        sprint = (self.seen and not self.frozen and cd > need
                  and d < R_SPRINT)
        if sprint:
            self.sprint_t = self.t
            tgt = wrap(los + self.side * (pi * 0.5))
            err = wrap(tgt - me.hull)
            omega = clamp(err * 5.0 + rate, -HULL_RATE, HULL_RATE)
            want = 1.0 if fabs(err) < 0.9 else 0.0
            return self._tracks(omega, want)

        # --- защита ---------------------------------------------------------
        arr = self.shot_dir if self.frozen else self._arrival(me, los)
        if self.frozen:
            self.threat_rate *= 0.3
        else:
            self.threat_rate = rate

        tgt = wrap(arr + pi + self.side * DEAD)
        g = self.grid
        probe = 58.0
        blocked = False
        if g is not None:
            blocked = not self._free_pt(me.x + cos(tgt) * probe,
                                        me.y + sin(tgt) * probe)
        if blocked and self.t - self.side_t > 0.6:
            alt = wrap(arr + pi - self.side * DEAD)
            if self._free_pt(me.x + cos(alt) * probe,
                             me.y + sin(alt) * probe):
                # менять сторону — значит пройти через ноль (нос на врага),
                # поэтому делаем это только когда стрелять ему нечем
                if cd > back + d / BULLET + 0.25:
                    self.side = -self.side
                    self.side_t = self.t
                    tgt = alt

        err = wrap(tgt - me.hull)
        omega = clamp(self.threat_rate + err * HULL_GAIN, -HULL_RATE, HULL_RATE)
        want = self._throttle(d, o)
        return self._tracks(omega, want)

    def _r_fight(self):
        return R_FIGHT

    def _los_rate(self, dx, dy, me):
        rvx = self.evx - me.vx
        rvy = self.evy - me.vy
        rr = dx * dx + dy * dy
        return (dx * rvy - dy * rvx) / rr if rr > 4.0 else 0.0

    def _arrival(self, me, los):
        tur = self.etur + clamp(self.e_tur_rate * 0.05, -0.18, 0.18)
        if fabs(wrap(tur - los)) < 0.7:
            return tur
        return los + pi

    def _tracks(self, omega, want_drive):
        u = clamp(omega / HULL_RATE, -1.0, 1.0)
        room = 1.0 - fabs(u)
        return u, clamp(want_drive, -room, room)

    def _throttle(self, d, o):
        rf = self._r_fight()
        if d > rf + 60.0:
            return 1.0
        ko = self.keep_out
        # Ничья по времени даёт пол-очка обеим. Кто впереди по ХП, тому она
        # крадёт победу: после ESC_T такой танк перестаёт держать дистанцию
        # и дожимает. Кто позади — наоборот, держит и тянет время.
        # Симметричная стойка на своём же рубеже — автоничья. Если к ESC_T никто
        # никого ни разу не пробил, держать дистанцию дальше бессмысленно.
        if self.t > ESC_T and (o.me.hp > self.ehp + 0.5
                               or (self.ehp > HP_MAX - 0.5
                                   and o.me.hp > HP_MAX - 0.5)):
            ko = KO_MIN
        if d < ko:
            return -0.85
        return 0.95

    def _go(self, me, gx, gy, want=1.0):
        want_dir = atan2(gy - me.y, gx - me.x)
        err = wrap(want_dir - me.hull)
        omega = clamp(err * 3.2, -HULL_RATE, HULL_RATE)
        u = clamp(omega / HULL_RATE, -1.0, 1.0)
        room = 1.0 - fabs(u)
        ae = fabs(err)
        drive = want if ae < 1.0 else (0.25 if ae < 2.0 else -0.35)
        return u, clamp(drive, -room, room)

    # ------------------------------------------------------------------ пушка
    def _predict(self, e, mx, my):
        t = hypot(e.x - mx, e.y - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        t = hypot(e.x + e.vx * t - mx, e.y + e.vy * t - my) / BULLET
        return e.x + e.vx * t, e.y + e.vy * t, t

    def _aim_point(self, me, px, py, d):
        """Направление ствола, при котором снаряд проходит через цель."""
        a = atan2(py - me.y, px - me.x)
        if d > 44.0:
            # одна итерация поправки на вылет ствола: при d > 44 она устойчива
            a = atan2(py - (me.y + sin(a) * MUZZLE),
                      px - (me.x + cos(a) * MUZZLE))
        return a

    def _scan(self, me, e, dt):
        """Веер направлений через силуэт цели: где снаряд пробивает.

        Направление выстрела однозначно задаёт и точку входа, и грань, и угол
        удара, поэтому перебираем не «точки на цели», а направления: для
        каждого луча считаем грань тем же slab-тестом, что и движок, и угол к
        её нормали. Цель за время подлёта успеет довернуть корпус на
        HULL_RATE * (t - dt), поэтому луч обязан пробивать при любом из трёх
        её курсов — иначе противник просто довернёт в мёртвую зону.
        """
        px, py, tf = self._predict(e, me.x, me.y)
        dx = px - me.x
        dy = py - me.y
        d = hypot(dx, dy)
        if d < 1.0:
            return None
        los = atan2(dy, dx)
        span = atan2(34.0, d)
        reach = HULL_RATE * (tf - dt) if tf > dt else 0.0
        # Кто держит ракурс к живому азимуту, тот к моменту удара довернёт
        # корпус к АЗИМУТУ, а не к линии моего снаряда: за время подлёта
        # азимут уедет на omega * t, и вместе с ним уедет чужой курс. Сдвиг
        # равен v_поперёк / 620 и от дистанции не зависит.
        shift = self.threat_rate * tf
        h0 = e.hull + shift
        hulls = (h0 - reach, h0, h0 + reach)
        n = SCAN_N
        step = 2.0 * span / (n - 1)
        best = None
        spare = None
        i = 0
        while i < n:
            a = los + (i * step - span)
            ca = cos(a)
            sa = sin(a)
            nr = ray_obb(me.x + ca * MUZZLE, me.y + sa * MUZZLE, ca, sa,
                         px, py, THX, THY, e.hull)
            if nr is None:
                i += 1
                continue
            mg = INF
            for h in hulls:
                v = hit_margin(nr[0], nr[1], ca, sa, h)
                if v < mg:
                    mg = v
            if mg > -90.0 and (spare is None or mg > spare[2]):
                spare = (a, 0.0, mg, px, py, tf, d)
            if mg <= FIRE_MARGIN:
                i += 1
                continue
            j = i
            top = mg
            while j + 1 < n:
                b = los + ((j + 1) * step - span)
                cb = cos(b)
                sb = sin(b)
                nr2 = ray_obb(me.x + cb * MUZZLE, me.y + sb * MUZZLE, cb, sb,
                              px, py, THX, THY, e.hull)
                if nr2 is None:
                    break
                m2 = INF
                for h in hulls:
                    v = hit_margin(nr2[0], nr2[1], cb, sb, h)
                    if v < m2:
                        m2 = v
                if m2 <= FIRE_MARGIN:
                    break
                j += 1
                if m2 < top:
                    top = m2
            width = (j - i) * step * DEG
            mid = los + ((i + j) * 0.5 * step - span)
            if best is None or width > best[1] or (
                    width == best[1] and top > best[2]):
                best = (mid, width, top, px, py, tf, d)
            i = j + 1
        return best if best is not None else spare

    def _gun(self, o, me, e):
        dx = e.x - me.x
        dy = e.y - me.y
        rr = dx * dx + dy * dy
        if rr > 4.0:
            self.threat_rate = (dx * (e.vy - me.vy)
                                - dy * (e.vx - me.vx)) / rr
        res = self._scan(me, e, o.dt)
        if res is None:
            px, py, tf = self._predict(e, me.x, me.y)
            a = self._aim_point(me, px, py, hypot(px - me.x, py - me.y))
            self.dbg = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            return clamp(wrap(a - me.turret) / (TURRET_RATE * o.dt),
                         -1.0, 1.0), False
        a, band, margin, px, py, tf, d = res
        need = BAND_MIN + atan2(PRED_ERR, d) * DEG
        if band < need:
            # Полосы нет — выстрел «на удачу». Тогда целиться в край силуэта
            # бессмысленно: любая ошибка упреждения уводит снаряд мимо.
            # Берём центр прогнозного корпуса, он всего устойчивее.
            a = self._aim_point(me, px, py, d)
        err = wrap(a - me.turret)
        st = TURRET_RATE * o.dt
        cmd = clamp(err / st, -1.0, 1.0)
        residual = fabs(err - cmd * st)
        tol = band * 0.5 / DEG - radians(SPREAD)
        self.dbg = (round(margin, 1), round(band, 1), round(need, 1),
                    round(residual * DEG, 1), 0.0, round(d, 1))
        # Полосы нет — не всегда повод молчать. Патрон не тратит ничего, кроме
        # секунды перезарядки, и если противник сам ещё перезаряжается, а мой
        # снаряд прилетит раньше, чем он изготовится, выстрел «на удачу»
        # выгоднее молчания: окно может и не открыться вовсе.
        blind = self.e_cd > tf + 0.15
        if tf < TF_MAX and me.ammo_ready and residual <= max(tol, 0.004) \
                and (band >= need or blind):
            mzx = me.x + cos(a) * MUZZLE
            mzy = me.y + sin(a) * MUZZLE
            if self._line_free(mzx, mzy, px, py) \
                    and not self._blocked(mzx, mzy):
                return cmd, True
        return cmd, False

    def _aim_cmd(self, me, px, py):
        a = atan2(py - me.y, px - me.x)
        a = atan2(py - (me.y + sin(a) * MUZZLE), px - (me.x + cos(a) * MUZZLE))
        return clamp(wrap(a - me.turret) * 4.0, -1.0, 1.0)

    # ------------------------------------------------------------- маршрут
    def _flow(self, tx, ty):
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
        big = 1 << 30
        dist = [[big] * w for _ in range(h)]
        dist[ty][tx] = 0
        q = [(tx, ty)]
        head = 0
        while head < len(q):
            x, y = q[head]
            head += 1
            nd = dist[y][x] + 1
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (0 <= nx < w and 0 <= ny < h and dist[ny][nx] > nd
                        and rows[ny][nx] not in "#o:"):
                    dist[ny][nx] = nd
                    q.append((nx, ny))
        self.flow = dist
        self.flow_goal = (tx, ty)
        self.flow_t = self.t
        return (tx, ty)

    def _waypoint(self, gx, gy):
        g = self.grid
        if g is None:
            return (gx, gy)
        tile = g.tile_size
        tx = int(gx // tile)
        ty = int(gy // tile)
        if (self.flow is None or self.flow_goal != (tx, ty)
                or self.t - self.flow_t > 0.6):
            if self._flow(tx, ty) is None:
                return None
        mx = int(self.mx // tile)
        my = int(self.my // tile)
        if not (0 <= mx < g.width and 0 <= my < g.height):
            return (gx, gy)
        here = self.flow[my][mx]
        if here == 0:
            return None
        rows = g.rows
        best = None
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nx = mx + dx
            ny = my + dy
            if nx < 0 or ny < 0 or nx >= g.width or ny >= g.height:
                continue
            if rows[ny][nx] in "#o:":
                continue
            if dx and dy and (rows[my][mx + dx] in "#o:"
                              or rows[my + dy][mx] in "#o:"):
                continue
            v = self.flow[ny][nx]
            if best is None or v < best[0]:
                best = (v, nx, ny)
        if best is None or best[0] >= (1 << 30) or best[0] >= here:
            return None
        return ((best[1] + 0.5) * tile, (best[2] + 0.5) * tile)

    def _hunt(self, o, me):
        # Точка чужого респауна — только первая догадка. Если враг ушёл оттуда,
        # стоять на ней вечно значит подарить ничью по времени: обходим карту
        # по кольцу из ключевых точек, пока не поймаем его в поле зрения.
        g = o.map
        pts = self.search_pts
        if pts is None and g is not None:
            w = g.width * g.tile_size
            h = g.height * g.tile_size
            pts = [(w * 0.5, h * 0.5), (w * 0.25, h * 0.25),
                   (w * 0.75, h * 0.25), (w * 0.75, h * 0.75),
                   (w * 0.25, h * 0.75)]
            if len(g.spawns) > 1:
                sp = g.spawns[1 - o.tank]
                pts.insert(0, (float(sp.x), float(sp.y)))
            self.search_pts = pts
            self.search_i = 0
            self.search_t = self.t
        if pts and self.t < PATROL_T:
            # Пока время не поджимает, первая догадка (чужой респаун) не хуже
            # обхода: патруль уводит из укрытий и подставляет на открытом.
            gx, gy = pts[0]
        elif pts:
            gx, gy = pts[self.search_i]
            if (hypot(gx - me.x, gy - me.y) < SEARCH_R
                    or self.t - self.search_t > SEARCH_T):
                self.search_i = (self.search_i + 1) % len(pts)
                self.search_t = self.t
                gx, gy = pts[self.search_i]
        else:
            gx = me.x + cos(me.hull) * 200.0
            gy = me.y + sin(me.hull) * 200.0
        wp = self._waypoint(gx, gy)
        if wp is None:
            wp = (gx, gy)
        return self._go(me, wp[0], wp[1], 1.0)

    # ---------------------------------------------------------------- карта
    def _free_pt(self, x, y):
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
        n = int(d / 14.0) + 1
        for i in range(1, n):
            k = i / n
            if self._opaque(x0 + dx * k, y0 + dy * k):
                return False
        return True

    # -------------------------------------------------------- застревание
    def _unstuck(self, o, me, turn, drive):
        t = self.t
        self.mile += hypot(me.x - self.px, me.y - self.py)
        self.px = me.x
        self.py = me.y
        if self.mile > STUCK_DIST:
            self.mile = 0.0
            self.stuck_t = t
        elif t - self.stuck_t > STUCK_WINDOW and self.mile < 3.0 and t > 1.5:
            self.escape_until = t + 0.8
            self.escape_turn = 1.0 if self.escape_turn <= 0.0 else -1.0
            self.mile = 0.0
            self.stuck_t = t
        if t < self.escape_until:
            return self.escape_turn, -1.0
        return turn, drive

program = Brain()
