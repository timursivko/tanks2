#!/usr/bin/env python
"""TANKSIM — точка входа.
Запуск интерфейса::

    python run.py                 # веб-интерфейс на http://127.0.0.1:8765
    python run.py --port 9000     # другой порт
    python run.py --host 127.0.0.1  # только локально (не виден в сети)
    python run.py --cli ai/01_chaser.tankp.py ai/02_flanker.tankp.py --map arena

Прогон без браузера (удобно для отладки и замеров)::

    python run.py --cli ai/06_berserk.tankp.py ai/05_turtle.tankp.py --seed 7 -v
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import AI_DIR, BALANCE, EXAMPLES_DIR, ROOT as PROJECT_ROOT  # noqa: E402

DEFAULT_HOST = "0.0.0.0"  # слушаем все интерфейсы: LAN и ZeroTier
DEFAULT_PORT = 8765


def local_ips() -> list[str]:
    """Нелупбэк IPv4-адреса этой машины — куда можно зайти снаружи."""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


def find_free_port(host: str, preferred: int) -> int:
    """Если порт занят — берём следующий свободный."""
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"не нашлось свободного порта начиная с {preferred}")


def resolve_program(key: str) -> str:
    """Путь к программе: абсолютный либо относительный к корню проекта."""
    p = Path(key)
    if p.exists():
        return str(p.resolve())
    for base in (AI_DIR, EXAMPLES_DIR):
        cand = base / key
        if cand.exists():
            return str(cand.resolve())
        cand = base / f"{key}.tankp.py"
        if cand.exists():
            return str(cand.resolve())
    raise SystemExit(f"программа «{key}» не найдена (ищите в ai/ или examples/)")


def run_cli(args: argparse.Namespace) -> int:
    """Расчёт боя в консоли: без сервера и браузера."""
    from config import BattleConfig, PlayerConfig
    from battle.runner import BattleRun
    from engine.program import load_program
    from engine.replay import save_replay

    path_a = resolve_program(args.cli[0])
    path_b = resolve_program(args.cli[1])
    meta_a, meta_b = load_program(path_a), load_program(path_b)
    for m in (meta_a, meta_b):
        if not m.ok:
            print(f"ОШИБКА {m.path}: {'; '.join(m.errors)}")
            return 2

    cfg = BattleConfig(
        a=PlayerConfig(kind="script", source=path_a, name=meta_a.name, color=meta_a.color),
        b=PlayerConfig(kind="script", source=path_b, name=meta_b.name, color=meta_b.color),
        map_name=args.map, budget_ms=args.budget, seed=args.seed,
        max_seconds=args.seconds)
    t0 = time.perf_counter()
    run = BattleRun(cfg, "cli")
    run.run_sync()
    dt = time.perf_counter() - t0

    if run.status != "done":
        print(f"ОШИБКА: {run.error}")
        return 1
    rep = run.replay
    s = rep.summary
    print(f"=== {s['a']['name']}  против  {s['b']['name']}  "
          f"({s['map']['name']}, сид {s['seed']})")
    print(f"итог: {s['outcome']} ({s['reason_label']}), "
          f"длительность {s['duration']:.1f} с, расчёт {dt:.2f} с")
    for key, label in (("a", "A"), ("b", "B")):
        t = s[key]
        print(f"  {label} {t['name']:<22} ХП {t['hp_left']:>5}  потеряно {t['damage_taken']:>5}  "
              f"выстрелов {t['shots']:>3}  попаданий {t['hits']:>3} ({t['accuracy']:>5}%)  "
              f"рикошетов {t['ricochets']:>2}  "
              f"решение max {t['think_ms_max']:.2f} мс / avg {t['think_ms_avg']:.2f} мс  "
              f"нарушений {t['over_budget']}")
    if args.verbose:
        print("  события:")
        for row in rep.events[:args.events]:
            print("   ", row)
    if args.save:
        print(f"  реплей сохранён: {save_replay(rep)}")
    return 0


def run_server(args: argparse.Namespace) -> int:
    import uvicorn

    host = args.host
    port = find_free_port(host, args.port)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{port}/"

    if args.open:
        threading.Thread(target=lambda: (time.sleep(0.8), webbrowser.open(url)),
                         daemon=True).start()

    print("=" * 62)
    print(f"  TANKSIM  —  арена танковых программ")
    print(f"  интерфейс:  {url}")
    if shown != host:
        for ip in local_ips():
            print(f"  из сети:    http://{ip}:{port}/")
    print(f"  программы:  {AI_DIR}")
    print(f"  примеры:    {EXAMPLES_DIR}")
    print(f"  баланс:     HP {BALANCE.hp}, КД {BALANCE.reload} с, "
          f"тик {BALANCE.tick_rate} Гц, лимит боя {BALANCE.max_seconds} с, "
          f"бюджет решения {BALANCE.default_budget_ms} мс")
    print("=" * 62)
    print("  Ctrl+C — остановить")
    uvicorn.run("server.app:app", host=host, port=port, log_level=args.log_level,
                reload=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tanksim", description="TANKSIM — танковая арена ИИ")
    p.add_argument("--cli", nargs=2, metavar=("A", "B"), default=None,
                   help="рассчитать бой двух программ в консоли")
    p.add_argument("--map", default="arena", help="карта (arena, corridor, colonnade, "
                                                  "fortress, meadow)")
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--budget", type=float, default=BALANCE.default_budget_ms,
                   help="лимит времени на решение, мс")
    p.add_argument("--seconds", type=int, default=BALANCE.max_seconds,
                   help="максимальная длительность боя, с")
    p.add_argument("--save", action="store_true", help="сохранить реплей в saves/replays")
    p.add_argument("-v", "--verbose", action="store_true", help="печатать события боя")
    p.add_argument("--events", type=int, default=40, help="сколько событий показать")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-open", dest="open", action="store_false",
                   help="не открывать браузер")
    p.add_argument("--log-level", default="warning")
    args = p.parse_args(argv)

    if args.cli:
        return run_cli(args)
    return run_server(args)


if __name__ == "__main__":
    raise SystemExit(main())