"""Globe Memories —— 一颗可以一起写故事的地球。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, cloudbackup, db, demoseed
from .globe_ctx import app_path, is_demo, reset_demo, set_demo, url_prefix
from .media import ImageError, delete_image, save_image

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

MEMBER_COLORS = ["#ffb703", "#4cc9f0", "#f72585", "#80ed99", "#c77dff"]


def recover_passwords() -> list[str]:
    """用环境变量重置密码。登录成功后务必把这些变量删掉。"""
    notes: list[str] = []
    owner_pw = os.environ.get("RESET_OWNER_PASSWORD", "").strip()
    user_pw = os.environ.get("RESET_USER_PASSWORD", "").strip()
    if not owner_pw and not user_pw:
        return notes

    conn = db.connect()
    try:
        if owner_pw:
            if len(owner_pw) < 4:
                notes.append("RESET_OWNER_PASSWORD 太短，没有改密码")
            else:
                row = conn.execute("SELECT id, username FROM users ORDER BY id LIMIT 1").fetchone()
                if row is None:
                    notes.append("还没有账号，无法重置密码")
                else:
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (auth.hash_password(owner_pw), row["id"]),
                    )
                    notes.append(f"已把账号 {row['username']} 的密码重置为环境变量里的值")
        if user_pw:
            username, sep, password = user_pw.partition(":")
            username = username.strip().lower()
            password = password.strip()
            if not sep or not username or len(password) < 4:
                notes.append("RESET_USER_PASSWORD 格式应为 用户名:新密码")
            else:
                row = conn.execute("SELECT id, username FROM users WHERE username = ?", (username,)).fetchone()
                if row is None:
                    notes.append(f"找不到账号 {username}，没有改密码")
                else:
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (auth.hash_password(password), row["id"]),
                    )
                    notes.append(f"已把账号 {row['username']} 的密码重置为环境变量里的值")
        conn.commit()
    finally:
        conn.close()
    return notes


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = logging.getLogger("uvicorn.error")
    on_render = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
    if on_render and not cloudbackup.configured():
        log.error(
            "Render 上没有配置 BACKUP_GITHUB_TOKEN / BACKUP_GITHUB_REPO。"
            "免费实例没有硬盘，每次部署都会丢掉账号。"
        )

    # 空数据 + 配了备份仓库时，先把云上的数据接回来，再建表。
    # 配了仓库却拉不回来时必须停掉，绝不能空库启动后再把空数据写回去。
    try:
        restored = await asyncio.to_thread(cloudbackup.restore_if_empty)
        if restored:
            log.info(restored)
    except Exception as exc:
        if cloudbackup.configured():
            log.error("备份仓库已配置但恢复失败，拒绝空库启动：%s", exc)
            raise
        log.warning("从备份恢复失败，将以空数据启动：%s", exc)

    db.init()
    auth.reload_secret()
    try:
        log.info(demoseed.reset_demo_globe())
    except Exception as exc:
        log.warning("演示星球生成失败（不影响私人星球）：%s", exc)
    repaired = None
    try:
        for recovered in recover_passwords():
            log.warning(recovered)
        pending = await asyncio.to_thread(cloudbackup.apply_pending_password_reset)
        if pending:
            log.warning(pending)
        coords = await asyncio.to_thread(cloudbackup.apply_coord_fixes)
        if coords:
            log.warning(coords)
            repaired = coords
        nom = await asyncio.to_thread(repair_misplaced_places)
        if nom:
            log.warning(nom)
            repaired = nom
    except Exception as exc:
        log.warning("启动时校正数据失败：%s", exc)

    backup_task = cloudbackup.start(app.state)
    if repaired:
        cloudbackup.schedule_soon()
    yield
    if backup_task is not None:
        backup_task.cancel()
    await cloudbackup.flush()


app = FastAPI(title="Globe Memories", lifespan=lifespan)

try:
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
except ImportError:
    pass

# 只读的、以及本身就很频繁的请求不值得触发备份
_NO_BACKUP_PATHS = {"/api/messages/read", "/api/auth/login", "/api/auth/logout", "/api/backup/now"}


@app.middleware("http")
async def backup_after_changes(request: Request, call_next):
    response = await call_next(request)
    if (
        request.method in {"POST", "PATCH", "PUT", "DELETE"}
        and response.status_code < 400
        and request.url.path.startswith("/api/")
        and request.url.path not in _NO_BACKUP_PATHS
    ):
        cloudbackup.schedule_soon()
    return response


# --------------------------------------------------------------------------
# 依赖与工具
# --------------------------------------------------------------------------

def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Row:
    token = request.cookies.get(auth.cookie_name())
    uid = auth.read_token(token) if token else None
    if uid is None:
        raise HTTPException(401, "请先登录")
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(401, "账号不存在")
    return row


def user_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "color": row["color"],
    }


def _is_https(request: Request) -> bool:
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        return True
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _session_cookie_kwargs(request: Request) -> dict[str, Any]:
    return {
        "path": "/demo" if is_demo() else "/",
        "httponly": True,
        "samesite": "lax",
        "secure": _is_https(request),
    }


def set_session_cookie(response: Response, request: Request, user_id: int) -> None:
    response.set_cookie(
        auth.cookie_name(),
        auth.make_token(user_id),
        max_age=auth.SESSION_MAX_AGE,
        **_session_cookie_kwargs(request),
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    # 删除 Cookie 必须带上和写入时相同的 Path / Secure / HttpOnly，
    # 否则浏览器会留下一份空的 gm_session，再登录就会被挡在门外。
    kwargs = _session_cookie_kwargs(request)
    response.delete_cookie(auth.cookie_name(), **kwargs)


def redirect_with_cookie(request: Request, url: str, user_id: int | None = None, clear: bool = False) -> RedirectResponse:
    redirect = RedirectResponse(app_path(url), status_code=303)
    if clear:
        clear_session_cookie(redirect, request)
    elif user_id is not None:
        set_session_cookie(redirect, request, user_id)
    return redirect


def login_error(tab: str, message: str) -> RedirectResponse:
    qs = urllib.parse.urlencode({"tab": tab, "err": message})
    return RedirectResponse(app_path(f"/login?{qs}"), status_code=303)


# --------------------------------------------------------------------------
# 认证
# --------------------------------------------------------------------------

@app.get("/api/auth/status")
def auth_status(conn: sqlite3.Connection = Depends(get_conn)):
    count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    owner = conn.execute(
        "SELECT username, display_name FROM users ORDER BY id LIMIT 1"
    ).fetchone()
    return {
        "has_owner": count > 0,
        "member_count": count,
        "owner_username": owner["username"] if owner else None,
        "owner_name": owner["display_name"] if owner else None,
        "backup_configured": cloudbackup.configured(),
        "demo": is_demo(),
        "demo_login": {"username": demoseed.DEMO_USER, "password": demoseed.DEMO_PASSWORD}
        if is_demo()
        else None,
    }


@app.post("/api/auth/register")
def register(
    request: Request,
    response: Response,
    username: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(""),
    conn: sqlite3.Connection = Depends(get_conn),
):
    username = username.strip().lower()
    display_name = display_name.strip()
    if is_demo():
        return login_error("login", "演示星球请用账号 demo，密码 demo2026")
    if len(username) < 2 or len(username) > 32:
        return login_error("register", "用户名需 2-32 个字符")
    if not display_name:
        return login_error("register", "请填写昵称")
    if len(password) < 4:
        return login_error("register", "密码至少 4 位")

    count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    invite = None
    if count > 0:
        code = invite_code.strip().upper()
        invite = conn.execute(
            "SELECT * FROM invites WHERE code = ? AND used_by IS NULL", (code,)
        ).fetchone()
        if invite is None:
            return login_error("register", "邀请码无效或已被使用")

    color = MEMBER_COLORS[count % len(MEMBER_COLORS)]
    try:
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, color) VALUES (?,?,?,?)",
            (username, display_name, auth.hash_password(password), color),
        )
    except sqlite3.IntegrityError:
        return login_error("register", "这个用户名已经被占用了")

    uid = cur.lastrowid
    if invite is not None:
        conn.execute(
            "UPDATE invites SET used_by = ?, used_at = datetime('now') WHERE code = ?",
            (uid, invite["code"]),
        )
    conn.execute("INSERT OR IGNORE INTO reads (user_id, last_read_id) VALUES (?, 0)", (uid,))
    conn.commit()
    return redirect_with_cookie(request, "/", uid)


@app.post("/api/auth/login")
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    key = username.strip()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (key.lower(),)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM users WHERE display_name = ? COLLATE NOCASE", (key,)
        ).fetchone()
    if row is None or not auth.verify_password(password, row["password_hash"]):
        return login_error("login", "用户名或密码不对")
    return redirect_with_cookie(request, "/", row["id"])


@app.post("/api/auth/logout")
def logout(request: Request):
    return redirect_with_cookie(request, "/login", clear=True)


@app.get("/api/auth/me")
def me(user: sqlite3.Row = Depends(current_user)):
    return user_public(user)


@app.get("/api/config")
def config(user: sqlite3.Row = Depends(current_user)):
    """share.sh 起隧道时会把公网地址写进这个文件，邀请码就能配上对的地址。"""
    path = db.DATA_DIR / "public_url.txt"
    url = path.read_text().strip() if path.is_file() else ""
    return {"public_url": url or None, "demo": is_demo()}


@app.post("/api/backup/now")
async def backup_now(user: sqlite3.Row = Depends(current_user)):
    try:
        message = await asyncio.to_thread(cloudbackup.run_once)
    except Exception as exc:
        raise HTTPException(502, f"备份失败：{exc}")
    return {"message": message}


@app.get("/api/members")
def members(
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [user_public(r) for r in rows]


# --------------------------------------------------------------------------
# 邀请码（把这颗地球送给另一个人）
# --------------------------------------------------------------------------

@app.get("/api/invites")
def list_invites(
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    rows = conn.execute(
        """SELECT i.code, i.created_at, i.used_at, u.display_name AS used_by_name
           FROM invites i LEFT JOIN users u ON u.id = i.used_by
           ORDER BY i.created_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/invites")
