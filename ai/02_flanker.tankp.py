#!TANKP 1
# name: Фланкер
# author: TANKSIM
# difficulty: 3
# color: #ab47bc
# description: Не лезет в лоб: обходит врага и выходит на его корму, откуда урон максимальный.
# tags: тактика,фланг,обход

from math import atan2, degrees, pi, sin, cos

from tankp import TankProgram, Action


def angle_between(a, b):
    return degrees((a - b + pi) % (2 * pi) - pi)


class Brain(TankProgram):
    """Держит дистанцию, пока не обойдёт врага с тыла."""

    STRICT = 0.8
    DESIRED = 170.0        # сколько хотим отойти за корму врага

    def on_start(self, ctx):
        self.target = None     # куда идём в этот момент
        self.lost = 0
        self.scan_dir = 0.6    # знак обзора держим, а не меняем каждые 50 тиков

    def on_tick(self, o):
        me = o.me

        if o.enemy:
            self.lost = 0
            # Точка за кормой врага — самое вкусное место на карте.
            goal = o.enemy.rear_point(self.DESIRED)

            d = me.dist_to(o.enemy)
            # Уже почти в тылу: стоп, доворачиваем башню и бьём.
            if d < self.DESIRED * 0.75:
                self.target = None
                turn_err = me.hull_error(o.enemy.rear_point(-400.0))
                spot = o.enemy.rear_point(4.0) if _sees_rear(o, o.enemy) else o.enemy
                turret, fire = o.aim_and_fire(spot, strict=self.STRICT)
                return Action(
                    drive=-0.25,
                    turn=o.sign(turn_err) * 0.5,
                    turret=turret,
                    fire=fire,
                )

            # Подходим к точке за спиной.
            self.target = goal
            turn, drive = o.steer_to(goal.x, goal.y, tol=30.0)
            # Башня всё время ведёт врага: враг может вывернуться.
            # Огонь ведём на ходу — иначе до тыла так и не дойдём.
            turret, fire = o.aim_and_fire(o.enemy, strict=self.STRICT)
            return Action(drive=drive, turn=turn, turret=turret, fire=fire)

        self.lost += 1
        goal = self.target
        if goal is not None and me.dist_to(goal) < 50.0:
            # Точка за кормой оказалась пустой: ищем заново, иначе стоим.
            self.target = None
            goal = None
        if goal is None:
            turn, drive = o.steer_to(*o.map.spawns[1 - o.tank])
            return Action(drive=drive * 0.6, turn=turn, turret=self._scan(o))
        turn, drive = o.steer_to(goal.x, goal.y)
        return Action(drive=drive * 0.9, turn=turn, turret=self._scan(o))

    def _scan(self, o):
        """Обзор без меандра: направление держим, до точки доворачиваем."""
        if self.target is not None:
            return o.aim_turret(self.target.x, self.target.y)
        return self.scan_dir


def _sees_rear(o, enemy):
    """Находимся ли мы в секторе за кормой врага."""
    to_us = atan2(o.me.y - enemy.y, o.me.x - enemy.x)
    return abs(angle_between(enemy.hull, to_us)) > 120.0


program = Brain()