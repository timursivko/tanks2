#!TANKP 1
# name: Сталкер
# author: arena-d9c54ba8
# difficulty: 5
# color: #ff5da2
# description: Мёртвая зона 35° к азимуту врага держится всегда (и на марше), маятник газом; огонь по геометрическому гейту пробития + темп: «на грани», когда враг перезаряжается; 4 модели упреждения с онлайн-выбором по радару; охота по A* к точной радарной позиции.
# tags: дуэль,рикошет,упреждение,маятник,радар,навигация

"""Сталкер — дуэльный танк на мёртвой зоне брони.

Несущие идеи:

* Радар: ``o.sight.bearing``/``o.sight.distance`` отдаются всегда, даже когда
  враг за стеной, — позиция врага известна точно в любой момент.
* Мёртвая зона: курс корпуса под ~35° к азимуту на врага. Тогда снаряд врага
  приходит под 30..40° к нормали — рикошетят и лоб (порог 30°), и передний
  борт (порог 50°). Ракурс держится ВСЕГДА — и в дуэли, и на марше; смены
  стороны редки (каждый разворот — окно уязвимости).
* Маятник газом: едем вперёд/назад вдоль оси под 35° к линии огня, корпус не
  крутится — чужое упреждение рвётся сменой передачи, ракурс не ломается.
* Газ платит за ракурс: при ошибке курса газ режется, корпус доворачивается
  быстрее (при полном газе+руле угловая скорость корпуса падает вдвое).
* Огонь — по геометрическому гейту: прогноз OBB врага на момент попадания,
  скан точек по периметру, максимум запаса (порог грани минус угол удара).
  Когда враг перезаряжается (его КД видно), стреляем «на грани» — темп важнее.
* Четыре модели упреждения (стоп, CV, затухание, дуга) соревнуются онлайн
  по ошибке против точной радарной позиции.
* Охота: A* к радарной позиции врага.
"""

from heapq import heappop, heappush
from math import acos, atan2, cos, degrees, exp, fmod, fabs, hypot, pi, radians, sin
from random import Random

from tankp import Action, TankProgram

TAU = pi * 2.0
DT = 1.0 / 60.0

# --- баланс движка (копия config.Balance) ---
BULLET = 620.0
MUZZLE = 26.0
SPREAD = radians(0.4)
RELOAD = 1.0
HULL_TURN = 2.6
TURRET_TURN = 3.6
SPEED_FWD = 190.0
HP_MAX = 10.0
DMG = 4.0

#: Пороги рикошета (угол к нормали, град): лоб / борт / корма.
RICO = (30.0, 50.0, 60.0)
FACE_HALF = 35.0
#: Полуразмеры корпуса цели с радиусом снаряда (half_far движка).
HX = 20.0
HY = 14.0

BLOCKED = "#o:"

# --- тактика ---
ANGLE_LOCK = radians(35.0)   # середина мёртвой зоны [30..40]
D_MIN = 240.0                # ближняя граница маятника
D_MAX = 300.0                # дальняя граница маятника
POINT_BLANK = 78.0           # в упор не стреляем (дуло внутри корпуса -> рикошет)
MARGIN_WIN = 6.0             # запас пробития внутри окна прицеливания, град
RADAR_MARGIN = 8.0           # дополнительный запас, когда враг не виден
TRACK_GAIN = 6.0             # усиление доворота корпуса на ракурс
AZ_LEAD = 0.07               # с: опережение корпуса по угловой скорости азимута
GEAR_LO = 0.50               # полупериод маятника, с
GEAR_HI = 1.20
SIDE_MIN_T = 6.0             # мин. интервал смены стороны ракурса, с
MARGIN_CLEAN = 6.0           # базовый запас гейта (адаптируется по промахам)
MARGIN_MAX = 14.0            # потолок адаптивного запаса
MARGIN_BASE = 1.0            # неснижаемый запас: стреляем по чистому пробитию
PROG_K = 1.0                 # градусы запаса на пиксель ошибки прогноза на dist


