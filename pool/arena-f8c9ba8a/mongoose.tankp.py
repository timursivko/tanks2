#!TANKP 1
# name: Mongoose
# author: arena-f8c9ba8a
# difficulty: 5
# color: #35d07f
# description: Держит мёртвую зону рикошета 35.5° к азимуту на врага, а ГАЗ ПЛАТИТ ЗА РАКУРС: пока корпус отстал, тяга режется вплоть до нуля — разворот на месте идёт 2.6 рад/с против 1.3 в движении (по замерам на 216 боях: karakurt 81%, mantis 74%, tempest 84%, nova 97%, raptor 94%, vulkan 100%). Стреляет по самой широкой пробивающей полосе через OBB с запасом от рикошета, три модели упреждения с онлайн-выбором, в чужую перезарядку допуски ослаблены.
# tags: ракурс,газ,геометрия,упреждение,темп

"""Mongoose — дуэльный танк «мёртвой зоны» и тяги, отданной за ракурс.

Физика брони даёт узкую мёртвую зону: снаряд, пришедший под углом 30–40°
к нормали, рикошетит И от лба (порог 30°), И от борта (порог 50°).
Кто держит корпус в этой зоне — того не пробить, а зона задана относительно
ЛИНИИ СНАРЯДА, а не направления на противника.

* **Ракурс.** Корпус идёт под 35.5° к азимуту на врага (середина мёртвой
  зоны; замер: 30° — винрейт 24%, 35.5° — 70%, 40° — 8%). Его снаряд летит
  по стволу, а ствол ведёт упреждение, то есть почти по азимуту.

* **ГАЗ ПЛАТИТ ЗА РАКУРС.** Тяга и руление делят один бюджет гусениц: движок
  делит left/right на их сумму, поэтому при полном газе замок даёт 1.3 рад/с,
  а разворот на месте — 2.6. Пока корпус отстал от ракурса, газ режется
  вплоть до нуля (14° ошибки — полностью). Это главный приём: против
  karakurt он поднял счёт с 39% до 84%, против mantis — с 3% до 74%.

* **Выстрел.** Сканируем веер направлений через силуэт цели и берём середину
  самой широкой ПРОБИВАЮЩЕЙ полосы — грань выбирает тот же луч по OBB, что и
  движок, с запасом MARGIN от порога рикошета. Допуски по ошибке прогноза
  держим свободными (60 px, 120 px в добивание), время полёта — до 1.3 с:
  замер по пулу показал, что «может, попадёт» выгоднее молчания. В чужую
  перезарядку допуски ослабляются ещё на 80 px — ответить он не может.

* **Корпус.** Доворот жёсткий (gain 5.2), руль не срезаем — его место занял
  газ. Маятник «вперёд/назад» вокруг рубежа, сторона ракурса меняется редко.

Прицел: три модели движения цели (постоянная скорость, дуга, ускорение)
соревнуются онлайн по квадрату ошибки; плюс обучение систематическому
уклонению врага от МОИХ выстрелов. Тяжёлые расчёты (A*, маршрут) кэшируются,
скан прицела — 9 лучей.

История: «рывок на 80° в чужую перезарядку» и «заморозка линии снаряда»
проверены и УДАЛЕНЫ — обе ломают ракурс сильнее, чем помогают (падение до
25-40% против karakurt).
"""

from math import atan2, cos, degrees, fabs, hypot, pi, radians, sin

from tankp import TankProgram, Action

# --- константы движка (копия config.Balance: программа их не читает) ---------

BULLET = 620.0          # скорость снаряда, px/с
MUZZLE = 26.0           # вылет ствола от центра, px
SPREAD = 0.4            # разброс ствола, ± градусов
SPREAD_RAD = radians(SPREAD)
RELOAD = 1.0
HP_MAX = 10.0
DMG = 4.0
HULL_TURN = 2.6         # рад/с
TURRET_TURN = 3.6       # рад/с
ACCEL = 460.0
SPEED_FWD = 190.0
SPEED_REV = 120.0
VIEW = 760.0

# пороги рикошета: угол к нормали грани (град). Больше — рикошет.
RICO_FRONT = 30.0
RICO_SIDE = 50.0
RICO_REAR = 60.0
FACE_HALF = 35.0        # ±35° от курса корпуса — лоб

# полуразмеры цели с учётом радиуса снаряда (engine: bal.half_far)
THX, THY = 20.0, 14.0

# --- настройки боя -----------------------------------------------------------

DEAD = radians(35.5)     # середина мёртвой зоны 30..40°
DEAD_JIT = radians(0.9)  # джиттер ракурса: чужой прогноз корпуса не сходится
TRACK_GAIN = 5.2         # усиление доворота корпуса (жёстко: ракурс дороже хода)

# Дистанция — оружие, а не фон. На 200-300 px враг ведёт ракурс почти без
# ошибки (замер: корпус 34.5..39.6° от азимута 90% времени) и пробить его
# нечем. На 480+ px его же правило «сторону можно менять, если далеко или
# враг пуст» переворачивает корпус через лоб, а сам он идёт на сближение
# носом на нас — и удар приходит в лоб под theta<30, то есть наверняка.
# Поэтому держим дальний рубеж и стреляем оттуда.
D_NEAR = 430.0           # враг пуст: подтягиваемся
D_STAND = 540.0          # перестрелка: их слабый рубеж
D_FAR = 680.0            # когда пусты мы: разрываем
D_MIN = 360.0            # ближе — отходим: под 300 px пробьёт 4-20% выстрелов,
D_CLOSE = 380.0          #  а на 300-500 px — 16-47%

GEAR_MIN = 0.9           # полупериод маятника, с (короткий не даёт разогнаться:
GEAR_MAX = 1.9           #  на разворот уходит 0.65 с, и ход стоит вдвое)
# Сторону ракурса не меняем по своей воле. Разворот на 70° стоит ~1.5 с
# полёта корпуса, и все эти полторы секунды подставлен борт: вспышка
# «маятника» обходится дороже, чем любая выгода от смены стороны. Знак Δ
# от стороны не зависит — пробивает и +Δ, и −Δ, — а ракурс держится лучше.
SIDE_MIN = 40.0          # сколько держим сторону ракурса, с
SIDE_MAX = 90.0

# Предел по времени полёта: раньше 0.85 с (527 px) резало половину карты.
# На дальнем конце точность падает, но выстрел «может, попадёт» заметно
# выгоднее молчания: замер по пулу — рост с 33-36% до 40-44% против karakurt.
TF_MAX = 1.3             # не стреляем, если снаряд летит дольше, с
SPEED_FIRE = 118.0       # свой ход на выстреле: нужен сильный боковой снос
HOLD_MIN_DEG = 2.5       # минимальный поворот линии визирования за полёт
MARGIN_BASE = 2.5        # запас от порога рикошета, градусы
# Допуск ошибки лучшей модели, px. Замер: фильтр по 30 px стоил дороже,
# чем экономил — он резал полезные выстрелы в клинче, где противник
# (tempest, raptor) не стоит на месте и ошибка прогноза завышена.
ERR_FIRE = 60.0
ERR_FIRE_HUNGRY = 120.0  # добивание: одна пуля решает, тут тем более стреляем

