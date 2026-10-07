"""Серверная часть TANKSIM.

Приложение импортируется лениво: иначе ``server.app`` перестал бы быть
модулем и uvicroн не нашёл бы ``app`` внутри модуля.
"""

from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from server.app import app as fastapi_app

        return fastapi_app
    raise AttributeError(name)