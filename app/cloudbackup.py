"""把数据定期推到 GitHub 私有仓库。

部署到云上之后，应用会自己备份自己，不依赖任何一台笔记本。
需要两个环境变量，没有就静默不启用：

    BACKUP_GITHUB_TOKEN   有 repo 权限的 GitHub Token
    BACKUP_GITHUB_REPO    形如 用户名/仓库名（请用私有仓库）

可选：
    BACKUP_INTERVAL_HOURS 间隔小时数，默认 12
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sqlite3
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import db
from .globe_ctx import is_demo

log = logging.getLogger("cloudbackup")

API = os.environ.get("GITHUB_API_BASE", "https://api.github.com").rstrip("/")
# GitHub contents 接口对单个文件有硬限制，超大文件直接跳过而不是让整次备份失败
MAX_FILE_BYTES = 40 * 1024 * 1024


def _config() -> tuple[str, str, float] | None:
    # 演示星球绝不能写进私人备份仓库
    if is_demo():
        return None
    token = os.environ.get("BACKUP_GITHUB_TOKEN", "").strip()
    repo = os.environ.get("BACKUP_GITHUB_REPO", "").strip()
    if not token or not repo:
        return None
    try:
        hours = float(os.environ.get("BACKUP_INTERVAL_HOURS", "1"))
    except ValueError:
        hours = 1.0
    return token, repo, max(hours, 0.25)


def _request(method: str, url: str, token: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "GlobeMemories-Backup")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def _remote_sha(repo: str, path: str, token: str) -> str | None:
    try:
        data = _request("GET", f"{API}/repos/{repo}/contents/{path}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return data.get("sha") if isinstance(data, dict) else None


def _remote_listing(repo: str, path: str, token: str) -> set[str]:
    try:
        data = _request("GET", f"{API}/repos/{repo}/contents/{path}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()
        raise
    return {item["name"] for item in data} if isinstance(data, list) else set()


def _upload(repo: str, path: str, content: bytes, token: str, message: str) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode(),
    }
    sha = _remote_sha(repo, path, token)
    if sha:
        payload["sha"] = sha
    _request("PUT", f"{API}/repos/{repo}/contents/{path}", token, payload)


def configured() -> bool:
    return _config() is not None


def _db_stats(path: Path | None) -> dict[str, int]:
    empty = {"users": 0, "places": 0, "photos": 0, "messages": 0}
    if path is None or not Path(path).exists():
        return empty
    try:
        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT"
            " (SELECT COUNT(*) FROM users),"
            " (SELECT COUNT(*) FROM places),"
            " (SELECT COUNT(*) FROM photos),"
            " (SELECT COUNT(*) FROM messages)"
        ).fetchone()
        conn.close()
        return {"users": row[0], "places": row[1], "photos": row[2], "messages": row[3]}
    except sqlite3.Error:
        return empty


def local_stats() -> dict[str, int]:
    return _db_stats(db.db_path() if db.db_path().exists() else None)


def _richer(a: dict[str, int], b: dict[str, int]) -> bool:
    """a 是否明显比 b 更完整，用来挡住用瘦数据覆盖肥数据。"""
    return (
        a["users"] > b["users"]
        or a["places"] > b["places"]
        or a["photos"] > b["photos"]
        or a["messages"] > b["messages"]
    )


def _remote_stats(repo: str, token: str) -> dict[str, int] | None:
    raw = _download(repo, "app.db", token)
    if raw is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        return _db_stats(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _local_is_empty() -> bool:
    """本地是不是一份还没有任何人的空数据。"""
    return local_stats()["users"] == 0


def _snapshot_db() -> bytes:
    """SQLite 在线备份，服务正在写也能拿到一致的副本。"""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "app.db"
        # 数据库开着 WAL，只读连接看不到还没合并进主库的最新写入，
        # 那样备份出来会是一份旧的甚至空的数据。
        source = sqlite3.connect(db.db_path())
        target = sqlite3.connect(out)
        with target:
            source.backup(target)
        target.close()
        source.close()
        return out.read_bytes()


def run_once() -> str:
    """执行一次备份，返回一句人话结果。"""
    config = _config()
    if config is None:
        return "没有配置备份仓库，跳过"
    token, repo, _ = config

    # 最重要的一道保险：如果本地是空的而云上有数据，说明这次启动没能把数据接回来
    # （比如恢复时网络出问题）。这种时候绝对不能用空库覆盖掉云端的备份。
    if _local_is_empty():
        if _remote_sha(repo, "app.db", token):
            raise RuntimeError("本地数据是空的，而云端有备份，已阻止覆盖")
        return "本地还没有任何数据，这次不备份"

    remote = _remote_stats(repo, token)
    local = local_stats()
    if remote and _richer(remote, local):
        raise RuntimeError(
            f"云端备份更完整（账号 {remote['users']} / 地点 {remote['places']}），"
            f"本地只有 {local['users']} / {local['places']}，已阻止覆盖"
        )

    if os.environ.get("BACKUP_DEBUG"):
        probe = sqlite3.connect(db.db_path())
        log.warning(
            "备份诊断 path=%s exists=%s users=%s size=%s",
            db.db_path(), db.db_path().exists(),
            probe.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            db.db_path().stat().st_size if db.db_path().exists() else -1,
        )
        probe.close()

    stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    _upload(repo, "app.db", _snapshot_db(), token, f"数据库备份 {stamp}")

    uploaded = 0
    skipped = 0
    if db.upload_dir().is_dir():
        existing = _remote_listing(repo, "photos", token)
        for photo in sorted(db.upload_dir().glob("*")):
            if not photo.is_file() or photo.name in existing:
                continue
            if photo.stat().st_size > MAX_FILE_BYTES:
                skipped += 1
                continue
            _upload(repo, f"photos/{photo.name}", photo.read_bytes(), token, f"照片 {photo.name}")
            uploaded += 1

    secret = db.db_path().parent / ".secret_key"
    if secret.is_file():
        _upload(repo, "secret_key", secret.read_bytes(), token, "登录密钥")

    result = f"已备份到 {repo}：数据库 1 份，新照片 {uploaded} 张"
    return result + (f"，跳过超大文件 {skipped} 个" if skipped else "")


def _download(repo: str, path: str, token: str) -> bytes | None:
    try:
        data = _request("GET", f"{API}/repos/{repo}/contents/{path}", token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(data, dict):
        return None
    content = data.get("content")
    if content:
        return base64.b64decode(content)
    # GitHub 的 contents 接口对大于 1MB 的文件不返回 content，要走 download_url
    url = data.get("download_url")
    if not url:
        return None
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "GlobeMemories-Backup",
        "Accept": "application/vnd.github.raw",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def assert_repo_ready() -> None:
    """启动时确认备份仓库真的能访问，避免下次部署才发现钥匙是假的。"""
    config = _config()
    if config is None:
        return
    token, repo, _ = config
    try:
        _request("GET", f"{API}/repos/{repo}", token)
    except Exception as exc:
        raise RuntimeError(f"访问备份仓库 {repo} 失败，请检查 BACKUP_GITHUB_TOKEN：{exc}") from exc


def _delete(repo: str, path: str, token: str) -> None:
    sha = _remote_sha(repo, path, token)
    if not sha:
        return
    _request(
        "DELETE",
        f"{API}/repos/{repo}/contents/{path}",
        token,
        {"message": "用掉一次性密码重置", "sha": sha},
    )


def apply_pending_password_reset() -> str | None:
    """备份仓库里如果有 reset-password（格式 用户名:新密码），启动时改一次然后删掉文件。"""
    config = _config()
    if config is None or not db.db_path().exists():
        return None
    token, repo, _ = config
    raw = _download(repo, "reset-password", token)
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    username, sep, password = text.partition(":")
    username = username.strip().lower()
    password = password.strip()
    if not sep or not username or len(password) < 4:
        return "reset-password 文件格式应为 用户名:新密码"

    from . import auth

    conn = sqlite3.connect(db.db_path())
    try:
        row = conn.execute(
            "SELECT id, username FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return f"reset-password 找不到账号 {username}"
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth.hash_password(password), row[0]),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        _delete(repo, "reset-password", token)
    except Exception as exc:
        log.warning("密码已改，但一次性文件没删掉：%s", exc)
    return f"已重置账号 {username} 的密码"


def apply_coord_fixes() -> str | None:
    """备份仓库里的 coord-fixes.json 按地点 id 改坐标，不依赖 Nominatim。"""
    config = _config()
    if config is None or not db.db_path().exists():
        return None
    token, repo, _ = config
    raw = _download(repo, "coord-fixes.json", token)
    if not raw:
        return None
    try:
        fixes = json.loads(raw.decode())
    except json.JSONDecodeError:
        return "coord-fixes.json 无法解析"
    if not isinstance(fixes, list):
        return None
    conn = sqlite3.connect(db.db_path())
    changed = 0
    try:
        for item in fixes:
            try:
                pid = int(item["id"])
                lat = float(item["lat"])
                lng = float(item["lng"])
            except (KeyError, TypeError, ValueError):
                continue
            row = conn.execute("SELECT lat, lng FROM places WHERE id = ?", (pid,)).fetchone()
            if row is None:
                continue
            if abs(row[0] - lat) < 1e-5 and abs(row[1] - lng) < 1e-5:
                continue
            conn.execute("UPDATE places SET lat = ?, lng = ? WHERE id = ?", (lat, lng, pid))
            changed += 1
        if changed:
            conn.commit()
            return f"已按 coord-fixes.json 校正 {changed} 个地点"
        return None
    finally:
        conn.close()


def restore_if_empty() -> str | None:
    """数据是空的时候，从 GitHub 备份里把数据拉回来。

    用于从笔记本迁到云上：本地推一次备份，云端第一次启动就自动接上，
    账号、地点、照片和登录状态都不用重新来过。
    """
    config = _config()
    if config is None:
        return None
    if not _local_is_empty():
        return None
    token, repo, _ = config
    assert_repo_ready()

    db_bytes = _download(repo, "app.db", token)
    if db_bytes is None:
        raise RuntimeError(f"已配置 {repo}，但仓库里没有数据库，拒绝空库启动")

    db.db_path().parent.mkdir(parents=True, exist_ok=True)
    db.upload_dir().mkdir(parents=True, exist_ok=True)
    for extra in (Path(f"{db.db_path()}-wal"), Path(f"{db.db_path()}-shm")):
        extra.unlink(missing_ok=True)
    db.db_path().write_bytes(db_bytes)

    secret = _download(repo, "secret_key", token)
    if secret:
        (db.db_path().parent / ".secret_key").write_bytes(secret)

    restored = 0
    for name in _remote_listing(repo, "photos", token):
        content = _download(repo, f"photos/{name}", token)
        if content:
            (db.upload_dir() / name).write_bytes(content)
            restored += 1

    return f"已从 {repo} 恢复数据：照片 {restored} 张"


async def _loop(interval_hours: float) -> None:
    # 先等一个间隔再备份。刚启动时数据要么是刚恢复回来的、要么还是空的，
    # 两种情况都没有必要立刻推一次。
    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            log.info(await asyncio.to_thread(run_once))
        except Exception as exc:
            log.warning("备份失败，下次再试：%s", exc)


_pending: asyncio.Task | None = None


def _debounce_seconds() -> float:
    try:
        return max(float(os.environ.get("BACKUP_DEBOUNCE_SECONDS", "20")), 5.0)
    except ValueError:
        return 20.0


async def _delayed_backup() -> None:
    await asyncio.sleep(_debounce_seconds())
    try:
        log.info(await asyncio.to_thread(run_once))
    except Exception as exc:
        log.warning("备份失败，等下一次改动或定时任务再试：%s", exc)


def schedule_soon() -> None:
    """数据有变化时调用。短时间内的多次改动会合并成一次备份。

    免费平台大多没有持久磁盘，重启就清空，所以不能只靠每隔几小时备份一次。
    """
    global _pending
    if _config() is None:
        return
    if _pending is not None and not _pending.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _pending = loop.create_task(_delayed_backup())


def start(app_state) -> asyncio.Task | None:
    """在应用启动时调用；没配置就返回 None。"""
    config = _config()
    if config is None:
        return None
    _, repo, hours = config
    log.info("已开启自动备份：每 %s 小时推送到 %s，有改动约 %s 秒后也会备份", hours, repo, _debounce_seconds())
    return asyncio.create_task(_loop(hours))


async def flush() -> None:
    """进程退出前立刻推一次，免费平台休眠时经常直接杀进程。"""
    global _pending
    if _config() is None:
        return
    if _pending is not None and not _pending.done():
        _pending.cancel()
        _pending = None
    try:
        log.info(await asyncio.to_thread(run_once))
    except Exception as exc:
        log.warning("退出前备份失败：%s", exc)
