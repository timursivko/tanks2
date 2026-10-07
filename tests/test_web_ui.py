"""Согласованность веб-интерфейса: разметка, скрипты и элементы.

Проверяет, что каждый ``id``, который ищет ``app.js``, есть в ``index.html``,
и что страница подключает все нужные файлы. Это ловит самые частые поломки
интерфейса без запуска браузера.
"""

from __future__ import annotations

import math
import re
import time

from fastapi.testclient import TestClient
import pytest

from engine.geometry import wrap_angle
from server.app import app


@pytest.fixture(scope="module")


def client() -> TestClient:
    with TestClient(app) as c:
        yield c

ID_IN_JS = re.compile(r"""\$\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""")
ID_IN_HTML = re.compile(r"""id\s*=\s*["']([A-Za-z0-9_-]+)["']""")
QUERY_IN_JS = re.compile(r"""querySelector(?:All)?\(\s*['"]#([A-Za-z0-9_-]+)""")


def _page(client: TestClient) -> str:
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.text


def test_all_ids_exist(client: TestClient) -> None:
    html = _page(client)
    app_js = client.get("/static/app.js").text
    render_js = client.get("/static/render.js").text
    known = set(ID_IN_HTML.findall(html))
    assert known, "в разметке нет ни одного id"

    wanted: set[str] = set()
    for source in (app_js, render_js):
        wanted |= set(ID_IN_JS.findall(source))
        wanted |= set(QUERY_IN_JS.findall(source))

    missing = sorted(wanted - known)
    assert not missing, f"в разметке нет элементов: {missing}"


def test_scripts_are_connected(client: TestClient) -> None:
    html = _page(client)
    for name in ("app.js", "render.js", "style.css"):
        assert name in html, f"index.html не подключает {name}"
    assert "<canvas" in html, "нужен canvas для боя"
    # Обычные скрипты: render.js обязан грузиться раньше app.js
    assert html.index("render.js") < html.index("app.js")


def test_tabs_have_panels(client: TestClient) -> None:
    html = _page(client)
    for tab in ("sim", "manual", "replays", "help"):
        assert f'data-tab="{tab}"' in html, f"нет вкладки {tab}"
        assert f'id="page-{tab}"' in html, f"нет панели {tab}"


def test_fighters_are_dropped_not_chosen_from_lists(client: TestClient) -> None:
    """Ни в бою, ни в ручном бою больше нет выпадающих списков с ИИ.

    Вместо них — квадратные поля-перетаскивания: два в бою и одно в ручном.
    """
    html = _page(client)
    assert html.count("Перетащите сюда Python скрипт ИИ бойца") == 3, \
        "нужно три зоны перетаскивания: две в бою и одна в ручном бою"
    for gone in ('id="sel-a"', 'id="sel-b"', 'id="man-foe"', 'id="upload"'):
        assert gone not in html, f"старый выбор из списка всё ещё на странице: {gone}"
    for zone, picker in (("drop-a", "file-a"), ("drop-b", "file-b"), ("drop-foe", "file-foe")):
        assert f'id="{zone}"' in html, f"нет поля перетаскивания {zone}"
        assert f'id="{picker}"' in html, f"нет выбора файла {picker}"
    css = client.get("/static/style.css").text
    assert "aspect-ratio: 1 / 1" in css, "поле бойца должно быть квадратным"


def test_fighter_name_comes_from_script_filename(client: TestClient) -> None:
    """Имя боца — имя скрипта без .py, а не заголовок и не метка времени."""
    from engine.program import fighter_name

    assert fighter_name("01_chaser.tankp.py") == "01_chaser"
    assert fighter_name("МойТанк.py") == "МойТанк"
    assert fighter_name("C:\\tmp\\x.tankp.py") == "x"
    assert fighter_name(".py") == "program"
    # Имя архива становится именем боя так же, как имя скрипта.
    assert fighter_name("МойТанк.zip") == "МойТанк"
    assert fighter_name("МойТанк") == "МойТанк"
    app_js = client.get("/static/app.js").text
    assert "meta.fighter || fighterName(file.name)" in app_js, \
        "имя бойца должно браться из имени файла скрипта"


