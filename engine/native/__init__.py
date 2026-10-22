"""Нативное (C) ядро боя.

Пакет необязательный: ``available()`` говорит, собралось ли ядро. Всё
остальное — в ``engine.native.api``.
"""

from __future__ import annotations

from engine.native.api import (ABI_VERSION, NativeWorld, PARAMS, available,
                               load_error, self_check)

__all__ = ["available", "load_error", "self_check", "NativeWorld", "PARAMS",
           "ABI_VERSION"]
