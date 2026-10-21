"""Лидербоард: круговой турнир всех танков репозитория, отчёт в Markdown.

Участвуют все программы, которые сейчас лежат в репозитории: семь эталонов из
``ai/``, всё дописанное в ``ai/`` руками и всех агентов из ``pool/`` — новые
подкаталоги pool попадают в турнир автоматически, дефолтные эталоны тоже
всегда на месте.

    python tools/leaderboard.py                     # полный прогон → LEADERBOARD.md
    python tools/leaderboard.py --maps arena duel --seeds 1 2
    python tools/leaderboard.py --deadline 600      # прерваться через 10 минут
    python tools/leaderboard.py --out pool/LEADERBOARD.md

Каждая пара играет в обе стороны: у спавнов разное положение на карте, и
сторона — часть условий боя. Программа загружается заново к каждому бою —
как в песочнице сервера, где на бой поднимается свежий процесс: внутреннее
состояние программ (обучение, кэши) не перетекает из боя в бой.

Отчёт — Markdown: условия, таблица мест (очки = победы + ½ ничьих), разбор
сильнейших друг против друга, восстания слабых (кто и сколько раз обыгрывал
вышестоящих и чьи скальпы самые крупные), подробная статистика (точность,
рикошеты, тараны, HP, длина боя, время решения и вылеты за бюджет), матрица
«каждый с каждым» и разбивка счёта по картам. Полный прогон — примерно
8 минут, поэтому у инструмента есть
``--deadline``: столько секунд wall-clock он потратит на бои и запишет то,
что успело (в отчёте останется пометка о неполном прогоне).
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import itertools
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine" / "sdk"))
sys.path.insert(0, str(ROOT / "tools"))

import tankp  # noqa: E402
from config import BALANCE  # noqa: E402
from engine.action import Action  # noqa: E402
from engine.grid import load_map  # noqa: E402
from engine.observation import build_both, map_payload  # noqa: E402
from engine.program import load_program  # noqa: E402
from engine.world import World  # noqa: E402
from probe import load_inproc  # noqa: E402

#: Карты по умолчанию — тот же набор, что у tools/matrix.py.
DEFAULT_MAPS = ["arena", "corridor", "colonnade", "fortress", "meadow"]
DEFAULT_SEEDS = [1, 2, 3]
#: Сколько сильнейших считаются «топом» — для разбора «топ против топа».
TOP_N = 5

#: Группы в таблице: откуда танк.
GROUP_DEFAULT = "Эталон"   # ai/0N_*.tankp.py — штатные противники
GROUP_AI = "ai/"           # лежит в ai/ рядом с эталонами, но не эталон
GROUP_POOL = "pool"        # опубликован агентом в pool/<agent-id>/

GROUP_RU = {GROUP_DEFAULT: "эталонов", GROUP_AI: "в ai/", GROUP_POOL: "агентов pool"}


@dataclass
class Fighter:
    """Один участник турнира."""

    key: str                  # относительный путь — стабильный ключ
    path: Path
    label: str                # короткая метка для матрицы «каждый с каждым»
    name: str                 # отображаемое имя из заголовка #!TANKP
    author: str
    group: str
    agent: str = ""           # id агента pool (pool/<agent-id>/)
    difficulty: int = 1


@dataclass
class Record:
    """Сумма боёв одного танка."""

    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    dealt: float = 0.0        # нанесённый урон по всем боям
    taken: float = 0.0        # полученный урон по всем боям
    errors: int = 0           # тиков, на которых программа упала
    shots: int = 0            # выстрелов всего
    hits: int = 0             # попаданий всего
    ric: int = 0              # мои снаряды, отскочившие от чужой брони
    ric_taken: int = 0        # чужие снаряды, отскочившие от моей брони
    rams: int = 0             # таранов всего
    hp_left: float = 0.0      # HP в конце боя, сумма
    ticks: int = 0            # длительность боёв в тиках, сумма
    think_total: float = 0.0  # время решений, мс, сумма
    think_calls: int = 0      # решений всего
    think_max: float = 0.0    # самое долгое решение, мс
    over_budget: int = 0      # решений дольше бюджета (10 мс)

    @property
    def points(self) -> float:
        return self.wins + 0.5 * self.draws

    @property
    def pct(self) -> float:
        return self.points / self.games * 100.0 if self.games else 0.0


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def fingerprint(path: Path) -> str:
    """Отпечаток программы: AST без комментариев, переносов и порядка имён import.

    Две копии одного танка, отформатированные по-разному (как ``ai/karakurt``
    и ``pool/arena-06cca649/karakurt``), дают одинаковый отпечаток — лидербоард
    не плодит дубли. Разные программы отпечатки не делят.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return f"raw:{path.resolve().as_posix()}"
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            node.names.sort(key=lambda alias: alias.name)
    return ast.dump(tree)