def test_upload_accepts_zip_alongside_py(client: TestClient) -> None:
    """Все три поля загрузки берут и скрипт, и zip с программой."""
    html = _page(client)
    for picker in ("file-a", "file-b", "file-foe"):
        match = re.search(rf'id="{picker}"[^>]*accept="([^"]*)"', html)
        assert match, f'у #{picker} нет accept'
        accept = match.group(1)
        assert ".py" in accept and ".zip" in accept, \
            f"#{picker} должен принимать .py и .zip, а принимает {accept}"
    app_js = client.get("/static/app.js").text
    # Один общий фильтр на .py/.zip: иначе перетащенный zip молча игнорируется.
    assert "function isProgramFile" in app_js
    assert re.search(r"function isProgramFile[\s\S]{0,120}py\|zip", app_js), \
        "фильтр программ должен пропускать и .py, и .zip"
    assert app_js.count("isProgramFile(") >= 2, \
        "фильтр должен применяться и при выборе файла, и при перетаскивании"


def test_both_slots_required_to_fight(client: TestClient) -> None:
    """Бой запускается только когда заполнены оба поля."""
    app_js = client.get("/static/app.js").text
    assert "if (!a || !b)" in app_js, "нужна проверка, что оба поля заполнены"
    assert "a.key === b.key" in app_js, "один и тот же боец против себя запрещён"
    assert "$('sel-a').value" not in app_js, "app.js всё ещё читает выпадающий список"
    assert "$('man-foe').value" not in app_js, "ручной бой всё ещё читает список"


def test_manual_input_blur_skips_drop_zone(client: TestClient) -> None:
    """Перед стартом ручного боя фокус не должен оставаться в зоне загрузки."""
    app_js = client.get("/static/app.js").text
    assert "'#page-manual select, #page-manual input'" in app_js
    assert 'id="file-foe"' in _page(client), "у ручного боя должен быть свой выбор файла"


def test_manual_keyboard_drives_the_tank(client: TestClient) -> None:
    """Клавиши ручного боя действительно двигают танк.

    Проверяем весь путь целиком: WebSocket -> InputMsg -> ManualSession.
    Раньше управление выглядело сломанным по двум причинам сразу: спавн был
    зажат стеной (газ давал 3-4 px) и часть клавиш уходила в поле «Имя».
    """
    key = next(p["key"] for p in client.get("/api/catalog").json()["programs"]
               if p["ok"])
    start = client.post("/api/manual/start", json={
        "key": key, "player_side": "a", "map_name": "arena",
        "player_name": "test", "seed": 5, "budget_ms": 10,
    })
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]

    def latest(ws) -> dict:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "frames" and msg["frames"]:
                return msg["frames"][-1]["me"]

    with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
        ws.receive_json()                      # hello
        idle = None
        for _ in range(20):
            idle = latest(ws)
        aim = [idle["x"] + 300, idle["y"]]
        for _ in range(20):
            ws.send_json({"keys": ["w"], "aim": aim, "fire": False})
        moved = None
        for _ in range(30):
            moved = latest(ws)
        d = math.hypot(moved["x"] - idle["x"], moved["y"] - idle["y"])
        assert d > 25.0, f"газ по W сдвинул танк всего на {d:.0f} px"
    client.post(f"/api/manual/stop/{sid}")


def test_manual_hull_turn_keys_work(client: TestClient) -> None:
    """A/D должны поворачивать корпус, а не только башню.

    Раньше клавиши сравнивались с `e.key`, и при русской раскладке W/A/S/D
    не совпадали ни с одним элементом списка — газ и поворот не доходили
    до сервера вовсе, работала только мышь (башня).
    """
    key = next(p["key"] for p in client.get("/api/catalog").json()["programs"]
               if p["ok"])
    start = client.post("/api/manual/start", json={
        "key": key, "player_side": "a", "map_name": "arena",
        "player_name": "test", "seed": 5, "budget_ms": 10,
    })
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]

    def latest(ws) -> dict:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "frames" and msg["frames"]:
                return msg["frames"][-1]["me"]

    with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
        ws.receive_json()                      # hello
        idle = None
        for _ in range(20):
            idle = latest(ws)
        aim = [idle["x"], idle["y"] + 300]
        # Физические коды клавиш — то, что шлёт браузер.
        for _ in range(40):
            ws.send_json({"keys": ["KeyD"], "aim": aim, "fire": False})
        turned = None
        for _ in range(30):
            turned = latest(ws)
        delta = abs(wrap_angle(turned["h"] - idle["h"]))
        assert delta > 0.2, f"D не повернул корпус (поворот {delta:.3f} рад)"
        # Корпус крутится на месте: это поворот, а не газ. Башня при этом
        # законно доворачивается вслед за неподвижным курсором.
        moved = math.hypot(turned["x"] - idle["x"], turned["y"] - idle["y"])
        assert moved < 20.0, f"D повёл танк вперёд на {moved:.0f} px"
    client.post(f"/api/manual/stop/{sid}")