def create_invite(
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    code = auth.new_invite_code()
    conn.execute("INSERT INTO invites (code, created_by) VALUES (?,?)", (code, user["id"]))
    conn.commit()
    return {"code": code}


# --------------------------------------------------------------------------
# 地点
# --------------------------------------------------------------------------

def place_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    photos = conn.execute(
        "SELECT id, filename, thumb, caption FROM photos WHERE place_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "lat": row["lat"],
        "lng": row["lng"],
        "date": row["place_date"],
        "location_name": row["location_name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "author": {
            "id": row["user_id"],
            "display_name": row["display_name"],
            "color": row["color"],
        },
        "photos": [
            {
                "id": p["id"],
                "url": f"{url_prefix()}/media/{p['filename']}",
                "thumb": f"{url_prefix()}/media/{p['thumb']}",
                "caption": p["caption"],
            }
            for p in photos
        ],
    }


@app.get("/api/places")
def list_places(
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    rows = conn.execute(
        """SELECT p.*, u.display_name, u.color
           FROM places p JOIN users u ON u.id = p.user_id
           ORDER BY COALESCE(p.place_date, p.created_at)"""
    ).fetchall()
    return [place_payload(conn, r) for r in rows]


@app.post("/api/places")
async def create_place(
    title: str = Form(...),
    lat: float = Form(...),
    lng: float = Form(...),
    place_date: str = Form(""),
    location_name: str = Form(""),
    description: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    title = title.strip()
    if not title:
        raise HTTPException(400, "给这个地方起个名字吧")
    location_name = location_name.strip()
    lat, lng = snap_coords_to_place_name(lat, lng, location_name)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(400, "经纬度超出范围")

    cur = conn.execute(
        """INSERT INTO places (user_id, title, lat, lng, place_date, location_name, description)
           VALUES (?,?,?,?,?,?,?)""",
        (
            user["id"],
            title,
            lat,
            lng,
            place_date.strip() or None,
            location_name.strip() or None,
            description.strip() or None,
        ),
    )
    place_id = cur.lastrowid

    saved: list[str] = []
    for upload in photos:
        raw = await upload.read()
        if not raw:
            continue
        try:
            full, thumb = save_image(raw, upload.content_type)
        except ImageError as exc:
            conn.rollback()
            delete_image(*saved)
            raise HTTPException(400, str(exc))
        saved += [full, thumb]
        conn.execute(
            "INSERT INTO photos (place_id, filename, thumb) VALUES (?,?,?)",
            (place_id, full, thumb),
        )
    conn.commit()

    row = conn.execute(
        """SELECT p.*, u.display_name, u.color FROM places p
           JOIN users u ON u.id = p.user_id WHERE p.id = ?""",
        (place_id,),
    ).fetchone()
    return place_payload(conn, row)


@app.post("/api/places/{place_id}/photos")
async def add_photos(
    place_id: int,
    photos: list[UploadFile] = File(default=[]),
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "地点不存在")

    for upload in photos:
        raw = await upload.read()
        if not raw:
            continue
        try:
            full, thumb = save_image(raw, upload.content_type)
        except ImageError as exc:
            raise HTTPException(400, str(exc))
        conn.execute(
            "INSERT INTO photos (place_id, filename, thumb) VALUES (?,?,?)",
            (place_id, full, thumb),
        )
    conn.commit()

    row = conn.execute(
        """SELECT p.*, u.display_name, u.color FROM places p
           JOIN users u ON u.id = p.user_id WHERE p.id = ?""",
        (place_id,),
    ).fetchone()
    return place_payload(conn, row)


@app.patch("/api/places/{place_id}")
def update_place(
    place_id: int,
    payload: dict[str, Any],
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "地点不存在")
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "只能修改自己添加的地点")

    fields = {
        "title": "title",
        "date": "place_date",
        "location_name": "location_name",
        "description": "description",
    }
    updates = {col: (payload[key] or None) for key, col in fields.items() if key in payload}
    if updates:
        sets = ", ".join(f"{col} = ?" for col in updates)
        conn.execute(f"UPDATE places SET {sets} WHERE id = ?", (*updates.values(), place_id))
        conn.commit()

    row = conn.execute(
        """SELECT p.*, u.display_name, u.color FROM places p
           JOIN users u ON u.id = p.user_id WHERE p.id = ?""",
        (place_id,),
    ).fetchone()
    return place_payload(conn, row)


@app.delete("/api/places/{place_id}")
def delete_place(
    place_id: int,
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "地点不存在")
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "只能删除自己添加的地点")

    files = conn.execute(
        "SELECT filename, thumb FROM photos WHERE place_id = ?", (place_id,)
    ).fetchall()
    conn.execute("DELETE FROM photos WHERE place_id = ?", (place_id,))
    conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
    conn.commit()
    for f in files:
        delete_image(f["filename"], f["thumb"])
    return {"ok": True}


@app.delete("/api/photos/{photo_id}")
def delete_photo(
    photo_id: int,
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    row = conn.execute(
        """SELECT ph.*, p.user_id FROM photos ph
           JOIN places p ON p.id = ph.place_id WHERE ph.id = ?""",
        (photo_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "图片不存在")
    if row["user_id"] != user["id"]:
        raise HTTPException(403, "只能删除自己添加的图片")
    conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    conn.commit()
    delete_image(row["filename"], row["thumb"])
    return {"ok": True}


# --------------------------------------------------------------------------
# 留言
# --------------------------------------------------------------------------

@app.get("/api/messages")
def list_messages(
    after: int = 0,
    limit: int = 200,
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    rows = conn.execute(
        """SELECT m.*, u.display_name, u.color FROM messages m
           JOIN users u ON u.id = m.user_id
           WHERE m.id > ? ORDER BY m.id LIMIT ?""",
        (after, max(1, min(limit, 500))),
    ).fetchall()
    last_read = conn.execute(
        "SELECT last_read_id FROM reads WHERE user_id = ?", (user["id"],)
    ).fetchone()
    last_read_id = last_read["last_read_id"] if last_read else 0
    unread = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE id > ? AND user_id != ?",
        (last_read_id, user["id"]),
    ).fetchone()["n"]

    return {
        "messages": [
            {
                "id": r["id"],
                "body": r["body"],
                "place_id": r["place_id"],
                "created_at": r["created_at"],
                "author": {
                    "id": r["user_id"],
                    "display_name": r["display_name"],
                    "color": r["color"],
                },
                "mine": r["user_id"] == user["id"],
            }
            for r in rows
        ],
        "unread": unread,
    }


@app.post("/api/messages")
def create_message(
    payload: dict[str, Any],
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    body = str(payload.get("body", "")).strip()
    if not body:
        raise HTTPException(400, "说点什么吧")
    if len(body) > 2000:
        raise HTTPException(400, "留言太长了")
    place_id = payload.get("place_id")
    if isinstance(place_id, int):
        exists = conn.execute("SELECT 1 FROM places WHERE id = ?", (place_id,)).fetchone()
        if exists is None:
            place_id = None  # 关联的地点可能已被删除，留言本身仍然保留
    else:
        place_id = None

    cur = conn.execute(
        "INSERT INTO messages (user_id, body, place_id) VALUES (?,?,?)",
        (user["id"], body, place_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {
        "id": row["id"],
        "body": row["body"],
        "place_id": row["place_id"],
        "created_at": row["created_at"],
        "author": user_public(user),
        "mine": True,
    }


@app.post("/api/messages/read")
def mark_read(
    payload: dict[str, Any],
    user: sqlite3.Row = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
):
    last_id = int(payload.get("last_id", 0))
    conn.execute(
        """INSERT INTO reads (user_id, last_read_id) VALUES (?,?)
           ON CONFLICT(user_id) DO UPDATE SET last_read_id = MAX(last_read_id, excluded.last_read_id)""",
        (user["id"], last_id),
    )
    conn.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# 地名搜索（代理 OpenStreetMap Nominatim，无网络时静默降级）
# --------------------------------------------------------------------------

_UA = "GlobeMemories/1.0 (personal use)"

_KIND_ZH = {
    "country": "国家",
    "state": "省/州",
    "province": "省",
    "city": "市",
    "town": "城镇",
    "village": "村庄",
    "hamlet": "村落",
    "suburb": "片区",
    "neighbourhood": "街区",
    "quarter": "街区",
    "city_district": "区",
    "district": "区",
    "county": "县",
    "municipality": "市",
    "residential": "住宅区",
    "road": "道路",
    "pedestrian": "步行街",
    "building": "建筑",
    "house": "门牌",
    "attraction": "景点",
    "museum": "博物馆",
    "park": "公园",
    "zoo": "动物园",
    "theme_park": "乐园",
    "artwork": "公共艺术",
    "viewpoint": "观景处",
    "hotel": "酒店",
    "guest_house": "民宿",
    "restaurant": "餐厅",
    "cafe": "咖啡馆",
    "fast_food": "快餐",
    "bar": "酒吧",
    "pub": "酒吧",
    "bakery": "面包店",
    "supermarket": "超市",
    "mall": "商场",
    "marketplace": "市场",
    "university": "大学",
    "college": "学院",
    "school": "学校",
    "hospital": "医院",
    "clinic": "诊所",
    "station": "车站",
    "subway": "地铁",
    "aerodrome": "机场",
    "airport": "机场",
    "bus_stop": "公交站",
    "place_of_worship": "宗教场所",
    "temple": "寺庙",
    "church": "教堂",
    "theatre": "剧院",
    "cinema": "影院",
    "stadium": "体育场",
    "library": "图书馆",
    "gallery": "美术馆",
    "beach": "海滩",
    "island": "岛屿",
}


_last_nominatim = 0.0


def _nominatim(path: str, params: dict[str, str]) -> Any:
    global _last_nominatim
    wait = 1.05 - (time.monotonic() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    url = f"https://nominatim.openstreetmap.org/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=8) as resp:
        _last_nominatim = time.monotonic()
        return json.loads(resp.read().decode())


def _looks_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _search_nominatim(extra: dict[str, str]) -> list[dict[str, Any]]:
    params = {
        "format": "json",
        "addressdetails": "1",
        "namedetails": "1",
        "limit": "12",
        "dedupe": "1",
        "accept-language": "zh-CN,zh,en",
        **extra,
    }
    try:
        data = _nominatim("search", params)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _geocode_query(q: str) -> list[dict[str, Any]]:
    q = q.strip()
    if not q:
        return []
    raw: list[dict[str, Any]] = []
    raw.extend(_search_nominatim({"q": q}))
    if _looks_cjk(q):
        raw.extend(_search_nominatim({"city": q, "country": "中国"}))
        raw.extend(_search_nominatim({"county": q, "country": "中国"}))
        if "中国" not in q:
            raw.extend(_search_nominatim({"q": f"{q} 中国"}))

    results = [_format_place(d) for d in raw]
    seen: set[tuple[float, float]] = set()
    unique: list[dict[str, Any]] = []
    for r in results:
        key = (round(r["lat"], 5), round(r["lng"], 5))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    def rank(r: dict[str, Any]) -> tuple:
        title = (r.get("title") or "").strip()
        addr = r.get("address") or {}
        cc = (addr.get("country_code") or "").lower()
        exact = 0 if title == q or title.rstrip("市区县镇乡") == q.rstrip("市区县镇乡") else 1
        cn = 0 if cc == "cn" else 1
        kind = r.get("osm_type") or ""
        admin = 0 if kind in {"administrative", "city", "town", "suburb", "county", "district", "municipality"} else 1
        return (exact, cn, admin)

    unique.sort(key=rank)
    return unique


def snap_coords_to_place_name(lat: float, lng: float, location_name: str) -> tuple[float, float]:
    """地点名和坐标对不上时，以搜索到的地点为准，避免气泡飞到别的国家。"""
    name = (location_name or "").strip()
    if not name:
        return lat, lng

    hits: list[dict[str, Any]] = []
    if _looks_cjk(name):
        raw: list[dict[str, Any]] = []
        raw.extend(_search_nominatim({"city": name, "country": "中国"}))
        raw.extend(_search_nominatim({"county": name, "country": "中国"}))
        hits = [_format_place(d) for d in raw]
        hits = [h for h in hits if (h.get("title") or "").strip() == name]
    if not hits:
        hits = _geocode_query(name)
    if not hits:
        return lat, lng
    if any(_km(lat, lng, h["lat"], h["lng"]) <= 80 for h in hits):
        return lat, lng
    best = hits[0]
    return best["lat"], best["lng"]


def repair_misplaced_places() -> str | None:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, location_name, lat, lng FROM places"
        ).fetchall()
        fixed = 0
        for row in rows:
            name = (row["location_name"] or "").strip()
            if not name or not _looks_cjk(name):
                continue
            new_lat, new_lng = snap_coords_to_place_name(row["lat"], row["lng"], name)
            if _km(row["lat"], row["lng"], new_lat, new_lng) < 80:
                continue
            conn.execute(
                "UPDATE places SET lat = ?, lng = ? WHERE id = ?",
                (new_lat, new_lng, row["id"]),
            )
            fixed += 1
        if fixed:
            conn.commit()
            return f"已把 {fixed} 个地点校正回搜索到的位置"
        return None
    finally:
        conn.close()


def _addr_parts(addr: dict[str, Any] | None) -> list[str]:
    if not addr:
        return []
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("municipality")
        or addr.get("city_district")
    )
    parts = [
        addr.get("country"),
        addr.get("state") or addr.get("province") or addr.get("region"),
        city,
        addr.get("county") if addr.get("county") not in {city, addr.get("state")} else None,
        addr.get("suburb") or addr.get("district") or addr.get("city_district") if addr.get("city_district") != city else None,
        addr.get("neighbourhood") or addr.get("quarter"),
        addr.get("road"),
        addr.get("house_number"),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in seen:
            continue
        seen.add(part)
        out.append(part)
    return out


def _format_place(item: dict[str, Any]) -> dict[str, Any]:
    addr = item.get("address") or {}
    named = item.get("namedetails") or {}
    title = (
        named.get("name:zh")
        or named.get("name:zh-Hans")
        or named.get("name")
        or item.get("name")
        or addr.get("attraction")
        or addr.get("tourism")
        or addr.get("amenity")
        or addr.get("building")
        or addr.get("road")
        or addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("city")
        or addr.get("town")
        or (item.get("display_name") or "").split(",")[0]
    )
    kind = _KIND_ZH.get(item.get("type") or "", "") or _KIND_ZH.get(item.get("class") or "", "")
    hierarchy = _addr_parts(addr)
    # 标题如果已经是层次里的某一级，层次里就不再重复
    path = [p for p in hierarchy if p != title]
    short = " · ".join(([title] + path)[:6]) if title else " · ".join(path)
    return {
        "name": short or item.get("display_name"),
        "title": title,
        "path": " · ".join(path),
        "kind": kind,
        "lat": float(item["lat"]),
        "lng": float(item["lon"]),
        "osm_type": item.get("type"),
        "address": addr,
    }


@app.get("/api/geocode")
def geocode(q: str, user: sqlite3.Row = Depends(current_user)):
    try:
        return _geocode_query(q)
    except Exception:
        return JSONResponse([], headers={"X-Geocode": "offline"})


@app.get("/api/reverse")
def reverse_geocode(
    lat: float,
    lng: float,
    zoom: int = 10,
    user: sqlite3.Row = Depends(current_user),
):
    zoom = max(3, min(18, int(zoom)))
    try:
        data = _nominatim(
            "reverse",
            {
                "lat": str(lat),
                "lon": str(lng),
                "format": "json",
                "addressdetails": "1",
                "namedetails": "1",
                "zoom": str(zoom),
                "accept-language": "zh-CN,zh,en",
            },
        )
    except Exception:
        return {"name": None}
    if not data:
        return {"name": None}
    formatted = _format_place(data)
    addr = formatted.get("address") or {}
    if zoom <= 3:
        label = addr.get("country")
    elif zoom <= 6:
        label = addr.get("state") or addr.get("province") or addr.get("region") or addr.get("country")
    elif zoom <= 10:
        label = (
            addr.get("city")
            or addr.get("town")
            or addr.get("municipality")
            or addr.get("county")
            or formatted["title"]
        )
    elif zoom <= 14:
        label = (
            addr.get("suburb")
            or addr.get("city_district")
            or addr.get("neighbourhood")
            or addr.get("town")
            or formatted["title"]
        )
    else:
        label = formatted["title"] or formatted["name"]
    if not label:
        return {"name": None}
    formatted["name"] = label
    formatted["title"] = label
    return formatted


# --------------------------------------------------------------------------
# 静态资源与页面
# --------------------------------------------------------------------------

@app.get("/media/{filename}")
def media(filename: str, user: sqlite3.Row = Depends(current_user)):
    """图片只对已登录成员可见。"""
    path = (db.UPLOAD_DIR / filename).resolve()
    if not str(path).startswith(str(db.UPLOAD_DIR.resolve())) or not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})


@app.get("/health")
def health():
    """给免费平台探活用，不需要登录。"""
    stats = cloudbackup.local_stats()
    return {
        "ok": True,
        "backup_configured": cloudbackup.configured(),
        "users": stats["users"],
        "places": stats["places"],
    }


def _html_page(name: str) -> HTMLResponse:
    text = (STATIC_DIR / name).read_text(encoding="utf-8")
    return HTMLResponse(text.replace("__GM_ROOT__", url_prefix()))


@app.get("/")
def index():
    return _html_page("index.html")


@app.get("/login")
def login_page():
    return _html_page("login.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class DemoGlobeMiddleware:
    """/demo 走虚构案例星球，根路径仍是私人星球。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        demo = path == "/demo" or path.startswith("/demo/")
        token = set_demo(demo)
        try:
            if not demo:
                await self.app(scope, receive, send)
                return
            new_scope = dict(scope)
            new_scope["path"] = path[5:] or "/"
            if "raw_path" in new_scope:
                tail = (path[5:] or "/").encode("ascii", "ignore")
                new_scope["raw_path"] = tail
            await self.app(new_scope, receive, send)
        finally:
            reset_demo(token)


_core_app = app
app = DemoGlobeMiddleware(_core_app)
