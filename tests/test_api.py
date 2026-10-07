"""Тесты HTTP/WebSocket API через in-process TestClient.

Сервер не запускается отдельным процессом: FastAPI проверяется целиком в памяти.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from server.app import MANUAL_SESSIONS, app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def keys(client: TestClient) -> list[str]:
    programs = client.get("/api/catalog").json()["programs"]
    ai = sorted(p["key"] for p in programs if p.get("ok") and p["key"].startswith("ai/"))
    assert len(ai) >= 2, "в каталоге должно быть минимум две рабочие программы"
    return ai


def test_examples_are_hidden_from_catalog(client: TestClient) -> None:
    """Примеры не должны попадать в список ИИ: они документация, а не бойцы."""
    programs = client.get("/api/catalog").json()["programs"]
    bad = [p["key"] for p in programs if "examples" in p["key"].replace("\\", "/")]
    assert not bad, f"примеры видны в UI: {bad}"
    ai = [p["key"] for p in programs if p["key"].startswith("ai/")]
    assert len(ai) >= 7, f"в каталоге должно быть 7 ИИ, а не {len(ai)}"


def test_uploaded_scripts_stay_in_catalog(client: TestClient) -> None:
    """Чистка каталога не должна выкидывать загруженные пользователем бои."""
    from config import SCRIPTS_DIR
    from server.sessions import Catalog

    cat = Catalog()
    key = "saves/scripts/_test_upload_probe.tankp.py"
    meta = cat.register(SCRIPTS_DIR / "_test_upload_probe.tankp.py",
                        "#!TANKP 1\n# name: Проба\n# author: t\n"
                        "# difficulty: 1\n# color: #fff\n# description: d\n"
                        "# tags: t\n\nfrom tankp import TankProgram, Action\n\n\n"
                        "class B(TankProgram):\n"
                        "    def on_tick(self, o):\n"
                        "        return Action()\n\n\nprogram = B()\n")
    try:
        assert cat.get(cat.key_of(meta)) is not None, "загрузка пропала из каталога"
        assert key.replace("\\", "/") in [k.replace("\\", "/") for k in cat._programs]
    finally:
        cat.drop_upload(cat.key_of(meta))


# --- справочные -------------------------------------------------------------


def test_health_and_index(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["ok"] is True

    page = client.get("/")
    assert page.status_code == 200
    assert "<canvas" in page.text


def test_static_files_served(client: TestClient) -> None:
    for name in ("style.css", "app.js", "render.js"):
        resp = client.get(f"/static/{name}")
        assert resp.status_code == 200, name
        assert resp.content, name


def test_catalog_maps_and_balance(client: TestClient) -> None:
    data = client.get("/api/catalog").json()
    programs = data["programs"]
    assert len(programs) >= 7
    assert all("key" in p and "ok" in p and "name" in p for p in programs)
    assert len(data["maps"]) >= 5

    maps = client.get("/api/maps").json()
    assert {m["id"] for m in maps} >= {"arena", "corridor", "meadow"}
    assert all(m["name"] and m["width"] > 0 and m["height"] > 0 for m in maps)

    balance = client.get("/api/balance").json()
    assert balance["balance"]["max_seconds"] > 0
    assert balance["balance"]["view_range"] > 0
    assert balance["balance"]["view_cone"] > 0
    assert balance["armor_table"], "нужна таблица пробития брони"


def test_program_source_returns_code(client: TestClient, keys: list[str]) -> None:
    resp = client.get("/api/programs/source", params={"key": keys[0]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"].startswith("#!TANKP 1")
    assert body["meta"]["name"]


def test_unknown_key_is_rejected(client: TestClient) -> None:
    resp = client.get("/api/programs/source", params={"key": "нет/такой.tankp.py"})
    assert resp.status_code == 404


def test_rejects_self_battle(client: TestClient, keys: list[str]) -> None:
    resp = client.post("/api/simulate", json={
        "a": {"kind": "script", "key": keys[0]},
        "b": {"kind": "script", "key": keys[0]},
        "map_name": "arena"})
    assert resp.status_code == 400
    assert "сам" in resp.json()["detail"].lower()


def test_upload_rejects_non_tankp(client: TestClient) -> None:
    resp = client.post("/api/upload",
                       files={"file": ("bad.tankp.py", io.BytesIO(b"print(1)\n"),
                                       "text/x-python")})
    assert resp.status_code == 400


def test_upload_accepts_tankp(client: TestClient) -> None:
    from pathlib import Path

    from server.sessions import sessions

    src = ("#!TANKP 1\n"
           "from tankp import TankProgram, Action\n\n\n"
           "class Brain(TankProgram):\n"
           "    def on_tick(self, o):\n"
           "        return Action()\n\n\n"
           "program = Brain()\n")
    resp = client.post("/api/upload",
                       files={"file": ("Загрузка тест.tankp.py",
                                       io.BytesIO(src.encode("utf-8")),
                                       "text/x-python")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    key = body["key"]
    try:
        assert body["ok"] is True, body["errors"]
        assert key.endswith(".tankp.py")
        # Имя боца — это имя скрипта без расширения, и кириллица в нём обязана
        # выжить: раньше файл переименовывался в 1234567__________ _____.tankp.py,
        # и в бою вместо «Загрузка тест» показывалось «program».
        assert body["fighter"] == "Загрузка тест", body
        assert Path(key).name == "Загрузка тест.tankp.py", key
        # загруженная программа сразу доступна в каталоге
        catalog = client.get("/api/catalog").json()["programs"]
        entry = next(p for p in catalog if p["key"] == key)
        assert entry["fighter"] == "Загрузка тест"
    finally:
        sessions.catalog.drop_upload(key)          # тест не оставляет мусор
    assert not any(p["key"] == key
                   for p in client.get("/api/catalog").json()["programs"])


def test_upload_same_name_twice_overwrites(client: TestClient) -> None:
    """Второй файл с тем же именем затирает первый, чтобы не плодить копии."""
    from server.sessions import sessions

    src = ("#!TANKP 1\n"
           "from tankp import TankProgram, Action\n\n\n"
           "class Brain(TankProgram):\n"
           "    def on_tick(self, o):\n"
           "        return Action()\n\n\n"
           "program = Brain()\n")
    keys: list[str] = []
    try:
        for _ in range(2):
            resp = client.post(
                "/api/upload",
                files={"file": ("Двойной.tankp.py", io.BytesIO(src.encode("utf-8")),
                                "text/x-python")})
            assert resp.status_code == 200, resp.text
            keys.append(resp.json()["key"])
        assert keys[0] == keys[1], f"файлы с одинаковым именем должны затираться: {keys}"
        assert keys[0].endswith("Двойной.tankp.py")
    finally:
        for key in keys:
            sessions.catalog.drop_upload(key)


# --- загрузка пакета в zip --------------------------------------------------


def _zip_bytes(entries: dict[str, str]) -> bytes:
    """Собирает zip из словаря ``имя → содержимое`` прямо в памяти."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buf.getvalue()