def test_manual_turn_direction_matches_ars(client: TestClient) -> None:
    """D — вправо (по часовой стрелке), A — влево.

    Экран у нас с осью y вниз, поэтому рост угла корпуса — это поворот вправо.
    """
    key = next(p["key"] for p in client.get("/api/catalog").json()["programs"]
               if p["ok"])
    start = client.post("/api/manual/start", json={
        "key": key, "player_side": "a", "map_name": "arena",
        "player_name": "test", "seed": 5, "budget_ms": 10,
    })
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]

    def latest(ws) -> dict:
        while True:
            msg = ws.receive_json()
            if msg["type"] == "frames" and msg["frames"]:
                return msg["frames"][-1]["me"]

    def hull_delta(press: str) -> float:
        with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
            ws.receive_json()                  # hello
            first = None
            for _ in range(15):
                first = latest(ws)
            aim = [first["x"] + 300, first["y"]]
            for _ in range(40):
                ws.send_json({"keys": [press], "aim": aim, "fire": False})
            last = None
            for _ in range(25):
                last = latest(ws)
            return wrap_angle(last["h"] - first["h"])

    assert hull_delta("KeyD") > 0.2, "D должен доворачивать корпус вправо"
    assert hull_delta("KeyA") < -0.2, "A должен доворачивать корпус влево"
    client.post(f"/api/manual/stop/{sid}")


def _manual_session(client: TestClient, **extra):
    key = next(p["key"] for p in client.get("/api/catalog").json()["programs"]
               if p["ok"])
    body = {"key": key, "player_side": "a", "map_name": "arena",
            "player_name": "test", "seed": 5, "budget_ms": 10}
    body.update(extra)
    start = client.post("/api/manual/start", json=body)
    assert start.status_code == 200, start.text
    return start.json()["session_id"]


def _latest_me(ws) -> dict:
    while True:
        msg = ws.receive_json()
        if msg["type"] == "frames" and msg["frames"]:
            return msg["frames"][-1]["me"]


def _hold_key(client: TestClient, sid: str, press: str, frames: int = 40):
    """Держит клавишу `frames` сообщений и возвращает снимки до/после."""
    with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
        ws.receive_json()                      # hello
        # Прогреваемся: иначе первый и последний кадры совпадают, и замер пустой.
        first = _latest_me(ws)
        for _ in range(14):
            first = _latest_me(ws)
        aim = [first["x"] + math.cos(first["h"]) * 300,
               first["y"] + math.sin(first["h"]) * 300]
        for _ in range(frames):
            ws.send_json({"keys": [press], "aim": aim, "fire": False})
        last = _latest_me(ws)
        for _ in range(24):
            last = _latest_me(ws)
    return first, last


def test_manual_drive_direction_forward_and_back(client: TestClient) -> None:
    """W — вперёд по курсу корпуса, S — назад.

    У каждого направления своя сессия: иначе танк въезжает на полной скорости
    с предыдущего замера и не успевает развернуться, и замер ничего не значит.
    """
    sid_f = _manual_session(client)
    first, last = _hold_key(client, sid_f, "KeyW")
    forward = ((last["x"] - first["x"]) * math.cos(first["h"])
               + (last["y"] - first["y"]) * math.sin(first["h"]))
    client.post(f"/api/manual/stop/{sid_f}")
    assert forward > 20.0, f"W должен везти вперёд, сдвиг {forward:.0f} px"

    sid_r = _manual_session(client)
    first, last = _hold_key(client, sid_r, "KeyS")
    backward = ((last["x"] - first["x"]) * math.cos(first["h"])
                + (last["y"] - first["y"]) * math.sin(first["h"]))
    client.post(f"/api/manual/stop/{sid_r}")
    assert backward < -10.0, f"S должен везти назад, сдвиг {backward:.0f} px"


