#!TANKP 1
# name: Taipan
# author: arena-1c3856dd
# difficulty: 5
# color: #17d39b
# description: Тайпан — дуэлянт мёртвой зоны: нос в ракурсе 35.5° к линии угрозы (летящий снаряд, иначе ствол врага и упреждённая точка), газ режется ради доворота (движок нормирует гусеницы — полный газ вдвое медлит разворот), выстрел — середина самой широкой пробивающей полосы через силуэт цели по трём моделям чужого хода с онлайн-выбором модели и обучением сноса, добивание раненого без оглядки на дистанцию, A*-навигация.

from math import acos, atan2, cos, degrees, fabs, hypot, pi, radians, sin

from tankp import Action, TankProgram

# --- баланс движка (config.py) ---------------------------------------------
DMG = 4.0
HP_MAX = 10.0
BULLET = 620.0
RELOAD = 1.0
HULL_TURN = 2.6
TURRET_TURN = 3.6
MUZ = 26.0
SPREAD = radians(0.4)
HL = 20.0
HW = 14.0
HULL_FRONT_HALF = 35.0
RICO = (30.0, 50.0, 60.0)

# --- настройки боя ---------------------------------------------------------
BAND = 35.5                 # ракурс к линии угрозы
BAND_STICK = 0.35           # гистерезис выбора стороны ракурса, рад
D_STAND = 235.0             # рабочая дистанция
D_DASH = 52.0               # цель при дожиме
D_CLOSE = 92.0              # ближе не подпускаем (кроме дожима)
DASH_MAX = 150.0            # с какой дистанции имеет смысл дожим
DASH_CD = 0.55              # чужой перезаряд, при котором идём в дожим
PEND_MIN = 0.45             # полупериод маятника, с
PEND_MAX = 1.05
GEAR_FLIP = 0.45
THROTTLE = 0.95
TURN_COST = 0.5
TRACK_GAIN = 4.5
GATE_MARGIN = 2.5           # запас пробития для выстрела, град
GATE_LOOSE = -5.0           # выстрел «на удачу», когда у врага пусто
FLEE_HP = 4.0
ERR_FIRE = 60.0             # допуск по ошибке прогноза, px (как у mongoose)
TF_MAX = 1.35               # предел времени полёта, с
RACKUR_FULL = 0.28          # рад (16°): на такой ошибке курса газ срезан полностью

TAU = 2.0 * pi
INF = float("inf")


def wrap(a):
    a = (a + pi) % TAU
    return a - pi


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def unit(x, y):
    d = hypot(x, y)
    if d < 1e-9:
        return (0.0, 0.0)
    return (x / d, y / d)


def face_of(nx, ny, hull):
    rel = degrees(fabs(wrap(atan2(ny, nx) - hull)))
    if rel <= HULL_FRONT_HALF:
        return 0
    if rel >= 180.0 - HULL_FRONT_HALF:
        return 2
    return 1


def ray_obb(ox, oy, dx, dy, cx, cy, hull, hl=HL, hw=HW, maxt=4000.0):
    """Первое пересечение луча с корпусом: (t, грань, theta). Копия движка."""
    ca = cos(hull)
    sa = sin(hull)
    rx = ox - cx
    ry = oy - cy
    lx = rx * ca + ry * sa
    ly = -rx * sa + ry * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca
    tmin = 0.0
    tmax = maxt
    axis = -1
    sign = 0.0
    if fabs(ldx) < 1e-12:
        if fabs(lx) > hl:
            return None
    else:
        s = -1.0 if ldx > 0.0 else 1.0
        t1 = (-hl - lx) / ldx
        t2 = (hl - lx) / ldx
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
    if fabs(ldy) < 1e-12:
        if fabs(ly) > hw:
            return None
    else:
        s = -1.0 if ldy > 0.0 else 1.0
        t1 = (-hw - ly) / ldy
        t2 = (hw - ly) / ldy
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
            nxl, nyl = (-1.0 if ldx > 0.0 else 1.0), 0.0
        else:
            nxl, nyl = 0.0, (-1.0 if ldy > 0.0 else 1.0)
        nx = nxl * ca - nyl * sa
        ny = nxl * sa + nyl * ca
        return 0.0, face_of(nx, ny, hull), 0.0
    if axis == 0:
        nxl, nyl = sign, 0.0
    else:
        nxl, nyl = 0.0, sign
    nx = nxl * ca - nyl * sa
    ny = nxl * sa + nyl * ca
    ct = -(dx * nx + dy * ny)
    if ct < 0.0:
        ct = 0.0
    elif ct > 1.0:
        ct = 1.0
    return tmin, face_of(nx, ny, hull), degrees(acos(ct))


