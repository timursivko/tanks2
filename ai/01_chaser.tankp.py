#!TANKP 1
# name: Погоня
# author: TANKSIM
# difficulty: 1
# color: #ff7043
# description: Рвётся навстречу врагу и стреляет, пока видит. Пропавшего противника ищет по последней замеченной точке.
# tags: агрессивный,ближний,простой

from math import atan2, degrees

from tankp import TankProgram, Action


def angle_between(a, b):
    """Знаковая разность углов в градусах: положительное — вправо по часовой."""
    return degrees((a - b + 3.141592653589793) % (2 * 3.141592653589793) - 3.141592653589793)


def best_aim(o, enemy, side=92.0):
    """Выбирает точку на корпусе врага, куда выгоднее всего стрелять.

    Враг смотрит в лоб — бьём в борт (брони втрое меньше).
    Видим корму — бьём в корму.
    Иначе целимся в центр: корпус и так поперёк линии огня.
    """
    to_me = atan2(o.me.y - enemy.y, o.me.x - enemy.x)
    facing = abs(angle_between(enemy.hull, to_me))
    if facing < 45.0:
        return (enemy.x - enemy.forward[0] * side,
                enemy.y - enemy.forward[1] * side)
    if facing > 135.0:
        return enemy.rear_point(8.0)
    return enemy


class Brain(TankProgram):
    """Самая честная тактика: ехать к врагу, доворачивать башню, стрелять."""

    # Насколько строго требовать попадания. 1.0 — максимум; раньше здесь стояло
    # «9 градусов», но на 400 px это 63 px промаха при цели шириной 34 px,
    # то есть гарантированный маз втрое больше цели.
    STRICT = 0.8

    def on_start(self, ctx):
        self.last_seen = None     # где видели врага в последний раз
        self.lost_ticks = 0
        self.scan_dir = 1.0       # знак поиска: выбирается один раз и не меняется

    def on_tick(self, o):
        me = o.me

        if o.enemy:
            self.last_seen = (o.enemy.x, o.enemy.y)
            self.lost_ticks = 0
            # Целимся с упреждением: иначе половина снарядов уходит в хвост.
            turret, fire = o.aim_and_fire(best_aim(o, o.enemy), strict=self.STRICT)
            hull_err = me.hull_error(o.enemy)
            return Action(
                drive=0.95 if abs(hull_err) < 1.1 else 0.35,
                turn=o.sign(hull_err),
                turret=turret,
                fire=fire and abs(hull_err) < 55.0,
            )

        # Контакта нет: едем в последнее известное место и там ищем.
        self.lost_ticks += 1
        if self.last_seen is None:
            turn, drive = o.steer_to(*o.map.spawns[1 - o.tank])
            return Action(drive=drive * 0.7, turn=turn, turret=self._scan(o))
        if me.dist_to(self.last_seen) < 50.0:
            # Мы на месте, а врага нет — иначе будем стоять тут до таймаута.
            self.last_seen = None
        turn, drive = o.steer_to(*self.last_seen) if self.last_seen else (0.0, 0.7)
        return Action(drive=drive * 0.8, turn=turn, turret=self._scan(o))

    def _scan(self, o):
        """Обзор без меандра.

        Раньше знак менялся каждые 45 тиков: башня доходила до ~90° вправо и
        возвращалась в исходную точку, ни разу не проходя полный круг. Теперь
        направление выбирается один раз и держится, а если есть последняя
        известная позиция — башня просто доводится до неё и стоит.
        """
        if self.last_seen:
            return o.aim_turret(*self.last_seen)
        return self.scan_dir


program = Brain()