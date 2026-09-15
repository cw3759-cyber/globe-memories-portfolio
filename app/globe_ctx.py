"""当前请求落在私人星球还是演示星球。默认私人，避免误写真实数据。"""

from __future__ import annotations

from contextvars import ContextVar, Token

_demo: ContextVar[bool] = ContextVar("globe_demo", default=False)


def is_demo() -> bool:
    return _demo.get()


def set_demo(flag: bool) -> Token:
    return _demo.set(bool(flag))


def reset_demo(token: Token) -> None:
    _demo.reset(token)


def url_prefix() -> str:
    return "/demo" if is_demo() else ""


def app_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return url_prefix() + path
