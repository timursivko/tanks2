"""Схемы запросов и ответов API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PlayerIn(BaseModel):
    """Описание участника боя от клиента."""

    kind: str = Field("script", pattern="^(script|manual)$")
    key: str = ""                       # ключ из каталога программ
    name: str = ""
    color: str = ""


class BattleIn(BaseModel):
    """Запрос на расчёт боя."""

    a: PlayerIn = Field(default_factory=PlayerIn)
    b: PlayerIn = Field(default_factory=PlayerIn)
    map_name: str = "arena"
    budget_ms: float = Field(10.0, ge=1.0, le=500.0)
    seed: int = 1
    max_seconds: int = Field(60, ge=5, le=60)


class ManualIn(BaseModel):
    """Запрос на живой бой с ручным управлением."""

    player_side: str = Field("a", pattern="^(a|b)$")
    key: str = ""                       # программа противника
    map_name: str = "arena"
    budget_ms: float = Field(10.0, ge=1.0, le=500.0)
    seed: int = 1
    player_name: str = "Игрок"
    reveal: bool = False                # показывать противника даже вне обзора


class InputMsg(BaseModel):
    """Ввод игрока в ручном бою (60 раз в секунду)."""

    keys: list[str] = Field(default_factory=list)
    aim: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    fire: bool = False
    #: None — не меняем режим видимости, True/False — переключаем на лету.
    reveal: bool | None = None