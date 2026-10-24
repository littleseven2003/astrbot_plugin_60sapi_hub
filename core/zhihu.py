"""知乎热榜接口的响应模型、错误和解析规则。"""

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

ENDPOINT = "/v2/zhihu"


class ZhihuError(NewsError):
    """知乎热榜获取或解析错误基类。"""


class ZhihuInvalidJSONError(NewsInvalidJSONError):
    """知乎热榜响应不是有效 JSON。"""


class ZhihuStructureError(NewsStructureError):
    """JSON 不符合知乎热榜接口约定。"""


class ZhihuBusinessError(NewsBusinessError):
    """知乎热榜业务 code 不是 200；只暴露数字 code。"""


@dataclass(frozen=True, slots=True)
class ZhihuHotItem:
    """按上游返回顺序保留的一条知乎热榜。

    只保留核验过的展示字段：标题、摘要、热度文本和可选安全链接。
    上游的 created/created_at 是话题创建时间，不是更新时间；接口没有
    排名字段，不在这里生成排名或热度数值。
    """

    title: str
    detail: str | None = None
    hot_value_desc: str | None = None
    link: str | None = None


@dataclass(frozen=True, slots=True)
class ZhihuHotSnapshot:
    """一次知乎热榜获取的不可变内容快照。"""

    site_id: str
    site_name: str
    endpoint: str
    fetched_at: str
    items: tuple[ZhihuHotItem, ...]


def build_zhihu_hot_url(base_url: str) -> str:
    """在站点基础地址后拼接知乎热榜 endpoint，保留反代路径前缀。"""
    return f"{base_url.rstrip('/')}{ENDPOINT}"


def parse_zhihu_hot(
    payload: object,
    *,
    site_id: str,
    site_name: str,
    fetched_at: datetime,
) -> ZhihuHotSnapshot:
    """解析官方/兼容实例的知乎热榜 JSON 响应。

    ``data`` 必须是数组，条目至少包含非空 ``title``。``detail`` 和
    ``hot_value_desc`` 是可选的文本字段，缺失或类型不符时保持未知；
    ``hot_value_desc`` 保留原始文本，不换算成数值。``link`` 可选且必须
    是安全 HTTP(S)。数组顺序原样保留，不补条、不重排，不使用序号冒充
    排名。
    """
    if not isinstance(payload, dict):
        raise ZhihuStructureError("响应顶层必须是 JSON 对象")

    code = payload.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise ZhihuStructureError("响应 code 必须是整数")
    if code != 200:
        raise ZhihuBusinessError(code)

    data = payload.get("data")
    if not isinstance(data, list):
        raise ZhihuStructureError("data 必须是数组")

    items = tuple(
        _parse_item(value, index)
        for index, value in enumerate(data)
    )
    return ZhihuHotSnapshot(
        site_id=site_id,
        site_name=site_name,
        endpoint=ENDPOINT,
        fetched_at=_as_utc(fetched_at).isoformat(timespec="seconds"),
        items=items,
    )


def _parse_item(value: object, index: int) -> ZhihuHotItem:
    if not isinstance(value, dict):
        raise ZhihuStructureError(f"data[{index}] 必须是对象")

    title_value = value.get("title")
    if not isinstance(title_value, str) or not title_value.strip():
        raise ZhihuStructureError(
            f"data[{index}].title 必须是非空字符串",
        )
    return ZhihuHotItem(
        title=title_value.strip(),
        detail=_optional_text(value.get("detail")),
        hot_value_desc=_optional_text(value.get("hot_value_desc")),
        link=optional_http_url(value.get("link")),
    )


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "ENDPOINT",
    "ZhihuBusinessError",
    "ZhihuError",
    "ZhihuHotItem",
    "ZhihuHotSnapshot",
    "ZhihuInvalidJSONError",
    "ZhihuStructureError",
    "build_zhihu_hot_url",
    "parse_zhihu_hot",
]
