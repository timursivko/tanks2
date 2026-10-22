"""Ядро одного боя для пакетных прогонов (лидербоард, матрица, сверки).

Один бой считается «в процессе», без песочницы: программы уже загружены, а
время решения меряется таймером вокруг вызова — ровно как в песочнице
сервера, только без отдельного процесса. Два взаимозаменяемых движка:

* ``run_battle_py`` — эталонный, на ``engine.world.World``: он и определяет,
  каким должен быть результат;
* ``run_battle_native`` — то же самое на C-ядре (``engine.native``): тот же
  порядок операций и те же числа, но в десятки раз меньше работы в Python.

Обе функции возвращают ``(world, stats)``, где ``world`` отвечает на
``.tick``, ``.over``, ``.outcome``, а ``stats[i]`` — словарь статистики боя.
Совпадение проверяет ``tools/parity.py``.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "engine" / "sdk"), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tankp  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.native import NativeWorld, available, load_error, self_check  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.world import World  # noqa: E402

class _ArenaShim:
    """Обёртка для ``map_payload``: ему нужен только ``.arena``."""

    __slots__ = ("arena",)

    def __init__(self, arena) -> None:
        self.arena = arena


# --- загрузка программ ------------------------------------------------------

_CODE_CACHE: dict[tuple[str, int, int], object] = {}


def _code_for(path: str):
    """Скомпилированный модуль программы (кэш по файлу, mtime и размеру).

    ``compile()`` — самая дорогая часть загрузки (у танков pool это 3–6 мс на
    файл), а к бою нужен не он, а свежий модуль. Кэшируется именно код:
    ``exec`` каждый раз создаёт новый словарь модуля, поэтому состояние
    программ между боями не перетекает, как и раньше.
    """
    try:
        st = Path(path).stat()
        key = (path, int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return compile(Path(path).read_text(encoding="utf-8"), str(path), "exec")
    hit = _CODE_CACHE.get(key)
    if hit is None:
        hit = compile(Path(path).read_text(encoding="utf-8"), str(path), "exec")
        _CODE_CACHE[key] = hit
    return hit


def load_program(path: str):
    """Загружает программу в этом процессе — точная копия probe.load_inproc.

    Отличие одно: исходник компилируется через кэш ``_code_for``. Порядок
    действий, точка входа и вызов ``on_start`` — те же.
    """
    cand = Path(path)
    mod = types.ModuleType("probe_program")
    mod.__file__ = str(cand)
    exec(_code_for(str(cand)), mod.__dict__)  # noqa: S102 — так и задумано
    prog = mod.__dict__.get("program")
    if prog is not None and hasattr(prog, "on_start"):
        prog.on_start({"tank": 0})
        return prog.on_tick
    fn = mod.__dict__.get("on_tick")
    if fn is None:
        raise SystemExit(f"нет точки входа в {cand}")
    if hasattr(mod.__dict__.get("on_start"), "__call__"):
        mod.__dict__["on_start"]({})
    return fn


# --- бой --------------------------------------------------------------------

def _play(world, build, mv, fns, budget):
    """Общий цикл боя: наблюдения → решения программ → шаг мира.

    Один и тот же код для обоих движков — иначе эталон и быстрый путь
    разъехались бы в мелочах (порядок вызовов, учёт бюджета, обработка
    исключений), а именно эти мелочи и определяют совпадение отчёта.
    """
    obs_cls, sdk_action = tankp.Observation, tankp.Action
    clamp = Action.clamp
    errors = [0, 0]
    think_total = [0.0, 0.0]
    think_max = [0.0, 0.0]
    think_calls = [0, 0]
    over = [0, 0]
    while not world.over:
        obs_a, obs_b = build(world, budget)
        acts = [None, None]
        for i, obs in enumerate((obs_a, obs_b)):
            t0 = time.perf_counter()
            try:
                raw = fns[i](obs_cls(obs, mv))
                acts[i] = raw if raw.__class__ is sdk_action else clamp(raw)
            except Exception:  # noqa: BLE001 — тик считается пустым
                errors[i] += 1
                acts[i] = clamp(None)
            ms = (time.perf_counter() - t0) * 1000.0
            think_total[i] += ms
            think_calls[i] += 1
            if ms > think_max[i]:
                think_max[i] = ms
            if ms > budget:
                over[i] += 1
        world.step(acts)
    return errors, think_total, think_max, think_calls, over


def _stats(tanks, errors, think_total, think_max, think_calls, over):
    return [{
        "shots": t.shots, "hits": t.hits,
        "ric": t.ricochets, "ric_taken": t.ricochets_taken,
        "rams": t.rams, "dealt": t.damage_dealt, "taken": t.damage_taken,
        "hp_left": t.hp, "think_total": think_total[i],
        "think_calls": think_calls[i], "think_max": think_max[i],
        "over_budget": over[i], "errors": errors[i],
    } for i, t in enumerate(tanks)]


def run_battle_py(fa, fb, arena, seed: int, bal, max_seconds: int):
    """Эталонный прогон боя на ``engine.world.World``."""
    world = World.create(arena, bal, seed=seed, max_seconds=max_seconds)
    mv = tankp.MapView(map_payload(world))
    budget = bal.default_budget_ms
    errors, think_total, think_max, think_calls, over = _play(
        world, build_both, mv, (fa, fb), budget)
    return world, _stats(world.tanks, errors, think_total, think_max,
                         think_calls, over)


def _native_is(engine: str) -> bool:
    return resolve_engine(engine) == "native"


def run_battle_native(fa, fb, arena, seed: int, bal, max_seconds: int):
    """Тот же бой на нативном ядре: те же числа, меньше работы в Python."""
    world = NativeWorld(arena, bal, seed=seed, max_seconds=max_seconds)
    mv = tankp.MapView(map_payload(_ArenaShim(arena)))
    budget = bal.default_budget_ms
    errors, think_total, think_max, think_calls, over = _play(
        world, _native_build, mv, (fa, fb), budget)
    final = world.stats()
    stats = [{
        "shots": s["shots"], "hits": s["hits"],
        "ric": s["ric"], "ric_taken": s["ric_taken"],
        "rams": s["rams"], "dealt": s["dealt"], "taken": s["taken"],
        "hp_left": s["hp_left"], "think_total": think_total[i],
        "think_calls": think_calls[i], "think_max": think_max[i],
        "over_budget": over[i], "errors": errors[i],
    } for i, s in enumerate(final)]
    return world, stats


def _native_build(world, budget):
    """``build_both`` для нативного мира: (payload, payload)."""
    return world.observations(budget)


# --- выбор движка -----------------------------------------------------------

_NATIVE: dict[str, object] = {}


def native_ready() -> bool:
    """Есть ли нативное ядро, прошедшее самопроверку (кэшируется)."""
    if "ok" not in _NATIVE:
        if not available():
            _NATIVE["ok"] = False
            _NATIVE["why"] = load_error() or "нет библиотеки"
        else:
            ok, why = self_check(quick=True)
            _NATIVE["ok"] = ok
            _NATIVE["why"] = why
    return bool(_NATIVE["ok"])


def native_reason() -> str:
    native_ready()
    return str(_NATIVE.get("why", ""))


def resolve_engine(engine: str) -> str:
    """``auto`` → ``native``, если ядро собралось и прошло самопроверку."""
    if engine == "auto":
        return "native" if native_ready() else "py"
    if engine == "native" and not native_ready():
        raise SystemExit(f"нативное ядро недоступно: {native_reason()}")
    return engine


def run_battle(fa, fb, arena, seed: int, bal, max_seconds: int,
               engine: str = "auto"):
    """Бой тем движком, который выбран (``auto`` — нативный, если можно)."""
    if _native_is(engine):
        return run_battle_native(fa, fb, arena, seed, bal, max_seconds)
    return run_battle_py(fa, fb, arena, seed, bal, max_seconds)