PACKAGE_MAIN = """#!TANKP 1
# name: Пакетный боец
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


def test_upload_accepts_zip_package(client: TestClient) -> None:
    """Пакет принимается как одна программа: файлы раскладываются рядом с main.py."""
    from pathlib import Path

    from config import SCRIPTS_DIR
    from server.sessions import sessions

    raw = _zip_bytes({"main.py": PACKAGE_MAIN,
                      "brain.py": "DRIVE = 0.75\n",
                      "weights.json": '{"k": 0.5}'})
    resp = client.post("/api/upload",
                       files={"file": ("Пакет боец.zip", io.BytesIO(raw), "application/zip")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    key = body["key"]
    try:
        assert body["ok"] is True, body["errors"]
        assert body["name"] == "Пакетный боец", body
        # Имя архива — это имя боя, а каталог называется так же.
        assert body["fighter"] == "Пакет боец", body
        assert Path(key).name == "main.py", key
        assert body["package"].endswith("Пакет боец"), body["package"]
        assert body["files"] == 3, body
        root = SCRIPTS_DIR / "Пакет боец"
        assert (root / "brain.py").is_file() and (root / "weights.json").is_file()
        # Точка входа в каталоге одна запись: выбирается и удаляется как скрипт.
        entry = next(p for p in client.get("/api/catalog").json()["programs"]
                     if p["key"] == key)
        assert entry["fighter"] == "Пакет боец"
    finally:
        sessions.catalog.drop_upload(key)
    assert not (SCRIPTS_DIR / "Пакет боец").exists(), "каталог пакета не убран"


def test_zip_package_runs_in_battle(client: TestClient, keys: list[str]) -> None:
    """Главное: соседние модули и веса пакета реально видны программе в бою.

    Если корень пакета не попал бы в sys.path воркера, бой упал бы на импорте.
    """
    import time

    from server.sessions import sessions

    raw = _zip_bytes({"main.py": PACKAGE_MAIN,
                      "brain.py": "DRIVE = 0.75\n",
                      "weights.json": '{"k": 0.5}'})
    resp = client.post("/api/upload",
                       files={"file": ("Боевой пакет.zip", io.BytesIO(raw), "application/zip")})
    assert resp.status_code == 200, resp.text
    key = resp.json()["key"]
    try:
        start = client.post("/api/simulate", json={
            "a": {"kind": "script", "key": key},
            "b": {"kind": "script", "key": keys[1]},
            "map_name": "arena", "budget_ms": 5, "seed": 9, "max_seconds": 5})
        assert start.status_code == 200, start.text
        body: dict = {}
        for _ in range(60):
            body = client.get(f"/api/simulate/{start.json()['run_id']}").json()
            if body.get("status") in ("done", "error"):
                break
            time.sleep(0.2)
        assert body["status"] == "done", body
    finally:
        sessions.catalog.drop_upload(key)


def test_upload_rejects_zip_without_main(client: TestClient) -> None:
    resp = client.post("/api/upload", files={
        "file": ("Без точки входа.zip",
                 io.BytesIO(_zip_bytes({"brain.py": "x = 1\n"})),
                 "application/zip")})
    assert resp.status_code == 400
    assert "main.py" in resp.json()["detail"]


def test_upload_rejects_zip_slip(client: TestClient) -> None:
    """Попытка выйти из каталога пакета отклоняется целиком."""
    from config import ROOT, SCRIPTS_DIR
    from server.sessions import sessions

    raw = _zip_bytes({"main.py": PACKAGE_MAIN, "../tanki_escape.py": "x = 1\n"})
    resp = client.post("/api/upload",
                       files={"file": ("Побег.zip", io.BytesIO(raw), "application/zip")})
    assert resp.status_code == 400
    assert "за пределы пакета" in resp.json()["detail"]
    assert not (SCRIPTS_DIR / "Побег").exists()
    assert not (ROOT / "tanki_escape.py").exists()
    assert not list(ROOT.glob("*_escape.py"))
    # Каталог остаётся рабочим: отказ не сломал загрузку целиком.
    assert sessions.catalog.get("saves/scripts/Побег/main.py") is None


def test_upload_rejects_non_tankp_zip(client: TestClient) -> None:
    """Архив без заголовка TANKP не попадает в каталог и не оставляет мусора."""
    from config import SCRIPTS_DIR
    from server.sessions import sessions

    raw = _zip_bytes({"main.py": "print('привет')\n", "notes.txt": "просто текст\n"})
    resp = client.post("/api/upload",
                       files={"file": ("Не программа.zip", io.BytesIO(raw), "application/zip")})
    assert resp.status_code == 400
    assert "TANKP" in resp.json()["detail"]
    assert not (SCRIPTS_DIR / "Не программа").exists()
    assert sessions.catalog.get("saves/scripts/Не программа/main.py") is None


def test_zip_package_reupload_cleans_old_files(client: TestClient) -> None:
    """Повторный пакет с тем же именем заменяет каталог целиком."""
    from config import SCRIPTS_DIR
    from server.sessions import sessions

    keys: list[str] = []
    try:
        for extra in ("old.txt", "new.txt"):
            raw = _zip_bytes({"main.py": PACKAGE_MAIN,
                              "brain.py": "DRIVE = 0.75\n",
                              extra: "просто текст\n"})
            resp = client.post(
                "/api/upload",
                files={"file": ("Сменный пакет.zip", io.BytesIO(raw), "application/zip")})
            assert resp.status_code == 200, resp.text
            keys.append(resp.json()["key"])
        assert keys[0] == keys[1], f"ключ пакета должен быть один: {keys}"
        root = SCRIPTS_DIR / "Сменный пакет"
        assert (root / "new.txt").is_file(), "файлы новой версии на месте"
        assert not (root / "old.txt").exists(), "файл прошлой версии остался"
    finally:
        for key in keys:
            sessions.catalog.drop_upload(key)


def test_zip_over_limit_is_rejected(client: TestClient) -> None:
    """Бомба не распаковывается: размер известен из заголовка архива."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr("main.py", PACKAGE_MAIN)
        zf.writestr("weights.bin", b"\0" * (40 * 1024 * 1024))
    resp = client.post("/api/upload",
                       files={"file": ("Бомба.zip", io.BytesIO(buf.getvalue()),
                                       "application/zip")})
    assert resp.status_code == 400
    assert "МБ" in resp.json()["detail"]


