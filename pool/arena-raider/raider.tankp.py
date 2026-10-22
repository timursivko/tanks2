#!TANKP 1
# name: Raider
# author: arena-raider
# difficulty: 5
# description: Модельный агент. Держит ракурс 34 градуса к оси прихода
#   снаряда (с поправкой на упреждение, которое противник берёт по моей
#   скорости), тратит выстрел только когда настоящая геометрия брони —
#   оболочечный луч по OBB и пороги рикошета по граням — даёт пробой при
#   любом довороте чужого корпуса за время полёта. Дистанцию держит по
#   чужому КД: пока он заряжен — ракурс и разрыв, пока перезаряжается —
#   рывок в упор, где окно пробоя шире всего.
"""
Три механизма, на которых держится танк.

1. Броня. Пробитие зависит не от дистанции, а от угла между снарядом и
   нормалью грани. У лба порог 30 градусов, у борта 50, у кормы порога нет.
   Поэтому «ракурс» 34-35 градусов к линии прихода — единственная поза, где
   и лоб (34 > 30), и борт (56 > 50) держат удар; отсюда же следует, что
   пробить ракурсного противника можно только в упор, где разброс углов
   выстрела шире окна.

2. Упреждение. Противник целится не в меня, а в мою будущую точку, поэтому
   ось угрозы — не линия визирования: на 400 px упреждение уводит её на
   15-20 градусов, и ракурс, построенный по визированию, проседает до
   25 градусов, то есть ровно в зону пробоя. Опорная ось считается по моей
   же скорости с временем полёта (плюс ствол противника, если он смотрит
   почти на меня — тогда упреждение уже учтено им).

3. Огонь. Угол ствола ищется перебором: оболочечный луч от дула проверяется
   по OBB цели, угол удара считается по нормали реально задетой грани, а
   гипотезы о курсе цели раздвинуты на её измеренную скорость доворота за
   время полёта. Стреляем только если пробивают все гипотезы — выстрел
   стоит секунды перезарядки, и промах по броне дороже паузы.
"""
from math import atan2, cos, degrees, fabs, hypot, pi, sin, sqrt

from tankp import Action, TankProgram

TAU = pi * 2.0
DT = 1.0 / 60.0

SPEED_F = 190.0
SPEED_R = 120.0
HULL_RATE = 2.6
TURRET_RATE = 3.6
BULLET = 620.0
MUZZLE = 26.0
HFX = 20.0
HFY = 14.0
FRONT_HALF = 35.0
RICO_FRONT = 30.0
RICO_SIDE = 50.0
RICO_REAR = 60.0

D = pi / 180.0

# --- настройки --------------------------------------------------------------
RICO = 34.0
SIDE_HYST = 6.0
FIRE_MARGIN = 1.0
KEEP_LO = 130.0
KEEP_HI = 200.0
RUSH_D = 38.0
HULL_SAMPLES = 5
PEN_MIN = 0.75       # доля гипотез курса, при которой стреляем
TURRET_GATE = 0.45   # когда ракурс считаем от ствола противника
FIRE_D = 700.0       # ближе этого стреляем
RETREAT = -1.0       # радиальная доля при отступлении
HUNT_NAKED = 1       # 1 = на сближении разворачиваться носом на цель
RAKE0 = 35.0         # страховочный ракурс

# настройки захвата


def wrap(a):
    a = (a + pi) % TAU - pi
    return a


def clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def ray_obb(ox, oy, dx, dy, cx, cy, ang, hx=HFX, hy=HFY, max_t=1e9):
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
    if -1e-12 < ldx < 1e-12:
        if lx > hx or lx < -hx:
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
    if -1e-12 < ldy < 1e-12:
        if ly > hy or ly < -hy:
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
        if fabs(ldx) >= fabs(ldy):
            n0 = -1.0 if ldx > 0.0 else 1.0
            n1 = 0.0
        else:
            n0 = 0.0
            n1 = -1.0 if ldy > 0.0 else 1.0
        return 0.0, n0 * ca - n1 * sa, n0 * sa + n1 * ca
    if axis == 0:
        n0, n1 = sign, 0.0
    else:
        n0, n1 = 0.0, sign
    return tmin, n0 * ca - n1 * sa, n0 * sa + n1 * ca


def pen_score(nx, ny, dx, dy, hull, margin=FIRE_MARGIN):
    c = -(dx * nx + dy * ny)
    if c < 0.0:
        c = 0.0
    elif c > 1.0:
        c = 1.0
    theta = degrees(atan2(sqrt(1.0 - c * c), c))
    rel = degrees(wrap(atan2(ny, nx) - hull))
    a = fabs(rel)
    if a <= FRONT_HALF:
        lim = RICO_FRONT
    elif a >= 180.0 - FRONT_HALF:
        lim = RICO_REAR
    else:
        lim = RICO_SIDE
    return 1 if theta < lim - margin else 0


