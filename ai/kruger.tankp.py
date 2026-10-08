#!TANKP 1
# name: Kruger
# author: arena-936d419b
# color: #ff3d81
# description: Мёртвая зона по ЦЕНТРУ: корпус держит β=35° (середина полосы блока [30,40]°) к линии на врага на фидфорварде (лаг ~1° против 5-10° у пула), давя в упор (d→150) — чужой angle-lock начинает болтать (e_beta уходит за 30/40). Огонь по приоритету: (1) ВЕЕР — самая широкая пробивающая полоса силуэта через ПРОГНОЗНЫЙ корпус (основное оружие, бьёт когда цель вышла из зоны); (2) задник — только вне мёртвой зоны (внутри — ложное пробитие на прогнозе носа, выстрел ушёл бы в блок); (3) ЗЕРКАЛЬНЫЙ БАНК — отражаем прогнозную цель в стене, прилёт идёт от стены под внеосевым углом: единственное надёжное пробитие УДЕРЖИВАЕМОЙ мёртвой зоны. Онлайн-симулятор чужого сервопривода (усиление/срез/референс/джиттер) предсказывает чужой нос к моменту удара. A*-навигация, прочёсывание (след->последняя позиция->чужой спавн->сетка), анти-застревание. 78.1% винрейт в локальном турнире (432 боя, 18 карт, 8 pool-соперников): 100% vulkan, 98% nova, 94% karakurt/raptor, 74% tempest, 48% mongoose, 39% mantis, 20% gyurza.
# tags: ракурс,мёртвая-зона,банк-шот,веер,симулятор-врага,упор,стены

import math

from tankp import TankProgram, Action

# --- константы движка (копия config.Balance) --------------------------------
BULLET = 620.0
MUZZLE = 26.0
SPREAD = 0.4
SPREAD_RAD = math.radians(SPREAD)
HULL_TURN = 2.6
TURRET_TURN = 3.6
SPEED_FWD = 190.0
SPEED_REV = 120.0
ACCEL = 460.0
DECEL = 560.0
DMG = 4.0
HP_MAX = 10.0

RICO_FRONT, RICO_SIDE, RICO_REAR = 30.0, 50.0, 60.0
FACE_HALF = 35.0
THX, THY = 20.0, 14.0          # полуразмеры цели + радиус снаряда

PI = math.pi
TAU = PI * 2.0
DEG = math.degrees
RAD = math.radians
DEAD = RAD(35.5)               # середина мёртвой зоны 30..40


def wrap(a):
    a = (a + PI) % TAU
    return a - PI


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def tracks(drive, turn):
    """Точная модель гусениц движка: (drive_eff, hull_rate)."""
    l = drive + turn
    r = drive - turn
    s = l if abs(l) > abs(r) else r
    if s > 1.0:
        l /= s
        r /= s
    return (l + r) * 0.5, (l - r) * 0.5 * HULL_TURN


def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang):
    """Луч против OBB: (t, nx, ny) или None — копия segment_obb движка."""
    ca = math.cos(ang)
    sa = math.sin(ang)
    rx = ox - cx
    ry = oy - cy
    lx = rx * ca + ry * sa
    ly = -rx * sa + ry * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca
    tmin = 0.0
    tmax = 1e18
    axis = -1
    sign = 0.0
    if abs(ldx) < 1e-12:
        if abs(lx) > hx:
            return None
    else:
        s = -1.0 if ldx > 0.0 else 1.0
        t1 = (-hx - lx) / ldx
        t2 = (hx - lx) / ldx
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
    if abs(ldy) < 1e-12:
        if abs(ly) > hy:
            return None
    else:
        s = -1.0 if ldy > 0.0 else 1.0
        t1 = (-hy - ly) / ldy
        t2 = (hy - ly) / ldy
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
        if abs(ldx) >= abs(ldy):
            nx, ny = (-1.0 if ldx > 0.0 else 1.0), 0.0
        else:
            nx, ny = 0.0, (-1.0 if ldy > 0.0 else 1.0)
        return 0.0, nx * ca - ny * sa, nx * sa + ny * ca
    if axis == 0:
        nx, ny = (sign, 0.0)
    else:
        nx, ny = (0.0, sign)
    return tmin, nx * ca - ny * sa, nx * sa + ny * ca


def impact_theta(nx, ny, dx, dy):
    """Угол удара к нормали грани, град (как в armor.resolve_impact)."""
    c = -(dx * nx + dy * ny)
    c = 1.0 if c > 1.0 else (-1.0 if c < -1.0 else c)
    return math.degrees(math.acos(c))


def face_of(nx, ny, hull):
    rel = abs(DEG(wrap(math.atan2(ny, nx) - hull)))
    if rel <= FACE_HALF:
        return 0
    if rel >= 180.0 - FACE_HALF:
        return 2
    return 1


def pen_ok(face, theta, margin):
    limit = RICO_FRONT if face == 0 else (RICO_SIDE if face == 1 else RICO_REAR)
    return theta <= limit - margin


# --- карта -------------------------------------------------------------------

