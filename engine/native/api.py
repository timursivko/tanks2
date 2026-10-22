"""Нативное ядро боя: ctypes-обёртка над ``sim.c``.

Модуль даёт быстрый аналог ``engine.world.World`` для пакетных прогонов
(лидербоард, матрица, пробник): вся физика, видимость, снаряды и округление
наблюдения считаются в C, Python остаётся только вызовом программ.

Равенство с Python-движком — не «примерно», а побитовое: см. ``self_check``
и ``tools/parity.py``. Если библиотеку собрать не удалось, ``available()``
возвращает False, и вызывающий код работает по-старому.
"""

from __future__ import annotations

import array
import ctypes
import math
import os


__all__ = ["available", "NativeWorld", "self_check", "PARAMS", "ABI_VERSION"]

ABI_VERSION = 3

#: Порядок параметров баланса в sim.c (enum P_*). Менять только вместе с C.
PARAMS = (
    "hp", "hull_len", "hull_wid", "speed_fwd", "speed_rev", "accel", "decel",
    "hull_turn", "turret_turn", "reload", "bullet_speed", "bullet_life",
    "bullet_radius", "spread", "muzzle_offset", "base_damage", "self_damage",
    "front_half_angle", "ric_front", "ric_side", "ric_rear", "ricochet_damage",
    "wall_ricochet_angle", "ram_damage", "ram_speed", "ram_cooldown",
    "ram_restitution", "mass", "view_range", "view_cone", "bumper_range",
)

#: Сколько double'ов пишет sim_step в буфер наблюдения.
OBS_N = 48
#: Сколько double'ов занимает дамп состояния (см. sim_state, NT_STATE_N).
STATE_N = 1800

_lib = None
_lib_error = ""


def _load():
    global _lib, _lib_error
    if _lib is not None:
        return _lib
    # импорт внутри: иначе ``python -m engine.native.build`` поднимает пакет,
    # который сам импортирует build, и runpy предупреждает о двойном импорте.
    from engine.native.build import HERE, LIBNAME, build
    if os.environ.get("TANKSIM_NATIVE", "").strip().lower() in (
            "0", "off", "false", "no"):
        _lib_error = "отключено переменной TANKSIM_NATIVE"
        return None
    path = HERE / LIBNAME
    if not path.exists():
        path = build(quiet=True)
        if path is None:
            _lib_error = ("нативное ядро не собралось: нужен компилятор C "
                          "(cc/gcc/clang) с поддержкой __int128")
            return None
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:                       # noqa: BLE001 — старый бинарник
        _lib_error = f"не удалось загрузить {path.name}: {exc}"
        return None
    try:
        version = lib.sim_version()
    except AttributeError:
        _lib_error = "библиотека устарела (нет sim_version)"
        build(force=True, quiet=True)
        return None
    if version != ABI_VERSION:
        _lib_error = f"ABI {version} != {ABI_VERSION}"
        build(force=True, quiet=True)
        return None

    d = ctypes.c_double
    p = ctypes.c_void_p
    lib.sim_create.restype = p
    lib.sim_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                               ctypes.POINTER(d), ctypes.POINTER(d),
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, d,
                               ctypes.POINTER(d), ctypes.c_longlong,
                               ctypes.c_int, ctypes.c_int, ctypes.POINTER(d)]
    lib.sim_step.restype = ctypes.c_int
    lib.sim_step.argtypes = [p, ctypes.POINTER(d), ctypes.POINTER(d)]
    lib.sim_stats.restype = None
    lib.sim_stats.argtypes = [p, ctypes.POINTER(d)]
    lib.sim_state.restype = ctypes.c_int
    lib.sim_state.argtypes = [p, ctypes.POINTER(d)]
    lib.sim_ticks.restype = ctypes.c_long
    lib.sim_ticks.argtypes = [p]
    lib.sim_over.restype = ctypes.c_int
    lib.sim_over.argtypes = [p]
    lib.sim_destroy.restype = None
    lib.sim_destroy.argtypes = [p]
    lib.sim_param_count.restype = ctypes.c_int
    lib.nt_round_raw.restype = d
    lib.nt_round_raw.argtypes = [d, ctypes.c_int]
    lib.nt_hypot_raw.restype = d
    lib.nt_hypot_raw.argtypes = [d, d]
    if lib.sim_param_count() != len(PARAMS):
        _lib_error = "список параметров не совпадает с sim.c"
        return None
    _lib = lib
    return lib


def prim_round(x: float, n: int) -> float:
    """``round(x, n)`` из C — для тестов равенства с Python."""
    return _load().nt_round_raw(float(x), int(n))


