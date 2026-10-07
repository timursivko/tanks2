"""Команда, которую танк отдаёт движку: намерения, а не координаты."""

from __future__ import annotations

from dataclasses import dataclass

from engine.geometry import clamp

FORWARD = "forward"
REVERSE = "reverse"
STOP = "stop"

#: Границы диапазона и «нечисла» для проверки в одном месте: собрать их
#: внутри функции дешевле не выйдет — float("inf") вызывается каждый раз.
INF_POS = float("inf")
INF_NEG = float("-inf")


def num(v, default=0.0) -> float:
    """Число из чего угодно: float, int, строка. Мусор и nan/inf — default."""
    if type(v) is float:
        f = v
    else:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
    if f != f or f == INF_POS or f == INF_NEG:
        return default
    return f


@dataclass
class Action:
    """Приведённая команда.

    * ``drive``  — -1..1, тяга вперёд/назад (0 — стоп)
    * ``turn``   — -1..1, поворот корпуса (0 — ровно)
    * ``turret`` — -1..1, поворот башни
    * ``fire``   —bool, попросить выстрел (сработает, только если готов КД)
    """

    drive: float = 0.0
    turn: float = 0.0
    turret: float = 0.0
    fire: bool = False

    def to_dict(self) -> dict:
        return {"d": round(self.drive, 4), "t": round(self.turn, 4),
                "u": round(self.turret, 4), "f": bool(self.fire)}

    @staticmethod
    def clamp(raw) -> "Action":
        """Приводит любой мусор из скрипта к валидной команде."""
        if raw is None:
            return Action()
        if isinstance(raw, (tuple, list)):
            vals = list(raw) + [0.0] * (4 - len(raw))
            return Action(vals[0], vals[1], vals[2], bool(vals[3]))
        if isinstance(raw, dict):
            return Action(
                drive=raw.get("drive", raw.get("d", 0.0)),
                turn=raw.get("turn", raw.get("t", 0.0)),
                turret=raw.get("turret", raw.get("u", 0.0)),
                fire=raw.get("fire", raw.get("f", False)),
            )
        # Любой похожий объект (в том числе Action из SDK tankp): четыре
        # обращения к атрибутам вместо all(hasattr(...)) — тот же ответ,
        # но без генератора и четырёх отдельных hasattr на команду.
        try:
            drive = raw.drive
            turn = raw.turn
            turret = raw.turret
            fire = raw.fire
        except AttributeError:
            return Action()
        # Обычный случай — скрипт уже отдал нормальные float в диапазоне:
        # тогда num() и clamp() не нужны, но ответ тот же самый.
        if type(drive) is float and -1.0 <= drive <= 1.0:
            d = drive
        else:
            d = clamp(num(drive), -1.0, 1.0)
        if type(turn) is float and -1.0 <= turn <= 1.0:
            t = turn
        else:
            t = clamp(num(turn), -1.0, 1.0)
        if type(turret) is float and -1.0 <= turret <= 1.0:
            u = turret
        else:
            u = clamp(num(turret), -1.0, 1.0)
        return Action(d, t, u, bool(fire))

    @staticmethod
    def from_dict(d: dict) -> "Action":
        return Action.clamp(d or {})