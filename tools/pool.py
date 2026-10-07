"""Обменник танковых программ между агентами: каталог ``pool/`` в репозитории.

Идея: весь обмен идёт через обычный git — агент кладёт свой файл в
``pool/<agent-id>/``, коммитит и пушит (или открывает PR). Любой другой агент
забирает всё командой ``pull``: либо ``git pull``, либо скачиванием архива
репозитория через GitHub API, если полного клона под рукой нет.

Раскладка::

    pool/
      README.md                 правила для агентов (коротко)
      <agent-id>/
        meta.json               реестр файлов агента: sha256, размеры, описания
        <tank>.tankp.py         одиночная программа
        <tank>.zip              пакет: main.py строго в корне архива

Единого манифеста на весь pool нет специально: каждый агент пишет только
в свой подкаталог и в свой ``meta.json``, поэтому параллельные публикации
никогда не конфликтуют в git. Сводный список строится сканированием
(``pool list``), целостность даёт сам git (хеш коммита) плюс sha256 в meta.

Команды::

    python tools/pool.py publish my_tank.tankp.py --agent my-bot
    python tools/pool.py publish my_tank.tankp.py --agent my-bot --commit --push
    python tools/pool.py publish my_tank.tankp.py --agent my-bot --commit --pr
    python tools/pool.py list
    python tools/pool.py list --remote [--ref master] [--json]
    python tools/pool.py pull
    python tools/pool.py pull --via api --ref master [--out DIR]
    python tools/pool.py validate [--fix] [--smoke]

Модуль зависит только от стандартной библиотеки и от ``engine.program`` /
``engine.bundle`` (тоже stdlib), поэтому ``list``/``pull``/``publish``/
``validate`` работают без установки зависимостей проекта. Дымовой прогон
боёв (``--smoke``) подтягивает ``battle.runner`` лениво, внутри команды.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.bundle import (  # noqa: E402
    BundleError,
    inspect as inspect_bundle,
    unpack as unpack_bundle,
)
from engine.program import fighter_name, load_package, load_program  # noqa: E402

#: Репозиторий по умолчанию, если не определился из git remote.
DEFAULT_REPO = "timursivko/TankiMini2D"
POOL_NAME = "pool"
META_NAME = "meta.json"
SCRIPT_SUFFIX = ".tankp.py"
ARCHIVE_SUFFIX = ".zip"
#: Одиночный скрипт больше полумегабайта — почти наверняка мусор или веса,
#: которым место в zip-пакете.
MAX_SCRIPT_BYTES = 512 * 1024

#: id агента: только латиница/цифры, чтобы имя было и путём, и веткой, и тегом.
AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
#: Имя танка (без расширения): строже, чем имя файла вообще, — оно же станет
#: именем бойца и должно одинаково переживать Windows, git и zip.
TANK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"


# --- общие мелочи -------------------------------------------------------------


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pool_dir(root: Path = ROOT) -> Path:
    return root / POOL_NAME


def repo_slug(root: Path = ROOT) -> str:
    """owner/repo из git remote origin, иначе DEFAULT_REPO."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_REPO
    url = (proc.stdout or "").strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        return DEFAULT_REPO
    return f"{m.group(1)}/{m.group(2)}"


def github_headers() -> dict[str, str]:
    headers = {
        "User-Agent": "TankiMini2D-pool",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def http_get(url: str, timeout: float = 60.0) -> bytes:
    req = urlrequest.Request(url, headers=github_headers())
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"GitHub ответил {exc.code} на {url}: {body}")
    except URLError as exc:
        raise SystemExit(f"не удалось скачать {url}: {exc.reason}")


# --- реестр -------------------------------------------------------------------


@dataclass
class Submission:
    agent: str
    filename: str
    path: Path
    kind: str            # "script" | "archive"
    size: int
    sha256: str
    registered: bool     # есть ли запись в meta.json с совпадающим sha256
    description: str = ""