def test_manual_reveal_always_sends_enemy(client: TestClient) -> None:
    """С галочкой «видеть противника» его позиция приходит в каждом кадре."""
    key = next(p["key"] for p in client.get("/api/catalog").json()["programs"]
               if p["ok"])
    start = client.post("/api/manual/start", json={
        "key": key, "player_side": "a", "map_name": "arena",
        "player_name": "test", "seed": 5, "budget_ms": 10, "reveal": True,
    })
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]

    frames = 0
    with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
        ws.receive_json()                      # hello
        while frames < 25:
            msg = ws.receive_json()
            if msg["type"] != "frames":
                continue
            for f in msg["frames"]:
                assert f["enemy_revealed"] is True
                assert f["enemy"] is not None, \
                    "с галочкой противник должен присылаться всегда"
                for field in ("x", "y", "h", "u", "hp", "alive"):
                    assert field in f["enemy"], f"в кадре противника нет {field}"
                frames += 1
    assert frames >= 25
    client.post(f"/api/manual/stop/{sid}")


def test_manual_reveal_can_be_toggled_mid_fight(client: TestClient) -> None:
    """Галочку можно переключить прямо в бою, без перезапуска."""
    key = next(p["key"] for p in client.get("/api/catalog").json()["programs"]
               if p["ok"])
    start = client.post("/api/manual/start", json={
        "key": key, "player_side": "a", "map_name": "arena",
        "player_name": "test", "seed": 5, "budget_ms": 10, "reveal": False,
    })
    assert start.status_code == 200, start.text
    sid = start.json()["session_id"]

    with client.websocket_connect(f"/api/manual/ws/{sid}") as ws:
        ws.receive_json()                      # hello
        seen_off = None
        for _ in range(25):
            ws.send_json({"keys": [], "aim": [0, 0], "fire": False, "reveal": False})
            msg = ws.receive_json()
            if msg["type"] == "frames" and msg["frames"]:
                seen_off = msg["frames"][-1]
        assert seen_off is not None and seen_off["enemy_revealed"] is False

        got_on = None
        for _ in range(30):
            ws.send_json({"keys": [], "aim": [0, 0], "fire": False, "reveal": True})
            msg = ws.receive_json()
            if msg["type"] == "frames" and msg["frames"]:
                f = msg["frames"][-1]
                if f["enemy_revealed"] and f["enemy"] is not None:
                    got_on = f
                    break
        assert got_on is not None, "галочка не включилась на лету"
    client.post(f"/api/manual/stop/{sid}")


def test_manual_visibility_checkboxes_are_wired(client: TestClient) -> None:
    """Обе галочки есть в разметке, читаются в app.js и доходят до render.js."""
    html = _page(client)
    for box in ("man-reveal", "man-foe-vision"):
        assert f'id="{box}"' in html, f"нет галочки {box}"
    app_js = client.get("/static/app.js").text
    for token in ("man-reveal", "man-foe-vision", "reveal:", "foeVision"):
        assert token in app_js, f"app.js не использует {token}"
    render_js = client.get("/static/render.js").text
    assert "opts.reveal" in render_js, "render.js игнорирует галочку видимости"
    assert "opts.foeVision" in render_js, "render.js не рисует обзор противника"


def test_manual_keys_accept_symbols_and_codes(client: TestClient) -> None:
    from server.manual import _pressed

    for variant in ("w", "W", "KeyW", "keyw", "ArrowUp"):
        assert "w" in _pressed([variant]), f"{variant!r} не распознан как газ"
    for variant in ("a", "KeyA", "ArrowLeft"):
        assert "a" in _pressed([variant]), f"{variant!r} не распознан как поворот"
    assert _pressed(["q", "Shift", "", None]) == set()
    assert _pressed(None) == set()


def test_manual_input_uses_physical_key_codes(client: TestClient) -> None:
    """Клавиши читаются по `code`, иначе не работает нелатинская раскладка."""
    app_js = client.get("/static/app.js").text
    assert "GAME_KEYS[e.code]" in app_js, \
        "клавиши должны определяться по KeyboardEvent.code, а не по e.key"
    assert "e.key.toLowerCase()) return" not in app_js, \
        "сравнение с e.key ломает газ на русской раскладке"


def test_many_maps_exist(client: TestClient) -> None:
    """Карт много, все читаются и симметричны на 180°."""
    from engine.grid import list_maps

    maps = list_maps()
    assert len(maps) >= 10, f"карт всего {len(maps)}, нужно хотя бы 10"
    broken = [m["id"] for m in maps if m.get("error")]
    assert not broken, f"битые карты: {broken}"
    asymmetric = [m["id"] for m in maps if not m.get("symmetric")]
    assert not asymmetric, f"карты без симметрии на 180°: {asymmetric}"


