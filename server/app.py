"""HTTP/WebSocket API TANKSIM и раздача веб-интерфейса."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import BALANCE, SCRIPTS_DIR, WEB_DIR
from engine import armor as armor_mod
from engine.bundle import MAX_ARCHIVE_BYTES, BundleError, unpack
from engine.grid import list_maps
from engine.program import fighter_name
from engine.replay import list_replays
from engine.sandbox import ProgramError
from server.manual import ManualSession
from server.models import BattleIn, InputMsg, ManualIn
from server.sessions import safe_program_path, sessions

app = FastAPI(title="TANKSIM", version="1.0.0",
              description="Арена танковых программ: загрузка скриптов, расчёт боя, просмотр.")

MANUAL_SESSIONS: dict[str, ManualSession] = {}


# --- каталог ----------------------------------------------------------------


@app.get("/api/catalog")
def catalog() -> dict:
    """Программы, карты и текущий баланс — всё для одного запроса."""
    return {"programs": programs(), "maps": list_maps(), "balance": BALANCE.to_dict()}


@app.get("/api/programs")
def programs() -> list[dict]:
    out = []
    for m in sessions.catalog.all():
        d = m.to_dict()
        key = sessions.catalog.key_of(m)
        d["key"] = key
        d["file"] = Path(m.path).name
        # У пакета имя боя — это имя каталога, а не «main» из точки входа.
        d["fighter"] = fighter_name(Path(m.package).name if m.package else m.path)
        out.append(d)
    return out


@app.get("/api/programs/source")
def program_source(key: str) -> dict:
    """Исходник выбранной программы — чтобы показать пользователю код."""
    path = safe_program_path(key)
    if path is None or not path.exists():
        raise HTTPException(404, "программа не найдена")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"не удалось прочитать: {exc}") from exc
    meta = sessions.catalog.get(key)
    return {"key": key, "source": source,
            "meta": meta.to_dict() if meta else {}}


@app.post("/api/upload")
async def upload(file: UploadFile) -> dict:
    """Загрузка своего боя: скрипт TANKP или zip с программой.

    Простой боец — это один файл. Если рядом с кодом нужны веса, настройки
    или несколько модулей, приходит zip: ``main.py`` в корне архива —
    точка входа, остальные файлы ложатся рядом с ней и едут в бой вместе.
    """
    raw = await file.read()
    name = Path(file.filename or "program").name
    if name.lower().endswith(".zip"):
        return _upload_package(raw, name)
    if len(raw) > 512 * 1024:
        raise HTTPException(413, "файл больше 512 КБ")
    try:
        source = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, f"файл не в кодировке UTF-8: {exc}") from exc
    target = _free_script_path(_upload_stem(name))
    meta = sessions.catalog.register(target, source)
    if not meta.ok:
        # Мусор в каталоге только мешает: убираем файл и объясняем причину.
        sessions.catalog.drop_upload(sessions.catalog.key_of(meta))
        raise HTTPException(400, f"это не программа TANKP: {'; '.join(meta.errors)}")
    d = meta.to_dict()
    d["key"] = sessions.catalog.key_of(meta)
    d["file"] = target.name
    d["fighter"] = fighter_name(target.name)
    return d


def _upload_package(raw: bytes, name: str) -> dict:
    """Распаковывает zip с программой в каталог загрузок и регистрирует его."""
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise HTTPException(413, f"архив больше {MAX_ARCHIVE_BYTES // (1024 * 1024)} МБ")
    target = SCRIPTS_DIR / _upload_stem(name)
    try:
        info = unpack(raw, target)
    except BundleError as exc:
        # unpack удаляет огрызок сам, в каталоге не остаётся мусора.
        raise HTTPException(400, f"zip не принят: {exc}") from exc
    meta = sessions.catalog.register_package(info.root)
    if not meta.ok:
        sessions.catalog.drop_upload(sessions.catalog.key_of(meta))
        raise HTTPException(400, f"это не программа TANKP: {'; '.join(meta.errors)}")
    d = meta.to_dict()
    d["key"] = sessions.catalog.key_of(meta)
    d["file"] = info.entry.name
    d["fighter"] = fighter_name(info.root.name)
    d["files"] = info.written
    d["skipped"] = info.skipped
    return d


def _upload_stem(name: str) -> str:
    """Имя файла без расширения, пригодное для каталога.

    Имя файла — это и есть имя бойца, поэтому буквы любого алфавита и пробелы
    остаются как есть, а запрещённые в именах файлов символы заменяются на
    подчёркивание. Раньше здесь оставались одни цифры и «program».
    """
    raw_stem = re.sub(r"\.tankp\.py$|\.py$|\.zip$", "", name, flags=re.IGNORECASE)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw_stem).strip(" .")
    return stem or "program"


def _free_script_path(stem: str) -> Path:
    """Путь для нового скрипта. Перезаписываем старый файл, если он есть,
    чтобы при редактировании скрипта не плодились копии с номерами.
    """
    return SCRIPTS_DIR / f"{stem}.tankp.py"


@app.get("/api/maps")
def maps() -> list[dict]:
    return list_maps()


@app.get("/api/balance")
def balance() -> dict:
    """Баланс и справочная таблица урона — панель «как пробить броню»."""
    return {
        "balance": BALANCE.to_dict(),
        "armor_table": armor_mod.armor_table(BALANCE),
        "faces": armor_mod.FACE_LABELS,
    }


# --- расчёт боя -------------------------------------------------------------


def _player_config(p) -> dict:
    kind = "manual" if p.kind == "manual" else "script"
    key = p.key or ""
    meta = sessions.catalog.get(key)
    if kind == "script":
        if meta is None:
            raise HTTPException(400, f"программа «{key or '—'}» не найдена в каталоге")
        if not meta.ok:
            raise HTTPException(400, f"{meta.name}: " + "; ".join(meta.errors))
    return {"kind": kind, "source": str(safe_program_path(key) or ""),
            "name": p.name or (meta.name if meta else ""),
            "color": p.color or (meta.color if meta else ""), "key": key,
            "seed": 0}


@app.post("/api/simulate")
def simulate(req: BattleIn) -> dict:
    """Запускает расчёт боя в фоне и отдаёт его идентификатор."""
    from config import BattleConfig, PlayerConfig

    pa = _player_config(req.a)
    pb = _player_config(req.b)
    if pa["kind"] == "manual" or pb["kind"] == "manual":
        raise HTTPException(400, "ручное управление — только в режиме живого боя")
    if pa["source"] == pb["source"]:
        raise HTTPException(400, "нельзя выставить программу против самой себя")

    cfg = BattleConfig(
        a=PlayerConfig(kind=pa["kind"], source=pa["source"], name=pa["name"],
                       color=pa["color"]),
        b=PlayerConfig(kind=pb["kind"], source=pb["source"], name=pb["name"],
                       color=pb["color"]),
        map_name=req.map_name, budget_ms=req.budget_ms, seed=req.seed,
        max_seconds=req.max_seconds)
    run = sessions.new_run(cfg)
    run.start()
    return {"run_id": run.id, "status": run.status}


@app.get("/api/simulate/{run_id}")
def simulate_status(run_id: str) -> dict:
    run = sessions.runs.get(run_id)
    if run is None:
        raise HTTPException(404, "бой не найден")
    out = {"run_id": run.id, "status": run.status, "progress": round(run.progress, 4),
           "message": run.message, "error": run.error}
    if run.status == "done" and run.replay:
        out["replay_id"] = run.replay.id
        out["summary"] = run.replay.summary
    return out


@app.get("/api/replay/{replay_id}")
def replay(replay_id: str) -> JSONResponse:
    rep = sessions.get_replay(replay_id)
    if rep is None:
        raise HTTPException(404, "реплей не найден")
    body = rep.to_payload()
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@app.get("/api/replays")
def replays() -> list[dict]:
    return list_replays()


# --- ручной бой -------------------------------------------------------------


def _finish_manual(sess: ManualSession) -> dict:
    """Останавливает живую сессию и сохраняет её реплей.

    Вызывается и по кнопке «стоп», и автоматически, когда бой дошёл до конца:
    итог должен попасть в реплей в обоих случаях.
    """
    # Сначала останавливаем цикл: он допишет результат боя в реплей,
    # иначе на диск лёг бы файл без итога.
    sess.stop()
    replay_id = None
    if sess.replay is not None:
        from engine.replay import save_replay

        try:
            save_replay(sess.replay)
            replay_id = sess.replay.id
        except OSError as exc:
            sess.error = sess.error or f"реплей не сохранён: {exc}"
    return {"stopped": True, "replay_id": replay_id,
            "result": sess.result, "error": sess.error}


@app.post("/api/manual/start")
def manual_start(req: ManualIn) -> dict:
    """Создаёт живую сессию: игрок против программы."""
    meta = sessions.catalog.get(req.key)
    if meta is None or not meta.ok:
        raise HTTPException(400, f"программа «{req.key}» не найдена или сломана")
    for old_id, old in list(MANUAL_SESSIONS.items()):
        old.stop()
        MANUAL_SESSIONS.pop(old_id, None)
    sid = f"s{int(time.time() * 1000) % 10_000_000}"
    sess = ManualSession(sid, req.player_side, req.key, req.map_name,
                         req.budget_ms, req.seed, req.player_name, BALANCE,
                         reveal=req.reveal)
    sess.start()
    MANUAL_SESSIONS[sid] = sess
    if sess.error and sess.runner is None:
        MANUAL_SESSIONS.pop(sid, None)
        raise HTTPException(400, sess.error)
    return {"session_id": sid, "map": sess.arena.to_dict(),
            "player_side": req.player_side, "spawns": [
                {"x": s.x, "y": s.y, "angle": s.angle} for s in sess.arena.spawns]}


@app.websocket("/api/manual/ws/{sid}")
async def manual_ws(ws: WebSocket, sid: str) -> None:
    """Поток управления: клиент шлёт ввод, сервер шлёт кадры."""
    sess = MANUAL_SESSIONS.get(sid)
    if sess is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    await ws.send_text(json.dumps({
        "type": "hello", "map": sess.arena.to_dict(),
        "balance": BALANCE.to_dict(),
        "player_side": sess.player_side,
        "spawns": [{"x": s.x, "y": s.y, "angle": s.angle} for s in sess.arena.spawns],
    }, ensure_ascii=False))

    stop = asyncio.Event()

    async def reader() -> None:
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = InputMsg.model_validate_json(raw)
                except Exception:  # noqa: BLE001
                    continue
                sess.set_input(msg.keys, msg.aim, msg.fire)
                if msg.reveal is not None:
                    sess.reveal = msg.reveal
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Соединение порвалось — забытые клавиши остались бы в сессии, и
            # танк продолжал бы ехать сам. Сбрасываем ввод при любом исходе.
            sess.set_input([], [0.0, 0.0], False)
        stop.set()

    async def writer() -> None:
        sent_end = False
        try:
            while not stop.is_set():
                if sess.frames:
                    batch = list(sess.frames)
                    sess.frames.clear()
                    await ws.send_text(json.dumps(
                        {"type": "frames", "frames": batch,
                         "error": sess.error}, ensure_ascii=False, default=float))
                if sess.over and not sent_end:
                    # финальный кадр с результатом, даже если он не в потоке
                    sent_end = True
                    last = list(sess.frames)
                    sess.frames.clear()
                    MANUAL_SESSIONS.pop(sid, None)
                    done = _finish_manual(sess)
                    await ws.send_text(json.dumps(
                        {"type": "end", "frames": last, **done},
                        ensure_ascii=False, default=float))
                    break
                await asyncio.sleep(1 / 90)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            stop.set()

    r = asyncio.create_task(reader())
    w = asyncio.create_task(writer())
    try:
        await stop.wait()
    finally:
        stop.set()
        r.cancel()
        w.cancel()
        try:
            await ws.close()
        except RuntimeError:
            pass


@app.post("/api/manual/stop/{sid}")
def manual_stop(sid: str) -> dict:
    sess = MANUAL_SESSIONS.pop(sid, None)
    if sess is None:
        raise HTTPException(404, "сессия не найдена")
    return _finish_manual(sess)


@app.get("/api/manual/replay/{sid}")
def manual_replay(sid: str) -> JSONResponse:
    sess = MANUAL_SESSIONS.get(sid)
    if sess is None or sess.replay is None:
        raise HTTPException(404, "реплей ручного боя не найден")
    return JSONResponse(sess.replay.to_payload())


# --- статика ----------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "manual": len(MANUAL_SESSIONS)}


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.exception_handler(ProgramError)
async def program_error_handler(_request, exc: ProgramError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)