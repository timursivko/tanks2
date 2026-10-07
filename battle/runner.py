"""Прогон боя: считаем целиком, отдаём реплей для последующего просмотра.

Симуляция идёт в своём потоке и честно соблюдает бюджет времени на решение —
никакого «ускорения», за которое платит один из участников.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field

from config import BALANCE, Balance, BattleConfig
from engine.action import Action
from engine.events import EndReason, EventKind, Outcome
from engine.grid import Arena, load_map
from engine.observation import build_observation, map_payload
from engine.program import load_any_program
from engine.replay import Replay, prune_replays, save_replay
from engine.sandbox import Decision, ScriptRunner
from engine.world import World

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


@dataclass
class Side:
    """Описание участника, подготовленное к бою."""

    kind: str
    name: str
    color: str
    source_path: str = ""
    meta: dict = field(default_factory=dict)
    program: str = ""


class BattleRun:
    """Один бой: состояние, прогресс и результат.

    ``save=False`` считает бой, но не оставляет реплей на диске. Так работает
    пакетных прогонов: их боёв сотни, а смотреть их всё равно некому, а
    ``prune_replays`` вытеснял бы подряд идущие файлы, и реплей свежего боя
    сетки клиент мог бы уже не найти.
    """

    def __init__(self, cfg: BattleConfig, run_id: str, bal: Balance | None = None,
                 save: bool = True):
        self.cfg = cfg
        self.id = run_id
        self.bal = bal or BALANCE
        self.save = save
        self.status = STATUS_QUEUED
        self.progress = 0.0
        self.message = "в очереди"
        self.error = ""
        self.replay: Replay | None = None
        self.finished_at = 0.0
        self.duration_sim = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # --- запуск -------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"battle-{self.id}",
                                        daemon=True)
        self._thread.start()

    def run_sync(self) -> None:
        self._run()

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return self._thread.is_alive() is False

    # --- собственно бой -----------------------------------------------------

    def _prepare(self, player) -> Side:
        if player.kind == "manual":
            return Side(kind="manual", name=player.name or "Игрок",
                        color=player.color or "#ffd166")
        path = player.source
        if not path:
            raise ValueError(f"для танка «{player.name or player.kind}» не выбран файл")
        # Точка входа пакета — main.py в каталоге: читаем её, но воркеру отдаём
        # и корень каталога, чтобы соседние модули и веса тоже были доступны.
        meta = load_any_program(path)
        if not meta.ok:
            raise ValueError(f"{path}: " + "; ".join(meta.errors))
        return Side(kind="script", name=player.name or meta.name, color=player.color or meta.color,
                    source_path=str(path), meta=meta.to_dict(), program=meta.entry)

    def _run(self) -> None:
        started = time.perf_counter()
        runners: dict[str, ScriptRunner] = {}
        try:
            with self._lock:
                self.status = STATUS_RUNNING
                self.message = "загрузка карты"
            if self.cfg.a.kind == "manual" or self.cfg.b.kind == "manual":
                raise ValueError("ручное управление возможно только в живом бою")
            arena = load_map(self.cfg.map_name)
            side_a = self._prepare(self.cfg.a)
            side_b = self._prepare(self.cfg.b)
            if not side_a.name:
                side_a.name = "Танк A"
            if not side_b.name:
                side_b.name = "Танк B"

            world = World.create(
                arena, self.bal, seed=self.cfg.seed,
                names=(side_a.name, side_b.name),
                colors=(side_a.color, side_b.color),
                kinds=(side_a.kind, side_b.kind),
                tick_rate=self.bal.tick_rate,
                max_seconds=min(self.cfg.max_seconds, self.bal.max_seconds),
            )
            mp = map_payload(world)

            # Поднимаем процесс для каждого скриптового участника.
            runners: dict[str, ScriptRunner] = {}
            for key, side in (("a", side_a), ("b", side_b)):
                if side.kind != "script":
                    continue
                runner = ScriptRunner(side.name, side.source_path, side.meta,
                                      self.cfg.budget_ms, self.bal)
                runner.start(mp, {"tank": 0 if key == "a" else 1,
                                  "map": self.cfg.map_name,
                                  "seed": self.cfg.seed,
                                  "budget_ms": self.cfg.budget_ms,
                                  "hp": self.bal.hp,
                                  "reload": self.bal.reload})
                runners[key] = runner

            replay = Replay(
                id=Replay.new_id(),
                created=time.time(),
                map=arena.to_dict(),
                config=self.cfg.to_dict(),
                meta={
                    "a": {"name": side_a.name, "color": side_a.color, "kind": side_a.kind,
                          "script": side_a.source_path,
                          "program_meta": side_a.meta},
                    "b": {"name": side_b.name, "color": side_b.color, "kind": side_b.kind,
                          "script": side_b.source_path,
                          "program_meta": side_b.meta},
                    "balance": self.bal.to_dict(),
                    "map": {"id": self.cfg.map_name, "name": arena.name,
                            "size": f"{arena.width}x{arena.height}",
                            "symmetric": arena.is_symmetric()},
                    "seed": self.cfg.seed,
                    "budget_ms": self.cfg.budget_ms,
                },
            )
            replay.push_frame(world.snapshot())
            replay.push_event({"kind": EventKind.START, "t": 0.0,
                               "note": f"{side_a.name} против {side_b.name} на карте «{arena.name}»"})

            done_bullets: set[int] = set()
            while not world.over:
                actions = []
                think_ms = [0.0, 0.0]
                bad = [False, False]
                for i in range(2):
                    obs = build_observation(world, i, self.cfg.budget_ms)
                    runner = runners.get("a" if i == 0 else "b")
                    if runner is None:
                        raise ValueError("ручное управление доступно только в живом бою")
                    dec = runner.decide(obs)
                    think_ms[i] = dec.ms
                    bad[i] = dec.kind in ("over_budget", "timeout")
                    if dec.error:
                        _log(replay, world, i, f"[{dec.kind}] {dec.error.splitlines()[0]}")
                    for line in dec.logs:
                        _log(replay, world, i, line)
                    # Не уложился в бюджет — команда не засчитывается: тик
                    # проходит как пустой, иначе можно было бы залить бой огнём.
                    if dec.kind == "over_budget":
                        actions.append(Action())
                    else:
                        actions.append(Action.clamp(dec.action))
                    _accumulate(world.tanks[i], dec, self.cfg.budget_ms)

                alive_before = {id(b): b for b in world.bullets}
                events = world.step(actions)
                for ev in events:
                    replay.push_event(ev)
                replay.push_frame(world.snapshot((think_ms[0], think_ms[1]),
                                                 (bad[0], bad[1])))

                # Статическая траектория закрывается, когда снаряд исчез:
                # пока он жив, его движение уже видно в покадровых снимках.
                alive_now = {id(b) for b in world.bullets}
                for bid, b in alive_before.items():
                    if bid not in alive_now and bid not in done_bullets:
                        replay.finish_bullet(b.track, b.owner, b.speed)
                        done_bullets.add(bid)

                with self._lock:
                    self.progress = world.tick / max(1, world.max_ticks)
                    self.message = f"тик {world.tick}/{world.max_ticks}"

            # Хвост: закрываем висящие снаряды.
            for b in world.bullets:
                if b.alive and id(b) not in done_bullets:
                    replay.finish_bullet(b.track, b.owner, b.speed)
                    done_bullets.add(id(b))

            replay.summary = self._summarize(world, side_a, side_b, replay)
            replay.push_event({"kind": EventKind.END, "t": world.tick * world.dt,
                               "outcome": world.outcome, "reason": world.end_reason})
            # Сначала сохраняем на диск и только потом отдаём статус «готово».
            # Наоборот клиент успевал увидеть done, запросить реплей и получить
            # 404: файл к этому моменту ещё не появился.
            if self.save:
                try:
                    save_replay(replay)
                    prune_replays(keep=20)
                except OSError:
                    traceback.print_exc()
            with self._lock:
                self.replay = replay
                self.status = STATUS_DONE
                self.progress = 1.0
                self.message = "бой рассчитан"
                self.finished_at = time.time()
                self.duration_sim = time.perf_counter() - started
        except Exception as exc:  # noqa: BLE001 — ошибка должна попасть в UI
            with self._lock:
                self.status = STATUS_ERROR
                self.error = f"{type(exc).__name__}: {exc}"
                self.message = "ошибка"
                self.finished_at = time.time()
                self.duration_sim = time.perf_counter() - started
            traceback.print_exc()
        finally:
            for r in runners.values():
                r.close()

    # --- итоги --------------------------------------------------------------

    def _summarize(self, world: World, side_a: Side, side_b: Side,
                   replay: Replay) -> dict:
        sa, sb = world.tanks[0].stats(), world.tanks[1].stats()
        sa.update({"kind": side_a.kind, "script": side_a.source_path,
                   "hp_max": world.bal.hp})
        sb.update({"kind": side_b.kind, "script": side_b.source_path,
                   "hp_max": world.bal.hp})
        # «Потери» — то, что считает игрок: сколько ХП ушло у нашего танка.
        return {
            "outcome": world.outcome,
            "reason": world.end_reason,
            "reason_label": _reason_label(world.end_reason),
            "duration": round(world.tick * world.dt, 3),
            "ticks": world.tick,
            "max_seconds": world.max_ticks / world.bal.tick_rate,
            "a": sa,
            "b": sb,
            "shots_total": sa["shots"] + sb["shots"],
            "hits_total": sa["hits"] + sb["hits"],
            "map": replay.meta["map"],
            "seed": self.cfg.seed,
            "budget_ms": self.cfg.budget_ms,
            "sim_seconds": round(self.duration_sim or 0.0, 2),
        }


def _accumulate(tank, dec: Decision, budget: float) -> None:
    tank.think_calls += 1
    tank.think_ms_total += dec.ms
    tank.think_ms_max = max(tank.think_ms_max, dec.ms)
    if dec.ms > budget:
        tank.over_budget += 1
    if dec.kind == "error":
        tank.errors += 1
    elif dec.kind == "timeout":
        tank.timeouts += 1


def _log(replay: Replay, world: World, i: int, text: str) -> None:
    replay.push_event({"kind": "log", "t": world.tick * world.dt, "tank": i,
                       "text": text[:400]})


def _reason_label(reason: str) -> str:
    return {
        EndReason.DESTROYED: "танк уничтожен",
        EndReason.TIMEOUT: "время вышло — ничья",
        EndReason.MUTUAL: "оба уничтожены — ничья",
        EndReason.ABORTED: "бой прерван",
    }.get(reason, reason)


def winner_of(summary: dict) -> str:
    return summary.get("outcome", Outcome.DRAW)