def test_replay_viewer_is_wired(client: TestClient) -> None:
    html = _page(client)
    app_js = client.get("/static/app.js").text
    for endpoint in ("/api/simulate", "/api/replay/", "/api/manual/start",
                     "/api/catalog", "/api/programs/source"):
        assert endpoint in app_js, f"app.js не вызывает {endpoint}"
    assert "canvas" in html.lower()


def test_vision_toggle_exists_everywhere(client: TestClient) -> None:
    """Галочка «зрение» должна быть и в бою, и в реплее, и в ручном бою."""
    html = _page(client)
    for box in ("opt-vision", "rep-vision", "man-vision"):
        assert f'id="{box}"' in html, f"нет галочки {box}"
    app_js = client.get("/static/app.js").text
    for box in ("opt-vision", "rep-vision", "man-vision"):
        assert box in app_js, f"app.js не читает галочку {box}"


def test_trails_actually_follow_the_checkbox(client: TestClient) -> None:
    """Галочка траекторий обязана влиять на отрисовку.

    Раньше ``opts.trails`` вычислялся из галочки и тут же затирался массивом
    ``p.trails``, так что переключатель не делал ничего, а на карте висели
    траектории всего боя целиком.
    """
    app_js = client.get("/static/app.js").text
    render_js = client.get("/static/render.js").text
    assert "trails: $(ui.trails).checked ? p.trails : null" in app_js, \
        "app.js должен отдавать массив траекторий только при включённой галочке"
    assert re.search(r"trails:\s*p\.trails\b", app_js) is None, \
        "массив траекторий передаётся в draw() без галочки"
    assert re.search(r"tr\.(p|q)\b", render_js) is None, \
        "render.js читает устаревший формат траекторий"
    assert "tr.pts" in render_js, "render.js должен резать траекторию по тикам"


