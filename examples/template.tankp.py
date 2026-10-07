#!TANKP 1
# name: Мой танк
# author: вы
# difficulty: 2
# color: #7ed957
# description: Опишите замысел в одну строку — её увидят в списке танков.
# tags: пример

from tankp import TankProgram, Action


class Brain(TankProgram):
    def on_start(self, ctx):
        self.found = False

    def on_tick(self, o):
        if o.enemy:
            # Ошибка наведения по башне в градусах: положительное — вправо.
            err = o.me.turret_error(o.enemy)
            return Action(drive=0.6, turn=o.sign(err), turret=o.sign(err),
                          fire=abs(err) < 8)

        # Врага не видно — идём к точке его спавна и ждём.
        turn, drive = o.steer_to(*o.map.spawns[1 - o.tank])
        return Action(drive=drive, turn=turn)


program = Brain()