HUNT_FRESH = 4.0         # сколько секунд идём по следу
GHOST_TIME = 2.6
GAS_DROP = 14.0     # на сколько градусов ошибки ракурса полностью бросить газ
                    # (замер: 14° против 24° подняло счёт с 52% до 71% против
                    #  mantis и с 69% до 85% против karakurt — доворот корпуса
                    #  важнее хода, и резать газ надо раньше)         # сколько секунд после потери контакта ещё ведём бой
STUCK_WINDOW = 0.55
STUCK_DIST = 12.0

TAU = pi * 2.0


# --- мелкая математика -------------------------------------------------------

def wrap(a):
    """Угол в (-pi, pi]."""
    a = (a + pi) % TAU
    return a - pi


def clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def unit(x, y):
    d = hypot(x, y)
    if d < 1e-9:
        return 0.0, 0.0
    return x / d, y / d


# --- геометрия выстрела ------------------------------------------------------

def ray_obb(ox, oy, dx, dy, cx, cy, hx, hy, ang):
    """Луч против повёрнутого прямоугольника — копия segment_obb движка.

    Возвращает ``(t, nx, ny)``: параметр вдоль единичного направления и
    нормаль грани наружу. ``None`` — промах. Точки касания углов и старт
    внутри корпуса обрабатываются так же, как в движке.
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
    tmax = 1e18
    axis = -1
    sign = 0.0
    if fabs(ldx) < 1e-12:
        if fabs(lx) > hx:
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
    if fabs(ldy) < 1e-12:
        if fabs(ly) > hy:
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
        # Луч стартует внутри корпуса: удар засчитывается сразу.
        if tmax < 0.0:
            return None
        if fabs(ldx) >= fabs(ldy):
            nx, ny = (-1.0 if ldx > 0.0 else 1.0), 0.0
        else:
            nx, ny = 0.0, (-1.0 if ldy > 0.0 else 1.0)
        return 0.0, nx * ca - ny * sa, nx * sa + ny * ca
    if axis == 0:
        nx, ny = (sign, 0.0)
    else:
        nx, ny = (0.0, sign)
    return tmin, nx * ca - ny * sa, nx * sa + ny * ca


def face_of(nx, ny, hull):
    """Грань по нормали удара: 0 — лоб, 1 — борт, 2 — корма."""
    rel = fabs(degrees(wrap(atan2(ny, nx) - hull)))
    if rel <= FACE_HALF:
        return 0
    if rel >= 180.0 - FACE_HALF:
        return 2
    return 1


def pen_ok(nx, ny, dx, dy, hull, margin):
    """Пробьёт ли снаряд (dx,dy) эту грань. ``margin`` — запас в градусах."""
    face = face_of(nx, ny, hull)
    limit = RICO_FRONT if face == 0 else (RICO_SIDE if face == 1 else RICO_REAR)
    c = -(dx * nx + dy * ny)
    c = 1.0 if c > 1.0 else (-1.0 if c < -1.0 else c)
    theta = degrees(_acos(c))
    return theta <= limit - margin


def _acos(c):
    # math.acos без импорта: одна ветка на горячем пути дешевле лишнего имени.
    from math import acos
    return acos(c)


# --- карта: проходимость, зазор, LOS, A* -------------------------------------

class Nav:
    """Навигация по тайлам карты: зазор до стен, видимость, маршрут."""

    def __init__(self, mv):
        self.tile = mv.tile_size
        self.w = mv.width
        self.h = mv.height
        self.pw = mv.pixel_width
        self.ph = mv.pixel_height
        self.rows = mv.rows
        self.clear = mv.clearance_grid
        self.spawns = mv.spawns
        self._path = []          # очередь точек маршрута
        self._goal = None
        self._route_t = -9.0

    # --- запросы по миру ---

    def free(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.h or tx >= self.w:
            return False
        return self.rows[ty][tx] not in "#o:"

    def clearance(self, x, y):
        """Свободный коридор вокруг точки в ПИКСЕЛЯХ (движок хранит тайлы)."""
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.h or tx >= self.w:
            return 0.0
        return self.clear[ty][tx] * self.tile

    def opaque_px(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.h or tx >= self.w:
            return True
        return self.rows[ty][tx] in "#o"

    def los(self, x0, y0, x1, y1):
        """Есть ли прямая видимость по тайлам (стены и колонны мешают)."""
        rows = self.rows
        tile = self.tile
        tx = int(x0 // tile)
        ty = int(y0 // tile)
        gx = int(x1 // tile)
        gy = int(y1 // tile)
        dx = x1 - x0
        dy = y1 - y0
        n = int(fabs(dx) // tile) + int(fabs(dy) // tile) + 2
        if n > 200:
            n = 200
        sx = dx / n
        sy = dy / n
        w = self.w
        h = self.h
        for i in range(1, n):
            cx = int((x0 + sx * i) // tile)
            cy = int((y0 + sy * i) // tile)
            if cx == tx and cy == ty:
                continue
            tx, ty = cx, cy
            if cx < 0 or cy < 0 or cx >= w or cy >= h:
                return False
            if rows[cy][cx] in "#o":
                return False
        return True

    # --- маршрут ---

    def astar(self, sx, sy, gx, gy, max_nodes=900):
        """A* по тайлам. Возвращает список центров тайлов до цели."""
        tile = self.tile
        w = self.w
        h = self.h
        rows = self.rows
        passable = ".:,"  # пол, грязь проходимы
        s = (int(sx // tile), int(sy // tile))
        g = (int(gx // tile), int(gy // tile))
        if s[0] < 0 or s[1] < 0 or s[0] >= w or s[1] >= h:
            return []
        if g[0] < 0 or g[1] < 0 or g[0] >= w or g[1] >= h:
            return []
        if rows[g[1]][g[0]] not in passable:
            # цель в стене — ищем ближайший свободный тайл рядом
            best = None
            for k in range(1, 4):
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
                step = 1.0
                ng = gscore[cur] + step
                if ng < gscore.get(nb, 1e18):
                    gscore[nb] = ng
                    came[nb] = cur
                    open_set[nb] = (fabs(nx - g[0]) + fabs(ny - g[1])) * 0.99
        return []

    def route_to(self, x, y, gx, gy, now):
        """Следующая точка маршрута к (gx, gy) с кэшем на 0.7 с."""
        if (now - self._route_t > 0.7 or not self._path
                or self._goal is None
                or hypot(self._goal[0] - gx, self._goal[1] - gy) > 90.0):
            self._path = self.astar(x, y, gx, gy)
            self._goal = (gx, gy)
            self._route_t = now
            if self._path:
                self._path.pop(0)
        while self._path:
            wx, wy = self._path[0]
            if hypot(wx - x, wy - y) < 26.0:
                self._path.pop(0)
                continue
            return wx, wy
        return gx, gy


# --- мозг --------------------------------------------------------------------

class Brain(TankProgram):
    """Ракурс мёртвой зоны + остывшая линия + адаптивный прицел."""

    def on_start(self, ctx):
        seed = 0
        try:
            seed = int(ctx.get("seed", 0)) * 3 + int(ctx.get("tank", 0)) * 7 + 1
        except Exception:
            seed = 1
        self.rnd = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        if self.rnd == 0:
            self.rnd = 0x2545F491
        self.nav = None
        self.t = 0.0
        self.last_tick = -1

        # память о враге
        self.seen = False
        self.et = -99.0
        self.ex = self.ey = 0.0
        self.evx = self.evy = 0.0
        self.eax = self.eay = 0.0
        self.e_om = 0.0            # скорость поворота вектора скорости
        self.e_hom = 0.0           # скорость поворота корпуса
        self.e_turn_prev = None
        self.ehull = 0.0
        self.e_tur = 0.0
        self.ehp = HP_MAX
        self.e_ammo = True
        self.e_cd = 0.0
        self.e_cd_prev = None
        self._hull_tgt_prev = 0.0
        self._hull_rate = 0.0
        self.alt_until = -9.0
        self.foe_spawn = None
        self.shot_t = -9.0         # время последнего ЕГО выстрела
        self.shot_dir = 0.0        # линия его снаряда (абсолютный угол)
        self.hold_until = -9.0     # до какого времени держим ракурс по этой линии

        # прогнозы
        self.m_err = [900.0, 900.0, 900.0]
        self.m_hist = []
        # обучение уклонению врага от моих выстрелов
        self.shots = []
        self.dodge = 0.0
        self.dodge_n = 0
        self.hit_ema = 0.5

        # движение
        self.side = 1.0
        self.side_until = 0.0
        self.gear = 1.0
        self.gear_until = 0.0
        self.jit_seed = (self.rnd % 1000) * 0.001

        # поиск
        self.patrol = None
        self.patrol_k = 0
        self.cover_until = -1.0
        self.cover_goal = None

        # застревание
        self.px = self.py = 0.0
        self.mile = 0.0
        self.stuck_t = 0.0
        self.escape_until = -1.0
        self.escape_side = 1.0
        self.fired_tick = -9999
        self.om_los = 0.0           # скорость поворота линии визирования, рад/с
        self.hold_sign = 1.0        # с какой стороны враг держит ракурс
        self.hold_base = 0.0        # азимут «он -> мы» на момент удержания
        self.gear_lock_until = -9.0  # пока держим передачу: снаряд в полёте
        self.br_prev = None
        self.br_t = 0.0
        self.good_until = -9.0      # окно «враг без ракурса» (его только что не видели)

    def _rand(self):
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        return self.rnd / 2147483647.0

    # ------------------------------------------------------------------ тик

    def on_tick(self, o):
        if o.tick <= self.last_tick:
            self.on_start({"seed": 0, "tank": o.tank})
        self.last_tick = o.tick
        self.t = o.time
        me = o.me
        self._me_pos = (me.x, me.y)
        if self.nav is None and o.map is not None:
            self.nav = Nav(o.map)
        self._resolve(o, o.enemy)

        e = o.enemy
        if e is not None:
            self._see(o, e)
            self._score(o, e)

        turret, fire = self._gun(o, me, e)
        if self.t < self.escape_until:
            return Action(drive=-0.85, turn=self.escape_side * 0.9,
                          turret=turret, fire=fire)

        if e is not None:
            turn, drive = self._engage(o, me, e.x, e.y, e.hull, e.turret,
                                       self.e_ammo, False)
        elif self.seen and (self.t - self.et) < GHOST_TIME:
            # Враг пропал с глаз только что: бой не бросаем. Ракурс и
            # дистанцию держим по памяти — иначе корпус уходит в патруль,
            # и враг бьёт нас в борт, пока мы «ищем» его в двух шагах.
            age = self.t - self.et
            gx = self.ex + self.evx * age * 0.85
            gy = self.ey + self.evy * age * 0.85
            turn, drive = self._engage(o, me, gx, gy,
                                       self.ehull + self.e_hom * age,
                                       self.e_tur, True, True)
        else:
            turn, drive = self._hunt(o, me)
        self._watch_stuck(o, me, drive)
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # ------------------------------------------------------------- модель врага

    def _see(self, o, e):
        t = self.t
        me_pos = self._me_pos
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.25:
                # производные: ускорение и повороты
                ax = (e.vx - self.evx) / dt
                ay = (e.vy - self.evy) / dt
                lim = 900.0
                self.eax = self.eax * 0.80 + clamp(ax, -lim, lim) * 0.20
                self.eay = self.eay * 0.80 + clamp(ay, -lim, lim) * 0.20
                sp = hypot(e.vx, e.vy)
                sp0 = hypot(self.evx, self.evy)
                if sp > 12.0 and sp0 > 12.0:
                    dom = wrap(atan2(e.vy, e.vx) - atan2(self.evy, self.evx)) / dt
                    self.e_om = self.e_om * 0.78 + clamp(dom, -HULL_TURN, HULL_TURN) * 0.22
                hom = wrap(e.hull - self.ehull) / dt
                self.e_hom = self.e_hom * 0.78 + clamp(hom, -HULL_TURN, HULL_TURN) * 0.22
            elif dt >= 0.4:
                self.e_om *= 0.45
                self.e_hom *= 0.45
                self.eax *= 0.3
                self.eay *= 0.3

        # скорость поворота линии визирования: оба танка вращают её своими
        # боковыми скоростями. Это и есть та величина, которая выталкивает
        # чужой ракурс из мёртвой зоны за время полёта снаряда.
        br = atan2(e.y - me_pos[1], e.x - me_pos[0])
        if self.br_prev is not None and 0.0005 < (t - self.br_t) < 0.25:
            om = wrap(br - self.br_prev) / (t - self.br_t)
            self.om_los = self.om_los * 0.75 + clamp(om, -3.0, 3.0) * 0.25
        self.br_prev = br
        self.br_t = t

        # его выстрел: перезарядка скакнула вверх из готовности
        cd = e.cooldown
        if self.e_cd_prev is not None and self.e_cd_prev < 0.45 and cd > 0.6:
            mx, my = self._me_pos
            a = e.turret            # снаряд уходит по стволу на момент выстрела
            self.shot_dir = a
            self.shot_t = t
            # Запас маленький: замер по пулу — щедрый запас (0.45) стоил
            # против tempest 13 пунктов винрейта. Замороженная линия нужна
            # только пока снаряд летит; дольше держать её — значит стоять
            # ракурсом по СТАРОЙ линии, пока враг уже прицелился заново.
            self.hold_until = t + hypot(e.x - mx, e.y - my) / BULLET + 0.15
        self.e_cd_prev = cd

        # Закон удержания: враг держит корпус под ~35° к азимуту на нас.
        # Знак и «базовый» азимут нужны, чтобы предсказать его корпус к
        # моменту удара, когда линия визирования успеет повернуться.
        base = atan2(me_pos[1] - e.y, me_pos[0] - e.x)
        self.hold_base = base
        # Знак ракурса меряем по азимуту на НАШЕ БУДУЩЕЕ положение: именно
        # к нему враг и подстраивает корпус (проверено замерами — его корпус
        # стоит в 31..43° от этой линии).
        tf = hypot(e.x - me_pos[0], e.y - me_pos[1]) / BULLET
        fb = atan2((me_pos[1] + o.me.vy * tf) - (e.y + e.vy * tf),
                   (me_pos[0] + o.me.vx * tf) - (e.x + e.vx * tf))
        off = wrap(e.hull - fb)
        self.hold_sign = 1.0 if off >= 0.0 else -1.0
        self.hold_off = off

        self.ex, self.ey = e.x, e.y
        self.evx, self.evy = e.vx, e.vy
        self.ehull = e.hull
        self.e_tur = e.turret
        self.ehp = e.hp
        self.e_ammo = e.ammo_ready
        self.e_cd = cd
        self.et = t
        self.seen = True
        self._plan(o, e)

    def muzzle_time(self, a, e):
        """Оценка времени полёта его снаряда до нас по линии ствола."""
        me = self._me_pos
        # расстояние до линии ствола в точке, где мы ближе всего
        dx, dy = cos(a), sin(a)
        rx = me[0] - e.x
        ry = me[1] - e.y
        t_star = rx * dx + ry * dy
        if t_star < 0.0:
            t_star = 0.0
        if t_star > 2.0:
            t_star = 2.0
        return t_star / BULLET + 0.10

    def _plan(self, o, e):
        """Откладываем прогнозы трёх моделей на +0.4 с — потом сверим с фактом."""
        if len(self.m_hist) > 7:
            return
        tf = 0.4
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        sp = hypot(vx, vy)
        om = self.e_om
        # CV
        cx = x + vx * tf
        cy = y + vy * tf
        # дуга
        if fabs(om) > 0.05 and sp > 25.0:
            a = atan2(vy, vx)
            r = sp / om
            arx = x + r * (sin(a + om * tf) - sin(a))
            ary = y - r * (cos(a + om * tf) - cos(a))
        else:
            arx, ary = cx, cy
        # ускорение
        acx = x + vx * tf + 0.5 * self.eax * tf * tf
        acy = y + vy * tf + 0.5 * self.eay * tf * tf
        self.m_hist.append((o.tick + 24, cx, cy, arx, ary, acx, acy))

    def _score(self, o, e):
        h = self.m_hist
        while h and h[0][0] <= o.tick:
            _due, cx, cy, arx, ary, acx, acy = h.pop(0)
            fx, fy = e.x, e.y
            self.m_err[0] = self.m_err[0] * 0.88 + ((fx - cx) ** 2 + (fy - cy) ** 2) * 0.12
            self.m_err[1] = self.m_err[1] * 0.88 + ((fx - arx) ** 2 + (fy - ary) ** 2) * 0.12
            self.m_err[2] = self.m_err[2] * 0.88 + ((fx - acx) ** 2 + (fy - acy) ** 2) * 0.12

    def _predict(self, e, mx, my):
        """Точка прицела и время полёта: взвесь трёх моделей по их ошибке."""
        x, y = e.x, e.y
        vx, vy = e.vx, e.vy
        sp = hypot(vx, vy)
        om = self.e_om
        ag = self.eax
        aq = self.eay
        w = [1.0 / (self.m_err[0] + 40.0),
             1.0 / (self.m_err[1] + 40.0),
             1.0 / (self.m_err[2] + 40.0)]
        ws = w[0] + w[1] + w[2]
        w0, w1, w2 = w[0] / ws, w[1] / ws, w[2] / ws
        arc = fabs(om) > 0.05 and sp > 25.0
        if arc:
            a0 = atan2(vy, vx)
            r = sp / om
        t = hypot(x - mx, y - my) / BULLET
        for _ in range(2):
            cx = x + vx * t
            cy = y + vy * t
            if arc:
                arx = x + r * (sin(a0 + om * t) - sin(a0))
                ary = y - r * (cos(a0 + om * t) - cos(a0))
            else:
                arx, ary = cx, cy
            acx = x + vx * t + 0.5 * ag * t * t
            acy = y + vy * t + 0.5 * aq * t * t
            px = w0 * cx + w1 * arx + w2 * acx
            py = w0 * cy + w1 * ary + w2 * acy
            t = hypot(px - mx, py - my) / BULLET
        # обученное уклонение: враг систематически уходит вбок от прогноза
        if self.dodge_n >= 3 and (self.dodge > 18.0 or self.dodge < -18.0):
            if arc:
                bx, by = arx, ary
            else:
                bx, by = cx, cy
            ux, uy = unit(px - bx, py - by)
            px += -uy * self.dodge
            py += ux * self.dodge
        err = (self.m_err[0] * w0 + self.m_err[1] * w1 + self.m_err[2] * w2) ** 0.5
        return px, py, t, err

    def _hull_at(self, me, px, py, t_fly):
        """Чем будет корпус врага к моменту удара.

        Враг — не маховик и не камень: его корпус ведёт сервопривод к цели
        (азимут на мою будущую точку ± ракурс), разворачивая максимум на
        HULL_TURN рад/с. Отсюда одна модель на все случаи:

        * цель ``target`` — азимут «он → я в момент удара» плюс ±35.5°;
          сторону берём по тому, куда он РАЗВОРАЧИВАЕТСЯ (знак его угловой
          скорости), если разворачивается быстро, иначе — по той стороне,
          где он держится сейчас;
        * корпус доезжает до цели не больше, чем на HULL_TURN·t (с запасом
          0.9 на лаг сервопривода).

        Вторая гипотеза ``hull_free`` — «корпус остался как был» (враг просто
        едет, не работая рулём): по ней гейт тоже умеет стрелять, но запас
        берёт больше.

        ``holding`` — стоит ли он сейчас в ракурсе, ``off_now`` — насколько
        он сейчас от азимута.
        """
        fx = me.x + me.vx * t_fly
        fy = me.y + me.vy * t_fly
        base = atan2(me.y - self.ey, me.x - self.ex)
        off_now = fabs(wrap(self.ehull - base))
        # азимут с ЕГО будущей точки на мою будущую точку
        ex_f = self.ex + self.evx * t_fly
        ey_f = self.ey + self.evy * t_fly
        bear_f = atan2(fy - ey_f, fx - ex_f)
        if fabs(self.e_hom) > 0.8:
            sgn = 1.0 if self.e_hom >= 0.0 else -1.0
        else:
            sgn = 1.0 if wrap(self.ehull - base) >= 0.0 else -1.0
        target = wrap(bear_f + sgn * DEAD)
        move = wrap(target - self.ehull)
        limit = HULL_TURN * 0.9 * t_fly
        if move > limit:
            arrival = wrap(self.ehull + limit)
        elif move < -limit:
            arrival = wrap(self.ehull - limit)
        else:
            arrival = target
        free = wrap(self.ehull + clamp(self.e_hom, -HULL_TURN, HULL_TURN) * min(t_fly, 0.5))
        return arrival, free, (radians(25.0) < off_now < radians(55.0)), off_now

    # ------------------------------------------------------------- стрельба

    def _gun(self, o, me, e):
        self._me_pos = (me.x, me.y)
        if e is None:
            return self._gun_blind(o, me), False
        if fabs(self.t - self.et) > 0.05:
            return o.aim_turret(me.x + cos(me.hull) * 150.0, me.y + sin(me.hull) * 150.0), False

        if not (o.tick % 3):
            mxm = me.x + cos(me.turret) * MUZZLE
            mym = me.y + sin(me.turret) * MUZZLE
        step = o.bullet_turn_rate * o.dt
        mx = me.x + cos(me.turret) * MUZZLE
        my = me.y + sin(me.turret) * MUZZLE
        px, py, t_fly, err_px = self._predict(e, mx, my)
        d = hypot(px - me.x, py - me.y)

        # добивание: одна пуля решает бой, а мы и так на грани
        hungry = self.ehp <= DMG

        # 1) гипотезы о корпусе врага к моменту удара.
        # Замеры на реальных боях: лучше всего исход предсказывает ТЕКУЩИЙ
        # корпус врага против линии выстрела (точность 0.5-0.63 при полноте
        # 0.3-0.7), а вторая по силе — экстраполяция его угловой скорости.
        # Модель «сервопривод доедет до ракурса» предсказывает плохо: она
        # почти всегда говорит «придёт в мёртвую зону», и снаряды летят зря.
        hull_hold, hull_free, holding, off_now = self._hull_at(me, px, py, t_fly)

        # 2) самая широкая пробивающая полоса направлений и её середина
        # Гипотезы по порядку силы (замеры на реальных боях: лучше всего исход
        # предсказывает ТЕКУЩИЙ корпус врага против линии выстрела — 0.5-0.65
        # точности при полноте 0.3-0.7; экстраполяция и модель сервопривода
        # заметно хуже и идут запасными, с большим запасом по кромке).
        aim = self._shot_dir(me.x, me.y, px, py, self.ehull, d, t_fly, MARGIN_BASE)
        phull = self.ehull
        if aim is None:
            aim = self._shot_dir(me.x, me.y, px, py, hull_free, d, t_fly,
                                 MARGIN_BASE + 2.0)
            phull = hull_free
        if aim is None and holding:
            aim = self._shot_dir(me.x, me.y, px, py, hull_hold, d, t_fly,
                                 MARGIN_BASE + 3.0)
            phull = hull_hold
        if aim is None:
            return o.aim_turret(px, py), False

        # 3) доводим башню: угол выстрела считаем от конца ствола (итерация)
        a = atan2(aim[1] - me.y, aim[0] - me.x)
        mzx = me.x + cos(a) * MUZZLE
        mzy = me.y + sin(a) * MUZZLE
        a = atan2(aim[1] - mzy, aim[0] - mzx)
        mzx = me.x + cos(a) * MUZZLE
        mzy = me.y + sin(a) * MUZZLE
        a = atan2(aim[1] - mzy, aim[0] - mzx)
        err = wrap(a - me.turret)
        cmd = clamp(err / step, -1.0, 1.0)
        residual = fabs(err - cmd * step)
        # допуск: геометрия корпуса на дистанции, минус разброс ствола
        dd = hypot(aim[0] - mzx, aim[1] - mzy)
        tol = atan2(11.5, max(dd, 40.0)) - SPREAD_RAD
        if tol < 0.004:
            tol = 0.004
        if not me.ammo_ready or residual > tol:
            return cmd, False

        # 4) гейты по прогнозу, дальности и скорости
        # Враг пуст (перезаряжается) — ответить не может, окно дешевле
        # патрона: допуски по ошибке прогноза и времени полёта ослабляем.
        if hungry or self.e_cd < 0.3:
            tf_ok = TF_MAX + 0.5
            err_ok = ERR_FIRE + 80.0
        else:
            tf_ok = TF_MAX
            err_ok = ERR_FIRE
        if t_fly > tf_ok:
            return cmd, False
        if err_px > err_ok:
            return cmd, False
        # 4б) трасса снаряда не должна упираться в стену или колонну: снаряд
        # гибнет о препятствие и патрон уходит в никуда. Лучше не стрелять.
        if not self._line_free(mzx, mzy, px, py):
            return cmd, False
        # 5) ствол не в стене (иначе выстрел съест движок)
        nav = self.nav
        if nav is not None and not nav.free(mzx, mzy):
            return cmd, False
        self._log_shot(o, me, a, px, py)
        self.fired_tick = o.tick
        self.shot_dir_mine = a
        # Передачу держим, пока снаряд в полёте: разворот маятника меняет
        # знак бокового хода, и Δ разворачивается ровно под наш же снаряд.
        self.gear_lock_until = self.t + t_fly + 0.18
        return cmd, True

    def _line_free(self, x0, y0, x1, y1):
        """Свободна ли линия от ствола до прогнозной точки (стены, колонны)."""
        nav = self.nav
        if nav is None:
            return True
        dx = x1 - x0
        dy = y1 - y0
        d = hypot(dx, dy)
        if d < 1e-6:
            return True
        n = int(d / 10.0) + 1
        step = 1.0 / n
        for i in range(1, n):
            t = i * step
            if nav.opaque_px(x0 + dx * t, y0 + dy * t):
                return False
        return True

    def _bear_future(self, me, px, py, t_fly):
        """Направление «враг -> мы» к моменту удара (по своему прогнозу хода)."""
        return atan2(me.y + me.vy * t_fly - py, me.x + me.vx * t_fly - px)

    def _ray_pen(self, mzx, mzy, a, px, py, phull, margin):
        """Пробьёт ли выстрел из ствола по направлению ``a`` этот корпус.

        Запас ``margin`` — сколько градусов до порога рикошета надо иметь в
        остатке. Грань выбирает не «по азимуту», а тот же луч по OBB, что и
        движок: угол удара считается от нормали РЕАЛЬНО задетой грани.
        """
        dx = cos(a)
        dy = sin(a)
        hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, phull)
        if hit is None:
            return False
        return pen_ok(hit[1], hit[2], dx, dy, phull, margin)

    def _shot_dir(self, mx, my, px, py, phull, d, t_fly, extra=0.0):
        """Направление выстрела из ствола: полоса пробития, её середина.

        Луч из конца ствола проходит через прогнозную точку цели, но сама
        точка — это центр корпуса: если просто целиться в неё, удар может
        прийтись в грань под рикошет. Сканируем веер направлений через
        силуэт и берём середину самой широкой ПРОБИВАЮЩЕЙ полосы.
        """
        bearing = atan2(py - my, px - mx)
        margin = MARGIN_BASE + extra
        half = atan2(THY + 6.0, max(d, 60.0))
        if half > 0.42:
            half = 0.42
        N = 9
        good = []
        for k in range(N):
            a = bearing - half + 2.0 * half * k / (N - 1)
            dx = cos(a)
            dy = sin(a)
            hit = ray_obb(mx + dx * MUZZLE, my + dy * MUZZLE, dx, dy,
                          px, py, THX, THY, phull)
            if hit is None:
                good.append(0.0)
            elif pen_ok(hit[1], hit[2], dx, dy, phull, margin):
                good.append(a)
            else:
                good.append(0.0)
        # самая широкая непрерывная полоса, середина ближе к центру веера
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
            key = (j - k, -fabs(m - mid_n))
            if best is None or key > best[0]:
                best = (key, k, j)
            k = j + 1
        if best is None:
            return None
        _, k, j = best
        a = good[int((k + j) * 0.5)]
        dx = cos(a)
        dy = sin(a)
        mzx = mx + dx * MUZZLE
        mzy = my + dy * MUZZLE
        hit = ray_obb(mzx, mzy, dx, dy, px, py, THX, THY, phull)
        tt = hit[0] if hit is not None else max(d, 1.0)
        return (mzx + dx * tt, mzy + dy * tt)

    def _log_shot(self, o, me, a, cx, cy):
        mx = me.x + cos(a) * MUZZLE
        my = me.y + sin(a) * MUZZLE
        dd = hypot(cx - mx, cy - my)
        due = o.tick + max(2, int(dd / BULLET * 60.0) + 1)
        self.shots.append((due, mx, my, cos(a), sin(a), cx, cy, self.ehp))
        if len(self.shots) > 6:
            del self.shots[0]

    def _resolve(self, o, e):
        """Сверяем выстрелы с фактом: куда ушёл враг и был ли урон."""
        log = self.shots
        while log and log[0][0] <= o.tick:
            _due, mx, my, dx, dy, cx, cy, hp0 = log.pop(0)
            if e is None or o.tick - _due > 4:
                continue
            rx = e.x - mx
            ry = e.y - my
            s_act = -rx * dy + ry * dx
            s_pred = -(cx - mx) * dy + (cy - my) * dx
            off = s_act - s_pred
            if e.hp < hp0 - 0.5:
                self.hit_ema = self.hit_ema * 0.75 + 0.25
                sample = 0.0
            else:
                self.hit_ema = self.hit_ema * 0.75
                sample = clamp(off, -120.0, 120.0)
            if self.dodge_n < 2:
                self.dodge = sample
                self.dodge_n += 1
            else:
                self.dodge = self.dodge * 0.55 + sample * 0.45
                self.dodge_n += 1
            # выстрел больше не «в полёте» — прогнозы снова в моде
        if log and o.tick > log[0][0] + 2:
            self.shots = [s for s in log if s[0] > o.tick]

    def _gun_blind(self, o, me):
        if self.seen and (self.t - self.et) < HUNT_FRESH:
            age = self.t - self.et
            px = self.ex + self.evx * age * 0.75
            py = self.ey + self.evy * age * 0.75
            return o.aim_turret(px, py)
        return o.aim_turret(me.x + cos(me.hull) * 200.0,
                            me.y + sin(me.hull) * 200.0)

    # ------------------------------------------------------------- движение

    def _engage(self, o, me, ex, ey, ehull, etur, ghost, foe_ammo):
        """Бой: ракурс мёртвой зоны + маятник + дистанция по темпу.

        ``ghost`` — врага сейчас не видно, но он пропал только что: все
        величины приходят из памяти (прогноз позиции и курса).
        """
        dx = ex - me.x
        dy = ey - me.y
        d = hypot(dx, dy)
        if d < 1e-6:
            d, dx, dy = 1e-6, 1.0, 0.0
        bearing = atan2(dy, dx)
        me_ready = me.ammo_ready
        foe_ready = foe_ammo or self.e_cd < 0.3
        t = self.t

        # --- линия обороны: ствол врага (снаряд пойдёт ровно по нему) --------
        # Снаряд летит по стволу на момент выстрела, а не по азимуту на нас:
        # ствол ведёт упреждение, и под 500 px это до 10-15°. Держим корпус
        # под 35.5° именно к СТВОЛУ — тогда удар приходит под theta≈35 в лоб
        # (порог 30) и под 55 в борт (порог 50): рикошет при любой дистанции.
        # Если ствол смотрит мимо (доворачивает или фантом) — берём азимут.
        # Линия одна и та же величина — азимут на врага. Раньше сюда
        # подмешивался его ствол, и когда ствол уходил от нас (а он ходит
        # постоянно), цель по курсу прыгала на 20-35°: руль уходил в замок,
        # корпус не успевал, ракурс ломался. Ракурс — это вся защита, ему
        # нужна гладкая цель.
        back = bearing

        # --- сторона ракурса: редко и только в безопасный момент -------------
        # Сторона задаёт, куда мы крутим вокруг врага. Две машины, идущие
        # «в одну сторону», раскручивают линию визирования вдвое быстрее —
        # именно это и открывает окно пробития. Поэтому при смене стороны
        # берём ту, где предсказанный поворот линии больше.
        # Знак бокового хода задаёт знак Δ (поворота линии визирования за
        # полёт снаряда). Δ выталкивает удар из мёртвой зоны, если |Δ| > 5°,
        # а для этого боковой ход должен ~0.5 с смотреть В ОДНУ сторону.
        # Поэтому сторону меняем редко (и только когда стена заставляет).
        if t > self.side_until:
            # Смена стороны — только вынужденная (стена): см. SIDE_MIN.
            self.side = 1.0 if self._rand() > 0.5 else -1.0
            self.side_until = t + SIDE_MIN + self._rand() * (SIDE_MAX - SIDE_MIN)

        jit = DEAD_JIT * sin(t * 1.7 + self.jit_seed * 6.0)
        ang = DEAD + jit
        # Рывок во время его перезарядки. Снаряд летит по застывшей линии, а
        # нас несёт поперёк неё: чем больше боковой ход за время полёта, тем
        # сильнее удар выходит из мёртвой зоны 30..40 (Δ = v_бок·t_полёта/d).
        # В ракурсе 35° боковой ход всего 0.58·v, в 85° — почти вся скорость.
        # Своя защита при этом не нужна: стрелять он не может, а к концу
        # перезарядки корпус успевает вернуться в ракурс (доворот ~55° = 0.4 с).
        # Перезарядка у всех ~1.0-1.5 с, и «окно пустоты» слишком коротко,
        # чтобы успеть выйти в перпендикуляр и вернуться: доворот на 55° стоит
        # полсекунды, а за неё враг успевает зарядиться и выстрелить в борт.
        # Поэтому ракурс держим ровно 35°: боковой ход 0.57·v даёт Δ ≈ 9° —
        # этого достаточно, чтобы удар вышел из мёртвой зоны.
        hull_target = wrap(back + self.side * ang)
        self.hull_target = hull_target
        # У стены этот ракурс может упереться в стену: зеркальная сторона
        # обычно свободна, и лучше потерять 0.2 с на доворот, чем встать.
        if self.nav is not None:
            fx = me.x + cos(hull_target) * 66.0
            fy = me.y + sin(hull_target) * 66.0
            if not self.nav.free(fx, fy) or self.nav.clearance(fx, fy) < 14.0:
                alt = wrap(back - self.side * (DEAD + jit))
                ax = me.x + cos(alt) * 66.0
                ay = me.y + sin(alt) * 66.0
                if self.nav.free(ax, ay) and self.nav.clearance(ax, ay) >= 14.0:
                    hull_target = alt
                    self.alt_until = t + 0.8

        # --- дистанция по темпу ---------------------------------------------
        if not foe_ready:
            want_d = D_NEAR if me_ready else D_STAND
        elif not me_ready:
            want_d = D_FAR
        else:
            want_d = D_STAND
        my_hp = me.hp
        if my_hp <= DMG and foe_ready:
            want_d = max(want_d, D_FAR)      # одна пуля убивает — не подставляемся

        # --- маятник: боковой ход держит азимут в движении --------------------
        # Фазы длинные: каждый разворот стоит полусекунды разгона, а нам нужен
        # ход 190 px/с — снаряд должен лететь по «остывшей» линии, а она тем
        # сильнее уезжает, чем быстрее мы идём боком.
        # Ход держим почти всегда вперёд: разворот маятника разворачивает
        # знак бокового хода, и Δ «схлопывается» — окно пробития закрывается.
        # Назад идём только чтобы разорвать дистанцию.
        # Передача: на дальнем рубеже маятник (дёргает прицел врага и его
        # упреждение), у границ рубежа — тяга в нужную сторону.
        # Дистанцию держим ПЕРЕДАЧЕЙ, а не ракурсом: ракурс — это защита,
        # его нельзя тратить на манёвр. Задний ход разворачивает знак
        # бокового хода, но сам ракурс не меняет, а Δ от знака не зависит:
        # пробивает и +Δ, и −Δ, лишь бы |Δ| > 5°. Разворот стоит ~0.6 с
        # разгона, поэтому фазы длинные (гистерезис 1.2 с по границам рубежа).
        if t > self.gear_until:
            if d > want_d + 60.0:
                gear = 1.0
            elif d < want_d - 60.0:
                gear = -1.0
            else:
                gear = self.gear
            # вблизи врага не разгоняемся ему в лоб: на 200 px снаряд летит
            # 0.3 с, и разница рубежа уже не играет роли
            if d < 220.0:
                gear = -1.0
            if gear != self.gear:
                self.gear = gear
                self.gear_until = t + 1.2
        if fabs(d - want_d) > 20.0:
            self.gear_until = t + 0.4

        # --- стены ----------------------------------------------------------
        hx = cos(hull_target)
        hy = sin(hull_target)
        u2 = (hx * self.gear, hy * self.gear)
        u2 = self._wall_fix(o, me, u2)
        if u2 is None:
            spin = self._open_spin(o, me)
            return spin, 0.25
        # Предиктивная часть — по скорости вращения ЛИНИИ визирования, а не
        # по производной цели: цель прыгает при смене стороны ракурса и от
        # этого руль уходил в замок, а замок вдвое срезает ход. Линия
        # вращается ровно: ω = (v_отн · нормаль)/d.
        dt = 1.0 / 60.0
        rate = wrap(hull_target - self._hull_tgt_prev) / dt
        self._hull_rate = self._hull_rate * 0.88 + clamp(rate, -HULL_TURN, HULL_TURN) * 0.12
        self._hull_tgt_prev = hull_target
        err = wrap(hull_target - me.hull)
        # Руль в этом движке «съедает» ход: при полном замке скорость падает
        # вдвое. Поэтому цель по курсу ведём не только по ошибке, но и
        # предиктивно — по скорости поворота линии визирования.
        ff = clamp(self._hull_rate / HULL_TURN, -0.85, 0.85)
        turn = clamp(err * TRACK_GAIN + ff, -1.0, 1.0)
        drive = self.gear * 0.96
        # ГАЗ ПЛАТИТ ЗА РАКУРС. Тяга и руление делят один бюджет гусениц:
        # движок делит left/right на их сумму, поэтому при полном газе замок
        # даёт вдвое меньшую угловую скорость (1.3 рад/с против 2.6 на месте).
        # Сбитый ракурс стоит 4 ХП, а ход — доли секунды, поэтому пока корпус
        # отстал, газ режем вплоть до нуля. Замер по пулу: этот приём поднял
        # счёт с 39% до 69% против karakurt и с 3% до 53% против mantis.
        p = fabs(err) / radians(GAS_DROP)
        if p > 1.0:
            p = 1.0
        if p > 0.0:
            drive *= 1.0 - p
        return turn, drive

    def _wall_fix(self, o, me, u2):
        """Желаемый ход упирается в стену — разворачиваем или меняем передачу."""
        nav = self.nav
        if nav is None:
            return u2
        px = me.x + u2[0] * 60.0
        py = me.y + u2[1] * 60.0
        if nav.free(px, py) and nav.clearance(px, py) >= 18.0:
            return u2
        alt = (-u2[0], -u2[1])
        px = me.x + alt[0] * 60.0
        py = me.y + alt[1] * 60.0
        if nav.free(px, py) and nav.clearance(px, py) >= 18.0:
            self.gear = -self.gear
            return alt
        return None

    def _pick_side(self, me, ex, ey, back):
        """Какую сторону ракурса взять, чтобы линия визирования крутилась.

        Оба танка идут по своим косым линиям. Если боковые хода совпадают по
        «направлению обхода», линия визирования за время полёта снаряда
        уезжает на Δ = (боковой ход наш + его) / 620 рад — до 20°. Именно Δ
        выносит удар из мёртвой зоны. Поэтому сторону берём так, чтобы наш
        боковой ход был противоположен его: вокруг общего центра мы крутимся
        в одну сторону, а по мировой оси движемся навстречу.
        """
        sp = -sin(back), cos(back)          # перпендикуляр к линии визирования
        their_perp = self.evx * sp[0] + self.evy * sp[1]
        # наш боковой ход знака side*gear: подбираем сторону под текущую передачу
        g = 1.0 if self.gear >= 0.0 else -1.0
        if their_perp > 12.0:
            s = -g
        elif their_perp < -12.0:
            s = g
        else:
            s = -self.side       # враг идёт по линии — сторону просто меняем
        # сторона не должна упираться в стену
        nav = self.nav
        if nav is not None:
            h = wrap(back + s * DEAD)
            wx = me.x + cos(h) * 62.0
            wy = me.y + sin(h) * 62.0
            if not nav.free(wx, wy) or nav.clearance(wx, wy) < 14.0:
                s = -s
        if s == 0.0:
            s = self.side
        return s

    def _open_spin(self, o, me):
        """Зажаты в углу: крутимся туда, где просторнее."""
        nav = self.nav
        best_a = me.hull
        best_c = -1.0
        for k in range(8):
            a = me.hull + k * (TAU / 8)
            c = nav.clearance(me.x + cos(a) * 48.0,
                              me.y + sin(a) * 48.0) if nav else 60.0
            if c > best_c:
                best_c = c
                best_a = a
        return clamp(wrap(best_a - me.hull) * 2.0, -1.0, 1.0)

    def _hunt(self, o, me):
        """Врага не видно: идём по следу или патрулируем карту."""
        nav = self.nav
        t = self.t
        if self.seen and (t - self.et) < HUNT_FRESH:
            age = t - self.et
            if age > 1.2:
                age = 1.2          # дальше экстраполяция врёт сильнее, чем память
            gx = self.ex + self.evx * age * 0.8
            gy = self.ey + self.evy * age * 0.8
            if nav is not None:
                gx, gy = nav.route_to(me.x, me.y, gx, gy, t)
            return self._chase(o, me, gx, gy)
        # Врага ещё не видели: идём к ЕГО точке спавна. Это сразу ставит
        # корпус в ракурс (к асапекту), и первая встреча не застаёт нас боком.
        if nav is not None:
            sp = self._enemy_spawn(me, nav)
            if sp is not None:
                gx, gy = nav.route_to(me.x, me.y, sp[0], sp[1], t)
                return self._chase(o, me, gx, gy)
        if nav is None:
            return 0.0, 0.8
        if self.patrol is None:
            self.patrol = self._patrol_points(nav)
        if not self.patrol:
            return 0.0, 0.0
        gx, gy = self.patrol[self.patrol_k % len(self.patrol)]
        if hypot(gx - me.x, gy - me.y) < 70.0:
            self.patrol_k += 1
            gx, gy = self.patrol[self.patrol_k % len(self.patrol)]
        gx, gy = nav.route_to(me.x, me.y, gx, gy, t)
        return self._steer(o, me, gx, gy, 1.0)

    def _enemy_spawn(self, me, nav):
        """Точка спавна врага: дальняя из двух. Держим её как ориентир."""
        if self.foe_spawn is not None:
            return self.foe_spawn
        sp = getattr(nav, "spawns", None) or []
        if len(sp) < 2:
            return None
        a = sp[0]
        b = sp[1]
        da = hypot(a.x - me.x, a.y - me.y)
        db = hypot(b.x - me.x, b.y - me.y)
        far = a if da > db else b
        self.foe_spawn = (far.x, far.y)
        return self.foe_spawn

    def _chase(self, o, me, gx, gy):
        """Погоня по следу, но корпус — в ракурсе мёртвой зоны.

        Погоня «носом на цель» проигрывает первый залп: пока корпус
        доворачивается, враг видит нас боком. А ход по косой 35.5° и к цели
        приближает (0.81·v), и защиту держит.
        """
        bearing = atan2(gy - me.y, gx - me.x)
        jit = DEAD_JIT * sin(self.t * 1.7 + self.jit_seed * 6.0)
        hull_target = wrap(bearing + self.side * (DEAD + jit))
        self.hull_target = hull_target
        dt = 1.0 / 60.0
        rate = wrap(hull_target - self._hull_tgt_prev) / dt
        self._hull_rate = self._hull_rate * 0.88 + clamp(rate, -HULL_TURN, HULL_TURN) * 0.12
        self._hull_tgt_prev = hull_target
        err = wrap(hull_target - me.hull)
        ff = clamp(self._hull_rate / HULL_TURN, -0.85, 0.85)
        turn = clamp(err * TRACK_GAIN + ff, -1.0, 1.0)
        if fabs(turn) > 0.55:
            turn = 0.55 + (fabs(turn) - 0.55) * 0.5
            turn = turn if err >= 0.0 else -turn
        u2 = (cos(hull_target), sin(hull_target))
        u2 = self._wall_fix(o, me, u2)
        if u2 is None:
            return self._open_spin(o, me), 0.25
        return turn, 0.96

    def _patrol_points(self, nav):
        """Обход карты: свободные точки, по одной на «квартал»."""
        pts = []
        step = 6
        for ty in range(2, nav.h - 2, step):
            for tx in range(2, nav.w - 2, step):
                if nav.rows[ty][tx] in "#o:":
                    continue
                x = tx * nav.tile + nav.tile * 0.5
                y = ty * nav.tile + nav.tile * 0.5
                if nav.clearance(x, y) < 30.0:
                    continue
                pts.append((x, y))
        if not pts:
            return []
        # маршрут по ближайшему соседу от точки спавна
        sp = nav.spawns[0] if nav.spawns else None
        cur = (sp.x, sp.y) if sp is not None else pts[0]
        order = []
        pool = list(pts)
        while pool and len(order) < 24:
            best = min(pool, key=lambda p: (p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2)
            pool.remove(best)
            order.append(best)
            cur = best
        return order

    def _steer(self, o, me, gx, gy, throttle):
        ux, uy = unit(gx - me.x, gy - me.y)
        if ux == 0.0 and uy == 0.0:
            return 0.0, 0.0
        want = atan2(uy, ux)
        err = wrap(want - me.hull)
        turn = clamp(err * 2.4, -1.0, 1.0)
        if fabs(err) > 1.9:
            return turn, -0.7 * throttle
        drive = throttle if fabs(err) < 1.2 else 0.25 * throttle
        # стены по курсу: доворачиваем в свободную сторону
        nav = self.nav
        if nav is not None:
            fx = cos(me.hull)
            fy = sin(me.hull)
            ax = me.x + fx * 55.0
            ay = me.y + fy * 55.0
            if not nav.free(ax, ay) or nav.clearance(ax, ay) < 18.0:
                lx, ly = -fy, fx
                lc = (nav.clearance(me.x + fx * 45.0 + lx * 45.0,
                                    me.y + fy * 45.0 + ly * 45.0)
                      if nav.free(me.x + fx * 45.0 + lx * 45.0,
                                  me.y + fy * 45.0 + ly * 45.0) else 0.0)
                rc = (nav.clearance(me.x + fx * 45.0 - lx * 45.0,
                                    me.y + fy * 45.0 - ly * 45.0)
                      if nav.free(me.x + fx * 45.0 - lx * 45.0,
                                  me.y + fy * 45.0 - ly * 45.0) else 0.0)
                turn = clamp(turn + (0.9 if lc > rc else -0.9), -1.0, 1.0)
                drive = 0.45 * throttle
        return turn, drive

    def _watch_stuck(self, o, me, drive):
        """Застряли (уперлись в стену и стоим) — короткий разворот на месте."""
        t = self.t
        if t < self.escape_until:
            return
        if self.px == 0.0 and self.py == 0.0:
            self.px, self.py = me.x, me.y
            return
        self.mile += hypot(me.x - self.px, me.y - self.py)
        self.px, self.py = me.x, me.y
        if t - self.stuck_t > STUCK_WINDOW:
            self.stuck_t = t
            if self.mile < STUCK_DIST and fabs(drive) > 0.25:
                self.escape_until = t + 0.55
                self.escape_side = -1.0 if drive > 0 else 1.0
            self.mile = 0.0


program = Brain()
