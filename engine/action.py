"""Команда, которую танк отдаёт движку: намерения, а не координаты."""

from __future__ import annotations

from dataclasses import dataclass

from engine.geometry import clamp

FORWARD = "forward"
REVERSE = "reverse"
STOP = "stop"


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
            raw = Action(vals[0], vals[1], vals[2], bool(vals[3]))
        elif isinstance(raw, dict):
            raw = Action(
                drive=raw.get("drive", raw.get("d", 0.0)),
                turn=raw.get("turn", raw.get("t", 0.0)),
                turret=raw.get("turret", raw.get("u", 0.0)),
                fire=raw.get("fire", raw.get("f", False)),
            )
        elif all(hasattr(raw, a) for a in ("drive", "turn", "turret", "fire")):
            # Любой похожий объект (в том числе Action из SDK tankp).
            pass
        else:
            raw = Action()

        def num(v, default=0.0) -> float:
            try:
                f = float(v)
            except (TypeError, ValueError):
                return default
            if f != f or f in (float("inf"), float("-inf")):
                return default
            return f

        return Action(
            drive=clamp(num(raw.drive), -1.0, 1.0),
            turn=clamp(num(raw.turn), -1.0, 1.0),
            turret=clamp(num(raw.turret), -1.0, 1.0),
            fire=bool(raw.fire),
        )

    @staticmethod
    def from_dict(d: dict) -> "Action":
        return Action.clamp(d or {})