# --- расчёт боя ------------------------------------------------------------


def test_simulate_produces_replay(client: TestClient, keys: list[str]) -> None:
    resp = client.post("/api/simulate", json={
        "a": {"kind": "script", "key": keys[0]},
        "b": {"kind": "script", "key": keys[1]},
        "map_name": "arena", "seed": 5, "max_seconds": 5})
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    body = {}
    for _ in range(60):                       # расчёт короткий, ждём до 12 с
        body = client.get(f"/api/simulate/{run_id}").json()
        if body.get("status") in ("done", "error"):
            break
        import time
        time.sleep(0.2)
    assert body["status"] == "done", body

    replay_id = body["replay_id"]
    assert replay_id
    # Статус «готово» обязан означать, что реплей уже лежит на диске: раньше
    # статус выставлялся до записи, и клиент получал 404 «реплей не найден».
    rep = client.get(f"/api/replay/{replay_id}")
    assert rep.status_code == 200, f"реплей недоступен сразу после done: {rep.text[:200]}"
    replay = rep.json()
    assert "bullets" in replay, f"в реплее нет траекторий: {sorted(replay)}"
    assert replay["t"], "реплей должен содержать кадры"
    assert replay["a"] and replay["b"], "в реплее должны быть траектории танков"
    assert replay["summary"]["outcome"] in ("a_win", "b_win", "draw")
    assert replay["map"]["rows"], "реплей должен хранить карту"

    listing = client.get("/api/replays").json()
    entry = next((r for r in listing if r["id"] == replay_id), None)
    assert entry is not None, "новый реплей должен появиться в списке"
    assert entry["summary"]["outcome"] in ("a_win", "b_win", "draw")
    assert not entry.get("broken")