def pen_ok(theta, face, margin=0.0):
    return theta < RICO[face] - margin


# ---------------------------------------------------------------------------
# карта
# ---------------------------------------------------------------------------
class Nav:
    __slots__ = ("w", "h", "ts", "free", "opq", "clear", "path", "goal", "pi_",
                 "spawns")

    def __init__(self, mv):
        self.ts = ts = mv.tile_size
        self.w = w = mv.width
        self.h = h = mv.height
        free = bytearray(w * h)
        opq = bytearray(w * h)
        for y, row in enumerate(mv.rows):
            base = y * w
            for x, ch in enumerate(row):
                if ch in "#o:":
                    opq[base + x] = 1
                else:
                    free[base + x] = 1
        self.free = free
        self.opq = opq
        self.clear = mv.clearance_grid
        self.path = []
        self.goal = None
        self.pi_ = 0
        self.spawns = [(s.x, s.y) for s in mv.spawns]

    def free_px(self, x, y):
        ts = self.ts
        tx = int(x // ts)
        ty = int(y // ts)
        if tx < 0 or ty < 0 or tx >= self.w or ty >= self.h:
            return False
        return self.free[ty * self.w + tx] == 1

    def clear_px(self, x, y):
        ts = self.ts
        tx = int(x // ts)
        ty = int(y // ts)
        if tx < 0:
            tx = 0
        elif tx >= self.w:
            tx = self.w - 1
        if ty < 0:
            ty = 0
        elif ty >= self.h:
            ty = self.h - 1
        return self.clear[ty][tx] * ts

    def opq_px(self, x, y):
        ts = self.ts
        tx = int(x // ts)
        ty = int(y // ts)
        if tx < 0 or ty < 0 or tx >= self.w or ty >= self.h:
            return True
        return self.opq[ty * self.w + tx] == 1

    def los(self, x0, y0, x1, y1):
        ts = self.ts
        dx = x1 - x0
        dy = y1 - y0
        dist = hypot(dx, dy)
        if dist < 1e-6:
            return not self.opq_px(x0, y0)
        dx /= dist
        dy /= dist
        if dx > 0:
            step_x = 1
            tdx = ts / dx
            tmx = ((int(x0 // ts) + 1) * ts - x0) / dx
        elif dx < 0:
            step_x = -1
            tdx = -ts / dx
            tmx = (int(x0 // ts) * ts - x0) / dx
        else:
            step_x = 0
            tdx = INF
            tmx = INF
        if dy > 0:
            step_y = 1
            tdy = ts / dy
            tmy = ((int(y0 // ts) + 1) * ts - y0) / dy
        elif dy < 0:
            step_y = -1
            tdy = -ts / dy
            tmy = (int(y0 // ts) * ts - y0) / dy
        else:
            step_y = 0
            tdy = INF
            tmy = INF
        x = int(x0 // ts)
        y = int(y0 // ts)
        t = 0.0
        guard = self.w + self.h + 4
        while t <= dist:
            if tmx < tmy:
                t = tmx
                tmx += tdx
                x += step_x
            else:
                t = tmy
                tmy += tdy
                y += step_y
            if t > dist:
                break
            guard -= 1
            if guard < 0:
                break
            if x < 0 or y < 0 or x >= self.w or y >= self.h:
                return False
            if self.opq[y * self.w + x]:
                return False
        return True

    def astar(self, sx, sy, gx, gy, limit=1400):
        import heapq
        ts = self.ts
        w = self.w
        h = self.h
        s_tx, s_ty = int(sx // ts), int(sy // ts)
        g_tx, g_ty = int(gx // ts), int(gy // ts)
        free = self.free
        if not (0 <= g_tx < w and 0 <= g_ty < h and free[g_ty * w + g_tx]):
            best = None
            best_d = INF
            for oy in range(-4, 5):
                for ox in range(-4, 5):
                    tx, ty = g_tx + ox, g_ty + oy
                    if 0 <= tx < w and 0 <= ty < h and free[ty * w + tx]:
                        d = ox * ox + oy * oy
                        if d < best_d:
                            best_d = d
                            best = (tx, ty)
            if best is None:
                return []
            g_tx, g_ty = best
        start = s_ty * w + s_tx
        goal = g_ty * w + g_tx
        if start == goal:
            return [(gx, gy)]
        open_heap = [(0.0, s_tx, s_ty)]
        gscore = {start: 0.0}
        came = {}
        seen = 0
        found = False
        while open_heap and seen < limit:
            _, cx, cy = heapq.heappop(open_heap)
            seen += 1
            if cx == g_tx and cy == g_ty:
                found = True
                break
            base = cy * w + cx
            cur = gscore.get(base, INF)
            for dx2, dy2 in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx2, cy + dy2
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                nb = ny * w + nx
                if not free[nb]:
                    continue
                ng = cur + 1.0
                if ng < gscore.get(nb, INF):
                    gscore[nb] = ng
                    came[nb] = base
                    heapq.heappush(open_heap,
                                   (ng + abs(nx - g_tx) + abs(ny - g_ty),
                                    nx, ny))
        if not found:
            return []
        out = []
        node = goal
        while node != start:
            out.append(((node % w + 0.5) * ts, (node // w + 0.5) * ts))
            node = came.get(node, start)
            if len(out) > 500:
                break
        out.reverse()
        return out

    def route(self, x, y, gx, gy):
        gd = hypot(self.goal[0] - gx, self.goal[1] - gy) if self.goal else INF
        if not self.path or gd > 70.0 or self.pi_ >= len(self.path):
            self.path = self.astar(x, y, gx, gy)
            self.goal = (gx, gy)
            self.pi_ = 0
        if not self.path:
            return None
        while self.pi_ < len(self.path) - 1:
            px, py = self.path[self.pi_]
            if hypot(px - x, py - y) < self.ts * 1.3:
                self.pi_ += 1
            else:
                break
        return self.path[min(self.pi_, len(self.path) - 1)]


# ---------------------------------------------------------------------------
# мозг
# ---------------------------------------------------------------------------
class Brain(TankProgram):
    def on_start(self, ctx):
        self.nav = None
        self.t = -1.0
        self.dt = 1.0 / 60.0
        # враг
        self.seen = False
        self.et = -99.0
        self.ex = 0.0
        self.ey = 0.0
        self.evx = 0.0
        self.evy = 0.0
        self.ehull = 0.0
        self.eturret = 0.0
        self.ehp = HP_MAX
        self.e_cd = 0.0
        self.e_ready = True
        self.prev_cd = None
        self.e_omega = 0.0
        self.bear_prev = None
        self.bear_t = -99.0
        self.bear_omega = 0.0
        # враждебные снаряды
        self.threats = []
        # поведение
        self.side = 1.0
        self.side_until = 0.0
        self.gear = 1.0
        self.gear_until = 0.0
        self.mode = "hunt"
        self.last_tick = -1
        self.rnd = 0x2545F491
        self.hunt_goal = None
        self.hunt_t = -99.0
        self.e_lastx = 0.0
        self.e_lasty = 0.0
        # три модели чужого хода, соревнуются по квадрату ошибки (mongoose)
        self.m_err = [1200.0, 1200.0, 1200.0]
        self.m_hist = []
        # обученный систематический снос врага от прогноза, px
        self.dodge = 0.0
        self.dodge_n = 0
        self.myshots = []
        # передача держится, пока наш снаряд в полёте
        self.gear_lock_until = -99.0
        # ускорение врага (третья модель хода)
        self.e_ax = 0.0
        self.e_ay = 0.0

    # --- враг ---------------------------------------------------------------
    def _see(self, o, e):
        t = self.t
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.25:
                om = wrap(e.hull - self.ehull) / dt
                self.e_omega = self.e_omega * 0.7 + \
                    clamp(om, -HULL_TURN * 1.4, HULL_TURN * 1.4) * 0.3
                self.e_ax = self.e_ax * 0.8 + clamp((e.vx - self.evx) / dt, -900.0, 900.0) * 0.2
                self.e_ay = self.e_ay * 0.8 + clamp((e.vy - self.evy) / dt, -900.0, 900.0) * 0.2
            elif dt >= 0.5:
                self.e_omega *= 0.4
        me = o.me
        bear = atan2(e.y - me.y, e.x - me.x)
        if self.bear_prev is not None and 0.0005 < t - self.bear_t < 0.25:
            om_b = wrap(bear - self.bear_prev) / (t - self.bear_t)
            self.bear_omega = self.bear_omega * 0.7 + \
                clamp(om_b, -4.0, 4.0) * 0.3
        self.bear_prev = bear
        self.bear_t = t
        if self.prev_cd is not None and e.cooldown > self.prev_cd + 0.4:
            ang = e.turret
            self.threats.append((e.x + cos(ang) * MUZ, e.y + sin(ang) * MUZ,
                                 cos(ang), sin(ang), t))
            if len(self.threats) > 3:
                del self.threats[0]
        self.prev_cd = e.cooldown
        self.e_lastx = self.ex
        self.e_lasty = self.ey
        self.ex = e.x
        self.ey = e.y
        self.evx = e.vx
        self.evy = e.vy
        self.ehull = e.hull
        self.eturret = e.turret
        self.ehp = e.hp
        self.e_cd = e.cooldown
        self.e_ready = e.ammo_ready
        self.et = t
        self.seen = True

    MODEL_HORIZON = 9          # тиков вперёд для проверки моделей (0.15 с)

    def _learn_models(self, tick, me, o):
        """Сверяем предсказания моделей с фактом и ведём их ошибки (EMA).

        Каждый тик каждая модель предсказывает чужой курс на HORIZON тиков
        вперёд; когда срок наступает и враг снова виден, ошибка идёт в EMA.
        Так программа сама выясняет школу соперника: gyurza держит ракурс к
        упреждённой точке, mongoose — к сегодняшнему азимуту.
        """
        q = self.mpred
        i = 0
        errs = self.model_err
        while i < len(q):
            due, idx, ph = q[i]
            if tick < due:
                i += 1
                continue
            q.pop(i)
            if self.seen and idx < len(errs):
                e = fabs(wrap(ph - self.ehull))
                errs[idx] = errs[idx] * 0.97 + e * 0.03
            continue
        h = self.MODEL_HORIZON * self.dt
        for idx, ph in enumerate(self._foe_hull_models(me, h, self.ex, self.ey)):
            if idx < len(errs):
                q.append((tick + self.MODEL_HORIZON, idx, ph))
        if len(q) > 64:
            del q[:len(q) - 64]

    def _best_model(self):
        errs = self.model_err
        bi = 0
        bv = errs[0]
        for i in range(1, len(errs)):
            if errs[i] < bv:
                bv = errs[i]
                bi = i
        return bi

    def _foe_pos(self):
        age = self.t - self.et
        k = 0.7 if age < 3.0 else 0.0
        return (self.ex + self.evx * age * k, self.ey + self.evy * age * k)

    def _advance_threats(self, me):
        live = []
        keep = []
        for (x0, y0, dx, dy, t0) in self.threats:
            age = self.t - t0
            bx = x0 + dx * BULLET * age
            by = y0 + dy * BULLET * age
            rx = me.x - bx
            ry = me.y - by
            along = dx * rx + dy * ry
            if age > 1.9:
                continue
            if along < -12.0 and hypot(rx, ry) > 50.0:
                continue
            keep.append((x0, y0, dx, dy, t0))
            live.append((bx, by, dx, dy, t0))
        self.threats = keep
        self._live = live

    def _incoming(self, me):
        best = None
        for (bx, by, dx, dy, t0) in getattr(self, "_live", ()):
            rx = me.x - bx
            ry = me.y - by
            along = dx * rx + dy * ry
            if along <= 0.0:
                continue
            lat = -dy * rx + dx * ry
            if fabs(lat) > 120.0:
                continue
            th = along / BULLET
            if th > 1.6:
                continue
            if best is None or th < best[0]:
                best = (th, lat, dx, dy, bx, by)
        return best

    # --- ракурс -------------------------------------------------------------
    def _band(self, me, toward):
        """Ракурс: нос в сторону угрозы +- BAND, сторона липкая."""
        if self.nav is None:
            pass
        a1 = toward + BAND * 0.017453292519943295 * self.side
        a2 = toward - BAND * 0.017453292519943295 * self.side
        d1 = fabs(wrap(a1 - me.hull))
        d2 = fabs(wrap(a2 - me.hull))
        if d2 + BAND_STICK < d1:
            self.side = -self.side
            return a2
        return a1

    def _hull_target(self, o, me):
        """Куда целить корпус: мёртвая зона к ТОЧНОЙ линии угрозы."""
        inc = self._incoming(me)
        if inc is not None:
            th, lat, dx, dy, bx, by = inc
            # линия снаряда: origin -> me; нос в сторону origin
            return self._band(me, atan2(-dy, -dx)), th
        if self.seen:
            # враг целится с упреждением: его снаряд придёт по линии
            # «его корпус -> моя будущая точка». Текущий угол ствола брать
            # нельзя: пока башня доворачивается, он смещает линию на
            # atan(26/d) и ракурс уходит из мёртвой зоны.
            fx, fy = self._foe_pos()
            t_fly = hypot(me.x - fx, me.y - fy) / BULLET
            ux = me.x + me.vx * t_fly * 0.95
            uy = me.y + me.vy * t_fly * 0.95
            toward = atan2(uy - fy, ux - fx)
            return self._band(me, toward + pi), t_fly
        if self.t - self.bear_t < 8.0:
            # по памяти: держим ракурс к последнему известному направлению,
            # снаряд мог быть выпущен и летит в нас до сих пор
            return self._band(me, self.bear_prev + pi), 9.9
        return None, 9.9

    # --- стрельба -----------------------------------------------------------
    def _learn_shot(self, o):
        """Сверка прогнозов с фактом: три модели хода и снос врага от выстрела."""
        e = o.enemy
        h = self.m_hist
        while h and h[0][0] <= o.tick:
            _due, cx, cy, arx, ary, acx, acy = h.pop(0)
            if e is not None:
                fx, fy = e.x, e.y
                self.m_err[0] = self.m_err[0] * 0.88 + ((fx - cx) ** 2 + (fy - cy) ** 2) * 0.12
                self.m_err[1] = self.m_err[1] * 0.88 + ((fx - arx) ** 2 + (fy - ary) ** 2) * 0.12
                self.m_err[2] = self.m_err[2] * 0.88 + ((fx - acx) ** 2 + (fy - acy) ** 2) * 0.12
        q = self.myshots
        while q and q[0][0] <= o.tick:
            _due, mx, my, dx, dy, cx, cy = q.pop(0)
            if e is not None:
                lat = -dy * (e.x - mx) + dx * (e.y - my)
                self.dodge = self.dodge * 0.7 + lat * 0.3
                self.dodge_n += 1

    def _predict(self, o, mx, my):
        """Точка прицела и время полёта: взвесь трёх моделей по их ошибке."""
        e = o.enemy
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        sp = hypot(vx, vy)
        om = self.e_omega
        w0 = 1.0 / (self.m_err[0] + 40.0)
        w1 = 1.0 / (self.m_err[1] + 40.0)
        w2 = 1.0 / (self.m_err[2] + 40.0)
        ws = w0 + w1 + w2
        w0, w1, w2 = w0 / ws, w1 / ws, w2 / ws
        arc = fabs(om) > 0.05 and sp > 25.0
        if arc:
            a0 = atan2(vy, vx)
            r = sp / om
        t = hypot(x - mx, y - my) / BULLET
        px, py = x, y
        for _ in range(2):
            cx = x + vx * t
            cy = y + vy * t
            if arc:
                arx = x + r * (sin(a0 + om * t) - sin(a0))
                ary = y - r * (cos(a0 + om * t) - cos(a0))
            else:
                arx, ary = cx, cy
            acx = x + vx * t + 0.5 * self.e_ax * t * t
            acy = y + vy * t + 0.5 * self.e_ay * t * t
            px = w0 * cx + w1 * arx + w2 * acx
            py = w0 * cy + w1 * ary + w2 * acy
            t = hypot(px - mx, py - my) / BULLET
        # обученный снос: враг систематически уходит вбок от нашей линии
        if self.dodge_n >= 3 and (self.dodge > 18.0 or self.dodge < -18.0):
            if arc:
                bx, by = arx, ary
            else:
                bx, by = cx, cy
            ux, uy = unit(px - bx, py - by)
            px += -uy * self.dodge
            py += ux * self.dodge
        err = (self.m_err[0] * w0 + self.m_err[1] * w1 + self.m_err[2] * w2) ** 0.5
        due = o.tick + int(t * 60.0) + 2
        self.m_hist.append((due, cx, cy, arx, ary, acx, acy))
        if len(self.m_hist) > 6:
            del self.m_hist[0]
        return px, py, t, err

    def _hull_arrival(self, me, t_fly):
        """Гипотезы чужого курса к моменту удара.

        Сильнейшая по замерам — «корпус как есть»; вторая — экстраполяция
        угловой скорости; третья — сервопривод успевает доехать до ракурса.
        """
        hull_free = wrap(self.ehull + clamp(self.e_omega, -HULL_TURN, HULL_TURN) * min(t_fly, 0.5))
        fx = me.x + me.vx * t_fly
        fy = me.y + me.vy * t_fly
        ex_f = self.ex + self.evx * t_fly
        ey_f = self.ey + self.evy * t_fly
        bear = atan2(fy - ey_f, fx - ex_f)
        base = atan2(me.y - self.ey, me.x - self.ex)
        if fabs(self.e_omega) > 0.8:
            sgn = 1.0 if self.e_omega >= 0.0 else -1.0
        else:
            sgn = 1.0 if wrap(self.ehull - base) >= 0.0 else -1.0
        target = wrap(bear + sgn * radians(BAND))
        move = wrap(target - self.ehull)
        limit = HULL_TURN * 0.9 * t_fly
        if move > limit:
            hull_servo = wrap(self.ehull + limit)
        elif move < -limit:
            hull_servo = wrap(self.ehull - limit)
        else:
            hull_servo = target
        return hull_free, hull_servo

    def _ray_pen(self, mx, my, a, px, py, hull, margin):
        """Пробьёт ли выстрел по направлению a этот корпус (луч из ствола)."""
        dx = cos(a)
        dy = sin(a)
        hit = ray_obb(mx + dx * MUZ, my + dy * MUZ, dx, dy, px, py, hull)
        if hit is None:
            return False
        return pen_ok(hit[2], hit[1], margin)

    def _shot_dir(self, mx, my, px, py, hull, d, margin):
        """Середина самой широкой пробивающей полосы через силуэт цели."""
        bearing = atan2(py - my, px - mx)
        half = atan2(HW + 6.0, max(d, 60.0))
        if half > 0.42:
            half = 0.42
        n = 9
        good = []
        for k in range(n):
            a = bearing - half + 2.0 * half * k / (n - 1)
            good.append(a if self._ray_pen(mx, my, a, px, py, hull, margin) else None)
        mid = (n - 1) * 0.5
        best = None
        k = 0
        while k < n:
            if good[k] is None:
                k += 1
                continue
            j = k
            while j + 1 < n and good[j + 1] is not None:
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
        hit = ray_obb(mx + dx * MUZ, my + dy * MUZ, dx, dy, px, py, hull)
        tt = hit[0] if hit is not None else max(d, 1.0)
        return (mx + dx * MUZ + dx * tt, my + dy * MUZ + dy * tt)

    def _gun(self, o, me, e):
        if e is None:
            return self._gun_blind(o, me)
        self._learn_shot(o)
        if fabs(self.t - self.et) > 0.05:
            # врага не видно: ствол по курсу, ждём
            return o.aim_turret(me.x + cos(me.hull) * 150.0,
                                me.y + sin(me.hull) * 150.0), False
        mx = me.x + cos(me.turret) * MUZ
        my = me.y + sin(me.turret) * MUZ
        px, py, t_fly, err_px = self._predict(o, mx, my)
        d = hypot(px - me.x, py - me.y)
        hungry = self.ehp <= DMG
        hull_free, hull_servo = self._hull_arrival(me, t_fly)
        aim = self._shot_dir(me.x, me.y, px, py, self.ehull, d, GATE_MARGIN)
        if aim is None:
            aim = self._shot_dir(me.x, me.y, px, py, hull_free, d, GATE_MARGIN + 2.0)
        if aim is None:
            aim = self._shot_dir(me.x, me.y, px, py, hull_servo, d, GATE_MARGIN + 3.0)
        if aim is None:
            return o.aim_turret(px, py), False
        a = atan2(aim[1] - me.y, aim[0] - me.x)
        for _ in range(2):
            mzx = me.x + cos(a) * MUZ
            mzy = me.y + sin(a) * MUZ
            a = atan2(aim[1] - mzy, aim[0] - mzx)
        mzx = me.x + cos(a) * MUZ
        mzy = me.y + sin(a) * MUZ
        err = wrap(a - me.turret)
        step = o.bullet_turn_rate * o.dt
        cmd = clamp(err / step)
        residual = fabs(err - cmd * step)
        dd = hypot(aim[0] - mzx, aim[1] - mzy)
        tol = atan2(11.5, max(dd, 40.0)) - SPREAD
        if tol < 0.004:
            tol = 0.004
        if not me.ammo_ready or residual > tol:
            return cmd, False
        if hungry or e.cooldown < 0.3:
            tf_ok = TF_MAX + 0.5
            err_ok = ERR_FIRE + 80.0
        else:
            tf_ok = TF_MAX
            err_ok = ERR_FIRE
        if t_fly > tf_ok or err_px > err_ok:
            return cmd, False
        if self.nav is not None:
            if not self.nav.los(mzx, mzy, px, py) or not self.nav.free_px(mzx, mzy):
                return cmd, False
        self.myshots.append((o.tick + int(dd / BULLET * 60.0) + 2, mzx, mzy,
                             cos(a), sin(a), aim[0], aim[1]))
        if len(self.myshots) > 6:
            del self.myshots[0]
        self.gear_lock_until = self.t + t_fly + 0.18
        return cmd, True

    def _gun_blind(self, o, me):
        if not self.seen:
            return 0.0, False
        age = self.t - self.et
        k = 0.7 if age < 4.0 else 0.0
        px = self.ex + self.evx * age * k
        py = self.ey + self.evy * age * k
        err = o.aim_error(px, py)
        step = o.bullet_turn_rate * o.dt
        return clamp(err / step), False

    # --- движение -----------------------------------------------------------
    def _road(self, me, hull_target):
        """Можно ли ехать по курсу hull_target и вперёд, и назад."""
        nav = self.nav
        if nav is None:
            return True, True
        hx = cos(hull_target)
        hy = sin(hull_target)
        fwd = (nav.free_px(me.x + hx * 52.0, me.y + hy * 52.0) and
               nav.clear_px(me.x + hx * 44.0, me.y + hy * 44.0) > 16.0)
        rev = (nav.free_px(me.x - hx * 52.0, me.y - hy * 52.0) and
               nav.clear_px(me.x - hx * 44.0, me.y - hy * 44.0) > 16.0)
        return fwd, rev

    def _spin(self, me):
        """Свободный курс: 16 проб, берём самый просторный."""
        nav = self.nav
        best = me.hull
        best_c = -1.0
        for k in range(16):
            a = k * 0.39269908169872414
            c = nav.clear_px(me.x + cos(a) * 60.0, me.y + sin(a) * 60.0)
            if c > best_c:
                best_c = c
                best = a
        return best

    def _rand(self):
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        return self.rnd / 2147483647.0

    def _engage(self, o, me, e):
        fx, fy = self._foe_pos()
        d = hypot(fx - me.x, fy - me.y)
        t = self.t
        foe_ready = e.ammo_ready or e.cooldown < 0.30
        want = D_STAND
        dash = False
        # дожим — только когда у врага заряда нет надолго, а у нас есть
        if me.ammo_ready and e.cooldown > DASH_CD and d < DASH_MAX:
            dash = True
            want = D_DASH
        # добивание: враг на одном попадании — гоним без оглядки на дистанцию
        if e.hp <= DMG and me.ammo_ready:
            dash = True
            want = D_DASH
        elif d < D_CLOSE:
            want = D_CLOSE + 80.0
        # клин опасен: у врага готов выстрел — разрываем дистанцию
        if not dash and d < 120.0 and e.cooldown < 0.35:
            want = 190.0
        if me.hp <= FLEE_HP and foe_ready and e.hp > DMG:
            want = 430.0
        if t > self.gear_until and t > self.gear_lock_until:
            self.gear_until = t + PEND_MIN + (PEND_MAX - PEND_MIN) * self._rand()
            if d > want + 40.0:
                self.gear = 1.0
            elif d < want - 40.0:
                self.gear = -1.0
            elif self._rand() > GEAR_FLIP:
                self.gear = -self.gear
        if dash and d > want:
            self.gear = 1.0
        # ближний бой: разрываем дистанцию, пока враг не прижал
        elif d < 85.0 and not dash:
            self.gear = -1.0
        gear = self.gear
        ht, t_imp = self._hull_target(o, me)
        if ht is None:
            err = wrap(atan2(fy - me.y, fx - me.x) - me.hull)
            return clamp(err * TRACK_GAIN), gear * THROTTLE
        # сторона ракурса: если в выбранную сторону газа путь закрыт,
        # пробуем зеркальный ракурс (BAND*2 по дуге)
        ht2 = ht - 2.0 * self.side * radians(BAND)
        fwd, rev = self._road(me, ht)
        fwd2, rev2 = self._road(me, ht2)
        if gear > 0.0:
            if not fwd and fwd2:
                ht = ht2
            best_free = fwd or fwd2
        else:
            if not rev and rev2:
                ht = ht2
            best_free = rev or rev2
        err = wrap(ht - me.hull)
        turn = clamp(err * TRACK_GAIN)
        if not best_free:
            # оба направления упираются в стену — выбираемся по самому
            # свободному курсу, ракурсом жертвуем
            ht = self._spin(me)
            err = wrap(ht - me.hull)
            return clamp(err * TRACK_GAIN), 0.35
        # ГАЗ ПЛАТИТ ЗА РАКУРС: движок нормирует гусеницы по максимуму, и на
        # полном газу доворот идёт 1.3 рад/с вместо 2.6. Пока корпус отстал от
        # ракурса, режем тягу — доворот важнее хода (приём mongoose).
        align = 1.0 - fabs(err) / RACKUR_FULL
        if align < 0.0:
            align = 0.0
        throttle = THROTTLE * align
        if gear < 0.0:
            return turn, max(-0.62, gear * throttle)
        return turn, gear * throttle

    def _hunt(self, o, me):
        nav = self.nav
        t = self.t
        age = t - self.et
        goal = None
        if self.seen and age < 6.0:
            goal = (self.ex + self.evx * age * 0.5, self.ey + self.evy * age * 0.5)
        elif self.ehp <= DMG and age < 25.0:
            # добивание: идём к последнему месту, где его видели
            goal = (self.ex, self.ey)
        elif nav is not None:
            if self.hunt_goal is None or t - self.hunt_t > 7.0:
                self.hunt_t = t
                sp = nav.spawns
                foe = sp[1] if len(sp) > 1 else sp[0]
                own = sp[0]
                k = int(t / 7.0) % 3
                if k == 0:
                    goal = foe
                elif k == 1:
                    goal = own
                else:
                    goal = ((me.x * 0.35 + foe[0] * 0.65),
                            (me.y * 0.35 + foe[1] * 0.65))
                self.hunt_goal = goal
            goal = self.hunt_goal
        hull_target, _ = self._hull_target(o, me)
        if hull_target is None:
            hull_target = None
        if goal is not None:
            gx, gy = goal
            if nav is not None:
                wp = nav.route(me.x, me.y, gx, gy)
                if wp is not None:
                    gx, gy = wp
            want = atan2(gy - me.y, gx - me.x)
        else:
            want = me.hull
        # путь важнее ракурса, но нос всё равно держим в сторону врага:
        # если известна линия угрозы, идём «змейкой» — корпус в ракурсе,
        # газ вдоль него.
        if hull_target is not None:
            fwd, rev = self._road(me, hull_target)
            closing_want = cos(wrap(want - hull_target)) >= 0.0
            gear = 1.0 if closing_want else -1.0
            if gear > 0 and not fwd:
                gear = -1.0 if rev else 0.0
            elif gear < 0 and not rev:
                gear = 1.0 if fwd else 0.0
            if gear != 0.0:
                err = wrap(hull_target - me.hull)
                return clamp(err * TRACK_GAIN), gear * 0.9
        err = wrap(want - me.hull)
        turn = clamp(err * 3.2)
        fwd, rev = self._road(me, want)
        gear = 1.0 if fwd else (-1.0 if rev else 0.0)
        if gear == 0.0:
            want = self._spin(me)
            return clamp(wrap(want - me.hull) * 3.2), 0.4
        return turn, gear * (1.0 if fabs(err) < 1.5 else 0.35)

    # --- тик ----------------------------------------------------------------
    def on_tick(self, o):
        me = o.me
        t = o.time
        if t < self.t - 0.5 or o.tick <= self.last_tick:
            self.on_start({"tank": o.tank})
        self.last_tick = o.tick
        self.t = t
        self.dt = o.dt
        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)
        e = o.enemy
        if e is not None:
            self._see(o, e)
        else:
            self.prev_cd = None
        self._advance_threats(me)
        if e is not None:
            turn, drive = self._engage(o, me, e)
            self.mode = "engage"
        else:
            turn, drive = self._hunt(o, me)
            self.mode = "hunt"
        turret, fire = self._gun(o, me, e)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)


program = Brain()
