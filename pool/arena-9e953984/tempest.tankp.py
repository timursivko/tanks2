#!TANKP 1
# name: Гроза
# author: arena-9e953984
# color: #7ee7ff
# description: Боевая машина манёвра: идёт к врагу по кратчайшему пути, в бою держит кольцо и дёргает передачу вперёд-назад, уходит с линии чужого выстрела и стреляет с упреждением только тогда, когда попадание вероятно.
# tags: манёвр,уклонение,упреждение,дистанция,охота

from math import atan2, cos, exp, hypot, pi, sin
from tankp import Action, TankProgram, clamp, wrap

TAU = 2.0 * pi
BULLET = 620.0        # скорость снаряда движка
MUZZLE = 26.0         # конец ствола от центра
TILE = 32.0           # тайл по умолчанию (реальный приходит в карте)

#: 8 направлений обхода по сетке: (dx, dy).
NB8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _unit(x, y):
    """Нормирует вектор; нулевой остаётся нулевым."""
    d = hypot(x, y)
    if d < 1e-9:
        return 0.0, 0.0
    return x / d, y / d


def _fold(a):
    """Складывает угол в (-pi/2, pi/2]: линия важнее направления по ней."""
    if a > pi * 0.5:
        return a - pi
    if a < -pi * 0.5:
        return a + pi
    return a


