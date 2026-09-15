"""密码哈希与基于签名 Cookie 的会话。"""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from itsdangerous import BadSignature, URLSafeSerializer

from .db import data_dir
from .globe_ctx import is_demo

SESSION_MAX_AGE = 60 * 60 * 24 * 365
_serializers: dict[bool, URLSafeSerializer] = {}


def cookie_name() -> str:
    return "gm_session_demo" if is_demo() else "gm_session"


COOKIE_NAME = "gm_session"  # 私人星球默认名；演示请求请用 cookie_name()


def _secret_file() -> Path:
    return data_dir() / ".secret_key"


def _load_secret() -> str:
    """密钥落盘，避免重启后所有人被登出。"""
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = _secret_file()
    if path.exists():
        return path.read_text().strip()
    key = secrets.token_hex(32)
    path.write_text(key)
    path.chmod(0o600)
    return key


def _get_serializer() -> URLSafeSerializer:
    demo = is_demo()
    ser = _serializers.get(demo)
    if ser is None:
        salt = "globe-memories-demo" if demo else "globe-memories"
        ser = URLSafeSerializer(_load_secret(), salt=salt)
        _serializers[demo] = ser
    return ser


def reload_secret() -> None:
    """从备份恢复密钥之后必须重载，否则每个人都会被踢去登录页。"""
    _serializers.clear()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


def make_token(user_id: int) -> str:
    return _get_serializer().dumps({"uid": user_id})


def read_token(token: str) -> int | None:
    try:
        data = _get_serializer().loads(token)
    except BadSignature:
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, int) else None


def new_invite_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join(
        "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)
    )
