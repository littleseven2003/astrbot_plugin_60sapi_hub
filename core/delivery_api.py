"""后台手动测试投递和执行记录查询 API 业务层。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .delivery import (
    DeliveryCapacityError,
    DeliveryClosedError,
    DeliveryNoContentError,
    DeliveryRevisionConflictError,
    DeliveryService,
    DeliveryStorageError,
    DeliveryTimeoutError,
    DeliveryValidationError,
)
from .profiles import (
    ContentProfileService,
    ProfileNotFoundError,
    ProfileStorageError,
)
from .site_store import (
    DeliveryRecordNotFoundError,
    SiteStore,
    StoredDeliveryExecution,
)
from .sites import SiteNotFoundError, SiteService, SiteStorageError
from .tasks import (
    PushTaskService,
    TaskNotFoundError,
    TaskStorageError,
    TaskValidationError,
)


@dataclass(frozen=True, slots=True)
class DeliveryApiResult:
    status_code: int
    body: dict[str, Any]


class DeliveryApi:
    def __init__(
        self,
        *,
        delivery: DeliveryService,
        tasks: PushTaskService,
        profiles: ContentProfileService,
        sites: SiteService,
        store: SiteStore,
        inspector: object,
    ) -> None:
        self._delivery = delivery
        self._tasks = tasks
        self._profiles = profiles
        self._sites = sites
        self._store = store
        self._inspector = inspector

    async def preview(self, task_id: str) -> DeliveryApiResult:
        try:
            task = await self._tasks.get_task(task_id)
            profile = await self._profiles.get_profile(task.profile_id)
            site = await self._sites.get_site(profile.site_id)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _not_found_error("推送任务不存在。")
        except ProfileNotFoundError:
            return _not_found_error("任务引用的内容组合不存在。")
        except SiteNotFoundError:
            return _not_found_error("组合引用的站点不存在。")
        except (TaskStorageError, ProfileStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()

        targets = []
        all_valid = True
        for umo in task.umos:
            try:
                info = self._inspector.inspect_target(umo)  # type: ignore[attr-defined]
            except Exception:
                info = {"valid": False, "reason": "inspection_failed"}
            valid = bool(info.get("valid"))
            all_valid = all_valid and valid
            targets.append({
                "umo": info.get("umo", umo),
                "platform_id": info.get("platform_id"),
                "message_type": info.get("message_type"),
                "target": info.get("target"),
                "isolated": bool(info.get("isolated")),
                "valid": valid,
                "reason": info.get("reason"),
            })
        can_deliver = bool(targets) and site.enabled and all_valid
        reason = None
        if not targets:
            reason = "empty_targets"
        elif not site.enabled:
            reason = "site_disabled"
        elif not all_valid:
            reason = "invalid_targets"
        return _ok({
            "task_id": task.id,
            "task_name": task.name,
            "task_revision": task.revision,
            "profile_id": profile.id,
            "profile_name": profile.name,
            "profile_revision": profile.revision,
            "site_id": site.id,
            "site_name": site.name,
            "site_revision": site.revision,
            "site_enabled": site.enabled,
            "output_format": task.output_format,
            "output_label": task.output_label,
            "targets": targets,
            "can_deliver": can_deliver,
            "reason": reason,
            "content_policy": "fresh",
        })

    async def execute(self, task_id: str, payload: object) -> DeliveryApiResult:
        data = payload if isinstance(payload, dict) else None
        if data is None:
            return _invalid_body_error()
        execution_id = data.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id.strip():
            return _error(400, "execution_id 必须是非空字符串。", code="validation_error", data={"field": "execution_id"})
        revisions = {}
        for field in (
            "expected_task_revision",
            "expected_profile_revision",
            "expected_site_revision",
        ):
            value = data.get(field)
            if type(value) is not int or value < 1:
                return _error(400, f"{field} 必须是正整数。", code="validation_error", data={"field": field})
            revisions[field] = value
        try:
            execution = await self._delivery.execute(
                task_id, execution_id.strip(),
                expected_task_revision=revisions["expected_task_revision"],
                expected_profile_revision=revisions["expected_profile_revision"],
                expected_site_revision=revisions["expected_site_revision"],
            )
        except DeliveryRevisionConflictError as exc:
            return _error(
                409, "确认的配置版本已变化，请重新审阅。",
                code="revision_conflict",
                data={"field": exc.field, "expected": exc.expected, "actual": exc.actual},
            )
        except DeliveryValidationError as exc:
            return _error(400, str(exc), code="validation_error", data={"field": exc.field})
        except (DeliveryNoContentError, DeliveryClosedError) as exc:
            return _error(409, "当前无法开始投递。", code="not_deliverable", data={"reason": str(exc)})
        except DeliveryCapacityError:
            return _error(429, "投递容量已满，请稍后重试。", code="capacity_exhausted")
        except DeliveryTimeoutError:
            return _error(504, "投递请求超时。", code="timeout")
        except DeliveryStorageError:
            return _storage_error()
        except TaskValidationError as exc:
            return _validation_error(exc)
        except Exception:
            return _internal_error()
        return _ok(_execution_dict(execution))

    async def list_executions(
        self,
        *,
        limit: object = 20,
        offset: object = 0,
        task_id: object = None,
    ) -> DeliveryApiResult:
        try:
            parsed_limit = int(limit) if not isinstance(limit, bool) else 0
            parsed_offset = int(offset) if not isinstance(offset, bool) else -1
        except (TypeError, ValueError):
            return _invalid_query_error()
        if not 1 <= parsed_limit <= 50 or parsed_offset < 0:
            return _invalid_query_error()
        if task_id is not None and not isinstance(task_id, str):
            return _invalid_query_error()
        try:
            ids = await self._store.list_delivery_execution_ids(
                limit=parsed_limit,
                offset=parsed_offset,
                task_id=task_id.strip() if isinstance(task_id, str) and task_id.strip() else None,
            )
            executions = []
            for execution_id in ids:
                item = await self._store.get_delivery_execution(execution_id)
                executions.append(_execution_summary(item))
        except DeliveryRecordNotFoundError:
            return _storage_error()
        except Exception:
            return _storage_error()
        return _ok({
            "executions": executions,
            "limit": parsed_limit,
            "offset": parsed_offset,
            "count": len(executions),
            "has_more": len(executions) == parsed_limit,
        })

    async def get_execution(self, execution_id: str) -> DeliveryApiResult:
        try:
            execution = await self._store.get_delivery_execution(execution_id)
        except DeliveryRecordNotFoundError:
            return _not_found_error("投递执行不存在。")
        except Exception:
            return _storage_error()
        return _ok(_execution_dict(execution))


def _execution_summary(e: StoredDeliveryExecution) -> dict[str, Any]:
    failed_sections = _project_failed_sections(e.failed_sections_json)
    delivered, skipped = _image_counts(e)
    return {
        "id": e.id, "task_id": e.task_id, "task_name": e.task_name,
        "output_format": e.output_format, "status": e.status,
        "content_status": e.content_status, "created_at": e.created_at,
        "updated_at": e.updated_at,
        "target_count": len(e.targets),
        "image_count": len(e.pages),
        "target_statuses": sorted({t.status for t in e.targets}),
        "delivered_image_count": delivered,
        "skipped_image_count": skipped,
        "failed_section_count": len(failed_sections),
        "image_partial": _image_partial(e),
    }


def _execution_dict(e: StoredDeliveryExecution) -> dict[str, Any]:
    failed_sections = _project_failed_sections(e.failed_sections_json)
    labels_by_index = {
        entry["image_index"]: entry["label"]
        for entry in failed_sections
        if "image_index" in entry
    }
    images_by_target: dict[int, list[dict[str, Any]]] = {}
    for page in e.pages:
        image: dict[str, Any] = {
            "image_index": page.page_index,
            "status": page.status,
            "error_code": page.error_code,
        }
        label = labels_by_index.get(page.page_index)
        if label:
            image["label"] = label
        images_by_target.setdefault(page.position, []).append(image)
    delivered, skipped = _image_counts(e)
    return {
        "id": e.id,
        "task_id": e.task_id,
        "task_name": e.task_name,
        "task_revision": e.task_revision,
        "profile_id": e.profile_id,
        "profile_revision": e.profile_revision,
        "site_id": e.site_id,
        "site_revision": e.site_revision,
        "output_format": e.output_format,
        "content_status": e.content_status,
        "content_hash": e.content_hash,
        "status": e.status,
        "error_code": e.error_code,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
        "failed_sections": failed_sections,
        "delivered_image_count": delivered,
        "skipped_image_count": skipped,
        "image_partial": _image_partial(e),
        "targets": [
            {
                "position": t.position,
                "umo": t.umo,
                "status": t.status,
                "error_code": t.error_code,
                "delivered_image_count": sum(
                    1 for image in images_by_target.get(t.position, [])
                    if image["status"] == "success"
                ),
                "skipped_image_count": sum(
                    1 for image in images_by_target.get(t.position, [])
                    if image["status"] == "skipped"
                ),
                "images": images_by_target.get(t.position, []),
            }
            for t in e.targets
        ],
    }


_SAFE_CODE_RE = re.compile(r"[a-z0-9_]{1,32}")
_MAX_FAILED_SECTIONS = 16


def _safe_label(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split()).strip()[:120]


def _project_failed_sections(raw: object) -> list[dict[str, Any]]:
    """把历史或当前的失败栏目 JSON 投影成安全字段；坏数据按空列表处理。"""
    if not isinstance(raw, str) or not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in decoded:
        if isinstance(entry, str):
            label = _safe_label(entry)
            reason = None
            image_index = None
        elif isinstance(entry, dict):
            label = _safe_label(entry.get("label"))
            reason_raw = entry.get("reason") or entry.get("code")
            reason = (
                reason_raw
                if isinstance(reason_raw, str)
                and _SAFE_CODE_RE.fullmatch(reason_raw)
                else None
            )
            image_index = entry.get("image_index")
            if type(image_index) is not int or image_index < 1:
                image_index = None
        else:
            continue
        if not label or label in seen:
            continue
        item: dict[str, Any] = {"label": label}
        if reason:
            item["reason"] = reason
        if image_index is not None:
            item["image_index"] = image_index
        result.append(item)
        seen.add(label)
        if len(result) >= _MAX_FAILED_SECTIONS:
            break
    return result


def _image_counts(e: StoredDeliveryExecution) -> tuple[int, int]:
    delivered = sum(1 for page in e.pages if page.status == "success")
    skipped = sum(1 for page in e.pages if page.status == "skipped")
    return delivered, skipped


def _image_partial(e: StoredDeliveryExecution) -> bool:
    delivered, _ = _image_counts(e)
    return delivered > 0 and any(
        page.status == "skipped"
        and page.error_code == "section_image_failed"
        for page in e.pages
    )


def _ok(data: Any, *, status_code: int = 200) -> DeliveryApiResult:
    return DeliveryApiResult(status_code, {"status": "ok", "data": data})


def _error(status_code: int, message: str, *, code: str, data: dict[str, Any] | None = None) -> DeliveryApiResult:
    details = {"code": code}
    if data:
        details.update(data)
    return DeliveryApiResult(status_code, {"status": "error", "message": message, "data": details})


def _invalid_body_error() -> DeliveryApiResult:
    return _error(400, "请求体必须是 JSON 对象。", code="invalid_body")


def _invalid_query_error() -> DeliveryApiResult:
    return _error(400, "分页或任务筛选参数不合法。", code="invalid_query")


def _validation_error(exc: Exception) -> DeliveryApiResult:
    return _error(400, str(exc), code="validation_error", data={"field": getattr(exc, "field", "request")})


def _not_found_error(message: str) -> DeliveryApiResult:
    return _error(404, message, code="not_found")


def _storage_error() -> DeliveryApiResult:
    return _error(503, "投递存储暂不可用。", code="storage_unavailable")


def _internal_error() -> DeliveryApiResult:
    return _error(500, "服务器内部错误。", code="internal_error")
