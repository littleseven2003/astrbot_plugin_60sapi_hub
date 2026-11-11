"""内容来源地址的规范化与安全展示文本。

来源地址来自服务端实际拼好的请求 URL，不是用户输入。这里仍做一次收敛：
只保留 http(s)、主机与路径，丢掉用户信息、查询串和 fragment，避免凭据或
敏感参数随快照进入文字、Markdown、图片和后台页面。
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

MAX_SOURCE_URL_LENGTH = 2048
SOURCE_UNAVAILABLE_TEXT = "来源不可用"

_UNSAFE_CHARS = frozenset('<>"`')


def sanitize_source_url(value: object) -> str | None:
    """把一次实际请求地址收成可展示的来源；无法安全展示时返回 ``None``。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SOURCE_URL_LENGTH:
        return None
    if any(
        ord(char) < 0x20 or ord(char) == 0x7F or char in _UNSAFE_CHARS
        for char in text
    ):
        return None
    try:
        parsed = urlsplit(text)
    except (UnicodeError, ValueError):
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return None
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    if ":" in host:
        # IPv6 必须保留方括号，否则拼出的地址无法再被解析。
        netloc = f"[{host}]" if port is None else f"[{host}]:{port}"
    else:
        netloc = host if port is None else f"{host}:{port}"

    path = parsed.path
    if any(char.isspace() or char in _UNSAFE_CHARS for char in path):
        return None
    result = urlunsplit((scheme, netloc, path, "", ""))
    if not result or len(result) > MAX_SOURCE_URL_LENGTH:
        return None
    return result


def source_url_text(value: object) -> str:
    """展示用来源：无法展示时明确写“来源不可用”，不猜测或补全。"""
    return sanitize_source_url(value) or SOURCE_UNAVAILABLE_TEXT


__all__ = [
    "MAX_SOURCE_URL_LENGTH",
    "SOURCE_UNAVAILABLE_TEXT",
    "sanitize_source_url",
    "source_url_text",
]
