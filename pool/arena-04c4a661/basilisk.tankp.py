#!TANKP 1
# name: Basilisk
# author: arena-04c4a661
# difficulty: 5
# color: #b967ff
# description: Дуэлянт мёртвой зоны 30..40°: качание ствола на полной скорости башни (3.6 рад/с против 2.6 у чужого корпуса) срывает чужой ракурс, гейт огня по отрезку курса цели (луч должен пробивать при 3 из 4 гипотез), уклонение сменой хода от уже выпущенного снаряда.
# tags: ракурс,рикошет,качание ствола,отрезок курса,упреждение,уклонение

from math import acos, atan2, cos, sin, hypot, pi, degrees, radians, fabs

from tankp import TankProgram, Action, clamp, wrap

TAU = 2.0 * pi
DEG = 180.0 / pi
RAD = pi / 180.0

BULLET = 620.0
SPEED_F = 190.0
SPEED_R = 120.0
HULL_RATE = 2.6
TURRET_RATE = 3.6
MUZZLE = 26.0
BAND = 35.5              # середина мёртвой зоны [30, 40]
HALF_WID = 11.0

CFG = {
    "range_hold": 160.0,    # дистанция, пока враг заряжен
    "range_push": 110.0,     # дистанция, пока он перезаряжается
    "horizon": 0.10,        # горизонт предсказания линии удара, с
    "band_stick": 0.30,
    "need1": 0.0,           # запас пробоя при полёте <= 0.06 c
    "need2": 0.0,           # <= 0.12
    "need3": 1.0,           # <= 0.20
    "need4": 3.0,           # <= 0.35
    "need5": 10.0,          # дальше
    "lead_decay": 1.0,
    "push_cd": 0.35,
    "aim_lead_k": 2.0,     # какой горизонт упреждения считать линией удара
    "band_bias": -4.0,      # сдвиг ракурса наружу, градусы
    "escape_dist": 52.0,   # ближе — выходим из свалки, ракурс вторичен
    "drive_cd": 0.30,      # при чужой перезарядке тяга важнее ракурса
    "track_k": 1.0,        # вера в то, что враг ведёт ракурс за нашим пеленгом
    "hull_k": 0.35,        # доля экстраполяции собственного поворота врага
    "hull_sigma": 0.0,     # разброс прогноза курса цели, градусы (на 0.3 c)
    "cand_k": 1.0,          # размах точек прицеливания внутри силуэта
    "off_pen": 0.05,       # штраф за смещение точки прицеливания, за px
    "max_flight": 0.90,    # дальше этого полёта не стреляем
    "always_drive": 1.0,   # не стоим: ход и уклонение важнее стоянки
    "attack_cd": 9.0,      # его перезарядка выше — включаем «атаку» (9 = выкл)
    "attack_band": 60.0,   # ракурс корпуса в атаке, градусы
    "bar_d": 150.0,        # ближе этой дистанции линия удара — чужой ствол (0 = выкл)
    "bar_ahead": 0.06,     # опережение по стволу, с
    "bar_win": 45.0,       # ствол в этом секторе от пеленга считается угрозой, градусы
    "threat_lead": 0.0,    # упреждение по пеленгу для УЖЕ летящего снаряда (0 = нет)
    "swing_amp": 0.30,     # амплитуда качания ствола, рад (0 = выкл)
    "swing_far": 560.0,    # дальше этого ствол не качаем
    "swing_need": 3,       # при скольких гипотезах курса луч должен пробивать
    "dodge_lat": 24.0,     # ближе этого снаряд попадёт — уклоняемся
    "dodge_amt": 0.75,     # насколько резко меняем ход
}


HX = 20.0                     # полуразмер коробки, по которой считается попадание
HY = 14.0
FACE_HALF = 35.0
LIMITS = (30.0, 50.0, 60.0)
MAX_HULL_SPAN = 1.05   # предел разброса курса цели по времени полёта, рад   # порог рикошета: лоб, борт, корма


