"""每日新闻预览的后台 API 业务层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .news import (
    NewsBusinessError,
    NewsClientClosedError,
    NewsHTTPStatusError,
    NewsInvalidJSONError,
    NewsRedirectError,
    NewsSnapshot,
    NewsStructureError,
    NewsTimeoutError,
    NewsTooLargeError,
    NewsTransportError,
)
from .news_service import NewsService
from .sites import (
    SiteDisabledError,
    SiteNotFoundError,
    SiteStorageError,
    SiteValidationError,
)


@dataclass(frozen=True, slots=True)
class NewsApiResult:
    """一个可直接序列化的每日新闻 API 结果。"""

    status_code: int
    body: dict[str, Any]


class NewsApi:
    """只接受站点 ID，由服务端读取站点地址、启用状态、超时和凭据。"""

    def __init__(self, service: NewsService) -> None:
        self._service = service

    async def preview(self, payload: object) -> NewsApiResult:
        if not isinstance(payload, dict):
            return _error(400, "请求体必须是 JSON 对象。", "invalid_body")
        try:
            snapshot = await self._service.preview(payload.get("site_id"))
        except SiteValidationError as exc:
            return _validation_error(exc)
        except SiteNotFoundError:
            return _error(404, "站点不存在或已被删除。", "not_found")
        except SiteDisabledError:
            return _error(
                409,
                "站点未启用，请先启用后再获取每日新闻。",
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
                "获取每日新闻超时，请稍后重试。",
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
                "上游 JSON 结构不符合每日新闻接口约定。",
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
        return _ok(_snapshot_dict(snapshot))


def _snapshot_dict(snapshot: NewsSnapshot) -> dict[str, Any]:
    return {
        "site_id": snapshot.site_id,
        "site_name": snapshot.site_name,
        "endpoint": snapshot.endpoint,
        "fetched_at": snapshot.fetched_at,
        "content_date": snapshot.content_date,
        "date_is_unknown": snapshot.date_is_unknown,
        "is_stale": snapshot.is_stale,
        "warnings": list(snapshot.warnings),
        "items": [
            {"title": item.title, "link": item.link}
            for item in snapshot.items
        ],
        "tip": snapshot.tip,
        "source_link": snapshot.source_link,
    }


def _ok(data: Any) -> NewsApiResult:
    return NewsApiResult(
        status_code=200,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    code: str,
    *,
    data: dict[str, Any] | None = None,
) -> NewsApiResult:
    details = {"code": code}
    if data:
        details.update(data)
    return NewsApiResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _validation_error(exc: SiteValidationError) -> NewsApiResult:
    return _error(
        400,
        str(exc),
        "validation_error",
        data={"field": exc.field},
    )


__all__ = ["NewsApi", "NewsApiResult"]
