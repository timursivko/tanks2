#!TANKP 1
# name: Ошибка в тике
# author: TANKSIM
# difficulty: 1
# color: #607d8b
# description: Демонстрация обработки исключений: текст ошибки попадает в лог боя, танк в этот тик стоит.
# tags: тест,исключение

from tankp import TankProgram, Action


class Brain(TankProgram):
    def on_tick(self, o):
        log("готовлю выстрел")
        # Опечатка: переменной target нет — движок поймает исключение.
        if o.enemy:
            return Action(turret=target.turret)
        return Action()


program = Brain()