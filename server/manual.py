"""Живой бой с ручным управлением через WebSocket.

Тот же движок и те же правила, что и в расчётном бою: ручной игрок отдаёт
команду движку напрямую, его противник — обычная программа в песочнице.
Бой пишется в реплей, его можно пересмотреть на любой скорости.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from config import BALANCE, Balance
from engine.action import Action
from engine.events import EventKind
from engine.geometry import ang_diff
from engine.grid import Arena, load_map
from engine.observation import build_observation, map_payload
from engine.replay import Replay
from engine.sandbox import ScriptRunner
from engine.world import World

#: Частота шагов боя в реальном времени
REAL_TICK_HZ = 60

#: Клавиши приводим к каноническим буквам. Принимаем и символ (``"w"``), и
#: физический код (``"KeyW"``): форма зависит от раскладки и от того, шлёт
#: клиент ``KeyboardEvent.code`` или ``KeyboardEvent.key``.
_KEY_ALIASES = {
    "w": "w", "keyw": "w", "arrowup": "w",
    "s": "s", "keys": "s", "arrowdown": "s",
    "a": "a", "keya": "a", "arrowleft": "a",
    "d": "d", "keyd": "d", "arrowright": "d",
    " ": " ", "space": " ", "spacebar": " ",
}


def _pressed(keys) -> set[str]:
    """Канонические буквы из списка клавиш клиента."""
    out: set[str] = set()
    for k in keys or ():
        name = _KEY_ALIASES.get(str(k).strip().lower())
        if name:
            out.add(name)
    return out


def action_from_input(me, keys, aim, fire) -> Action:
    """Команда танка из ввода игрока: газ, корпус, башня к курсору и огонь.

    Клиент шлёт список клавиш, точку прицела и признак огня, а команду из них
    считает сервер: поведение не зависит от машины игрока.
    """
    pressed = _pressed(keys)

    if aim and len(aim) >= 2:
        # Башня доворачивается к курсору: чем ближе цель, тем точнее прицел.
        want = math.atan2(float(aim[1]) - me.y, float(aim[0]) - me.x)
        err = ang_diff(want, me.turret)
        if abs(err) > 0.25:
            turret = max(-1.0, min(1.0, err / 0.45))
        else:
            turret = max(-1.0, min(1.0, err * 4.0))
    else:
        turret = 0.0

    drive = (1.0 if "w" in pressed else 0.0) - (1.0 if "s" in pressed else 0.0)
    turn = (1.0 if "d" in pressed else 0.0) - (1.0 if "a" in pressed else 0.0)

    if drive < 0.0:
        turn = -turn

    # Смягчаем ручной поворот на ходу, чтобы танк не терял половину скорости
    # при каждом касании кнопок (что ощущается как "коробка автомат скрипит").
    if drive != 0.0 and turn != 0.0:
        turn *= 0.5

    return Action(drive=drive, turn=turn, turret=turret, fire=fire)


class ManualSession:
    """Один ручной бой: мир, противник, поток шагов и очередь кадров."""

    def __init__(self, session_id: str, player_side: str, script_key: str,
                 map_name: str, budget_ms: float, seed: int,
                 player_name: str, bal: Balance | None = None,
                 reveal: bool = False):
        self.id = session_id
        self.bal = bal or BALANCE
        self.player_side = player_side
        self.script_key = script_key
        self.map_name = map_name
        self.budget_ms = budget_ms
        self.seed = seed
        self.player_name = player_name
        # Показывать противника даже вне обзора: настройка просмотра, где видно
        # и чужой танк, и его сектор обзора. Секретность тут не нужна.
        self.reveal = bool(reveal)

        self.arena: Arena = load_map(map_name)
        self.player_index = 0 if player_side == "a" else 1
        self.foe_index = 1 - self.player_index
        self.input = {"keys": [], "aim": [0.0, 0.0], "fire": False}
        self.frames: deque = deque(maxlen=120)
        self.events: deque = deque(maxlen=200)
        self.over = False
        self.result: dict = {}
        self.replay: Replay | None = None
        self.error = ""
        self.runner: ScriptRunner | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False

    # --- запуск -------------------------------------------------------------

    def start(self) -> None:
        names = [self.player_name, "Противник"]
        if self.player_side == "b":
            names = ["Противник", self.player_name]
        colors = ["#ffd166", "#3ea6ff"] if self.player_side == "a" else ["#3ea6ff", "#ffd166"]
        self.world = World.create(
            self.arena, self.bal, seed=self.seed,
            names=(names[0], names[1]), colors=(colors[0], colors[1]),
            kinds=("manual", "script"),
            tick_rate=self.bal.tick_rate, max_seconds=self.bal.max_seconds)

        meta = self._script_meta()
        self.replay = Replay(id=f"m_{int(time.time() * 1000) % 10_000_000}",
                             created=time.time(),
                             map=self.arena.to_dict(),
                             meta={"a": {"name": names[0], "color": colors[0]},
                                   "b": {"name": names[1], "color": colors[1],
                                         "kind": "script", "script": meta.path,
                                         "program_meta": meta.to_dict()} if meta else
                                         {"name": names[1], "color": colors[1]},
                                   "map": {"id": self.map_name, "name": self.arena.name},
                                   "manual_side": self.player_side,
                                   "budget_ms": self.budget_ms,
                                   "balance": self.bal.to_dict()})

        if meta is not None:
            self.runner = ScriptRunner(meta.name, meta.path, meta.to_dict(),
                                       self.budget_ms, self.bal)
            try:
                self.runner.start(map_payload(self.world),
                                  {"tank": self.foe_index, "map": self.map_name,
                                   "seed": self.seed, "budget_ms": self.budget_ms})
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                self.runner = None

        self._thread = threading.Thread(target=self._loop, name=f"manual-{self.id}",
                                        daemon=True)
        self._running = True
        self._thread.start()

    def _script_meta(self):
        from server.sessions import safe_program_path

        path = safe_program_path(self.script_key)
        if path is None or not path.exists():
            self.error = f"программа «{self.script_key}» не найдена в каталоге"
            return None
        from engine.program import load_any_program

        # Точка входа пакета — main.py в каталоге: с корнем каталога воркер
        # подхватит и соседние модули, и веса.
        meta = load_any_program(path)
        if not meta.ok:
            self.error = "; ".join(meta.errors)
            return None
        return meta

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        if not self.over:
            # Игрок остановил бой до финала — всё равно закрываем результат,
            # иначе реплей сохранится без итогов.
            self._finish(interrupted=True)
        if self.runner:
            self.runner.close()

    # --- цикл боя -----------------------------------------------------------

    def _loop(self) -> None:
        period = 1.0 / REAL_TICK_HZ
        next_t = time.perf_counter()
        while self._running and not self.over and not self.world.over:
            try:
                events = self._tick()
            except Exception as exc:  # noqa: BLE001
                self.error = f"{type(exc).__name__}: {exc}"
                self.over = True
                break
            self._push_frame(events)
            next_t += period
            sleep = next_t - time.perf_counter()
            if sleep < -0.25:      # отстали — догоняем без сна
                next_t = time.perf_counter()
            elif sleep > 0:
                time.sleep(sleep)

    def _tick(self) -> list[dict]:
        w = self.world
        pi = self.player_index
        fi = self.foe_index
        actions: list[Action] = [Action(), Action()]

        with self._lock:
            keys = list(self.input["keys"])
            aim = list(self.input["aim"])
            fire = bool(self.input["fire"])

        actions[pi] = action_from_input(w.tanks[pi], keys, aim, fire)

        think_ms = [0.0, 0.0]
        bad = [False, False]
        # Что показать в журнале за этот тик: строки от программы и события боя.
        # Раньше они искались в общей очереди по времени кадра, но движок
        # округляет время события до 4 знаков, а кадра — до 5, и события не
        # находились никогда: журнал боя был пустым.
        shown: list[dict] = []
        if self.runner is not None:
            obs = build_observation(w, fi, self.budget_ms)
            dec = self.runner.decide(obs)
            think_ms[fi] = dec.ms
            bad[fi] = dec.kind in ("over_budget", "timeout")
            actions[fi] = Action() if dec.kind == "over_budget" \
                else Action.clamp(dec.action)
            for line in dec.logs:
                shown.append({"kind": "log", "t": w.tick * w.dt, "tank": fi,
                              "text": line[:300]})

        events = w.step(actions)
        self.events.extend(shown)
        self.events.extend(events)
        if self.replay:
            self.replay.push_frame(w.snapshot((think_ms[0], think_ms[1]), (bad[0], bad[1])))
            for ev in events:
                self.replay.push_event(ev)
        if w.over:
            self._finish()
        return shown + events

    def _finish(self, interrupted: bool = False) -> None:
        self.over = True
        w = self.world
        sa, sb = w.tanks[0].stats(), w.tanks[1].stats()
        outcome = w.outcome or ("interrupted" if interrupted else "")
        reason = ("бой остановлен игроком" if interrupted and not w.end_reason
                  else w.end_reason)
        self.result = {"outcome": outcome, "reason": reason,
                       "duration": round(w.tick * w.dt, 2),
                       "a": sa, "b": sb,
                       "manual_side": self.player_side}
        if self.replay:
            self.replay.summary = dict(self.result)
            self.replay.push_event({"kind": EventKind.END, "t": w.tick * w.dt,
                                    "outcome": outcome, "reason": reason})

    # --- кадры --------------------------------------------------------------

    def _push_frame(self, events: list[dict]) -> None:
        w = self.world
        pi = self.player_index
        me = w.tanks[pi]
        snap = w.snapshot()
        # Противника и снаряды отдаём только то, что игрок реально видит:
        # вручную играющий не должен подсматривать скрытые данные.
        # С галочкой «видеть противника» позиция идёт всегда, но enemy_seen
        # остаётся честным — по нему видно, виден ли противник на самом деле.
        enemy = None
        enemy_seen = bool(me.sees_enemy)
        if enemy_seen or self.reveal:
            foe = snap["b" if pi == 0 else "a"]
            enemy = {"x": foe["x"], "y": foe["y"], "h": foe["h"], "u": foe["u"],
                     "hp": foe["hp"], "alive": foe.get("alive", 1)}
        self.frames.append({
            "t": snap["t"], "tick": w.tick,
            "me": snap["a" if pi == 0 else "b"],
            "enemy": enemy, "enemy_seen": enemy_seen,
            "enemy_revealed": bool(self.reveal),
            "bullets": self._visible_bullets(me),
            "events": events,
            "over": self.over,
            "result": self.result,
        })

    def _visible_bullets(self, me) -> list[list]:
        """Снаряды показываем, только если они в поле зрения игрока."""
        from engine.visibility import line_clear

        out = []
        for b in self.world.bullets:
            if line_clear(self.arena, me.x, me.y, b.x, b.y):
                out.append([round(b.x, 1), round(b.y, 1),
                            round(b.dx, 4), round(b.dy, 4), b.owner])
        return out

    def set_input(self, keys, aim, fire) -> None:
        with self._lock:
            self.input = {"keys": keys, "aim": aim, "fire": fire}

    def pop_events(self) -> list[dict]:
        out = list(self.events)
        self.events.clear()
        return out