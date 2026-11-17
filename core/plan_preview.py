"""按一份内容方案在单个站点上取内容的预览与图片生成服务。

预览、聊天查询、手动/定时投递共用这里：调用方给出栏目方案与站点，
本类只负责「这一个站点上的这一套栏目」是否取全、版本是否稳定，
不判断候选站点是否满足要求（那是 candidate_preview 的事）。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

from .content_api import content_snapshot_dict
from .content_service import ContentService
from .content_types import CONTENT_LABELS
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
from .image_service import (
    ImageRenderFailedError,
    ImageServiceClosedError,
    ImageTimeoutError,
    ProfileImageService,
)
from .image_snapshots import (
    DEFAULT_MAX_RENDER_OUTPUT_BYTES,
    ImageSnapshotClosedError,
    ImageSnapshotError,
    ImageSnapshotStore,
)
from .profile_image import (
    FontUnavailableError,
    ImageLimitError,
    ProfileImageError,
)
from .profile_render import render_profile_outputs
from .content_plan import PlanItem, plan_payload
from .tasks import (
    PushTaskService,
    TaskNotFoundError,
    TaskStorageError,
    TaskValidationError,
)
from .sites import (
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
)

_SECTION_IMAGE_STATUSES = frozenset({"success", "empty", "failed"})
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
# 一次组合预览最多做两轮：首轮 + 一次按最终一致 revision 重试。
_MAX_PREVIEW_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class PlanPreviewResult:
    status_code: int
    body: dict[str, Any]


class PlanPreview:
    """把一份栏目方案落到一个站点上取内容，并保存可渲染的图片快照。"""

    def __init__(
        self,
        tasks: PushTaskService,
        content: ContentService,
        sites: SiteService,
        *,
        image_snapshots: ImageSnapshotStore | None = None,
        image_service: ProfileImageService | None = None,
    ) -> None:
        self._tasks = tasks
        self._content = content
        self._sites = sites
        self._image_snapshots = image_snapshots
        self._image_service = image_service


    async def preview_site(
        self,
        payload: object,
        *,
        owner: str | None = None,
        site_id: object = None,
    ) -> PlanPreviewResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        force_refresh = data.get("force_refresh", False)
        if type(force_refresh) is not bool:
            return _error(
                400,
                "force_refresh 必须是布尔值。",
                code="invalid_force_refresh",
            )
        task_id = data.get("task_id")
        if task_id is None:
            # 旧客户端仍可能发送 profile_id；方案行 id 与任务 id 相同。
            task_id = data.get("profile_id")
        if site_id is None:
            site_id = data.get("site_id")
        if site_id is None or site_id == "":
            return _error(
                400,
                "请先选择用于预览的站点。",
                code="site_required",
            )
        try:
            config = await self._tasks.get_config_snapshot(task_id)
            site = await self._sites.get_site(site_id)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _task_not_found_error()
        except TaskStorageError:
            return _storage_error()
        except SiteNotFoundError:
            return _site_not_found_error()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        if not site.enabled:
            return _error(
                409,
                "该站点未启用，无法获取上游内容。",
                code="site_disabled",
            )
        plan_items = config.task.items
        plan_revision = config.plan_revision

        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        # 一次预览的每个栏目都各自重新读取站点，若中途改过站点或组合配置，
        # 收上来的栏目会来自不同 revision。这里在每一轮结束后复核版本：
        # 只要变过就丢弃整轮结果，按最终一致版本重做；超过上限明确失败，
        # 绝不把不同版本的栏目拼进同一个 200 响应。
        for attempt in range(_MAX_PREVIEW_ATTEMPTS):
            sections, failures = await self._collect_sections(
                plan_items, force_refresh, site.id,
            )
            try:
                latest = await self._tasks.get_config_snapshot(task_id)
                latest_site = await self._sites.get_site(site_id)
            except TaskValidationError as exc:
                return _validation_error(exc)
            except TaskNotFoundError:
                return _task_not_found_error()
            except SiteNotFoundError:
                return _site_not_found_error()
            except (TaskStorageError, SiteStorageError):
                return _storage_error()
            except Exception:
                return _internal_error()
            if not latest_site.enabled:
                return _error(
                    409,
                    "该站点未启用，无法获取上游内容。",
                    code="site_disabled",
                )
            if (
                latest.plan_revision == plan_revision
                and latest_site.id == site.id
                and latest_site.revision == site.revision
            ):
                break
            if attempt + 1 >= _MAX_PREVIEW_ATTEMPTS:
                return _error(
                    409,
                    "任务配置在获取期间发生变化，本次预览已取消，请重试。",
                    code="preview_revision_changed",
                )
            plan_items = latest.task.items
            plan_revision = latest.plan_revision
            site = latest_site

        success_count = len(sections) - len(failures)
        data = {
            "task_id": config.task.id,
            "task_name": config.task.name,
            "task_revision": config.task.revision,
            "plan_revision": plan_revision,
            "items": plan_payload(plan_items),
            "site_id": site.id,
            "site_name": site.name,
            "success_count": success_count,
            "failure_count": len(failures),
            "sections": sections,
            "failed_sections": [
                {
                    "content_type": section["content_type"],
                    "label": section["label"],
                    "message": section["message"],
                }
                for section in failures
            ],
        }
        if success_count == 0:
            data["status"] = "failed"
        elif failures:
            data["status"] = "partial"
        else:
            data["status"] = "complete"

        if success_count == 0:
            failed_labels = "、".join(
                section["label"] for section in failures
            ) or "未知栏目"
            return _error(
                502,
                f"所有栏目获取失败：{failed_labels}。",
                code="all_sections_failed",
                data=data,
            )

        data["outputs"] = render_profile_outputs(data)
        if self._image_snapshots is not None:
            try:
                snapshot = self._image_snapshots.put(
                    owner=owner or "",
                    profile_id=config.task.id,
                    profile_revision=plan_revision,
                    site_id=site.id,
                    site_revision=site.revision,
                    data=_image_snapshot_data(data),
                )
            except ImageSnapshotError:
                snapshot = None
            if snapshot is not None:
                data["image_snapshot"] = {
                    "id": snapshot.id,
                    "expires_at": snapshot.expires_at,
                }
        return _ok(data)

    async def _collect_sections(
        self,
        plan_items: tuple[PlanItem, ...],
        force_refresh: bool,
        site_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按给定栏目方案获取全部栏目；失败栏目单独记录，不中断其余栏目。"""
        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for item in plan_items:
            try:
                # 必须使用本次实际选中的站点，否则会出现“标记 B、内容来自 A”。
                cached = await self._content.preview_cached(
                    item.content_type,
                    site_id,
                    force_refresh=force_refresh,
                )
            except Exception as exc:
                section = _section_error(item.content_type, item.label, exc)
                sections.append(section)
                failures.append(section)
                continue

            try:
                snapshot = content_snapshot_dict(
                    item.content_type,
                    cached.snapshot,
                )
            except Exception:
                section = _section_error(
                    item.content_type,
                    item.label,
                    ValueError("serialize failed"),
                )
                sections.append(section)
                failures.append(section)
                continue

            all_items = snapshot.get("items")
            total = len(all_items) if isinstance(all_items, list) else 0
            if item.limit is not None and isinstance(all_items, list):
                snapshot["items"] = all_items[: item.limit]
            shown = (
                len(snapshot["items"])
                if isinstance(snapshot.get("items"), list)
                else 0
            )
            snapshot["item_count"] = shown
            snapshot["total_item_count"] = total
            snapshot["displayed_count"] = shown
            snapshot["limit"] = item.limit
            snapshot["label"] = item.label
            snapshot["status"] = "ok"
            snapshot["cache"] = {
                "hit": cached.hit,
                "expires_at": cached.expires_at,
                "ttl_seconds": self._content.cache_ttl_seconds,
            }
            sections.append(snapshot)

        return sections, failures

    async def generate_image(
        self,
        plan_id: str,
        payload: object,
        *,
        owner: str | None = None,
    ) -> PlanPreviewResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return _error(
                400,
                "snapshot_id 必须是非空字符串。",
                code="invalid_snapshot_id",
            )
        if self._image_snapshots is None or self._image_service is None:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        try:
            snapshot = self._image_snapshots.get(snapshot_id.strip())
        except ImageSnapshotClosedError:
            return _error(
                503,
                "图片快照当前不可用。",
                code="snapshot_unavailable",
            )
        if snapshot is None:
            return _error(
                410,
                "图片快照已过期或被淘汰，请重新预览。",
                code="snapshot_expired",
            )
        if (owner or "") != snapshot.owner:
            return _error(
                403,
                "无权使用该图片快照。",
                code="forbidden",
            )
        if snapshot.profile_id != plan_id:
            return _error(
                404,
                "图片快照不属于该任务。",
                code="snapshot_not_found",
            )
        try:
            config = await self._tasks.get_config_snapshot(plan_id)
        except TaskNotFoundError:
            return _stale_snapshot_error()
        except TaskValidationError:
            return _stale_snapshot_error()
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        # 方案 revision 必须一致；站点则校验快照记录的那一个，
        # 这样故障切换选中的备用站点快照不会被误判为过期。
        if config.plan_revision != snapshot.profile_revision:
            return _stale_snapshot_error()
        try:
            site = await self._sites.get_site(snapshot.site_id)
        except SiteNotFoundError:
            return _stale_snapshot_error()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        if not site.enabled or site.revision != snapshot.site_revision:
            return _stale_snapshot_error()

        try:
            section_images = await self._image_service.render(
                snapshot.data,
                created_at=snapshot.created_at,
            )
        except ImageTimeoutError:
            return _error(
                504,
                "图片生成超时，请稍后重试。",
                code="image_timeout",
            )
        except FontUnavailableError:
            return _error(
                503,
                "宿主缺少可用的中文字体，无法生成图片。",
                code="font_unavailable",
            )
        except ImageLimitError:
            return _error(
                422,
                "图片内容超过高度或大小限制，请减少栏目或条数后重试。",
                code="image_limit_exceeded",
            )
        except ImageServiceClosedError:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        except (ImageRenderFailedError, ProfileImageError):
            return _error(
                422,
                "图片渲染失败，文字和 Markdown 仍可使用。",
                code="image_render_failed",
            )
        except Exception:
            return _error(
                500,
                "图片渲染失败，文字和 Markdown 仍可使用。",
                code="image_internal_error",
            )

        images: list[dict[str, Any]] = []
        failed_sections: list[str] = []
        total_png_bytes = 0
        for position, section_image in enumerate(section_images, start=1):
            entry = _section_image_payload(section_image, position)
            if entry["status"] == "failed":
                failed_sections.append(entry["label"])
                images.append(entry)
                continue
            png = getattr(section_image, "png", None)
            if not isinstance(png, bytes) or not png:
                # 渲染器声明成功却没有图片：按该栏目失败处理，不伪造成功。
                entry["status"] = "failed"
                entry["error"] = "render_failed"
                failed_sections.append(entry["label"])
                images.append(entry)
                continue
            total_png_bytes += len(png)
            if total_png_bytes > DEFAULT_MAX_RENDER_OUTPUT_BYTES:
                return _error(
                    422,
                    "图片总大小超过上限，请减少栏目或条数后重试。",
                    code="image_limit_exceeded",
                )
            entry["png_base64"] = base64.b64encode(png).decode("ascii")
            images.append(entry)
        if not any(entry.get("png_base64") for entry in images):
            # 全部栏目都失败时和「全部栏目获取失败」一致：不返回成功图片。
            return _error(
                422,
                "所有栏目图片生成失败，请减少栏目或条数后重试；文字和 Markdown 仍可使用。",
                code="image_render_failed",
                data={"images": images, "failed_sections": failed_sections},
            )
        return _ok(
            {
                "images": images,
                "failed_sections": failed_sections,
                "snapshot_id": snapshot.id,
            },
        )


