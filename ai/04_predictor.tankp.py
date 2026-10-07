#!TANKP 1
# name: Стрелок
# author: TANKSIM
# difficulty: 4
# color: #42a5f5
# description: Держит среднюю дистанцию, стреляет с упреждением и ждёт момента, когда удар придётся в борт или корму.
# tags: огонь,упреждение,манёвр

from math import atan2, cos, degrees, hypot, pi, radians, sin

from tankp import TankProgram, Action

def angle_between(a, b):
    return degrees((a - b + pi) % (2 * pi) - pi)


def lead_point(o, enemy):
    """Куда стрелять, чтобы снаряд пришёл туда же, куда поедет враг.

    Делегировано SDK: там время полёта считается от ствола и уточняется
    двумя итерациями, а здесь была одна итерация от центра танка — на
    встречном ходу упреждение недобивало примерно на треть.
    """
    return o.lead(enemy)


def impact_face(o, enemy):
    """Какая грань окажется под ударом: 0 — лоб, 90 — борт, 180 — корма."""
    aim = lead_point(o, enemy)
    los = atan2(aim[1] - o.me.y, aim[0] - o.me.x)
    return abs(angle_between(enemy.hull, los))


class Brain(TankProgram):
    """Работает на дистанции и стреляет только по открытой грани."""

    MIN_DIST = 240.0
    MAX_DIST = 420.0
    STRICT = 0.85

    def on_start(self, ctx):
        self.last_seen = None

    def on_tick(self, o):
        me = o.me
        if not o.enemy:
            # Обзор держим в одну сторону: раньше башня каждые 90 тиков
            # разворачивалась и уходила обратно, не проходя полный круг.
            if self.last_seen:
                turret = o.aim_turret(*self.last_seen)
                if me.dist_to(self.last_seen) > 50.0:
                    turn, drive = o.steer_to(*self.last_seen)
                    return Action(drive=drive * 0.7, turn=turn, turret=turret)
            # На месте, а врага нет — идём к его спавну, иначе стопоримся.
            self.last_seen = None
            turn, drive = o.steer_to(*o.map.spawns[1 - o.tank])
            return Action(drive=drive * 0.6, turn=turn, turret=0.5)

        self.last_seen = (o.enemy.x, o.enemy.y)
        enemy = o.enemy
        d = me.dist_to(enemy)
        aim = lead_point(o, enemy)
        face = impact_face(o, enemy)
        # Ведение и стрельба с упреждением; `fire` значит попадание по
        # геометрии, а не «7 градусов» (на 400 px это 49 px промаха).
        turret, fire = o.aim_and_fire(o.enemy, strict=self.STRICT)

        # Грань под ударом: 60..150 — борт и корма, там урон в разы выше.
        good_face = 55.0 < face < 150.0

        clear = o.map.blocked_between(me.x, me.y, aim[0], aim[1]) is False
        if fire and clear:
            # Стреляем всегда, когда ствол смотрит на цель: удар в лоб слабый,
            # но лучше слабого попадания, чем очередь в молоко.
            return Action(drive=0.0 if good_face else -0.35, turn=0.0,
                          turret=turret, fire=True)

        # Не выгодно — маневрируем, меняя ракурс.
        move = 0.0
        turn = 0.0
        if d < self.MIN_DIST:
            move, turn = -0.8, 0.0
        elif d > self.MAX_DIST:
            turn, move = o.steer_to(enemy.x, enemy.y)
        else:
            # Идём по касательной: разворачиваемся вокруг врага, меняя угол удара.
            side = 1.0 if (o.tick // 90) % 2 == 0 else -1.0
            cx = enemy.x - cos(enemy.hull) * 300.0 - side * sin(enemy.hull) * 260.0
            cy = enemy.y - sin(enemy.hull) * 300.0 + side * cos(enemy.hull) * 260.0
            turn, move = o.steer_to(cx, cy, tol=50.0)

        return Action(drive=move, turn=turn, turret=turret, fire=False)


program = Brain()