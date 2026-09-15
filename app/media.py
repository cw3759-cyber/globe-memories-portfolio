"""图片保存：统一转 JPEG、限制尺寸、生成缩略图。"""

from __future__ import annotations

import io
import secrets

from PIL import Image, ImageOps

from .db import upload_dir

MAX_SIDE = 1800
THUMB_SIDE = 480
MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic"}


class ImageError(ValueError):
    pass


def save_image(raw: bytes, content_type: str | None) -> tuple[str, str]:
    """返回 (原图文件名, 缩略图文件名)。"""
    if len(raw) > MAX_BYTES:
        raise ImageError("图片超过 20MB")
    if content_type and content_type.split(";")[0] not in ALLOWED:
        raise ImageError(f"不支持的图片格式：{content_type}")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # Pillow 抛出的异常类型很杂
        raise ImageError("无法解析这张图片") from exc

    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    stem = secrets.token_hex(12)
    full_name, thumb_name = f"{stem}.jpg", f"{stem}_t.jpg"

    full = img.copy()
    full.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    full.save(upload_dir() / full_name, "JPEG", quality=88, optimize=True)

    thumb = img.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    thumb.save(upload_dir() / thumb_name, "JPEG", quality=80, optimize=True)

    return full_name, thumb_name


def delete_image(*names: str) -> None:
    for name in names:
        path = upload_dir() / name
        if path.is_file():
            path.unlink(missing_ok=True)
