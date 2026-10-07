#!TANKP 1
# name: Черепаха
# author: TANKSIM
# difficulty: 2
# color: #8d6e63
# description: Почти не двигается: лобовая броня всегда развёрнута к врагу, урон наносит только когда безопасный угол.
# tags: оборона,броня,пассивный

from math import atan2, cos, degrees, pi, radians, sin

from tankp import TankProgram, Action


def angle_between(a, b):
    return degrees((a - b + pi) % (2 * pi) - pi)


class Brain(TankProgram):
    """Максимум брони: стоим, доворачиваемся носом, стреляем редко."""

    # Черепаха целится строже всех, но не настолько, чтобы мазать:
    # 0.45 даёт примерно половину гарантированного допуска попадания.
    STRICT = 0.45
    TURN_TOLERANCE = 12.0   # насколько корпус должен быть «носом» к врагу
    ROAM_AFTER = 7 * 60     # тиков тишины до поиска: бой длится 25 секунд,
                            # при 14 секундах ожидания мы просто стояли в углу
                            # и ни разу не видели противника.

    def on_start(self, ctx):
        self.patience = 0
        self.spot = None        # точка, к которой ползём, когда врага не видно
        self.last_seen = None   # где видели врага: башня остаётся наведенной

    def on_tick(self, o):
        me = o.me

        if not o.enemy:
            self.patience += 1
            # Башня не крутится меандром: доезжает до последней точки
            # и стоит, пока мы ищем противника.
            turret = o.aim_turret(*self.last_seen) if self.last_seen else 0.0
            # Стоять можно недолго: иначе бой уходит в ничью по таймауту.
            if self.patience > self.ROAM_AFTER:
                if self.spot is None or me.dist_to(self.spot) < 60.0:
                    self.spot = self._next_spot(o)
                turn, drive = o.steer_to(*self.spot, tol=50.0)
                return Action(drive=drive * 0.55, turn=turn * 0.7, turret=turret)
            # Ничего не делаем: экономит время решения и не выдаёт позиции.
            return Action(turret=turret)

        # Черепаха не спешит, но снаряды летают: целимся с упреждением.
        self.last_seen = (o.enemy.x, o.enemy.y)
        turret, fire = o.aim_and_fire(o.enemy, strict=self.STRICT)
        hull_err = me.hull_error(o.enemy)
        d = me.dist_to(o.enemy)

        # Разворачиваем корпус лбом к врагу: в лоб урон проходит втрое слабее.
        if abs(hull_err) > self.TURN_TOLERANCE:
            return Action(drive=0.12 if abs(hull_err) < 90 else -0.12,
                          turn=o.sign(hull_err) * 0.8,
                          turret=turret,
                          fire=False)

        # Враг близко — отступаем, не теряя разворота.
        if d < 200.0:
            away = (me.x - o.enemy.x, me.y - o.enemy.y)
            turn, drive = o.steer_to(me.x + away[0] * 4, me.y + away[1] * 4)
            return Action(drive=-0.6, turn=turn * 0.5, turret=turret,
                          fire=fire and d < 140)

        return Action(drive=0.0, turn=0.0, turret=turret, fire=fire)


    def _next_spot(self, o):
        """Куда ползти, когда врага не видно.

        Раньше точка выбиралась как «соседняя у стены» — то есть шаг вдоль
        стены от текущей позиции. На практике это уводило черепаху всё
        дальше от противника: на ``arena`` спавны разнесены на 1160 px, а
        радиус обзора 760 px, и оба танка так и не увидели друг друга —
        ноль выстрелов за весь бой. Теперь идём к чужому спавну: это
        единственная точка, о которой мы точно знаем, что враг от неё
        ушёл, но она же — кратчайший путь к пересечению.
        """
        sx, sy = o.map.spawns[1 - o.tank]
        return (sx, sy)


program = Brain()