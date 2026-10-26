"""今日头条热搜接口的响应模型、错误和解析规则。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .news import (
    NewsBusinessError,
    NewsError,
    NewsInvalidJSONError,
    NewsStructureError,
    optional_http_url,
)

ENDPOINT = "/v2/toutiao"


class ToutiaoError(NewsError):
    """今日头条热搜获取或解析错误基类。"""


class ToutiaoInvalidJSONError(NewsInvalidJSONError):
    """今日头条热搜响应不是有效 JSON。"""


class ToutiaoStructureError(NewsStructureError):
    """JSON 不符合今日头条热搜接口约定。"""


class ToutiaoBusinessError(NewsBusinessError):
    """今日头条热搜业务 code 不是 200；只暴露数字 code。"""


@dataclass(frozen=True, slots=True)
class ToutiaoHotItem:
    """按上游返回顺序保留的一条今日头条热搜。

    只保留核验过的展示字段：标题、原始热度整数和可选安全链接。
    上游的 cover 不渲染；接口未提供排名、摘要、标签或更新时间。
    """

    title: str
    hot_value: int | None = None
    link: str | None = None


@dataclass(frozen=True, slots=True)
class ToutiaoHotSnapshot:
    """一次今日头条热搜获取的不可变内容快照。"""

    site_id: str
    site_name: str
    endpoint: str
    fetched_at: str
    items: tuple[ToutiaoHotItem, ...]


def build_toutiao_hot_url(base_url: str) -> str:
    """在站点基础地址后拼接今日头条热搜 endpoint，保留反代路径前缀。"""
    return f"{base_url.rstrip('/')}{ENDPOINT}"


def parse_toutiao_hot(
    payload: object,
    *,
    site_id: str,
    site_name: str,
    fetched_at: datetime,
) -> ToutiaoHotSnapshot:
    """解析官方/兼容实例的今日头条热搜 JSON 响应。

    ``data`` 必须是数组，条目至少包含非空 ``title``。``hot_value`` 是
    上游原始整数；缺失、类型不符或负数时保持未知，不做单位换算。
    ``link`` 可选且必须是安全 HTTP(S)。数组顺序原样保留，不补条、不
    重排，不使用序号冒充排名。封面和时间字段不进入快照。
    """
    if not isinstance(payload, dict):
        raise ToutiaoStructureError("响应顶层必须是 JSON 对象")

    code = payload.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise ToutiaoStructureError("响应 code 必须是整数")
    if code != 200:
        raise ToutiaoBusinessError(code)

    data = payload.get("data")
    if not isinstance(data, list):
        raise ToutiaoStructureError("data 必须是数组")

    items = tuple(
        _parse_item(value, index)
        for index, value in enumerate(data)
    )
    return ToutiaoHotSnapshot(
        site_id=site_id,
        site_name=site_name,
        endpoint=ENDPOINT,
        fetched_at=_as_utc(fetched_at).isoformat(timespec="seconds"),
        items=items,
    )


def _parse_item(value: object, index: int) -> ToutiaoHotItem:
    if not isinstance(value, dict):
        raise ToutiaoStructureError(f"data[{index}] 必须是对象")

    title_value = value.get("title")
    if not isinstance(title_value, str) or not title_value.strip():
        raise ToutiaoStructureError(
            f"data[{index}].title 必须是非空字符串",
        )

    hot_value = value.get("hot_value")
    if isinstance(hot_value, bool) or not isinstance(hot_value, int):
        hot_value = None
    elif hot_value < 0:
        hot_value = None

    return ToutiaoHotItem(
        title=title_value.strip(),
        hot_value=hot_value,
        link=optional_http_url(value.get("link")),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ENDPOINT",
    "ToutiaoBusinessError",
    "ToutiaoError",
    "ToutiaoHotItem",
    "ToutiaoHotSnapshot",
    "ToutiaoInvalidJSONError",
    "ToutiaoStructureError",
    "build_toutiao_hot_url",
    "parse_toutiao_hot",
]