class Brain(TankProgram):
    def __init__(self):
        self.ready = False
        self.en = None
        self.miss = 99.0
        self.mode = "hunt"
        self.t = 0.0
        self.side = 1.0
        self.last_hull = None
        self.hull_w = 0.0
        self.dbg = None

    # --- карта ------------------------------------------------------------
    def _init_map(self, mv):
        self.tile = mv.tile_size
        self.rows = mv.rows
        self.mw = mv.width
        self.mh = mv.height
        self.ready = True

    def _opaque(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.mh or tx >= self.mw:
            return True
        return self.rows[ty][tx] in "#o"

    def _blocked(self, x, y):
        tx = int(x // self.tile)
        ty = int(y // self.tile)
        if tx < 0 or ty < 0 or ty >= self.mh or tx >= self.mw:
            return True
        return self.rows[ty][tx] in "#o:"

    def los(self, x0, y0, x1, y1):
        dx = x1 - x0
        dy = y1 - y0
        d = hypot(dx, dy)
        if d < 1e-6:
            return True
        ux = dx / d
        uy = dy / d
        t = 0.0
        while t < d:
            t += 12.0
            if self._opaque(x0 + ux * t, y0 + uy * t):
                return False
        return True

    def free_dist(self, x, y, ang, maxd, margin=13.0):
        c = cos(ang)
        s = sin(ang)
        nx = -s * margin
        ny = c * margin
        d = 0.0
        while d < maxd:
            d += 12.0
            px = x + c * d
            py = y + s * d
            if self._blocked(px, py) or self._blocked(px + nx, py + ny) \
                    or self._blocked(px - nx, py - ny):
                return d - 12.0
        return maxd

    # --- оценка выстрела --------------------------------------------------
    def shot_eval(self, me, ex, ey, hulls, dist, ang):
        """(доля пробивающих гипотез, попадание) для ствола в направлении ang."""
        mx = me.x + cos(ang) * MUZZLE
        my = me.y + sin(ang) * MUZZLE
        dx = cos(ang)
        dy = sin(ang)
        max_t = dist + 90.0
        n = 0
        ok = 0
        for hull in hulls:
            res = ray_obb(mx, my, dx, dy, ex, ey, hull, max_t=max_t)
            if res is None:
                return 0.0, 0
            n += 1
            if pen_score(res[1], res[2], dx, dy, hull):
                ok += 1
        return ok / n, 1

    def pick_aim(self, me, ex, ey, dist, hulls):
        """Угол ствола: ближайший к текущему из дающих пробой, иначе лучший."""
        base = atan2(ey - me.y, ex - me.x)
        cur_off = wrap(me.turret - base)
        step = 0.05 if dist < 60 else (0.032 if dist < 130 else 0.022)
        n = 11 if dist < 60 else (8 if dist < 130 else 6)
        scans = []
        for k in range(-n, n + 1):
            a = base + k * step
            mx = me.x + cos(a) * MUZZLE
            my = me.y + sin(a) * MUZZLE
            if self._blocked(mx, my) or not self.los(mx, my, ex, ey):
                scans.append((k * step, -1.0))
                continue
            pen, hit = self.shot_eval(me, ex, ey, hulls, dist, a)
            scans.append((k * step, pen if hit else -1.0))
        # 1) ближайший к текущему стволу угол, который пробивает
        best = None
        bd = 1e9
        for off, pen in scans:
            if pen >= PEN_MIN:
                dd = fabs(wrap(off - cur_off))
                if dd < bd:
                    bd = dd
                    best = off
        if best is not None:
            return base + best
        # 2) иначе — максимум пробоя, при равенстве ближе к стволу
        bkey = None
        for off, pen in scans:
            if pen < 0.0:
                continue
            key = (round(pen, 2), -fabs(wrap(off - cur_off)))
            if bkey is None or key > bkey:
                bkey = key
                best = off
        return base + (best or 0.0)

    # --- основной тик ------------------------------------------------------
    def on_tick(self, o):
        me = o.me
        if not self.ready:
            if o.map is None:
                return Action()
            self._init_map(o.map)
        dt = o.dt
        self.t += dt

        en = o.enemy
        seen = en is not None
        if seen:
            if self.last_hull is not None:
                self.hull_w = wrap(en.hull - self.last_hull) / dt
            self.last_hull = en.hull
            self.en = (en.x, en.y, en.hull, en.turret, en.vx, en.vy, en.cooldown)
            self.miss = 0.0
        else:
            self.miss += dt
        if self.en is None:
            return Action(drive=0.8, turn=0.1)

        ex, ey, ehull, eturret, evx, evy, ecd = self.en
        if not seen:
            t = self.miss
            k = max(0.0, 1.0 - t / 0.5)
            ex += evx * t * k
            ey += evy * t * k
            ecd = max(0.0, ecd - t)
            eturret = eturret + pi

        dx = ex - me.x
        dy = ey - me.y
        dist = hypot(dx, dy)
        bearing = atan2(dy, dx)
        my_ready = me.ammo_ready

        # --- режим
        if self.miss > 0.30:
            mode = "hunt"
        elif my_ready and ecd > 0.30 + dist / 800.0:
            mode = "rush"
        elif (not my_ready) and dist < KEEP_LO:
            mode = "retreat"
        else:
            mode = "guard"
        self.mode = mode

        # --- ось угрозы: куда реально полетит его снаряд.
        # Он целится с упреждением на мою скорость, поэтому ось — не линия
        # визирования, а линия «противник -> моя будущая точка». На 400 px
        # упреждение уводит её на 15-20 градусов.
        t_lead = dist / BULLET
        pmx = me.x + me.vx * t_lead
        pmy = me.y + me.vy * t_lead
        ref = atan2(pmy - ey, pmx - ex) + pi
        if fabs(wrap(eturret - (ref + pi))) < TURRET_GATE:
            ref = eturret + pi
        c1 = ref + RICO * D
        c2 = ref - RICO * D
        d1 = fabs(wrap(c1 - me.hull))
        d2 = fabs(wrap(c2 - me.hull))
        if d1 < d2 - SIDE_HYST * D:
            self.side = 1.0
        elif d2 < d1 - SIDE_HYST * D:
            self.side = -1.0
        hull_want = ref + self.side * RICO * D
        radial = 0.0
        if mode == "rush":
            radial = 1.0 if dist > RUSH_D else 0.0
        elif mode == "hunt":
            if HUNT_NAKED:
                hull_want = bearing
            radial = 1.0
        elif mode == "retreat":
            radial = RETREAT
        else:
            if dist > KEEP_HI:
                radial = 0.9
            elif dist < KEEP_LO:
                radial = -0.7
            else:
                radial = 0.0

        # --- стены
        if radial >= 0:
            if self.free_dist(me.x, me.y, hull_want, 70.0) < 30.0:
                bestc = -1.0
                besta = hull_want
                for k in range(-7, 8):
                    a = hull_want + k * 0.22
                    c = self.free_dist(me.x, me.y, a, 70.0) - fabs(k) * 1.5
                    if c > bestc:
                        bestc = c
                        besta = a
                hull_want = besta
                if bestc < 24.0:
                    radial = -0.8
        else:
            if self.free_dist(me.x, me.y, hull_want + pi, 50.0) < 26.0:
                radial = 0.0
                if self.free_dist(me.x, me.y, hull_want, 45.0) < 26.0:
                    for k in range(1, 8):
                        done = False
                        for s in (1.0, -1.0):
                            a = hull_want + s * k * 0.3
                            if self.free_dist(me.x, me.y, a, 70.0) > 50.0:
                                hull_want = a
                                done = True
                                break
                        if done:
                            break

        want = radial * (SPEED_F if radial >= 0 else SPEED_R)
        err = wrap(hull_want - me.hull)
        # упреждение по вращению опорной оси: без него корпус отстаёт и ракурс
        # проседает до 25-28 градусов — ровно в зону пробоя
        rate = clamp(err / DT, -HULL_RATE, HULL_RATE)
        g = fabs(rate) / HULL_RATE
        speed_frac = min(1.0, fabs(want) / SPEED_F)
        f = speed_frac if g <= 0.02 else min(speed_frac, 1.0 - g)
        drive = f if want >= 0 else -f
        turn = g if rate >= 0 else -g
        if fabs(want) < 1.0:
            drive = 0.0

        # --- ствол и выстрел
        tf = dist / BULLET
        px = ex + evx * tf
        py = ey + evy * tf
        phull = ehull + self.hull_w * tf * 0.5
        span = min(HULL_RATE, fabs(self.hull_w) + 1.2) * tf
        hulls = [phull + span * (2.0 * i / (HULL_SAMPLES - 1) - 1.0)
                 for i in range(HULL_SAMPLES)]
        if dist < FIRE_D and self.miss < 0.30:
            aim = self.pick_aim(me, px, py, dist, hulls)
        else:
            aim = bearing
        turret_cmd = clamp(wrap(aim - me.turret) / (TURRET_RATE * DT), -1.0, 1.0)
        fire = False
        if my_ready and dist < FIRE_D and self.miss < 0.30:
            fired = me.turret + turret_cmd * TURRET_RATE * DT
            pen, hit = self.shot_eval(me, px, py, hulls, dist, fired)
            fire = hit and pen >= PEN_MIN
        return Action(drive=drive, turn=turn, turret=turret_cmd, fire=fire)


program = Brain()