def ray_box(ox, oy, dx, dy, cx, cy, hh):
    """Вход луча в коробку — точная копия engine.geometry.segment_obb."""
    ca = cos(hh)
    sa = sin(hh)
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
    for p, q, h, i in ((lx, ldx, HX, 0), (ly, ldy, HY, 1)):
        if q < 1e-12 and q > -1e-12:
            if p > h or p < -h:
                return None
            continue
        sg = -1.0 if q > 0.0 else 1.0
        t1 = (-h - p) / q
        t2 = (h - p) / q
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = i
            sign = sg
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    if axis < 0:
        if fabs(ldx) >= fabs(ldy):
            nlx, nly = (-1.0 if ldx > 0.0 else 1.0), 0.0
        else:
            nlx, nly = 0.0, (-1.0 if ldy > 0.0 else 1.0)
        return (0.0, ox, oy, nlx * ca - nly * sa, nlx * sa + nly * ca)
    if axis == 0:
        px_l = sign * HX
        py_l = ly + tmin * ldy
        nlx, nly = sign, 0.0
    else:
        px_l = lx + tmin * ldx
        py_l = sign * HY
        nlx, nly = 0.0, sign
    return (tmin,
            cx + px_l * ca - py_l * sa,
            cy + px_l * sa + py_l * ca,
            nlx * ca - nly * sa,
            nlx * sa + nly * ca)


def shot_margin(ox, oy, dx, dy, cx, cy, hh):
    """Запас пробоя выстрела: > 0 — пробьёт, < 0 — рикошет или промах."""
    r = ray_box(ox, oy, dx, dy, cx, cy, hh)
    if r is None:
        return -1e9
    nx = r[3]
    ny = r[4]
    c = -(dx * nx + dy * ny)
    if c > 1.0:
        c = 1.0
    elif c < 0.0:
        c = 0.0
    rel = degrees(wrap(atan2(ny, nx) - hh))
    if rel < 0.0:
        rel = -rel
    if rel <= FACE_HALF:
        lim = LIMITS[0]
    elif rel >= 180.0 - FACE_HALF:
        lim = LIMITS[2]
    else:
        lim = LIMITS[1]
    return lim - degrees(acos(c))


def ang(a, b):
    return wrap(a - b)


