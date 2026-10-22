"""Замер скорости турнира: один и тот же набор боёв в разных режимах.

Инструмент отвечает на вопрос «насколько быстрее и не изменился ли счёт»:
бои берутся одни и те же, меняются только движок (``--engine``) и число
процессов (``--jobs``). После каждого режима итоги сверяются с эталонным
Python-прогоном — совпадать должны тик, исход и вся статистика.

    python tools/bench.py                       # 60 боёв, 4 режима
    python tools/bench.py --battles 120 --seconds 60
    python tools/bench.py --markdown            # таблица для PR/README
    python tools/bench.py --dump /tmp/tasks.json  # список задач: те же бои
                                                  # можно замерить старым кодом

Бои выбираются детерминированно (фиксированное зерно и равномерный шаг по
списку пар), поэтому два прогона ``bench.py`` сравнимы между собой.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))
sys.path.insert(0, str(ROOT / "tools"))

from config import BALANCE  # noqa: E402
from leaderboard import (DEFAULT_MAPS, DEFAULT_SEEDS, _mp_context,  # noqa: E402
                         _run_match, _workers_default, discover)

#: Поля статистики, которые зависят от машины (время решения), — их сверять
#: нельзя, они и в двух одинаковых прогонах разные.
TIMING_KEYS = ("think_total", "think_calls", "think_max", "over_budget")


def sample_tasks(n: int, maps: list[str], seeds: list[int],
                 seconds: int) -> list[tuple]:
    """Детерминированная выборка боёв турнира.

    Пары идут с равномерным шагом по всему списку (а не первые подряд),
    стороны чередуются, карты и сиды тоже — выборка представляет турнир.
    """
    fighters, _ = discover()
    pairs = list(itertools.combinations(fighters, 2))
    if not pairs:
        raise SystemExit("участников меньше двух — нечего замерять")
    bal = replace(BALANCE, max_seconds=seconds)
    n = min(n, len(pairs) * len(maps) * len(seeds) * 2)
    step = max(1, len(pairs) // n)
    picked = []
    for i in range(n):
        a, b = pairs[(i * step) % len(pairs)]
        x, y = (a, b) if i % 2 == 0 else (b, a)
        picked.append((str(x.path), str(y.path), maps[i % len(maps)],
                       seeds[i % len(seeds)], seconds, bal))
    # Порядок задач — тоже часть выборки: перемешиваем фиксированным зерном,
    # чтобы в выборку попали разные пары, а не только соседние по списку.
    random.Random(20260101).shuffle(picked)
    return [(i,) + task for i, task in enumerate(picked)]


def run_seq(tasks: list[tuple], engine: str) -> tuple[list[tuple], float]:
    """Все бои подряд в этом процессе."""
    prepared = [(t[0], t[1], t[2], t[3], t[4], t[5], engine, t[6])
                for t in tasks]
    t0 = time.perf_counter()
    got = [_run_match(task) for task in prepared]
    return got, time.perf_counter() - t0


def run_par(tasks: list[tuple], engine: str, jobs: int
            ) -> tuple[list[tuple], float]:
    """Те же бои в ``jobs`` процессах; результаты — в порядке задач."""
    from concurrent.futures import ProcessPoolExecutor
    prepared = [(t[0], t[1], t[2], t[3], t[4], t[5], engine, t[6])
                for t in tasks]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=jobs, mp_context=_mp_context()) as pool:
        got = [r for r in pool.map(_run_match, prepared)]
    return got, time.perf_counter() - t0


def engine_is_native(mode: str) -> bool:
    """В каком из режимов считается нативное ядро."""
    return mode.startswith("nat")


def signature(results: list[tuple]) -> list[tuple]:
    """Итоги боёв без машинно-зависимых полей — то, что обязано совпасть."""
    out = []
    for idx, tick, outcome, stats in results:
        out.append((idx, tick, outcome,
                    tuple(tuple(sorted((k, v) for k, v in st.items()
                                       if k not in TIMING_KEYS))
                          for st in stats)))
    return out


def fmt_ms(x: float) -> str:
    return f"{x * 1000:.0f} мс" if x < 60 else f"{x:.1f} с"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench", description="Замер скорости турнира в разных режимах")
    ap.add_argument("--battles", type=int, default=60,
                    help="сколько боёв брать в выборку (по умолчанию %(default)s)")
    ap.add_argument("--maps", nargs="+", default=DEFAULT_MAPS,
                    help="карты выборки (по умолчанию как в турнире)")
    ap.add_argument("--seeds", nargs="+", type=int, default=DEFAULT_SEEDS,
                    help="сиды выборки (по умолчанию как в турнире)")
    ap.add_argument("--seconds", type=int, default=int(BALANCE.max_seconds),
                    help="лимит боя, с (по умолчанию %(default)s)")
    ap.add_argument("--jobs", type=int, default=0,
                    help="процессов в параллельных режимах "
                         "(0 — по числу ядер)")
    ap.add_argument("--skip", nargs="+", default=(), choices=("py", "native",
                                                             "py-par", "nat-par"),
                    help="какие режимы не запускать")
    ap.add_argument("--markdown", action="store_true",
                    help="напечатать таблицу в Markdown")
    ap.add_argument("--dump", default=None,
                    help="записать выборку боёв в JSON (для замеров старым кодом)")
    args = ap.parse_args(argv)

    from engine.native import available, load_error
    if not available():
        print(f"нативное ядро недоступно: {load_error()}", flush=True)

    tasks = sample_tasks(args.battles, args.maps, args.seeds, args.seconds)
    jobs = _workers_default() if args.jobs <= 0 else args.jobs
    if args.dump:
        Path(args.dump).write_text(json.dumps(
            [[t[1], t[2], t[3], t[4], t[5]] for t in tasks]), encoding="utf-8")
        print(f"выборка из {len(tasks)} боёв записана в {args.dump}")

    modes = [("py", "Python-движок, 1 процесс", 1),
             ("native", "нативное ядро, 1 процесс", 1),
             ("py-par", "Python-движок, %d проц." % jobs, jobs),
             ("nat-par", "нативное ядро, %d проц." % jobs, jobs)]

    print(f"выборка: {len(tasks)} боёв, карты {', '.join(args.maps)}, "
          f"сиды {args.seeds}, лимит {args.seconds} с, ядер {jobs}\n",
          flush=True)

    rows = []
    reference: list[tuple] | None = None
    for name, label, procs in modes:
        if name in args.skip:
            continue
        if engine_is_native(name) and not available():
            continue
        print(f"{label}: считаю…", end="", flush=True)
        engine = "native" if engine_is_native(name) else "py"
        got, elapsed = (run_seq(tasks, engine) if procs <= 1
                        else run_par(tasks, engine, procs))
        sig = signature(got)
        ok = "—"
        if reference is None:
            reference = sig
            ok = "эталон"
        else:
            ok = "совпало" if sig == reference else "РАСХОЖДЕНИЕ!"
        ticks = sum(r[1] for r in got)
        rows.append({"title": label, "elapsed": elapsed,
                     "ms": elapsed / len(tasks) * 1000.0,
                     "tps": ticks / elapsed if elapsed else 0.0,
                     "ok": ok})
        print(f"\r{label}: {fmt_ms(elapsed)}, "
              f"{elapsed / len(tasks) * 1000:.0f} мс/бой, "
              f"{ticks / elapsed:,.0f} тиков/с, {ok}".replace(",", " "),
              flush=True)

    base = rows[0]["elapsed"] if rows else 0.0
    if args.markdown:
        print("\n| Режим | Время | мс/бой | тиков/с | Ускорение | Сверка |")
        print("|---|--:|--:|--:|--:|---|")
        for r in rows:
            speed = f"×{base / r['elapsed']:.2f}" if r["elapsed"] else "—"
            print(f"| {r['title']} | {fmt_ms(r['elapsed'])} | "
                  f"{r['ms']:.0f} | {r['tps']:,.0f} | {speed} | {r['ok']} |"
                  .replace(",", " "))
    else:
        print()
        for r in rows:
            speed = f"×{base / r['elapsed']:.2f}" if r["elapsed"] else "—"
            print(f"{r['title']:<28} {fmt_ms(r['elapsed']):>8}  "
                  f"{r['ms']:>6.0f} мс/бой  {r['tps']:>9,.0f} тиков/с  "
                  f"{speed:>6}  {r['ok']}".replace(",", " "))

    bad = [r for r in rows if r["ok"].startswith("РАСХОЖ")]
    if bad:
        print("\nИТОГ: результаты разошлись — ускорение недействительно")
        return 1
    print("\nИТОГ: все режимы дали одинаковый счёт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
