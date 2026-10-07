"""События боя: выстрелы, попадания, рикошеты, тараны, смерть, лог скриптов."""

from __future__ import annotations

SHUTDOWN = "shutdown"


class EventKind:
    START = "start"
    SHOT = "shot"
    HIT = "hit"
    RAM = "ram"
    DEATH = "death"
    LOG = "log"           # сообщение из log() скрипта
    THINK = "think"       # нарушение бюджета времени / ошибка / таймаут
    END = "end"


class Outcome:
    A_WIN = "a_win"
    B_WIN = "b_win"
    DRAW = "draw"

    LABELS = {A_WIN: "Победа A", B_WIN: "Победа B", DRAW: "Ничья"}


class EndReason:
    DESTROYED = "destroyed"
    TIMEOUT = "timeout"
    MUTUAL = "mutual"
    ABORTED = "aborted"