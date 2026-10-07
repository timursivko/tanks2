#!TANKP 1
# name: Бесконечный цикл
# author: TANKSIM
# difficulty: 1
# color: #607d8b
# description: Демонстрация жёсткого таймаута: программа зависает и её процесс убивается, бой продолжается.
# tags: тест,таймаут

from tankp import TankProgram, Action


class Brain(TankProgram):
    def on_tick(self, o):
        while True:          # движок не сможет прервать это внутри on_tick
            pass
        return Action()      # сюда управление не дойдёт


program = Brain()