def discover() -> tuple[list[Fighter], list[tuple[Fighter, list[str]]]]:
    """Все программы репозитория: ai/ (эталоны и личные) + pool всех агентов.

    Возвращает ``(бойцы, копии)``. Копии — группы файлов с одинаковым
    отпечатком: в турнире идёт один файл (предпочтительно из pool — это
    опубликованная версия), остальные перечислены в заметках отчёта.
    """
    out: list[Fighter] = []
    for p in sorted((ROOT / "ai").glob("*.tankp.py")):
        meta = load_program(p)
        if not meta.ok:
            print(f"пропуск {rel(p)}: битый файл ({'; '.join(meta.errors)})")
            continue
        stem = p.name[: -len(".tankp.py")]
        group = GROUP_DEFAULT if stem[:1].isdigit() else GROUP_AI
        out.append(Fighter(key=rel(p), path=p, label=stem,
                           name=meta.name or stem, author=meta.author,
                           group=group, difficulty=meta.difficulty))
    for agent_dir in sorted((ROOT / "pool").iterdir()):
        if not agent_dir.is_dir():
            continue
        for p in sorted(agent_dir.glob("*.tankp.py")):
            meta = load_program(p)
            if not meta.ok:
                print(f"пропуск {rel(p)}: битый файл ({'; '.join(meta.errors)})")
                continue
            stem = p.name[: -len(".tankp.py")]
            out.append(Fighter(key=rel(p), path=p, label=stem,
                               name=meta.name or stem,
                               author=meta.author or agent_dir.name,
                               group=GROUP_POOL, agent=agent_dir.name,
                               difficulty=meta.difficulty))
    # Один и тот же танк может лежать в ai/ и в pool/ (копия одного файла,
    # просто отформатированная иначе) — это один боец, а не два.
    by_fp: dict[str, list[Fighter]] = {}
    for f in out:
        by_fp.setdefault(fingerprint(f.path), []).append(f)
    kept: list[Fighter] = []
    copies: list[tuple[Fighter, list[str]]] = []
    for group in by_fp.values():
        group.sort(key=lambda f: (0 if f.group == GROUP_POOL else 1, f.key))
        kept.append(group[0])
        if len(group) > 1:
            copies.append((group[0], [f.key for f in group[1:]]))
    kept.sort(key=lambda f: f.key)
    out = kept
    # Метки матрицы должны быть уникальными: одинаковые имена в разных
    # группах (ai/ и pool) — разные файлы, но один боец.
    counts: dict[str, int] = {}
    for f in out:
        counts[f.label] = counts.get(f.label, 0) + 1
    for f in out:
        if counts[f.label] > 1:
            f.label = f"{f.label}({f.group})"
    return out, copies


def run_battle(fa, fb, arena, seed: int, bal, max_seconds: int):
    """Один бой в этом процессе. Программы — свежезагруженные точки входа.

    Если программа падает на тике, тик засчитывается пустым — так же, как в
    песочнице сервера. Время решения каждого танка считается здесь же: в
    песочнице его меряет runner, в прогоне в процессе — таймер вокруг вызова.
    Возвращает ``(world, stats)``: stats[0] — танк A боя, stats[1] — танк B.
    """
    world = World.create(arena, bal, seed=seed, max_seconds=max_seconds)
    mv = tankp.MapView(map_payload(world))
    fns = (fa, fb)
    budget = bal.default_budget_ms
    obs_cls, sdk_action = tankp.Observation, tankp.Action
    build, clamp, step = build_both, Action.clamp, world.step
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
        step(acts)
    stats = [{
        "shots": t.shots, "hits": t.hits,
        "ric": t.ricochets, "ric_taken": t.ricochets_taken,
        "rams": t.rams, "dealt": t.damage_dealt, "taken": t.damage_taken,
        "hp_left": t.hp, "think_total": think_total[i],
        "think_calls": think_calls[i], "think_max": think_max[i],
        "over_budget": over[i], "errors": errors[i],
    } for i, t in enumerate(world.tanks)]
    return world, stats


