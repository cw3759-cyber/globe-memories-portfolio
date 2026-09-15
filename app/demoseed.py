"""虚构的投资人演示星球。绝不读取私人 data/ 目录。"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from . import auth, db
from .globe_ctx import reset_demo, set_demo

DEMO_PASSWORD = "demo2026"
DEMO_USER = "demo"
DEMO_PARTNER = "case"

# 荆州附近同一地点：用来演示「四段回忆一个气泡」
JINGZHOU = (30.352, 112.204)

STORY_PLACES = [
    {
        "user": DEMO_USER,
        "title": "第一次遇见",
        "date": "2022-04-16",
        "location_name": "荆州",
        "lat": JINGZHOU[0],
        "lng": JINGZHOU[1],
        "description": "案例故事：两个人在同一座小城遇见。投资人把鼠标放在这个气泡上，会看到四段回忆一起绽开。",
        "color": (232, 164, 120),
        "label": "遇见",
    },
    {
        "user": DEMO_PARTNER,
        "title": "江边的晚饭",
        "date": "2022-07-03",
        "location_name": "荆州",
        "lat": 30.348,
        "lng": 112.218,
        "description": "还是这座城。同一地点的第二段回忆，会和另外三段叠在同一个气泡里。",
        "color": (120, 168, 196),
        "label": "晚饭",
    },
    {
        "user": DEMO_USER,
        "title": "一周年",
        "date": "2023-04-16",
        "location_name": "荆州",
        "lat": 30.355,
        "lng": 112.190,
        "description": "第三段。功能演示：附近几公里内的回忆会合成为一个大气泡。",
        "color": (214, 122, 148),
        "label": "周年",
    },
    {
        "user": DEMO_PARTNER,
        "title": "冬天的信",
        "date": "2023-12-24",
        "location_name": "荆州",
        "lat": 30.360,
        "lng": 112.210,
        "description": "第四段。点开绽开的拍立得，可以分别看每一段。",
        "color": (176, 140, 196),
        "label": "冬天",
    },
    {
        "user": DEMO_USER,
        "title": "塞纳河边",
        "date": "2024-05-11",
        "location_name": "巴黎",
        "lat": 48.8584,
        "lng": 2.2945,
        "description": "离开小城之后，把去过的地方继续钉在地球上。",
        "color": (96, 140, 188),
        "label": "巴黎",
    },
    {
        "user": DEMO_PARTNER,
        "title": "京都的雨",
        "date": "2024-09-22",
        "location_name": "京都",
        "lat": 35.0116,
        "lng": 135.7681,
        "description": "每一段都可以配照片、日期和想说的话。",
        "color": (80, 156, 132),
        "label": "京都",
    },
    {
        "user": DEMO_USER,
        "title": "看海的那天",
        "date": "2025-06-08",
        "location_name": "圣托里尼",
        "lat": 36.3932,
        "lng": 25.4615,
        "description": "这颗星球是给投资人看的案例，内容全部虚构。",
        "color": (72, 156, 196),
        "label": "海",
    },
]


def _swatch(color: tuple[int, int, int], label: str, size: int) -> bytes:
    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((18, 18, size - 18, size - 18), outline=(255, 248, 240), width=6)
    return _jpeg(img)


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


def _save_photo(raw: bytes) -> tuple[str, str]:
    from .media import save_image

    return save_image(raw, "image/jpeg")


def reset_demo_globe() -> str:
    """每次启动都重写演示库，保证案例干净，且绝不碰私人 data/。"""
    token = set_demo(True)
    try:
        target = db.data_dir()
        if target.exists():
            shutil.rmtree(target)
        db.init()
        conn = db.connect()
        try:
            owner = conn.execute(
                "INSERT INTO users (username, display_name, password_hash, color) VALUES (?,?,?,?)",
                (DEMO_USER, "晚晚", auth.hash_password(DEMO_PASSWORD), "#ffb703"),
            )
            partner = conn.execute(
                "INSERT INTO users (username, display_name, password_hash, color) VALUES (?,?,?,?)",
                (DEMO_PARTNER, "阿舟", auth.hash_password(DEMO_PASSWORD), "#4cc9f0"),
            )
            ids = {DEMO_USER: owner.lastrowid, DEMO_PARTNER: partner.lastrowid}
            for uid in ids.values():
                conn.execute(
                    "INSERT OR IGNORE INTO reads (user_id, last_read_id) VALUES (?, 0)",
                    (uid,),
                )
            jingzhou_ids: list[int] = []
            for item in STORY_PLACES:
                cur = conn.execute(
                    """INSERT INTO places (user_id, title, lat, lng, place_date, location_name, description)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        ids[item["user"]],
                        item["title"],
                        item["lat"],
                        item["lng"],
                        item["date"],
                        item["location_name"],
                        item["description"],
                    ),
                )
                place_id = cur.lastrowid
                if item["location_name"] == "荆州":
                    jingzhou_ids.append(place_id)
                full, thumb = _save_photo(_swatch(item["color"], item["label"], 1200))
                conn.execute(
                    "INSERT INTO photos (place_id, filename, thumb) VALUES (?,?,?)",
                    (place_id, full, thumb),
                )
            conn.execute(
                "INSERT INTO messages (user_id, body, place_id) VALUES (?,?,?)",
                (ids[DEMO_USER], "这颗是给演示用的案例地球。四段荆州回忆会叠在同一个气泡里。", jingzhou_ids[0] if jingzhou_ids else None),
            )
            conn.execute(
                "INSERT INTO messages (user_id, body) VALUES (?,?)",
                (ids[DEMO_PARTNER], "功能跟正式产品一样：钉地点、传照片、互相留言。内容是虚构的。"),
            )
            conn.commit()
        finally:
            conn.close()
        return f"演示星球已就绪：{target}"
    finally:
        reset_demo(token)


def export_seed_dir(dest: Path) -> None:
    """把当前演示数据拷出去，方便放进 GitHub 演示仓库。"""
    token = set_demo(True)
    try:
        src = db.data_dir()
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.log", "public_url.txt"))
    finally:
        reset_demo(token)
