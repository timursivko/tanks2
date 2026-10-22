#!TANKP 1
# name: Vektor
# author: arena-ae8ceb49
# difficulty: 5
# color: #7ad7f0
# description: Держит ракурс 35° (мёртвая зона брони 30..40°), стреляет по
#   посчитанной вероятности пробития в момент выстрела, работает темпом
#   «выстрелил — отступил — ворвался» и придерживает ход вплотную, чтобы
#   корпус успевал доворачиваться за линией прихода.
# tags: ракурс,рикошет,вероятность пробития,темп,дистанция,перезарядка

"""Vektor — тактика, выведенная из геометрии брони движка.

Три факта, на которых держится всё:

* урон не режется бронёй: пробитие стоит ровно 4 hp при 10 hp, поэтому танка
  убивают три пробития, а рикошет не стоит ничего, кроме секунды перезарядки;
* борт держит удар с 50°, лоб — с 30°, корма не держит вовсе. Если ракурс
  (угол между носом цели и линией «цель → стрелок») равен φ, то луч в центр
  корпуса приходит в лоб под углом φ, а в борт — под углом 90° − φ. Значит
  пробить можно при φ < 30 (в лоб) или при φ > 40 (в борт), а полоса
  30…40° — мёртвая зона, где рикошетят обе грани;
* за 60 с фиксируется ничья, поэтому частичный урон не стоит ничего: нужны
  ровно три пробития.

Отсюда три обязанности, и они враждуют между собой.

Своя поза. Держать нос под 35° к линии прихода: любой снаряд противника
приходит либо в лоб под 35° (порог 30), либо в борт под 55° (порог 50), и
рикошетит. Корпус успевает поворачиваться только на 2.6 рад/с, а линия
прихода в свалке крутится быстрее, поэтому вплотную ход придерживается: на
сближении скорость режется пропорционально дистанции, иначе поворот тратит
весь бюджет команды и ракурс уезжает из полосы.

Свой выстрел. Стрелять только тогда, когда снаряд действительно пробьёт:
броня считается по настоящей коробке попадания и по курсу цели на момент
удара. Курс цели приходит в движение, поэтому вероятность пробития — это
доля гипотез о её угловой скорости, при которых броня не держит. Против
неподвижной цели, вставшей в мёртвую зону, вероятность равна нулю и патрон
не тратится; против вертлявой — стрелять надо.

Темп. Патрон дороже выстрела, потому что рикошет тратит секунду
перезарядки: пока он разряжен, дистанция сокращается до упора, пока
перезаряжаемся мы — растёт.

Ствол вынесен на 26 px от центра, поэтому ближе ~45 px дуло противника
оказывается внутри нашего корпуса и удар считается под углом ракурса —
дистанция свалки 45 px держится как нижняя граница, а в упор идём только к
разряженному противнику.
"""

import os
from math import atan2, cos, degrees, fabs, hypot, pi, radians as rad, sin

from tankp import Action, TankProgram, clamp, wrap

INF = float("inf")
DEG = 180.0 / pi

# --- факты движка (config.Balance) -----------------------------------------
BULLET = 620.0          # скорость снаряда
MUZZLE = 26.0           # центр танка → конец ствола
TURRET_RATE = 3.6       # рад/с; башня абсолютная и не завязана на корпус
HULL_RATE = 2.6         # рад/с при развороте на месте
DT = 1.0 / 60.0
THX = 20.0              # полуразмер коробки попадания (полкорпуса + радиус)
THY = 14.0
FACE_HALF = 35.0        # ± от курса корпуса — ещё лоб
RICO_FRONT = 30.0
RICO_SIDE = 50.0

