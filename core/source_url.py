"""内容来源地址的规范化与安全展示文本。

来源地址来自服务端实际拼好的请求 URL，不是用户输入。这里仍做一次收敛：
只保留 http(s)、主机与路径，丢掉用户信息、查询串和 fragment，避免凭据或
敏感参数随快照进入文字、Markdown、图片和后台页面。

公开来源对外只展示域名，不展示路径、查询串或凭据；自建站点只写标签。
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

MAX_SOURCE_URL_LENGTH = 2048
# 站点基础地址上限是 2048，拼接 endpoint 后必然可能超过它，所以快照另给
# 一个更宽的预算：合法的长地址也要完整表达端口、路径和 endpoint。
MAX_SNAPSHOT_URL_LENGTH = 4096
# 与 httpx 发送请求行时的编码保持一致：路径里 HTTP 客户端会编码的字符
# （空格、尖括号、引号、花括号、非 ASCII 等）percent-encode 后写入快照，
# 其余字符原样保留，保证快照地址就是本次实际请求的地址。
_PATH_SAFE = "/:@!$&'()*+,;=~-._%|[]^"
# 只提取域名时不需要整串地址都合规：拼接 endpoint 后的长地址、路径里的
# 特殊字符都不应让可展示的域名丢失。这里仍给解析一个上界，避免异常输入。
MAX_SOURCE_SCAN_LENGTH = 8192
SOURCE_UNAVAILABLE_TEXT = "来源不可用"
SOURCE_SELF_BUILT_TEXT = "自建服务"
SOURCE_KIND_PUBLIC = "public"
SOURCE_KIND_SELF_BUILT = "self_built"
SOURCE_KINDS = frozenset({SOURCE_KIND_PUBLIC, SOURCE_KIND_SELF_BUILT})

_UNSAFE_CHARS = frozenset('<>"' + "`")


def _split_source(value: object) -> tuple[str, str, object] | None:
    """解析出来源的 scheme、主机与解析结果；不合规时返回 None。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SOURCE_SCAN_LENGTH:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
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
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower()
    if any(char in _UNSAFE_CHARS or char.isspace() for char in host):
        return None
    return scheme, host, parsed


def sanitize_source_url(value: object) -> str | None:
    """把一次实际请求地址收成可展示的完整来源；无法安全展示时返回 None。"""
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
    split = _split_source(text)
    if split is None:
        return None
    scheme, host, parsed = split
    try:
        port = parsed.port
    except ValueError:
        return None
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


def source_domain_text(value: object) -> str | None:
    """只取可展示的域名，丢弃协议、端口、路径、查询串与用户信息。

    IPv6 保留方括号，避免 ::1 这类地址在文本里产生歧义。路径是否包含
    特殊字符、整串地址是否超过长度上限都不影响域名本身的可展示性。
    """
    split = _split_source(value)
    if split is None:
        return None
    _scheme, host, _parsed = split
    if ":" in host:
        return f"[{host}]"
    return host


def snapshot_source_url(value: object) -> str | None:
    """写入快照的来源地址：完整表达本次请求使用的端口、路径与 endpoint。

    路径按 HTTP 客户端的同一规则 percent-encode，因此快照地址与实际上游
    请求地址一致，不会丢失端口、反向代理前缀或 endpoint。仍然只保留
    http(s)、主机与路径，去掉用户信息、查询串和 fragment；无法安全表达
    时返回 None，由展示层写“来源不可用”，不做静默缩短。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SNAPSHOT_URL_LENGTH:
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        return None
    # 站点基础地址本身禁止反斜杠，这里保持一致，避免出现歧义路径。
    if "\\" in text:
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
    if any(char in _UNSAFE_CHARS or char.isspace() for char in host):
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = quote(parsed.path, safe=_PATH_SAFE)
    result = urlunsplit((scheme, netloc, path, "", ""))
    if not result or len(result) > MAX_SNAPSHOT_URL_LENGTH:
        return None
    return result


def source_url_text(value: object) -> str:
    """展示用完整来源地址：无法展示时写“来源不可用”，不猜测或补全。"""
    return sanitize_source_url(value) or SOURCE_UNAVAILABLE_TEXT


def source_display_text(source_url: object, source_kind: object) -> str:
    """按站点来源类型给出对外可展示的文本。

    公开实例只写域名，不暴露路径、查询串或凭据；自建站点只写“自建服务”。
    未知类型有可展示域名时按自建隐藏，避免旧快照意外泄露内网地址；完全
    没有来源信息时写“来源不可用”，不做任何推测。
    """
    if source_kind == SOURCE_KIND_PUBLIC:
        return source_domain_text(source_url) or SOURCE_UNAVAILABLE_TEXT
    if source_kind == SOURCE_KIND_SELF_BUILT:
        return SOURCE_SELF_BUILT_TEXT
    if source_domain_text(source_url) is None:
        return SOURCE_UNAVAILABLE_TEXT
    return SOURCE_SELF_BUILT_TEXT


__all__ = [
    "MAX_SNAPSHOT_URL_LENGTH",
    "MAX_SOURCE_SCAN_LENGTH",
    "MAX_SOURCE_URL_LENGTH",
    "SOURCE_KIND_PUBLIC",
    "SOURCE_KIND_SELF_BUILT",
    "SOURCE_KINDS",
    "SOURCE_SELF_BUILT_TEXT",
    "SOURCE_UNAVAILABLE_TEXT",
    "sanitize_source_url",
    "snapshot_source_url",
    "source_display_text",
    "source_domain_text",
    "source_url_text",
]