def prim_hypot(x: float, y: float) -> float:
    """``math.hypot(x, y)`` из C — для тестов равенства с Python."""
    return _load().nt_hypot_raw(float(x), float(y))


def available() -> bool:
    """Есть ли рабочая нативная библиотека."""
    return _load() is not None


def load_error() -> str:
    """Почему нативное ядро недоступно (пустая строка, если доступно)."""
    _load()
    return _lib_error


# --- параметры баланса ------------------------------------------------------

def balance_params(bal) -> tuple[float, ...]:
    """31 число для sim_create в порядке ``PARAMS``."""
    ric = bal.ricochet_angles
    return (
        float(bal.hp), float(bal.hull_len), float(bal.hull_wid),
        float(bal.speed_fwd), float(bal.speed_rev), float(bal.accel),
        float(bal.decel), float(bal.hull_turn), float(bal.turret_turn),
        float(bal.reload), float(bal.bullet_speed), float(bal.bullet_life),
        float(bal.bullet_radius), float(bal.spread), float(bal.muzzle_offset),
        float(bal.base_damage), 1.0 if bal.self_damage else 0.0,
        float(bal.front_half_angle),
        float(ric.get("front") if ric.get("front") is not None else math.nan),
        float(ric.get("side") if ric.get("side") is not None else math.nan),
        float(ric.get("rear") if ric.get("rear") is not None else math.nan),
        float(bal.ricochet_damage), float(bal.wall_ricochet_angle),
        float(bal.ram_damage), float(bal.ram_speed), float(bal.ram_cooldown),
        float(bal.ram_restitution), float(bal.mass), float(bal.view_range),
        float(bal.view_cone), float(bal.bumper_range),
    )


# --- арена ------------------------------------------------------------------

class _ArenaData:
    """Готовые C-буферы карты: собираются один раз на арену."""

    __slots__ = ("opaque", "block", "mud", "clearance", "spawns", "width",
                 "height", "tile", "mud_factor", "key")

    def __init__(self, arena) -> None:
        self.key = (arena.name, arena.width, arena.height, arena.tile)
        self.width = arena.width
        self.height = arena.height
        self.tile = arena.tile
        self.mud_factor = arena.mud_factor
        opaque = bytearray()
        block = bytearray()
        mud = bytearray()
        for y in range(arena.height):
            row_o = arena.opaque[y]
            row_b = arena.block[y]
            row_m = arena.mud[y]
            opaque.extend(bytes(1 if v else 0 for v in row_o))
            block.extend(bytes(1 if v else 0 for v in row_b))
            mud.extend(bytes(1 if v else 0 for v in row_m))
        self.opaque = bytes(opaque)
        self.block = bytes(block)
        self.mud = bytes(mud)
        flat = []
        for row in arena.clearance:
            flat.extend(float(v) for v in row)
        self.clearance = (ctypes.c_double * len(flat))(*flat)
        sp = []
        for s in arena.spawns[:2]:
            sp.extend((float(s.x), float(s.y), float(s.angle)))
        while len(sp) < 6:
            sp.extend((0.0, 0.0, 0.0))
        self.spawns = (ctypes.c_double * 6)(*sp)


_ARENA_CACHE: dict[int, tuple] = {}


def arena_data(arena) -> _ArenaData:
    """C-буферы для арены (кэш по объекту: карты переиспользуются между боями)."""
    key = id(arena)
    hit = _ARENA_CACHE.get(key)
    if hit is not None and hit[0] is arena:
        return hit[1]
    data = _ArenaData(arena)
    _ARENA_CACHE[key] = (arena, data)
    return data


# --- мир --------------------------------------------------------------------

OUTCOMES = {0: "", 1: "draw", 2: "a_win", 3: "b_win"}
REASONS = {0: "", 1: "destroyed", 2: "timeout", 3: "mutual"}


