"""Bilibili 热搜接口的响应模型、错误和解析规则。"""

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

ENDPOINT = "/v2/bili"


class BiliError(NewsError):
    """Bilibili 热搜获取或解析错误基类。"""


class BiliInvalidJSONError(NewsInvalidJSONError):
    """Bilibili 热搜响应不是有效 JSON。"""


class BiliStructureError(NewsStructureError):
    """JSON 不符合 Bilibili 热搜接口约定。"""


class BiliBusinessError(NewsBusinessError):
    """Bilibili 热搜业务 code 不是 200；只暴露数字 code。"""


@dataclass(frozen=True, slots=True)
class BiliHotItem:
    """按上游返回顺序保留的一条 Bilibili 热搜。

    当前上游只公开标题和搜索链接；排名、热度、更新时间不在响应中，
    因此不在此模型中生成。
    """

    title: str
    link: str | None = None


@dataclass(frozen=True, slots=True)
class BiliHotSnapshot:
    """一次 Bilibili 热搜获取的不可变内容快照。"""

    site_id: str
    site_name: str
    endpoint: str
    fetched_at: str
    items: tuple[BiliHotItem, ...]


def build_bili_hot_url(base_url: str) -> str:
    """在站点基础地址后拼接 Bilibili 热搜 endpoint，保留反代路径前缀。"""
    return f"{base_url.rstrip('/')}{ENDPOINT}"


def parse_bili_hot(
    payload: object,
    *,
    site_id: str,
    site_name: str,
    fetched_at: datetime,
) -> BiliHotSnapshot:
    """解析官方/兼容实例的 Bilibili 热搜 JSON 响应。

    ``data`` 必须是数组，条目至少包含非空 ``title``；``link`` 可选，格式
    不安全时忽略。数组顺序原样保留，不补条、不重排，也不把序号解释成
    上游排名。当前接口未返回热度或更新时间，快照中不生成这些字段。
    """
    if not isinstance(payload, dict):
        raise BiliStructureError("响应顶层必须是 JSON 对象")

    code = payload.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise BiliStructureError("响应 code 必须是整数")
    if code != 200:
        raise BiliBusinessError(code)

    data = payload.get("data")
    if not isinstance(data, list):
        raise BiliStructureError("data 必须是数组")

    items = tuple(
        _parse_item(value, index)
        for index, value in enumerate(data)
    )
    return BiliHotSnapshot(
        site_id=site_id,
        site_name=site_name,
        endpoint=ENDPOINT,
        fetched_at=_as_utc(fetched_at).isoformat(timespec="seconds"),
        items=items,
    )


def _parse_item(value: object, index: int) -> BiliHotItem:
    if not isinstance(value, dict):
        raise BiliStructureError(f"data[{index}] 必须是对象")

    title_value = value.get("title")
    if not isinstance(title_value, str) or not title_value.strip():
        raise BiliStructureError(
            f"data[{index}].title 必须是非空字符串",
        )
    return BiliHotItem(
        title=title_value.strip(),
        link=optional_http_url(value.get("link")),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "BiliBusinessError",
    "BiliError",
    "BiliHotItem",
    "BiliHotSnapshot",
    "BiliInvalidJSONError",
    "BiliStructureError",
    "ENDPOINT",
    "build_bili_hot_url",
    "parse_bili_hot",
]
