"""每日新闻接口的响应模型、错误和解析规则。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

ENDPOINT = "/v2/60s"
CHINA_TIMEZONE = timezone(timedelta(hours=8))
_DATE_RE = re.compile(r"^(\d{4})[-/](\d{2})[-/](\d{2})$")


class NewsError(Exception):
    """每日新闻获取或解析错误基类。"""


class NewsInvalidJSONError(NewsError):
    """响应不是有效 JSON。"""


class NewsStructureError(NewsError):
    """JSON 不符合每日新闻接口约定。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"上游 JSON 结构不符合接口约定：{reason}。")


class NewsBusinessError(NewsError):
    """上游 JSON 的业务 code 不是 200；只暴露数字 code。"""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"上游返回业务错误（code {code}）。")


class NewsTimeoutError(NewsError):
    """连接、读取或整体操作超时。"""


class NewsTooLargeError(NewsError):
    """响应体超过读取上限。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"上游响应超过大小限制（{limit} 字节）。")


class NewsHTTPStatusError(NewsError):
    """上游返回非 2xx HTTP 状态；只暴露数字状态码。"""

    def __init__(
        self,
        status_code: int,
        *,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"上游站点返回 HTTP {status_code}。")


class NewsRedirectError(NewsError):
    """上游返回重定向；本实现不跟随重定向。"""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("上游站点返回重定向，已拒绝跟随。")


class NewsTransportError(NewsError):
    """网络传输失败。"""


class NewsClientClosedError(NewsError):
    """共享 HTTP 客户端未打开或已关闭。"""


@dataclass(frozen=True, slots=True)
class NewsItem:
    """按上游顺序保留的一条新闻。"""

    title: str
    link: str | None = None


@dataclass(frozen=True, slots=True)
class NewsSnapshot:
    """一次每日新闻获取的不可变内容快照。"""

    site_id: str
    site_name: str
    endpoint: str
    fetched_at: str
    content_date: str | None
    date_is_unknown: bool
    is_stale: bool
    warnings: tuple[str, ...]
    items: tuple[NewsItem, ...]
    tip: str | None = None
    source_link: str | None = None


def build_daily_news_url(base_url: str) -> str:
    """在站点基础地址后拼接每日新闻 endpoint，保留反代路径前缀。"""
    return f"{base_url.rstrip('/')}{ENDPOINT}"


def parse_daily_news(
    payload: object,
    *,
    site_id: str,
    site_name: str,
    fetched_at: datetime,
) -> NewsSnapshot:
    """解析官方/兼容实例的每日新闻 JSON 响应。

    ``data.news`` 是必要数组；条目可以是字符串，也可以是带 ``title`` 的
    对象（可选 ``link``）。``data.date``、``tip`` 和顶层来源链接是可选字段；
    日期缺失或无法识别时保持未知，不替换为抓取日期。
    """
    if not isinstance(payload, dict):
        raise NewsStructureError("响应顶层必须是 JSON 对象")

    code = payload.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise NewsStructureError("响应 code 必须是整数")
    if code != 200:
        raise NewsBusinessError(code)

    data = payload.get("data")
    if not isinstance(data, dict):
        raise NewsStructureError("data 必须是对象")

    raw_news = data.get("news")
    if not isinstance(raw_news, list):
        raise NewsStructureError("data.news 必须是数组")

    items = tuple(
        _parse_news_item(value, index)
        for index, value in enumerate(raw_news)
    )
    content_date, date_unknown, warnings = _parse_content_date(
        data.get("date"),
    )

    fetched_at_utc = _as_utc(fetched_at)
    today = fetched_at_utc.astimezone(CHINA_TIMEZONE).date()
    is_stale = content_date is not None and content_date < today
    if is_stale:
        warnings.append(
            f"上游内容日期 {content_date.isoformat()} 早于当前日期 "
            f"{today.isoformat()}；预览保留原始日期，未替换为今天。",
        )

    return NewsSnapshot(
        site_id=site_id,
        site_name=site_name,
        endpoint=ENDPOINT,
        fetched_at=fetched_at_utc.isoformat(timespec="seconds"),
        content_date=content_date.isoformat() if content_date else None,
        date_is_unknown=date_unknown,
        is_stale=is_stale,
        warnings=tuple(warnings),
        items=items,
        tip=_optional_text(data.get("tip")),
        source_link=optional_http_url(data.get("link")),
    )


def _parse_news_item(value: object, index: int) -> NewsItem:
    if isinstance(value, str):
        title = value.strip()
        if not title:
            raise NewsStructureError(
                f"data.news[{index}] 的标题不能为空",
            )
        return NewsItem(title=title)

    if isinstance(value, dict):
        title_value = value.get("title")
        if not isinstance(title_value, str) or not title_value.strip():
            raise NewsStructureError(
                f"data.news[{index}].title 必须是非空字符串",
            )
        return NewsItem(
            title=title_value.strip(),
            link=optional_http_url(value.get("link")),
        )

    raise NewsStructureError(
        f"data.news[{index}] 必须是字符串或含 title 的对象",
    )


def _parse_content_date(
    value: object,
) -> tuple[date | None, bool, list[str]]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, True, ["上游未提供内容日期，按未知处理。"]
    if not isinstance(value, str):
        return None, True, ["上游内容日期格式无法识别，按未知处理。"]

    match = _DATE_RE.fullmatch(value.strip())
    if match is None:
        return None, True, ["上游内容日期格式无法识别，按未知处理。"]
    try:
        parsed = date(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
    except ValueError:
        return None, True, ["上游内容日期格式无法识别，按未知处理。"]
    return parsed, False, []


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def optional_http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ord(char) < 0x20 for char in text):
        return None
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return text


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "CHINA_TIMEZONE",
    "ENDPOINT",
    "NewsBusinessError",
    "NewsClientClosedError",
    "NewsError",
    "NewsHTTPStatusError",
    "NewsInvalidJSONError",
    "NewsItem",
    "NewsRedirectError",
    "NewsSnapshot",
    "NewsStructureError",
    "NewsTimeoutError",
    "NewsTooLargeError",
    "NewsTransportError",
    "build_daily_news_url",
    "optional_http_url",
    "parse_daily_news",
]
