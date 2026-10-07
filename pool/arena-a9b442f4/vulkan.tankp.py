#!TANKP 1
# name: Вулкан
# author: arena-a9b442f4
# difficulty: 5
# color: #ff5722
# description: Кружит врага, выходя на борт/корму, и стреляет с точной геометрией: каждый выстрел — луч в вращающийся прямоугольник, точка удара выбрана так, чтобы пробить броню, а не дать рикошет.
# tags: геометрия,орбита,укрытия,прогнозирование

import heapq
import math

from tankp import TankProgram, Action

# --- константы баланса (скрипт не может импортировать config) --------------
BULLET_SPEED = 620.0
TURRET_TURN = 3.6
MUZZLE = 26.0
HL = 20.0            # полуразмер корпуса цели + радиус снаряда
HW = 14.0
RICO_FRONT = 30.0    # порог рикошета: угол к нормали, градусы
RICO_SIDE = 50.0
RICO_REAR = 60.0
FRONT_HALF = 35.0    # полуугол лба от курса
SPREAD = 0.4         # разброс ствола, ± градусы
DEG = math.degrees
RAD = math.radians
PI = math.pi
TAU = PI * 2.0
INF = float("inf")


def wrap(a):
    a = math.fmod(a + PI, TAU)
    if a < 0:
        a += TAU
    return a - PI


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def seg_obb(ox, oy, dx, dy, cx, cy, hx, hy, angle):
    """Луч из (ox, oy) в направлении (dx, dy) в OBB. (t, nx, ny) или None.

    Тот же алгоритм, что движок считает для снарядов (engine/geometry).
    """
    ca = math.cos(angle)
    sa = math.sin(angle)
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
    q = ldx
    if abs(q) < 1e-12:
        if abs(lx) > hx:
            return None
    else:
        s = -1.0 if q > 0.0 else 1.0
        t1 = (-hx - lx) / q
        t2 = (hx - lx) / q
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
    q = ldy
    if abs(q) < 1e-12:
        if abs(ly) > hy:
            return None
    else:
        s = -1.0 if q > 0.0 else 1.0
        t1 = (-hy - ly) / q
        t2 = (hy - ly) / q
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
        # Ствол уже внутри корпуса (танки в упор): удар в перпендикулярную грань.
        if tmax < 0.0:
            return None
        if abs(ldx) >= abs(ldy):
            n = (-1.0 if ldx > 0.0 else 1.0, 0.0)
        else:
            n = (0.0, -1.0 if ldy > 0.0 else 1.0)
        return 0.0, n[0] * ca - n[1] * sa, n[0] * sa + n[1] * ca
    if axis == 0:
        n = (sign, 0.0)
    else:
        n = (0.0, sign)
    return tmin, n[0] * ca - n[1] * sa, n[0] * sa + n[1] * ca


def face_of(nx, ny, hull):
    """Грань корпуса по нормали удара: лоб/борт/корма (как в engine/armor)."""
    rel = abs(DEG(wrap(math.atan2(ny, nx) - hull)))
    if rel <= FRONT_HALF:
        return 0                    # front
    if rel >= 180.0 - FRONT_HALF:
        return 2                    # rear
    return 1                         # side


RICO_BY_FACE = (RICO_FRONT, RICO_SIDE, RICO_REAR)

#: Кандидаты точки удара в системе координат врага (x — вперёд, y — влево).
#: Центр, борта, корма/нос по центру и углы — углы дают наклонные попадания
#: в борт, когда в центр снаряд рикошетил бы.
CANDS = ((0.0, 0.0), (0.0, 11.0), (0.0, -11.0),
         (-14.0, 0.0), (14.0, 0.0),
         (-14.0, 8.0), (-14.0, -8.0),
         (14.0, 8.0), (14.0, -8.0))
NC = len(CANDS)


