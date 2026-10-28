"""手动投递核心：内容准备、发送器边界、逐目标逐页记录和失败处理。

本轮只提供可注入假发送器的服务，不在插件启动时装配真实发送器，也不暴露
后台或聊天触发入口。任务保持未启用，保存配置、预览、启动和重载均零发送。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .profile_api import ProfileApi
from .profiles import ContentProfileService, ProfileNotFoundError
from .site_store import (
    DeliveryExecutionExistsError,
    DeliveryRecordNotFoundError,
    SiteStore,
    StoredDeliveryExecution,
)
from .sites import SiteService, SiteNotFoundError
from .tasks import (
    PushTask,
    PushTaskService,
    TaskNotFoundError,
    TaskStorageError,
    TaskValidationError,
)


class DeliveryError(Exception):
    """投递核心错误基类。"""


class DeliveryValidationError(DeliveryError):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} 无效：{reason}")


class DeliveryClosedError(DeliveryError):
    """投递服务已关闭。"""


class DeliveryStorageError(DeliveryError):
    """投递记录存储失败。"""


class DeliveryNoContentError(DeliveryError):
    """内容准备失败，不能投递。"""


class DeliveryCapacityError(DeliveryError):
    """排队/执行容量已满。"""


class DeliveryTimeoutError(DeliveryError):
    """执行在总截止时间内未完成。"""


class SendStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DeliverySendResult:
    status: SendStatus
    code: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryMessage:
    """发送器收到的受控载荷，不接受调用方任意正文/文件。"""

    umo: str
    kind: str
    page_index: int
    page_count: int
    text: str | None = None
    image_png: bytes | None = None


class DeliverySender(Protocol):
    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        ...


@dataclass(frozen=True, slots=True)
class DeliveryLimits:
    max_targets: int = 20
    max_pages: int = 6
    max_concurrent_executions: int = 2
    max_pending_executions: int = 4
    max_execution_seconds: float = 60.0
    sender_timeout_seconds: float = 15.0


ALLOWED_SEND_CODES = frozenset({
    "send_failed",
    "send_unknown",
    "sender_cancelled",
    "sender_exception",
    "sender_invalid_result",
    "sender_timeout",
})
MAX_ERROR_CODE_LENGTH = 64
SAFE_ERROR_CODES = frozenset({
    "all_sections_failed", "cancelled", "config_changed",
    "execution_timeout", "image_failed", "image_invalid",
    "image_render_failed", "image_snapshot_unavailable",
    "image_unavailable", "interrupted", "internal_error",
    "preview_failed", "preview_invalid", "profile_not_found",
    "record_after_send_failed", "render_failed", "send_failed",
    "send_unknown", "sender_cancelled", "sender_exception",
    "sender_invalid_result", "sender_timeout", "site_disabled",
    "site_not_found", "task_not_found", "too_many_pages",
    "too_many_targets",
})


PAGE_STATUS_SUCCESS = "success"
PAGE_STATUS_FAILED = "failed"
PAGE_STATUS_UNKNOWN = "unknown"
PAGE_STATUS_SKIPPED = "skipped"


class DeliveryService:
    def __init__(
        self,
        *,
        tasks: PushTaskService,
        profiles: ContentProfileService,
        sites: SiteService,
        profile_api: ProfileApi,
        store: SiteStore,
        sender: DeliverySender,
        limits: DeliveryLimits | None = None,
    ) -> None:
        self._tasks = tasks
        self._profiles = profiles
        self._sites = sites
        self._api = profile_api
        self._store = store
        self._sender = sender
        self._limits = limits or DeliveryLimits()
        self._closed = False
        self._owned: set[asyncio.Task[object]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._admission_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._pending = 0
        self._semaphore = asyncio.Semaphore(
            self._limits.max_concurrent_executions,
        )

    async def execute(
        self,
        task_id: object,
        execution_id: object,
    ) -> StoredDeliveryExecution:
        if self._closed:
            raise DeliveryClosedError("投递服务已关闭。")
        task_key = _normalize_key(task_id, "task_id", 64)
        execution_key = _normalize_key(execution_id, "execution_id", 128)
        deadline = asyncio.get_running_loop().time() + self._limits.max_execution_seconds
        start_gate = asyncio.Event()
        owned = asyncio.create_task(
            self._owned_execute(
                task_key, execution_key, deadline, start_gate,
            ),
            name=f"delivery-{execution_key}",
        )
        async with self._lifecycle_lock:
            if self._closed:
                owned.cancel()
                await asyncio.gather(owned, return_exceptions=True)
                raise DeliveryClosedError("投递服务已关闭。")
            self._owned.add(owned)
            start_gate.set()
        try:
            return await asyncio.shield(owned)
        except asyncio.CancelledError:
            if not self._closed:
                owned.cancel()
            await asyncio.gather(owned, return_exceptions=True)
            raise
        finally:
            async with self._lifecycle_lock:
                self._owned.discard(owned)

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._close_task is None:
                self._closed = True
                self._close_task = asyncio.create_task(
                    self._shutdown_owned(),
                    name="delivery-close",
                )
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _shutdown_owned(self) -> None:
        async with self._lifecycle_lock:
            owned = tuple(self._owned)
        for task in owned:
            task.cancel()
        if owned:
            await asyncio.gather(*owned, return_exceptions=True)

    async def _owned_execute(
        self,
        task_key: str,
        execution_key: str,
        deadline: float,
        start_gate: asyncio.Event,
    ) -> StoredDeliveryExecution:
        await start_gate.wait()
        if self._closed:
            raise DeliveryClosedError("投递服务已关闭。")
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise DeliveryTimeoutError("投递总耗时已超限。")
        try:
            return await asyncio.wait_for(
                self._execute_work(task_key, execution_key),
                timeout=remaining,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            await self._mark_incomplete_safely(execution_key)
            if isinstance(exc, asyncio.TimeoutError):
                existing = await self._read_optional(execution_key)
                if existing is not None:
                    return existing
                raise DeliveryTimeoutError("投递总耗时已超限。") from exc
            raise

    async def _execute_work(
        self,
        task_key: str,
        execution_key: str,
    ) -> StoredDeliveryExecution:
        existing = await self._existing_for_task(execution_key, task_key)
        if existing is not None:
            return existing
        task = await self._load_task(task_key)
        profile, site = await self._load_config(task)
        async with self._admission():
            if self._closed:
                raise DeliveryClosedError("投递服务已关闭。")
            existing = await self._existing_for_task(execution_key, task_key)
            if existing is not None:
                return existing
            try:
                await self._store.create_delivery_execution(
                    execution_id=execution_key,
                    task_id=task.id,
                    task_name=task.name,
                    task_revision=task.revision,
                    profile_id=profile.id,
                    profile_revision=profile.revision,
                    site_id=site.id,
                    site_revision=site.revision,
                    output_format=task.output_format,
                    umos=task.umos,
                )
            except DeliveryExecutionExistsError:
                return await self._existing_for_task(execution_key, task_key)
            except Exception as exc:
                raise DeliveryStorageError("无法创建投递记录。") from exc
            return await self._run(execution_key, task, profile, site)

    @asynccontextmanager
    async def _admission(self):
        async with self._admission_lock:
            if self._closed:
                raise DeliveryClosedError("投递服务已关闭。")
            if self._pending >= self._limits.max_pending_executions:
                raise DeliveryCapacityError("投递排队已满。")
            self._pending += 1
        acquired = False
        try:
            await self._semaphore.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                self._semaphore.release()
            async with self._admission_lock:
                self._pending -= 1

    async def _existing_for_task(
        self,
        execution_key: str,
        task_key: str,
    ) -> StoredDeliveryExecution | None:
        try:
            existing = await self._store.get_delivery_execution(execution_key)
        except DeliveryRecordNotFoundError:
            return None
        if existing.task_id != task_key:
            raise DeliveryValidationError(
                "execution_id", "该执行标识属于其他任务",
            )
        return existing

    async def _read_optional(self, execution_key: str) -> StoredDeliveryExecution | None:
        try:
            return await self._store.get_delivery_execution(execution_key)
        except DeliveryRecordNotFoundError:
            return None

    async def _run(
        self,
        execution_id: str,
        task: PushTask,
        profile: object,
        site: object,
    ) -> StoredDeliveryExecution:
        if task.target_count == 0:
            await self._finish(execution_id, "no_targets", None)
            return await self._read(execution_id)
        if len(task.umos) > self._limits.max_targets:
            await self._finish(execution_id, "failed", "too_many_targets")
            return await self._read(execution_id)
        if not site.enabled:
            await self._finish(execution_id, "failed", "site_disabled")
            return await self._read(execution_id)

        try:
            prepared = await self._prepare(execution_id, task)
        except DeliveryNoContentError as exc:
            await self._finish(execution_id, "failed", str(exc))
            return await self._read(execution_id)
        if len(prepared.pages) > self._limits.max_pages:
            await self._finish(execution_id, "failed", "too_many_pages")
            return await self._read(execution_id)

        if prepared.failed_sections:
            metadata = json.dumps(prepared.failed_sections, ensure_ascii=False)
        else:
            metadata = None
        try:
            await self._store.set_delivery_content(
                execution_id,
                content_hash=prepared.content_hash,
                content_status=prepared.content_status,
                failed_sections_json=metadata,
                status="running",
            )
            for position, _ in enumerate(task.umos):
                await self._store.add_delivery_pages(
                    execution_id,
                    position=position,
                    page_count=len(prepared.pages),
                )
        except Exception as exc:
            raise DeliveryStorageError("无法保存投递计划。") from exc

        return await self._send_targets(execution_id, task, profile, site, prepared)

    async def _prepare(self, execution_id: str, task: PushTask) -> "_Prepared":
        preview = await self._api.preview_profile(
            {"profile_id": task.profile_id},
            owner=execution_id,
        )
        if preview.status_code != 200:
            code = preview.body.get("data", {}).get("code", "preview_failed")
            raise DeliveryNoContentError(code)
        data = preview.body.get("data")
        if not isinstance(data, dict):
            raise DeliveryNoContentError("preview_invalid")
        outputs = data.get("outputs")
        if task.output_format in ("text", "markdown"):
            if not isinstance(outputs, dict):
                raise DeliveryNoContentError("render_failed")
            body = outputs.get(task.output_format)
            if not isinstance(body, str):
                raise DeliveryNoContentError("render_failed")
            pages = (body,)
        else:
            snapshot = data.get("image_snapshot")
            if not isinstance(snapshot, dict) or not snapshot.get("id"):
                raise DeliveryNoContentError("image_snapshot_unavailable")
            image = await self._api.generate_image(
                task.profile_id,
                {"snapshot_id": snapshot["id"]},
                owner=execution_id,
            )
            if image.status_code != 200:
                code = image.body.get("data", {}).get("code", "image_failed")
                raise DeliveryNoContentError(code)
            image_data = image.body.get("data") or {}
            raw_pages = image_data.get("pages")
            if not isinstance(raw_pages, list) or not raw_pages:
                raise DeliveryNoContentError("image_failed")
            decoded: list[bytes] = []
            for page in raw_pages:
                try:
                    decoded.append(base64.b64decode(page["png_base64"], validate=True))
                except Exception as exc:
                    raise DeliveryNoContentError("image_invalid") from exc
            pages = tuple(decoded)
        content_status = data.get("status", "complete")
        failed = data.get("failed_sections") or []
        if not isinstance(failed, list) or not isinstance(content_status, str):
            raise DeliveryNoContentError("preview_invalid")
        digest = hashlib.sha256()
        digest.update(task.output_format.encode("ascii"))
        for page in pages:
            digest.update(page if isinstance(page, bytes) else page.encode("utf-8"))
        return _Prepared(
            pages=pages,
            content_hash=digest.hexdigest(),
            content_status=content_status,
            failed_sections=failed,
        )

    async def _send_targets(
        self,
        execution_id: str,
        task: PushTask,
        profile: object,
        site: object,
        prepared: "_Prepared",
    ) -> StoredDeliveryExecution:
        statuses: dict[int, str] = {}
        any_unknown = False
        any_failed = False
        any_success = False
        for position, umo in enumerate(task.umos):
            unchanged = await self._config_unchanged(
                task, profile, site, umo,
            )
            if not unchanged:
                await self._mark_remaining(
                    execution_id, position, len(prepared.pages), "skipped",
                    "config_changed",
                )
                await self._mark_target_safely(
                    execution_id, position, status="skipped",
                    error_code="config_changed",
                )
                statuses[position] = "skipped"
                continue
            try:
                await self._store.set_delivery_target_status(
                    execution_id, position, status="sending",
                )
            except Exception as exc:
                raise DeliveryStorageError("无法保存发送计划。") from exc
            target_status = "success"
            for page_index, page in enumerate(prepared.pages, start=1):
                if page_index > 1:
                    unchanged = await self._config_unchanged(
                        task, profile, site, umo,
                    )
                    if not unchanged:
                        await self._mark_remaining(
                            execution_id, position, len(prepared.pages),
                            "skipped", "config_changed", start=page_index,
                        )
                        target_status = "skipped" if page_index == 1 else "partial"
                        break
                try:
                    await self._store.set_delivery_page_status(
                        execution_id, position, page_index, status="sending",
                    )
                except Exception as exc:
                    raise DeliveryStorageError("无法保存发送计划。") from exc
                message = DeliveryMessage(
                    umo=umo,
                    kind=task.output_format,
                    page_index=page_index,
                    page_count=len(prepared.pages),
                    text=page if isinstance(page, str) else None,
                    image_png=page if isinstance(page, bytes) else None,
                )
                # 最后一次生命周期/配置校验：必须紧邻外部调用，之后不能再写入。
                if self._closed or not await self._config_unchanged(
                    task, profile, site, umo,
                ):
                    await self._mark_remaining(
                        execution_id, position, len(prepared.pages),
                        "skipped", "config_changed", start=page_index,
                    )
                    await self._mark_target_safely(
                        execution_id, position, status="skipped",
                        error_code="config_changed",
                    )
                    target_status = "skipped"
                    break
                try:
                    result = await self._call_sender(message)
                except asyncio.CancelledError:
                    await self._mark_page_safely(
                        execution_id, position, page_index,
                        status="unknown", error_code="cancelled",
                    )
                    await self._mark_target_safely(
                        execution_id, position,
                        status="unknown", error_code="cancelled",
                    )
                    raise
                if result.status is SendStatus.SUCCESS:
                    try:
                        await self._store.set_delivery_page_status(
                            execution_id, position, page_index,
                            status="success",
                        )
                    except Exception as exc:
                        await self._finish_safely(
                            execution_id, status="unknown",
                            error_code="record_after_send_failed",
                        )
                        raise DeliveryStorageError(
                            "发送成功后写入投递记录失败。",
                        ) from exc
                    any_success = True
                    continue
                error_code = result.code or (
                    "send_unknown" if result.status is SendStatus.UNKNOWN
                    else "send_failed"
                )
                await self._mark_page_safely(
                    execution_id, position, page_index,
                    status=(
                        "unknown" if result.status is SendStatus.UNKNOWN
                        else "failed"
                    ),
                    error_code=error_code,
                )
                if result.status is SendStatus.UNKNOWN:
                    any_unknown = True
                    target_status = "unknown"
                else:
                    any_failed = True
                    target_status = "failed"
                await self._mark_remaining(
                    execution_id, position, len(prepared.pages),
                    "skipped", "after_" + error_code, start=page_index + 1,
                )
                break
            await self._mark_target_safely(execution_id, position, status=target_status)
            statuses[position] = target_status

        final_status = self._aggregate(statuses)
        if any_unknown:
            final_status = "unknown"
        elif any(status == "partial" for status in statuses.values()):
            final_status = "partial"
        elif any_success and (
            any_failed or "skipped" in statuses.values()
        ):
            final_status = "partial"
        elif any_failed:
            final_status = "failed"
        await self._finish(execution_id, final_status, None)
        return await self._read(execution_id)

    async def _call_sender(self, message: DeliveryMessage) -> DeliverySendResult:
        try:
            result = await asyncio.wait_for(
                self._sender.send(message),
                timeout=self._limits.sender_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return DeliverySendResult(SendStatus.UNKNOWN, "sender_timeout")
        except asyncio.CancelledError:
            raise
        except Exception:
            return DeliverySendResult(SendStatus.UNKNOWN, "sender_exception")
        if not isinstance(result, DeliverySendResult):
            return DeliverySendResult(SendStatus.UNKNOWN, "sender_invalid_result")
        status = _normalize_send_status(result.status)
        if status is SendStatus.SUCCESS:
            return DeliverySendResult(SendStatus.SUCCESS)
        default_code = (
            "send_unknown" if status is SendStatus.UNKNOWN else "send_failed"
        )
        return DeliverySendResult(
            status,
            _safe_error_code(result.code, default_code),
        )

    async def _config_unchanged(
        self,
        task: PushTask,
        profile: object,
        site: object,
        umo: str,
    ) -> bool:
        try:
            current = await self._tasks.get_task(task.id)
            current_profile = await self._profiles.get_profile(task.profile_id)
            current_site = await self._sites.get_site(site.id)
        except Exception:
            return False
        return (
            not self._closed
            and current.revision == task.revision
            and current.profile_id == task.profile_id
            and current.output_format == task.output_format
            and umo in current.umos
            and current_profile.revision == profile.revision
            and current_profile.site_id == site.id
            and current_site.revision == site.revision
            and current_site.enabled
        )

    async def _load_task(self, task_id: str) -> PushTask:
        try:
            return await self._tasks.get_task(task_id)
        except TaskValidationError as exc:
            raise DeliveryValidationError("task_id", exc.reason) from exc
        except TaskNotFoundError as exc:
            raise DeliveryNoContentError("task_not_found") from exc
        except TaskStorageError as exc:
            raise DeliveryStorageError("无法读取任务。") from exc

    async def _load_config(self, task: PushTask) -> tuple[object, object]:
        try:
            profile = await self._profiles.get_profile(task.profile_id)
            site = await self._sites.get_site(profile.site_id)
        except ProfileNotFoundError as exc:
            raise DeliveryNoContentError("profile_not_found") from exc
        except SiteNotFoundError as exc:
            raise DeliveryNoContentError("site_not_found") from exc
        except Exception as exc:
            raise DeliveryStorageError("无法读取任务配置。") from exc
        return profile, site

    async def _mark_page_safely(self, *args, **kwargs) -> None:
        try:
            await self._store.set_delivery_page_status(*args, **kwargs)
        except Exception:
            pass

    async def _mark_target_safely(self, *args, **kwargs) -> None:
        try:
            await self._store.set_delivery_target_status(*args, **kwargs)
        except Exception:
            pass

    async def _mark_remaining(
        self,
        execution_id: str,
        position: int,
        page_count: int,
        status: str,
        error_code: str,
        *,
        start: int = 1,
    ) -> None:
        for page_index in range(start, page_count + 1):
            await self._store.set_delivery_page_status(
                execution_id, position, page_index,
                status=status, error_code=_safe_error_code(error_code),
            )

    async def _finish(self, execution_id: str, status: str, code: str | None) -> None:
        try:
            await self._store.set_delivery_execution_status(
                execution_id, status=status, error_code=_safe_error_code(code),
            )
        except Exception as exc:
            raise DeliveryStorageError("无法保存投递结果。") from exc

    async def _finish_safely(self, execution_id: str, *, status: str, error_code: str) -> None:
        try:
            await self._store.set_delivery_execution_status(
                execution_id, status=status, error_code=_safe_error_code(error_code),
            )
        except Exception:
            pass

    async def _mark_incomplete_safely(self, execution_id: str) -> None:
        try:
            await asyncio.shield(self._store.mark_delivery_incomplete(execution_id))
        except BaseException:
            pass

    async def _read(self, execution_id: str) -> StoredDeliveryExecution:
        try:
            return await self._store.get_delivery_execution(execution_id)
        except Exception as exc:
            raise DeliveryStorageError("无法读取投递记录。") from exc

    @staticmethod
    def _aggregate(statuses: dict[int, str]) -> str:
        if not statuses:
            return "failed"
        if all(status == "success" for status in statuses.values()):
            return "success"
        if all(status == "skipped" for status in statuses.values()):
            return "stopped"
        if any(status == "unknown" for status in statuses.values()):
            return "unknown"
        if any(status == "success" for status in statuses.values()):
            return "partial"
        return "failed"


@dataclass(frozen=True, slots=True)
class _Prepared:
    pages: tuple[str | bytes, ...]
    content_hash: str
    content_status: str
    failed_sections: list[object]


def _normalize_send_status(value: object) -> SendStatus:
    if isinstance(value, SendStatus):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "success":
            return SendStatus.SUCCESS
        if normalized == "failed":
            return SendStatus.FAILED
    return SendStatus.UNKNOWN


def _safe_error_code(value: object, default: str = "internal_error") -> str:
    if isinstance(value, str) and value in SAFE_ERROR_CODES:
        return value
    return default


def _normalize_key(value: object, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise DeliveryValidationError(field, "必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise DeliveryValidationError(field, "不能为空")
    if len(normalized) > max_length:
        raise DeliveryValidationError(field, "长度超出限制")
    if not all(char.isalnum() or char in "_-" for char in normalized):
        raise DeliveryValidationError(field, "只能包含字母、数字、下划线和连字符")
    return normalized


__all__ = [
    "DeliveryClosedError",
    "DeliveryError",
    "DeliveryLimits",
    "DeliveryMessage",
    "DeliveryNoContentError",
    "DeliverySendResult",
    "DeliverySender",
    "DeliveryService",
    "DeliveryStorageError",
    "DeliveryValidationError",
    "SendStatus",
]