class NativeWorld:
    """Быстрый мир: тот же API, что нужен пакетному прогону.

    Атрибуты как у ``engine.world.World``: ``tick``, ``over``, ``outcome``,
    ``end_reason``; плюс ``observations(budget_ms)`` — точная замена
    ``engine.observation.build_both`` и ``stats()`` — итоговая статистика.
    """

    __slots__ = ("sim", "bal", "arena", "max_ticks", "_obs", "_view", "_act",
                 "_stats", "_stats_buf", "bullet_speed", "turret_turn",
                 "muzzle_offset", "half", "spread", "_data")

    def __init__(self, arena, bal, seed: int = 0, max_seconds: int = 60,
                 tick_rate: int = 60) -> None:
        lib = _load()
        if lib is None:
            raise RuntimeError(f"нативное ядро недоступно: {_lib_error}")
        self.bal = bal
        self.arena = arena
        self._data = arena_data(arena)
        d = self._data
        # Буфер наблюдения — array('d'): у него C-скорости tolist(), а память
        # отдаётся в C через from_buffer (общий буфер, без копирования).
        self._view = array.array("d", bytes(8 * OBS_N))
        self._obs = (ctypes.c_double * OBS_N).from_buffer(self._view)
        self._act = (ctypes.c_double * 8)()
        self._stats_buf = array.array("d", bytes(8 * 16))
        self._stats = (ctypes.c_double * 16).from_buffer(self._stats_buf)
        params = (ctypes.c_double * len(PARAMS))(*balance_params(bal))
        self.sim = lib.sim_create(
            d.opaque, d.block, d.mud, d.clearance, d.spawns,
            d.width, d.height, d.tile, float(d.mud_factor),
            params, int(seed), int(tick_rate), int(max_seconds), self._obs)
        if not self.sim:
            raise MemoryError("sim_create вернул NULL")
        self.max_ticks = int(tick_rate) * int(max_seconds)
        self.bullet_speed = float(bal.bullet_speed)
        self.turret_turn = float(bal.turret_turn)
        self.muzzle_offset = float(bal.muzzle_offset)
        self.half = (float(bal.hull_len) / 2.0, float(bal.hull_wid) / 2.0)
        self.spread = float(bal.spread)

    # --- состояние ----------------------------------------------------------

    @property
    def tick(self) -> int:
        return int(self._view[0])

    @property
    def over(self) -> bool:
        return self._view[2] > 0.5

    @property
    def outcome(self) -> str:
        return OUTCOMES.get(int(self._view[3]), "")

    @property
    def end_reason(self) -> str:
        return REASONS.get(int(self._view[4]), "")

    # --- шаг ----------------------------------------------------------------

    def step(self, actions) -> tuple:
        """Один такт. ``actions`` — две команды с полями drive/turn/turret/fire.

        События не собираются: пакетному прогону они не нужны, а словари на
        каждый выстрел — это те же аллокации, что и в Python-версии. Счётчики
        статистики (попадания, рикошеты, урон) обновляются в C всегда.
        """
        a = self._act
        for i in (0, 1):
            act = actions[i]
            if act is None:
                a[i * 4] = a[i * 4 + 1] = a[i * 4 + 2] = 0.0
                a[i * 4 + 3] = 0.0
                continue
            a[i * 4] = act.drive
            a[i * 4 + 1] = act.turn
            a[i * 4 + 2] = act.turret
            a[i * 4 + 3] = 1.0 if act.fire else 0.0
        _load().sim_step(self.sim, a, self._obs)
        return ()

    # --- наблюдения ---------------------------------------------------------

    def observations(self, budget_ms: float) -> tuple:
        """Два payload-кортежа — ровно как ``build_both`` из Python-движка."""
        v = self._view.tolist()
        tick = int(v[0])
        t = v[1]
        dt = v[43]
        bs = self.bullet_speed
        tt = self.turret_turn
        mo = self.muzzle_offset
        half = self.half
        sp = self.spread
        me0 = (v[5], v[6], v[7], v[8], v[9], v[10], v[11], v[12],
               v[13] > 0.5, v[14], v[15])
        me1 = (v[16], v[17], v[18], v[19], v[20], v[21], v[22], v[23],
               v[24] > 0.5, v[25], v[26])
        en0 = me1 if v[27] > 0.5 else None
        en1 = me0 if v[35] > 0.5 else None
        s0 = (v[28] > 0.5, v[29] > 0.5, v[30] > 0.5, v[31] > 0.5,
              v[32] > 0.5, v[33], v[34])
        s1 = (v[36] > 0.5, v[37] > 0.5, v[38] > 0.5, v[39] > 0.5,
              v[40] > 0.5, v[41], v[42])
        return ((tick, t, dt, 0, budget_ms, me0, en0, bs, tt, mo, half, sp, s0),
                (tick, t, dt, 1, budget_ms, me1, en1, bs, tt, mo, half, sp, s1))

    # --- итоги --------------------------------------------------------------

    def stats(self) -> list[dict]:
        """Статистика боя в том же виде, что собирает ``run_battle``."""
        _load().sim_stats(self.sim, self._stats)
        s = self._stats_buf
        out = []
        for i in (0, 1):
            b = i * 8
            out.append({
                "shots": int(s[b]), "hits": int(s[b + 1]),
                "ric": int(s[b + 2]), "ric_taken": int(s[b + 3]),
                "rams": int(s[b + 4]), "dealt": s[b + 5],
                "taken": s[b + 6], "hp_left": s[b + 7],
            })
        return out

    def close(self) -> None:
        if getattr(self, "sim", None):
            _load().sim_destroy(self.sim)
            self.sim = None

    def __del__(self) -> None:                   # noqa: D401 — освобождение handle
        try:
            self.close()
        except Exception:                        # noqa: BLE001 — при выходе
            pass

    # --- отладка ------------------------------------------------------------

    def state(self) -> list[float]:
        """Полный дамп состояния (для сверки с Python-движком)."""
        buf = (ctypes.c_double * STATE_N)()
        n = _load().sim_state(self.sim, buf)
        return list(buf)[:n]


