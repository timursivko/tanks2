"""Проверки приёма zip с программой: безопасность распаковки и запуск в бою.

Здесь важна не только польза (пакет действительно едет в бой), но и вред:
архив приходит из интернета, поэтому каждый отказ проверяется отдельно.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from engine.bundle import BundleError, inspect, unpack

MAIN = """#!TANKP 1
# name: Пакетный
# difficulty: 2

import json
from pathlib import Path

from brain import DRIVE
from tankp import Action, TankProgram

HERE = Path(__file__).resolve().parent


class Brain(TankProgram):
    def on_start(self, ctx):
        self.k = json.loads((HERE / "weights.json").read_text(encoding="utf-8"))["k"]

    def on_tick(self, o):
        return Action(drive=DRIVE * self.k)


program = Brain()
"""

BRAIN = "DRIVE = 0.75\n"
WEIGHTS = '{"k": 0.5}'


def make_zip(entries: dict[str, str | bytes]) -> bytes:
    """Собирает zip из словаря ``имя → содержимое``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buf.getvalue()


def good_zip() -> bytes:
    return make_zip({"main.py": MAIN, "brain.py": BRAIN, "weights.json": WEIGHTS})


def bomb_zip(unpacked: int = 40 * 1024 * 1024) -> bytes:
    """Настоящий zip-бомба: в заголовке 40 МБ нулей, на диске — килобайт."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("main.py", MAIN)
        zf.writestr("weights.bin", b"\0" * unpacked)
    return buf.getvalue()


# --- проверки без записи на диск ---------------------------------------------


def test_inspect_accepts_package_with_siblings() -> None:
    info = inspect(good_zip())
    assert info.unpacked == len(MAIN.encode()) + len(BRAIN.encode()) + len(WEIGHTS)


def test_inspect_requires_main_at_root() -> None:
    """Точка входа — именно main.py в корне, а не где попало внутри."""
    with pytest.raises(BundleError, match="main.py"):
        inspect(make_zip({"src/main.py": MAIN, "brain.py": BRAIN}))


def test_inspect_rejects_garbage() -> None:
    with pytest.raises(BundleError, match="не zip"):
        inspect(b"not a zip at all")


def test_inspect_rejects_empty_archive() -> None:
    with pytest.raises(BundleError, match="нет файлов"):
        inspect(make_zip({}))


@pytest.mark.parametrize("bad", ["../escape.py", "a/../../escape.py",
                                 "/abs/escape.py", "//host/share/escape.py",
                                 "C:/Windows/system32/x.py", "..\\escape.py"])
def test_inspect_rejects_paths_outside_package(bad: str) -> None:
    """Zip slip: ни ``..``, ни абсолютный путь не должны выйти из пакета."""
    with pytest.raises(BundleError, match="за пределы пакета"):
        inspect(make_zip({"main.py": MAIN, bad: "print(1)"}))


@pytest.mark.parametrize("name", ["engine/brain.py", "tankp.py", "config.py",
                                  "sitecustomize.py", "numpy/pad.py"])
def test_inspect_rejects_reserved_names(name: str) -> None:
    """Пакет не должен занимать имена движка: иначе он подменил бы модуль."""
    with pytest.raises(BundleError, match="движка"):
        inspect(make_zip({"main.py": MAIN, name: "print(1)"}))


def test_inspect_rejects_zip_bomb() -> None:
    """Суммарный размер известен из заголовка — диск не трогаем вообще."""
    with pytest.raises(BundleError, match="МБ"):
        inspect(bomb_zip())


def test_inspect_rejects_too_many_files() -> None:
    entries = {f"part{i}.txt": "x" for i in range(600)}
    entries["main.py"] = MAIN
    with pytest.raises(BundleError, match="файлов"):
        inspect(make_zip(entries))


# --- распаковка на диск ------------------------------------------------------


def test_unpack_writes_files_and_skips_junk(tmp_path) -> None:
    raw = make_zip({"main.py": MAIN, "brain.py": BRAIN, "weights.json": WEIGHTS,
                    "__pycache__/main.cpython-312.pyc": b"\0",
                    "tools/run.exe": b"MZ"})
    info = unpack(raw, tmp_path / "bot")
    assert info.written == 3
    assert (info.root / "brain.py").read_text(encoding="utf-8") == BRAIN
    assert (info.root / "weights.json").read_text(encoding="utf-8") == WEIGHTS
    # Служебный мусор не распаковывается, но попадает в отчёт — иначе пользователь
    # не поймёт, куда делся его файл.
    assert not (info.root / "tools").exists()
    assert not (info.root / "__pycache__").exists()
    assert any("main.cpython-312.pyc" in s for s in info.skipped), info.skipped
    assert any("run.exe" in s for s in info.skipped), info.skipped


def test_unpack_cleans_previous_version(tmp_path) -> None:
    """Повторная загрузка того же имени не оставляет старых файлов пакета."""
    target = tmp_path / "bot"
    unpack(make_zip({"main.py": MAIN, "brain.py": BRAIN, "weights.json": WEIGHTS}), target)
    info = unpack(make_zip({"main.py": MAIN, "brain.py": BRAIN}), target)
    assert not (target / "weights.json").exists(), "старые веса остались в пакете"
    assert info.written == 2


def test_unpack_leaves_nothing_on_bad_archive(tmp_path) -> None:
    """Ошибка не оставляет каталога: мусор в каталоге загрузок только мешает."""
    target = tmp_path / "bot"
    with pytest.raises(BundleError):
        unpack(make_zip({"brain.py": BRAIN}), target)
    assert not target.exists()


def test_unpack_replaces_directory_file_collision(tmp_path) -> None:
    """Если раньше на этом месте лежал файл, а теперь пакет — каталог нужен."""
    target = tmp_path / "bot"
    target.write_text("бывший скрипт", encoding="utf-8")
    info = unpack(good_zip(), target)
    assert info.entry.is_file()
    assert json.loads((info.root / "weights.json").read_text(encoding="utf-8"))["k"] == 0.5