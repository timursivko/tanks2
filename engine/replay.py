"""Реплей: компактная упаковка кадров и хранение на диске.

Числовые данные складываются в колонки (по массиву на величину), поэтому
60-секундный бой — это около мегабайта текста и в разы меньше после gzip.
Браузер получает те же структуры и рисует бой сам, без связи с Python.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from config import REPLAYS_DIR
from engine.events import Outcome

TANK_KEYS = ("x", "y", "h", "u", "hp", "cd", "vis", "ms", "bad", "alive", "spd")
#: как часто сохранять точку траектории снаряда (в тиках)
BULLET_STRIDE = 2
#: Порядок полей события после времени и вида. Строка всегда одной длины, даже
#: если у события половины полей нет: клиент распарсит её по индексу. Раньше
#: лишние ключи просто пропускались, колонки съезжали, и в журнале попадание
#: показывало урон вместо номера танка.
EVENT_KEYS = ("tank", "target", "shooter", "damage", "face", "theta",
              "ricochet", "bounce", "outcome", "reason", "x", "y", "angle")


@dataclass
class Replay:
    """Полная запись боя."""

    meta: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    map: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    t: list[float] = field(default_factory=list)
    a: dict = field(default_factory=dict)
    b: dict = field(default_factory=dict)
    bullets: list = field(default_factory=list)
    events: list = field(default_factory=list)
    shots: dict = field(default_factory=dict)
    id: str = ""
    created: float = 0.0

    def __post_init__(self) -> None:
        if not self.a:
            self.a = self.new_tank_columns()
        if not self.b:
            self.b = self.new_tank_columns()
        if not isinstance(self.shots, dict):
            self.shots = {int(k): v for k, v in dict(self.shots).items()}

    # --- сборка -------------------------------------------------------------

    def new_tank_columns(self) -> dict:
        return {k: [] for k in TANK_KEYS}

    def push_frame(self, snap: dict) -> None:
        self.t.append(snap["t"])
        # снаряды в полёте: храним только кадры, где они есть
        if snap.get("sh"):
            self.shots[len(self.t) - 1] = snap["sh"]
        for key, tank in (("a", snap["a"]), ("b", snap["b"])):
            col = getattr(self, key)
            for k in TANK_KEYS:
                col[k].append(tank[k])

    def push_event(self, ev: dict) -> None:
        if ev.get("kind") == "log":
            self.events.append([round(ev["t"], 4), ev["tank"], ev.get("text", "")])
            return
        self.events.append([round(ev["t"], 4), ev["kind"]]
                           + [ev.get(key) for key in EVENT_KEYS])

    def finish_bullet(self, track: list, owner: int, speed: float) -> None:
        """Сохраняет траекторию снаряда: [x, y, тик] через BULLET_STRIDE тиков.

        Тик в каждой точке нужен фронтенду, чтобы обрезать хвост по текущему
        кадру и погасить его черезTRAIL_TTL после попадания.
        """
        if len(track) < 2:
            return
        pts = [[round(p[0], 1), round(p[1], 1), int(p[2])]
               for p in track[::BULLET_STRIDE]]
        last = track[-1]
        tail = [round(last[0], 1), round(last[1], 1), int(last[2])]
        if pts[-1] != tail:
            pts.append(tail)
        self.bullets.append({"o": owner, "s": speed, "pts": pts})

    # --- свойства -----------------------------------------------------------

    @property
    def frames(self) -> int:
        return len(self.t)

    @property
    def duration(self) -> float:
        return self.t[-1] if self.t else 0.0

    def to_payload(self) -> dict:
        return {
            "v": 1,
            "id": self.id,
            "created": self.created,
            "meta": self.meta,
            "summary": self.summary,
            "config": self.config,
            "map": self.map,
            "t": self.t,
            "a": self.a,
            "b": self.b,
            "shots": {str(k): v for k, v in self.shots.items()},
            "bullets": self.bullets,
            "events": self.events,
        }

    def to_json(self, pretty: bool = False) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False,
                          separators=(",", ":") if not pretty else None,
                          indent=2 if pretty else None)

    @staticmethod
    def from_payload(data: dict) -> "Replay":
        r = Replay(
            meta=data.get("meta", {}), summary=data.get("summary", {}),
            map=data.get("map", {}), config=data.get("config", {}),
            t=data.get("t", []), a=data.get("a", {}), b=data.get("b", {}),
            bullets=data.get("bullets", []), events=data.get("events", []),
            id=data.get("id", ""), created=data.get("created", 0.0),
            shots={int(k): v for k, v in data.get("shots", {}).items()},
        )
        return r

    @staticmethod
    def new_id() -> str:
        return f"{int(time.time())}_{uuid.uuid4().hex[:6]}"


def save_replay(replay: Replay, directory: Path | None = None) -> Path:
    """Пишет реплей в gzip-JSON. Каталог создаётся при необходимости.

    Пишем во временный файл и переименовываем: иначе читатель, открывший
    реплей в момент записи, получал обрезанный gzip и падал с
    ``JSONDecodeError`` вместо нормального ответа.
    """
    directory = directory or REPLAYS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    if not replay.id:
        replay.id = Replay.new_id()
    path = directory / f"{replay.id}.json.gz"
    tmp = directory / f".{replay.id}.{uuid.uuid4().hex[:6]}.tmp"
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(replay.to_json())
        _atomic_replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def _atomic_replace(tmp: Path, path: Path, attempts: int = 40) -> None:
    """Переименовывает временный файл на место.

    На Windows ``os.replace`` падает с PermissionError, если читатель держит
    открытым прошлый файл: сжатый реплей открывают через gzip, и при частом
    чтении дескриптор живёт заметно дольше одного кадра. Повторяем с растущей
    паузой — файл либо переименуется, либо читатель получит целую предыдущую
    версию вместо обрезанной.
    """
    for i in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(min(0.05, 0.002 * (i + 1)))


def replay_path(replay_id: str, directory: Path | None = None) -> Path:
    """Путь к файлу реплея: id можно дать и с расширением, и без него."""
    directory = directory or REPLAYS_DIR
    name = replay_id
    for suffix in (".json.gz", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return directory / f"{name}.json.gz"


def load_replay(replay_id: str, directory: Path | None = None,
                attempts: int = 8) -> Replay:
    """Читает реплей.

    На Windows файл может быть на миг занят переименованием, поэтому короткое
    ожидание вместо ``PermissionError`` в ответе API.
    """
    path = replay_path(replay_id, directory)
    for i in range(attempts):
        if not path.exists():
            raise FileNotFoundError(f"реплей {replay_id} не найден")
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return Replay.from_payload(json.load(f))
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.01 * (i + 1))
    raise FileNotFoundError(f"реплей {replay_id} не найден")


def list_replays(directory: Path | None = None, limit: int = 20) -> list[dict]:
    """Последние сохранённые бои для списка в UI."""
    directory = directory or REPLAYS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    files = sorted(directory.glob("*.json.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for p in files:
        rid = p.name[: -len(".json.gz")] if p.name.endswith(".json.gz") else p.stem
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                head = json.load(f)
        except Exception as exc:  # noqa: BLE001
            out.append({"id": rid, "created": p.stat().st_mtime,
                        "broken": True, "error": str(exc)[:200]})
            continue
        out.append({
            "id": head.get("id", rid),
            "created": head.get("created", p.stat().st_mtime),
            "summary": head.get("summary", {}),
            "meta": head.get("meta", {}),
        })
    return out


def prune_replays(keep: int = 20, directory: Path | None = None) -> int:
    """Оставляет только последние ``keep`` боёв."""
    directory = directory or REPLAYS_DIR
    files = sorted(directory.glob("*.json.gz"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for p in files[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def outcome_label(outcome: str) -> str:
    return Outcome.LABELS.get(outcome, outcome)