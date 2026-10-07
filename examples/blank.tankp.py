#!TANKP 1
# name: Пустая болванка
# author: TANKSIM
# difficulty: 1
# color: #78909c
# description: Ничего не делает и почти не тратит время решения. Удобна как база для своих экспериментов.
# tags: заготовка,нулевой

from tankp import TankProgram, Action


class Brain(TankProgram):
    def on_tick(self, o):
        return Action()


program = Brain()