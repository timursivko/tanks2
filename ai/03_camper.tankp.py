#!TANKP 1
# name: Кампер
# author: TANKSIM
# difficulty: 3
# color: #26a69a
# description: Спиной к стене ждёт врага в узком секторе. Приближение противника заставляет отступать.
# tags: оборона,засада,углы

from math import atan2, cos, degrees, pi, radians, sin

from tankp import TankProgram, Action


def angle_between(a, b):
    return degrees((a - b + pi) % (2 * pi) - pi)


class Brain(TankProgram):
    """Занимает угол и стреляет только когда враг подходит."""

    SECTOR = 55.0        # полусектор, в котором мы готовы принять бой
    PANIC = 190.0        # ближе этого — отходим
    STRICT = 0.8
    RELOCATE_AFTER = 7 * 60     # тиков тишины до смены угла. Бой длится
                               # 25 секунд, а раньше ожидание было
                               # 15 секунд: за это время мы успевали только
                               # доехать до угла и ни разу не встретить
                               # противника, если тот тоже стоял.

    def on_start(self, ctx):
        self.anchor = None    # точка, у которой стоим спиной к стене
        self.last_seen = None  # где видели врага: башня остаётся наведенной
        self.quiet = 0
        self.scan_dir = 0.5   # обзор в одну сторону, без меандра

    def on_tick(self, o):
        me = o.me
        if o.enemy:
            self._note_enemy(o)
        else:
            self.quiet += 1

        if (self.anchor is None or me.dist_to(self.anchor) > 240
                or self.quiet > self.RELOCATE_AFTER):
            self._claim_anchor(o, hunt=self.quiet > self.RELOCATE_AFTER)

        if o.enemy:
            d = me.dist_to(o.enemy)
            # Базовое ведение и стрельба с упреждением (пиксельный допуск).
            turret, fire = o.aim_and_fire(o.enemy, strict=self.STRICT)
            # Сектор считаем по ГДЕ СЕЙЧАС враг, а не по упреждённой точке.
            # Раньше бралась lead(): на 760 px полёт занимает 1.2 с,
            # упреждение уводило точку на десятки градусов от башни, условие
            # ``abs(aim_err) <= SECTOR`` не выполнялось никогда, и кампер
            # стоял с наведённой башней, не сделав ни одного выстрела.
            aim_err = me.turret_error(o.enemy)

            if d < self.PANIC:
                # Слишком близко: пятимся, не разворачиваясь лобовой бронёй.
                away = (me.x - o.enemy.x, me.y - o.enemy.y)
                turn, drive = o.steer_to(me.x + away[0] * 3, me.y + away[1] * 3)
                return Action(drive=-0.9, turn=turn * 0.6,
                              turret=turret,
                              fire=fire and d < 150)

            if abs(aim_err) <= self.SECTOR:
                # Враг в секторе — стоим, целимся и бьём.
                return Action(drive=0.0, turn=0.0,
                              turret=turret, fire=fire)

            # Враг сбоку: доводим башню и попутно доворачиваем корпус так,
            # чтобы лобовая броня смотрела в его сторону. Стрельбу
            # не блокируем: ``fire`` уже означает «наведён и вписан в
            # геометрию корпуса», а сектор — это про позицию, а не
            # про разрешение нажать курок.
            turn_err = me.hull_error(o.enemy)
            return Action(drive=0.15, turn=o.sign(turn_err) * 0.35,
                          turret=turret, fire=fire)

        # Тишина: доезжаем до своей точки, потом стоим и осматриваемся.
        if self.anchor is not None and me.dist_to(self.anchor) > 45.0:
            turn, drive = o.steer_to(*self.anchor)
            return Action(drive=drive * 0.75, turn=turn, turret=self._scan(o))
        return Action(drive=0.0, turn=0.0, turret=self._scan(o))

    def _note_enemy(self, o):
        """Запомнить, где враг. Башня не должна терять цель."""
        self.last_seen = (o.enemy.x, o.enemy.y)
        self.quiet = 0

    def _claim_anchor(self, o, hunt: bool = False):
        """Выбираем точку, от которой до ближайшей стены ~50 пикселей.

        При `hunt` смещаемся в сторону чужого спавна: стоять в одном углу
        все шестьдесят секунд — верный способ получить ничью по таймауту.
        """
        if hunt:
            # Идём прямо к чужому спавну, а не на 260 px в его сторону:
            # на «arena» спавны разнесены на 1160 px, шаг в 260 px не
            # сближал нас с противником вообще, бой кончался нулём
            # выстрелов. Стоять дальше в своём углу бессмысленно.
            sx, sy = o.map.spawns[1 - o.tank]
            self.anchor = (sx, sy)
            self.quiet = 0
            return
        wall = radians(o.map.wall_dir(o.me.x, o.me.y))
        # Стена в направлении wall, значит «спиной к стене» — это противоположная
        # сторона: встаём так, чтобы между нами и стеной было ~50 пикселей.
        self.anchor = (o.me.x + cos(wall) * 60.0, o.me.y + sin(wall) * 60.0)

    def _scan(self, o):
        """Обзор без меандра и без потери цели.

        Раньше фаза менялась каждые 70 тиков: башня уходила вправо на ~120°
        и возвращалась, ни разу не пройдя полный круг. Затем башня в тиках
        без видимости наводилась на ``anchor`` — а это на 760 px в стороне.
        Граница обзора «мигала», башня скакала туда-сюда и не успевала
        доводиться до врага: за 779 тиков видимости кампер не сделал ни
        одного выстрела. Теперь приоритет у последней известной позиции.
        """
        if self.last_seen is not None:
            return o.aim_turret(self.last_seen)
        if self.anchor is not None and o.me.dist_to(self.anchor) < 45.0:
            return o.aim_turret(self.anchor)
        return self.scan_dir


program = Brain()