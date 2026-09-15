"""SQLite 存储层：连接管理与建表。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .globe_ctx import is_demo

BASE_DIR = Path(__file__).resolve().parent.parent
PERSONAL_DIR = BASE_DIR / "data"
DEMO_DIR = BASE_DIR / "data-demo"


def data_dir() -> Path:
    return DEMO_DIR if is_demo() else PERSONAL_DIR


def upload_dir() -> Path:
    return data_dir() / "uploads"


def db_path() -> Path:
    return data_dir() / "app.db"


# 旧名字留给还没改完的引用；取值随当前星球变化
class _DirProxy:
    def __fspath__(self):
        return str(data_dir())

    def __truediv__(self, other):
        return data_dir() / other

    def mkdir(self, *args, **kwargs):
        return data_dir().mkdir(*args, **kwargs)

    def resolve(self):
        return data_dir().resolve()

    def __str__(self):
        return str(data_dir())


DATA_DIR = _DirProxy()


class _UploadProxy(_DirProxy):
    def __fspath__(self):
        return str(upload_dir())

    def __truediv__(self, other):
        return upload_dir() / other

    def mkdir(self, *args, **kwargs):
        return upload_dir().mkdir(*args, **kwargs)

    def resolve(self):
        return upload_dir().resolve()

    def is_dir(self):
        return upload_dir().is_dir()

    def glob(self, pattern):
        return upload_dir().glob(pattern)

    def __str__(self):
        return str(upload_dir())


UPLOAD_DIR = _UploadProxy()


class _DbPathProxy:
    def exists(self):
        return db_path().exists()

    def write_bytes(self, data):
        return db_path().write_bytes(data)

    def read_bytes(self):
        return db_path().read_bytes()

    def stat(self):
        return db_path().stat()

    @property
    def parent(self):
        return db_path().parent

    def __str__(self):
        return str(db_path())

    def __fspath__(self):
        return str(db_path())


DB_PATH = _DbPathProxy()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    color         TEXT NOT NULL DEFAULT '#ffb703',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_by INTEGER NOT NULL REFERENCES users(id),
    used_by    INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS places (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    title         TEXT NOT NULL,
    lat           REAL NOT NULL,
    lng           REAL NOT NULL,
    place_date    TEXT,
    location_name TEXT,
    description   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id   INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    thumb      TEXT NOT NULL,
    caption    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    body       TEXT NOT NULL,
    place_id   INTEGER REFERENCES places(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reads (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id),
    last_read_id INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_photos_place ON photos(place_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(id);
"""


def connect() -> sqlite3.Connection:
    # 每个请求独占一个连接，但 async 端点与同步依赖可能落在不同线程上，
    # 所以关掉线程归属检查。
    conn = sqlite3.connect(db_path(), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    upload_dir().mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
