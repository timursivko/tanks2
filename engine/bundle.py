"""Архив программы: zip с ``main.py`` и файлами рядом.

Загружать можно не только одиночный ``.py``, но и zip: точка входа — ``main.py``
в корне архива, рядом лежат веса, данные и остальные модули ИИ. Пакет
разворачивается в отдельный каталог и попадает в каталог программ как одна
запись, поэтому в бою он занимает столько же места, сколько обычный скрипт.

Распаковка — это запись чужих данных на диск, поэтому проверок здесь много, и
каждая отвечает на свой вопрос:

* ``..`` и абсолютные пути не могут выйти за пределы каталога пакета;
* служебные каталоги и бинарники не распаковываются вовсе;
* имена из архива не могут занять имена движка и SDK — иначе пакет подменил бы
  ``tankp`` или ``config`` и повлиял бы на другие бои того же сервера;
* суммарный размер, число файлов и коэффициент сжатия ограничены, иначе
  zip-бомба съест диск или память;
* каталог пакета очищается перед записью, а при любой ошибке удаляется целиком.

Что остаётся за рамками модуля: программа и дальше выполняется в отдельном
процессе песочницы с бюджетом времени, но у неё остаётся доступ к файловой
системе пользователя — это ограничение Windows-песочницы, а не наша недоработка.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

#: Имя точки входа в корне архива
ENTRY_NAME = "main.py"
#: Ограничения на архив. Против «бомбы» важнее всего суммарный размер: он
#: известен из заголовка zip, до распаковки.
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024      # сам архив
MAX_UNPACKED_BYTES = 64 * 1024 * 1024     # сколько он распухнет
MAX_FILES = 512
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION = 200.0                   # во сколько раз может сжаться
#: Служебные каталоги Python внутри пакета: мусор, который только засоряет
#: каталог и мешает отличать исходники от скомпилированного.
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".mypy_cache",
             ".pytest_cache", ".ipynb_checkpoints"}
#: Бинарники в пакете боту не нужны, а на Windows значимый кусок вредоносного
#: архива приезжает именно исполняемым файлом.
SKIP_SUFFIX = {".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bat", ".cmd",
               ".com", ".msi", ".scr", ".lnk", ".jar"}
#: Имена верхнего уровня, которые пакет не имеет права занимать: иначе он
#: подменит модули движка и песочницы. Имя проверяется без учёта расширения.
RESERVED_TOP = {"tankp", "engine", "config", "server", "battle", "tools",
                "tests", "run", "sdk", "sitecustomize", "usercustomize",
                "site", "encodings", "importlib", "json", "os", "sys",
                "runpy", "threading", "subprocess", "socket", "http",
                "urllib", "requests", "numpy", "scipy", "torch"}


class BundleError(Exception):
    """Архив нельзя принять: сообщаем причину, огрызок удаляет вызывающий."""


@dataclass
class BundleInfo:
    """Что получилось после распаковки."""

    root: Path = Path()
    entry: Path = Path()
    written: int = 0
    unpacked: int = 0
    skipped: list[str] = field(default_factory=list)


def _looks_absolute(name: str) -> bool:
    """Похоже ли имя на абсолютный путь или на путь с чужим диском."""
    return (name.startswith("/") or name.startswith("//")
            or bool(PureWindowsPath(name).is_absolute())
            or bool(PureWindowsPath(name).drive))


def _parts(raw: str) -> list[str]:
    """Имя записи в виде списка сегментов: обратные слэши, без ``./`` и ``/``."""
    name = (raw or "").replace("\\", "/").strip()
    while name.startswith("./"):
        name = name[2:]
    return [p for p in PurePosixPath(name).parts if p not in ("", ".")]


def _reserved(parts: list[str]) -> str:
    """Имя верхнего уровня, которое пакет занимать не может ("" — можно)."""
    if not parts:
        return ""
    top = parts[0]
    stem = top[:-3] if top.lower().endswith(".py") else top
    return top if stem.lower() in RESERVED_TOP else ""


def skip_reason(parts: list[str]) -> str:
    """Пусто, если файл распаковывать, иначе причина пропуска."""
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return "служебный каталог"
    if Path(parts[-1]).suffix.lower() in SKIP_SUFFIX:
        return "двоичный файл"
    return ""


def inspect(raw: bytes) -> BundleInfo:
    """Проверяет архив и считает, во что он распухнет. Ничего не пишет."""
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise BundleError(f"архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise BundleError(f"это не zip-архив: {exc}") from exc
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise BundleError("в архиве нет файлов")
        if len(infos) > MAX_FILES:
            raise BundleError(f"в архиве {len(infos)} файлов, а можно до {MAX_FILES}")
        total = 0
        for i in infos:
            name = i.filename
            parts = _parts(name)
            if not parts:
                continue
            if any(p == ".." for p in parts) or _looks_absolute(name):
                raise BundleError(f"файл «{name}» уходит за пределы пакета")
            # Сначала мусор: его мы просто не кладём, и архив из-за него не ломаем.
            # А вот имя движка — уже настоящая коллизия, и такой архив не берём.
            if skip_reason(parts):
                continue
            if _reserved(parts):
                raise BundleError(
                    f"файл «{name}» называется как модуль движка — переименуйте его")
            if i.file_size > MAX_FILE_BYTES:
                raise BundleError(
                    f"файл «{name}» больше {MAX_FILE_BYTES // (1024 * 1024)} МБ")
            if i.compress_size > 0 and i.file_size / i.compress_size > MAX_COMPRESSION:
                raise BundleError(
                    f"файл «{name}» сжат в {i.file_size / i.compress_size:.0f} раз — "
                    "похоже на бомбу")
            total += i.file_size
        if total > MAX_UNPACKED_BYTES:
            raise BundleError(
                f"пос распаковки архив занимает {total // (1024 * 1024)} МБ, "
                f"а можно до {MAX_UNPACKED_BYTES // (1024 * 1024)} МБ")
        if not any(_parts(i.filename) == [ENTRY_NAME] for i in infos):
            raise BundleError(f"в архиве нет {ENTRY_NAME} в корне — это точка входа")
    return BundleInfo(unpacked=total)


def unpack(raw: bytes, target: Path) -> BundleInfo:
    """Распаковывает архив в ``target``. При любой ошибке каталог удаляется."""
    info = inspect(raw)
    root = Path(target)
    _clear(root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            for i in zf.infolist():
                if i.is_dir():
                    continue
                parts = _parts(i.filename)
                if not parts:
                    continue
                reason = skip_reason(parts) or ("зарезервированное имя" if _reserved(parts) else "")
                if reason:
                    info.skipped.append(f"{i.filename} ({reason})")
                    continue
                dst = (root / Path(*parts)).resolve()
                if not _within(dst, root):
                    info.skipped.append(f"{i.filename} (путь наружу)")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(i) as src, dst.open("wb") as out:
                    shutil.copyfileobj(src, out, length=256 * 1024)
                info.written += 1
                # unpacked не трогаем: это полный размер из заголовка архива,
                # он нужен для отчёта независимо от того, что мы пропустили.
    except Exception as exc:  # noqa: BLE001 — любая ошибка распаковки: убираем огрызок
        shutil.rmtree(root, ignore_errors=True)
        raise BundleError(f"не удалось распаковать: {exc}") from exc
    entry = root / ENTRY_NAME
    if not entry.is_file():
        shutil.rmtree(root, ignore_errors=True)
        raise BundleError(f"пос распаковки нет {ENTRY_NAME}")
    info.root = root
    info.entry = entry
    return info


def _within(path: Path, root: Path) -> bool:
    """Лежит ли путь внутри каталога — последняя проверка от zip slip."""
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _clear(path: Path) -> None:
    """Убирает с места старый пакет: каталог или файл с тем же именем."""
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except OSError:
        pass