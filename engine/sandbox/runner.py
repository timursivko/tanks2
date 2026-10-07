"""Песочница на стороне сервера: запуск, обмен, жёсткие таймауты, статистика.

Каждый танк живёт в отдельном процессе (``python -m engine.sandbox.worker``).
Родитель читает ответ через очередь с дедлайном: если скрипт не уложился в
бюджет — команда в этом тике считается пустой и фиксируется нарушение;
если процесс вообще не отвечает (завис, съел память) — он убивается и
поднимается заново.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import BALANCE, Balance

WORKER_MODULE = "engine.sandbox.worker"


class ProgramError(Exception):
    """Программа танка не загрузилась или упала на старте."""


@dataclass
class Decision:
    """Результат одного тика по одному танку."""

    action: dict = field(default_factory=dict)
    ms: float = 0.0
    logs: list[str] = field(default_factory=list)
    error: str = ""
    kind: str = ""          # "ok" | "over_budget" | "error" | "timeout" | "dead"

    @property
    def ok(self) -> bool:
        return not self.error


class ScriptRunner:
    """Один скрипт = один процесс."""

    def __init__(self, name: str, source_path: str, meta: dict,
                 budget_ms: float, bal: Balance):
        self.name = name
        self.source_path = source_path
        self.meta = meta
        self.budget_ms = budget_ms
        self.bal = bal
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue()
        self.reader: threading.Thread | None = None
        self.map_payload: dict = {}
        self.ctx: dict = {}
        self.restarts = 0
        self.last_error = ""
        self.closed = False

    # --- жизненный цикл -----------------------------------------------------

    def _load_msg(self) -> dict:
        """Команда загрузки программы.

        ``root`` — каталог пакета: у одиночного скрипта его нет, у пакета воркер
        кладёт этот каталог в ``sys.path``, и соседние модули и веса видны
        обычным ``import`` и ``open``.
        """
        return {"op": "load", "path": self.source_path,
                "source": Path(self.source_path).read_text(encoding="utf-8"),
                "root": str(self.meta.get("package") or ""),
                "meta": self.meta}

    def start(self, map_payload: dict, ctx: dict) -> None:
        """Поднимает процесс и прогоняет загрузку модуля. Бросает исключение при ошибке."""
        self.map_payload = map_payload
        self.ctx = ctx
        self._spawn()
        self._expect({"op": "init", "map": map_payload, "meta": self.meta}, 5.0, "инициализация")
        self._expect(self._load_msg(), 10.0, "загрузка программы")
        self._expect({"op": "start", "ctx": ctx}, 5.0, "on_start")

    def _expect(self, msg: dict, timeout: float, phase: str) -> dict:
        """Обмен с обязательной проверкой ответа — ошибки программы видны сразу."""
        resp = self._exchange(msg, timeout)
        if resp.get("op") == "error":
            detail = resp.get("message", "неизвестная ошибка")
            tb = (resp.get("traceback") or "").strip()
            self.last_error = detail
            self._kill()
            raise ProgramError(f"{phase}: {detail}" + (f"\n{tb}" if tb else ""))
        return resp

    def _spawn(self) -> None:
        root = str(Path(__file__).resolve().parents[2])
        env = dict(os.environ)
        env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["TANKSIM_SANDBOX"] = "1"
        # Никаких выводов в stdout — протокол занимает канал целиком.
        env["PYTHONWARNINGS"] = "ignore"
        self.proc = subprocess.Popen(  # noqa: S603 — фиксированный модуль и путь
            [sys.executable, "-u", "-m", WORKER_MODULE],
            cwd=root, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.q = queue.Queue()
        self.reader = threading.Thread(target=self._read_loop, daemon=True,
                                       name=f"tankp-reader-{self.name}")
        self.reader.start()

    def _read_loop(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.q.put(json.loads(line))
                except json.JSONDecodeError:
                    self.q.put({"op": "garbage", "raw": line[:400]})
        except Exception as exc:  # noqa: BLE001 — поток не должен падать
            self.q.put({"op": "error", "phase": "pipe",
                        "message": f"обрыв канала: {exc}"})
        finally:
            self.q.put({"op": "eof"})

    def close(self) -> None:
        self.closed = True
        self._kill()

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write('{"op": "stop"}\n')
                proc.stdin.flush()
                proc.wait(timeout=0.3)
        except Exception:  # noqa: BLE001
            pass
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass

    def restart(self) -> None:
        """Поднимает процесс заново — после зависания или падения."""
        self.restarts += 1
        self._kill()
        try:
            self._spawn()
            self._expect({"op": "init", "map": self.map_payload, "meta": self.meta},
                         5.0, "инициализация")
            self._expect(self._load_msg(), 10.0, "загрузка программы")
            self._expect({"op": "start", "ctx": self.ctx}, 5.0, "on_start")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"перезапуск не удался: {exc}"

    # --- обмен --------------------------------------------------------------

    def _send(self, msg: dict) -> bool:
        proc = self.proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
            return True
        except Exception as exc:  # noqa: BLE001 — обрыв пайпа
            self.last_error = f"не удалось отправить команду: {exc}"
            return False

    def _exchange(self, msg: dict, timeout: float) -> dict:
        if not self._send(msg):
            return {"op": "error", "phase": msg.get("op", "?"),
                    "message": self.last_error or "процесс не запущен"}
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return {"op": "error", "phase": msg.get("op", "?"),
                    "message": f"нет ответа за {timeout:.1f} с"}

    def _drain(self) -> None:
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                return

    def hard_timeout_s(self) -> float:
        """Жёсткий предел ожидания: в разы больше бюджета, но не меньше 250 мс."""
        return max(self.bal.hard_kill_ms, self.budget_ms * self.bal.kill_grace) / 1000.0

    # --- такт ----------------------------------------------------------------

    def decide(self, obs_payload: dict) -> Decision:
        """Опрашивает скрипт. Никогда не бросает — всегда возвращает результат."""
        if self.proc is None:
            try:
                self.restart()
            except Exception as exc:  # noqa: BLE001
                return Decision(error=f"процесс мёртв: {exc}", kind="dead")
            if self.proc is None:
                return Decision(error=self.last_error or "процесс не запущен", kind="dead")

        self._drain()
        t_send = time.perf_counter()
        if not self._send({"op": "tick", "obs": obs_payload}):
            return Decision(error=self.last_error or "обрыв связи с процессом", kind="dead")

        # Основной дедлайн — бюджет + запас на IPC, затем жёсткий kill.
        soft = self.budget_ms / 1000.0 + 0.020
        try:
            msg = self.q.get(timeout=soft)
        except queue.Empty:
            return self._hard_fail("превышен бюджет решения (жёсткий таймаут)")

        op = msg.get("op")
        if op == "cmd":
            ms = float(msg.get("ms", 0.0))
            logs = [str(s) for s in msg.get("logs", [])]
            over = ms > self.budget_ms
            return Decision(action=msg.get("action") or {}, ms=ms, logs=logs,
                            kind="over_budget" if over else "ok")
        if op == "error":
            detail = msg.get("message", "ошибка")
            tb = (msg.get("traceback") or "").strip()
            if tb:
                detail = f"{detail}\n{tb}"
            self.last_error = detail
            return Decision(error=detail, kind="error",
                            logs=[str(s) for s in msg.get("logs", [])])
        if op == "eof":
            return self._hard_fail("процесс завершился неожиданно")
        return Decision(error=f"непонятный ответ воркера: {msg.get('raw', op)!r}", kind="error")

    def _hard_fail(self, reason: str) -> Decision:
        self.last_error = reason
        self.restarts += 1
        self._kill()
        return Decision(error=reason, kind="timeout")


def run_headless(source: str, name: str = "inline",
                 bal: Balance | None = None) -> ScriptRunner:
    """Готовит одноразовый раннер из исходника (для тестов)."""
    from config import SCRIPTS_DIR

    from engine.program import parse_source

    bal = bal or BALANCE
    path = SCRIPTS_DIR / f"_inline_{name}.tankp.py"
    path.write_text(source, encoding="utf-8")
    meta = parse_source(source, str(path))
    return ScriptRunner(name, str(path), meta.to_dict(), bal.default_budget_ms, bal)