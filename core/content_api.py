"""每日新闻 / Bilibili 热搜统一预览的后台 API 业务层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bili import BiliHotSnapshot
from .content_service import ContentService
from .douyin import DouyinHotSnapshot
from .toutiao import ToutiaoHotSnapshot
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
from .source_url import source_display_text
from .sites import (
    SiteConflictError,
    SiteTestBusyError,
    SiteTestUnavailableError,
    SiteDisabledError,
    SiteNotFoundError,
    SiteStorageError,
    SiteValidationError,
)
from .zhihu import ZhihuHotSnapshot

from .content_types import (
    BILIBILI_HOT,
    CONTENT_LABELS,
    DAILY_NEWS,
    DOUYIN_HOT,
    KNOWN_CONTENT_TYPES,
    SUPPORTED_CONTENT_TYPES,
    TOUTIAO_HOT,
    ZHIHU_HOT,
)


# 批量测试由管理员显式发起，允许比单站点测试更高的并发；仍与单站点
# 测试共享同一个在途计数，避免页面连点时打爆上游。
BATCH_INFLIGHT_LIMIT = 6

# 批量测试按“用户发起时看到的版本”执行：版本已变化时不再请求上游，
# 逐行返回 stale，由页面提示刷新后重新测试。
BATCH_STALE_MESSAGE = "站点配置已变化，请刷新后重新测试。"


def _batch_row(
    site_id: str,
    content_type: str,
    result: str,
    message: str,
    elapsed_ms: int,
    revision: int | None,
) -> dict[str, Any]:
    """一行批量测试结果；revision 标明该结果对应哪一份站点配置。"""
    return {
        "site_id": site_id,
        "content_type": content_type,
        "result": result,
        "message": message,
        "elapsed_ms": elapsed_ms,
        "site_revision": revision,
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

    async def test_connection(
        self, site_id: object, payload: object, *, inflight_limit: int = 2,
    ) -> ContentApiResult:
        if not isinstance(payload, dict):
            return _error(400, "请求体必须是 JSON 对象。", "invalid_body")
        if set(payload) - {"expected_revision", "content_type"}:
            return _error(400, "请求字段无效。", "validation_error")
        content_type = payload.get("content_type")
        if not isinstance(content_type, str) or content_type not in KNOWN_CONTENT_TYPES:
            return _error(400, "内容类型无效。", "validation_error")
        revision = payload.get("expected_revision")
        if type(revision) is not int or revision < 1:
            return _error(400, "revision 无效。", "validation_error")
        try:
            data = await self._service.test_connection(
                site_id, content_type, revision, inflight_limit=inflight_limit,
            )
        except SiteNotFoundError:
            return _error(404, "站点不存在。", "site_not_found")
        except SiteConflictError:
            return _error(409, "站点配置已变化，请刷新后重试。", "revision_conflict")
        except SiteTestBusyError:
            return _error(429, "同时进行的连接测试过多，请稍后重试。", "test_busy")
        except SiteTestUnavailableError:
            return _error(503, "插件当前不可用。", "plugin_unavailable")
        except SiteValidationError as exc:
            return _error(400, "测试参数无效。", "validation_error")
        except NewsTimeoutError:
            return _error(504, "测试连接超时。", "test_timeout")
        except NewsRedirectError:
            return _error(502, "上游发生不允许的重定向。", "test_redirect")
        except NewsTooLargeError:
            return _error(502, "上游响应过大。", "test_too_large")
        except NewsTransportError:
            return _error(502, "网络传输失败。", "test_network_error")
        except NewsHTTPStatusError:
            return _error(502, "上游返回了失败状态。", "test_http_status")
        except NewsInvalidJSONError:
            return _error(502, "上游返回非 JSON。", "test_invalid_json")
        except NewsClientClosedError:
            return _error(503, "内容客户端当前不可用。", "test_unavailable")
        except NewsBusinessError:
            return _error(502, "上游业务状态无效。", "test_business_error")
        except NewsStructureError:
            return _error(502, "上游内容结构无效。", "test_structure_error")
        except SiteStorageError:
            return _error(503, "站点存储不可用。", "storage_unavailable")
        except Exception:
            return _error(502, "测试失败，无法完成连接验证。", "test_failed")
        return ContentApiResult(200, {"status": "ok", "data": data})

    async def test_sites_batch(self, payload: object) -> ContentApiResult:
        """批量连接测试：只做主动检查，不改配置、不启用站点、不发送消息。"""
        import asyncio
        import time

        if not isinstance(payload, dict):
            return _error(400, "请求体必须是 JSON 对象。", "invalid_body")
        if set(payload) - {"site_ids", "content_types", "expected_revisions"}:
            return _error(400, "请求字段无效。", "validation_error")
        site_ids = payload.get("site_ids")
        content_types = payload.get("content_types")
        if (not isinstance(site_ids, list) or not site_ids
                or len(site_ids) > 20
                or any(not isinstance(item, str) or not item for item in site_ids)):
            return _error(400, "请选择 1~20 个站点。", "validation_error")
        if (not isinstance(content_types, list) or not content_types
                or len(content_types) > len(KNOWN_CONTENT_TYPES)
                or any(item not in KNOWN_CONTENT_TYPES for item in content_types)):
            return _error(400, "内容类型无效。", "validation_error")
        expected = payload.get("expected_revisions")
        if expected is not None and (
            not isinstance(expected, dict)
            or any(
                not isinstance(key, str) or key not in site_ids
                or type(value) is not int or value < 1
                for key, value in expected.items()
            )
        ):
            return _error(400, "站点版本无效。", "validation_error")

        # 每个站点这里只读一次版本：同一站点的五类结果都对应同一份配置。
        # expected_revisions 给出用户发起测试时看到的版本，不一致就不请求上游。
        revisions: dict[str, int] = {}
        missing: set[str] = set()
        stale: set[str] = set()
        for site_id in dict.fromkeys(site_ids):
            try:
                site = await self._service.sites.get_site(site_id)
            except Exception:
                missing.add(site_id)
                continue
            revisions[site_id] = site.revision
            wanted = expected.get(site_id) if expected is not None else None
            if wanted is not None and wanted != site.revision:
                stale.add(site_id)

        semaphore = asyncio.Semaphore(BATCH_INFLIGHT_LIMIT)

        async def run_one(site_id: str, content_type: str) -> dict:
            revision = revisions.get(site_id)
            if site_id in missing:
                return _batch_row(
                    site_id, content_type, "error", "站点不存在或不可用", 0, None,
                )
            if site_id in stale:
                return _batch_row(
                    site_id, content_type, "stale", BATCH_STALE_MESSAGE, 0, revision,
                )
            async with semaphore:
                started = time.monotonic()
                outcome = await self.test_connection(
                    site_id,
                    {"expected_revision": revision,
                     "content_type": content_type},
                    inflight_limit=BATCH_INFLIGHT_LIMIT,
                )
                elapsed = int((time.monotonic() - started) * 1000)
                if outcome.status_code == 200:
                    data = outcome.body.get("data") or {}
                    result = "empty" if data.get("result") == "empty" else "success"
                    message = data.get("message") or ""
                elif outcome.status_code == 409:
                    result = "stale"
                    message = BATCH_STALE_MESSAGE
                else:
                    result = "error"
                    message = outcome.body.get("message") or "测试失败"
                return _batch_row(
                    site_id, content_type, result, message, elapsed, revision,
                )

        tasks = [
            run_one(site_id, content_type)
            for site_id in site_ids
            for content_type in content_types
        ]
        results = await asyncio.gather(*tasks)
        return ContentApiResult(200, {
            "results": list(results),
            "failed": sum(1 for item in results if item["result"] == "error"),
            "stale": sum(1 for item in results if item["result"] == "stale"),
        })

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
        if "force_refresh" in payload:
            force_refresh = payload["force_refresh"]
            if type(force_refresh) is not bool:
                return _error(
                    400,
                    "force_refresh 必须是布尔值。",
                    "invalid_force_refresh",
                )
        else:
            force_refresh = False
        site_id = payload.get("site_id")

        try:
            cached = await self._service.preview_cached(
                content_type,
                site_id,
                force_refresh=force_refresh,
            )
            snapshot = cached.snapshot
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

        data = content_snapshot_dict(content_type, snapshot)
        data["cache"] = {
            "hit": cached.hit,
            "expires_at": cached.expires_at,
            "ttl_seconds": self._service.cache_ttl_seconds,
        }
        return _ok(data)


def content_snapshot_dict(content_type: str, snapshot: Any) -> dict[str, Any]:
    """把领域快照转为统一 API 内容；不包含缓存元信息。"""
    if content_type == DAILY_NEWS:
        return {**snapshot_dict(snapshot), "content_type": DAILY_NEWS}
    if content_type == BILIBILI_HOT:
        return _bili_snapshot_dict(snapshot)
    if content_type == ZHIHU_HOT:
        return _zhihu_snapshot_dict(snapshot)
    if content_type == DOUYIN_HOT:
        return _douyin_snapshot_dict(snapshot)
    if content_type == TOUTIAO_HOT:
        return _toutiao_snapshot_dict(snapshot)
    raise ValueError("unknown content type")


def _bili_snapshot_dict(snapshot: BiliHotSnapshot) -> dict[str, Any]:
    return {
        "content_type": BILIBILI_HOT,
        "site_id": snapshot.site_id,
        "site_name": snapshot.site_name,
        "endpoint": snapshot.endpoint,
        "source_url": snapshot.source_url,
        "source_kind": snapshot.source_kind,
        "source_display": source_display_text(
            snapshot.source_url, snapshot.source_kind,
        ),
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
        "source_url": snapshot.source_url,
        "source_kind": snapshot.source_kind,
        "source_display": source_display_text(
            snapshot.source_url, snapshot.source_kind,
        ),
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


def _douyin_snapshot_dict(snapshot: DouyinHotSnapshot) -> dict[str, Any]:
    return {
        "content_type": DOUYIN_HOT,
        "site_id": snapshot.site_id,
        "site_name": snapshot.site_name,
        "endpoint": snapshot.endpoint,
        "source_url": snapshot.source_url,
        "source_kind": snapshot.source_kind,
        "source_display": source_display_text(
            snapshot.source_url, snapshot.source_kind,
        ),
        "fetched_at": snapshot.fetched_at,
        "item_count": len(snapshot.items),
        "items": [
            {
                "title": item.title,
                "hot_value": item.hot_value,
                "link": item.link,
            }
            for item in snapshot.items
        ],
    }


def _toutiao_snapshot_dict(snapshot: ToutiaoHotSnapshot) -> dict[str, Any]:
    return {
        "content_type": TOUTIAO_HOT,
        "site_id": snapshot.site_id,
        "site_name": snapshot.site_name,
        "endpoint": snapshot.endpoint,
        "source_url": snapshot.source_url,
        "source_kind": snapshot.source_kind,
        "source_display": source_display_text(
            snapshot.source_url, snapshot.source_kind,
        ),
        "fetched_at": snapshot.fetched_at,
        "item_count": len(snapshot.items),
        "items": [
            {
                "title": item.title,
                "hot_value": item.hot_value,
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
    "DOUYIN_HOT",
    "KNOWN_CONTENT_TYPES",
    "SUPPORTED_CONTENT_TYPES",
    "TOUTIAO_HOT",
    "ZHIHU_HOT",
    "ContentApi",
    "ContentApiResult",
    "content_snapshot_dict",
]