class Brain(TankProgram):
    def on_start(self, ctx):
        self.swing = 0.0
        self.swing_dir = 1.0
        self.rot_dir = 1.0
        self.side = 1.0
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.eh = 0.0
        self.eu = 0.0
        self.eu_rate = 0.0
        self.et = -99.0
        self.ecd = 0.0
        self.seen_prev = False
        self.threats = []
        self.eh_rate = 0.0
        self.bear = None
        self.bear_rate = 0.0
        self.aim_prev = None
        self.aim_rate = 0.0
        self.goal = None
        self.path = []
        self.path_t = -99
        self.patrol_i = 0
        self.dbg_on = bool(ctx.get("debug")) if isinstance(ctx, dict) else False
        self.dbg = []

    # --- восприятие ---------------------------------------------------------

    def _update(self, o):
        e = o.enemy
        now = o.time
        dt = o.dt
        if e is not None:
            if self.seen_prev and now - self.et < 0.2:
                d = now - self.et
                if d > 1e-4:
                    r = ang(e.hull, self.eh) / d
                    if r > HULL_RATE:
                        r = HULL_RATE
                    elif r < -HULL_RATE:
                        r = -HULL_RATE
                    self.eh_rate = self.eh_rate * 0.6 + r * 0.4
            else:
                self.eh_rate *= 0.4
            if e.cooldown - self.ecd > 0.5:
                ox = self.ex + cos(self.eu) * MUZZLE + cos(self.eu) * BULLET * dt
                oy = self.ey + sin(self.eu) * MUZZLE + sin(self.eu) * BULLET * dt
                self.threats.append((ox, oy, cos(self.eu), sin(self.eu), now))
            self.ecd = e.cooldown
            self.ex, self.ey = e.x, e.y
            self.evx, self.evy = e.vx, e.vy
            if self.seen_prev and now - self.et < 0.2 and now - self.et > 1e-4:
                r = ang(e.turret, self.eu) / (now - self.et)
                if r > TURRET_RATE:
                    r = TURRET_RATE
                elif r < -TURRET_RATE:
                    r = -TURRET_RATE
                self.eu_rate = self.eu_rate * 0.6 + r * 0.4
            self.eh, self.eu = e.hull, e.turret
            self.et = now
            self.seen_prev = True
            b = atan2(e.y - o.me.y, e.x - o.me.x)
            if self.bear is not None:
                r = ang(b, self.bear) / max(1e-4, dt)
                if r > 7.0:
                    r = 7.0
                elif r < -7.0:
                    r = -7.0
                self.bear_rate = self.bear_rate * 0.55 + r * 0.45
            self.bear = b
        else:
            self.seen_prev = False
            self.bear_rate *= 0.9
        if self.threats:
            self.threats = [th for th in self.threats if now - th[4] < 2.0]

    def _threat(self, o):
        """Ближайший снаряд, который летит в нас: (ox, oy, dx, dy, t0, t_impact)."""
        me = o.me
        best = None
        for ox, oy, dx, dy, t0 in self.threats:
            age = o.time - t0
            bx = ox + dx * BULLET * age
            by = oy + dy * BULLET * age
            rx = me.x - bx
            ry = me.y - by
            along = dx * rx + dy * ry
            if along <= -5.0:
                continue
            lat = -dy * rx + dx * ry
            if lat > 38.0 or lat < -38.0:
                continue
            t = along / BULLET
            if t < 0.0:
                t = 0.0
            if t > 0.8:
                continue
            if best is None or t < best[5]:
                best = (ox, oy, dx, dy, t0, t)
        return best

    def _threat_aim(self, o, me):
        """Азимут угрозы (от меня к источнику огня) и время до удара.

        Стреляют из КОНЦА СТВОЛА, а он вынесен на 26 px от центра: на 90 px
        дистанции это 16° — больше ширины мёртвой зоны. Поэтому линию удара
        считаем от дула, а не от центра корпуса, и итерацией: дуло смотрит
        туда, куда целятся.
        """
        th = self._threat(o)
        if th is not None:
            return atan2(-th[3], -th[2]), th[5]
        e = o.enemy
        if e is not None:
            rx = me.x - e.x
            ry = me.y - e.y
            d = hypot(rx, ry)
            t = d / BULLET
            t = hypot(me.x + me.vx * t - e.x, me.y + me.vy * t - e.y) / BULLET
            # Снаряд вылетает из дула строго по стволу (разброс ±0.4°). Значит,
            # ракурс надо держать к СТВОЛУ, а не к линии визирования: тогда
            # любой его выстрел — с упреждением, по краю силуэта — придёт под
            # 35° и срикошетит. Но у ствола есть угловой сектор, в котором он
            # вообще может задеть наш корпус; вне его ракурс к стволу
            # бесполезен, и берём линию упреждения.
            # ВНИМАНИЕ: ствол цели смотрит ОТ НЕЁ К НАМ, поэтому сравнивать его
            # надо с пеленгом «от неё к нам», а не с обратным. Раньше здесь
            # стоял +pi, из-за чего расхождение выходило 180° и ветка «ракурс к
            # стволу» не включалась НИКОГДА: ракурс держался к линии упреждения,
            # а снаряд летит по стволу.
            bear = atan2(ry, rx)               # от неё к нам
            dv = fabs(wrap(e.turret - bear))
            tp = t * CFG["aim_lead_k"]
            ax = me.x + me.vx * tp
            ay = me.y + me.vy * tp
            mx = e.x + cos(e.turret) * MUZZLE
            my = e.y + sin(e.turret) * MUZZLE
            a = atan2(ay - my, ax - mx)
            for _ in range(2):
                mx = e.x + cos(a) * MUZZLE
                my = e.y + sin(a) * MUZZLE
                a = atan2(ay - my, ax - mx)
            if CFG["bar_d"] > 0.0 and d < CFG["bar_d"]:
                # Вплотную (50-120 px) дуло вынесено на 26 px от центра — это
                # 12-27° параллакса. Наша догадка «куда он метит» на такой
                # дистанции врёт сильнее, чем сам ствол: снаряд-то уходит ровно
                # по нему. Поэтому вплотную ракурс держим к стволу с поправкой
                # на его угловую скорость.
                tp2 = self.eu + self.eu_rate * CFG["bar_ahead"]
                if fabs(wrap(tp2 - bear)) < CFG["bar_win"] * RAD:
                    return tp2 + pi, t
            return a + pi, t
        if o.time - self.et < 2.5:
            return atan2(self.ey - me.y, self.ex - me.x), 0.3
        return None, 0.0

    # --- огонь --------------------------------------------------------------

    def _need(self, t):
        if t <= 0.06:
            return CFG["need1"]
        if t <= 0.12:
            return CFG["need2"]
        if t <= 0.20:
            return CFG["need3"]
        if t <= 0.35:
            return CFG["need4"]
        return CFG["need5"]

    #: Точки прицеливания в системе координат цели: центр и окрестности.
    #: Коробка брони 40x28, поэтому сдвиг на 13 px остаётся внутри силуэта с
    #: запасом, но меняет угол входа в грань на atan(13/d) — на 150 px это 5°,
    #: а мёртвая зона всего 10° шириной.
    CANDS = ((0.0, 0.0),
             (13.0, 0.0), (-13.0, 0.0), (0.0, 9.0), (0.0, -9.0),
             (13.0, 9.0), (13.0, -9.0), (-13.0, 9.0), (-13.0, -9.0),
             (0.0, 13.0), (0.0, -13.0), (18.0, 0.0), (-18.0, 0.0),
             (17.0, 11.0), (-17.0, 11.0), (17.0, -11.0), (-17.0, -11.0))
    CANDS = tuple((x * CFG["cand_k"], y * CFG["cand_k"]) for x, y in CANDS)

    def _margin(self, o, me, vx, vy):
        """Прогноз стрельбы при нашей скорости (vx, vy): (запас, время, курс, ствол)."""
        e = o.enemy
        ld = CFG["lead_decay"]
        t = hypot(e.x - me.x, e.y - me.y) / BULLET
        tx = e.x + e.vx * t * ld
        ty = e.y + e.vy * t * ld
        t = hypot(tx - me.x, ty - me.y) / BULLET
        tx = e.x + e.vx * t * ld
        ty = e.y + e.vy * t * ld
        t = hypot(tx - me.x, ty - me.y) / BULLET
        # Курс цели на момент прилёта: она держит ракурс к пеленгу на нас, а
        # пеленг едет вместе с нами. Ошибка такого прогноза — единицы градусов
        # на коротком полёте и десяток на длинном, а щель мёртвой зоны всего
        # 10°, поэтому запас считаем не по одной точке, а по коридору курсов.
        rho0 = atan2(me.y - e.y, me.x - e.x)
        off = wrap(self.eh - rho0)
        k = CFG["track_k"]
        if k > 0.0:
            rho = atan2(me.y + vy * t - ty, me.x + vx * t - tx)
            hp = off + rho
            if k < 1.0:
                hp = self.eh + wrap(hp - self.eh) * k
        else:
            hr = self.eh_rate * CFG["hull_k"]
            if hr > HULL_RATE:
                hr = HULL_RATE
            elif hr < -HULL_RATE:
                hr = -HULL_RATE
            hp = self.eh + hr * t
        sig = CFG["hull_sigma"]
        if sig > 0.0:
            sig *= 0.3 + 2.3 * t
        ch = cos(hp)
        sh = sin(hp)
        best = None
        for ux, uy in self.CANDS:
            px = tx + ch * ux - sh * uy
            py = ty + sh * ux + ch * uy
            a = me.turret
            for _ in range(3):
                mx = me.x + cos(a) * MUZZLE
                my = me.y + sin(a) * MUZZLE
                a = atan2(py - my, px - mx)
            mx = me.x + cos(a) * MUZZLE
            my = me.y + sin(a) * MUZZLE
            ca = cos(a)
            sa = sin(a)
            m0 = shot_margin(mx, my, ca, sa, tx, ty, hp)
            m = m0
            if sig > 0.0:
                for dh in (hp - sig, hp + sig):
                    mm = shot_margin(mx, my, ca, sa, tx, ty, dh)
                    if mm < m:
                        m = mm
            if m > -1e8:
                m -= CFG["off_pen"] * hypot(ux, uy)
            if best is None or m > best[0]:
                best = (m, a, m0)
        self.ltx = tx
        self.lty = ty
        return best[0], t, hp, best[1], best[2]

    def _aim_fire(self, o):
        me = o.me
        if o.enemy is None:
            return 0.0, False
        m, t, hp, a, m0 = self._margin(o, me, me.vx, me.vy)
        tx = self.ltx
        ty = self.lty
        d = hypot(o.enemy.x - me.x, o.enemy.y - me.y)
        step = TURRET_RATE * o.dt
        # «Качание ствола»: чужой корпус держит ракурс к нашему стволу, но
        # доворачивается не быстрее 2.6 рад/с, а башня идёт 3.6. Ствол качается
        # поперёк азимута на полной скорости, и к моменту нашего выстрела чужой
        # корпус стоит НЕ под 35°, а там, куда его увели: за время полёта он
        # отстаёт, и удар выходит из мёртвой зоны.
        # Вплотную амплитуду НЕ уменьшать: на 64 px полуразмер цели это 17°,
        # то есть 0.30 рад уже край силуэта. Проверено — выключение качания
        # ближе 150 px стоит 3-1-36 против osa (было 4-16-20): не «ствол
        # уводит за цель», а «перестают появляться моменты, когда чужой
        # корпус вне мёртвой зоны».
        amp = CFG["swing_amp"]
        if amp > 0.0 and d < CFG["swing_far"]:
            sw = self.swing + self.swing_dir * step
            if sw > amp:
                sw = amp - (sw - amp)
                self.swing_dir = -1.0
            elif sw < -amp:
                sw = -amp + (-amp - sw)
                self.swing_dir = 1.0
            self.swing = sw
        else:
            self.swing = 0.0
        turret = clamp(wrap(a + self.swing - me.turret) / step, -1.0, 1.0)
        if not me.ammo_ready or t > CFG["max_flight"]:
            return turret, False
        # снаряд уйдёт ровно по стволу после этого тика — по нему и считаем
        dirv = me.turret + turret * step
        dx = cos(dirv)
        dy = sin(dirv)
        mx = me.x + dx * MUZZLE
        my = me.y + dy * MUZZLE
        if o.map is not None and o.map.blocked_between(mx, my, tx, ty):
            return turret, False
        # Курс цели к прилёту — не точка, а отрезок: корпус крутится не
        # быстрее HULL_RATE, значит за t он покроет +-HULL_RATE*t. Луч годится,
        # только если пробивает почти при всех таких гипотезах.
        st = HULL_RATE * t
        if st > MAX_HULL_SPAN:
            st = MAX_HULL_SPAN
        need = self._need(t)
        good = 0
        pred = -1e9
        for k in range(4):
            if k == 0:
                hh = hp
            elif k == 1:
                hh = hp + st
            elif k == 2:
                hh = hp - st
            else:
                hh = self.eh + self.eh_rate * t
            mm = shot_margin(mx, my, dx, dy, tx, ty, hh)
            if k == 0:
                pred = mm
            if mm >= 0.0:
                good += 1
        if good < CFG["swing_need"] or pred < need:
            return turret, False
        if self.dbg_on:
            self.dbg.append(("F", o.tick, round(pred, 1), round(t, 3), good,
                             round(degrees(dirv), 1)))
        return turret, True

    # --- управление корпусом ------------------------------------------------

    @staticmethod
    def _hull_cmd(w, d0):
        """(turn, drive) на заданную угловую скорость корпуса при данной тяге.

        Физика движка: w = 2.6·|turn| / (|turn| + |drive|). Отсюда жёсткая связь
        «газ платит за доворот»: при |drive| = 1 корпус крутится не быстрее
        1.3 рад/с, а полные 2.6 доступны только на месте. Обратная сторона не
        менее важна: НУЛЕВАЯ тяга даёт только 0 или 2.6 — плавно доворачивать
        можно лишь подгазовывая, поэтому ниже держим минимальную тягу.
        """
        aw = w if w >= 0.0 else -w
        if aw > HULL_RATE:
            aw = HULL_RATE
        ad = d0 if d0 >= 0.0 else -d0
        if aw > 0.02 and ad < 0.07:
            ad = 0.07
        if aw >= HULL_RATE - 1e-4:
            t = 1.0
            ad = 0.0
        else:
            rho = aw / (HULL_RATE - aw)
            t = rho * ad
            if t > 1.0:
                t = 1.0
                ad = 1.0 / rho
            elif t < 0.0:
                t = 0.0
        return (t if w >= 0.0 else -t), (ad if d0 >= 0.0 else -ad)

    # --- навигация ----------------------------------------------------------

    def _astar(self, o, gx, gy):
        m = o.map
        ts = m.tile_size
        rows = m.rows
        w, h = m.width, m.height

        def ok(tx, ty):
            if tx < 0 or ty < 0 or tx >= w or ty >= h:
                return False
            return rows[ty][tx] not in "#o:"

        sx = int(o.me.x // ts)
        sy = int(o.me.y // ts)
        tx = int(gx // ts)
        ty = int(gy // ts)
        if not ok(tx, ty):
            best = None
            for dy in range(-3, 4):
                for dx in range(-3, 4):
                    if ok(tx + dx, ty + dy):
                        dd = dx * dx + dy * dy
                        if best is None or dd < best[0]:
                            best = (dd, tx + dx, ty + dy)
            if best is None:
                return []
            tx, ty = best[1], best[2]
        if (sx, sy) == (tx, ty):
            return [(gx, gy)]
        openq = {(sx, sy): 0.0}
        came = {}
        g = {(sx, sy): 0.0}
        closed = set()
        while openq:
            cur = min(openq, key=lambda k: openq[k])
            del openq[cur]
            if cur == (tx, ty):
                break
            closed.add(cur)
            cx, cy = cur
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in closed or not ok(nx, ny):
                    continue
                if dx and dy and (not ok(cx + dx, cy) or not ok(cx, cy + dy)):
                    continue
                ng = g[cur] + (1.4142 if dx and dy else 1.0)
                if ng < g.get((nx, ny), 1e9):
                    g[(nx, ny)] = ng
                    came[(nx, ny)] = cur
                    openq[(nx, ny)] = ng + (abs(nx - tx) + abs(ny - ty)) * 1.05
        if (tx, ty) != (sx, sy) and (tx, ty) not in came:
            return []
        path = []
        cur = (tx, ty)
        while cur != (sx, sy):
            path.append(((cur[0] + 0.5) * ts, (cur[1] + 0.5) * ts))
            cur = came.get(cur)
            if cur is None:
                return []
        path.reverse()
        return path

    def _approach(self, o, gx, gy):
        me = o.me
        if o.tick - self.path_t > 20 or not self.path:
            self.path = self._astar(o, gx, gy)
            self.path_t = o.tick
        path = self.path
        if not path:
            want = atan2(gy - me.y, gx - me.x)
        else:
            i = 0
            while i < len(path) - 1 and hypot(path[i][0] - me.x, path[i][1] - me.y) < 24.0:
                i += 1
            want = atan2(path[i][1] - me.y, path[i][0] - me.x)
        err = wrap(want - me.hull)
        drive = 1.0 if fabs(err) < 1.0 else 0.3
        return self._hull_cmd(err * 4.0, drive)

    def _goal(self, o):
        me = o.me
        if o.time - self.et < 6.0:
            self.goal = (self.ex, self.ey)
        else:
            sp = o.map.spawns[1 - o.tank]
            if self.goal is None or hypot(self.goal[0] - me.x, self.goal[1] - me.y) < 70.0:
                w = o.map.pixel_width
                h = o.map.pixel_height
                corners = [(w * 0.22, h * 0.22), (w * 0.78, h * 0.22),
                           (sp.x, sp.y), (w * 0.78, h * 0.78), (w * 0.22, h * 0.78)]
                self.patrol_i = (self.patrol_i + 1) % len(corners)
                self.goal = corners[self.patrol_i]
        return self.goal

    # --- основной такт ------------------------------------------------------

    def on_tick(self, o):
        me = o.me
        self._update(o)
        e = o.enemy
        contact = (e is not None) or (o.time - self.et < 1.5)

        turret, fire = self._aim_fire(o)

        if not contact:
            gx, gy = self._goal(o)
            turn, drive = self._approach(o, gx, gy)
            return Action(drive=drive, turn=turn, turret=0.0, fire=False)

        aim, t_hit = self._threat_aim(o, me)
        if aim is None:
            return Action(drive=0.0, turn=0.0, turret=turret, fire=False)

        # скорость самой линии угрозы: она нужна как упреждение в регуляторе,
        # потому что линия ствола и линия визирования едут по-разному
        if self.aim_prev is not None:
            dr = ang(aim, self.aim_prev) / max(1e-4, o.dt)
            if fabs(dr) > 6.0:
                dr = 0.0
            self.aim_rate = self.aim_rate * 0.55 + dr * 0.45
        self.aim_prev = aim
        th = self._threat(o)
        e_cd = e.cooldown if e is not None else 1.0
        dist = hypot(self.ex - me.x, self.ey - me.y)
        if th is not None:
            # линия снаряда зафиксирована в мире: доворачиваем так, чтобы
            # НА МОМЕНТ УДАРА ракурс был 35.5° к этой линии
            ti = t_hit if t_hit > 0.03 else 0.03
            # Линия уже выпущенного снаряда ЗАФИКСИРОВАНА в мире: угол удара
            # зависит только от его направления и нашего курса, поэтому
            # никакого упреждения по скорости пеленга тут быть не должно —
            # пеленг едет, а линия нет. Ошибка в bear_rate*horizon (4-5°)
            # ровно и съедала запас мёртвой зоны.
            ap = aim + self.bear_rate * CFG["horizon"] * CFG["threat_lead"]
            c1 = ap + BAND * RAD
            c2 = ap - BAND * RAD
            d1 = fabs(wrap(c1 - me.hull))
            d2 = fabs(wrap(c2 - me.hull))
            if d1 <= d2:
                w = wrap(c1 - me.hull) / ti
                self.side = 1.0
            else:
                w = wrap(c2 - me.hull) / ti
                self.side = -1.0
            # Уклонение: снаряд летит по прямой, а упреждение чужой стрельбы
            # считалось по нашей скорости. Смена хода на полном газу сдвигает
            # нас на 2·v·t поперёк его расчёта — при t = 0.2 c это 76 px,
            # втрое больше корпуса. Ракурс при этом не трогаем: он считается
            # для этой же линии.
            bx = th[0] + th[2] * BULLET * (o.time - th[4])
            by = th[1] + th[3] * BULLET * (o.time - th[4])
            cur = fabs(-th[3] * (me.x - bx) + th[2] * (me.y - by))
            best = None
            for sg in (1.0, -1.0):
                sp = SPEED_F if sg > 0.0 else SPEED_R
                px = me.x + cos(me.hull) * sp * ti * sg
                py = me.y + sin(me.hull) * sp * ti * sg
                lat = fabs(-th[3] * (px - bx) + th[2] * (py - by))
                if best is None or lat > best[0]:
                    best = (lat, sg)
            if best[0] > cur and cur < CFG["dodge_lat"]:
                want = best[1] * CFG["dodge_amt"]
            else:
                want = 0.0
        else:
            # до выстрела линия удара едет вместе с пеленгом — держим ракурс
            # к ней через горизонт предсказания
            h = CFG["horizon"]
            ap = aim + self.bear_rate * h
            bd = (BAND + CFG["band_bias"]) * RAD
            if e_cd > CFG["attack_cd"]:
                # он перезаряжается — ракурс можно не беречь: разворачиваем
                # корпус почти по касательной, чтобы пеленг на нас ехал
                # втрое быстрее и его корпус не успевал за ним к прилёту
                bd = CFG["attack_band"] * RAD
            c1 = ap + bd
            c2 = ap - bd
            d1 = fabs(wrap(c1 - me.hull))
            d2 = fabs(wrap(c2 - me.hull))
            if self.side > 0:
                if d2 + CFG["band_stick"] < d1:
                    self.side = -1.0
            else:
                if d1 + CFG["band_stick"] < d2:
                    self.side = 1.0
            if self.side > 0:
                w = wrap(c1 - me.hull) / h
            else:
                w = wrap(c2 - me.hull) / h
            if e_cd > CFG["push_cd"]:
                r_hi = CFG["range_push"] + 34.0
                r_lo = CFG["range_push"] - 20.0
            else:
                r_hi = CFG["range_hold"] + 35.0
                r_lo = CFG["range_hold"] - 50.0
            if CFG["always_drive"] > 0.0:
                # Стоять нельзя: свой ход — это и упреждение для ЧУЖОГО ракурса
                # (пеленг на нас едет, и на момент прилёта его корпус уже не в
                # мёртвой зоне), и уклонение от уже выпущенного снаряда.
                want = 1.0 if dist > (r_hi + r_lo) * 0.5 else -1.0
            else:
                want = 0.0
                if dist > r_hi:
                    want = 1.0
                elif dist < r_lo:
                    want = -0.9
            if dist < CFG["escape_dist"]:
                # свалка в упор: там геометрия скачет каждый тик, и держать
                # ракурс всё равно не выйдет — зато выйти из неё можно тягой
                want = -1.0
            elif e_cd > CFG["drive_cd"] and dist < CFG["range_hold"]:
                pass
            # тяга важнее ракурса, когда враг всё равно не может стрелять
            if want != 0.0 and (e_cd > CFG["drive_cd"] or dist < CFG["escape_dist"]):
                ad = want if want >= 0.0 else -want
                wmax = HULL_RATE * ad / (1.0 + ad)
                if w > wmax:
                    w = wmax
                elif w < -wmax:
                    w = -wmax

        turn, drive = self._hull_cmd(w, want)
        if self.dbg_on:
            self.dbg.append((o.tick, me.hull, aim, w, t_hit,
                             1.0 if th is not None else 0.0, e_cd))

        # стены
        m = o.map
        if m is not None:
            fx, fy = cos(me.hull), sin(me.hull)
            sgn = 1.0 if drive >= 0.0 else -1.0
            ahead = m.clearance(me.x + fx * 50.0 * sgn, me.y + fy * 50.0 * sgn)
            if ahead < 24.0 and fabs(drive) > 0.05:
                turn, drive = self._hull_cmd(w, 0.0)
            if dist > 220.0 and m.blocked_between(me.x, me.y, self.ex, self.ey):
                turn, drive = self._approach(o, self.ex, self.ey)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)


program = Brain()
