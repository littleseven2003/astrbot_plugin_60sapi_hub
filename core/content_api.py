"""每日新闻 / Bilibili 热搜统一预览的后台 API 业务层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bili import BiliHotSnapshot
from .content_service import ContentService
from .news import (
    NewsBusinessError,
    NewsClientClosedError,
    NewsHTTPStatusError,
    NewsInvalidJSONError,
    NewsRedirectError,
    NewsStructureError,
    NewsTimeoutError,
    NewsTooLargeError,
    NewsTransportError,
)
from .news_api import snapshot_dict
from .sites import (
    SiteDisabledError,
    SiteNotFoundError,
    SiteStorageError,
    SiteValidationError,
)
from .zhihu import ZhihuHotSnapshot

DAILY_NEWS = "daily_news"
BILIBILI_HOT = "bilibili_hot"
ZHIHU_HOT = "zhihu_hot"
SUPPORTED_CONTENT_TYPES = (DAILY_NEWS, BILIBILI_HOT, ZHIHU_HOT)
KNOWN_CONTENT_TYPES = frozenset(SUPPORTED_CONTENT_TYPES)
CONTENT_LABELS = {
    DAILY_NEWS: "每日新闻",
    BILIBILI_HOT: "Bilibili 热搜",
    ZHIHU_HOT: "知乎热榜",
}


@dataclass(frozen=True, slots=True)
class ContentApiResult:
    """一个可直接序列化的内容预览 API 结果。"""

    status_code: int
    body: dict[str, Any]


class ContentApi:
    """按内容类型调用固定适配器；只接收站点 ID，不接受上游 URL。"""

    def __init__(self, service: ContentService) -> None:
        self._service = service

    async def preview(self, payload: object) -> ContentApiResult:
        if not isinstance(payload, dict):
            return _error(400, "请求体必须是 JSON 对象。", "invalid_body")

        content_type = payload.get("content_type")
        if not isinstance(content_type, str) or content_type not in KNOWN_CONTENT_TYPES:
            return _error(
                400,
                "未知内容类型，当前支持："
                f"{'、'.join(CONTENT_LABELS[item] for item in SUPPORTED_CONTENT_TYPES)}。",
                "unknown_content_type",
            )
        label = CONTENT_LABELS[content_type]
        site_id = payload.get("site_id")

        try:
            if content_type == DAILY_NEWS:
                snapshot = await self._service.preview_daily(site_id)
            elif content_type == BILIBILI_HOT:
                snapshot = await self._service.preview_bili(site_id)
            else:
                snapshot = await self._service.preview_zhihu(site_id)
        except SiteValidationError as exc:
            return _validation_error(exc)
        except SiteNotFoundError:
            return _error(404, "站点不存在或已被删除。", "not_found")
        except SiteDisabledError:
            return _error(
                409,
                "站点未启用，请先启用后再获取内容。",
                "site_disabled",
            )
        except SiteStorageError:
            return _error(
                503,
                "站点存储暂不可用，请稍后重试。",
                "storage_unavailable",
            )
        except NewsTimeoutError:
            return _error(
                504,
                f"获取{label}超时，请稍后重试。",
                "upstream_timeout",
            )
        except NewsTooLargeError:
            return _error(
                502,
                "上游响应超过大小限制，已停止读取。",
                "upstream_too_large",
            )
        except NewsInvalidJSONError:
            return _error(
                502,
                "上游返回的内容不是有效 JSON。",
                "upstream_invalid_json",
            )
        except NewsBusinessError as exc:
            return _error(
                502,
                str(exc),
                "upstream_business_error",
                data={"upstream_code": exc.code},
            )
        except NewsStructureError:
            return _error(
                502,
                "上游 JSON 结构不符合接口约定。",
                "upstream_structure",
            )
        except NewsHTTPStatusError as exc:
            return _error(
                502,
                str(exc),
                "upstream_http_error",
                data={"upstream_status": exc.status_code},
            )
        except NewsRedirectError:
            return _error(
                502,
                "上游站点返回重定向，已拒绝跟随。",
                "upstream_redirect",
            )
        except NewsTransportError:
            return _error(
                502,
                "无法连接上游站点，请检查地址或网络。",
                "upstream_unreachable",
            )
        except NewsClientClosedError:
            return _error(
                503,
                "上游客户端当前不可用。",
                "upstream_unavailable",
            )
        except Exception:
            return _error(
                500,
                "服务器内部错误，请稍后重试。",
                "internal_error",
            )

        if content_type == DAILY_NEWS:
            data = {**snapshot_dict(snapshot), "content_type": DAILY_NEWS}
        elif content_type == BILIBILI_HOT:
            data = _bili_snapshot_dict(snapshot)
        else:
            data = _zhihu_snapshot_dict(snapshot)
        return _ok(data)


def _bili_snapshot_dict(snapshot: BiliHotSnapshot) -> dict[str, Any]:
    return {
        "content_type": BILIBILI_HOT,
        "site_id": snapshot.site_id,
        "site_name": snapshot.site_name,
        "endpoint": snapshot.endpoint,
        "fetched_at": snapshot.fetched_at,
        "item_count": len(snapshot.items),
        "items": [
            {"title": item.title, "link": item.link}
            for item in snapshot.items
        ],
    }


def _zhihu_snapshot_dict(snapshot: ZhihuHotSnapshot) -> dict[str, Any]:
    return {
        "content_type": ZHIHU_HOT,
        "site_id": snapshot.site_id,
        "site_name": snapshot.site_name,
        "endpoint": snapshot.endpoint,
        "fetched_at": snapshot.fetched_at,
        "item_count": len(snapshot.items),
        "items": [
            {
                "title": item.title,
                "detail": item.detail,
                "hot_value_desc": item.hot_value_desc,
                "link": item.link,
            }
            for item in snapshot.items
        ],
    }


def _ok(data: Any) -> ContentApiResult:
    return ContentApiResult(
        status_code=200,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    code: str,
    *,
    data: dict[str, Any] | None = None,
) -> ContentApiResult:
    details = {"code": code}
    if data:
        details.update(data)
    return ContentApiResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _validation_error(exc: SiteValidationError) -> ContentApiResult:
    return _error(
        400,
        str(exc),
        "validation_error",
        data={"field": exc.field},
    )


__all__ = [
    "BILIBILI_HOT",
    "CONTENT_LABELS",
    "DAILY_NEWS",
    "KNOWN_CONTENT_TYPES",
    "SUPPORTED_CONTENT_TYPES",
    "ZHIHU_HOT",
    "ContentApi",
    "ContentApiResult",
]
