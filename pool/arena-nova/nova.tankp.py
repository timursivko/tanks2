#!TANKP 1
# name: Нова
# author: arena-nova
# difficulty: 5
# color: #38e8b0
# description: Дуэлянт: считает траекторию снаряда по броне врага, стреляет только навылет, кружит и уклоняется.
# tags: дуэль,точность,манёвр,броня

"""Нова — танк-дуэлянт.

Замысел: танк видит карту целиком и знает геометрию брони, поэтому исход
дуэли решает не «увидел — выстрелил», а математика:

* наводка считается до угла, при котором ствол после доворота этого тика
  смотрит точно в выбранную точку (движок поворачивает башню до выстрела);
* точка выбирается так, чтобы удар пришёлся навылет: перебираются
  направления через коробку врага и берётся то, где угол к нормали грани
  меньше порога рикошета (лоб 30°, борт 50°, корма 60°);
* огонь открывается только тогда, когда разброс ствола ±0.4° не выносит
  снаряд за габарит цели, а стена на пути не ждёт раньше брони;
* корпус живёт отдельно от башни: он кружит вокруг врага и меняет
  направление рывками, чтобы упреждение противника не сходилось.

Ничего, кроме SDK, программа не использует: карта приходит в ``o.map``,
геометрия — в ``o.enemy``.
"""

from math import acos, atan2, cos, degrees, hypot, pi, radians, sin

from tankp import Action, TankProgram, clamp, wrap

TAU = pi * 2.0
PIO180 = pi / 180.0
#: Размеры коробки, по которой снаряд бьёт в корпус: полуразмеры корпуса
#: плюс радиус снаряда (совпадает с Balance.half_far движка).
BHX = 20.0
BHY = 14.0
#: Порог «лоб» по нормали (Balance.front_half_angle) и пороги рикошета
#: по грани (Balance.ricochet_angles), градусы к нормали грани.
FRONT_HALF = 35.0
RICO_FRONT = 30.0
RICO_SIDE = 50.0
RICO_REAR = 60.0
#: Разброс ствола, градусы (Balance.spread).
SPREAD_DEG = 0.4
SPREAD = radians(SPREAD_DEG)

#: Дистанция боя: ближе — отходим, дальше — сближаемся по дуге.
R_MIN = 165.0
R_MAX = 340.0
#: Полный бак: половина скорости теряется на разворотах, но с места
#: танк разгоняется за 0.4 с, так что газ держим открытым.
SPEED = 1.0

# Ракурс в бою: 90° — чистое кружение (движение по касательной), меньше —
# движение с доворотом в сторону врага (корпус доворачивается к снаряду).
ANGLE_DEG = 90.0

# Запас по углу к нормали брони (градусы): ниже порога рикошета на эту
# величину кандидат считается надёжным. Иначе скользящее попадание.
AIM_MARGIN = 8.0

# Порог вероятности попадания для выстрела.
FIRE_P = 0.6

# Запас по углу к нормали, при котором выстрел считается совсем скользким.
MARGIN_HARD = 12.0

# Запас, на который нормируется риск рикошета при решении о выстреле.
MARGIN_SAFE = 10.0

# Дистанция «в упор»: ствол почти в корпусе, доворачиваем и жмём.
POINT_BLANK = 62.0

# Урон одного пробития (для добивания).
DAMAGE = 4.0

# Вес второго члена упреждения (ускорение цели).
ACC_W = 0.0
ACC_LIM = 620.0
ACC_EMA = 0.65


def _ray_obb(px, py, dx, dy, cx, cy, hx, hy, ang):
    """Пересечение луча с повёрнутым прямоугольником (slab-тест).

    Возвращает ``(t, nx, ny)``: расстояние до входа и внешнюю нормаль грани,
    через которую луч вошёл. ``None`` — мимо. Луч единичный, ``t`` в пикселях.
    """
    ca = cos(ang)
    sa = sin(ang)
    ox = px - cx
    oy = py - cy
    # Локальные координаты: +x — вдоль курса корпуса.
    lx = ox * ca + oy * sa
    ly = -ox * sa + oy * ca
    ldx = dx * ca + dy * sa
    ldy = -dx * sa + dy * ca
    tmin = -1.0e18
    tmax = 1.0e18
    enx = 0.0
    eny = 0.0
    if ldx > 1e-12 or ldx < -1e-12:
        inv = 1.0 / ldx
        t1 = (-hx - lx) * inv
        t2 = (hx - lx) * inv
        n1 = -1.0
        if t1 > t2:
            t1, t2 = t2, t1
            n1 = 1.0
        if t1 > tmin:
            tmin = t1
            enx, eny = n1, 0.0
        if t2 < tmax:
            tmax = t2
    elif lx < -hx or lx > hx:
        return None
    if ldy > 1e-12 or ldy < -1e-12:
        inv = 1.0 / ldy
        t1 = (-hy - ly) * inv
        t2 = (hy - ly) * inv
        n1 = -1.0
        if t1 > t2:
            t1, t2 = t2, t1
            n1 = 1.0
        if t1 > tmin:
            tmin = t1
            enx, eny = 0.0, n1
        if t2 < tmax:
            tmax = t2
    elif ly < -hy or ly > hy:
        return None
    if tmax < tmin or tmax < 0.0:
        return None
    if tmin < 0.0:
        # Луч родился внутри коробки: считаем попаданием в упор.
        tmin = 0.0
    # Нормаль из локальных осей в мировые.
    return tmin, enx * ca - eny * sa, enx * sa + eny * ca