def wrap(a):
    a = fmod(a + pi, TAU)
    if a < 0.0:
        a += TAU
    return a - pi


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def seg_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang, max_t=1e9):
    """Пересечение луча (o, d) с OBB. Возвращает (t, nx, ny) или None.

    Нормаль — грани входа, наружу. Если луч стартует внутри корпуса,
    возвращается t=0 с нормалью, перпендикулярной полёту — такой удар у
    движка всегда рикошетит (угол к нормали 90°), что гейт выстрела и ловит.
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
    sgn = 0.0
    q = ldx
    p = lx
    if fabs(q) < 1e-12:
        if fabs(p) > hx:
            return None
    else:
        s = -1.0 if q > 0.0 else 1.0
        t1 = (-hx - p) / q
        t2 = (hx - p) / q
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = 0
            sgn = s
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return None
    q = ldy
    p = ly
    if fabs(q) < 1e-12:
        if fabs(p) > hy:
            return None
    else:
        s = -1.0 if q > 0.0 else 1.0
        t1 = (-hy - p) / q
        t2 = (hy - p) / q
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
            axis = 1
            sgn = s
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
        nx = sgn
        ny = 0.0
    else:
        nx = 0.0
        ny = sgn
    return tmin, nx * ca - ny * sa, nx * sa + ny * ca


class Brain(TankProgram):
    def on_start(self, ctx):
        self._seed = None
        self._tank_id = 0
        if isinstance(ctx, dict):
            try:
                if ctx.get("seed") is not None:
                    self._seed = int(ctx["seed"])
                self._tank_id = int(ctx.get("tank") or 0)
            except (TypeError, ValueError):
                pass
        self._reset()

    def _reset(self):
        if self._seed is None:
            self.rng = Random()
        else:
            self.rng = Random(self._seed * 131 + self._tank_id * 7 + 1)
        # радар (позиция врага известна всегда)
        self.rx = self.ry = 0.0
        self.radar_ok = False
        # трекер врага: текущее состояние (позиция — всегда по радару)
        self.ex = self.ey = 0.0
        self.eh = 0.0
        self.evx = self.evy = 0.0
        self.ew = 0.0
        self.ehp = HP_MAX
        self.e_cd = 0.0
        self.seen = False
        self.seen_t = -10.0
        self.prev_rx = self.prev_ry = 0.0
        self.prev_rt = -10.0
        # снапшот 0.4с назад — по нему модели оцениваются против радара
        self.snap_t = -10.0
        self.snap = (0.0,) * 15
        # модели упреждения: EMA ошибки против радара
        self.merr = [25.0] * 7      # позиция + курс (для выбора)
        self.merr_pos = [25.0] * 7  # только позиция (для гейта)
        self.eax = self.eay = 0.0
        self.prev_evx = self.prev_evy = 0.0
        self.prev_evt = -10.0
        self.me_x = self.me_y = 0.0
        self.me_vx = self.me_vy = 0.0
        self.me_ax = self.me_ay = 0.0
        self.prev_mvx = self.prev_mvy = 0.0
        self.prev_mt = -10.0
        self.rel_now = ANGLE_LOCK  # курс врага = az_to_me + rel_now (модель 5)
        # огонь
        self.margin = MARGIN_CLEAN
        self.misses = 0.0
        self.miss_t = -10.0
        self.pending = []
        # движение
        self.side = 1.0
        self.dir_now = 1.0
        self.next_flip = 0.0
        self.flip_period = 0.6
        self.next_side = 0.0
        self.last_side_t = -10.0
        self.az_prev = None
        self.az_ema = 0.0
        self.stuck_t = -10.0
        # навигация
        self.path = []
        self.path_t = -10.0
        self.path_goal = (0.0, 0.0)
        self.move_goal = None
        self.move_goal_t = -10.0

    # --- прогноз врага ------------------------------------------------------

    def _model_pos(self, m, t):
        """Позиция/курс врага через t секунд от текущего состояния, модель m."""
        return self._model_from(self.ex, self.ey, self.eh,
                                self.evx, self.evy, self.ew, m, t,
                                self.eax, self.eay,
                                self.me_x, self.me_y, self.me_vx, self.me_vy,
                                self.rel_now, self.me_ax, self.me_ay)

    @staticmethod
    def _model_from(x, y, h, vx, vy, w, m, t, ax=0.0, ay=0.0,
                    mx=0.0, my=0.0, mvx=0.0, mvy=0.0, rel=0.0,
                    max=0.0, may=0.0):
        """Позиция/курс врага через t секунд от якоря (x, y, h, vx, vy, w)."""
        if m == 0:
            return x, y, h
        if m == 1:
            return x + vx * t, y + vy * t, h + w * t
        if m == 2:
            e = exp(-1.3 * t)
            return x + vx * t * e, y + vy * t * e, h + w * t * e
        if m == 4:
            return x + vx * t + 0.5 * ax * t * t, y + vy * t + 0.5 * ay * t * t, h + w * t
        if m == 5:
            # модель «мёртвая зона»: враг держит курс к нам под своим углом
            hx = x + vx * t
            hy = y + vy * t
            fx = mx + mvx * t + 0.5 * max * t * t
            fy = my + mvy * t + 0.5 * may * t * t
            az = atan2(fy - hy, fx - hx)
            return hx, hy, az + rel
        if m == 6:
            # модель «смена стороны»: враг переворачивает борт (курс -rel)
            hx = x + vx * t
            hy = y + vy * t
            fx = mx + mvx * t + 0.5 * max * t * t
            fy = my + mvy * t + 0.5 * may * t * t
            az = atan2(fy - hy, fx - hx)
            return hx, hy, az - rel
        # m == 3: дуга (постоянные скорость и угловая скорость)
        sp = hypot(vx, vy)
        if fabs(w) < 0.05 or sp < 2.0:
            return x + vx * t, y + vy * t, h + w * t
        ca = cos(h)
        sa = sin(h)
        wt = w * t
        r = sp / w
        lx = r * sin(wt)
        ly = r * (1.0 - cos(wt))
        return x + lx * ca - ly * sa, y + lx * sa + ly * ca, h + wt

    def _predict(self, dt):
        """Прогноз (px, py, hull) через dt от якоря: лучшая из 4 моделей
        (онлайн-выбор по EMA ошибки против радара)."""
        bm = 0
        for m in range(6):
            if self.merr[m] < self.merr[bm]:
                bm = m
        px, py, ph = self._model_pos(bm, dt)
        return px, py, ph, bm

    # --- стрельба -----------------------------------------------------------

    def _shot_margin(self, mx, my, tx, ty, px, py, ph):
        """Запас пробития (порог грани минус угол удара) для луча в точку."""
        dx = tx - mx
        dy = ty - my
        d = hypot(dx, dy)
        if d < 1.0:
            return None
        dx /= d
        dy /= d
        res = seg_obb(mx, my, dx, dy, px, py, HX, HY, ph)
        if res is None:
            return None
        _t, nx, ny = res
        ct = -(dx * nx + dy * ny)
        if ct < 0.0:
            ct = 0.0
        elif ct > 1.0:
            ct = 1.0
        theta = degrees(acos(ct))
        rel = fabs(degrees(wrap(atan2(ny, nx) - ph)))
        if rel <= FACE_HALF:
            limit = RICO[0]
        elif rel >= 180.0 - FACE_HALF:
            limit = RICO[2]
        else:
            limit = RICO[1]
        return limit - theta

    def _best_shot(self, mx, my, px, py, ph):
        """Скан по силуэту прогнозного корпуса: окно углов от ствола, где
        выстрел гарантированно пробивает (margin >= 0), максимальной ширины.

        Возвращает (tx, ty, width_deg) — центр окна (точка попадания) и его
        ширина в градусах, либо None. Широкое окно прощает ошибку прогноза.
        """
        d = hypot(px - mx, py - my)
        if d < 1.0:
            return None
        base = atan2(py - my, px - mx)
        half_ang = atan2(HX + 4.0, d) + 0.06
        N = 28
        step = 2.0 * half_ang / N
        margins = []
        hits = []
        for i in range(N + 1):
            a = base - half_ang + step * i
            dx = cos(a)
            dy = sin(a)
            res = seg_obb(mx, my, dx, dy, px, py, HX, HY, ph)
            if res is None:
                margins.append(None)
                hits.append(None)
                continue
            t, nx, ny = res
            ct = -(dx * nx + dy * ny)
            if ct < 0.0:
                ct = 0.0
            elif ct > 1.0:
                ct = 1.0
            theta = degrees(acos(ct))
            rel = fabs(degrees(wrap(atan2(ny, nx) - ph)))
            if rel <= FACE_HALF:
                limit = RICO[0]
            elif rel >= 180.0 - FACE_HALF:
                limit = RICO[2]
            else:
                limit = RICO[1]
            margins.append(limit - theta)
            hits.append(t)
        # максимальное окно, где margin >= MARGIN_WIN (запас против ошибки)
        best = None
        i = 0
        while i <= N:
            if margins[i] is not None and margins[i] >= MARGIN_WIN:
                j = i
                while j <= N and margins[j] is not None and margins[j] >= MARGIN_WIN:
                    j += 1
                width_deg = (j - i) * step * 57.2958
                if best is None or width_deg > best[2]:
                    ic = (i + j - 1) // 2
                    a = base - half_ang + step * ic
                    t = hits[ic]
                    best = (mx + cos(a) * t, my + sin(a) * t, width_deg)
                i = j
            else:
                i += 1
        return best

    def _gun(self, o, t, seen):
        """Прицеливание и выстрел. Возвращает (turret_cmd, fire)."""
        me = o.me
        mx = me.x + cos(me.turret) * MUZZLE
        my = me.y + sin(me.turret) * MUZZLE
        fallback = (self.rx, self.ry)
        if not me.ammo_ready or not self.radar_ok:
            err = o.aim_error(fallback[0], fallback[1])
            return clamp(err * 4.0, -1.0, 1.0), False
        # прогноз на момент попадания (две итерации по времени полёта)
        d0 = hypot(self.rx - mx, self.ry - my)
        t0 = d0 / BULLET
        dt0 = t - self.seen_t + t0 if self.seen else t0
        px, py, ph, bm = self._predict(dt0)
        d1 = hypot(px - mx, py - my)
        t1 = d1 / BULLET
        dt1 = t - self.seen_t + t1 if self.seen else t1
        px, py, ph, bm = self._predict(dt1)
        sol = self._best_shot(mx, my, px, py, ph)
        if sol is None:
            err = o.aim_error(px, py)
            return clamp(err * 4.0, -1.0, 1.0), False
        _tx, _ty, width = sol
        # целимся в центр корпуса: попадание в любую грань, при окне урон ~50-70%
        tx, ty = px, py
        # на дальних дистанциях ошибка прогноза на время полёта слишком велика
        if d1 > 320.0:
            err = o.aim_error(tx, ty)
            return clamp(err * 4.0, -1.0, 1.0), False
        # стреляем, когда окно шире ошибки прогноза позиции (в градусах);
        # целимся в центр окна — в корпус попасть при ошибке позиции < half
        prog_err = min(self.merr_pos)
        err_deg = prog_err * 57.2958 / max(1.0, d1)
        need = max(3.0, err_deg * 1.0 * (1.0 + t1 * 0.3))
        if not seen:
            need += RADAR_MARGIN
        if t > 50.0:
            need += -0.5 if me.hp >= self.ehp else 0.5
        if width < need:
            err = o.aim_error(tx, ty)
            return clamp(err * 4.0, -1.0, 1.0), False
        # наведение на выбранную точку
        err = o.aim_error(tx, ty)
        tcmd = clamp(err * 4.0, -1.0, 1.0)
        residual = fabs(err) - fabs(tcmd) * TURRET_TURN * DT
        if residual < 0.0:
            residual = 0.0
        dist_t = hypot(tx - mx, ty - my)
        if dist_t < POINT_BLANK:
            return tcmd, False
        tol = atan2(13.0, dist_t) - SPREAD
        if tol < 0.002:
            tol = 0.002
        fire = residual <= tol
        if fire:
            mp = o.map
            if mp.opaque(mx, my) or mp.blocked_between(mx, my, tx, ty):
                fire = False
        if fire:
            self.pending.append((t, t1, self.ehp if seen else HP_MAX))
        return tcmd, fire

    def _score_shots(self, o, e, t):
        """Оценка выстрелов: попал/промах по изменению ХП врага."""
        if not self.pending:
            return
        keep = []
        for p in self.pending:
            t_shot, t_flight, hp0 = p
            if t < t_shot + t_flight + 0.05:
                keep.append(p)
                continue
            if e is not None:
                dhp = hp0 - e.hp
                if dhp >= 3.5:
                    self.misses = max(0.0, self.misses - 0.6)
                elif dhp <= 0.5:
                    self.misses += 1.0
                    self.miss_t = t
            elif t <= t_shot + t_flight + 1.5:
                keep.append(p)
        self.pending = keep
        if t - self.miss_t < 8.0:
            self.margin = clamp(MARGIN_CLEAN + self.misses * 1.5, MARGIN_CLEAN, MARGIN_MAX)
        else:
            self.margin = max(MARGIN_CLEAN, self.margin - 0.02)
            self.misses *= 0.98

    # --- движение ------------------------------------------------------------

    def _duel_move(self, o, t, az, dist_e):
        me = o.me
        if t >= self.next_side:
            self._maybe_switch_side(o, t, az, dist_e)
        lead = clamp(self.az_ema * AZ_LEAD, -0.09, 0.09)
        if dist_e < 260.0:
            lead = 0.0               # в упоре азимут крутится быстро — без lead
        want = az - self.side * ANGLE_LOCK + lead
        err = wrap(want - me.hull)
        aerr = fabs(err)
        turn = clamp(err * TRACK_GAIN, -1.0, 1.0)
        if t >= self.next_flip:
            self._pick_gear(t, dist_e)
            self.next_flip = t + self.flip_period
        drive = self.dir_now * self.rng.uniform(0.8, 1.0)
        retreating = dist_e < D_MIN or dist_e < POINT_BLANK + 60.0
        if retreating:
            drive = -fabs(drive)
        elif dist_e > D_MAX:
            drive = fabs(drive)
        # газ платит за ракурс (но при отъезде едем полным — дистанция важнее)
        if aerr > 0.18 and not retreating:
            drive *= clamp(1.15 - aerr * 2.0, 0.0, 1.0)
        # в упоре враг давит — рывок: меняем сторону и уезжаем вперёд
        espeed = hypot(self.evx, self.evy)
        if dist_e < 200.0 and espeed > 90.0 and me.speed < 80.0:
            if t - self.stuck_t > 0.7:
                self.stuck_t = t
                self.side = -self.side
                self.last_side_t = t
                self.dir_now = 1.0
                self.next_flip = t + 0.5
        # стена впереди по желаемому курсу — обход по A* к радарной позиции
        if o.map.clearance(me.x + cos(want) * 70.0,
                           me.y + sin(want) * 70.0) < 50.0:
            goal = (self.rx, self.ry)
            if (t - self.path_t > 0.35
                    or hypot(goal[0] - self.path_goal[0],
                            goal[1] - self.path_goal[1]) > 90.0):
                self._repath(o, t, goal)
            if self.path:
                if self.move_goal is None or t - self.move_goal_t > 0.12:
                    self.move_goal = self._far_visible(o, me, self.path)
                    self.move_goal_t = t
                _t2, d2 = o.steer_to(self.move_goal[0], self.move_goal[1],
                                     tol=22.0)
                return turn, d2
            turn += self.side * 0.55
            drive *= 0.55
        return turn, drive

    def _pick_gear(self, t, dist_e):
        if dist_e < D_MIN:
            self.dir_now = -1.0
        elif dist_e > D_MAX + 20.0:
            self.dir_now = 1.0
        else:
            self.dir_now = 1.0 if self.rng.random() < 0.5 else -1.0
        self.flip_period = self.rng.uniform(GEAR_LO, GEAR_HI)

    def _maybe_switch_side(self, o, t, az, dist_e):
        me = o.me
        want = az - self.side * ANGLE_LOCK
        wall_close = o.map.clearance(me.x + cos(want) * 90.0,
                                     me.y + sin(want) * 90.0) < 55.0
        if t - self.last_side_t < SIDE_MIN_T:
            self.next_side = t + 0.3
        elif wall_close:
            self.side = -self.side
            self.last_side_t = t
            self.next_side = t + self.rng.uniform(2.0, 3.5)
            self.next_flip = t + 0.35
        else:
            self.next_side = t + 0.4

    def _hunt_move(self, o, t, az, dist_e):
        # ракурс держим всегда; A* только когда стена впереди и надо обойти
        me = o.me
        turn, drive = self._duel_move(o, t, az, dist_e)
        want = az - self.side * ANGLE_LOCK
        if o.map.clearance(me.x + cos(want) * 70.0,
                           me.y + sin(want) * 70.0) < 50.0:
            goal = (self.rx, self.ry)
            if (t - self.path_t > 0.35
                    or hypot(goal[0] - self.path_goal[0],
                            goal[1] - self.path_goal[1]) > 90.0):
                self._repath(o, t, goal)
            if self.path:
                if self.move_goal is None or t - self.move_goal_t > 0.12:
                    self.move_goal = self._far_visible(o, me, self.path)
                    self.move_goal_t = t
                t2, d2 = o.steer_to(self.move_goal[0], self.move_goal[1],
                                    tol=22.0)
                # корпус всё равно держим под ракурсом к врагу
                return turn, d2
        return turn, drive

    def _repath(self, o, t, goal):
        mp = o.map
        tile = mp.tile_size
        me = o.me
        sx = int(me.x // tile)
        sy = int(me.y // tile)
        gx = int(goal[0] // tile)
        gy = int(goal[1] // tile)
        self.path_goal = goal
        self.path_t = t
        self.path = self._astar(mp, sx, sy, gx, gy)
        self.move_goal = None

    def _astar(self, mp, sx, sy, gx, gy):
        rows = mp.rows
        cl = mp.clearance_grid
        tile = mp.tile_size
        W = mp.width
        H = mp.height

        def passable(tx, ty):
            if tx < 0 or ty < 0 or tx >= W or ty >= H:
                return False
            if rows[ty][tx] in BLOCKED:
                return False
            return cl[ty][tx] >= 0.75

        def nearest_free(tx, ty):
            if passable(tx, ty):
                return tx, ty
            for r in range(1, 6):
                for oy in range(-r, r + 1):
                    for ox in range(-r, r + 1):
                        if max(fabs(ox), fabs(oy)) != r:
                            continue
                        nx = tx + ox
                        ny = ty + oy
                        if passable(nx, ny):
                            return nx, ny
            return tx, ty

        sx, sy = nearest_free(sx, sy)
        gx, gy = nearest_free(gx, gy)

        def h(tx, ty):
            dx = fabs(tx - gx)
            dy = fabs(ty - gy)
            return (dx + dy) + 0.4142 * (dx if dx < dy else dy)

        start = (sx, sy)
        goal = (gx, gy)
        gcost = {start: 0.0}
        came = {start: None}
        heap = [(h(sx, sy), 0.0, sx, sy)]
        while heap:
            _f, g, cx, cy = heappop(heap)
            if (cx, cy) == goal:
                break
            if g > gcost.get((cx, cy), 1e18):
                continue
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx = cx + ox
                ny = cy + oy
                if not passable(nx, ny):
                    continue
                if ox != 0 and oy != 0:
                    if not passable(cx + ox, cy) or not passable(cx, cy + oy):
                        continue
                    cost = 1.4142
                else:
                    cost = 1.0
                if cl[ny][nx] < 1.6:
                    cost += 0.5
                ng = g + cost
                key = (nx, ny)
                if ng < gcost.get(key, 1e18):
                    gcost[key] = ng
                    came[key] = (cx, cy)
                    heappush(heap, (ng + h(nx, ny), ng, nx, ny))
        if goal not in came:
            return []
        path = []
        cur = goal
        while cur is not None:
            path.append(((cur[0] + 0.5) * tile, (cur[1] + 0.5) * tile))
            cur = came[cur]
        path.reverse()
        return path[1:]

    def _far_visible(self, o, me, path):
        mp = o.map
        for i in range(len(path) - 1, -1, -1):
            px, py = path[i]
            if not mp.blocked_between(me.x, me.y, px, py):
                return px, py
        return path[-1]

    # --- главный тик ----------------------------------------------------------

    def on_tick(self, o):
        if o.tick <= 1:
            # matrix/probe держат программу между боями — сброс по новому бою
            self._reset()
        me = o.me
        t = o.time
        # --- радар: азимут и дальность отдаются всегда ---
        sg = o.sight
        if sg.bearing is not None and sg.distance is not None:
            self.rx = me.x + cos(sg.bearing) * sg.distance
            self.ry = me.y + sin(sg.bearing) * sg.distance
            self.radar_ok = True
            # радарная скорость (сглаженная) — обновляем трекер позиции
            if self.prev_rt > 0.0:
                rdt = t - self.prev_rt
                if rdt > 0.005:
                    rvx = (self.rx - self.prev_rx) / rdt
                    rvy = (self.ry - self.prev_ry) / rdt
                    k = clamp(rdt * 60.0, 0.0, 0.95)
                    # ускорение для CVA-модели
                    if self.prev_evt > 0.0:
                        adt = t - self.prev_evt
                        if adt > 0.005:
                            self.eax += ((self.evx - self.prev_evx) / adt - self.eax) * 0.8
                            self.eay += ((self.evy - self.prev_evy) / adt - self.eay) * 0.8
                    self.prev_evx = self.evx
                    self.prev_evy = self.evy
                    self.prev_evt = t
                    self.evx += (rvx - self.evx) * k
                    self.evy += (rvy - self.evy) * k
            self.prev_rx = self.rx
            self.prev_ry = self.ry
            self.prev_rt = t
            self.ex = self.rx
            self.ey = self.ry
        ex = self.rx
        ey = self.ry
        dist_e = hypot(ex - me.x, ey - me.y)
        az = atan2(ey - me.y, ex - me.x)
        # угловая скорость азимута (для опережения корпуса)
        if self.az_prev is not None:
            daz = wrap(az - self.az_prev) / DT
            self.az_ema = self.az_ema * 0.85 + daz * 0.15
        self.az_prev = az
        # --- враг виден: обновить трекер ---
        self.me_x = me.x
        self.me_y = me.y
        self.me_vx = me.vx
        self.me_vy = me.vy
        if self.prev_mt > 0.0:
            mdt = t - self.prev_mt
            if mdt > 0.005:
                self.me_ax += ((me.vx - self.prev_mvx) / mdt - self.me_ax) * 0.5
                self.me_ay += ((me.vy - self.prev_mvy) / mdt - self.me_ay) * 0.5
        self.prev_mvx = me.vx
        self.prev_mvy = me.vy
        self.prev_mt = t
        e = o.enemy
        if e is not None:
            self._update_tracker(o, e, t)
            # релевантный угол (враг держит курс к нам под этим углом)
            az_me = atan2(me.y - e.y, me.x - e.x)
            self.rel_now = wrap(e.hull - az_me)
        # --- оценка моделей: прогноз от снапшота 0.4с назад против радара ---
        if t - self.snap_t >= 0.25:
            self.snap = (self.ex, self.ey, self.eh,
                         self.evx, self.evy, self.ew,
                         self.eax, self.eay,
                         self.me_x, self.me_y, self.me_vx, self.me_vy,
                         self.rel_now, self.me_ax, self.me_ay)
            self.snap_t = t
        if t - self.snap_t > 0.17:
            dt = t - self.snap_t
            fx = self.rx
            fy = self.ry
            (sx, sy, sh, svx, svy, sw, sax, say,
             smx, smy, smvx, smvy, srel, smax, smay) = self.snap
            for m in range(7):
                px, py, ph = self._model_from(sx, sy, sh, svx, svy, sw, m, dt,
                                              sax, say, smx, smy, smvx, smvy,
                                              srel, smax, smay)
                perr = hypot(px - fx, py - fy)
                self.merr_pos[m] = self.merr_pos[m] * 0.7 + perr * 0.3
                err = perr
                if e is not None:
                    # ошибка курса важна для грани: 1° курса на дистанции d
                    # сдвигает грань попадания на d/57.3 px
                    kerr = fabs(degrees(wrap(ph - e.hull))) * dist_e / 57.3
                    err += kerr
                self.merr[m] = self.merr[m] * 0.7 + err * 0.3
        # --- оценка своих выстрелов ---
        self._score_shots(o, e, t)
        # --- движение ---
        if e is not None:
            turn, drive = self._duel_move(o, t, az, dist_e)
        else:
            turn, drive = self._hunt_move(o, t, az, dist_e)
        # --- стрельба ---
        turret, fire = self._gun(o, t, e is not None)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    def _update_tracker(self, o, e, t):
        dt = t - self.seen_t
        if self.seen and dt > 0.005:
            w = wrap(e.hull - self.eh) / dt
            k = clamp(dt * 10.0, 0.0, 0.6)
            self.ew += (w - self.ew) * k
        self.eh = e.hull
        self.ehp = e.hp
        self.e_cd = e.cooldown
        self.seen = True
        self.seen_t = t


program = Brain()