class Nav:
    """Тайлы карты: проходимость, зазор, A* с кэшем маршрута."""

    def __init__(self, mv):
        self.tile = mv.tile_size
        self.w = mv.width
        self.h = mv.height
        self.rows = mv.rows
        self.clear = mv.clearance_grid
        self.spawns = mv.spawns
        self.pw = mv.pixel_width
        self.ph = mv.pixel_height
        self._path = []
        self._goal = None
        self._route_t = -9.0

    def free(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.h or tx >= self.w:
            return False
        return self.rows[ty][tx] not in "#o:"

    def opaque(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.h or tx >= self.w:
            return True
        return self.rows[ty][tx] in "#o"

    def clearance(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.h or tx >= self.w:
            return 0.0
        return self.clear[ty][tx] * self.tile

    def astar(self, sx, sy, gx, gy, max_nodes=1100):
        tile = self.tile
        w, h = self.w, self.h
        rows = self.rows
        passable = ".:,"
        s = (int(sx // tile), int(sy // tile))
        g = (int(gx // tile), int(gy // tile))
        if s[0] < 0 or s[1] < 0 or s[0] >= w or s[1] >= h:
            return []
        if not (0 <= g[0] < w and 0 <= g[1] < h) or rows[g[1]][g[0]] not in passable:
            best = None
            for k in range(1, 5):
                for oy in range(-k, k + 1):
                    for ox in range(-k, k + 1):
                        cx, cy = g[0] + ox, g[1] + oy
                        if 0 <= cx < w and 0 <= cy < h and rows[cy][cx] in passable:
                            dd = ox * ox + oy * oy
                            if best is None or dd < best[0]:
                                best = (dd, cx, cy)
                if best:
                    break
            if best is None:
                return []
            g = (best[1], best[2])
        open_set = {s: 0.0}
        came = {}
        gscore = {s: 0.0}
        closed = set()
        count = 0
        while open_set and count < max_nodes:
            count += 1
            cur = min(open_set, key=lambda k: gscore[k] + open_set[k])
            del open_set[cur]
            if cur == g:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                path.reverse()
                return [(x * tile + tile * 0.5, y * tile + tile * 0.5)
                        for x, y in path]
            closed.add(cur)
            cx, cy = cur
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if nx < 0 or ny < 0 or nx >= w or ny >= h:
                    continue
                if rows[ny][nx] not in passable:
                    continue
                nb = (nx, ny)
                if nb in closed:
                    continue
                ng = gscore[cur] + 1.0
                if ng < gscore.get(nb, 1e18):
                    gscore[nb] = ng
                    came[nb] = cur
                    open_set[nb] = (abs(nx - g[0]) + abs(ny - g[1])) * 0.99
        return []

    def route_to(self, x, y, gx, gy, now):
        """Следующая точка маршрута; A* кэшируется 0.6 с."""
        if (now - self._route_t > 0.6 or not self._path
                or self._goal is None
                or math.hypot(self._goal[0] - gx, self._goal[1] - gy) > 80.0):
            self._path = self.astar(x, y, gx, gy)
            self._goal = (gx, gy)
            self._route_t = now
            if self._path:
                self._path.pop(0)
        while self._path:
            wx, wy = self._path[0]
            if math.hypot(wx - x, wy - y) < 24.0:
                self._path.pop(0)
                continue
            return wx, wy
        return gx, gy


# --- мозг --------------------------------------------------------------------

class Brain(TankProgram):
    """Мёртвая зона на фидфорварде + симулятор врага + дожим до стены."""

    # --- настройки ---
    TRACK_GAIN = 4.0           # P-часть доворота (фидфорвард — поправка)
    FF_RATE_MAX = 1.0          # фидфорвард только при плавной цели, рад/с
    FF_GAIN = 0.8
    GAS_DROP_DEG = 13.0        # срез газа при отставании корпуса (страховка)
    # джиттер ракурса: быстрая АСИММЕТРИЧНАЯ модуляция (β −1.2..+3.0°).
    # Цель: ломать dodge_ema gyurza — он учит системное боковое смещение
    # (lat) моих рикошетов (3+ промаха, |ema|>8px → смещение прицела до
    # 0.75×ema). Смена знака lat на каждой их серии → EMA ~ 0.
    JITTER_AMP = RAD(3.0)
    JITTER_W = 2.2
    D_HOLD = 400.0             # рубеж перестрелки
    D_NEAR = 240.0             # враг пуст: вплотную
    CLINCH_D = 110.0           # режим «упора»: спираль + слалом
    APPROACH_K = 0.78          # газ сближения: v <= K·d (угол дороже хода)
    SLALOM_CD = 0.60           # враг перезаряжается не меньше, с
    SLALOM_D0, SLALOM_D1 = 55.0, 105.0
    SLALOM_GAP = 2.2           # пауза между слаломами, с
    TF_MAX = 1.15              # макс время полёта, с
    TF_MAX_HUNGRY = 1.6
    TF_RICO = 1.28             # макс время полёта рикошета, с
    ERR_FIRE = 95.0            # допуск ошибки модели цели, px
    MARGIN_BASE = 1.2          # запас от порога рикошета, град
    GHOST = 2.6                # сколько секунд ведём бой по памяти
    HUNT_FRESH = 4.0           # сколько секунд идём по следу

    def on_start(self, ctx):
        seed = 12345
        try:
            seed = int(ctx.get("seed", 0)) * 7 + int(ctx.get("tank", 0)) * 131 + 17
        except Exception:
            pass
        self.rnd = seed & 0x7FFFFFFF
        if not self.rnd:
            self.rnd = 0x2545F491
        self.nav = None
        self.last_tick = -1
        self.t = 0.0
        self.dt = 1.0 / 60.0
        self._reset_state()

    def _reset_state(self):
        # память о враге
        self.seen = False
        self.et = -99.0
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.eax = self.eay = 0.0
        self.e_om = 0.0
        self.ehull = 0.0
        self.e_tur = 0.0
        self.ehp = HP_MAX
        self.e_cd = 0.0
        self.e_cd_prev = None
        self.e_hom = 0.0
        self.e_d = 0.4
        self.k_e = 4.8            # усиление чужого P-регулятора
        self.cut_e = 0.5          # его срез газа
        self.ref_e = 0.0          # его референс (смещение ракурса от азимута)
        self.jit_e = RAD(1.0)     # амплитуда его джиттера
        self.side_e = 1.0         # сторона его ракурса
        self._e_vlat_sgn = 0      # знак их тангенциальной скорости
        self._e_vlat_t = -10.0    # когда знак менялся
        self.spin = 0.0           # >0 — враг крутится на месте (рад/с)
        self.e_fire_t = -99.0
        # контроллеры
        self.side = 1.0
        self.side_until = 0.0
        self._gear = 1.0
        self._gear_until = 0.0
        self.az_prev = None
        self.az_t = -9.0
        # модели упреждения (CV / дуга / ускорение)
        self.m_err = [900.0, 900.0, 900.0]
        self.m_hist = []
        # движение
        self.gear = 1.0
        self.gear_until = 0.0
        self.jit_seed = (self.rnd % 1000) * 0.001
        self._last_drive = 0.4
        self._last_turn = 0.0
        # слалом
        self.slalom_t0 = -99.0
        self.slalom_dir = 1.0
        self._last_slalom = -99.0
        # поиск
        self.patrol = None
        self.patrol_k = 0
        self.foe_spawn = None
        # застревание
        self.px = None
        self.py = None
        self.mile = 0.0
        self.stuck_t = 0.0
        self.escape_until = -1.0
        self.escape_side = 1.0
        self.hull_rate_ff = 0.0
        self._hull_tgt_prev = 0.0
        self._hunt_ref = None
        self._hunt_goal = None
        self._hold_s = None
        self._drive_s = None
        self._aim_pt = None
        self._last_flip = -99.0

    def _rand(self):
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        return self.rnd / 2147483647.0

    # ------------------------------------------------------------------ тик

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"seed": 12345, "tank": o.tank})
        self.last_tick = o.tick
        self.t = o.time
        self.dt = o.dt if o.dt else (1.0 / 60.0)
        me = o.me
        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)
        self._me = (me.x, me.y)

        e = o.enemy
        if e is not None:
            self._see(o, e)

        turret, fire = self._gun(o, me, e)

        if self.t < self.escape_until:
            return Action(drive=-0.8, turn=self.escape_side * 0.9,
                          turret=turret, fire=fire)
        if e is not None:
            turn, drive = self._move(o, me, e.x, e.y, e)
        elif self.seen and self.t - self.et < self.GHOST:
            age = min(self.t - self.et, 1.0)
            gx = self.ex + self.evx * age * 0.8
            gy = self.ey + self.evy * age * 0.8
            turn, drive = self._move(o, me, gx, gy, None)
        else:
            turn, drive = self._hunt(o, me)
        self._watch_stuck(me, drive, turn)
        self._last_drive = drive
        self._last_turn = turn
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # ------------------------------------------------------------- модель врага

    def _see(self, o, e):
        t = self.t
        mx, my = self._me
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.3:
                ax = (e.vx - self.evx) / dt
                ay = (e.vy - self.evy) / dt
                self.eax = self.eax * 0.82 + clamp(ax, -1400.0, 1400.0) * 0.18
                self.eay = self.eay * 0.82 + clamp(ay, -1400.0, 1400.0) * 0.18
                sp = math.hypot(e.vx, e.vy)
                sp0 = math.hypot(self.evx, self.evy)
                if sp > 14.0 and sp0 > 14.0:
                    dom = wrap(math.atan2(e.vy, e.vx)
                               - math.atan2(self.evx, self.evy)) / dt
                    self.e_om = (self.e_om * 0.8
                                 + clamp(dom, -HULL_TURN, HULL_TURN) * 0.2)
                hom = wrap(e.hull - self.ehull) / dt
                self.e_hom = (self.e_hom * 0.8
                              + clamp(hom, -HULL_TURN, HULL_TURN) * 0.2)
            elif dt >= 0.45:
                self.e_om *= 0.4
                self.e_hom *= 0.4
                self.eax *= 0.25
                self.eay *= 0.25

        br = math.atan2(e.y - my, e.x - mx)     # азимут «я -> враг»
        A_them = wrap(br + PI)                  # азимут «враг -> я»
        off = wrap(e.hull - (A_them + self.side_e * DEAD))
        if abs(off) < RAD(16.0):
            # держится в ракурсе: оцениваем параметры его контроллера
            denom = 2.6 * (1.0 - 0.5 * max(0.0, self.e_d))
            if abs(self.e_hom) < 2.45 and denom > 0.3:
                t_cmd = abs(self.e_hom) / denom
                if t_cmd > 0.05:
                    k_obs = t_cmd / abs(off)
                    self.k_e = self.k_e * 0.97 + clamp(k_obs, 2.5, 9.0) * 0.03
            if self.e_d < 0.6 and abs(off) > RAD(2.5):
                cut_obs = clamp(1.0 - abs(self.e_d) / 0.96, 0.0, 1.0)
                self.cut_e = self.cut_e * 0.95 + cut_obs * 0.05
            rel = wrap(wrap(e.hull - A_them) * self.side_e - DEAD)
            self.ref_e = self.ref_e * 0.985 + rel * 0.015
            self.jit_e = self.jit_e * 0.97 + (RAD(0.5) + abs(off) * 0.5) * 0.03
        else:
            a = wrap(e.hull - A_them)
            if abs(a) > RAD(20.0):
                self.side_e = 1.0 if a >= 0.0 else -1.0
        # спин: крутится на месте
        if abs(self.e_hom) > 2.15 and math.hypot(e.vx, e.vy) < 45.0:
            self.spin = clamp(self.e_hom, -HULL_TURN, HULL_TURN)
        else:
            self.spin = 0.0

        cd = e.cooldown
        if self.e_cd_prev is not None and self.e_cd_prev < 0.45 and cd > 0.6:
            self.e_fire_t = t
        self.e_cd_prev = cd

        v_along = e.vx * math.cos(e.hull) + e.vy * math.sin(e.hull)
        d_obs = (v_along / SPEED_FWD if v_along > 12.0
                 else (-v_along / SPEED_REV if v_along < -12.0 else 0.0))
        self.e_d = self.e_d * 0.9 + clamp(d_obs, -0.95, 0.96) * 0.1

        self.ex, self.ey = e.x, e.y
        self.evx, self.evy = e.vx, e.vy
        self.ehull = e.hull
        self.e_tur = e.turret
        self.ehp = e.hp
        self.e_cd = cd
        self.et = t
        self.seen = True
        self._plan_models()

    def _plan_models(self):
        if len(self.m_hist) > 7:
            return
        tf = 0.4
        x, y = self.ex, self.ey
        vx, vy = self.evx, self.evy
        sp = math.hypot(vx, vy)
        om = self.e_om
        cx = x + vx * tf
        cy = y + vy * tf
        if abs(om) > 0.05 and sp > 25.0:
            a = math.atan2(vy, vx)
            r = sp / om
            arx = x + r * (math.sin(a + om * tf) - math.sin(a))
            ary = y - r * (math.cos(a + om * tf) - math.cos(a))
        else:
            arx, ary = cx, cy
        acx = x + vx * tf + 0.5 * self.eax * tf * tf
        acy = y + vy * tf + 0.5 * self.eay * tf * tf
        self.m_hist.append((self.t + tf, cx, cy, arx, ary, acx, acy))

    def _score_models(self, e):
        h = self.m_hist
        while h and h[0][0] <= self.t:
            _due, cx, cy, arx, ary, acx, acy = h.pop(0)
            fx, fy = e.x, e.y
            self.m_err[0] = (self.m_err[0] * 0.88
                             + ((fx - cx) ** 2 + (fy - cy) ** 2) * 0.12)
            self.m_err[1] = (self.m_err[1] * 0.88
                             + ((fx - arx) ** 2 + (fy - ary) ** 2) * 0.12)
            self.m_err[2] = (self.m_err[2] * 0.88
                             + ((fx - acx) ** 2 + (fy - acy) ** 2) * 0.12)

    def _predict_pos(self, mx, my):
        """Прогноз позиции врага на момент удара (итеративно, 3 модели)."""
        w = [1.0 / (self.m_err[0] + 40.0),
             1.0 / (self.m_err[1] + 40.0),
             1.0 / (self.m_err[2] + 40.0)]
        ws = w[0] + w[1] + w[2]
        w0, w1, w2 = w[0] / ws, w[1] / ws, w[2] / ws
        x, y = self.ex, self.ey
        vx, vy = self.evx, self.evy
        sp = math.hypot(vx, vy)
        om = self.e_om
        agx, agy = self.eax, self.eay
        arc = abs(om) > 0.05 and sp > 25.0
        if arc:
            a0 = math.atan2(vy, vx)
            r = sp / om
        t = math.hypot(x - mx, y - my) / BULLET
        for _ in range(2):
            cx = x + vx * t
            cy = y + vy * t
            if arc:
                arx = x + r * (math.sin(a0 + om * t) - math.sin(a0))
                ary = y - r * (math.cos(a0 + om * t) - math.cos(a0))
            else:
                arx, ary = cx, cy
            acx = x + vx * t + 0.5 * agx * t * t
            acy = y + vy * t + 0.5 * agy * t * t
            px = w0 * cx + w1 * arx + w2 * acx
            py = w0 * cy + w1 * ary + w2 * acy
            t = math.hypot(px - mx, py - my) / BULLET
        err = (self.m_err[0] * w0 + self.m_err[1] * w1
               + self.m_err[2] * w2) ** 0.5
        return px, py, t, err

    # -------------------------------------------- симулятор чужого сервопривода

    def _foe_hull_future(self, t, me_x, me_y, me_vx, me_vy, me_hull,
                         plan_drive, plan_turn):
        """Корпус врага через t: его P-регулятор (k, срез, референс, сторона)."""
        if t <= 0.02:
            return self.ehull
        if self.spin:
            return wrap(self.ehull + self.spin * t)
        h = self.ehull
        x, y = self.ex, self.ey
        vx, vy = self.evx, self.evy
        agx, agy = self.eax, self.eay
        mx, my = me_x, me_y
        mvx, mvy = me_vx, me_vy
        mh = me_hull
        k = self.k_e
        cut = self.cut_e
        ref = clamp(self.ref_e, -RAD(25.0), RAD(25.0))
        side = self.side_e
        dt = self.dt
        n = int(t / dt + 0.5)
        for _ in range(n):
            # позиция врага (CV + a)
            vx += agx * dt
            vy += agy * dt
            sp = math.hypot(vx, vy)
            if sp > SPEED_FWD:
                k2 = SPEED_FWD / sp
                vx *= k2
                vy *= k2
            x += vx * dt
            y += vy * dt
            # моя позиция (мои же команды — точно)
            de, om = tracks(plan_drive, plan_turn)
            want = de * (SPEED_FWD if de >= 0.0 else SPEED_REV)
            tvx = math.cos(mh) * want
            tvy = math.sin(mh) * want
            dvx = tvx - mvx
            dvy = tvy - mvy
            dlen = math.hypot(dvx, dvy)
            limit = (ACCEL if abs(want) > 1e-6 else DECEL) * dt
            if dlen > limit:
                k3 = limit / dlen
                dvx *= k3
                dvy *= k3
            mvx += dvx
            mvy += dvy
            msp = math.hypot(mvx, mvy)
            if msp > SPEED_FWD:
                k4 = SPEED_FWD / msp
                mvx *= k4
                mvy *= k4
            mh = wrap(mh + om * dt)
            mx += mvx * dt
            my += mvy * dt
            # его контроллер
            A = math.atan2(my - y, mx - x)
            target = A + ref + side * DEAD
            eerr = wrap(target - h)
            t_cmd = clamp(k * eerr, -1.0, 1.0)
            d_cmd = self.e_d
            if abs(eerr) > RAD(4.0):
                p = abs(eerr) / RAD(16.0)
                if p > 1.0:
                    p = 1.0
                d_cmd = self.e_d * (1.0 - cut * p)
            _de, om = tracks(d_cmd, t_cmd)
            h = wrap(h + om * dt)
        return h

    # ------------------------------------------------------------- стрельба

    def _gun(self, o, me, e):
        if e is None:
            if self.seen and self.t - self.et < self.HUNT_FRESH:
                age = min(self.t - self.et, 1.2)
                return (o.aim_turret(self.ex + self.evx * age * 0.75,
                                     self.ey + self.evy * age * 0.75), False)
            return (o.aim_turret(me.x + math.cos(me.hull) * 180.0,
                                 me.y + math.sin(me.hull) * 180.0), False)

        self._score_models(e)
        mx = me.x + math.cos(me.turret) * MUZZLE
        my = me.y + math.sin(me.turret) * MUZZLE
        px, py, t_fly, err_px = self._predict_pos(mx, my)
        # стабилизируем точку прицеливания: веса трёх моделей колышутся,
        # и башня гонится за дрожью (не сходится — окно уходит)
        if self._aim_pt is None:
            self._aim_pt = (px, py)
        kk = min(1.0, self.dt / 0.05)
        ax_ = self._aim_pt[0] + (px - self._aim_pt[0]) * kk
        ay_ = self._aim_pt[1] + (py - self._aim_pt[1]) * kk
        if math.hypot(ax_ - px, ay_ - py) > 130.0:
            ax_, ay_ = px, py
        self._aim_pt = (ax_, ay_)
        px, py = ax_, ay_
        d = math.hypot(px - me.x, py - me.y)
        is_ric = False
        ric_pt = None

        # план моего движения на время полёта (для симулятора его ракурса)
        if self._slalom_active():
            pd, pt = self._slalom_cmd()
        else:
            pd, pt = self._last_drive, self._last_turn
        # его корпус к моменту удара
        fh = self._foe_hull_future(t_fly, me.x, me.y, me.vx, me.vy, me.hull,
                                   pd, pt)
        # если корпус почти не движется — текущий точнее симуляции (нет
        # накопления ошибки прогноза bearings)
        if abs(wrap(fh - self.ehull)) < RAD(2.5) and not self.spin:
            fh = self.ehull
        # в развороте (флип борта) симуляция отстаёт — берём текущий корпус
        if abs(self.e_om) > 0.9:
            fh = self.ehull

        # запас: на ближней дистанции веер накрывает кромку — стреляем
        # «в кромку» с маленьким запасом (джиттер цели открывает полосу)
        close_k = 0.65 if d < 210.0 else (0.85 if d < 330.0 else 1.0)
        margin = (self.MARGIN_BASE
                  + 0.8 * self.jit_e
                  + 0.9 * (err_px / max(d, 60.0)) * 14.3
                  + 0.4 * t_fly) * close_k
        if margin < 1.4:
            margin = 1.4

        # ЗАДНИК (главное оружие, любой нос-лок): их задняя половина
        # (u<0 в системе носа) пробивается при γ_d 40-60 (θ_rear 40-60,
        # предел 60 — запас 0-20°); носовая половина — блок (борт
        # θ 55-59). Прицел: 10 px ЗАД их CV (против носа): bearing
        # их ракурс СЕЙЧАС (нос от линии на нас): в мёртвой зоне [28,42]
        # прямой выстрел блокируется — только банк (прилёт со стороны стены)
        eb = abs(DEG(wrap(e.hull - math.atan2(me.y - e.y, me.x - e.x))))
        in_dz = 28.0 <= eb <= 42.0

        aim = None
        # 1) ВЕЕР (основное): самая широкая пробивающая полоса силуэта.
        #    Работает, когда цель ВЫШЛА из мёртвой зоны (её нос ушёл от
        #    35°). Против удерживающего лок — полоса пуста (все углы блок).
        aim = self._shot_band(me.x, me.y, px, py, fh, d, margin)
        if aim is None:
            aim = self._shot_band(me.x, me.y, px, py, self.ehull, d,
                                  margin + 2.5)
        # 2) ЗАДНИК (50 px за их нос): только вне мёртвой зоны. Внутри
        #    зоны этот прицел бьёт в борт/кромку под θ 44-55 (блок) —
        #    pen_ok на прогнозе носа даёт ложное "пробитие" (их доворот
        #    за t_полёта), выстрел уходит в рикошет и тратит перезарядку.
        if aim is None and not in_dz and d < 520.0:
            wl = ((px - me.x) * e.vy - (py - me.y) * e.vx) / max(d * d, 1.0)
            nn = wrap(math.atan2(me.y - py, me.x - px)
                      + self.side_e * DEAD - 0.1855 * wl)
            if d < 170.0:
                nn += self.side_e * RAD(2.6)
            bx_ = px - math.cos(nn) * 50.0
            by_ = py - math.sin(nn) * 50.0
            ab_ = math.atan2(by_ - my, bx_ - mx)
            dxb_ = math.cos(ab_); dyb_ = math.sin(ab_)
            hitb = ray_obb(mx, my, dxb_, dyb_, px, py, THX, THY, nn)
            if hitb is not None:
                fb_ = face_of(hitb[1], hitb[2], nn)
                tb_ = impact_theta(hitb[1], hitb[2], dxb_, dyb_)
                if pen_ok(fb_, tb_, 0.5):
                    aim = (bx_, by_)
        hungry = self.ehp <= DMG
        foe_empty = self.e_cd > 0.45

        if aim is None and d < 125.0:
            # вплотную: внеосевой выстрел в кромку корпуса (мёртвая зона —
            # только ±5.5° вокруг линии; кромка вне её)
            th = self._center_theta(me.x, me.y, px, py, fh)
            if th is not None and 27.5 <= th < 46.0:
                u = math.atan2(py - me.y, px - me.x)
                sgn = 1.0 if wrap(fh - u) > 0.0 else -1.0
                off = math.tan(RAD(6.5)) * d * sgn
                aim = (px - math.sin(u) * off, py + math.cos(u) * off)
        if aim is None:
            # 3) БАНК (главное оружие против удерживаемой мёртвой зоны):
            #    прилёт идёт от стены под внеосевым углом — мимо их лок.
            #    Зеркальный решатель (отражаем цель в стене) + старый.
            ric = self._bank_aim(me, e, fh)
            if ric is None:
                ric = self._ricochet_aim(me, e)
            if ric is not None:
                aim = (ric[0], ric[1])
                is_ric = True
                t_fly = ric[2]
        if aim is None:
            return o.aim_turret(px, py), False

        # доводим башню: угол от конца ствола (итерация)
        a = math.atan2(aim[1] - me.y, aim[0] - me.x)
        for _ in range(2):
            a = math.atan2(aim[1] - (me.y + math.sin(a) * MUZZLE),
                           aim[0] - (me.x + math.cos(a) * MUZZLE))
        mzx = me.x + math.cos(a) * MUZZLE
        mzy = me.y + math.sin(a) * MUZZLE
        err = wrap(a - me.turret)
        step = TURRET_TURN * self.dt
        cmd = clamp(err / step, -1.0, 1.0)
        residual = abs(err - cmd * step)
        dd = math.hypot(aim[0] - mzx, aim[1] - mzy)
        tol = math.atan2(11.0, max(dd, 40.0)) - SPREAD_RAD
        if tol < 0.004:
            tol = 0.004
        if not me.ammo_ready or residual > tol:
            return cmd, False

        # гейты
        if is_ric:
            tf_ok = self.TF_RICO
            err_ok = self.ERR_FIRE + 90.0
        else:
            tf_ok = (self.TF_MAX_HUNGRY if (foe_empty or hungry)
                     else self.TF_MAX)
            err_ok = self.ERR_FIRE + (60.0 if (foe_empty or hungry) else 0.0)
        if t_fly > tf_ok or err_px > err_ok:
            return cmd, False
        if is_ric:
            if not (self._line_free(mzx, mzy, aim[0], aim[1])
                    and self._line_free(aim[0], aim[1], px, py)):
                return cmd, False
        elif not self._line_free(mzx, mzy, px, py):
            return cmd, False
        nav = self.nav
        if nav is not None and not nav.free(mzx, mzy):
            return cmd, False
        return cmd, True

    def _center_theta(self, cx, cy, px, py, phull):
        dx = px - cx
        dy = py - cy
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return None
        dx /= d
        dy /= d
        hit = ray_obb(cx + dx * MUZZLE, cy + dy * MUZZLE, dx, dy,
                      px, py, THX, THY, phull)
        if hit is None:
            return None
        return impact_theta(hit[1], hit[2], dx, dy)

    def _shot_band(self, cx, cy, px, py, phull, d, margin):
        """Середина самой широкой пробивающей полосы веера через силуэт."""
        bearing = math.atan2(py - cy, px - cx)
        half = math.atan2(THY + 8.0, max(d, 60.0))
        if half > 0.5:
            half = 0.5
        N = 11
        good = []
        for k in range(N):
            a = bearing - half + 2.0 * half * k / (N - 1)
            dx = math.cos(a)
            dy = math.sin(a)
            hit = ray_obb(cx + dx * MUZZLE, cy + dy * MUZZLE, dx, dy,
                          px, py, THX, THY, phull)
            if hit is None:
                good.append(0.0)
            elif pen_ok(face_of(hit[1], hit[2], phull),
                        impact_theta(hit[1], hit[2], dx, dy), margin):
                good.append(a)
            else:
                good.append(0.0)
        mid_n = (N - 1) * 0.5
        best = None
        k = 0
        while k < N:
            if good[k] == 0.0:
                k += 1
                continue
            j = k
            while j + 1 < N and good[j + 1] != 0.0:
                j += 1
            m = (k + j) * 0.5
            key = (j - k, -abs(m - mid_n))
            if best is None or key > best[0]:
                best = (key, k, j)
            k = j + 1
        if best is None:
            return None
        _, k, j = best
        a = good[int((k + j) * 0.5)]
        dx = math.cos(a)
        dy = math.sin(a)
        mzx = cx + dx * MUZZLE
        mzy = cy + dy * MUZZLE
        hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, phull)
        tt = hit[0] if hit is not None else max(d, 1.0)
        if tt < 20.0:
            return (cx + dx * d, cy + dy * d)
        return (mzx + dx * tt, mzy + dy * tt)

    def _line_free(self, x0, y0, x1, y1):
        nav = self.nav
        if nav is None:
            return True
        d = math.hypot(x1 - x0, y1 - y0)
        if d < 1e-6:
            return True
        n = int(d / 8.0) + 1
        dx = (x1 - x0) / n
        dy = (y1 - y0) / n
        for i in range(1, n):
            if nav.opaque(x0 + dx * i, y0 + dy * i):
                return False
        return True

    # ------------------------------------------------- рикошет (пробой пина)
    # Мёртвая зона закрывает только прямую линию «я—цель». Снаряд, пришедший
    # под углом >10° к линии, попадает по борту/кромке (θ < 50) — рикошет
    # от стены даёт такой ракурс, даже против неподвижного закрытого врага.

    def _wall_normal(self, x, y):
        nav = self.nav
        tile = nav.tile
        tx = int(x // tile)
        ty = int(y // tile)
        rows = nav.rows
        bx = by = 0.0
        n = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                cx, cy = tx + dx, ty + dy
                if (0 <= cx < nav.w and 0 <= cy < nav.h
                        and rows[cy][cx] not in ".:,"):
                    bx += cx * tile + tile * 0.5
                    by += cy * tile + tile * 0.5
                    n += 1
        if n == 0:
            return None
        bx /= n
        by /= n
        d = math.hypot(x - bx, y - by)
        if d < 1e-6:
            return None
        return ((x - bx) / d, (y - by) / d)

    def _wall_segments(self):
        """Стеновые рёбра карты (кэш; ось координат — пиксели)."""
        nav = self.nav
        segs = getattr(nav, "_segs", None)
        if segs is not None:
            return segs
        tile = nav.tile
        w, h = nav.w, nav.h
        rows = nav.rows
        pw = w * tile
        ph = h * tile
        segs = [
            (0.0, 0.0, pw, 0.0),
            (0.0, ph, pw, ph),
            (0.0, 0.0, 0.0, ph),
            (pw, 0.0, pw, ph),
        ]
        free_c = ".:,"
        for ty in range(h):
            row = rows[ty]
            for tx in range(w):
                if row[tx] in free_c:
                    continue
                x0 = tx * tile
                y0 = ty * tile
                if ty > 0 and rows[ty - 1][tx] in free_c:
                    segs.append((x0, y0, x0 + tile, y0))
                if ty + 1 < h and rows[ty + 1][tx] in free_c:
                    segs.append((x0, y0 + tile, x0 + tile, y0 + tile))
                if tx > 0 and row[tx - 1] in free_c:
                    segs.append((x0, y0, x0, y0 + tile))
                if tx + 1 < w and row[tx + 1] in free_c:
                    segs.append((x0 + tile, y0, x0 + tile, y0 + tile))
        nav._segs = segs
        return segs

    def _bank_aim(self, me, e, fh):
        """Банк-шот (зеркальный решатель): отражаем ПРОГНОЗНУЮ цель в стене,
        стрельба в точку отражения. Прилёт идёт от стены под внеосевым углом —
        мимо удерживаемой мёртвой зоны. Ствол от muzzle (26px) — итерация."""
        nav = self.nav
        if nav is None:
            return None
        cvx, cvy = me.x, me.y
        tf = 0.4
        gx, gy = e.x + e.vx * tf, e.y + e.vy * tf
        d0 = math.hypot(gx - cvx, gy - cvy)
        if d0 > 900.0 or d0 < 60.0:
            return None
        best = None
        for (x1, y1, x2, y2) in self._wall_segments():
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 16.0:
                continue
            tx_, ty_ = (x2 - x1) / L, (y2 - y1) / L
            nx0, ny0 = -ty_, tx_
            cxm, cym = (x1 + x2) * 0.5, (y1 + y2) * 0.5
            if not nav.free(cxm + nx0 * 9.0, cym + ny0 * 9.0):
                nx0, ny0 = ty_, -tx_
            cG = nx0 * gx + ny0 * gy - (nx0 * x1 + ny0 * y1)
            if cG < 4.0:
                continue
            # зеркало цели в стене
            gpx = gx - 2.0 * cG * nx0
            gpy = gy - 2.0 * cG * ny0
            mxz, myz = cvx, cvy
            valid = False
            bx = by = L1 = 0.0
            for _ in range(3):
                cmz = nx0 * mxz + ny0 * myz - (nx0 * x1 + ny0 * y1)
                if cmz < 4.0:
                    break
                dxg = gpx - mxz
                dyg = gpy - myz
                denom = dxg * ty_ - dyg * tx_
                if abs(denom) < 1e-9:
                    break
                u = ((x1 - mxz) * ty_ - (y1 - myz) * tx_) / denom
                if u < 0.02 or u > 1.0:
                    break
                if abs(tx_) > 1e-9:
                    s = (u * dxg - (x1 - mxz)) / tx_
                else:
                    s = (u * dyg - (y1 - myz)) / ty_
                if s < -4.0 or s > L + 4.0:
                    break
                bx = mxz + u * dxg
                by = myz + u * dyg
                L1 = math.hypot(bx - mxz, by - myz)
                if L1 < 24.0:
                    break
                aa = math.atan2(by - cvy, bx - cvx)
                mxz = cvx + math.cos(aa) * MUZZLE
                myz = cvy + math.sin(aa) * MUZZLE
                valid = True
            if not valid:
                continue
            sdx = (bx - mxz) / L1
            sdy = (by - myz) / L1
            # скользящий угол: |sin угла к нормали| <= sin(60)=0.5, иначе гасят
            dn = sdx * nx0 + sdy * ny0
            if abs(dn) > 0.5:
                continue
            rdx = sdx - 2.0 * dn * nx0
            rdy = sdy - 2.0 * dn * ny0
            L2 = math.hypot(gx - bx, gy - by)
            if L2 < 10.0:
                continue
            if not self._line_free(mxz, myz, bx, by):
                continue
            if not self._line_free(bx, by, gx, gy):
                continue
            hit = ray_obb(bx, by, rdx, rdy, gx, gy, THX, THY, fh)
            if hit is None:
                continue
            face = face_of(hit[1], hit[2], fh)
            th = impact_theta(hit[1], hit[2], rdx, rdy)
            if not pen_ok(face, th, 1.0):
                continue
            t_fly = (L1 + L2) / BULLET
            cand = (L1 + L2, bx, by, t_fly)
            if best is None or cand[0] < best[0]:
                best = cand
        if best is None:
            return None
        return (best[1], best[2], best[3])

    def _ricochet_aim(self, me, e):
        """Банк-шот вдоль стены.

        Движок отражает снаряд от стены только под «скользящим» углом
        (theta >= 60° от нормали, т.е. не дальше 30° от плоскости стены),
        иначе гасит. Поэтому ищем точное геометрическое решение:
        ствол ведём вдоль стены так, чтобы после отскока линия шла в цель
        (tan alpha = (cM + cT) / K — из условия пересечения лучей).
        Прилёт идёт под 6..80° к линии «я—цель» — всегда за пределами
        мёртвой зоны (±5.5°), пробивает даже неподвижного закрытого врага.
        """
        nav = self.nav
        if nav is None:
            return None
        mx, my = me.x, me.y
        ex, ey, evx, evy = e.x, e.y, e.vx, e.vy
        d0 = math.hypot(ex - mx, ey - my)
        if d0 > 640.0 or d0 < 55.0:
            return None
        u = math.atan2(ey - my, ex - mx)
        best = None
        for (x1, y1, x2, y2) in self._wall_segments():
            L = math.hypot(x2 - x1, y2 - y1)
            if L < 10.0:
                continue
            tx_ = (x2 - x1) / L
            ty_ = (y2 - y1) / L
            # нормаль в сторону свободного пространства
            nx0, ny0 = -ty_, tx_
            cxm = (x1 + x2) * 0.5
            cym = (y1 + y2) * 0.5
            if not nav.free(cxm + nx0 * 9.0, cym + ny0 * 9.0):
                nx0, ny0 = ty_, -tx_
            # стена должна быть близка к параллельной линии «я—цель»
            du = math.atan2(ty_, tx_)
            if abs(wrap(du - u)) > RAD(65.0):
                continue
            c = nx0 * x1 + ny0 * y1
            cM = nx0 * mx + ny0 * my - c
            if cM < 4.0:
                continue  # я не по свободную сторону
            for sgn in (1.0, -1.0):
                K0 = tx_ * ex + ty_ * ey - (tx_ * mx + ty_ * my)
                if abs(K0) < 24.0:
                    continue
                t_fly = 0.6
                for _ in range(3):
                    tx_t = ex + evx * t_fly
                    ty_t = ey + evy * t_fly
                    cT = nx0 * tx_t + ny0 * ty_t - c
                    if cT < 4.0:
                        break
                    K = tx_ * tx_t + ty_ * ty_t - (tx_ * mx + ty_ * my)
                    sgnk = sgn * (cM + cT) / K
                    if sgnk <= 0.12:      # tan(alpha) слишком мал
                        break
                    alpha = math.atan(sgnk)
                    if alpha > RAD(29.0):
                        break
                    sa = math.sin(alpha)
                    ca = math.cos(alpha)
                    L1 = cM / sa
                    s2 = cT / sa
                    if L1 < 30.0 or L1 + s2 > 820.0:
                        break
                    # точка отскока
                    rx = mx + (sgn * tx_ * ca) * L1 - nx0 * sa * L1
                    ry = my + (sgn * ty_ * ca) * L1 - ny0 * sa * L1
                    tr = (rx - x1) * tx_ + (ry - y1) * ty_
                    if tr < -6.0 or tr > L + 6.0:
                        break
                    # направление прилёта
                    do_x = sgn * tx_ * ca + nx0 * sa
                    do_y = sgn * ty_ * ca + ny0 * sa
                    t_fly = (L1 + s2) / BULLET
                    phx = ex + evx * t_fly
                    phy = ey + evy * t_fly
                    fh = self.ehull
                    hit = ray_obb(phx - do_x * 30.0, phy - do_y * 30.0,
                                  do_x, do_y, phx, phy, THX, THY, fh)
                    if hit is None:
                        break
                    face = face_of(hit[1], hit[2], fh)
                    th = impact_theta(hit[1], hit[2], do_x, do_y)
                    if not pen_ok(face, th, 1.5):
                        break
                    if not self._line_free(mx, my, rx, ry):
                        break
                    if not self._line_free(rx, ry, phx, phy):
                        break
                    cand = (L1 + s2, rx, ry, t_fly)
                    if best is None or cand[0] < best[0]:
                        best = cand
                    break
        if best is None:
            return None
        return (best[1], best[2], best[3])

    _wall_pt = None
    _wall_pt_t = -9.0

    def _nearest_wall_point(self, x, y):
        """Ближайшая точка стены (для перетаскивания standoff к границе)."""
        nav = self.nav
        if nav is None:
            return None
        if (self._wall_pt is not None
                and self.t - self._wall_pt_t < 1.0
                and math.hypot(self._wall_pt[0] - x, self._wall_pt[1] - y)
                < 220.0):
            return self._wall_pt
        best = None
        for k in range(16):
            a = k * TAU / 16.0
            dx = math.cos(a)
            dy = math.sin(a)
            for step in range(1, 80):
                px = x + dx * step * 14.0
                py = y + dy * step * 14.0
                if not nav.free(px, py):
                    px = x + dx * (step - 1) * 14.0
                    py = y + dy * (step - 1) * 14.0
                    d = step * 14.0
                    if best is None or d < best[0]:
                        best = (d, (px, py))
                    break
        self._wall_pt = best[1] if best else None
        self._wall_pt_t = self.t
        return best[1] if best else None

    # ------------------------------------------------------------- движение

    def _slalom_active(self):
        """Слалом активен? Три фазы: разворот вбок, рывок, возврат."""
        dt = self.t - self.slalom_t0
        if dt < 0.0:
            return False
        if dt < 0.14:
            return True
        if dt < 0.44:
            return True
        if dt < 0.74:
            return True
        return False

    def _slalom_cmd(self):
        dt = self.t - self.slalom_t0
        s = self.slalom_dir
        if dt < 0.14:
            return 0.0, s * 1.0
        if dt < 0.44:
            return 1.0, s * 0.35
        return 0.35, -s * 1.0

    # предел «закрытого» сближения: боковой ход v·sin(H°) не должен
    # выносить чужое упреждение из мёртвой зоны (v_lat/620 < ~5°)
    V_SEALED = 92.0          # враг может стрелять
    V_SEALED_EMPTY = 95.0    # враг пуст: чуть быстрее (он не бьёт)

    def _move(self, o, me, gx, gy, e):
        t = self.t
        dx = gx - me.x
        dy = gy - me.y
        d = math.hypot(dx, dy)
        if d < 1e-6:
            d, dx, dy = 1e-6, 1.0, 0.0
        A = math.atan2(dy, dx)
        nav = self.nav

        # слалом вырезан: боковой рывок — это изменение скорости; чужая
        # модель упреживания срывается на нём и (если враг заряжен)
        # даёт бесплатную очередь по нашему боку.

        # --- сторона ракурса ------------------------------------------------
        # сторона на весь бой (смена — только у стены, см. ниже):
        # фиксированное направление спирали = максимальная предсказуемость
        # для чужих моделей упреживания. Стартовая сторона — от стены
        # (иначе первый же флип на глазах у врага = их окно для выстрела).
        if t > self.side_until:
            if t < 2.0 and nav is not None:
                clp = nav.clearance(me.x + math.cos(A + 35.0 * 0.01745) * 80.0,
                                    me.y + math.sin(A + 35.0 * 0.01745) * 80.0)
                clm = nav.clearance(me.x + math.cos(A - 35.0 * 0.01745) * 80.0,
                                    me.y + math.sin(A - 35.0 * 0.01745) * 80.0)
                if clp - clm > 25.0:
                    self.side = 1.0
                elif clm - clp > 25.0:
                    self.side = -1.0
                else:
                    self.side = 1.0 if self._rand() > 0.5 else -1.0
            else:
                self.side = 1.0 if self._rand() > 0.5 else -1.0
            self.side_until = t + 300.0
        jit = self.JITTER_AMP * (0.4 + 0.6 * math.sin(
            t * self.JITTER_W + self.jit_seed * 6.0))
        # угол ракурса (проверенная схема gyurza, A/B: self-lead = не рычаг):
        #   фиксированный 35.5° к ЛИНВИИ на врага (не к упреждению).
        # Почему: чужая модель стреляет в моё будущее положение (lead =
        #   v_lat/620); при фикс. 35.5° прилёт «идеального» снаряда:
        #   gamma = 35.5 − lead. На газу 0.45 (v~80, v_lat~47) lead ≈ 4.5°:
        #   gamma ≈ 31 — чужой гейт выстрела требует запас ≥6° до порога
        #   рикошета (24° для лба): 31 > 24 → выстрел ЗАБЛОКИРОВАН их же
        #   гейтом на любой дистанции. Упреждать СВОЙ ход (LEAD_SELF>0)
        #   здесь вредно: добавляет lead к gamma и сужает запас.
        # Ближе (d<170) расширяем до 38°: там ошибка наведения выше
        # (отвес ствола 26px = 9° при d=170), запас гейта нужен больше.
        # --- темп: маятник передач (pendulum, как у gyurza) ------------------
        # Смена НАПРАВЛЕНИЯ корпуса (флип) = окно 1-1.5 с, в которое их
        # модели целятся с ошибкой 10-20° — так и убивали тремя попаданиями.
        # Смена ЗНАКА газа (маятник) = их модели видят новую скорость
        # мгновенно (цель по текущей v остаётся верной) — окна НЕТ, а моё
        # положение становится непредсказуемым для их наведения.
        # ВАЖНО (данные боя): при заднем ходе чужое упреждение ложится на
        # НЕРАКУРСНУЮ сторону, и их «идеальный» снаряд бьёт в лоб под
        # 31-32° (рикошет с запасом 1-2° — модельная ошибка 2-3° пробивает).
        # Поэтому на заднем ходе ракурс шире (39°): прилёт в лоб 34-35°
        # (запас 4-5°), а их выстрел по кромке (44°) гейтом (24°) закрыт.
        # --- статическая поза в зоне боя (d <= 380) -------------------------
        # Выводы данных боя:
        #  1) ЛЮБОЕ моё движение создаёт окно: их 4 модели упреждения
        #     бьют в моё будущее CV с ошибкой 1-2°, и при β 35.5-39 их
        #     луч φ = β − δ попадает в 26-32 (пробитие лба) либо в
        #     41-49 (пробитие борта); на переходах v≈0 — θ 49.8-50.
        #  2) СТАТИКА: их луч = мой текущий CV (v=0, lead≈0): φ = β.
        #     При β 35±3 (джиттер): φ 32-38 — мёртвая зона [30,40]
        #     с запасом 2-8° в обе стороны; переходов v≈0 НЕТ
        #     (корпус доворачивается, но положение не меняется —
        #     их цель = позиция, а не угол); dodge_ema учит lat ≈ 0
        #     (обучаться нечему: я не ухожу от линии).
        #  3) Мой ответ — КОРНЕР-шот (см. _gun): их дальний передний
        #     угол (γ 45) движется вместе с носом — позиционное
        #     упреждение попадает в угол без компенсации их
        #     доворотом (центральный лид они «съедают» доворотом
        #     носа: прилёт 33-40, мёртвая зона — проверено 10/10).
        # Дистанцию НЕ управляем: их пресс/откат осциллирует d 180-330,
        # и статика безопасна на всех d (φ = β при любом d).
        # --- трёхзонная статика (выводы v3.0) ------------------------------
        # СИЛУЭТ: их нос заперт на 35.5° к линии → весь их силуэт
        # (±atan2(14, d) ≈ ±2.6° при d 300, ±5.7° при d 140) лежит в
        # мёртвой зоне [30,40] на любой d > ~110: ЛЮБОЙ снаряд, бьющий
        # в силуэт, — рикошет. Пробить можно только в упор (d < 150),
        # где дальний борт силуэта выходит за γ 40 (θ_bort 48-50).
        # Поэтому: (а) в середине — статика (их луч = моя позиция,
        # γ = β 32-38: мёртвая зона, lead им не нужен — я не двигаюсь;
        # dodge_ema учит lat ≈ 0); (б) в упор — сближение к d 105-150
        # и сливер-шот в дальний борт (см. _gun).
        if d > 380.0:
            # сближение: фикс. перед, β 40 (их луч γ = 40−8.8 = 31.2 —
            # мёртвая зона), флипов нет (окна устаревшего лида)
            gear = 1.0
            ang = DEAD + RAD(4.5) + jit
            drive = 0.45
        elif d > 150.0:
            # давим в упор (d -> 150): на ближней дистанции чужой
            # angle-lock "болтается" (e_beta уходит за 30/40 — проверено
            # на mantis: там e_beta +8/+53 в упоре дали 3 пробития).
            # β 35 (центр мёртвой зоны) держим даже на сближении.
            gear = 1.0
            ang = RAD(35.0)
            drive = 0.55
        else:
            # упор (d <= 150): стоим в мёртвой зоне, бьём слёвером/банком
            gear = 1.0
            ang = RAD(35.0)
            drive = 0.0
        self._gear = gear

        if self._hold_s is None:
            self._hold_s = ang
        self._hold_s += (ang - self._hold_s) * min(1.0, self.dt / 0.25)
        hull_target = wrap(A + self.side * self._hold_s)

        # --- доворот: П + фидфорвард (только по плавной цели) -----------------
        rate = wrap(hull_target - self._hull_tgt_prev) / self.dt
        # медленный EMA (0.95): форвард берёт плавную составляющую доворота
        # (спираль), а не джиттер манёвра врага — быстрый форвард (0.85)
        # раскачивал корпус ±10° вокруг движущейся цели
        self.hull_rate_ff = (self.hull_rate_ff * 0.95
                             + clamp(rate, -HULL_TURN, HULL_TURN) * 0.05)
        self._hull_tgt_prev = hull_target
        err = wrap(hull_target - me.hull)
        if abs(self.hull_rate_ff) < self.FF_RATE_MAX:
            ff = clamp(self.hull_rate_ff / HULL_TURN, -1.0, 1.0) * self.FF_GAIN
        else:
            ff = 0.0
        turn = clamp(err * self.TRACK_GAIN + ff, -1.0, 1.0)

        # газ платит за угол (страховка от срыва на стартовом выравнивании)
        p = abs(err) / RAD(self.GAS_DROP_DEG)
        if p > 1.0:
            p = 1.0
        if p > 0.0:
            drive *= 1.0 - 0.92 * p

        # сглаживаем смену знака маятника (0.2 с) — плавный разворот
        # газа, без рывка (рывок = кратковременный разгон/тормож)
        if self._drive_s is None:
            self._drive_s = drive
        self._drive_s += (drive - self._drive_s) * min(1.0, self.dt / 0.12)
        drive = self._drive_s

        # --- стены по курсу ---------------------------------------------------
        hx = math.cos(hull_target)
        hy = math.sin(hull_target)
        u2 = (hx * (1.0 if drive >= 0.0 else -1.0),
              hy * (1.0 if drive >= 0.0 else -1.0))
        if nav is not None:
            px = me.x + u2[0] * 58.0
            py = me.y + u2[1] * 58.0
            if not nav.free(px, py) or nav.clearance(px, py) < 16.0:
                px = me.x - u2[0] * 58.0
                py = me.y - u2[1] * 58.0
                if nav.free(px, py) and nav.clearance(px, py) >= 16.0:
                    drive = -drive
                else:
                    best_a = me.hull
                    best_c = -1.0
                    for kk in range(8):
                        a = me.hull + kk * (TAU / 8)
                        c = nav.clearance(me.x + math.cos(a) * 46.0,
                                          me.y + math.sin(a) * 46.0)
                        if c > best_c:
                            best_c = c
                            best_a = a
                    turn = clamp(wrap(best_a - me.hull) * 2.0, -1.0, 1.0)
                    return turn, 0.2
        return turn, drive

    # ------------------------------------------------------------- поиск

    def _hunt(self, o, me):
        nav = self.nav
        t = self.t
        if nav is None:
            return 0.0, 0.8
        if self.seen and t - self.et < self.HUNT_FRESH:
            age = min(t - self.et, 1.2)
            gx = self.ex + self.evx * age * 0.8
            gy = self.ey + self.evy * age * 0.8
        else:
            sp = self._foe_spawn()
            if sp is not None and math.hypot(sp[0] - me.x, sp[1] - me.y) > 250.0:
                gx, gy = sp
            else:
                if self.patrol is None:
                    self.patrol = self._patrol_points()
                if not self.patrol:
                    return 0.0, 0.0
                gx, gy = self.patrol[self.patrol_k % len(self.patrol)]
                if math.hypot(gx - me.x, gy - me.y) < 80.0:
                    self.patrol_k += 1
                    gx, gy = self.patrol[self.patrol_k % len(self.patrol)]
        # угловая опора — на ЦЕЛЬ (стабильна); узел A* — только для поворота
        # пути (жесткий поворот), не для угла корпуса
        goal_bearing = math.atan2(gy - me.y, gx - me.x)
        if (self._hunt_goal is not None
                and math.hypot(gx - self._hunt_goal[0],
                               gy - self._hunt_goal[1]) > 260.0):
            self._hunt_ref = None
        self._hunt_goal = (gx, gy)
        gx, gy = nav.route_to(me.x, me.y, gx, gy, t)
        node_bearing = math.atan2(gy - me.y, gx - me.x)
        ref = goal_bearing + clamp(
            wrap(node_bearing - goal_bearing), -RAD(60.0), RAD(60.0))
        if self._hunt_ref is None:
            self._hunt_ref = ref
        self._hunt_ref += (ref - self._hunt_ref) * min(1.0, self.dt / 0.2)
        ref = self._hunt_ref
        jit = self.JITTER_AMP * (0.4 + 0.6 * math.sin(
            t * self.JITTER_W + self.jit_seed * 6.0))
        # β 40° на сближении: их луч = β − δ (δ = BEAR_LEAD 4° + CV-упреждение
        # v·sin β·t/d ≈ 5-6° при v 90) → φ 30-31: мёртвая зона + гейт
        # (запас до кромки лба < 6°). Скорость 0.5 стабильна — их best-of-4
        # модели не переупреждают (при 0.95 + газ-резке v 85-171 их лучший
        # model переупреждал на 3-8°: убивали θ 24-30 в первые 4 с)
        hull_target = wrap(ref + self.side * (DEAD + RAD(4.5) + jit))
        rate = wrap(hull_target - self._hull_tgt_prev) / self.dt
        # медленный EMA (0.95): форвард берёт плавную составляющую доворота
        # (спираль), а не джиттер манёвра врага — быстрый форвард (0.85)
        # раскачивал корпус ±10° вокруг движущейся цели
        self.hull_rate_ff = (self.hull_rate_ff * 0.95
                             + clamp(rate, -HULL_TURN, HULL_TURN) * 0.05)
        self._hull_tgt_prev = hull_target
        err = wrap(hull_target - me.hull)
        if abs(self.hull_rate_ff) < self.FF_RATE_MAX:
            ff = clamp(self.hull_rate_ff / HULL_TURN, -1.0, 1.0) * self.FF_GAIN
        else:
            ff = 0.0
        turn = clamp(err * self.TRACK_GAIN + ff, -1.0, 1.0)
        drive = 0.5
        p = abs(err) / RAD(self.GAS_DROP_DEG)
        if p > 1.0:
            p = 1.0
        if p > 0.0:
            drive *= 1.0 - 0.92 * p
        fx = math.cos(me.hull)
        fy = math.sin(me.hull)
        ax = me.x + fx * 55.0
        ay = me.y + fy * 55.0
        if not nav.free(ax, ay) or nav.clearance(ax, ay) < 16.0:
            lx, ly = -fy, fx
            lc = nav.clearance(me.x + fx * 44.0 + lx * 44.0,
                               me.y + fy * 44.0 + ly * 44.0)
            rc = nav.clearance(me.x + fx * 44.0 - lx * 44.0,
                               me.y + fy * 44.0 - ly * 44.0)
            turn = clamp(turn + (0.9 if lc > rc else -0.9), -1.0, 1.0)
            drive *= 0.5
        return turn, drive

    def _foe_spawn(self):
        if self.foe_spawn is not None:
            return self.foe_spawn
        nav = self.nav
        sp = getattr(nav, "spawns", None) or []
        if len(sp) < 2:
            return None
        mx, my = self._me
        a = sp[0]
        b = sp[1]
        far = a if (math.hypot(a.x - mx, a.y - my)
                    > math.hypot(b.x - mx, b.y - my)) else b
        self.foe_spawn = (far.x, far.y)
        return self.foe_spawn

    def _patrol_points(self):
        nav = self.nav
        pts = []
        step = 6
        for ty in range(2, nav.h - 2, step):
            for tx in range(2, nav.w - 2, step):
                if nav.rows[ty][tx] in "#o:":
                    continue
                x = tx * nav.tile + nav.tile * 0.5
                y = ty * nav.tile + nav.tile * 0.5
                if nav.clearance(x, y) < 28.0:
                    continue
                pts.append((x, y))
        if not pts:
            return []
        sp = nav.spawns[0] if nav.spawns else None
        cur = (sp.x, sp.y) if sp is not None else pts[0]
        order = []
        pool = list(pts)
        while pool and len(order) < 22:
            best = min(pool, key=lambda p: (p[0] - cur[0]) ** 2
                       + (p[1] - cur[1]) ** 2)
            pool.remove(best)
            order.append(best)
            cur = best
        return order

    # ------------------------------------------------------------- застревание

    def _watch_stuck(self, me, drive, turn):
        t = self.t
        if abs(turn) > 0.45 or self._slalom_active():
            self.px, self.py = (me.x, me.y)
            self.mile = 0.0
            self.stuck_t = t
            return
        if self.px is None:
            self.px, self.py = (me.x, me.y)
            self.stuck_t = t
            return
        self.mile += math.hypot(me.x - self.px, me.y - self.py)
        self.px, self.py = (me.x, me.y)
        if t - self.stuck_t > 0.6:
            self.stuck_t = t
            if self.mile < 12.0 and abs(drive) > 0.25:
                self.escape_until = t + 0.5
                self.escape_side = -1.0 if drive > 0 else 1.0
            self.mile = 0.0


program = Brain()