# --- тактика ----------------------------------------------------------------
DEAD = 35.0             # целевой ракурс: середина мёртвой зоны 30..40°
SIDE_HYST = 22.0        # градусы: насколько другая сторона должна быть ближе
KG = 5.0                # градусы: усиление по ошибке курса
FF = 1.6                # доля feed-forward по скорости вращения линии прихода
HSM = 0.25              # сглаживание оценки угловой скорости корпуса цели
P_MIN = float(os.environ.get("EX_PMIN", "0.6"))             # с какой вероятностью пробития тратим патрон
W0 = 2.0                # градусы: базовая неуверенность в курсе цели
AIM_SPREAD = rad(0.45)  # разброс снаряда (±0.4°) плюс ошибка наведения
# Перебор углов ствола: вплотную полоса пробития открывается не по центру, а
# в 10..25° от линии визирования — там луч входит в борт или в лоб под
# острым углом. Точный шаг дорог, поэтому сетка редкая, а последние градусы
# добираются дважды (перебор идёт по возрастанию цены смещения).
OFFSETS = ((0.0, -5.0, 5.0, -9.0, 9.0, -13.0, 13.0, -17.0, 17.0,
            -21.0, 21.0, -25.0, 25.0, -30.0, 30.0, -36.0, 36.0)
           if os.environ.get("EX_WIDE", "1") == "1"
           else (0.0, -5.0, 5.0, -9.0, 9.0, -14.0, 14.0))
D_HOLD = 45.0           # держим, пока оба заряжены
D_CHARGE = 22.0         # врываемся, пока он разряжен
D_RETREAT = 115.0       # отходим, пока перезаряжаемся
D_GRIND = 38          # куда идём, если ракурс цели не даётся
POST_D = 55.0           # до какой дистанции бьём того, кто держит ракурс к стволу
POST_FIT = 0.55         # доля подходящих тиков, при которой считаем его ракурсным
GRIND_T = float(os.environ.get("EX_GRIND", "1.3"))           # c: столько без выстрела — и идём в упор
SLOW_D = 90.0           # до какой дистанции придерживаем ход
JINK = 0.75             # амплитуда маятника газом
JINK_T = (0.15, 0.45)   # полупериод маятника, c
JINK_R = 220.0          # докуда работает маятник, px
CLOSE_MIN = 19.0        # ближе не подпускаем: толкотня корпусами
STUCK_D = 12.0
STUCK_WIN = 0.9