class Brain(TankProgram):
    """Геометрия удара + орбита на корму + укрытия + навигация по полю."""

    FIRE_MIN = 0.72          # минимальная уверенность в пробитии
    FIRE_MIN_CLOSE = 0.30    # в упор стреляем и по «сомнительным» граням
    RANGE_MAX = 560.0        # дальше упреждение слишком ненадёжно

    # --- запуск -------------------------------------------------------------

    def on_start(self, ctx):
        self.my_tank = ctx.get("tank", 0) if isinstance(ctx, dict) else 0
        self.my_hp = 10.0
        self.phase = float((ctx.get("seed", 0) if isinstance(ctx, dict) else 0)
                           % 97) * 0.13 + self.my_tank * 2.4
        self.map_key = None
        self.passable = None
        self.mud = None
        self.cg = None
        self.tile = 32
        self.map_w = 0
        self.map_h = 0
        # следящая система
        self.ever_seen = False
        self.last_pos = (0.0, 0.0)
        self.e_hull = 0.0
        self.ev_x = 0.0
        self.ev_y = 0.0
        self.e_omega = 0.0
        self.e_ax = 0.0        # оценённое ускорение врага, px/с²
        self.lost = 0.0
        self.last_time = 0.0
        self._om_hist = []     # (t, om) — угловая скорость курса врага, ~0.7 с
        # манёвр
        self.orbit_side = 1.0
        self.evade_until = -1.0
        # навигация
        self.waypoints = None
        self.wi = 0
        self.nav_target = None
        self.nav_t = -10.0
        self.planned_target = None
        self.stuck_pos = None
        self.stuck_tick = -100
        self.aim_idx = 0
        self.sweep_t = 0.0
        self.tour_pts = None
        self.tour_i = 0
        self.tour_t0 = 0.0

    # --- карта --------------------------------------------------------------

    def _map(self, o):
        m = o.map
        key = (m.width, m.height, m.tile_size, tuple(m.rows))
        if key == self.map_key:
            return
        self.map_key = key
        self.tile = m.tile_size
        self.map_w = m.width
        self.map_h = m.height
        self.cg = m.clearance_grid
        pas = []
        mud = []
        for row in m.rows:
            pas.append(bytearray(0 if ch in "#o:" else 1 for ch in row))
            mud.append(bytearray(1 if ch == "," else 0 for ch in row))
        self.passable = pas
        self.mud = mud
        self.waypoints = None
        self.tour_pts = None

    # --- A* -----------------------------------------------------------------

    def _tile(self, x, y):
        return (int(x // self.tile), int(y // self.tile))

    def _near_passable(self, tx, ty):
        pas = self.passable
        w, h = self.map_w, self.map_h
        for r in range(0, 6):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    nx, ny = tx + dx, ty + dy
                    if 0 <= nx < w and 0 <= ny < h and pas[ny][nx]:
                        return (nx, ny)
        return None

    def _astar(self, sx, sy, tx, ty):
        """Путь в мировых координатах (центры тайлов) либо None."""
        pas = self.passable
        w, h = self.map_w, self.map_h
        s = self._near_passable(*self._tile(sx, sy))
        g = self._near_passable(*self._tile(tx, ty))
        if s is None or g is None:
            return None
        if s == g:
            return [((g[0] + 0.5) * self.tile, (g[1] + 0.5) * self.tile)]
        hx0, hy0 = g
        heur = lambda p: math.hypot(p[0] - hx0, p[1] - hy0)  # noqa: E731
        open_ = [(heur(s), 0.0, s)]
        best = {s: 0.0}
        came = {}
        closed = set()
        it = 0
        while open_:
            it += 1
            if it > 9000:
                return None
            _f, _g, cur = heapq.heappop(open_)
            if cur in closed:
                continue
            if cur == g:
                break
            closed.add(cur)
            cx, cy = cur
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = cx + dx, cy + dy
                    if not (0 <= nx < w and 0 <= ny < h) or not pas[ny][nx]:
                        continue
                    if dx and dy and (not pas[cy][nx] or not pas[ny][cx]):
                        continue
                    cost = 1.4142 if (dx and dy) else 1.0
                    if self.mud[ny][nx]:
                        cost *= 1.9
                    c = self.cg[ny][nx]
                    if c <= 1.0:
                        cost *= 1.35
                    ng = best[cur] + cost
                    if ng < best.get((nx, ny), INF):
                        best[(nx, ny)] = ng
                        came[(nx, ny)] = cur
                        heapq.heappush(open_, (ng + heur((nx, ny)), ng, (nx, ny)))
        if g not in came and s != g:
            return None
        path = []
        cur = g
        while cur != s:
            path.append(((cur[0] + 0.5) * self.tile, (cur[1] + 0.5) * self.tile))
            cur = came[cur]
        path.reverse()
        return path

    def _smooth(self, path):
        out = [path[0]]
        for p in path[1:-1]:
            if self._los_ok(out[-1], p):
                continue
            out.append(p)
        out.append(path[-1])
        return out

    def _los_ok(self, a, b):
        pas = self.passable
        tile = self.tile
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = int(d / 10.0)
        if steps <= 1:
            return True
        k = 1.0 / steps
        x = a[0]
        y = a[1]
        for i in range(1, steps):
            x += (b[0] - a[0]) * k
            y += (b[1] - a[1]) * k
            tx = int(x // tile)
            ty = int(y // tile)
            if tx < 0 or ty < 0 or ty >= self.map_h or tx >= self.map_w:
                return False
            if not pas[ty][tx]:
                return False
        return True

    def _go_to(self, o, tx, ty):
        """Считать путь к точке (всегда; решение «перепланировать или нет»
        принимает _nav_tick)."""
        me = o.me
        if self._tile(me.x, me.y) == self._tile(tx, ty):
            self.waypoints = None
            self.planned_target = (tx, ty)
            self.nav_t = o.tick
            return
        path = self._astar(me.x, me.y, tx, ty)
        if path is None or len(path) <= 1:
            self.waypoints = None
        else:
            self.waypoints = self._smooth(path)
            self.wi = 0
        self.planned_target = (tx, ty)
        self.nav_t = o.tick

    def _nav_tick(self, o, tx, ty):
        """Следить по точкам; при тупике — перепланировать. (turn, drive)"""
        me = o.me
        t_now = o.tick
        wps = self.waypoints
        active = wps is not None and self.wi < len(wps)
        pt = self.planned_target
        target_moved = (pt is None or
                        math.hypot(tx - pt[0], ty - pt[1]) >= 70.0)
        if (not active) or target_moved or (t_now - self.nav_t > 300):
            self._go_to(o, tx, ty)
            wps = self.waypoints
        if wps is None or self.wi >= len(wps):
            d = math.hypot(tx - me.x, ty - me.y)
            if d < 45.0:
                return 0.0, 0.0
            return self._steer_dir(o, math.atan2(ty - me.y, tx - me.x), 0.85)
        # тупик?
        if self.stuck_pos is None:
            self.stuck_pos = (me.x, me.y)
            self.stuck_tick = t_now
        elif t_now - self.stuck_tick > 45:
            if math.hypot(me.x - self.stuck_pos[0], me.y - self.stuck_pos[1]) < 9.0:
                # дрейфуем: отталкиваем цель вбок и планируем заново
                k = 60.0
                sgn = 1.0 if (t_now // 45) % 2 == 0 else -1.0
                ox = tx - me.x
                oy = ty - me.y
                dl = math.hypot(ox, oy) or 1.0
                tx2 = tx - oy / dl * k * sgn
                ty2 = ty + ox / dl * k * sgn
                self.stuck_pos = (me.x, me.y)
                self.stuck_tick = t_now
                self._go_to(o, tx2, ty2)
                wps = self.waypoints
                if wps is None or self.wi >= len(wps):
                    d = math.hypot(tx2 - me.x, ty2 - me.y)
                    if d < 45.0:
                        return 0.0, 0.0
                    return self._steer_dir(o, math.atan2(ty2 - me.y, tx2 - me.x), 0.85)
            else:
                self.stuck_pos = (me.x, me.y)
                self.stuck_tick = t_now
        wp = wps[self.wi]
        d = math.hypot(wp[0] - me.x, wp[1] - me.y)
        if d < 30.0:
            self.wi += 1
            if self.wi >= len(wps):
                d2 = math.hypot(tx - me.x, ty - me.y)
                if d2 < 45.0:
                    return 0.0, 0.0
                return self._steer_dir(o, math.atan2(ty - me.y, tx - me.x), 0.85)
            wp = wps[self.wi]
            d = math.hypot(wp[0] - me.x, wp[1] - me.y)
        phi = math.atan2(wp[1] - me.y, wp[0] - me.x)
        spd = 0.9 if d > 90.0 else max(0.25, d / 90.0)
        return self._steer_dir(o, phi, spd)

    def _steer_dir(self, o, phi, speed01):
        """Команда (turn, drive) на движение в направлении phi с уходом от стен."""
        me = o.me
        m = o.map
        probe = 64.0
        px = me.x
        py = me.y
        c_straight = m.clearance(px + math.cos(phi) * probe,
                                 py + math.sin(phi) * probe)
        best = phi
        if c_straight < 24.0:
            bc = c_straight
            for da in (0.55, -0.55, 1.1, -1.1, 1.9, -1.9):
                p2 = phi + da
                c = m.clearance(px + math.cos(p2) * probe,
                                py + math.sin(p2) * probe)
                if c >= 24.0:
                    best = p2
                    break
                if c > bc:
                    bc = c
                    best = p2
        err = wrap(best - me.hull)
        aerr = abs(err)
        if aerr > 2.35:
            return (1.0 if err > 0 else -1.0), -0.55
        if aerr > 1.4:
            return (0.7 if err > 0 else -0.7), 0.12
        return clamp(err * 2.2), clamp(speed01, 0.0, 1.0)

    # --- следящая система ----------------------------------------------------

    def _track(self, o):
        me = o.me
        t = o.time
        e = o.enemy
        if e is not None:
            re_seen = self.lost > 0.25     # снова увидели после слепого участка
            if not self.ever_seen:
                self.e_omega = 0.0
                self.ever_seen = True
            else:
                dt = max(o.dt, 1e-4)
                dh = wrap(e.hull - self.e_hull)
                om = clamp(dh / dt, -3.0, 3.0)
                self.e_omega += (om - self.e_omega) * 0.22
                if re_seen:
                    self._om_hist = []        # свежий след — чистая история
                self._om_hist.append((t, om))
                _oc = t - 0.7
                while len(self._om_hist) > 2 and self._om_hist[0][0] < _oc:
                    self._om_hist.pop(0)
                if not re_seen:
                    # ускорение: насколько быстро меняется вектор скорости —
                    # манёврирующая цель требует больший запас при прицеливании
                    dvx = (e.vx - self.ev_x) / dt
                    dvy = (e.vy - self.ev_y) / dt
                    a = math.hypot(dvx, dvy)
                    if a > 1500.0:
                        a = 1500.0
                    self.e_ax += (a - self.e_ax) * 0.3
                else:
                    self.e_ax = 0.0
                    self.e_omega = 0.0
            # скорость врага — точные данные движка, сглаживать не нужно:
            # EMA запаздывала за сменой направления на 2-3 тика
            self.ev_x = e.vx
            self.ev_y = e.vy
            self.e_hull = e.hull
            self.last_pos = (e.x, e.y)
            self.last_time = t
            self.lost = 0.0
        else:
            self.lost = t - self.last_time if self.ever_seen else 0.0
            # вне зоны видимости враг может затормозить/остановиться
            self.e_omega *= max(0.0, 1.0 - 1.4 * o.dt)
            self.e_ax *= max(0.0, 1.0 - 1.4 * o.dt)
        # урон по нам — срываем контакт
        if me.hp < self.my_hp - 1e-6:
            self.evade_until = t + 0.85
        self.my_hp = me.hp

    def _predict(self, o, tf):
        """Позиция и курс врага через tf секунд (с упором в стену)."""
        lx, ly = self.last_pos
        damp = 1.0 / (1.0 + 0.3 * self.lost)
        vx = self.ev_x * damp
        vy = self.ev_y * damp
        px = lx + vx * tf
        py = ly + vy * tf
        if (px - lx) * (px - lx) + (py - ly) * (py - ly) > 256.0 and \
                o.map.blocked_between(lx, ly, px, py):
            # враг не проедет сквозь стену: обрезаем предсказание
            for k in (6, 5, 4, 3, 2, 1):
                qx = lx + (px - lx) * k / 6.0
                qy = ly + (py - ly) * k / 6.0
                if o.map.blocked_between(lx, ly, qx, qy):
                    px, py = lx + (px - lx) * (k - 1) / 6.0, ly + (py - ly) * (k - 1) / 6.0
                    break
        # курс — инерция текущего вращения; неопределённость свинга
        # накрывает _aim worst-case оценкой (см. там half_swing)
        eh = wrap(self.e_hull + self.e_omega * tf)
        return px, py, eh

    # --- прицел: выбор точки удара -------------------------------------------

    def _aim(self, o, px, py, eh):
        """Лучи от ствола к кандидатам на предсказанном корпусе.

        Каждый луч оценивается в худшем случае: корпус к моменту удара мог
        повернуться на ±half_swing (вращение могло пойти иначе, чем сейчас).
        Возвращает (idx, score, wx, wy, flight_s): точку, куда вести башню.
        """
        me = o.me
        mx = me.x + math.cos(me.turret) * MUZZLE
        my = me.y + math.sin(me.turret) * MUZZLE
        ch = math.cos(eh)
        sh = math.sin(eh)
        # насколько корпус мог развернуться за время полёта: текущее
        # вращение могло продолжить/остановиться, либо (пока статичная,
        # но движущаяся) цель могла начать манёвр корпуса — минимум
        # ~0.2 рад закрывает p90 таких «стартующих» вращений (кал-ка
        # по боям: |rot| med 5°, p90 13°, max 15° при спокойной истории).
        flight0 = math.hypot(px - mx, py - my) / BULLET_SPEED
        v_e = math.hypot(self.ev_x, self.ev_y)
        floor = 0.20 * flight0 if v_e > 60.0 else 0.05
        half_swing = max(0.55 * abs(self.e_omega) * flight0, floor)
        hulls = (eh, eh + half_swing, eh - half_swing) if half_swing > 0.06 \
            else (eh,)
        best_score = -1.0
        best_idx = self.aim_idx
        best_w = (px, py)
        best_fl = 0.0
        for i in range(NC):
            lx, ly = CANDS[i]
            wx = px + lx * ch - ly * sh
            wy = py + lx * sh + ly * ch
            dx = wx - mx
            dy = wy - my
            L = math.hypot(dx, dy)
            if L < 8.0:
                continue
            dx /= L
            dy /= L
            res = seg_obb(mx, my, dx, dy, px, py, HL, HW, eh)
            if res is None:
                continue
            tpx, _nx, _ny = res
            flight = tpx / BULLET_SPEED
            err_px = 9.0 * flight + 90.0 * flight * flight
            margin = err_px / max(tpx, 1.0) * 57.2958 + SPREAD + 0.8
            # манёвр (смена направления): положение к моменту удара
            # непредсказуемо пропорционально квадрату времени полёта
            margin += (min(self.e_ax, 900.0) * flight * flight * 0.5
                       / max(tpx, 1.0)) * 57.2958
            if o.map.blocked_between(mx, my, wx, wy):
                worst = 0.02    # стена на линии: почти потерянный выстрел
            else:
                worst = 2.0
                for hh in hulls:
                    res2 = seg_obb(mx, my, dx, dy, px, py, HL, HW, hh)
                    if res2 is None:
                        worst = 0.0
                        break
                    _t2, nx, ny = res2
                    f = face_of(nx, ny, hh)
                    limit = RICO_BY_FACE[f]
                    ct = -(dx * nx + dy * ny)
                    if ct < 0.0:
                        ct = 0.0
                    elif ct > 1.0:
                        ct = 1.0
                    theta = DEG(math.acos(ct))
                    if theta < limit - margin:
                        sc = 1.0
                    elif theta < limit:
                        sc = 0.45
                    else:
                        sc = 0.16   # рикошет: отскок сам может доложить цель
                    if sc < worst:
                        worst = sc
            if worst > best_score:
                best_score = worst
                best_idx = i
                best_w = (wx, wy)
                best_fl = flight
        return best_idx, best_score, best_w, best_fl

    # --- орбита ---------------------------------------------------------------

    def _orbit_speed_dir(self, o, px, py, eh):
        """Желаемое направление скорости (радиальное + тангенциальное)."""
        me = o.me
        t = o.time
        ux = me.x - px
        uy = me.y - py
        d = math.hypot(ux, uy) or 1.0
        rx = ux / d
        ry = uy / d
        rear_x = -math.cos(eh)
        rear_y = -math.sin(eh)
        pxp = -ry            # перпендикуляр к радиусу
        pyp = rx
        s = self.orbit_side
        side_dot = pxp * rear_x + pyp * rear_y
        if abs(side_dot) > 0.05:
            s = 1.0 if side_dot > 0.0 else -1.0
            self.orbit_side = s
        # радиус
        e_hp = o.enemy.hp if o.enemy else 5.0
        R = 330.0 + (e_hp - me.hp) * 12.0
        if me.hp <= 4.0:
            R = 430.0
        elif e_hp <= 4.5:
            R = 265.0
        if t < self.evade_until:
            R += 90.0
        R = clamp(R, 240.0, 450.0)
        # радиальная составляющая: vr > 0 — отдаляемся (rx направлен от врага)
        rd = d - R
        if rd > 45.0:
            vr = -150.0          # далеко — сближаемся
        elif rd < -45.0:
            vr = 85.0            # близко — отходим
        else:
            vr = 0.0
        vt = (150.0 + 55.0 * math.sin(t * 1.1 + self.phase)
              + 35.0 * math.sin(t * 2.3 + self.phase * 2.0))
        vx = s * pxp * vt + rx * vr
        vy = s * pyp * vt + ry * vr
        # в упор — срываем контакт вбок
        if d < 160.0:
            vx = rx * 165.0 + s * pxp * 95.0
            vy = ry * 165.0 + s * pyp * 95.0
        # получили урон — уплываем
        if t < self.evade_until:
            vx = rx * 190.0 + s * pxp * 120.0
            vy = ry * 190.0 + s * pyp * 120.0
        return math.atan2(vy, vx), d

    # --- тик ------------------------------------------------------------------

    def on_tick(self, o):
        self._map(o)
        me = o.me
        t = o.time
        self._track(o)

        if o.enemy is not None:
            # упреждение: итерации от позиции ствола (от него летит снаряд)
            mx0 = me.x + math.cos(me.turret) * MUZZLE
            my0 = me.y + math.sin(me.turret) * MUZZLE
            px, py, eh = o.enemy.x, o.enemy.y, o.enemy.hull
            tf = 0.0
            for _ in range(3):
                tf = math.hypot(px - mx0, py - my0) / BULLET_SPEED
                qx, qy, qh = self._predict(o, tf)
            px, py, eh = qx, qy, qh
            idx, score, (wx, wy), flight = self._aim(o, px, py, eh)
            self.aim_idx = idx
            # башня
            err = o.aim_error(wx, wy)
            turret = clamp(err * 4.0)
            residual = abs(err - turret * TURRET_TURN * o.dt)
            dme = math.hypot(px - me.x, py - me.y)
            tol = o.hit_tolerance(dme, 0.9)
            fire = False
            if me.ammo_ready and dme < self.RANGE_MAX:
                fmin = self.FIRE_MIN_CLOSE if dme < 200.0 else self.FIRE_MIN
                fire = residual < tol and score >= fmin
            # ход
            phi, d = self._orbit_speed_dir(o, o.enemy.x, o.enemy.y, o.enemy.hull)
            turn, drive = self._steer_dir(o, phi, 0.85)
            # джиттер: ломает прогноз скорости у противника — несколько
            # частот, чтобы ускорение было непредсказуемым
            turn = clamp(turn + 0.15 * math.sin(t * 4.7 + self.phase)
                         + 0.10 * math.sin(t * 8.3 + self.phase * 1.7))
            drive = clamp(drive + 0.05 * math.sin(t * 2.9 + self.phase * 2.0)
                          + 0.04 * math.sin(t * 6.1 + self.phase))
            return Action(drive=drive, turn=turn, turret=turret, fire=fire)

        # --- врага не видно ----------------------------------------------------
        if not self.ever_seen:
            return self._go_hunt(o)
        px, py, eh = self._predict(o, 0.0)
        lost = self.lost
        # засада: стоим за укрытием, пока враг в радиусе 300 от последней точки
        if lost < 7.0:
            lx, ly = self.last_pos
            if (not o.map.blocked_between(lx, ly, me.x, me.y)) is False and \
                    math.hypot(me.x - lx, me.y - ly) < 320.0:
                # нас не видно из его последней позиции — держим позицию
                turn = clamp(wrap(math.atan2(py - me.y, px - me.x) - me.hull) * 1.5)
                turret = o.aim_turret(px, py)
                return Action(drive=0.0, turn=turn, turret=turret,
                               fire=False)
        # свежий след — догоняем предсказание; иначе — маршрут разведки
        if lost < 1.5:
            tx, ty, _ = self._predict(o, min(lost + 0.3, 0.8))
        else:
            tx, ty = self._tour_point(o)
        turn, drive = self._nav_tick(o, tx, ty)
        if lost < 6.0:
            turret = o.aim_turret(px, py)
        else:
            turret = o.aim_turret(*o.map.spawns[1 - self.my_tank])
        return Action(drive=drive, turn=turn, turret=turret, fire=False)

    def _flank_point(self, o):
        """Точка в 170 px сбоку от вражеского спавна (тот, где просторнее)."""
        m = o.map
        sx, sy = m.spawns[1 - self.my_tank]
        me = o.me
        ox = me.x - sx
        oy = me.y - sy
        dl = math.hypot(ox, oy) or 1.0
        nx = -oy / dl
        ny = ox / dl
        ax = sx + nx * 170.0
        ay = sy + ny * 170.0
        bx = sx - nx * 170.0
        by = sy - ny * 170.0
        if m.clearance(bx, by) > m.clearance(ax, ay):
            return bx, by
        return ax, ay

    def _tour_point(self, o):
        """Следующая точка маршрута разведки.

        Маршрут: фланг вражеского спавна -> центр карты -> свой спавн ->
        середины кромок. Ездить по кругу нужно: стоять на вражеском спавне
        и ждать — верный способ доиграть бой в ничью (враг там уже был).
        """
        m = o.map
        me = o.me
        t = o.time
        if self.tour_pts is None:
            W, H = m.pixel_width, m.pixel_height
            ex, ey = m.spawns[1 - self.my_tank]
            mx, my = m.spawns[self.my_tank]
            self.tour_pts = [self._flank_point(o), (W * 0.5, H * 0.5),
                             (mx, my), (W * 0.5, H * 0.22), (W * 0.5, H * 0.78)]
            self.tour_i = 0
            self.tour_t0 = t
        px, py = self.tour_pts[self.tour_i]
        if math.hypot(me.x - px, me.y - py) < 80.0 or t - self.tour_t0 > 14.0:
            self.tour_i = (self.tour_i + 1) % len(self.tour_pts)
            self.tour_t0 = t
            px, py = self.tour_pts[self.tour_i]
        return px, py

    def _go_hunt(self, o):
        """Врага не видно: едем по маршруту разведки."""
        me = o.me
        tx, ty = self._tour_point(o)
        turn, drive = self._nav_tick(o, tx, ty)
        if self.ever_seen and self.lost < 6.0:
            px, py, _ = self._predict(o, 0.0)
            turret = o.aim_turret(px, py)
        else:
            # башня ведёт направление движения: видим раньше, целимся быстрее
            phi = math.atan2(ty - me.y, tx - me.x)
            turret = clamp(wrap(phi - me.turret) * 2.0)
        return Action(drive=drive, turn=turn, turret=turret, fire=False)


program = Brain()