class Brain(TankProgram):
    """Гроза — танк манёвренного боя.

    Три идеи, на которых держится поведение:

    * **корпус и башня независимы** — танцевать и целиться можно
      одновременно, поэтому манёвр ничего не стоит стрельбе;
    * **снаряд летит по прямой** — его линию можно посчитать самому, как
      только враг выстрелил, и увести с неё своё будущее положение;
    * **выстрел стоит секунды перезарядки** — стреляем тогда, когда
      попадание вероятно, а не «лишь бы бахнуть».

    Танец устроен как маятник: корпус держит линию, перпендикулярную
    направлению на врага, а передача сама меняет знак — танк ездит то
    вперёд, то назад по одной и той же линии. Разворот корпуса на 180°
    стоил бы двух секунд и полной потери скорости; смена передачи —
    мгновенная.
    """

    # --- настройки боя ---------------------------------------------------

    STANDOFF = 290.0        # желаемая дистанция дуэли
    D_MIN = 175.0           # ближе — отходим
    D_MAX = 430.0           # дальше — сближаемся
    GEAR_MIN = 0.34         # полупериод маятника, секунды
    GEAR_MAX = 0.85
    DODGE_MISS = 46.0       # промах, который считается безопасным (px)
    P_HIT_MIN = 0.17        # ниже этой оценки вероятность — не стреляем
    P_HIT_FINISH = 0.06     # добивающий выстрел: стреляем и с меньшим шансом
    SEARCH_FRESH = 7.0      # сколько секунд идём к последней точке врага
    TURN_CAP = 0.62         # предел руля: выше — тяга падает, танк буксует

    # --- жизненный цикл --------------------------------------------------

    def on_start(self, ctx):
        self.rnd = 0x2545F491
        self.t = 0.0
        # память о враге
        self.seen = False
        self.et = -99.0          # время последнего контакта
        self.ex = self.ey = 0.0  # последняя известная позиция
        self.evx = self.evy = 0.0
        self.ehull = 0.0
        self.e_omega = 0.0       # сглаженная угловая скорость корпуса врага
        self.flips = []          # моменты смены знака боковой скорости врага
        self.sign = 0
        # карта
        self.blk = None
        self.rows = None
        self.W = self.H = 0
        self.tsize = TILE
        self.field = None        # поле расстояний BFS до цели
        self.goal = None         # тайл цели, для которой оно построено
        self.field_tick = -999
        self.path = None         # путь по тайлам: список индексов
        self.path_i = 0          # указатель на текущее звено пути
        self.sweep = None        # маршрут поиска: список точек по всей карте
        self.stage = 0           # номер точки маршрута
        # застревание
        self.px = self.py = 0.0
        self.mile = 0.0          # пройденный путь за окно наблюдения
        self.stuck_t = 0.0
        self.escape_until = -1.0
        self.escape_side = 1.0
        self.cmd_drive = 0.0     # прошлая команда газа (для сторожа)
        self.prev_k = 0          # прошлый сектор веера проб
        self.replans = 0
        self.path = None
        self.path_i = 0
        # бой
        self.gear = 1            # передача маятника: +1 вперёд, -1 назад
        self.gear_until = 0.0
        self.threat = None       # (x, y, dx, dy, t) — последний чужой выстрел
        self.prev_cd = None
        self.logged = False

    # --- контроль буксования ----------------------------------------------

    def _watch_stuck(self, o, me):
        """Замечает, что танк встал: команда есть, а движения нет.

        Танк можно заклинить углом колонны: скорость гасится о стену,
        и сколько ни дави газом — он стоит. Спасает только задний ход с
        разворотом: выйти из клина и зайти заново. Раньше в такой ситуации
        бой заканчивался ничьей: танк стоял до таймаута.
        """
        # Считаем именно пройденный путь: танк может честно дёргаться
        # вперёд-назад на месте и почти не смещаться при этом.
        self.mile += hypot(me.x - self.px, me.y - self.py)
        self.px, self.py = me.x, me.y
        self.stuck_t += o.dt
        if self.stuck_t >= 0.6:
            # Быстрого хода требуем только от «рабочей» команды газа: если
            # танк сознательно крадётся или тормозит, это не застревание.
            stalled = self.mile < 14.0 and abs(self.cmd_drive) > 0.3
            if stalled and self.t >= self.escape_until:
                self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
                self.escape_side = 1.0 if self.rnd % 2 else -1.0
                self.escape_until = self.t + 0.65
                self.gear = 1
                self.replans += 1
                self.field = None   # путь перестроим на свежей позиции
                self.path = None
                self.sweep = None
            self.mile = 0.0
            self.stuck_t = 0.0

    # --- главный тик ------------------------------------------------------

    def on_tick(self, o):
        me = o.me
        self.t = o.time
        if self.blk is None:
            self._build_grid(o)
        enemy = o.enemy
        if enemy is not None:
            self._see(o, enemy)
        self._watch_stuck(o, me)
        threat = self._threat(o, enemy, me)
        turret, fire = self._gun(o, enemy, me)
        if self.t < self.escape_until:
            # Клиновая ситуация: задний ход с одновременным разворотом.
            # Задним ходом машина сходит с упора, разворот уводит нос от
            # стены, чтобы следующий заход не повторил ту же ошибку.
            turn = clamp(self.escape_side * 0.9, -1.0, 1.0)
            drive = -0.9
            self.cmd_drive = drive
            return Action(drive=drive, turn=turn, turret=turret, fire=fire)
        ux, uy, sp, axis = self._move(o, enemy, me, threat)
        turn, drive = self._drive(o, me, ux, uy, sp, axis)
        self.cmd_drive = drive
        return Action(drive=drive, turn=turn, turret=turret, fire=fire)

    # --- память и оценка врага -------------------------------------------

    def _see(self, o, enemy):
        """Запоминает врага и оценивает, как он дёргается."""
        t = self.t
        if self.seen:
            dt = t - self.et
            if 0.0005 < dt < 0.2:
                om = wrap(enemy.hull - self.ehull) / dt
                if om > 2.6:
                    om = 2.6
                elif om < -2.6:
                    om = -2.6
                self.e_omega = self.e_omega * 0.72 + om * 0.28
                # знак боковой скорости относительно линии «враг → мы»
                ux, uy = _unit(self.ex - enemy.x, self.ey - enemy.y)
                vp = ux * enemy.vy - uy * enemy.vx
                s = 1 if vp > 22.0 else (-1 if vp < -22.0 else 0)
                if s and self.sign and s != self.sign:
                    self.flips.append(t)
                    if len(self.flips) > 8:
                        del self.flips[0]
                if s:
                    self.sign = s
            elif dt >= 0.5:
                # долго не виделись — прежняя статистика дёрганья не годится
                self.flips = []
                self.sign = 0
                self.e_omega *= 0.5
        else:
            self.flips = []
            self.sign = 0
        self.ex, self.ey = enemy.x, enemy.y
        self.evx, self.evy = enemy.vx, enemy.vy
        self.ehull = enemy.hull
        self.et = t
        self.seen = True
        if not self.logged:
            self.logged = True
            from tankp import log
            log("Гроза: контакт, работаю по цели")

    def _jink_period(self):
        """Оценка полупериода дёрганья врага: маленькая — он вертлявый."""
        f = self.flips
        if len(f) < 2:
            return 3.0
        gap = (f[-1] - f[0]) / (len(f) - 1)
        if gap < 0.2:
            return 0.2
        if gap > 3.0:
            return 3.0
        return gap

    # --- прицел и выстрел -------------------------------------------------

    def _predict(self, o, enemy):
        """Куда стрелять: упреждение с учётом дуги, если враг доворачивает."""
        me = o.me
        mx = me.x + cos(me.turret) * o.muzzle_len
        my = me.y + sin(me.turret) * o.muzzle_len
        x, y = enemy.x, enemy.y
        vx, vy = enemy.vx, enemy.vy
        speed = hypot(vx, vy)
        om = self.e_omega
        t = hypot(x - mx, y - my) / o.bullet_speed
        arc = abs(om) > 0.06 and speed > 30.0
        for _ in range(2):
            if arc:
                # равномерное вращение вектора скорости: дуга окружности
                a = atan2(vy, vx)
                da = om * t
                r = speed / om
                nx = x + r * (sin(a + da) - sin(a))
                ny = y - r * (cos(a + da) - cos(a))
            else:
                nx = x + vx * t
                ny = y + vy * t
            t = hypot(nx - mx, ny - my) / o.bullet_speed
        return nx, ny

    def _gun(self, o, enemy, me):
        """Доворот башни и решение о выстреле."""
        if enemy is None:
            if self.seen and (self.t - self.et) < 4.0:
                aim = (self.ex, self.ey)
            else:
                aim = self._scan_point(o, me)
            err = o.aim_error(aim[0], aim[1])
            step = o.bullet_turn_rate * o.dt
            if err > step:
                return 1.0, False
            if err < -step:
                return -1.0, False
            return err / step, False

        aim = self._predict(o, enemy)
        err = o.aim_error(aim[0], aim[1])
        step = o.bullet_turn_rate * o.dt
        if err > step:
            cmd = 1.0
        elif err < -step:
            cmd = -1.0
        else:
            cmd = err / step
        # Точная проверка: снаряд выйдет из ствола уже после доворота.
        after = me.turret + cmd * step
        mx = me.x + cos(after) * o.muzzle_len
        my = me.y + sin(after) * o.muzzle_len
        residual = wrap(atan2(aim[1] - my, aim[0] - mx) - after)
        if residual < 0.0:
            residual = -residual
        d = hypot(aim[0] - mx, aim[1] - my)
        # Допуск: половина корпуса цели минус разброс ствола.
        hx, hy = o.target_half
        half = hy if hy < hx else hx
        if half < 8.0:
            half = 8.0
        tol = (half * 1.05) / (d if d > 60.0 else 60.0) - o.spread_deg * pi / 180.0
        if tol < 0.0015:
            tol = 0.0015
        if residual > tol or not me.ammo_ready:
            return cmd, False
        # Стреляем, только если снаряд дойдёт: ствол не в стене и трасса
        # не перехвачена. Проверка идёт настоящим обходом тайлов, не
        # выборкой точек: угол стены толщиной в один тайл — это ровно тот
        # случай, который выборка пропускает.
        if not o.map.passable(mx, my):
            return cmd, False
        if self._line_blocked(o, mx, my, aim[0], aim[1]):
            return cmd, False
        # Шанс попадания: снаряд летит t секунд, за это время цель успевает
        # сманеврировать. Чем вертлявее враг и дальше дистанция — тем ниже.
        tf = d / o.bullet_speed
        p = exp(-tf / self._jink_period())
        if p >= self.P_HIT_MIN:
            return cmd, True
        if enemy.hp <= 4.0 and p >= self.P_HIT_FINISH:
            return cmd, True
        return cmd, False

    def _line_blocked(self, o, x0, y0, x1, y1):
        """Пересекает ли отрезок непрозрачный тайл.

        Своя реализация вместо ``map.blocked_between``: та проверяет точки
        через каждые 0.4 тайла и на углу может проскочить стену. Здесь идёт
        честный обход сетки (DDA) сразу тремя параллельными лучами —
        центральным и двумя со сдвигом на радиус снаряда, чтобы касание
        угла тоже считалось стеной.
        """
        m = o.map
        ts = m.tile_size
        gx = x1 - x0
        gy = y1 - y0
        ln = hypot(gx, gy)
        if ln < 1e-6:
            return False
        px = -gy / ln
        py = gx / ln
        for off in (0.0, 4.0, -4.0):
            if self._ray_blocked(m, x0 + px * off, y0 + py * off, gx, gy, ln, ts):
                return True
        return False

    def _ray_blocked(self, m, ox, oy, gx, gy, ln, ts):
        """DDA по сетке карты: упрётся ли луч в непрозрачный тайл."""
        dx = gx / ln
        dy = gy / ln
        tx = int(ox // ts)
        ty = int(oy // ts)
        step_x = 1 if dx > 0 else -1
        step_y = 1 if dy > 0 else -1
        # расстояния (в параметре t вдоль луча) до следующих границ
        dtx = abs(ts / dx) if dx != 0.0 else 1e18
        dty = abs(ts / dy) if dy != 0.0 else 1e18
        if dx > 0.0:
            mx = ((tx + 1) * ts - ox) / dx
        elif dx < 0.0:
            mx = (tx * ts - ox) / dx
        else:
            mx = 1e18
        if dy > 0.0:
            my = ((ty + 1) * ts - oy) / dy
        elif dy < 0.0:
            my = (ty * ts - oy) / dy
        else:
            my = 1e18
        t = 0.0
        guard = 0
        limit = (m.width + m.height) * 2 + 8
        rows = self.rows
        while t <= ln and guard < limit:
            guard += 1
            if mx < my:
                t = mx
                mx += dtx
                tx += step_x
            else:
                t = my
                my += dty
                ty += step_y
            if t > ln:
                break
            if tx < 0 or ty < 0 or ty >= m.height or tx >= m.width:
                return True
            ch = rows[ty][tx]
            if ch == "#" or ch == "o":
                return True
        return False

    def _scan_point(self, o, me):
        """Куда смотреть башней, когда врага нет: цель поиска или её направление."""
        if self.goal is not None:
            gx = (self.goal[0] + 0.5) * o.map.tile_size
            gy = (self.goal[1] + 0.5) * o.map.tile_size
            if hypot(gx - me.x, gy - me.y) > 40.0:
                return gx, gy
        if self.seen:
            return self.ex, self.ey
        return me.x + cos(me.hull) * 300.0, me.y + sin(me.hull) * 300.0

    # --- уклонение от чужих снарядов -------------------------------------

    def _threat(self, o, enemy, me):
        """Ловит момент выстрела врага и ведёт его снаряд, пока тот летит.

        Выстрел виден по скачку перезарядки: было «почти готово» — стало
        «только что выстрелил». Направление известно (угол башни врага в этот
        момент), точка выхода — конец ствола. Дальше снаряд летит по прямой,
        и своё будущее положение можно увести с его линии.
        """
        if enemy is not None:
            cd = enemy.cooldown
            if self.prev_cd is not None and self.prev_cd < 0.5 and cd > 0.6:
                a = enemy.turret
                self.threat = (enemy.x + cos(a) * MUZZLE,
                               enemy.y + sin(a) * MUZZLE,
                               cos(a), sin(a), self.t)
            self.prev_cd = cd
        else:
            self.prev_cd = None

        th = self.threat
        if th is None:
            return None
        bx, by, dx, dy, t0 = th
        age = self.t - t0
        if age > 3.0:
            self.threat = None
            return None
        # Снаряд за время наблюдения прошёл (и продолжает идти) по прямой.
        bx += dx * BULLET * age
        by += dy * BULLET * age
        # Относительное движение «снаряд -> мы»: где точка наибольшего сближения.
        rx = bx - me.x
        ry = by - me.y
        vx = dx * BULLET - me.vx
        vy = dy * BULLET - me.vy
        vv = vx * vx + vy * vy
        if vv < 1.0:
            self.threat = None
            return None
        t_star = -(rx * vx + ry * vy) / vv
        if t_star <= 0.0:
            self.threat = None
            return None
        mx = rx + vx * t_star
        my = ry + vy * t_star
        miss = hypot(mx, my)
        if miss > self.DODGE_MISS:
            # И так разминулись: не тратим манёвр на пустой снаряд.
            self.threat = None
            return None
        if miss < 0.5:
            # Снаряд летит точно в нас: уходим вбок, используя свою инерцию.
            px, py = -vy, vx
            side = 1.0 if px * me.vx + py * me.vy >= 0.0 else -1.0
            ex, ey = px * side, py * side
        else:
            # Направление, в котором промах растёт быстрее всего.
            ex, ey = mx, my
            n = hypot(ex, ey)
            ex /= n
            ey /= n
        if o.map.clearance(me.x + ex * 46.0, me.y + ey * 46.0) < 20.0:
            ex, ey = -ex, -ey
        return (ex, ey)

    # --- движение ---------------------------------------------------------

    def _move(self, o, enemy, me, threat):
        """Желаемое направление, доля газа и «осевое» ли это движение."""
        if self.t < self.escape_until:
            # Выход из клина: назад и в разворот, к стене спиной не едем.
            return cos(me.hull + pi), sin(me.hull + pi), 0.85, False
        if threat is not None:
            # Уклонение: выравниваем корпус по линии ухода, а передачу выбираем
            # по уже набранной скорости, а не по корпусу. Иначе знак тяги и
            # руль начинают спорить друг с другом, и танк замирает на месте —
            # ровно то, чего добивается чужой прицел.
            ex, ey = threat
            sp = hypot(me.vx, me.vy)
            if sp > 25.0:
                c = ex * me.vx + ey * me.vy
            else:
                c = ex * cos(me.hull) + ey * sin(me.hull)
            self.gear = 1 if c >= 0.0 else -1
            self.gear_until = self.t + 0.30
            return ex, ey, 0.95, True
        if enemy is not None:
            self.path = None
            return self._fight_move(o, enemy, me)
        return self._hunt_move(o, me)

    def _fight_move(self, o, enemy, me):
        """Танец вокруг врага: кольцо, маятник передачей, дистанция."""
        dx = enemy.x - me.x
        dy = enemy.y - me.y
        d = hypot(dx, dy)
        ux, uy = dx / d, dy / d
        px, py = -uy, ux
        # ось танца — тангенс к кольцу, чуть наклонённый в нужную сторону
        # по дистанции (наклон работает в обе передачи: вперёд и назад)
        rad = (d - self.STANDOFF) / 150.0
        if rad > 0.7:
            rad = 0.7
        elif rad < -0.7:
            rad = -0.7
        ax, ay = _unit(px + ux * rad, py + uy * rad)
        self._shift_gear(o, d, me, ux, uy)
        return ax, ay, 0.95, True

    def _shift_gear(self, o, d, me, ux, uy):
        """Маятник: вперёд-назад по одной линии со случайной полуволной.

        Передача меняется мгновенно и не требует разворота корпуса — именно
        поэтому уклонение ничего не стоит. Когда дистанция сильно не та,
        передача смещается в нужную сторону чаще, но не подряд: ровный ход
        по прямой — подарок для чужого прицела.
        """
        if self.t < self.gear_until:
            return
        self.rnd = (self.rnd * 1103515245 + 12345) & 0x7FFFFFFF
        u = self.rnd / 2147483647.0
        self.gear_until = self.t + self.GEAR_MIN + (self.GEAR_MAX - self.GEAR_MIN) * u
        # «сближающая» передача: та, у которой нос смотрит в сторону врага
        cg = 1 if (ux * cos(me.hull) + uy * sin(me.hull)) >= 0.0 else -1
        if d > self.D_MAX and u > 0.22:
            self.gear = cg            # издалека в основном подходим
        elif d < self.D_MIN and u > 0.22:
            self.gear = -cg           # в упор в основном отходим
        elif u > 0.5:
            self.gear = -self.gear    # на дистанции — свободный маятник

    def _hunt_move(self, o, me):
        """Поиск: едем по заранее проложенному пути к цели.

        Путь — не жадный шаг на один тайл, а цепочка тайлов от текущей
        клетки до цели. Ведём машину по дальней точке цепочки (загляд на
        два тайла вперёд): так руль не мечется между двумя равнозначными
        обходами и танк не застревает в горле коридора.
        """
        gx, gy = self._search_goal(o, me)
        gx, gy = self._nudge(o, gx, gy)
        gtile = (int(gx // o.map.tile_size), int(gy // o.map.tile_size))
        if self.path is None or self.goal != gtile:
            self._plan_path(o, me, gx, gy)
        path = self.path
        ts = o.map.tile_size
        W = self.W
        if not path:
            ux, uy = _unit(gx - me.x, gy - me.y)
            if ux == 0.0 and uy == 0.0:
                ux, uy = cos(me.hull), sin(me.hull)
            self._gear_to(me, ux, uy)
            return ux, uy, 0.95, False
        # сбились с пути (стена вытолкнула, клин, разворот) — путь строим
        # заново от текущей клетки: гнаться за старым звеном нельзя, оно
        # может быть уже за стеной или позади
        tx = (path[self.path_i] % W + 0.5) * ts
        ty = (path[self.path_i] // W + 0.5) * ts
        if hypot(tx - me.x, ty - me.y) > 70.0:
            self._plan_path(o, me, gx, gy)
            path = self.path
            if not path:
                ux, uy = _unit(gx - me.x, gy - me.y)
                self._gear_to(me, ux, uy)
                return ux, uy, 0.95, False
        # продвигаем указатель: звено считается пройденным у своего центра
        while self.path_i < len(path) - 1:
            tx = (path[self.path_i] % W + 0.5) * ts
            ty = (path[self.path_i] // W + 0.5) * ts
            if hypot(tx - me.x, ty - me.y) < 38.0:
                self.path_i += 1
            else:
                break
        tx = (path[self.path_i] % W + 0.5) * ts
        ty = (path[self.path_i] // W + 0.5) * ts
        if hypot(tx - me.x, ty - me.y) > 80.0:
            self._plan_path(o, me, gx, gy)
        look = len(path) - 1
        for k in range(self.path_i + 1, len(path)):
            tx = (path[k] % W + 0.5) * ts
            ty = (path[k] // W + 0.5) * ts
            if hypot(tx - me.x, ty - me.y) >= 92.0:
                look = k
                break
        lp = path[look]          # именно тайл, а не индекс в списке пути
        wx = (lp % W + 0.5) * ts
        wy = (lp // W + 0.5) * ts
        ux, uy = _unit(wx - me.x, wy - me.y)
        if ux == 0.0 and uy == 0.0:
            ux, uy = _unit(gx - me.x, gy - me.y)
        self._gear_to(me, ux, uy)
        return ux, uy, 0.95, False

    def _gear_to(self, me, ux, uy):
        """Передача поиска с гистерезисом: реже переключаемся — ровнее ход.

        Сзади цель — едем задним ходом, но включаем его, только если цель
        ушла далеко за корму, и возвращаемся на передний, лишь когда она
        уже почти по носу. Без зазора передача дёргалась бы каждый тик.
        """
        err = wrap(atan2(uy, ux) - me.hull)
        if self.gear > 0 and (err > 2.2 or err < -2.2):
            self.gear = -1
        elif self.gear < 0 and -1.2 < err < 1.2:
            self.gear = 1

    def _plan_path(self, o, me, gx, gy):
        """Прокладывает цепочку тайлов от танка к цели по полю BFS."""
        self._build_field(o, gx, gy)
        self.path = None
        self.path_i = 0
        f = self.field
        if f is None:
            return
        ts = o.map.tile_size
        W = self.W
        cx = int(me.x // ts)
        cy = int(me.y // ts)
        if cx < 0 or cy < 0 or cx >= W or cy >= self.H:
            return
        i = cy * W + cx
        if f[i] < 0:
            return
        path = [i]
        guard = 0
        while f[i] > 0 and guard < 512:
            guard += 1
            x = i % W
            y = i // W
            best = -1
            bd = f[i]
            bx = by = 0
            for dx, dy in NB8:
                nx = x + dx
                ny = y + dy
                if nx < 0 or ny < 0 or nx >= W or ny >= self.H:
                    continue
                if dx and dy and (self.blk[y][nx] or self.blk[ny][x]):
                    continue  # угол не срезаем: танк шире точки
                j = ny * W + nx
                dd = f[j]
                if dd < 0 or dd >= bd:
                    continue
                best = j
                bd = dd
                bx, by = nx, ny
            if best < 0:
                break
            path.append(best)
            i = best
        self.path = path

    def _search_goal(self, o, me):
        """Куда идти, когда врага не видно.

        Пока врага нет, танк методично обходит карту по маршруту: чужой
        спавн → свой спавн → центр → четыре угла четвертей. Без маршрута
        два осторожных танка могут простоять весь бой, так и не увидев
        друг друга — на больших открытых картах это чистые ничьи.
        """
        if self.seen and (self.t - self.et) < self.SEARCH_FRESH:
            return self.ex, self.ey
        if self.seen:
            self.seen = False
        if self.sweep is None:
            self._make_sweep(o)
        gx, gy = self.sweep[self.stage]
        if hypot(gx - me.x, gy - me.y) < 130.0:
            self.stage = (self.stage + 1) % len(self.sweep)
            gx, gy = self.sweep[self.stage]
        return gx, gy

    def _make_sweep(self, o):
        """Маршрут поиска: чужой и свой спавн, затем «змейка» по карте.

        Змейка идёт полосами через всю карту: стоящего в углу врага видно,
        когда танк проходит соседнюю полосу. Маршрут из семи отдельных
        точек такой гарантии не даёт — в этом и была причина ничьих с
        кампером на длинных картах.
        """
        w, h = o.map.pixel_width, o.map.pixel_height
        sp = o.map.spawns
        route = [(sp[1 - o.tank].x, sp[1 - o.tank].y),
                 (sp[o.tank].x, sp[o.tank].y),
                 (w * 0.5, h * 0.5)]
        bands = 5
        for i in range(bands):
            y = h * (i + 0.5) / bands
            left, right = 0.12 * w, 0.88 * w
            route.append((right, y) if i % 2 else (left, y))
            route.append((left, y) if i % 2 else (right, y))
        self.sweep = route
        self.stage = 0

    # --- сетка, поле расстояний, шаг по нему ------------------------------

    def _build_grid(self, o):
        m = o.map
        self.rows = m.rows
        self.W = m.width
        self.H = m.height
        self.tsize = m.tile_size
        self.blk = [bytearray(1 if c in "#o:" else 0 for c in row) for row in m.rows]

    def _nudge(self, o, gx, gy):
        """Сдвигает цель к ближайшему проезжему тайлу (центр — в стену)."""
        ts = o.map.tile_size
        gtx = int(gx // ts)
        gty = int(gy // ts)
        if 0 <= gtx < self.W and 0 <= gty < self.H and not self.blk[gty][gtx]:
            return gx, gy
        for r in range(1, 9):
            for oy in range(-r, r + 1):
                for ox in range(-r, r + 1):
                    if ox != -r and ox != r and oy != -r and oy != r:
                        continue
                    tx, ty = gtx + ox, gty + oy
                    if 0 <= tx < self.W and 0 <= ty < self.H and not self.blk[ty][tx]:
                        return (tx + 0.5) * ts, (ty + 0.5) * ts
        return gx, gy

    def _build_field(self, o, gx, gy):
        """BFS от цели: поле расстояний по тайлам. Считается редко."""
        ts = o.map.tile_size
        W, H = self.W, self.H
        gtx = int(gx // ts)
        gty = int(gy // ts)
        if gtx < 0 or gty < 0 or gtx >= W or gty >= H:
            return
        if self.blk[gty][gtx]:
            return
        if self.goal == (gtx, gty) and self.field is not None:
            # цель та же: поле пересчитываем только если наш тайл недостижим
            cx = int(o.me.x // ts)
            cy = int(o.me.y // ts)
            if 0 <= cx < W and 0 <= cy < H and self.field[cy * W + cx] >= 0 \
                    and self.t - self.field_tick < 1.5:
                return
        self.goal = (gtx, gty)
        self.field_tick = self.t
        blk = self.blk
        dist = [-1] * (W * H)
        start = gty * W + gtx
        dist[start] = 0
        q = [start]
        qi = 0
        while qi < len(q):
            i = q[qi]
            qi += 1
            x = i % W
            y = i // W
            nd = dist[i] + 1
            for dx, dy in NB8:
                nx = x + dx
                ny = y + dy
                if nx < 0 or ny < 0 or nx >= W or ny >= H:
                    continue
                if blk[ny][nx]:
                    continue
                if dx and dy and (blk[y][nx] or blk[ny][x]):
                    continue  # угол не срезаем: танк шире точки
                j = ny * W + nx
                if dist[j] < 0:
                    dist[j] = nd
                    q.append(j)
        self.field = dist

    # --- низкий уровень: стены и руль -------------------------------------

    def _fan_score(self, m, me, ang):
        """Насколько свободен луч: минимум зазора по четырём пробам.

        Минимум, а не сумма: путь перекрывает самая близкая стена, и
        именно она должна решать. Провал зазора ниже габарита танка
        штрафуется сразу и сильно — иначе руль ведёт в щель, куда
        корпус не влезает.
        """
        if m.clearance(me.x + cos(ang) * 34.0, me.y + sin(ang) * 34.0) < 24.0:
            return -1000.0        # прямо сейчас тут не пролезть
        c = 1e9
        for d in (66.0, 110.0, 170.0):
            v = m.clearance(me.x + cos(ang) * d, me.y + sin(ang) * d)
            if v < c:
                c = v
        return c

    def _drive(self, o, me, ux, uy, speed, axis):
        """Обходит стены веером проб и превращает направление в (turn, drive).

        ``axis`` — желаемое направление задано линией, а не вектором: тогда
        руль выравнивает корпус по линии, а куда ехать — вперёд или назад —
        решает передача. Это и есть маятник: смена знака тяги разворачивает
        движение, не трогая корпус.
        """
        m = o.map
        # прижались к стене — добавляем отталкивание от неё
        if m.clearance(me.x, me.y) < 26.0:
            a = m.wall_dir(me.x, me.y) * pi / 180.0
            ux -= cos(a) * 0.9
            uy -= sin(a) * 0.9
            ux, uy = _unit(ux, uy)
            if ux == 0.0 and uy == 0.0:
                ux, uy = cos(me.hull), sin(me.hull)
        base = atan2(uy, ux)
        best_k = 0
        # Веер проб включаем, только если прямо по курсу тесно: в чистом
        # поле он не нужен, а лишние повороты стоят скорости и сбивают
        # прицел. Память о прошлом секторе не даёт рулю метаться между
        # двумя равнозначными проходами.
        if m.clearance(me.x + cos(base) * 42.0, me.y + sin(base) * 42.0) < 26.0:
            best_k = 0
            best_score = self._fan_score(m, me, base)
            for k in (-2, -1, 1, 2):
                sc = self._fan_score(m, me, base + k * 0.22) - abs(k) * 5.0
                if k == self.prev_k:
                    sc += 6.0
                if sc > best_score:
                    best_score = sc
                    best_k = k
            self.prev_k = best_k
            if best_k:
                base += best_k * 0.22
                ux, uy = cos(base), sin(base)
        if axis:
            # линия: руль выравнивает корпус, знак тяги решает передача
            err = _fold(wrap(base - me.hull))
        else:
            # направление: рулим на ту сторону, куда едет танк с этой передачей
            vel = me.hull if self.gear > 0 else me.hull + pi
            err = wrap(base - vel)
        turn = clamp(err * 2.0, -self.TURN_CAP, self.TURN_CAP)
        mag = turn if turn >= 0.0 else -turn
        drive = self.gear * speed * (1.0 - 0.8 * mag)
        if drive > -0.12 and drive < 0.12:
            drive = 0.12 * self.gear
        self.drive_dbg = (ux, uy, best_k, speed, axis, turn, drive, me.hull)
        return turn, drive


program = Brain()