# --- самопроверка -----------------------------------------------------------

def self_check(quick: bool = True) -> tuple[bool, str]:
    """Сверяет нативный мир с Python-движком на коротком синтетическом бою.

    Проверка нужна как предохранитель: если libm или компилятор в этой
    системе дадут другой последний бит, нативное ядро отключается, и
    результат остаётся тем же, что у чистой Python-версии.
    """
    if not available():
        return False, load_error() or "нет библиотеки"
    try:
        from config import BALANCE
        from engine.action import Action
        from engine.grid import load_map
        from engine.observation import build_both
        from engine.world import World
    except Exception as exc:                     # noqa: BLE001
        return False, f"импорт движка: {exc}"

    maps = ["arena"] if quick else ["arena", "fortress", "colonnade"]
    ticks = 240 if quick else 900
    for name in maps:
        try:
            arena = load_map(name)
        except Exception as exc:                 # noqa: BLE001
            return False, f"карта {name}: {exc}"
        for seed in (1, 7):
            py = World.create(arena, BALANCE, seed=seed, max_seconds=60)
            nat = NativeWorld(arena, BALANCE, seed=seed, max_seconds=60)
            rng = __import__("random").Random(seed * 7919 + 13)
            for step in range(ticks):
                acts = [Action(rng.uniform(-1, 1), rng.uniform(-1, 1),
                               rng.uniform(-1, 1), rng.random() < 0.35)
                        for _ in range(2)]
                # наблюдения обязаны совпадать до последнего бита
                o_py = build_both(py, 10.0)
                o_nat = nat.observations(10.0)
                if o_py != o_nat:
                    return False, (f"{name} seed={seed} тик {py.tick}: "
                                   f"наблюдения расходятся")
                py.step([Action.clamp(acts[0]), Action.clamp(acts[1])])
                nat.step([Action.clamp(acts[0]), Action.clamp(acts[1])])
                if _py_state(py) != nat.state():
                    return False, (f"{name} seed={seed} тик {py.tick}: "
                                   f"состояние расходится")
                if py.over or nat.over:
                    if py.outcome != nat.outcome or py.tick != nat.tick:
                        return False, f"{name} seed={seed}: итог боя расходится"
                    break
            nat.close()
    return True, ""


def _py_state(world) -> list[float]:
    """Дамп состояния Python-мира в том же порядке, что ``sim_state``."""
    out = [float(world.tick), 1.0 if world.over else 0.0,
           float({"": 0, "draw": 1, "a_win": 2, "b_win": 3}.get(world.outcome, 0)),
           float({"": 0, "destroyed": 1, "timeout": 2, "mutual": 3}.get(
               world.end_reason, 0)),
           float(len(world.bullets))]
    for t in world.tanks:
        out += [t.x, t.y, t.hull, t.turret, t.vx, t.vy, t.hp, t.cooldown,
                t.ram_cd, 1.0 if t.alive else 0.0, 1.0 if t.sees_enemy else 0.0]
        sight = t.sight_detail or (False, False, False, False, False, math.nan,
                                   math.nan)
        out += [float(v) if v is not None else math.nan for v in sight]
        out += [float(t.shots), float(t.hits), float(t.ricochets),
                float(t.ricochets_taken), float(t.rams), t.damage_dealt,
                t.damage_taken]
    for b in world.bullets:
        out += [float(b.owner), b.x, b.y, b.dx, b.dy, b.life, b.speed,
                float(b.skip)]
        if not b.alive:
            out[-8] = float(b.owner)
    state = world.rng.getstate()
    out += [float(v) for v in state[1][:624]]
    out.append(float(state[1][624]))
    return out
