"""Тесты обменника pool/: публикация, реестр, проверки, pull мимо git.

Всё крутится на временном --root, настоящий pool/ и сеть не трогаем:
сеть подменяется monkeypatch, бои — короткие и локальные.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import pool as poolmod
from tools.pool import (
    extract_pool_from_tarball,
    main,
    publish,
    remote_tree,
    scan,
    smoke_battle,
    validate_pool,
)

TANK_SRC = """#!TANKP 1
# name: TestBot
from tankp import TankProgram, Action

class Brain(TankProgram):
    def on_tick(self, o):
        return Action(drive=1.0)

program = Brain()
"""

BROKEN_SRC = "print('no magic here')\nprogram = 1\n"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_zip(path: Path, main_src: str = TANK_SRC) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.py", main_src)
        zf.writestr("helper.py", "VALUE = 42\n")
    return path


# --- publish / validate -------------------------------------------------------


def test_publish_script_roundtrip(tmp_path: Path):
    src = write(tmp_path / "my_bot.tankp.py", TANK_SRC)
    dest, replaced = publish(src, "agent-7", None, tmp_path, "едет вперёд")
    assert dest == tmp_path / "pool" / "agent-7" / "my_bot.tankp.py"
    assert not replaced
    assert dest.read_text(encoding="utf-8") == TANK_SRC
    meta = json.loads((tmp_path / "pool" / "agent-7" / "meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["agent"] == "agent-7"
    assert [t["file"] for t in meta["tanks"]] == ["my_bot.tankp.py"]
    assert meta["tanks"][0]["description"] == "едет вперёд"
    assert validate_pool(tmp_path / "pool") == []


def test_publish_rename_and_republish_keeps_description(tmp_path: Path):
    src = write(tmp_path / "a.py", TANK_SRC)
    dest, _ = publish(src, "bot", "killer", tmp_path, "злой")
    assert dest.name == "killer.tankp.py"
    _, replaced = publish(src, "bot", "killer", tmp_path)
    assert replaced
    meta = json.loads((dest.parent / "meta.json").read_text(encoding="utf-8"))
    # Описание без --describe не затирается пустым.
    assert meta["tanks"][0]["description"] == "злой"
    assert validate_pool(tmp_path / "pool") == []


def test_publish_rejects_broken_and_bad_names(tmp_path: Path):
    bad = write(tmp_path / "bad.tankp.py", BROKEN_SRC)
    with pytest.raises(SystemExit):
        publish(bad, "agent-1", None, tmp_path)
    good = write(tmp_path / "good.tankp.py", TANK_SRC)
    with pytest.raises(SystemExit):
        publish(good, "Bad Agent!", None, tmp_path)
    with pytest.raises(SystemExit):
        publish(good, "agent-1", "имя.py", tmp_path)
    assert not (tmp_path / "pool").exists()


def test_publish_zip_bundle(tmp_path: Path):
    src = make_zip(tmp_path / "pack.zip")
    dest, _ = publish(src, "zip-bot", "heavy", tmp_path)
    assert dest.name == "heavy.zip"
    assert validate_pool(tmp_path / "pool") == []


def test_publish_zip_without_main_rejected(tmp_path: Path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "no entry")
    with pytest.raises(SystemExit):
        publish(path, "zip-bot", None, tmp_path)


def test_validate_catches_unregistered_and_tampered(tmp_path: Path):
    src = write(tmp_path / "x.tankp.py", TANK_SRC)
    publish(src, "bot", None, tmp_path)
    agent_dir = tmp_path / "pool" / "bot"
    # Файл мимо publish — не зарегистрирован.
    write(agent_dir / "ghost.tankp.py", TANK_SRC)
    errors = validate_pool(tmp_path / "pool")
    assert any("ghost" in e and "meta.json" in e for e in errors)
    # Подмена содержимого — sha не сходится.
    (agent_dir / "x.tankp.py").write_text(TANK_SRC + "# tampered\n",
                                          encoding="utf-8")
    errors = validate_pool(tmp_path / "pool")
    assert any("sha256" in e for e in errors)
    # --fix пересобирает реестр по диску, описания сохраняются.
    assert validate_pool(tmp_path / "pool", fix=True)
    fixed = json.loads((agent_dir / "meta.json").read_text(encoding="utf-8"))
    assert sorted(t["file"] for t in fixed["tanks"]) == ["ghost.tankp.py",
                                                         "x.tankp.py"]
    assert validate_pool(tmp_path / "pool") == []


def test_validate_catches_junk_and_orphans(tmp_path: Path):
    src = write(tmp_path / "x.tankp.py", TANK_SRC)
    publish(src, "bot", None, tmp_path)
    agent_dir = tmp_path / "pool" / "bot"
    write(agent_dir / "notes.txt", "junk")
    meta_path = agent_dir / "meta.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["tanks"].append({"file": "gone.tankp.py", "sha256": "0" * 64,
                          "size": 1, "added": "x", "description": ""})
    meta_path.write_text(json.dumps(data), encoding="utf-8")
    errors = validate_pool(tmp_path / "pool")
    assert any("notes.txt" in e for e in errors)
    assert any("gone.tankp.py" in e for e in errors)


def test_scan_and_list_json(tmp_path: Path, capsys):
    src = write(tmp_path / "x.tankp.py", TANK_SRC)
    publish(src, "bot-a", None, tmp_path)
    make_zip_path = make_zip(tmp_path / "p.zip")
    publish(make_zip_path, "bot-b", "iron", tmp_path)
    subs = scan(tmp_path / "pool")
    assert [(s.agent, s.filename, s.kind, s.registered) for s in subs] == [
        ("bot-a", "x.tankp.py", "script", True),
        ("bot-b", "iron.zip", "archive", True),
    ]
    assert main(["--root", str(tmp_path), "list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["file"] for d in data] == ["x.tankp.py", "iron.zip"]


# --- pull мимо git ------------------------------------------------------------


def make_tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            raw = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


def test_extract_tarball_only_pool_and_no_traversal(tmp_path: Path):
    raw = make_tarball({
        "TankiMini2D-abc123/pool/bot-1/a.tankp.py": TANK_SRC,
        "TankiMini2D-abc123/pool/bot-1/meta.json": '{"tanks": []}',
        "TankiMini2D-abc123/README.md": "not pool",
        "TankiMini2D-abc123/pool/../../evil.py": "escape",
    })
    out = tmp_path / "fetched"
    added, updated = extract_pool_from_tarball(raw, out)
    assert (added, updated) == (2, 0)
    assert (out / "bot-1" / "a.tankp.py").read_text(encoding="utf-8") == TANK_SRC
    assert not (tmp_path / "evil.py").exists()
    assert not list(tmp_path.glob("*/evil.py"))
    # Повторный прогон ничего не меняет.
    assert extract_pool_from_tarball(raw, out) == (0, 0)


def test_remote_tree_parses_api_response(monkeypatch):
    payload = {"tree": [
        {"path": "pool/bot-1/a.tankp.py", "type": "blob"},
        {"path": "pool/bot-1/meta.json", "type": "blob"},
        {"path": "pool/README.md", "type": "blob"},
        {"path": "ai/01_chaser.tankp.py", "type": "blob"},
        {"path": "pool/bot-1", "type": "tree"},
    ]}
    monkeypatch.setattr(poolmod, "http_get",
                        lambda url, timeout=60.0: json.dumps(payload).encode())
    assert remote_tree("o/r", "master") == [
        "pool/README.md",
        "pool/bot-1/a.tankp.py",
        "pool/bot-1/meta.json",
    ]


def test_pull_via_api_uses_tarball(tmp_path: Path, monkeypatch):
    raw = make_tarball({"R-sha/pool/rmt/r.tankp.py": TANK_SRC,
                        "R-sha/pool/rmt/meta.json": '{"agent":"rmt","tanks":[]}'})
    monkeypatch.setattr(poolmod, "http_get",
                        lambda url, timeout=60.0: raw)
    rc = main(["--root", str(tmp_path), "pull", "--via", "api",
               "--repo", "o/r", "--ref", "master",
               "--out", str(tmp_path / "fetched")])
    assert rc == 0
    assert (tmp_path / "fetched" / "rmt" / "r.tankp.py").exists()


# --- дымовой бой --------------------------------------------------------------


def test_smoke_battle_runs(tmp_path: Path):
    src = write(tmp_path / "x.tankp.py", TANK_SRC)
    dest, _ = publish(src, "bot", None, tmp_path)
    ref = Path(__file__).resolve().parents[1] / "ai" / "01_chaser.tankp.py"
    result = smoke_battle(dest, str(ref), seconds=2, seed=7)
    assert result.startswith("бой "), result