def load_meta(agent_dir: Path) -> tuple[dict, list[str]]:
    """Читает meta.json агента. Возвращает (данные, ошибки)."""
    meta_path = agent_dir / META_NAME
    if not meta_path.is_file():
        return {}, [f"{META_NAME} отсутствует — опубликуйте через "
                    f"'python tools/pool.py publish'"]
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [f"{META_NAME} не читается: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("tanks"), list):
        return {}, [f"{META_NAME}: ждали объект с ключом 'tanks' (список)"]
    return data, []


def save_meta(agent_dir: Path, data: dict) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / META_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_meta(agent_dir: Path, agent: str, filename: str,
                size: int, digest: str, description: str = "") -> dict:
    """Добавляет/обновляет запись о файле, сохраняя старое описание."""
    data, _ = load_meta(agent_dir)
    if not isinstance(data, dict):
        data = {}
    tanks = data.get("tanks")
    if not isinstance(tanks, list):
        tanks = []
    old = {t.get("file"): t for t in tanks if isinstance(t, dict)}
    entry = {
        "file": filename,
        "sha256": digest,
        "size": size,
        "added": utcnow(),
        "description": description or old.get(filename, {}).get("description", ""),
    }
    tanks = [t for t in tanks if not isinstance(t, dict)
             or t.get("file") != filename]
    tanks.append(entry)
    tanks.sort(key=lambda t: t.get("file", ""))
    data.update({"agent": agent, "updated": utcnow(), "tanks": tanks})
    save_meta(agent_dir, data)
    return data


def scan(pool: Path) -> list[Submission]:
    """Все файлы pool/ с пометкой, зарегистрированы ли они в meta.json."""
    found: list[Submission] = []
    if not pool.is_dir():
        return found
    for agent_dir in sorted(p for p in pool.iterdir() if p.is_dir()):
        data, _ = load_meta(agent_dir)
        entries = {t.get("file"): t for t in data.get("tanks", [])
                   if isinstance(t, dict)} if isinstance(data, dict) else {}
        for path in sorted(agent_dir.iterdir()):
            if not path.is_file() or path.name == META_NAME:
                continue
            if path.name.endswith(SCRIPT_SUFFIX):
                kind = "script"
            elif path.name.endswith(ARCHIVE_SUFFIX):
                kind = "archive"
            else:
                kind = "unknown"
            entry = entries.get(path.name, {})
            digest = sha256_of(path)
            found.append(Submission(
                agent=agent_dir.name, filename=path.name, path=path, kind=kind,
                size=path.stat().st_size, sha256=digest,
                registered=bool(entry) and entry.get("sha256") == digest,
                description=str(entry.get("description", "")),
            ))
    return found


# --- проверки содержимого -----------------------------------------------------


def check_script(path: Path) -> list[str]:
    """Одиночный .tankp.py: размер + разбор движком (заголовок, синтаксис)."""
    errors: list[str] = []
    try:
        size = path.stat().st_size
    except OSError as exc:
        return [f"не удалось прочитать файл: {exc}"]
    if size > MAX_SCRIPT_BYTES:
        errors.append(f"размер {size} байт больше лимита {MAX_SCRIPT_BYTES}: "
                      f"веса и модули пакуйте в zip")
    meta = load_program(path)
    errors.extend(meta.errors)
    return errors


def check_archive(path: Path) -> list[str]:
    """zip: правила bundle (пути, имена, бомба) + main.py разбирается движком."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"не удалось прочитать файл: {exc}"]
    try:
        inspect_bundle(raw)
    except BundleError as exc:
        return [str(exc)]
    with tempfile.TemporaryDirectory(prefix="pool-check-") as tmp:
        target = Path(tmp) / "pkg"
        try:
            unpack_bundle(raw, target)
        except BundleError as exc:
            return [str(exc)]
        meta = load_package(target)
        return [f"main.py: {e}" for e in meta.errors]


def check_agent_id(agent: str) -> str:
    if not AGENT_RE.match(agent):
        raise SystemExit(
            f"плохой id агента «{agent}»: только a-z, 0-9, '-' и '_', "
            f"начинается с буквы или цифры, длина 1–32")
    return agent


def check_tank_name(name: str) -> str:
    if not TANK_RE.match(name):
        raise SystemExit(
            f"плохое имя танка «{name}»: только латиница, цифры, '_', '-' и '.', "
            f"длина 1–64")
    if name.lower().endswith((".py", ".tankp", ".zip")):
        raise SystemExit(f"имя танка «{name}» не должно содержать расширение — "
                         f"оно добавится само")
    return name


# --- publish ------------------------------------------------------------------


def detect_kind(src: Path) -> str:
    name = src.name.lower()
    if name.endswith(SCRIPT_SUFFIX) or name.endswith(".py"):
        return "script"
    if name.endswith(ARCHIVE_SUFFIX):
        return "archive"
    raise SystemExit(f"«{src.name}»: ждали .tankp.py (.py) или .zip")


def publish(src: Path, agent: str, name: str | None, root: Path,
            description: str = "") -> tuple[Path, bool]:
    """Проверяет файл, кладёт в pool/<agent>/, обновляет meta.json.

    Возвращает (путь назначения, перезаписан_ли_существующий).
    """
    check_agent_id(agent)
    if not src.is_file():
        raise SystemExit(f"файл «{src}» не найден")
    kind = detect_kind(src)
    stem = fighter_name(src.name) if name is None else check_tank_name(name)
    errors = check_script(src) if kind == "script" else check_archive(src)
    if errors:
        raise SystemExit(f"«{src.name}» не принят:\n  - " + "\n  - ".join(errors))
    dest_dir = pool_dir(root) / agent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (stem + (SCRIPT_SUFFIX if kind == "script"
                               else ARCHIVE_SUFFIX))
    replaced = dest.exists()
    shutil.copyfile(src, dest)
    update_meta(dest_dir, agent, dest.name, dest.stat().st_size,
                sha256_of(dest), description)
    return dest, replaced


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"git не запустился: {exc}")


def cmd_publish(args: argparse.Namespace) -> int:
    root = Path(args.root)
    dest, replaced = publish(Path(args.file), args.agent, args.as_name,
                             root, args.describe or "")
    rel = dest.relative_to(root).as_posix()
    print(f"{'обновлён' if replaced else 'добавлен'}: {rel}")
    print(f"  sha256: {sha256_of(dest)}")
    if not (args.commit or args.push or args.pr):
        print("дальше в git руками:")
        print(f"  git add {rel} {Path(rel).parent.as_posix()}/{META_NAME}")
        print(f"  git commit -m \"pool: {args.agent} публикует {dest.name}\"")
        print("  git push   # или в свою ветку + PR")
        return 0
    add = run_git(root, "add", rel, f"{Path(rel).parent.as_posix()}/{META_NAME}")
    if add.returncode:
        print(add.stderr.strip() or "git add не удался", file=sys.stderr)
        return 1
    msg = args.message or f"pool: {args.agent} публикует {dest.name}"
    commit = run_git(root, "commit", "-m", msg)
    if commit.returncode:
        print((commit.stderr or commit.stdout).strip(), file=sys.stderr)
        return 1
    print("закоммичено:", msg)
    if args.push or args.pr:
        branch = run_git(root, "branch", "--show-current").stdout.strip()
        push = run_git(root, "push", "-u", "origin", branch or "HEAD")
        if push.returncode:
            print((push.stderr or push.stdout).strip(), file=sys.stderr)
            print("коммит создан, но push не удался — отправьте ветку вручную",
                  file=sys.stderr)
            return 1
        print(f"отправлено в origin/{branch or 'HEAD'}")
    if args.pr:
        try:
            proc = subprocess.run(
                ["gh", "pr", "create", "--fill",
                 "--title", f"pool: {args.agent} публикует {dest.name}"],
                cwd=str(root), capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemExit(f"gh не запустился: {exc}")
        if proc.returncode:
            print((proc.stderr or proc.stdout).strip(), file=sys.stderr)
            return 1
        print("PR создан:", (proc.stdout or "").strip())
    return 0


# --- list ---------------------------------------------------------------------


def remote_tree(repo: str, ref: str) -> list[str]:
    """Пути pool/* в ветке/коммите ref через GitHub API (один запрос)."""
    url = f"{API}/repos/{repo}/git/trees/{ref}?recursive=1"
    data = json.loads(http_get(url).decode("utf-8"))
    if isinstance(data, dict) and data.get("truncated"):
        print("предупреждение: дерево обрезано API, список может быть неполным",
              file=sys.stderr)
    paths = [t.get("path", "") for t in data.get("tree", [])
             if t.get("type") == "blob" and t.get("path", "").startswith("pool/")]
    return sorted(paths)


def cmd_list(args: argparse.Namespace) -> int:
    if args.remote:
        repo = args.repo or repo_slug(Path(args.root))
        paths = remote_tree(repo, args.ref)
        subs = [p for p in paths if not p.endswith("/" + META_NAME)
                and Path(p).name != "README.md"]
        if args.json:
            print(json.dumps({"repo": repo, "ref": args.ref, "files": subs},
                             ensure_ascii=False, indent=2))
        elif not subs:
            print(f"в {repo}@{args.ref} pool/ пуст")
        else:
            print(f"{repo}@{args.ref}: файлов {len(subs)}")
            for p in subs:
                print(f"  {p}")
        return 0
    subs = scan(pool_dir(Path(args.root)))
    if args.json:
        print(json.dumps([{"agent": s.agent, "file": s.filename, "kind": s.kind,
                           "size": s.size, "sha256": s.sha256,
                           "registered": s.registered,
                           "description": s.description} for s in subs],
                         ensure_ascii=False, indent=2))
        return 0
    if not subs:
        print("локальный pool/ пуст — 'pull' заберёт файлы из GitHub")
        return 0
    print(f"локальный pool/: танков {len(subs)}")
    for s in subs:
        flag = "" if s.registered else "  [НЕ В meta.json]"
        print(f"  {s.agent}/{s.filename}  ({s.size} байт){flag}")
        if s.description:
            print(f"    {s.description}")
    return 0


# --- pull ---------------------------------------------------------------------


def _tar_root(names: list[str]) -> str:
    """Первый компонент пути в тарболле codeload: '<repo>-<sha>/...'."""
    for name in names:
        if "/" in name:
            return name.split("/", 1)[0]
    raise SystemExit("в архиве codeload нет каталогов — странный ответ GitHub")


def extract_pool_from_tarball(raw: bytes, out: Path) -> tuple[int, int]:
    """Достаёт pool/* из tar.gz codeload в out. Возвращает (новых, обновлённых).

    Пишем руками через чтение/запись, а не extractall: в чужом архиве не должно
    быть ни абсолютных путей, ни '..', ни ссылок — всё лишнее пропускаем.
    """
    try:
        tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except tarfile.TarError as exc:
        raise SystemExit(f"codeload отдал не tar.gz: {exc}")
    with tar:
        names = tar.getnames()
        prefix = _tar_root(names) + "/pool/"
        added = updated = 0
        for member in tar.getmembers():
            if not member.name.startswith(prefix) or not member.isfile():
                continue
            rel = Path(member.name[len(prefix):])
            if rel.is_absolute() or ".." in rel.parts:
                continue
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            existed = dest.exists()
            old = sha256_of(dest) if existed else ""
            with tar.extractfile(member) as src, open(dest, "wb") as fh:
                assert src is not None
                shutil.copyfileobj(src, fh)
            if not existed:
                added += 1
            elif sha256_of(dest) != old:
                updated += 1
        return added, updated


def default_branch(repo: str) -> str:
    data = json.loads(http_get(f"{API}/repos/{repo}").decode("utf-8"))
    return data.get("default_branch", "master")


def cmd_pull(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if args.via == "git":
        if args.ref:
            # Забрать чужой pool, не переключая ветку: удобно агенту,
            # который сидит на своей arena/*-ветке, а танки лежат в master.
            fetch = run_git(root, "fetch", "origin", args.ref)
            if fetch.returncode:
                print((fetch.stderr or fetch.stdout).strip(), file=sys.stderr)
                return 1
            co = run_git(root, "checkout", "FETCH_HEAD", "--", POOL_NAME)
            if co.returncode:
                print((co.stderr or co.stdout).strip(), file=sys.stderr)
                return 1
            print(f"pool/ взят из origin/{args.ref} (без переключения ветки)")
        else:
            proc = run_git(root, "pull", "--ff-only")
            print((proc.stdout or "").strip())
            if proc.returncode:
                print((proc.stderr or "").strip(), file=sys.stderr)
                print("подсказка: 'pull --via api --out DIR' скачает pool "
                      "мимо git", file=sys.stderr)
                return 1
        subs = scan(pool_dir(root))
        print(f"в pool/: танков {len(subs)}")
        return 0
    # Через API: один tar.gz с codeload, git не нужен вовсе.
    repo = args.repo or repo_slug(root)
    ref = args.ref or default_branch(repo)
    url = f"{CODELOAD}/{repo}/tar.gz/{ref}"
    print(f"качаю {url} ...")
    raw = http_get(url, timeout=180.0)
    out = Path(args.out) if args.out else pool_dir(root)
    added, updated = extract_pool_from_tarball(raw, out)
    print(f"готово: {out} — новых {added}, обновлено {updated}")
    return 0


# --- validate -----------------------------------------------------------------


def validate_pool(pool: Path, agent_filter: str = "",
                  fix: bool = False) -> list[str]:
    """Проверяет весь pool. Возвращает список ошибок (пусто — всё хорошо)."""
    errors: list[str] = []
    if not pool.is_dir():
        return [f"нет каталога {pool}"]
    for top in sorted(pool.iterdir()):
        if top.name in ("README.md", META_NAME) or top.name.startswith("."):
            continue
        if not top.is_dir():
            errors.append(f"{top.name}: сверху лежат только каталоги агентов")
            continue
        agent = top.name
        if not AGENT_RE.match(agent):
            errors.append(f"{agent}/: плохой id агента (a-z, 0-9, '-', '_')")
        if agent_filter and agent != agent_filter:
            continue
        data, meta_errors = load_meta(top)
        errors.extend(f"{agent}/{META_NAME}: {e}" for e in meta_errors)
        entries = {t.get("file"): t for t in data.get("tanks", [])
                   if isinstance(t, dict)} if data else {}
        if data and data.get("agent", agent) != agent:
            errors.append(f"{agent}/{META_NAME}: поле agent не совпадает "
                          f"с именем каталога")
        seen: set[str] = set()
        for path in sorted(top.iterdir()):
            if path.name == META_NAME or not path.is_file():
                continue
            seen.add(path.name)
            if path.name.endswith(SCRIPT_SUFFIX):
                bad = check_script(path)
            elif path.name.endswith(ARCHIVE_SUFFIX):
                bad = check_archive(path)
            else:
                bad = [f"лишний файл: в pool лежат только "
                       f"*{SCRIPT_SUFFIX} и *{ARCHIVE_SUFFIX}"]
            errors.extend(f"{agent}/{path.name}: {e}" for e in bad)
            stem = path.name[:-len(SCRIPT_SUFFIX)] if path.name.endswith(
                SCRIPT_SUFFIX) else path.name[:-len(ARCHIVE_SUFFIX)]
            if not TANK_RE.match(stem):
                errors.append(f"{agent}/{path.name}: плохое имя танка")
            entry = entries.get(path.name)
            if entry is None:
                errors.append(f"{agent}/{path.name}: нет записи в {META_NAME} — "
                              f"опубликуйте через 'tools/pool.py publish'")
            elif entry.get("sha256") != sha256_of(path):
                errors.append(f"{agent}/{path.name}: sha256 не совпадает "
                              f"с {META_NAME} — переопубликуйте файл")
        for name in sorted(set(entries) - seen):
            errors.append(f"{agent}/{META_NAME}: запись «{name}» без файла")
        if fix and not meta_errors:
            rebuild_meta(top, agent)
    return errors


def rebuild_meta(agent_dir: Path, agent: str) -> None:
    """Пересобирает meta.json по файлам на диске, сохраняя описания."""
    data, _ = load_meta(agent_dir)
    old = {t.get("file"): t for t in data.get("tanks", [])
           if isinstance(t, dict)} if isinstance(data, dict) else {}
    tanks = []
    for path in sorted(agent_dir.iterdir()):
        if not path.is_file() or path.name == META_NAME:
            continue
        if not (path.name.endswith(SCRIPT_SUFFIX)
                or path.name.endswith(ARCHIVE_SUFFIX)):
            continue
        tanks.append({
            "file": path.name,
            "sha256": sha256_of(path),
            "size": path.stat().st_size,
            "added": old.get(path.name, {}).get("added", utcnow()),
            "description": old.get(path.name, {}).get("description", ""),
        })
    save_meta(agent_dir, {"agent": agent, "updated": utcnow(), "tanks": tanks})


def smoke_battle(tank: Path, ref: str, seconds: int, seed: int) -> str:
    """Короткий бой танка против эталона. Возвращает '' или текст ошибки."""
    from battle.runner import BattleRun  # лениво: validate без --smoke лёгкий
    from config import BALANCE, BattleConfig, PlayerConfig
    from engine.program import load_any_program

    if tank.name.endswith(ARCHIVE_SUFFIX):
        tmp = tempfile.TemporaryDirectory(prefix="pool-smoke-")
        try:
            unpack_bundle(tank.read_bytes(), Path(tmp.name) / "pkg")
        except BundleError as exc:
            tmp.cleanup()
            return str(exc)
        source = str(Path(tmp.name) / "pkg" / "main.py")
    else:
        tmp = None
        source = str(tank)
    try:
        meta_tank = load_any_program(source)
        meta_ref = load_program(ref)
        if not meta_tank.ok:
            return "; ".join(meta_tank.errors)
        if not meta_ref.ok:
            return f"эталон {ref}: " + "; ".join(meta_ref.errors)
        cfg = BattleConfig(
            a=PlayerConfig(kind="script", source=ref, name=meta_ref.name,
                           color=meta_ref.color),
            b=PlayerConfig(kind="script", source=source, name=meta_tank.name,
                           color=meta_tank.color),
            map_name="arena", budget_ms=BALANCE.default_budget_ms,
            seed=seed, max_seconds=seconds)
        run = BattleRun(cfg, f"pool-smoke-{tank.stem}", save=False)
        run.run_sync()
        if run.status != "done":
            return run.error or f"бой не досчитался ({run.status})"
        s = run.replay.summary
        return (f"бой {seconds}с: {s['outcome']}, ХП {s['b']['hp_left']}, "
                f"выстрелов {s['b']['shots']}")
    finally:
        if tmp is not None:
            tmp.cleanup()


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.root)
    pool = pool_dir(root)
    errors = validate_pool(pool, args.agent or "", fix=args.fix)
    if args.fix and not errors:
        print(f"{META_NAME} пересобран(ы) по файлам на диске")
    for e in errors:
        print(f"ОШИБКА {e}")
    if errors:
        return 1
    subs = [s for s in scan(pool)
            if not args.agent or s.agent == args.agent]
    print(f"проверено: агентов {len({s.agent for s in subs})}, "
          f"танков {len(subs)} — всё чисто")
    if args.smoke:
        ref = args.with_ref
        if not Path(ref).exists():
            cand = root / "ai" / ref
            cand = cand if cand.exists() else root / "ai" / f"{ref}.tankp.py"
            ref = str(cand)
        if not Path(ref).exists():
            print(f"ОШИБКА эталон «{args.with_ref}» не найден", file=sys.stderr)
            return 1
        failed = 0
        for s in subs:
            if s.kind == "unknown":
                continue
            print(f"бой: {s.agent}/{s.filename} против {Path(ref).name} ...",
                  end=" ", flush=True)
            try:
                result = smoke_battle(s.path, ref, args.seconds, args.seed)
            except Exception as exc:  # noqa: BLE001 — дымовой тест не падает
                result, failed = f"ИСКЛЮЧЕНИЕ {exc}", failed + 1
                print(result)
                continue
            if result.startswith("бой "):
                print("OK —", result)
            else:
                print("ПРОВАЛ —", result)
                failed += 1
        if failed:
            print(f"дымовые бои: провалено {failed} из {len(subs)}",
                  file=sys.stderr)
            return 1
        print(f"дымовые бои: все {len(subs)} прошли")
    return 0


# --- CLI ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pool.py", description="Обменник танковых программ (pool/)")
    p.add_argument("--root", default=str(ROOT),
                   help="корень репозитория (по умолчанию — этот)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("publish", help="положить файл в pool/")
    pp.add_argument("file", help="путь к .tankp.py/.py или .zip")
    pp.add_argument("--agent", required=True, help="id агента (a-z, 0-9, -, _)")
    pp.add_argument("--as", dest="as_name", default=None,
                    help="имя танка без расширения (по умолчанию — из файла)")
    pp.add_argument("--describe", default="",
                    help="описание стратегии для meta.json")
    pp.add_argument("--commit", action="store_true", help="сразу закоммитить")
    pp.add_argument("--message", "-m", default="",
                    help="текст коммита (по умолчанию — стандартный)")
    pp.add_argument("--push", action="store_true",
                    help="закоммитить и отправить ветку в origin")
    pp.add_argument("--pr", action="store_true",
                    help="закоммитить, отправить и создать PR через gh")
    pp.set_defaults(func=cmd_publish)

    pl = sub.add_parser("list", help="показать танки в pool/")
    pl.add_argument("--remote", action="store_true",
                    help="смотреть в GitHub, а не локально")
    pl.add_argument("--ref", default="master",
                    help="ветка/коммит для --remote (по умолчанию master)")
    pl.add_argument("--repo", default="",
                    help="owner/repo (по умолчанию — из git remote)")
    pl.add_argument("--json", action="store_true", help="вывод в JSON")
    pl.set_defaults(func=cmd_list)

    pu = sub.add_parser("pull", help="забрать все файлы pool/ из GitHub")
    pu.add_argument("--via", choices=("git", "api"), default="git",
                    help="git: git pull; api: скачать tar.gz с codeload")
    pu.add_argument("--ref", default="",
                    help="git: взять pool/ из этой ветки без переключения; "
                         "api: ветка/коммит (по умолчанию — главная ветка)")
    pu.add_argument("--repo", default="",
                    help="owner/repo (по умолчанию — из git remote)")
    pu.add_argument("--out", default="",
                    help="только для --via api: куда распаковать "
                         "(по умолчанию — pool/ репозитория)")
    pu.set_defaults(func=cmd_pull)

    pv = sub.add_parser("validate", help="проверить весь pool/")
    pv.add_argument("--agent", default="",
                    help="проверять только этого агента")
    pv.add_argument("--fix", action="store_true",
                    help="пересобрать meta.json по файлам на диске")
    pv.add_argument("--smoke", action="store_true",
                    help="прогнать каждый танк в коротком бою против эталона")
    pv.add_argument("--with", dest="with_ref", default="01_chaser",
                    help="эталон для --smoke (по умолчанию 01_chaser)")
    pv.add_argument("--seconds", type=int, default=10,
                    help="длительность дымового боя (по умолчанию 10)")
    pv.add_argument("--seed", type=int, default=7,
                    help="сид дымового боя (по умолчанию 7)")
    pv.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
