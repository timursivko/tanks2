"""Формат TANKP v1: заголовок в комментариях, проверка кода, загрузка.

Файл программы начинается строкой-магией ``#!TANKP 1``, затем идут пары
``# ключ: значение`` с метаданными (движок показывает их в интерфейсе),
дальше — обычный Python::

    #!TANKP 1
    # name: Погоня
    # difficulty: 2
    # color: #ff7043

    from tankp import TankProgram

    class Brain(TankProgram):
        def on_tick(self, o):
            return Action(drive=0.8, fire=o.enemy_visible)

    program = Brain()

Тот же формат принимается в zip: ``main.py`` в корне архива — точка входа,
рядом лежат остальные модули и данные (см. ``engine.bundle``).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = "#!TANKP"
VERSION = 1
#: Имя точки входа пакета — zip с этим файлом в корне принимается как программа
PACKAGE_ENTRY = "main.py"

META_KEYS = {
    "name": "Отображаемое имя",
    "author": "Автор",
    "difficulty": "Сложность (1-5)",
    "color": "Цвет танка (#rrggbb)",
    "description": "Краткое описание стратегии",
    "tags": "Теги через запятую",
}

_HEADER_RE = re.compile(r"^\s*#\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.+?)\s*$")


def fighter_name(filename: str) -> str:
    """Имя бойца из имени файла: всё, кроме расширения.

    Именно так называется танк в интерфейсе: загрузили ``МойТанк.tankp.py`` —
    боец «МойТанк». Хвост ``.tankp`` отбрасывается вместе с ``.py``, иначе в имени
    остаётся технический мусор. Для пакета то же самое делается с именем zip
    или каталога: имя боя одно и то же, как бы он ни был загружен. Если после
    обрезки ничего не осталось, отдаём заглушку.
    """
    stem = Path(filename or "").name
    # Хвосты отбрасываются по очереди: у «01_chaser.tankp.py» их сразу два.
    for suffix in (".py", ".tankp", ".zip"):
        while stem.lower().endswith(suffix):
            stem = stem[:-len(suffix)]
    return stem or "program"


@dataclass
class ProgramMeta:
    """Разобранный заголовок + результат проверки кода."""

    name: str = "Безымянный"
    author: str = ""
    difficulty: int = 1
    color: str = "#8aa0b5"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    version: int = VERSION
    source: str = ""
    path: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    entry: str = ""
    #: Корень пакета (каталог с main.py и файлами рядом). Пусто для одиночного
    #: скрипта. По нему воркер добавляет каталог в sys.path, поэтому соседние
    #: модули и веса доступны обычным import/open.
    package: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "name": self.name, "author": self.author, "difficulty": self.difficulty,
            "color": self.color, "description": self.description, "tags": self.tags,
            "version": self.version, "path": self.path, "ok": self.ok,
            "warnings": self.warnings, "errors": self.errors, "entry": self.entry,
            "package": self.package,
        }


def parse_source(source: str, path: str = "") -> ProgramMeta:
    """Разбирает заголовок и проверяет синтаксис, ничего не выполняя."""
    meta = ProgramMeta(source=source, path=path)
    lines = source.splitlines()
    seen_magic = False
    header_done = False

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if lineno == 1:
            if not stripped.startswith(MAGIC):
                meta.errors.append(
                    f"строка 1: файл должен начинаться с '{MAGIC} {VERSION}'")
            else:
                seen_magic = True
                try:
                    meta.version = int(stripped[len(MAGIC):].strip() or VERSION)
                    if meta.version > VERSION:
                        meta.warnings.append(
                            f"версия формата {meta.version} новее поддерживаемой {VERSION}")
                except ValueError:
                    meta.errors.append(f"строка 1: не удалось разобрать версию формата")
            continue
        if not header_done:
            if stripped.startswith("#!"):
                meta.errors.append(f"строка {lineno}: магия должна быть только на первой строке")
                continue
            if stripped.startswith("#"):
                m = _HEADER_RE.match(line)
                if m:
                    key, value = m.group(1).lower(), m.group(2)
                    if key in META_KEYS:
                        _apply_meta(meta, key, value)
                    elif key not in ("type", "style"):
                        meta.warnings.append(
                            f"строка {lineno}: неизвестный ключ заголовка «{key}»")
                continue
            header_done = True

    if not seen_magic:
        return meta

    try:
        tree = ast.parse(source, filename=path or "program.tankp.py")
    except SyntaxError as exc:
        meta.errors.append(f"строка {exc.lineno}: синтаксическая ошибка: {exc.msg}")
        return meta

    top = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assigned = {t.id for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}

    has_program = "program" in assigned
    has_on_tick_fn = "on_tick" in top
    has_class = any(
        isinstance(n, ast.ClassDef)
        and "on_tick" in {f.name for f in n.body if isinstance(f, ast.FunctionDef)}
        for n in tree.body)

    if has_program:
        meta.entry = "program"
    elif has_on_tick_fn:
        meta.entry = "on_tick"
    elif has_class:
        meta.entry = "program"
        meta.warnings.append("переменная 'program' не найдена — будет взят последний класс")
    else:
        meta.errors.append("нет точки входа: определите 'program' или функцию 'on_tick(o)'")
        meta.entry = ""

    return meta


def _apply_meta(meta: ProgramMeta, key: str, value: str) -> None:
    if key == "name":
        meta.name = value[:64]
    elif key == "author":
        meta.author = value[:64]
    elif key == "color":
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            meta.color = value.lower()
        else:
            meta.warnings.append(f"цвет «{value}» не в формате #rrggbb — игнорирован")
    elif key == "description":
        meta.description = value[:400]
    elif key == "tags":
        meta.tags = [t.strip() for t in value.split(",") if t.strip()][:8]
    elif key == "difficulty":
        try:
            meta.difficulty = max(1, min(5, int(value)))
        except ValueError:
            meta.warnings.append(f"сложность «{value}» не число — игнорирована")


def load_program(path: str | Path) -> ProgramMeta:
    """Читает файл программы и возвращает метаданные с проверкой синтаксиса."""
    p = Path(path)
    try:
        source = p.read_text(encoding="utf-8")
    except OSError as exc:
        m = ProgramMeta(path=str(p))
        m.errors.append(f"не удалось прочитать файл: {exc}")
        return m
    except UnicodeDecodeError as exc:
        m = ProgramMeta(path=str(p))
        m.errors.append(f"файл не в UTF-8: {exc}")
        return m
    return parse_source(source, str(p))


def load_package(root: str | Path) -> ProgramMeta:
    """Пакет: каталог с ``main.py`` и файлами рядом.

    Метаданные берём из точки входа — заголовок ``#!TANKP`` пишется именно
    в ``main.py``. Ошибки чтения и синтаксиса возвращаются теми же списком
    ``errors``, что и у одиночного скрипта, чтобы вызывающий код не различал
    два случая.
    """
    r = Path(root)
    meta = load_program(r / PACKAGE_ENTRY)
    meta.package = str(r)
    return meta


def is_package_dir(path: Path) -> bool:
    """Похож ли каталог на пакет программы."""
    return path.is_dir() and (path / PACKAGE_ENTRY).is_file()


def load_any_program(path: str | Path) -> ProgramMeta:
    """Читает программу по пути: одиночный скрипт или пакет с ``main.py`` рядом.

    Одна точка входа для всех, кто запускает программу, — иначе легко забыть
    про пакет в одном из путей и получить ``import`` соседнего модуля в бою.
    """
    p = Path(path)
    return load_package(p.parent) if is_package_dir(p.parent) else load_program(p)


def scan_programs(dirs: list[Path], limit: int = 200) -> list[ProgramMeta]:
    """Сканирует каталоги с программами для выпадающего списка в UI."""
    out: list[ProgramMeta] = []
    seen: set[str] = set()
    for d in dirs:
        if not d or not d.exists():
            continue
        for p in sorted(d.glob("*.tankp.py")):
            key = p.resolve().as_posix()
            if key in seen or len(out) >= limit:
                continue
            seen.add(key)
            out.append(load_program(p))
        # Пакеты лежат подкаталогами: в каждом ищем точку входа main.py.
        for sub in sorted(d.iterdir()) if d.is_dir() else []:
            if len(out) >= limit or not is_package_dir(sub):
                continue
            meta = load_package(sub)
            key = Path(meta.path).resolve().as_posix()
            if key in seen:
                continue
            seen.add(key)
            out.append(meta)
    return out


TEMPLATE = '''#!TANKP 1
# name: Мой танк
# author: вы
# difficulty: 2
# color: #7ed957
# description: Опишите замысел в одну строку — её увидят в списке танков.
# tags: пример

from tankp import TankProgram, Action


class Brain(TankProgram):
    def on_start(self, ctx):
        self.found = False

    def on_tick(self, o):
        if o.enemy:
            # Ошибка наведения по башне, градусы: положительное — вправо.
            err = o.me.turret_error(o.enemy)
            return Action(drive=0.6, turn=o.sign(err), turret=o.sign(err),
                          fire=abs(err) < 8)
        # Врага не видно — идём к точке спавна противника и ждём.
        turn, drive = o.steer_to(*o.map.spawns[1 - o.tank])
        return Action(drive=drive, turn=turn)


program = Brain()
'''


def write_template(target: Path) -> Path:
    """Создаёт заготовку программы, если её ещё нет."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(TEMPLATE, encoding="utf-8")
    return target