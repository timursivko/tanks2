"""Воркер песочницы: отдельный процесс на каждый танк.

Запускается как обычный скрипт, общается с родителем построчным JSON.
Зависший скрипт убивается снаружи — здесь ничего не мешает.

Если родитель исчез, а команды перестали приходить, воркер выходит сам:
иначе осиротевший процесс продолжал бы крутиться вхолостую.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

#: Сколько секунд ждать следующую команду, прежде чем считать себя брошенным
IDLE_TIMEOUT = 5.0


def _bootstrap() -> None:
    """Готовит пути так, чтобы ``import tankp`` работал из любого каталога."""
    here = Path(__file__).resolve()
    root = here.parents[2]            # корень проекта
    sys.path.insert(0, str(here.parents[1] / "sdk"))
    sys.path.insert(0, str(root))
    # Не даём программе случайно утащить весь GUI-стек.
    os.environ.setdefault("TANKSIM_SANDBOX", "1")


class Runner:
    """Обёртка над загруженной программой: загрузка, старт, тик, лог."""

    def __init__(self) -> None:
        self.module = None
        self.entry = None
        self.meta: dict = {}
        self.tankp = None
        self.mapview = None

    # --- загрузка ---

    def load(self, path: str, source: str, root: str = "") -> None:
        import types

        _bootstrap()
        if root:
            # Пакет: его каталог встаёт первым, поэтому `import my_brain` и
            # `open('weights.json')` находят файлы рядом с main.py. Движок и
            # SDK лежат глубже и остаются видны как обычно.
            p = str(Path(root).resolve())
            if p not in sys.path:
                sys.path.insert(0, p)
        mod = types.ModuleType("tank_program")
        mod.__file__ = path
        code = compile(source, path, "exec")
        sys.modules["tank_program"] = mod
        import tankp  # noqa: PLC0415 — путь готовит _bootstrap

        self.tankp = tankp
        # Даём программе тот же модуль tankp, что и движку: общий log().
        exec(code, mod.__dict__)  # noqa: S102 — это и есть запуск пользовательского кода

        prog = mod.__dict__.get("program")
        fn = mod.__dict__.get("on_tick")
        if prog is not None:
            if not hasattr(prog, "on_tick") and hasattr(prog, "__call__"):
                target = prog
            elif hasattr(prog, "on_tick"):
                target = prog.on_tick
                if hasattr(prog, "on_start"):
                    self._start = prog.on_start
            else:
                raise AttributeError("в 'program' нет метода on_tick")
        elif callable(fn):
            target = fn
            self._start = mod.__dict__.get("on_start")
        else:
            raise AttributeError("нет точки входа: 'program' или 'on_tick(o)'")
        self.entry = target

    def _start(self, ctx):  # noqa: D401 — заглушка, если on_start не задан
        return None

    # --- работа ---

    def start(self, ctx: dict) -> None:
        """Одноразовый вызов on_start, если он есть."""
        fn = getattr(self, "_start", None)
        if callable(fn):
            fn(ctx)

    def tick(self, obs_payload: dict) -> dict:
        """Вызывает on_tick и возвращает команду со временем выполнения."""
        t0 = time.perf_counter()
        obs = self.tankp.build_observation(obs_payload, self.mapview)
        action = self.entry(obs)
        ms = (time.perf_counter() - t0) * 1000.0
        logs = self.tankp._take_logs()  # noqa: SLF001 — свой же SDK
        return {
            "action": action.to_dict() if hasattr(action, "to_dict") else action,
            "ms": ms,
            "logs": logs,
        }


def _read_lines(stream, inbox: "queue.Queue[str | None]") -> None:
    """Читает команды в отдельном потоке.

    Так главный цикл может заметить, что родитель куда-то пропал: если
    команды не приходят дольше ``IDLE_TIMEOUT``, воркер выходит сам, иначе
    он остался бы висеть и жечь процессор после падения хозяина.
    """
    try:
        for line in stream:
            inbox.put(line)
    finally:
        inbox.put(None)


def main() -> int:
    _bootstrap()
    runner = Runner()
    mapview = None
    stdout = sys.stdout

    inbox: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=_read_lines, args=(sys.stdin, inbox),
                     name="worker-stdin", daemon=True).start()

    def send(obj: dict) -> None:
        stdout.write(json.dumps(obj, ensure_ascii=False, default=_default) + "\n")
        stdout.flush()

    while True:
        try:
            line = inbox.get(timeout=IDLE_TIMEOUT)
        except queue.Empty:
            return 0                       # родитель молчит — выходим сами
        if line is None:
            return 0                       # stdin закрыт, работа окончена
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            send({"op": "error", "phase": "protocol", "message": str(exc)})
            continue
        op = msg.get("op")

        if op == "init":
            try:
                import tankp

                runner.tankp = tankp
                runner.mapview = tankp.MapView(msg["map"])
                send({"op": "ready", "meta": msg.get("meta", {})})
            except Exception as exc:  # noqa: BLE001
                send({"op": "error", "phase": "init",
                      "message": str(exc), "traceback": traceback.format_exc()})
            continue

        if op == "load":
            try:
                runner.load(msg["path"], msg["source"], msg.get("root", ""))
                send({"op": "loaded", "meta": msg.get("meta", {})})
            except BaseException as exc:  # noqa: BLE001 — в том числе SystemExit
                send({"op": "error", "phase": "load",
                      "message": f"{type(exc).__name__}: {exc}",
                      "traceback": _short_traceback()})
            continue

        if op == "start":
            try:
                runner.start(msg.get("ctx", {}))
                send({"op": "started"})
            except BaseException as exc:  # noqa: BLE001
                send({"op": "error", "phase": "start",
                      "message": f"{type(exc).__name__}: {exc}",
                      "traceback": _short_traceback()})
            continue

        if op == "tick":
            try:
                out = runner.tick(msg["obs"])
                out["op"] = "cmd"
                send(out)
            except BaseException as exc:  # noqa: BLE001
                send({"op": "error", "phase": "think",
                      "message": f"{type(exc).__name__}: {exc}",
                      "traceback": _short_traceback(),
                      "logs": _safe_logs(runner)})
            continue

        if op == "stop":
            break

        send({"op": "error", "phase": "protocol", "message": f"неизвестная операция {op!r}"})
    return 0


def _safe_logs(runner) -> list[str]:
    try:
        return runner.tankp._take_logs()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return []


def _short_traceback() -> str:
    """Трейс без внутренних кадров движка — только код пользователя."""
    lines = traceback.format_exc().splitlines()
    keep = [ln for ln in lines if "tank_program" in ln or "File \"<" in ln or ln.startswith((" ", "\t"))]
    tail = lines[-1] if lines else ""
    return "\n".join(keep[-14:] + [tail]) if keep else tail


def _default(obj):
    try:
        return obj.to_dict()
    except AttributeError:
        return str(obj)


if __name__ == "__main__":
    sys.exit(main())