def _face_and_theta(nx, ny, dx, dy, hull):
    """Грань, по которой бьёт снаряд, и угол к её нормали (градусы).

    Копия ``engine.armor``: грань — по направлению нормали в системе корпуса,
    ``theta`` — угол между направлением снаряда и нормалью (0 — в упор,
    90 — скользящий удар).
    """
    rel = degrees(wrap(atan2(ny, nx) - hull))
    if rel < 0.0:
        rel = -rel
    if rel > 180.0:
        rel = 360.0 - rel
    if rel <= FRONT_HALF:
        limit = RICO_FRONT
    elif rel >= 180.0 - FRONT_HALF:
        limit = RICO_REAR
    else:
        limit = RICO_SIDE
    # cos(theta) = -dot(d, n): нормаль наружу, снаряд летит "в" грань.
    c = -(dx * nx + dy * ny)
    if c < 0.0:
        c = 0.0
    elif c > 1.0:
        c = 1.0
    theta = degrees(acos(c))
    return limit, theta


class Brain(TankProgram):
    """Дуэлянт с точной наводкой и круговым манёвром."""

    # --- запуск ---------------------------------------------------------

    def on_start(self, ctx):
        # Карта придёт с первым наблюдением: ctx её не содержит.
        self.ready = False
        self.tile = 32
        self.mask = None          # непрозрачные тайлы с рамкой (для лучей)
        self.W = 0
        self.H = 0
        self.cw = 0               # размер грубой сетки для поиска пути
        self.ch = 0
        self.cell = 1             # тайлов в клетке грубой сетки
        self.blocked = None       # грубая сетка: 1 — клетка непроезжая
        self.flow = None          # расстояние BFS до цели (в клетках)
        self.flow_goal = None
        self.path = []            # точки маршрута (мировые пиксели)
        self.path_i = 0
        self.spawns = [(0.0, 0.0), (0.0, 0.0)]
        self.seen_at = None       # когда в последний раз видели клетку
        self.seen_cell = 6        # тайлов в клетке обзора
        self.sw = 0
        self.sh = 0
        self.map_rows = []

        # Память о противнике.
        self.seen = False
        self.lost_ticks = 10 ** 9
        self.ex = 0.0             # последняя известная позиция
        self.ey = 0.0
        self.evx = 0.0            # последняя известная скорость
        self.evy = 0.0
        self.eax = 0.0            # оценка ускорения (сглаженная)
        self.eay = 0.0
        self.prevv = None         # (vx, vy, tick) прошлого наблюдения
        self.ehull = 0.0
        self.eturret = 0.0
        self.ehp = 10.0
        self.ecooldown = 0.0
        self.e_seen_at = -10 ** 9

        # Своё состояние.
        self.hp_last = 10.0
        self.damage_tick = -1000
        self.side = 1.0           # сторона кружения
        self.side_until = 0
        self.jink = 0.0
        self.stuck = 0
        self.last_x = 0.0
        self.last_y = 0.0
        self.last_drive = 0.0
        self.jam_until = 0
        self.esc_until = 0
        self.esc_goal = None
        self.esc_cool = 0
        self.mode = "hunt"
        self.aim_note = ""
        self.pen = False
        self.tgt = None

    # --- карта ----------------------------------------------------------

    def _init_map(self, o):
        m = o.map
        self.tile = m.tile_size
        rows = m.rows
        self.H = len(rows)
        self.W = len(rows[0])
        # Маска непрозрачности с рамкой в два тайла: по ней ходит DDA и не
        # проверяет границы — как в движке (engine.grid.Arena.opaque_pad).
        pad = [bytearray(b"\x01" * (self.W + 4)) for _ in range(2)]
        for r in rows:
            pad.append(bytearray(b"\x01\x01" + bytes(
                1 if ch in "#o" else 0 for ch in r) + b"\x01\x01"))
        pad.append(bytearray(b"\x01" * (self.W + 4)))
        pad.append(bytearray(b"\x01" * (self.W + 4)))
        self.mask = pad
        cl = m.clearance_grid
        # Грубая сетка: 2×2 тайла. Клетка проезжая, если в ней есть центр
        # со свободой не меньше тайла: танк (40 px) в полуклетку не влезет.
        self.map_rows = rows
        c = self.cell
        cw = (self.W + c - 1) // c
        ch = (self.H + c - 1) // c
        self.cw = cw
        self.ch = ch
        blocked = bytearray(cw * ch)
        for cy in range(ch):
            ty = cy * c
            if ty >= self.H:
                ty = self.H - 1
            row = rows[ty]
            crow = cl[ty]
            for cx in range(cw):
                tx = cx * c
                if tx >= self.W:
                    tx = self.W - 1
                # Проезжа только там, где сам центр клетки стоит не ближе
                # тайла к стене: иначе маршрут ведёт танк бортом по углам.
                blocked[cy * cw + cx] = 0 if (row[tx] not in "#o:"
                                              and crow[tx] >= 1.0) else 1
        self.blocked = blocked
        # Сетка обзора для поиска: 6 тайлов (192 px) на клетку.
        sc = self.seen_cell
        sw = (self.W + sc - 1) // sc
        sh = (self.H + sc - 1) // sc
        self.sw = sw
        self.sh = sh
        self.seen_at = [-10 ** 9] * (sw * sh)
        self.flow = None
        self.flow_goal = None
        sp = m.spawns
        self.spawns = [(sp[0].x, sp[0].y), (sp[1].x, sp[1].y)]
        self.ready = True

    def _opaque(self, px, py):
        """Непрозрачен ли тайл под точкой (за картой — стена)."""
        t = self.tile
        tx = int(px // t)
        ty = int(py // t)
        if tx < 0 or ty < 0 or tx >= self.W or ty >= self.H:
            return True
        return self.mask[ty + 2][tx + 2] != 0

    def _wall_t(self, ox, oy, dx, dy, max_t):
        """DDA по сетке: ``t`` до первой непрозрачной стены или ``None``.

        Копия ``engine.visibility._ray_grid_opaque``: шагаем по границам
        тайлов, поэтому луч не проскакивает угол.
        """
        if dx != dx or dy != dy:
            return None
        t = float(self.tile)
        x = int(ox // t)
        y = int(oy // t)
        if dx > 0:
            step_x = 1
            dtx = t / dx
            tx_next = ((x + 1) * t - ox) / dx
        elif dx < 0:
            step_x = -1
            dtx = -t / dx
            tx_next = (x * t - ox) / dx
        else:
            step_x = -1
            dtx = 1e18
            tx_next = 1e18
        if dy > 0:
            step_y = 1
            dty = t / dy
            ty_next = ((y + 1) * t - oy) / dy
        elif dy < 0:
            step_y = -1
            dty = -t / dy
            ty_next = (y * t - oy) / dy
        else:
            step_y = -1
            dty = 1e18
            ty_next = 1e18
        pad = self.mask
        px = x + 2
        py = y + 2
        row = pad[py]
        while True:
            if tx_next < ty_next:
                t_now = tx_next
                tx_next += dtx
                px += step_x
            else:
                t_now = ty_next
                ty_next += dty
                py += step_y
                row = pad[py]
            if t_now > max_t:
                return None
            if row[px]:
                return t_now

    def _clear_path(self, x0, y0, x1, y1):
        """Есть ли прямой проезд между точками (для сглаживания маршрута)."""
        dx = x1 - x0
        dy = y1 - y0
        d = hypot(dx, dy)
        if d < 1.0:
            return True
        if self._wall_t(x0, y0, dx / d, dy / d, d) is not None:
            return False
        # Мимо стен, но корпус шире точки: проверяем ещё две параллельные
        # линии со сдвигом на полкорпуса в стороны.
        nx = -dy / d
        ny = dx / d
        off = 14.0
        for s in (off, -off):
            if self._wall_t(x0 + nx * s, y0 + ny * s,
                            dx / d, dy / d, d) is not None:
                return False
        return True

    # --- поиск пути -----------------------------------------------------

    def _build_flow(self, gx, gy):
        """BFS по грубой сетке от цели: расстояние в клетках до неё."""
        c = self.cell
        t = self.tile
        cw = self.cw
        ch = self.ch
        blocked = self.blocked
        gcx = int(gx // (t * c))
        gcy = int(gy // (t * c))
        if gcx < 0:
            gcx = 0
        elif gcx >= cw:
            gcx = cw - 1
        if gcy < 0:
            gcy = 0
        elif gcy >= ch:
            gcy = ch - 1
        # Если цель в стене — ищем ближайшую свободную клетку вокруг.
        if blocked[gcy * cw + gcx]:
            best = None
            best_d = 10 ** 9
            cx0 = int(self.me_x // (t * c))
            cy0 = int(self.me_y // (t * c))
            for oy in range(-3, 4):
                for ox in range(-3, 4):
                    nx = gcx + ox
                    ny = gcy + oy
                    if nx < 0 or ny < 0 or nx >= cw or ny >= ch:
                        continue
                    if blocked[ny * cw + nx]:
                        continue
                    d = (nx - cx0) * (nx - cx0) + (ny - cy0) * (ny - cy0)
                    if d < best_d:
                        best_d = d
                        best = (nx, ny)
            if best is None:
                self.flow = None
                return
            gcx, gcy = best
        dist = [-1] * (cw * ch)
        dist[gcy * cw + gcx] = 0
        queue = [gcy * cw + gcx]
        head = 0
        while head < len(queue):
            cur = queue[head]
            head += 1
            d = dist[cur] + 1
            cy = cur // cw
            cx = cur - cy * cw
            if cx > 0:
                n = cur - 1
                if dist[n] < 0 and not blocked[n]:
                    dist[n] = d
                    queue.append(n)
            if cx < cw - 1:
                n = cur + 1
                if dist[n] < 0 and not blocked[n]:
                    dist[n] = d
                    queue.append(n)
            if cy > 0:
                n = cur - cw
                if dist[n] < 0 and not blocked[n]:
                    dist[n] = d
                    queue.append(n)
            if cy < ch - 1:
                n = cur + cw
                if dist[n] < 0 and not blocked[n]:
                    dist[n] = d
                    queue.append(n)
        self.flow = dist
        self.flow_goal = (gcx, gcy)

    def _flow_step(self, x, y):
        """Следующая точка маршрута по полю расстояний (мировые пиксели)."""
        c = self.cell
        t = self.tile
        cw = self.cw
        ch = self.ch
        dist = self.flow
        if dist is None:
            return None
        cx = int(x // (t * c))
        cy = int(y // (t * c))
        if cx < 0 or cy < 0 or cx >= cw or cy >= ch:
            return None
        i = cy * cw + cx
        here = dist[i]
        if here < 0:
            # Мы в «непроезжей» клетке (например, прижались к стене):
            # выбираем лучшего соседа без учёта собственного расстояния.
            here = 10 ** 9
        best = None
        best_d = here
        for oy in (-1, 0, 1):
            for ox in (-1, 0, 1):
                if ox == 0 and oy == 0:
                    continue
                nx = cx + ox
                ny = cy + oy
                if nx < 0 or ny < 0 or nx >= cw or ny >= ch:
                    continue
                j = ny * cw + nx
                d = dist[j]
                if d < 0:
                    continue
                # Диагональ дороже: предпочитаем прямые шаги.
                if ox != 0 and oy != 0:
                    d += 0.41
                if d < best_d:
                    best_d = d
                    best = (nx, ny)
        if best is None:
            return None
        return ((best[0] + 0.5) * t * c, (best[1] + 0.5) * t * c)

    def _nav_to(self, o, gx, gy):
        """Руль к цели: прямо, если видно, иначе по полю расстояний."""
        me = o.me
        if self.flow is None or self.flow_goal is None:
            self._build_flow(gx, gy)
        else:
            c = self.cell
            t = self.tile
            gcx = int(gx // (t * c))
            gcy = int(gy // (t * c))
            if (gcx, gcy) != self.flow_goal or (o.tick & 63) == 0:
                self._build_flow(gx, gy)
        if self._clear_path(me.x, me.y, gx, gy):
            return gx, gy
        wp = self._flow_step(me.x, me.y)
        if wp is None:
            return gx, gy
        # Сглаживание: если до дальней точки поля путь свободен — идём туда.
        return wp

    # --- память о противнике --------------------------------------------

    def _sense(self, o):
        me = o.me
        if me.hp < self.hp_last - 1e-6:
            self.damage_tick = o.tick
            self.hp_last = me.hp
        if o.enemy is not None:
            e = o.enemy
            if self.lost_ticks > 40:
                # Давно не видели — скорость берём как есть, фильтр не нужен.
                self.evx = e.vx
                self.evy = e.vy
                self.eax = 0.0
                self.eay = 0.0
            else:
                # Сглаживание: рывок корпуса не должен уводить упреждение.
                nvx = 0.5 * self.evx + 0.5 * e.vx
                nvy = 0.5 * self.evy + 0.5 * e.vy
                dt = (o.tick - self.prevv[2]) * o.dt if self.prevv else o.dt
                if dt > 0.0:
                    ax = (nvx - self.evx) / dt
                    ay = (nvy - self.evy) / dt
                    # Ускорение танка ограничено двигателем, поэтому оценку
                    # подрезаем: иначе единичный рывок уводит упреждение.
                    lim = ACC_LIM
                    ax = -lim if ax < -lim else (lim if ax > lim else ax)
                    ay = -lim if ay < -lim else (lim if ay > lim else ay)
                    self.eax = (1.0 - ACC_EMA) * self.eax + ACC_EMA * ax
                    self.eay = (1.0 - ACC_EMA) * self.eay + ACC_EMA * ay
                self.evx = nvx
                self.evy = nvy
            self.prevv = (self.evx, self.evy, o.tick)
            self.ex = e.x
            self.ey = e.y
            self.ehull = e.hull
            self.eturret = e.turret
            self.ehp = e.hp
            self.ecooldown = e.cooldown
            self.e_seen_at = o.tick
            self.lost_ticks = 0
            self.seen = True
        else:
            self.lost_ticks += 1

    # --- наводка --------------------------------------------------------

    def _predict(self, o, px, py, evx, evy, mx, my):
        """Куда придёт цель, пока летит снаряд.

        Скорость врага мы видим, ускорение — оцениваем по истории: без
        второго члена упреждение систематически не добирает на разгоне, а
        на торможении уходит вперёд на те же десятки пикселей.
        """
        speed = o.bullet_speed
        ax = self.eax * ACC_W
        ay = self.eay * ACC_W
        t = hypot(px - mx, py - my) / speed
        for _ in range(2):
            fx = px + evx * t + 0.5 * ax * t * t
            fy = py + evy * t + 0.5 * ay * t * t
            t = hypot(fx - mx, fy - my) / speed
        return px + evx * t + 0.5 * ax * t * t, py + evy * t + 0.5 * ay * t * t

    def _solve_aim(self, o, px, py, hull, evx, evy):
        """Выбор угла башни: ``(cmd, fire, note)``.

        Перебираем направления через коробку врага (с упреждением), для
        каждого считаем грань и угол к нормали, оставляем те, что пробивают
        броню. Из них берём середину непрерывного участка — там разброс
        ствола не выносит снаряд ни за цель, ни в рикошет. Огонь открываем
        только тогда, когда вероятность попадания с учётом разброса и
        остаточного доводота башни достаточно высока.
        """
        me = o.me
        ml = o.muzzle_len
        step = o.bullet_turn_rate * o.dt
        # Прикидка упреждения по центру цели — от неё пляшут все направления.
        mx0 = me.x + cos(me.turret) * ml
        my0 = me.y + sin(me.turret) * ml
        fx, fy = self._predict(o, px, py, evx, evy, mx0, my0)
        d = hypot(fx - me.x, fy - me.y)
        bearing = atan2(fy - me.y, fx - me.x)
        # В упор (дуло почти в корпусе) углы считать поздно: ствол уже внутри
        # коробки, и снаряд попадёт при любом раскладе. Просто доводим башню
        # на центр и стреляем, как только она смотрит туда.
        if d < 62.0:
            err = wrap(bearing - me.turret)
            cmd = clamp(err / step, -1.0, 1.0)
            residual = err - cmd * step
            return cmd, me.ammo_ready and abs(residual) <= 0.012, "в упор"
        half_ang = atan2(BHX + 7.0, max(40.0, d))
        n = 11
        span = 2.0 * half_ang / (n - 1)
        good = [False] * n
        dbg = []
        theta_min = 1e9
        for k in range(n):
            a = bearing - half_ang + span * k
            mx = me.x + cos(a) * ml
            my = me.y + sin(a) * ml
            res = _ray_obb(mx, my, cos(a), sin(a), fx, fy, BHX, BHY, hull)
            if res is None:
                continue
            t_hit, nx, ny = res
            if t_hit > d + 40.0:
                continue
            limit, theta = _face_and_theta(nx, ny, cos(a), sin(a), hull)
            dbg.append((round(theta, 1), round(limit, 1), round(t_hit, 1)))
            if theta < limit:
                good[k] = True
                if theta < theta_min:
                    theta_min = theta
        if not any(good):
            self.dbg = (round(d, 1), dbg)
            self.pen = False
            self.tgt = (fx, fy, hull)
            return 0.0, False, "нет пробития"
        # Лучший участок: непрерывная полоса пробивающих направлений,
        # ближайшая к центру коробки.
        center = (n - 1) / 2.0
        best_k = None
        for k in range(n):
            if good[k] and (best_k is None
                            or abs(k - center) < abs(best_k - center)):
                best_k = k
        k0 = best_k
        while k0 > 0 and good[k0 - 1]:
            k0 -= 1
        k1 = best_k
        while k1 < n - 1 and good[k1 + 1]:
            k1 += 1
        # Полуширина пробивающей полосы вокруг лучшего направления (радианы).
        half_w = min(best_k - k0 + 0.5, k1 - best_k + 0.5) * span
        a = bearing - half_ang + span * best_k
        err = wrap(a - me.turret)
        cmd = clamp(err / step, -1.0, 1.0)
        residual = err - cmd * step
        # Вероятность попадания навылет: разброс ствола и остаток доводота
        # башни должны вместе укладываться в полуширину полосы.
        lo = -SPREAD
        hi = SPREAD
        r0 = residual - half_w
        r1 = residual + half_w
        if r0 > lo:
            lo = r0
        if r1 < hi:
            hi = r1
        p_hit = (hi - lo) / (2.0 * SPREAD)
        if p_hit < 0.0:
            p_hit = 0.0
        # Стена на пути снаряда важнее брони: проверяем выбранный луч.
        mx = me.x + cos(a) * ml
        my = me.y + sin(a) * ml
        t_hit_wall = self._wall_t(mx, my, cos(a), sin(a), d)
        blocked = t_hit_wall is not None
        fire = (not blocked) and p_hit >= 0.5 and me.ammo_ready
        note = "p%.2f" % p_hit
        if blocked:
            note = "стена"
        self.pen = True
        self.tgt = (fx, fy, hull)
        return cmd, fire, note

    # --- движение -------------------------------------------------------

    def _free_along(self, x, y, a, cap):
        """Сколько пикселей свободно по направлению ``a`` (до стены)."""
        t = self._wall_t(x, y, cos(a), sin(a), cap)
        return cap if t is None else t

    def _scan_heading(self, o, want):
        """Курс: тянемся к цели, объезжая стены.

        Для каждого из 24 направлений берём не «запас клетки», а честное
        расстояние до стены вдоль луча (DDA по тайлам). Клеточный запас
        слишком грубый: в узком коридоре он одинаков и у центра, и у стены,
        и танк вёл бортом по углам (что и клинило его намертво).
        """
        me = o.me
        x = me.x
        y = me.y
        cw = cos(want)
        sw = sin(want)
        best = want
        best_score = -1e9
        for i in range(24):
            a = want + (i - 11.5) * 0.2618          # шаг 15°, ±172°
            ca = cos(a)
            sa = sin(a)
            align = ca * cw + sa * sw
            free = self._free_along(x, y, a, 96.0)
            # Носом в стену — почти запрещено: узкая свобода штрафуется
            # квадратично, широкая не даёт бонуса.
            if free > 60.0:
                free = 60.0
            score = align - (60.0 - free) * 0.030
            if score > best_score:
                best_score = score
                best = a
        return best

    def _move(self, o, gx, gy, throttle=SPEED, jink=0.0):
        """Ехать к точке: курс — с объездом стен, газ — по свободе впереди."""
        me = o.me
        want = atan2(gy - me.y, gx - me.x) + jink
        # Клин: газ был, а танк не сдвинулся — значит физика держит корпус
        # бортом за угол. Выезд назад с разворотом на месте только
        # закручивал танк в спираль (проверено трассировкой), поэтому
        # отходим к ближайшей открытой точке и едем оттуда по маршруту.
        if o.tick >= self.jam_until:
            if o.tick % 10 == 0:
                moved = hypot(me.x - self.last_x, me.y - self.last_y)
                if self.last_drive > 0.35 and moved < 6.0:
                    self.stuck += 1
                else:
                    self.stuck = 0
                self.last_x = me.x
                self.last_y = me.y
                if self.stuck >= 2:
                    self.stuck = 0
                    self.esc_goal = self._open_goal(o)
                    self.esc_until = o.tick + 60
                    self.jam_until = self.esc_until + 45
                    self.last_drive = 0.0
                    self.flow = None
        if self.esc_goal is not None and o.tick < self.esc_until:
            gx2, gy2 = self.esc_goal
            if hypot(gx2 - me.x, gy2 - me.y) < 14.0:
                self.esc_goal = None
            else:
                drive, turn = self._drive_to(o, gx2, gy2, throttle)
                self.last_drive = drive
                return drive, turn
        else:
            self.esc_goal = None
        drive, turn = self._drive_to(o, gx, gy, throttle)
        self.last_drive = drive
        return drive, turn

    def _drive_to(self, o, gx, gy, throttle=SPEED):
        """Газ и руль к точке: курс через скан, газ по свободе впереди."""
        me = o.me
        want = atan2(gy - me.y, gx - me.x)
        a = self._scan_heading(o, want)
        err = wrap(a - me.hull)
        ae = err if err >= 0.0 else -err
        turn = clamp(err * 3.0, -1.0, 1.0)
        # Свобода по текущему курсу: если впереди стена — притормаживаем и
        # доворачиваем, а не тараним её (иначе корпус клинит бортом).
        ahead = self._free_along(me.x, me.y, me.hull, 72.0)
        if ae > 2.4:
            drive = -0.6
        elif ahead > 60.0:
            drive = throttle
        elif ahead > 34.0:
            drive = throttle * 0.55
        elif ahead > 18.0:
            drive = throttle * 0.2
        else:
            drive = -0.5 if ae < 1.8 else 0.0
        return drive, turn

    def _open_goal(self, o):
        """Ближайшая открытая точка: куда отъехать, чтобы расклиниться."""
        me = o.me
        m = o.map
        best = None
        for r in (44.0, 72.0, 104.0, 148.0):
            for i in range(16):
                a = i * 0.3927
                px = me.x + cos(a) * r
                py = me.y + sin(a) * r
                if m.clearance(px, py) < 34.0:
                    continue
                if not self._clear_path(me.x, me.y, px, py):
                    continue
                best = (px, py)
                break
            if best is not None:
                break
        if best is None:
            return me.x, me.y
        return best

    def _probe_rays(self, x, y, fx, fy, hull):
        """Появится ли пробитие, если стрелять из точки (x, y).

        Используется, чтобы выбрать сторону смещения: угол к броне зависит
        от ракурса, поэтому достаточно сдвинуться вбок, и «мёртвая зона»
        (когда и лоб, и борт скользят) уходит.
        """
        d = hypot(fx - x, fy - y)
        if d < 62.0:
            return True
        bearing = atan2(fy - y, fx - x)
        half_ang = atan2(BHX + 7.0, max(40.0, d))
        n = 7
        for k in range(n):
            a = bearing - half_ang + 2.0 * half_ang * k / (n - 1)
            mx = x + cos(a) * 26.0
            my = y + sin(a) * 26.0
            res = _ray_obb(mx, my, cos(a), sin(a), fx, fy, BHX, BHY, hull)
            if res is None:
                continue
            t_hit, nx, ny = res
            if t_hit > d + 40.0:
                continue
            limit, theta = _face_and_theta(nx, ny, cos(a), sin(a), hull)
            if theta < limit:
                return True
        return False

    def _tactical_goal(self, o):
        """Куда ехать в бою: держим кольцо вокруг врага и рвём упреждение."""
        me = o.me
        # Кружение: сторона меняется рывками, чтобы нас не вели по дуге.
        if o.tick >= self.side_until:
            self.side_until = o.tick + 28 + ((o.tick * 7919 + 13) % 34)
            self.side = -self.side
        f = self._predict(o, self.ex, self.ey, self.evx, self.evy,
                          me.x, me.y)
        bx = f[0] - me.x
        by = f[1] - me.y
        d = hypot(bx, by)
        if d < 1.0:
            return me.x + cos(me.hull + 1.0) * 100.0, me.y + sin(me.hull + 1.0) * 100.0
        rx = bx / d
        ry = by / d
        # Радиальная тяга: дальше R_MAX — сближаемся, ближе R_MIN — отходим.
        if d > R_MAX:
            radial = 1.0
        elif d < R_MIN:
            radial = -1.0
        else:
            radial = 0.0
        tx = -ry * self.side
        ty = rx * self.side
        # Ракурс не пробивается: смещаемся боком, чтобы сменить угол к
        # броне. Сторона выбирается проверкой — с какой быстрее откроется
        # пробитие; при равном варианте держим текущее направление обхода.
        if not self.pen and self.tgt is not None:
            fxx, fyy, ehh = self.tgt
            bearing = atan2(by, bx)
            order = (1.0, -1.0) if self.side > 0 else (-1.0, 1.0)
            for sgn in order:
                px = me.x + cos(bearing + sgn * 1.5708) * 96.0
                py = me.y + sin(bearing + sgn * 1.5708) * 96.0
                if self._probe_rays(px, py, fxx, fyy, ehh):
                    tx2 = cos(bearing + sgn * 1.5708)
                    ty2 = sin(bearing + sgn * 1.5708)
                    return me.x + tx2 * 260.0, me.y + ty2 * 260.0
            sgn = self.side
            tx2 = cos(bearing + sgn * 1.5708)
            ty2 = sin(bearing + sgn * 1.5708)
            return me.x + tx2 * 260.0, me.y + ty2 * 260.0
        # Ракурс: корпус доворачиваем к врагу на ANGLE_DEG от направления
        # «на него». При 90° это чистая касательная (кружение), при меньших
        # углах снаряды приходят в лобовую деталь под углом больше порога
        # рикошета — тогда часть попаданий уходит в отскок.
        ph = ANGLE_DEG * PIO180
        cph = cos(ph)
        sph = sin(ph) * 1.05
        # Радиальная тяга (кольцо) складывается с доворотом корпуса.
        rin = cph + radial
        vx = rx * rin + tx * sph
        vy = ry * rin + ty * sph
        n = hypot(vx, vy)
        if n < 1e-6:
            n = 1.0
        # Точка в 260 px по выбранному направлению — цель маневра.
        return me.x + vx / n * 260.0, me.y + vy / n * 260.0

    def _hunt_goal(self, o):
        """Без контакта: идём к последней точке, потом к спавну врага."""
        me = o.me
        if self.lost_ticks < 60 * 6:
            return self.ex, self.ey
        sx, sy = self.spawns[1 - o.tank]
        if hypot(sx - me.x, sy - me.y) > 160.0 or self.lost_ticks < 60 * 14:
            return sx, sy
        cx = o.map.pixel_width * 0.5
        cy = o.map.pixel_height * 0.5
        if hypot(cx - me.x, cy - me.y) > 140.0:
            return cx, cy
        return self.spawns[o.tank]

    def _cell_index(self, px, py):
        c = self.cell
        t = self.tile
        cx = int(px // (t * c))
        cy = int(py // (t * c))
        if cx < 0:
            cx = 0
        elif cx >= self.cw:
            cx = self.cw - 1
        if cy < 0:
            cy = 0
        elif cy >= self.ch:
            cy = self.ch - 1
        return cy * self.cw + cx

    def _mark_seen(self, o):
        """Отмечаем клетки обзора, до которых есть прямая видимость."""
        if (o.tick & 3) != 0:
            return
        me = o.me
        sc = self.seen_cell
        t = self.tile
        sr = sc * t
        now = o.tick
        for cy2 in range(self.sh):
            py = (cy2 + 0.5) * sr
            if py > o.map.pixel_height:
                continue
            for cx2 in range(self.sw):
                px = (cx2 + 0.5) * sr
                if px > o.map.pixel_width:
                    continue
                i = cy2 * self.sw + cx2
                if now - self.seen_at[i] < 240:
                    continue
                dx = px - me.x
                dy = py - me.y
                d = hypot(dx, dy)
                if d < 1.0:
                    self.seen_at[i] = now
                    continue
                if self._wall_t(me.x, me.y, dx / d, dy / d, d) is None:
                    self.seen_at[i] = now

    # --- тик ------------------------------------------------------------

    def on_tick(self, o):
        if not self.ready:
            self._init_map(o)
        me = o.me
        self.me_x = me.x
        self.me_y = me.y
        self._sense(o)

        cmd = 0.0
        fire = False
        note = ""
        # Целимся в противника, пока его видно; иначе — в последнюю точку.
        if o.enemy is not None:
            cmd, fire, note = self._solve_aim(o, self.ex, self.ey,
                                              self.ehull, self.evx, self.evy)
        elif self.lost_ticks < 40 and self.seen:
            px, py = self._predict(o, self.ex, self.ey, self.evx, self.evy,
                                   me.x, me.y)
            # Доводим башню на упреждённую точку, но не стреляем вслепую —
            # выстрел стоит секунды перезарядки.
            want = atan2(py - me.y, px - me.x)
            err = wrap(want - me.turret)
            step = o.bullet_turn_rate * o.dt
            cmd = clamp(err / step, -1.0, 1.0)
        else:
            gx, gy = self._hunt_goal(o)
            want = atan2(gy - me.y, gx - me.x)
            err = wrap(want - me.turret)
            step = o.bullet_turn_rate * o.dt
            cmd = clamp(err / step, -1.0, 1.0)

        # Движение.
        if o.enemy is not None or self.lost_ticks < 30:
            gx, gy = self._tactical_goal(o)
            drive, turn = self._move(o, gx, gy)
        else:
            gx, gy = self._hunt_goal(o)
            nx, ny = self._nav_to(o, gx, gy)
            drive, turn = self._move(o, nx, ny)
        self.aim_note = note
        return Action(drive=drive, turn=turn, turret=cmd, fire=fire)


program = Brain()
