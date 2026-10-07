#!TANKP 1
# name: Берсерк
# author: TANKSIM
# difficulty: 2
# color: #e53935
# description: Ни о чём не думает: полный газ, башня крутится сама, огонь без остановок. Показывает, чем оборачивается таран.
# tags: агрессивный,таран,хаос

from tankp import TankProgram, Action


class Brain(TankProgram):
    """Классика «танка-рубаки»: корпус идёт на врага, башня живёт своей жизнью."""

    # Берсерк бьёт грубо, но не в молоко: 0.6 — около 60% гарантированного
    # допуска попадания. Раньше стояло 9 градусов, а это 63 px промаха
    # на 400 px при цели шириной 34 px.
    STRICT = 0.6

    def on_start(self, ctx):
        self.rage = 0
        self.scan_dir = 1.0       # знак обзора держим, а не меняем каждые 25 тиков

    def on_tick(self, o):
        self.rage += 1

        if not o.enemy:
            # Без цели едем к вражескому спавну, башня смотрит в ту же
            # сторону. Раньше знак переключался каждые 25 тиков: башня
            # доходила до ~86° вправо и возвращалась в исходную точку,
            # не проходя полный круг.
            turn, drive = o.steer_to(*o.map.spawns[1 - o.tank])
            return Action(drive=0.8, turn=turn * 0.5, turret=self.scan_dir)

        me = o.me
        d = me.dist_to(o.enemy)
        hull_err = me.hull_error(o.enemy)
        turret, fire = o.aim_and_fire(o.enemy, strict=self.STRICT)
        return Action(
            drive=1.0 if d > 120.0 else 0.55,   # в упор не давим — мешает манёвру
            turn=o.sign(hull_err),
            turret=turret,
            fire=fire,
        )


program = Brain()