def _section_error(
    content_type: str,
    label: str,
    exc: Exception,
) -> dict[str, Any]:
    code, message = _classify_error(exc)
    return {
        "content_type": content_type,
        "label": label,
        "status": "error",
        "code": code,
        "message": message,
    }


def _classify_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, SiteDisabledError):
        return "site_disabled", "站点未启用。"
    if isinstance(exc, SiteNotFoundError):
        return "site_not_found", "站点不存在。"
    if isinstance(exc, NewsTimeoutError):
        return "upstream_timeout", "上游请求超时。"
    if isinstance(exc, NewsTooLargeError):
        return "upstream_too_large", "上游响应超过大小限制。"
    if isinstance(exc, NewsInvalidJSONError):
        return "upstream_invalid_json", "上游返回的内容不是有效 JSON。"
    if isinstance(exc, NewsBusinessError):
        return "upstream_business_error", f"上游返回业务错误（code {exc.code}）。"
    if isinstance(exc, NewsStructureError):
        return "upstream_structure", "上游 JSON 结构不符合接口约定。"
    if isinstance(exc, NewsHTTPStatusError):
        return (
            "upstream_http_error",
            f"上游站点返回 HTTP {exc.status_code}。",
        )
    if isinstance(exc, NewsRedirectError):
        return "upstream_redirect", "上游站点返回重定向，已拒绝跟随。"
    if isinstance(exc, NewsTransportError):
        return "upstream_unreachable", "无法连接上游站点。"
    if isinstance(exc, NewsClientClosedError):
        return "upstream_unavailable", "上游客户端当前不可用。"
    return "internal_error", "栏目获取失败。"


