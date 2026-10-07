"""Сессии и каталог программ на стороне сервера.

Пути к программам клиент не присылает «как есть»: он выбирает запись из
каталога или загружает файл. Так движок случайно не выполнит то, чего
пользователь не показывал.
"""

from __future__ import annotations

import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from battle.runner import BattleRun
from config import AI_DIR, BALANCE, ROOT, SCRIPTS_DIR
from engine.program import ProgramMeta, is_package_dir, load_package, scan_programs
from engine.replay import load_replay, prune_replays


def safe_program_path(key: str) -> Path | None:
    """Превращает ключ каталога в путь, гарантируя, что он внутри проекта."""
    if not key:
        return None
    p = Path(key)
    if p.is_absolute():
        return p if _inside(p) else None
    p = (ROOT / p).resolve()
    return p if _inside(p) else None


def _inside(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        return False
    return path.suffix == ".py"


class Catalog:
    """Список программ: скрипты проекта и загруженные пользователем файлы."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._programs: dict[str, ProgramMeta] = {}
        self.reload()

    def reload(self) -> None:
        # EXAMPLES_DIR намеренно не сканируется: примеры — документация,
        # в бою и в выпадающем списке им не место. Файлы остаются на диске.
        # SCRIPTS_DIR сканируется: это загруженные пользователем бои, их
        # показываем.
        found = scan_programs([AI_DIR, SCRIPTS_DIR])
        with self._lock:
            self._programs = {self.key_of(m): m for m in found}

    @staticmethod
    def key_of(meta: ProgramMeta) -> str:
        try:
            return Path(meta.path).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return meta.path

    def all(self) -> list[ProgramMeta]:
        with self._lock:
            return sorted(self._programs.values(),
                          key=lambda m: (0 if "ai/" in m.path.replace("\\", "/")
                                         else 1, m.name.lower()))

    def get(self, key: str) -> ProgramMeta | None:
        with self._lock:
            return self._programs.get(key)

    def register(self, path: Path, source: str) -> ProgramMeta:
        """Сохраняет загруженный файл и добавляет его в каталог."""
        from engine.program import parse_source

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        meta = parse_source(source, str(path))
        with self._lock:
            self._programs[self.key_of(meta)] = meta
        return meta

    def register_package(self, root: Path) -> ProgramMeta:
        """Добавляет в каталог уже распакованный пакет.

        В каталоге пакет — это одна запись с ключом от ``main.py``, поэтому
        выбирается и скачивается он так же, как обычный скрипт, а каталог с
        весами едет вместе с ним.
        """
        meta = load_package(root)
        with self._lock:
            self._programs[self.key_of(meta)] = meta
        return meta

    def drop_upload(self, key: str) -> None:
        path = safe_program_path(key)
        if not path or SCRIPTS_DIR not in path.parents:
            return
        with self._lock:
            self._programs.pop(key, None)
        # Пакет удаляем целиком: один main.py оставил бы сиротские веса и
        # модули, а следующая загрузка того же имени всё равно затрёт каталог.
        root = path.parent if is_package_dir(path.parent) else path
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)
        else:
            try:
                root.unlink()
            except OSError:
                pass


@dataclass
class Sessions:
    """Прогоны боёв и реплеи в памяти сервера."""

    runs: dict[str, BattleRun] = field(default_factory=dict)
    catalog: Catalog = field(default_factory=Catalog)
    balance: object = field(default_factory=lambda: BALANCE)

    def new_run(self, cfg) -> BattleRun:
        from battle.runner import STATUS_QUEUED  # noqa: F401 — для ясности
        run = BattleRun(cfg, f"b{int(time.time() * 1000) % 10_000_000}", self.balance)
        self.runs[run.id] = run
        self._gc_runs()
        return run

    def _gc_runs(self, keep: int = 12) -> None:
        if len(self.runs) <= keep:
            return
        order = sorted(self.runs.values(), key=lambda r: r.finished_at or time.time(),
                       reverse=True)
        for old in order[keep:]:
            self.runs.pop(old.id, None)

    def get_replay(self, replay_id: str):
        # id реплея не совпадает с id боя, поэтому ищем по обоим.
        run = self.runs.get(replay_id)
        if run is not None and run.replay is not None:
            return run.replay
        for r in self.runs.values():
            if r.replay is not None and r.replay.id == replay_id:
                return r.replay
        try:
            return load_replay(replay_id)
        except (FileNotFoundError, ValueError):
            return None

    def cleanup(self) -> None:
        try:
            prune_replays(keep=20)
        except OSError:
            pass


sessions = Sessions()