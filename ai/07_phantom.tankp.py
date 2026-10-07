#!TANKP 1
# name: Призрак
# author: TANKSIM
# difficulty: 4
# color: #7e57c2
# description: Прячется за стенами, рвёт контакт, а потом бьёт в спину, когда враг потерял нас из виду.
# tags: скрытность,контрбой,фланг

from math import atan2, cos, degrees, pi, sin

from tankp import TankProgram, Action


def angle_between(a, b):
    return degrees((a - b + pi) % (2 * pi) - pi)


class Brain(TankProgram):
    """Работает отрывами: ломает линию огня, прячется, бьёт из-за угла."""

    HIDE_RANGE = 260.0      # ближе этого — считаем, что нас видят
    STRICT = 0.8

    def on_start(self, ctx):
        self.hiding = False
        self.cover = None      # точка у стены, где ждём
        self.patience = 0
        self.scan_dir = 0.6    # знак обзора держим, а не меняем циклически
        self.last_seen = None  # где видели врага: башня остаётся наведённой

    def on_tick(self, o):
        me = o.me

        if not o.enemy:
            self.patience += 1
            return self._wait(o)

        self.patience = 0
        self.last_seen = (o.enemy.x, o.enemy.y)
        d = me.dist_to(o.enemy)
        turret, fire = o.aim_and_fire(o.enemy, strict=self.STRICT)
        # Смотрит ли враг в нашу сторону? Если нет — можно спокойно бить.
        to_us = atan2(me.y - o.enemy.y, me.x - o.enemy.x)
        enemy_faces_us = abs(angle_between(o.enemy.hull, to_us)) < 75.0

        if d < self.HIDE_RANGE and enemy_faces_us:
            # Нас видят и мы близко — рвём контакт перпендикулярно линии огня.
            self.hiding = True
            self.cover = self._cover_point(o)
            los = atan2(o.enemy.y - me.y, o.enemy.x - me.x)
            side = 1.0 if (o.tick // 40) % 2 == 0 else -1.0
            bx = me.x + cos(los + pi / 2) * side * 260.0
            by = me.y + sin(los + pi / 2) * side * 260.0
            turn, drive = o.steer_to(bx, by)
            return Action(drive=drive * 0.85, turn=turn,
                          turret=turret, fire=False)

        self.hiding = False
        # Враг смотрит в другую сторону или далеко: выбираем борт/корму и бьём.
        to_me = atan2(me.y - o.enemy.y, me.x - o.enemy.x)
        facing = abs(angle_between(o.enemy.hull, to_me))
        if facing < 45.0:
            aim = (o.enemy.x - o.enemy.forward[0] * 90.0,
                   o.enemy.y - o.enemy.forward[1] * 90.0)
        elif facing > 135.0:
            aim = o.enemy.rear_point(8.0)
        else:
            aim = o.enemy

        # Выбранную броневую точку отдаём SDK: он сам считает упреждение
        # с учётом позиции ствола и стреляет только при реальном попадании.
        turret, fire = o.aim_and_fire(aim, strict=self.STRICT)
        # Немного стыкуем вбок, чтобы враг не попал в лоб.
        turn, drive = o.steer_to(o.enemy.x, o.enemy.y, tol=300.0)
        return Action(drive=drive * 0.35, turn=turn * 0.4,
                      turret=turret,
                      fire=fire)

    HUNT_AFTER = 12 * 60      # тиков без контакта — идём охотиться сами

    def _wait(self, o):
        """Контакта нет: отсиживаемся, а если ждать долго — идём искать."""
        if self.patience > self.HUNT_AFTER:
            # Поздно ждать: идём к спавну противника и ищем визуально.
            self.cover = None
            sx, sy = o.map.spawns[1 - o.tank]
            turn, drive = o.steer_to(sx, sy, tol=120.0)
            return Action(drive=drive * 0.8, turn=turn, turret=self.scan_dir)
        if self.cover is None:
            self.cover = self._cover_point(o)
        turn, drive = o.steer_to(*self.cover, tol=40.0)
        # Башня доезжает до последней известной точки и стоит. Раньше знак
        # переключался каждые 40/80 тиков: башня уходила вправо и
        # возвращалась, не проходя полный круг.
        turret = o.aim_turret(*self.last_seen) if self.last_seen else self.scan_dir
        return Action(drive=drive * 0.6, turn=turn * 0.7, turret=turret)

    def _cover_point(self, o):
        """Точка в 70 пикселях ОТ ближайшей стены: за её углом нас не видно."""
        wall = o.map.wall_dir(o.me.x, o.me.y)
        a = wall * pi / 180.0
        return (o.me.x + cos(a) * 70.0, o.me.y + sin(a) * 70.0)


program = Brain()