def h2h_cell(pos: dict[str, int], h2h: dict[tuple[str, str], list[int]],
             key_r: str, key_c: str) -> tuple[int, int, int]:
    """Счёт key_r против key_c: (победы, ничьи, поражения) с точки зрения key_r.

    ``pos`` — порядок бойцов из ``discover()``: в нём же закладывались ключи
    ``h2h``, поэтому он обязан быть по всему списку, даже если матрица печатает
    только часть строк.
    """
    a, b = (key_r, key_c) if pos[key_r] < pos[key_c] else (key_c, key_r)
    w, d, l = h2h.get((a, b), [0, 0, 0])
    if key_r != a:
        w, l = l, w
    return w, d, l


def render_matrix(fighters: list[Fighter],
                  h2h: dict[tuple[str, str], list[int]],
                  only: set[str] | None = None) -> str:
    """Матрица «каждый с каждым»: строка против столбца, победы-ничьи-поражения.

    ``only`` — ключи строк и столбцов (подмножество бойцов); порядок внутри —
    как в ``fighters``.
    """
    keys = [f.key for f in fighters if only is None or f.key in only]
    labels = {f.key: f.label for f in fighters}
    pos = {f.key: i for i, f in enumerate(fighters)}
    width = max([len(labels[k]) for k in keys] + [7])
    lines = [" " * width + "".join(f"{labels[k]:>{width + 2}}" for k in keys)]
    for r in keys:
        line = f"{labels[r]:<{width}}"
        for c in keys:
            if r == c:
                cell = "—"
            else:
                w, d, l = h2h_cell(pos, h2h, r, c)
                cell = f"{w}-{d}-{l}"
            line += f"{cell:>{width + 2}}"
        lines.append(line)
    return "\n".join(lines)


def render_map_matrix(ranked: list[Fighter],
                      mrec: dict[tuple[str, str], list[int]],
                      maps: list[str]) -> str:
    """Счёт на каждой карте: строка — танк, столбец — карта, победы-ничьи-поражения."""
    labels = {f.key: f.label for f in ranked}
    width = max([len(v) for v in labels.values()] + [7])
    lines = [" " * width + "".join(f"{m:>{width + 2}}" for m in maps)]
    for f in ranked:
        line = f"{f.label:<{width}}"
        for m in maps:
            w, d, l = mrec.get((m, f.key), [0, 0, 0])
            line += f"{f'{w}-{d}-{l}':>{width + 2}}"
        lines.append(line)
    return "\n".join(lines)


def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} с"
    return f"{int(seconds // 60)} мин {int(seconds % 60):02d} с"