# --- ручной бой ------------------------------------------------------------


def test_manual_session_streams_frames(client: TestClient, keys: list[str]) -> None:
    start = client.post("/api/manual/start", json={
        "player_side": "a", "key": keys[0], "map_name": "arena",
        "player_name": "Тест", "seed": 3})
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]

    with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["map"]["rows"]
        assert len(hello["spawns"]) == 2

        got_frames = 0
        tick = 0
        while got_frames < 24:
            ws.send_text(json.dumps({
                "keys": ["KeyW"], "aim": [800.0, 300.0], "fire": True}))
            msg = ws.receive_json()
            if msg["type"] == "frames":
                got_frames += len(msg["frames"])
            tick += 1
            assert tick < 200, "сервер перестал слать кадры"

    stop = client.post(f"/api/manual/stop/{sid}")
    assert stop.status_code == 200
    body = stop.json()
    assert body["stopped"] is True
    assert body["error"] == ""
    assert body["result"]["manual_side"] == "a"
    assert body["result"]["duration"] > 0

    replay_id = body["replay_id"]
    assert replay_id, "после ручного боя должен сохраниться реплей"
    replay = client.get(f"/api/replay/{replay_id}").json()
    assert replay["summary"]["manual_side"] == "a"
    assert replay["meta"]["a"]["name"] == "Тест"
    assert replay["meta"]["manual_side"] == "a"

    MANUAL_SESSIONS.pop(sid, None)

