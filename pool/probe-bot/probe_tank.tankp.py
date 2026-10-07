#!TANKP 1
# name: ProbeTank
from tankp import TankProgram, Action
class Brain(TankProgram):
    def on_tick(self, o):
        return Action(drive=0.5, turret=0.3)
program = Brain()