def build_report(fighters: list[Fighter], rec: dict[str, Record],
                 h2h: dict[tuple[str, str], list[int]], *,
                 maps: list[str], seeds: list[int], max_seconds: int,
                 done: int, planned: int, elapsed: float,
                 copies: list[tuple[Fighter, list[str]]],
                 mrec: dict[tuple[str, str], list[int]]) -> str:
    ranked = sorted(fighters,
                    key=lambda f: (-rec[f.key].points, -rec[f.key].pct, f.name))
    counts = {g: sum(1 for f in fighters if f.group == g)
              for g in (GROUP_DEFAULT, GROUP_AI, GROUP_POOL)}
    participants = (f"{counts[GROUP_DEFAULT]} {GROUP_RU[GROUP_DEFAULT]}, "
                    f"{counts[GROUP_AI]} {GROUP_RU[GROUP_AI]}, "
                    f"{counts[GROUP_POOL]} {GROUP_RU[GROUP_POOL]}")

    lines = [
        "# Лидербоард TANKSIM",
        "",
        "Круговой турнир всех программ репозитория: эталоны из `ai/`, личные",
        "танки из `ai/` и всех агентов из `pool/`. Каждая пара играет в обе",
        "стороны (у спавнов разное положение), на каждой карте и с каждом сиде;",
        "программа загружается заново к каждому бою — как в песочнице сервера,",
        "где на бой поднимается свежий процесс.",
        "",
        f"_Сгенерировано {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC "
        "командой `python tools/leaderboard.py`._",
        "",
        "## Условия",
        "",
        "| Параметр | Значение |",
        "|---|---|",
        f"| Участников | {len(fighters)} — {participants} |",
        f"| Боёв | {done} из {planned} "
        f"({len(list(itertools.combinations(fighters, 2)))} пара × 2 стороны "
        f"× {len(maps)} карт × {len(seeds)} сида) |",
        f"| Карты | {', '.join(f'`{m}`' for m in maps)} |",
        f"| Сиды | {', '.join(str(s) for s in seeds)} |",
        f"| Лимит боя | {max_seconds} с, HP {BALANCE.hp:g}, урон "
        f"{BALANCE.base_damage:g}, КД {BALANCE.reload:g} с, тик "
        f"{BALANCE.tick_rate} Гц |",
        f"| Бюджет решения | {BALANCE.default_budget_ms:g} мс |",
        f"| Прогон | {fmt_duration(elapsed)} |",
        "",
        "Очки = победы + ½ ничьих, % = очки / бои. Ср. нанесено и получено —",
        "средний урон за бой. Ниже: разбор сильнейших друг против друга,"
        "восстания слабых, подробная статистика, матрица «каждый с каждым» и",
        "счёт по картам.",
        "",
        "## Таблица",
        "",
        "| # | Танк | Автор | Группа | И | В | Н | П | Очки | % | "
        "Ср. нанесено | Ср. получено |",
        "|--:|---|--|:--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for i, f in enumerate(ranked, 1):
        r = rec[f.key]
        lines.append(
            f"| {i} | {f.name} | {f.author or '—'} | {f.group} | {r.games} "
            f"| {r.wins} | {r.draws} | {r.losses} | {r.points:g} | "
            f"{r.pct:.1f} | {r.dealt / max(1, r.games):.1f} "
            f"| {r.taken / max(1, r.games):.1f} |")
    # --- Топ против топа и восстания слабых --------------------------------
    pos = {f.key: i for i, f in enumerate(fighters)}
    top = ranked[:TOP_N]
    top_keys = {f.key for f in top}
    if len(top) >= 2:
        lines += [
            "",
            "## Топ против топа",
            "",
            f"Взаимный счёт {len(top)} сильнейших ({', '.join(f.name for f in top)}) — "
            "каждый с каждым, строка против столбца: победы-ничьи-поражения.",
            "",
            "```text",
            render_matrix(fighters, h2h, only=top_keys),
            "```",
            "",
        ]
        circle = []
        for f in top:
            pts = 0.0
            games = 0
            for g in top:
                if g.key == f.key:
                    continue
                w, d, l = h2h_cell(pos, h2h, f.key, g.key)
                pts += w + 0.5 * d
                games += w + d + l
            circle.append((pts, games, f))
        circle.sort(key=lambda t: (-t[0], -t[1], t[2].name))
        parts = ", ".join(f"{f.name} — {pts:g}/{games}"
                          for pts, games, f in circle)
        lines.append(f"Очки в круге сильнейших (из общего числа боёв): {parts}.")

    upsets = []
    for i, f in enumerate(ranked):
        wins_up = 0
        wins_top = 0
        scalp = None
        for g in ranked[:i]:          # от сильного к слабому
            w, d, l = h2h_cell(pos, h2h, f.key, g.key)
            if w:
                wins_up += w
                if g.key in top_keys:
                    wins_top += w
                if scalp is None:
                    scalp = (ranked.index(g) + 1, g, w, d, l)
        upsets.append((f, i + 1, wins_up, wins_top, scalp))
    if any(u[2] for u in upsets):
        lines += [
            "",
            "## Когда слабые обыгрывают сильных",
            "",
            "Восстание — победа над танком, который выше в таблице. «Крупнейший "
            f"скальп» — сильнейший побеждённый танк и счёт против него. "
            f"«Над топ-{TOP_N}» — сколько таких побед взято у первых "
            f"{TOP_N} мест.",
            "",
            f"| Место | Танк | Побед над выше | Над топ-{TOP_N} | Крупнейший скальп |",
            "|--:|---|--:|--:|---|",
        ]
        shown = sorted((u for u in upsets if u[2]),
                       key=lambda u: (-u[2], u[4][0], u[1]))
        for f, place, wins_up, wins_top, scalp in shown:
            cell = (f"**{scalp[1].name}** ({scalp[0]}-е место, "
                    f"{scalp[2]}-{scalp[3]}-{scalp[4]})" if scalp else "—")
            lines.append(f"| {place} | {f.name} | {wins_up} | {wins_top} | {cell} |")
        clean = [f.name for f, _, wu, _, _ in upsets if not wu]
        if clean:
            lines.append("")
            lines.append(f"Ни одной победы над вышестоящим: {', '.join(clean)}.")
    lines += [
        "",
        "## Подробная статистика",
        "",
        "Средние значения за бой. Точность — попадания / выстрелы; рикошеты —",
        "свои снаряды, отскочившие от чужой брони / чужие снаряды, отскочившие",
        "от моей; «вне бюджета» — решений дольше 10 мс за весь турнир.",
        "",
        "| # | Танк | Точность | Выстр./бой | Рикошеты свои/на мне | "
        "Таранов/бой | Ср. HP в конце | Ср. длина боя | Пик think, мс | "
        "Ср. think, мс | Вне бюджета |",
        "|--:|---|--:|--:|:--:|--:|--:|--:|--:|--:|--:|",
    ]
    for i, f in enumerate(ranked, 1):
        r = rec[f.key]
        g = max(1, r.games)
        acc = r.hits / r.shots * 100.0 if r.shots else 0.0
        avg_think = r.think_total / r.think_calls if r.think_calls else 0.0
        lines.append(
            f"| {i} | {f.name} | {acc:.1f}% | {r.shots / g:.1f} "
            f"| {r.ric / g:.1f} / {r.ric_taken / g:.1f} | {r.rams / g:.1f} "
            f"| {r.hp_left / g:.1f} | {r.ticks / g / 60:.1f} с "
            f"| {r.think_max:.2f} | {avg_think:.2f} | {r.over_budget} |")
    lines += [
        "",
        "## Каждый с каждым",
        "",
        "<details><summary>матрица: строка против столбца, "
        "победы-ничьи-поражения</summary>",
        "",
        "```text",
        render_matrix(fighters, h2h),
        "```",
        "",
        "</details>",
        "",
        "## По картам",
        "",
        "Счёт строки на карте (победы-ничьи-поражения), порядок строк — как в",
        "таблице мест.",
        "",
        "```text",
        render_map_matrix(ranked, mrec, maps),
        "```",
        "",
        "## Заметки",
        "",
    ]

    # Копии одного танка в разных файлах — в турнире шёл один файл.
    for kept, dropped in copies:
        dup = ", ".join(f"`{d}`" for d in dropped)
        lines.append(f"- **{kept.name}**: {dup} — та же программа, что "
                     f"`{kept.key}`, просто лежит вторым файлом. В турнире шла "
                     "версия из pool, дубля в таблице нет.")
    # Лучший в каждой группе.
    for group in (GROUP_DEFAULT, GROUP_POOL):
        fs = [f for f in ranked if f.group == group]
        if fs:
            f, r = fs[0], rec[fs[0].key]
            word = "Лучший эталон" if group == GROUP_DEFAULT else "Лучший агент pool"
            place = ranked.index(f) + 1
            lines.append(f"- {word}: **{f.name}** ({f.author}) — "
                         f"{place}-е место, {r.pct:.1f}%.")
    if any(rec[f.key].errors for f in fighters):
        bad = ", ".join(f"{f.name} ({rec[f.key].errors})"
                        for f in ranked if rec[f.key].errors)
        lines.append(f"- Тики с падением программы (тик зачтён пустым): {bad}.")
    if done < planned:
        lines.append(f"- ⚠️ Прогон прерван по `--deadline`: учтено {done} из "
                     f"{planned} боёв — таблица неполная.")
    lines += [
        "- Обновить таблицу: `python tools/leaderboard.py` — полный прогон "
        "~7 мин, файл перезапишется. Новые агенты в `pool/` и новые танки в "
        "`ai/` попадают в турнир сами.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="leaderboard",
        description="Круговой турнир всех танков репозитория → Markdown-лидербоард")
    ap.add_argument("--maps", nargs="+", default=DEFAULT_MAPS,
                    help="карты турнира (по умолчанию %(default)s)")
    ap.add_argument("--seeds", nargs="+", type=int, default=DEFAULT_SEEDS,
                    help="сиды турнира (по умолчанию %(default)s)")
    ap.add_argument("--seconds", type=float, default=float(BALANCE.max_seconds),
                    help="лимит одного боя, с (по умолчанию %(default)s)")
    ap.add_argument("--out", default=str(ROOT / "LEADERBOARD.md"),
                    help="куда писать отчёт (по умолчанию %(default)s)")
    ap.add_argument("--deadline", type=float, default=None,
                    help="столько секунд wall-clock потратить на бои")
    args = ap.parse_args(argv)

    fighters, copies = discover()
    if len(fighters) < 2:
        print("участников меньше двух — нечего ранжировать")
        return 1
    max_seconds = int(args.seconds)
    bal = replace(BALANCE, max_seconds=max_seconds)

    arenas = {m: load_map(m) for m in args.maps}
    pairs = list(itertools.combinations(fighters, 2))
    # Пары идут вперемешку со сменой сторон: при --deadline успевают пройти
    # все пары на равных условиях, а не только первые.
    matches = [(a, b, m, s, home)
               for a, b in pairs for m in args.maps for s in args.seeds
               for home in (0, 1)]
    total = len(matches)

    rec: dict[str, Record] = {f.key: Record() for f in fighters}
    h2h: dict[tuple[str, str], list[int]] = {}
    mrec: dict[tuple[str, str], list[int]] = {}
    t0 = time.perf_counter()
    done = 0
    for a, b, m, s, home in matches:
        if args.deadline is not None and time.perf_counter() - t0 >= args.deadline:
            break
        x, y = (a, b) if home == 0 else (b, a)
        world, stats = run_battle(load_inproc(str(x.path)),
                                  load_inproc(str(y.path)),
                                  arenas[m], s, bal, max_seconds)
        done += 1
        rx, ry = rec[x.key], rec[y.key]
        rx.games += 1
        ry.games += 1
        for idx, f in enumerate((x, y)):
            r, st = rec[f.key], stats[idx]
            r.dealt += st["dealt"]
            r.taken += st["taken"]
            r.errors += st["errors"]
            r.shots += st["shots"]
            r.hits += st["hits"]
            r.ric += st["ric"]
            r.ric_taken += st["ric_taken"]
            r.rams += st["rams"]
            r.hp_left += st["hp_left"]
            r.ticks += world.tick
            r.think_total += st["think_total"]
            r.think_calls += st["think_calls"]
            r.think_max = max(r.think_max, st["think_max"])
            r.over_budget += st["over_budget"]
        # Счёт по картам ведут оба бойца боя: mc — для текущей стороны x,
        # my — для стороны y. Иначе в отчёт попадёт ровно половина боёв.
        mc = mrec.setdefault((m, x.key), [0, 0, 0])
        my = mrec.setdefault((m, y.key), [0, 0, 0])
        cell = h2h.setdefault((a.key, b.key), [0, 0, 0])
        if world.outcome == "draw":
            rx.draws += 1
            ry.draws += 1
            cell[1] += 1
            mc[1] += 1
            my[1] += 1
        else:
            # cell считает победы a (первого в комбинации), x и y — текущие
            # стороны боя: при home == 1 они поменялись местами.
            a_won = world.outcome == ("a_win" if home == 0 else "b_win")
            cell[0 if a_won else 2] += 1
            if (home == 0) == a_won:
                rx.wins += 1
                ry.losses += 1
                mc[0] += 1
                my[2] += 1
            else:
                rx.losses += 1
                ry.wins += 1
                mc[2] += 1
                my[0] += 1
        if done % 200 == 0 or done == total:
            el = time.perf_counter() - t0
            eta = el / done * (total - done)
            print(f"{done}/{total} боёв ({done / total:.0%}), {el:.0f} с, "
                  f"осталось ~{eta:.0f} с", flush=True)

    elapsed = time.perf_counter() - t0
    report = build_report(fighters, rec, h2h, maps=args.maps,
                          seeds=args.seeds, max_seconds=max_seconds,
                          done=done, planned=total, elapsed=elapsed,
                          copies=copies, mrec=mrec)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    ranked = sorted(fighters, key=lambda f: -rec[f.key].points)
    print(f"\nбоёв: {done} из {total}, прогон {fmt_duration(elapsed)}")
    for f in ranked:
        r = rec[f.key]
        print(f"  {f.label:<16} {r.wins:>3}-{r.draws:>3}-{r.losses:>3} "
              f"{r.points:g} очк ({r.pct:.1f}%)")
    print(f"ГОТОВО: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