def test_trails_are_cut_by_time(client: TestClient) -> None:
    """Хвост траектории должен исчезать, а не висеть до конца боя.

    Проверяем ту же арифметику, что в ``drawTrails``: точка за точкой до
    текущего кадра, плюс окно жизни TRAIL_TTL. Если формат трека снова
    поменяется, тест напомнит и про него.
    """
    ttl = int(re.search(r"TRAIL_TTL\s*=\s*(\d+)",
                        client.get("/static/render.js").text).group(1))
    keys = [p["key"] for p in client.get("/api/catalog").json()["programs"] if p["ok"]]
    run = client.post("/api/simulate", json={
        "a": {"kind": "script", "key": keys[0]},
        "b": {"kind": "script", "key": keys[4] if len(keys) > 4 else keys[0]},
        "map_name": "arena", "seed": 4, "budget_ms": 10, "max_seconds": 30,
    })
    assert run.status_code == 200, run.text
    run_id = run.json()["run_id"]
    body: dict = {}
    for _ in range(120):                      # ждём расчёта боя, он дольше 12 с
        body = client.get(f"/api/simulate/{run_id}").json()
        if body.get("status") in ("done", "error"):
            break
        time.sleep(0.1)
    assert body.get("status") == "done", body
    replay = client.get(f"/api/replay/{body['replay_id']}").json()
    bullets = replay["bullets"]
    assert bullets, "в бою не было ни одного выстрела"
    for tr in bullets:
        ticks = [pt[2] for pt in tr["pts"]]
        assert ticks == sorted(ticks), "тики трека должны идти по порядку"
        assert len(tr["pts"]) == len({tuple(pt) for pt in tr["pts"]}), "точки повторяются"

    def shown(idx: int) -> int:
        n = 0
        for tr in bullets:
            pts = tr["pts"]
            k = sum(1 for pt in pts if pt[2] <= idx)
            if k >= 2 and (idx - pts[k - 1][2]) < ttl:
                n += 1
        return n

    last = len(replay["t"]) - 1
    assert shown(last) <= shown(last // 2) or shown(last) <= ttl, \
        "в конце боя на карте должно остаться меньше хвостов, чем в середине"


def test_vision_cone_is_drawn(client: TestClient) -> None:
    """Конус зрения рисуется всегда, когда включена галочка."""
    render_js = client.get("/static/render.js").text
    assert "drawVision(scene" not in render_js, \
        "конус зрения не должен рисоваться по scene: в реплее enemy_seen нет"
    assert "if (!scene.enemy_seen) return" not in render_js, \
        "конус зрения не должен выходить по scene.enemy_seen"
    assert "drawVision(pov, opts)" in render_js, "draw() должен звать drawVision от выбранного танка"
    assert "foeVisible" in render_js, "противник должен скрываться вне зоны видимости"


def test_vision_is_full_circle(client: TestClient) -> None:
    """Обзор круговой: движок не режет сектор, рендер не делит угол дважды."""
    render_js = client.get("/static/render.js").text
    bal = client.get("/api/balance").json()["balance"]
    assert bal["view_cone"] >= 180, "зона обзора должна быть круговой, а не конусом"

    # Раньше здесь стояло `opts.cone / 2`, и на экран попадал сектор вдвое
    # уже настоящего — противник пропадал, когда танк смотрел в сторону.
    assert "opts.cone == null ? 180 : opts.cone" in render_js, \
        "drawVision должен брать половину угла из настроек без деления"
    assert "cone = ((opts.cone || 62) * Math.PI) / 180 / 2" not in render_js, \
        "лишнее деление на 2 делает конус вдвое уже, чем реальный обзор"
    assert "rays = full ? 72 : 40" in render_js, "для круга нужно больше лучей"


def test_render_opaque_tiles_match_engine(client: TestClient) -> None:
    """Стены в рендере и в движке должны быть одни и те же."""
    from config import OPAQUE_TILES

    render_js = client.get("/static/render.js").text
    m = re.search(r"OPAQUE = new Set\(\[([^\]]+)\]\)", render_js)
    assert m, "в render.js должен быть набор непрозрачных тайлов"
    js_tiles = set(re.findall(r"'([^']+)'", m.group(1)))
    assert js_tiles == set(OPAQUE_TILES), (
        f"рендер считает стенами {sorted(js_tiles)}, "
        f"а движок — {sorted(OPAQUE_TILES)}; сквозь низкое укрытие ':' видно"
    )
    assert ":" not in js_tiles, "низкое укрытие мешает проезду, но не обзору"


def test_replay_write_is_atomic(tmp_path) -> None:
    """Чтение реплея во время записи не должно падать.

    Раньше файл писался прямо на месте, и читатель успевал открыть
    обрезанный gzip — API отвечал 500 и тест падал в зависимости от того,
    как повезло с таймингом.
    """
    import threading
    import time

    from engine.replay import Replay, load_replay, save_replay

    big = Replay(meta={"a": {"name": "A"}}, summary={},
                 t=[i / 60 for i in range(900)],
                 bullets=[{"pts": [[i, i, i] for i in range(40)]} for _ in range(20)])
    big.id = "race"
    save_replay(big, tmp_path)

    errors: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                load_replay("race", tmp_path)
            except FileNotFoundError:
                pass
            except Exception as exc:                # noqa: BLE001
                errors.append(repr(exc))
            time.sleep(0.01)                         # читатель из UI, не busy-loop

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    for _ in range(30):
        save_replay(big, tmp_path)                   # тот же id, перезапись
    stop.set()
    for t in threads:
        t.join(timeout=2)

    assert not errors, f"чтение во время записи падало: {errors[:3]}"
    assert not list(tmp_path.glob(".*tmp")), "временные файлы не убраны"


def test_manual_frames_are_interpolated(client: TestClient) -> None:
    """Экран не должен прыгать между пачками кадров: нужна интерполяция."""
    app_js = client.get("/static/app.js").text

    assert "m.frames[m.frames.length - 1]" not in app_js, \
        "рисовать только последний кадр пачки значит прыгать на 8 тиков за раз"
    assert "lerpTank" in app_js, "нужна интерполяция положения между кадрами"
    assert "lerpAngle" in app_js, "угол надо крутить по короткой дуге, иначе рывок на 2*PI"
    assert re.search(r"h:\s*a\.h\s*\+\s*\(b\.h\s*-\s*a\.h\)\s*\*\s*k", app_js) is None, \
        "линейная интерполяция угла ломается на переходе через 359 градусов"
    assert "S.man.frames.length - 60" in app_js, \
        "очередь должна содержать только кадры для интерполяции, а не копить секунду"