def _section_image_payload(section_image: Any, position: int) -> dict[str, Any]:
    """把一个栏目长图整理成冻结的响应结构，不回显渲染器内部异常。"""
    raw_index = getattr(section_image, "index", position)
    index = raw_index if type(raw_index) is int and raw_index >= 1 else position
    content_type = _safe_text(getattr(section_image, "content_type", ""))
    label = _safe_text(getattr(section_image, "label", "")) or CONTENT_LABELS.get(
        content_type,
        "栏目",
    )
    raw_status = getattr(section_image, "status", "")
    known_status = raw_status in _SECTION_IMAGE_STATUSES
    status = raw_status if known_status else "failed"
    payload: dict[str, Any] = {
        "index": index,
        "content_type": content_type,
        "label": label,
        "status": status,
        "width": _safe_size(getattr(section_image, "width", 0)),
        "height": _safe_size(getattr(section_image, "height", 0)),
    }
    if status == "failed":
        payload["error"] = (
            _safe_error_code(getattr(section_image, "error", None))
            if known_status else "render_failed"
        )
    return payload


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split())[:200]


def _safe_size(value: Any) -> int:
    if type(value) is int and value >= 0:
        return value
    return 0


def _safe_error_code(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if _SAFE_ERROR_CODE.fullmatch(candidate):
            return candidate
    return "render_failed"


def _image_snapshot_data(data: dict[str, Any]) -> dict[str, Any]:
    """只保存渲染需要的已验证数据，不保存 HTML 或前端路径。"""
    return {
        # 渲染器沿用 profile_name 作为标题，这里由任务名映射过去。
        "profile_name": data.get("task_name"),
        "site_name": data.get("site_name"),
        "status": data.get("status"),
        "success_count": data.get("success_count"),
        "failure_count": data.get("failure_count"),
        "sections": data.get("sections"),
    }


def _stale_snapshot_error() -> PlanPreviewResult:
    return _error(
        409,
        "任务或站点配置已变化，请重新预览后再生成图片。",
        code="snapshot_stale",
    )


def _payload_dict(payload: object) -> dict[str, Any] | None:
    return payload if isinstance(payload, dict) else None


def _ok(data: Any, *, status_code: int = 200) -> PlanPreviewResult:
    return PlanPreviewResult(
        status_code=status_code,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    *,
    code: str,
    data: dict[str, Any] | None = None,
) -> PlanPreviewResult:
    details = {"code": code}
    if data:
        details.update(data)
    return PlanPreviewResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _invalid_body_error() -> PlanPreviewResult:
    return _error(
        400,
        "请求体必须是 JSON 对象。",
        code="invalid_body",
    )


def _validation_error(exc: Exception) -> PlanPreviewResult:
    field = getattr(exc, "field", "request")
    return _error(
        400,
        str(exc),
        code="validation_error",
        data={"field": field},
    )


def _task_not_found_error() -> PlanPreviewResult:
    return _error(404, "推送任务不存在或已被删除。", code="not_found")


def _site_not_found_error() -> PlanPreviewResult:
    return _error(404, "引用的站点不存在。", code="site_not_found")


def _storage_error() -> PlanPreviewResult:
    return _error(
        503,
        "配置存储暂不可用，请稍后重试。",
        code="storage_unavailable",
    )


def _internal_error() -> PlanPreviewResult:
    return _error(
        500,
        "服务器内部错误，请稍后重试。",
        code="internal_error",
    )


__all__ = ["PlanPreview", "PlanPreviewResult"]