def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang, max_t=INF):
    """Луч против повёрнутой коробки: (t, nx, ny) или None.

    Копия engine.geometry.segment_obb вместе с вырожденным случаем «луч уже
    внутри коробки»: в упор дуло оказывается внутри чужого корпуса, и грань
    там выбирается по ведущей оси полёта, а не по точке входа.
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


def pen_margin(nx, ny, dx, dy, hull):
    """Запас до рикошета в градусах: > 0 — пробитие, < 0 — отскок."""
    nlen = hypot(nx, ny)
    if nlen <= 0.0:
        return -99.0
    nx /= nlen
    ny /= nlen
    c = -(dx * nx + dy * ny)
    if c > 1.0:
        c = 1.0
    elif c < -1.0:
        c = -1.0
    s = -(dx * ny - dy * nx)
    theta = atan2(fabs(s), c) * DEG
    rel = wrap(atan2(ny, nx) - hull) * DEG
    if rel < 0.0:
        rel = -rel
    if rel <= FACE_HALF:
        limit = RICO_FRONT
    elif rel >= 180.0 - FACE_HALF:
        limit = INF          # корма не держит удар никогда
    else:
        limit = RICO_SIDE
    return limit - theta


class Brain(TankProgram):
    """Мёртвая зона, вероятность пробития и темп перезарядки."""

    # ------------------------------------------------------------------ старт

    def on_start(self, ctx):
        self.last_tick = -1
        self.grid = None
        # --- цель
        self.ex = 0.0
        self.ey = 0.0
        self.evx = 0.0
        self.evy = 0.0
        self.ehull = 0.0
        self.etur = 0.0
        self.ehp = 10.0
        self.ecd = 0.0
        self.ecd_prev = 0.0
        self.et = -99.0
        self.seen = False
        self.h_rate = 0.0
        self.bear_rate = 0.0
        self.bear_prev = 0.0
        self.bear_t = -99.0
        self.ps_n = 0
        self.ps_fit = 0
        self.ps_rel = 0.0
        self.posturer = False
        # --- чужие выстрелы: (x, y, dx, dy, момент)
        self.shots = []
        # --- маршрут
        self.field = None
        self.field_goal = None
        self.field_t = -9.0
        self.spawn_goal = None
        self.lx = None
        self.ly = None
        self.mile = 0.0
        self.mile_t = 0.0
        self.esc_until = -9.0
        self.esc_turn = 1.0
        self.esc_n = 0
        # --- поза и темп
        self.side = 1.0
        self.fire_t = -9.0
        self.jink_t = -9.0
        self.jink_s = 1.0
        self.seed = 12345

    # ---------------------------------------------------------------- память

    def _remember(self, o):
        """Обновляет память о противнике. True, если он виден сейчас."""
        t = o.time
        e = o.enemy
        if e is None:
            return False
        if self.seen:
            dt = t - self.et
            if 1e-4 < dt < 0.3:
                raw = wrap(e.hull - self.ehull) / dt
                lim = HULL_RATE * 1.3
                if raw > lim:
                    raw = lim
                elif raw < -lim:
                    raw = -lim
                self.h_rate += (raw - self.h_rate) * HSM
            elif dt > 0.3:
                self.h_rate *= 0.5
        # выстрел врага виден по скачку перезарядки с нуля к полной
        if self.ecd_prev < 0.2 and e.cooldown > 0.85:
            d = e.turret
            self.shots.append((e.x + cos(d) * MUZZLE, e.y + sin(d) * MUZZLE,
                               cos(d), sin(d), t))
            if len(self.shots) > 4:
                del self.shots[0]
        self.ecd_prev = e.cooldown
        self.ecd = e.cooldown
        self.ex = e.x
        self.ey = e.y
        self.evx = e.vx
        self.evy = e.vy
        self.ehull = e.hull
        self.etur = e.turret
        self.ehp = e.hp
        self.et = t
        self.seen = True
        return True

    def _ecool(self, o):
        """Перезарядка противника: сейчас или по памяти с поправкой на возраст."""
        if o.enemy is not None:
            return o.enemy.cooldown
        if not self.seen:
            return 0.0
        c = self.ecd - (o.time - self.et)
        return c if c > 0.0 else 0.0

    # -------------------------------------------------------------- стены

    def _wall_hit(self, mx, my, dx, dy, maxd):
        """Расстояние до первой непрозрачной стены по лучу (DDA по тайлам)."""
        g = self.grid
        if g is None:
            return INF
        tile = g.tile_size
        rows = g.rows
        x = int(mx // tile)
        y = int(my // tile)
        if dx > 0.0:
            sx = 1
            tdx = tile / dx
            tmx = ((x + 1) * tile - mx) / dx
        elif dx < 0.0:
            sx = -1
            tdx = -tile / dx
            tmx = (x * tile - mx) / dx
        else:
            sx = 0
            tdx = INF
            tmx = INF
        if dy > 0.0:
            sy = 1
            tdy = tile / dy
            tmy = ((y + 1) * tile - my) / dy
        elif dy < 0.0:
            sy = -1
            tdy = -tile / dy
            tmy = (y * tile - my) / dy
        else:
            sy = 0
            tdy = INF
            tmy = INF
        t = 0.0
        guard = 0
        while guard < 200:
            guard += 1
            if tmx < tmy:
                t = tmx
                tmx += tdx
                x += sx
            else:
                t = tmy
                tmy += tdy
                y += sy
            if t > maxd:
                return INF
            if rows[y][x] in "#o":
                return t
        return INF

    def _clear(self, mx, my, dx, dy, dist):
        return self._wall_hit(mx, my, dx, dy, dist) >= dist

    # ------------------------------------------------------------ стрельба

    def _hit_time(self, me, u):
        """Время полёта до прогнозной точки перехвата под углом u."""
        dx = cos(u)
        dy = sin(u)
        mx = me.x + dx * MUZZLE
        my = me.y + dy * MUZZLE
        t = 0.0
        for _ in range(3):
            s = (self.ex + self.evx * t - mx) * dx + (self.ey + self.evy * t - my) * dy
            if s <= 2.0 or s / BULLET > 1.2:
                return None, 0.0, 0.0
            t = s / BULLET
        return t, mx + dx * s, my + dy * s

    def _shot(self, me, u, na=1):
        """Выстрел под углом u: (лучший запас пробития, вероятность пробития).

        Сначала ищется момент удара: коробка цели подвигается на своём ходу, а
        курс доворачивается на её угловой скорости, и так до тех пор, пока
        точка входа луча в коробку не перестанет меняться. Дальше перебираются
        гипотезы: несколько вариантов курса цели (неизвестна её угловая
        скорость) и, если просят, несколько вариантов угла ствола. Доля
        вариантов, где броня не держит, — это и есть вероятность пробития.

        Разброс ствола здесь принципиален. Без него выстрел, который проходит
        по самой кромке корпуса, выглядит надёжным: удар приходится в угол, и
        классификация грани меняется от десятых долей градуса. Такой выстрел
        рикошетит через раз, а патрон уже потрачен.
        """
        dx = cos(u)
        dy = sin(u)
        mx = me.x + dx * MUZZLE
        my = me.y + dy * MUZZLE
        t = 0.0
        s = 0.0
        for _ in range(3):
            cx = self.ex + self.evx * t
            cy = self.ey + self.evy * t
            h = self.ehull + self.h_rate * t
            res = ray_obb(mx, my, dx, dy, cx, cy, THX, THY, h, 900.0)
            if res is None:
                return -99.0, 0.0
            s = res[0]
            t = s / BULLET
            if t > 1.0:
                return -99.0, 0.0
        # Снаряд вылетает уже после того, как оба корпуса отходили свой такт:
        # наблюдение всегда отстаёт на 1/60 с. На дистанции это мелочь, а
        # вплотную — 2.5° чужого разворота, ровно на границе мёртвой зоны.
        cx = self.ex + self.evx * t
        cy = self.ey + self.evy * t
        h0 = self.ehull + self.h_rate * t
        # Разброс гипотез: своя неуверенность в курсе плюс цена одного такта
        # (курс цели к моменту удара успевает уехать ещё на один шаг).
        w = rad(W0 + float(os.environ.get("EX_W", "2.5"))) + fabs(self.h_rate) * t * 0.35
        if self.et < 0.0:
            w += rad(8.0)
        nh = 5
        best = -99.0
        good = 0
        tot = 0
        for a in range(na):
            if na == 1:
                uu = u
            else:
                uu = u + AIM_SPREAD * (2.0 * a / (na - 1) - 1.0)
            ax = cos(uu)
            ay = sin(uu)
            axx = me.x + ax * MUZZLE
            ayy = me.y + ay * MUZZLE
            for k in range(nh):
                h = h0 + w * (2.0 * k / (nh - 1) - 1.0)
                res = ray_obb(axx, ayy, ax, ay, cx, cy, THX, THY, h, s + 40.0)
                if res is None:
                    continue
                tot += 1
                m = pen_margin(res[1], res[2], ax, ay, h)
                if m > best:
                    best = m
                if m >= 0.0:
                    good += 1
        if best < -90.0:
            return -99.0, 0.0
        return best, good / float(tot) if tot else 0.0

    def _pen(self, me, u, full=False):
        """(запас пробития в градусах, вероятность пробития)."""
        return self._shot(me, u, na=3) if full else self._shot(me, u)

    def _style(self, o, me):
        """Стиль врага: держит ли он ракурс к нашему стволу или к пеленгу.

        Ракурс к стволу — отдельный архетип: танк считает, что снаряд летит
        ровно по стволу, и доворачивает корпус под DEAD именно к нему. Наш
        выстрел «в полосу» по собственной развёртке против такого бесполезен:
        пока снаряд летит, корпус успевает переехать. Архетип ловится
        статистикой: наш ствол смотрит на цель, а её ракурс к стволу стабилен
        и держится в 28…46°.
        """
        if fabs(wrap(me.turret - atan2(self.ey - me.y, self.ex - me.x))) > rad(18.0):
            return
        rel = degrees(fabs(wrap(self.ehull - (me.turret + pi))))
        if self.ps_n == 0:
            self.ps_rel = rel
        else:
            self.ps_rel += (rel - self.ps_rel) * 0.1
        self.ps_n += 1
        if fabs(rel - self.ps_rel) < 7.0 and 28.0 <= self.ps_rel <= 46.0:
            self.ps_fit += 1
        if self.ps_n >= 45 and self.ps_fit >= POST_FIT * self.ps_n:
            self.posturer = True

    def _gun(self, o, me):
        """Наведение и выстрел. Возвращает (turret_cmd, fire)."""
        d = hypot(self.ex - me.x, self.ey - me.y)
        if d < 1.0:
            return 0.0, False
        u_des = atan2(self.ey - me.y, self.ex - me.x)
        t, px, py = self._hit_time(me, u_des)
        if t is not None:
            u_des = atan2(py - me.y, px - me.x)
        cmd = clamp(wrap(u_des - me.turret) * 6.0, -1.0, 1.0)
        if not me.ammo_ready:
            return cmd, False
        # ствол после этого тика: стрелять можно только туда, куда он смотрит
        barrel = wrap(me.turret + cmd * TURRET_RATE * DT)
        if os.environ.get("EX_ST", "1") == "1" and self.posturer and d > POST_D:
            # Он держит ракурс к стволу: на этой дистанции любой наш луч
            # приходит в мёртвую зону уже после того, как корпус переехал.
            # Такому ответ один — вплотную, где дуло внутри чужого корпуса.
            return cmd, False
        m, p = self._pen(me, barrel, full=True) if os.environ.get("EX_SP", "1") == "1" else self._pen(me, barrel)
        if m > -90.0 and p >= P_MIN:
            dx = cos(barrel)
            dy = sin(barrel)
            if self._clear(me.x + dx * MUZZLE, me.y + dy * MUZZLE, dx, dy, d + 8.0):
                return cmd, True
        # пробития мало: ствол смотрит в мёртвую зону. Смещение уводит луч с
        # курса цели на борт или на корму, где порог рикошета выше.
        if d < 300.0:
            span = degrees(atan2(11.0, d)) + float(os.environ.get("EX_SPAN", "40"))
            best_p = -1.0
            best_u = u_des
            for off in OFFSETS:
                if fabs(off) > span:
                    continue
                u = wrap(u_des + rad(off))
                m, pp = self._pen(me, u)
                if m < -90.0 or pp < P_MIN:
                    continue
                # Запас важнее дешевизны смещения: выстрел с запасом в полградуса
                # — лотерея, ведь курс цели мы знаем с ошибкой около двух.
                # Смещение всё же стоит денег: пока ствол едет в сторону, враг
                # видит направление и успевает переставить корпус.
                score = (m if m < 8.0 else 8.0) - 0.08 * fabs(off)
                if score > best_p:
                    best_p = score
                    best_u = u
            if best_p > -99.0:
                cmd = clamp(wrap(best_u - me.turret) * 6.0, -1.0, 1.0)
                barrel = wrap(me.turret + cmd * TURRET_RATE * DT)
                if fabs(wrap(best_u - barrel)) < rad(4.0):
                    m2, pp = self._pen(me, barrel, full=True) if os.environ.get("EX_SP", "1") == "1" else self._pen(me, barrel)
                    if m2 > -90.0 and pp >= P_MIN:
                        dx = cos(barrel)
                        dy = sin(barrel)
                        if self._clear(me.x + dx * MUZZLE, me.y + dy * MUZZLE,
                                       dx, dy, d + 8.0):
                            return cmd, True
        return cmd, False

    # -------------------------------------------------------------- защита

    def _arrival(self, o, me):
        """Откуда придёт ближайший снаряд: направление и время полёта.

        Летящий снаряд фиксирует линию — она уже не изменится. Иначе берётся
        линия ствола противника (если он смотрит на нас) или линия визирования.
        """
        t = o.time
        best = None
        live = []
        for ox, oy, dx, dy, t0 in self.shots:
            age = t - t0
            if age > 1.2:
                continue
            live.append((ox, oy, dx, dy, t0))
            bx = ox + dx * BULLET * age
            by = oy + dy * BULLET * age
            rx = me.x - bx
            ry = me.y - by
            along = dx * rx + dy * ry
            if along <= 0.0:
                continue
            if fabs(dx * ry - dy * rx) > 22.0:
                continue
            ti = along / BULLET
            if ti > 0.35:
                continue
            if best is None or ti < best[1]:
                best = (atan2(-dy, -dx), ti)
        self.shots = live
        if best is not None:
            return best[0], best[1]
        d = hypot(self.ex - me.x, self.ey - me.y)
        los = atan2(self.ey - me.y, self.ex - me.x)
        if fabs(wrap(self.etur - (los + pi))) < rad(30.0):
            mx = self.ex - cos(self.etur) * MUZZLE
            my = self.ey - sin(self.etur) * MUZZLE
            return atan2(my - me.y, mx - me.x), d / BULLET
        return los, d / BULLET

    def _posture_side(self, me, arr):
        """Курс корпуса под DEAD к линии прихода; сторона — с гистерезисом."""
        plus = wrap(arr + rad(DEAD))
        minus = wrap(arr - rad(DEAD))
        dp = wrap(plus - me.hull)
        dm = wrap(minus - me.hull)
        if self.side > 0.0:
            if fabs(dm) + rad(SIDE_HYST) < fabs(dp):
                self.side = -1.0
        else:
            if fabs(dp) + rad(SIDE_HYST) < fabs(dm):
                self.side = 1.0
        return plus if self.side > 0.0 else minus

    def _target(self, o, me):
        """Какую дистанцию держим: темп «выстрелил — отступил — ворвался»."""
        if self.posturer:
            return D_GRIND
        if not me.ammo_ready:
            return D_RETREAT          # патрона нет: любая дистанция дороже
        if self._ecool(o) > 0.30:
            return D_CHARGE           # его окно: он секунду не выстрелит
        if o.time - self.fire_t > GRIND_T:
            return D_GRIND            # ракурс не даётся — ломаем его вплотную
        return D_HOLD

    def _steer(self, me, d, target, arr, t_now):
        """Курс и ход. Возвращает (turn, drive).

        Корпус всегда стоит под DEAD к линии прихода — это единственная поза,
        в которой броня держит удар. Доворот ограничен, поэтому к ошибке курса
        добавляется feed-forward на скорость вращения линии прихода: без него
        корпус отстаёт на несколько градусов и выезжает из мёртвой зоны
        ровно тогда, когда прилетает снаряд.
        """
        if d < CLOSE_MIN:
            drive = -1.0
        elif d > target + 6.0:
            drive = 1.0
        elif d < target - 6.0:
            drive = -0.9
        else:
            drive = 0.0
        # вплотную ход придерживается: поворот и газ делят один бюджет, и на
        # полном ходу корпус не успевает за линией прихода
        if d < SLOW_D:
            k = d / SLOW_D
            if k < 0.35:
                k = 0.35
            drive *= k
        hd = self._posture_side(me, arr)
        err = wrap(hd - me.hull)
        turn = clamp(self.bear_rate * FF / HULL_RATE + err / rad(KG), -1.0, 1.0)
        if JINK > 0.0 and d < JINK_R and fabs(drive) < JINK:
            # маятник газом: полный ход вперёд/назад со случайным полупериодом.
            # Своё упреждение врагу считать бесполезно, а линия прихода от
            # этого гуляет — и его корпус отстаёт от неё сильнее, чем наш.
            if t_now > self.jink_t:
                self.seed = (self.seed * 1103515245 + 12345) & 0x7FFFFFFF
                self.jink_s = 1.0 if (self.seed >> 16) & 1 else -1.0
                self.seed = (self.seed * 1103515245 + 12345) & 0x7FFFFFFF
                self.jink_t = t_now + JINK_T[0] + (JINK_T[1] - JINK_T[0]) * ((self.seed >> 8) & 255) / 255.0
            drive = self.jink_s * JINK
        cap = 1.0 - fabs(turn)
        if drive > cap:
            drive = cap
        elif drive < -cap:
            drive = -cap
        return turn, drive

    # ------------------------------------------------------------ маршрут

    def _bfs(self, cx, cy):
        """Поле расстояний до тайла (cx, cy) по проходимым клеткам."""
        g = self.grid
        rows = g.rows
        w = g.width
        h = g.height
        if cx < 0 or cy < 0 or cx >= w or cy >= h:
            return None
        if rows[cy][cx] in "#o:":
            found = None
            for r in range(1, 5):
                for ax in range(cx - r, cx + r + 1):
                    for ay in range(cy - r, cy + r + 1):
                        if 0 <= ax < w and 0 <= ay < h and rows[ay][ax] not in "#o:":
                            found = (ax, ay)
                            break
                    if found:
                        break
                if found:
                    break
            if found is None:
                return None
            cx, cy = found
        dist = {(cx, cy): 0}
        queue = [(cx, cy)]
        head = 0
        while head < len(queue):
            x, y = queue[head]
            head += 1
            base = dist[(x, y)] + 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = x + dx
                ny = y + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                if rows[ny][nx] in "#o:" or (nx, ny) in dist:
                    continue
                dist[(nx, ny)] = base
                queue.append((nx, ny))
        return dist

    def _follow(self, o, me, gx, gy):
        """Направление на цель по полю расстояний: (turn, drive)."""
        g = self.grid
        if g is None:
            return clamp(wrap(atan2(gy - me.y, gx - me.x) - me.hull) * 2.0,
                         -1.0, 1.0), 1.0
        tile = g.tile_size
        cx = int(gx // tile)
        cy = int(gy // tile)
        key = (cx, cy)
        if self.field is None or self.field_goal != key or o.time - self.field_t > 0.6:
            self.field = self._bfs(cx, cy)
            self.field_goal = key
            self.field_t = o.time
        f = self.field
        if not f:
            return clamp(wrap(atan2(gy - me.y, gx - me.x) - me.hull) * 2.0,
                         -1.0, 1.0), 1.0
        tx = int(me.x // tile)
        ty = int(me.y // tile)
        best = None
        best_v = f.get((tx, ty), 1 << 30)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                v = f.get((tx + dx, ty + dy))
                if v is None or v >= best_v:
                    continue
                best_v = v
                best = (tx + dx, ty + dy)
        if best is None:
            return clamp(wrap(atan2(gy - me.y, gx - me.x) - me.hull) * 2.0,
                         -1.0, 1.0), 1.0
        wx = (best[0] + 0.5) * tile
        wy = (best[1] + 0.5) * tile
        return clamp(wrap(atan2(wy - me.y, wx - me.x) - me.hull) * 2.4,
                     -1.0, 1.0), 1.0

    def _goal_point(self, o):
        """Куда идти, когда противник не виден."""
        if self.seen:
            age = o.time - self.et
            if age > 6.0:
                return None
            return self.ex + self.evx * age * 0.5, self.ey + self.evy * age * 0.5
        g = self.grid
        if g is None:
            return None
        if self.spawn_goal is None:
            xs = 0.0
            ys = 0.0
            n = 0
            for s in g.spawns:
                xs += s.x
                ys += s.y
                n += 1
            if n:
                self.spawn_goal = (xs / n, ys / n)
            else:
                self.spawn_goal = (g.pixel_width * 0.5, g.pixel_height * 0.5)
        return self.spawn_goal

    def _unstuck(self, o, me, turn, drive):
        """Упёрлись — сдаём назад с поворотом, потом снова вперёд."""
        t = o.time
        if self.lx is None:
            self.lx = me.x
            self.ly = me.y
            self.mile_t = t
        moved = hypot(me.x - self.lx, me.y - self.ly)
        self.lx = me.x
        self.ly = me.y
        if fabs(drive) > 0.25:
            self.mile += moved
        if t - self.mile_t > STUCK_WIN:
            if self.mile < STUCK_D and fabs(drive) > 0.25 and t > 1.0:
                self.esc_n += 1
                self.esc_turn = 1.0 if (self.esc_n & 1) else -1.0
                self.esc_until = t + 0.45
            self.mile = 0.0
            self.mile_t = t
        if t < self.esc_until:
            return self.esc_turn, -0.8
        return turn, drive

    # ------------------------------------------------------------------ тик

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"tank": o.tank})
        self.last_tick = o.tick
        me = o.me
        if self.grid is None and o.map is not None:
            self.grid = o.map
        seen = self._remember(o)

        if seen:
            d = hypot(self.ex - me.x, self.ey - me.y)
            bear = atan2(self.ey - me.y, self.ex - me.x)
            dtb = o.time - self.bear_t
            if d > 1.0 and 0.0 < dtb < 0.2:
                raw = wrap(bear - self.bear_prev) / dtb
                self.bear_rate += (raw - self.bear_rate) * 0.25
                lim = rad(260.0)
                if self.bear_rate > lim:
                    self.bear_rate = lim
                elif self.bear_rate < -lim:
                    self.bear_rate = -lim
            self.bear_prev = bear
            self.bear_t = o.time
            self._style(o, me)
            turret, fire = self._gun(o, me)
            arr, tf = self._arrival(o, me)
            turn, drive = self._steer(me, d, self._target(o, me), arr, o.time)
        else:
            turret, fire = 0.0, False
            gp = self._goal_point(o)
            if gp is None:
                turn, drive = 0.6, 0.8
            else:
                turn, drive = self._follow(o, me, gp[0], gp[1])

        if fire:
            self.fire_t = o.time
        turn, drive = self._unstuck(o, me, turn, drive)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)


program = Brain()
