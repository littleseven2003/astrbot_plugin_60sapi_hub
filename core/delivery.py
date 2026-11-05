"""手动投递核心：内容准备、发送器边界、逐目标逐页记录和失败处理。

本轮只提供可注入假发送器的服务，不在插件启动时装配真实发送器，也不暴露
后台或聊天触发入口。任务保持未启用，保存配置、预览、启动和重载均零发送。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import uuid
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


class DeliveryRevisionConflictError(DeliveryError):
    """确认的配置版本已变化。"""

    def __init__(self, field: str, expected: int, actual: int) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(f"{field} 配置版本已变化")


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
    max_images: int = 6
    max_concurrent_executions: int = 2
    max_pending_executions: int = 4
    max_execution_seconds: float = 60.0
    sender_timeout_seconds: float = 15.0


ALLOWED_SEND_CODES = frozenset({
    "send_failed",
    "send_unknown",
    "sender_cancelled",
    "sender_invalid_payload",
    "sender_not_found",
    "sender_not_supported",
    "sender_route_rejected",
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
    "record_after_send_failed", "render_failed",
    "section_image_failed", "send_failed",
    "send_unknown", "sender_cancelled", "sender_exception",
    "sender_invalid_payload", "sender_not_found",
    "sender_not_supported", "sender_route_rejected",
    "sender_invalid_result", "sender_timeout", "site_disabled",
    "site_not_found", "task_not_found", "too_many_images",
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
        self._validate_limits(self._limits)
        self._owned: set[asyncio.Task[object]] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._admission_condition = asyncio.Condition()
        self._close_task: asyncio.Task[None] | None = None
        self._pending = 0
        self._running = 0

    def supports_umo(self, umo: object) -> bool:
        """发送适配器是否声明支持该 UMO；未知能力按可用处理。"""
        checker = getattr(self._sender, "supports_umo", None)
        if checker is None:
            # Unknown sender capability is not an affirmative declaration.
            return False
        try:
            return bool(checker(umo))
        except Exception:
            return False

    async def execute(
        self,
        task_id: object,
        execution_id: object,
        *,
        expected_task_revision: int | None = None,
        expected_profile_revision: int | None = None,
        expected_site_revision: int | None = None,
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
                expected_task_revision,
                expected_profile_revision,
                expected_site_revision,
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
        expected_task_revision: int | None = None,
        expected_profile_revision: int | None = None,
        expected_site_revision: int | None = None,
    ) -> StoredDeliveryExecution:
        await start_gate.wait()
        if self._closed:
            raise DeliveryClosedError("投递服务已关闭。")
        owner_token = uuid.uuid4().hex
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise DeliveryTimeoutError("投递总耗时已超限。")
        try:
            return await asyncio.wait_for(
                self._execute_work(
                    task_key, execution_key, owner_token,
                    expected_task_revision,
                    expected_profile_revision,
                    expected_site_revision,
                ),
                timeout=remaining,
            )
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            await self._mark_incomplete_if_owner(execution_key, owner_token)
            if isinstance(exc, asyncio.TimeoutError):
                existing = await self._existing_for_task(
                    execution_key, task_key,
                )
                if existing is not None:
                    return existing
                raise DeliveryTimeoutError("投递总耗时已超限。") from exc
            raise

    async def _execute_work(
        self,
        task_key: str,
        execution_key: str,
        owner_token: str,
        expected_task_revision: int | None = None,
        expected_profile_revision: int | None = None,
        expected_site_revision: int | None = None,
    ) -> StoredDeliveryExecution:
        async with self._admission():
            existing = await self._existing_for_task(execution_key, task_key)
            if existing is not None:
                return existing
            task = await self._load_task(task_key)
            profile, site = await self._load_config(task)
            self._check_revisions(
                task, profile, site,
                expected_task_revision,
                expected_profile_revision,
                expected_site_revision,
            )
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
                    owner_token=owner_token,
                )
            except DeliveryExecutionExistsError:
                return await self._existing_for_task(execution_key, task_key)
            except Exception as exc:
                raise DeliveryStorageError("无法创建投递记录。") from exc
            return await self._run(execution_key, task, profile, site)

    @asynccontextmanager
    async def _admission(self):
        async with self._admission_condition:
            while self._running >= self._limits.max_concurrent_executions:
                if self._pending >= self._limits.max_pending_executions:
                    raise DeliveryCapacityError("投递排队已满。")
                self._pending += 1
                try:
                    await self._admission_condition.wait()
                finally:
                    self._pending -= 1
            self._running += 1
        try:
            yield
        finally:
            async with self._admission_condition:
                self._running -= 1
                self._admission_condition.notify_all()

    @staticmethod
    def _validate_limits(limits: DeliveryLimits) -> None:
        def valid_timeout(value: object) -> bool:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            try:
                return value > 0 and math.isfinite(value)
            except OverflowError:
                return False

        if (
            type(limits.max_targets) is not int or limits.max_targets < 1
            or type(limits.max_images) is not int or limits.max_images < 1
            or type(limits.max_concurrent_executions) is not int
            or limits.max_concurrent_executions < 1
            or type(limits.max_pending_executions) is not int
            or limits.max_pending_executions < 0
            or not valid_timeout(limits.max_execution_seconds)
            or not valid_timeout(limits.sender_timeout_seconds)
        ):
            raise DeliveryValidationError("limits", "容量/超时配置不合法")

    @staticmethod
    def _check_revisions(
        task: PushTask,
        profile: object,
        site: object,
        task_revision: int | None,
        profile_revision: int | None,
        site_revision: int | None,
    ) -> None:
        checks = (
            ("task_revision", task_revision, task.revision),
            ("profile_revision", profile_revision, profile.revision),
            ("site_revision", site_revision, site.revision),
        )
        for field, expected, actual in checks:
            if expected is not None and expected != actual:
                raise DeliveryRevisionConflictError(field, expected, actual)

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
        if len(prepared.slots) > self._limits.max_images:
            await self._finish(execution_id, "failed", "too_many_images")
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
                    page_count=len(prepared.slots),
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
        raw_failed = data.get("failed_sections") or []
        if not isinstance(raw_failed, list):
            raise DeliveryNoContentError("preview_invalid")
        failed: list[object] = list(raw_failed)
        if task.output_format in ("text", "markdown"):
            if not isinstance(outputs, dict):
                raise DeliveryNoContentError("render_failed")
            body = outputs.get(task.output_format)
            if not isinstance(body, str):
                raise DeliveryNoContentError("render_failed")
            # 文字/Markdown 是一条逻辑消息，只有一张“栏目图”槽位。
            slots: tuple[tuple[int, str | bytes | None], ...] = ((1, body),)
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
            decoded = _decode_section_images(image_data.get("images"))
            if decoded is None:
                raise DeliveryNoContentError("image_invalid")
            raw_image_failed = image_data.get("failed_sections") or []
            if not isinstance(raw_image_failed, list):
                raise DeliveryNoContentError("image_invalid")
            slots, image_failed_labels = decoded
            if not any(payload is not None for _, payload in slots):
                # 全部栏目都没生成出图：不发送、不降级、不伪造成功。
                raise DeliveryNoContentError("image_failed")
            _merge_failed_sections(
                failed, raw_image_failed, image_failed_labels,
            )
        content_status = data.get("status", "complete")
        if not isinstance(content_status, str):
            raise DeliveryNoContentError("preview_invalid")
        if content_status == "complete" and any(
            payload is None for _, payload in slots
        ):
            content_status = "partial"
        digest = hashlib.sha256()
        digest.update(task.output_format.encode("ascii"))
        for _, payload in slots:
            if payload is None:
                continue
            digest.update(
                payload if isinstance(payload, bytes)
                else payload.encode("utf-8")
            )
        return _Prepared(
            slots=slots,
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
        slot_total = len(prepared.slots)
        sendable = [
            index for index, payload in prepared.slots if payload is not None
        ]
        for position, umo in enumerate(task.umos):
            unchanged = await self._config_unchanged(
                task, profile, site, umo,
            )
            if not unchanged:
                await self._mark_images(
                    execution_id, position, sendable, "skipped",
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
            sent_images = 0
            for index, payload in prepared.slots:
                if payload is None:
                    # 该栏目没有出图：逐张跳过并留痕，不重试、不伪造图片。
                    await self._mark_page_safely(
                        execution_id, position, index,
                        status="skipped", error_code="section_image_failed",
                    )
                    continue
                if sent_images > 0:
                    unchanged = await self._config_unchanged(
                        task, profile, site, umo,
                    )
                    if not unchanged:
                        await self._mark_images(
                            execution_id, position,
                            [item for item in sendable if item >= index],
                            "skipped", "config_changed",
                        )
                        target_status = (
                            "partial" if sent_images else "skipped"
                        )
                        break
                try:
                    await self._store.set_delivery_page_status(
                        execution_id, position, index, status="sending",
                    )
                except Exception as exc:
                    raise DeliveryStorageError("无法保存发送计划。") from exc
                message = DeliveryMessage(
                    umo=umo,
                    kind=task.output_format,
                    page_index=index,
                    page_count=slot_total,
                    text=payload if isinstance(payload, str) else None,
                    image_png=payload if isinstance(payload, bytes) else None,
                )
                # 最后一次生命周期/配置校验：必须紧邻外部调用，之后不能再写入。
                if self._closed or not await self._config_unchanged(
                    task, profile, site, umo,
                ):
                    await self._mark_images(
                        execution_id, position,
                        [item for item in sendable if item >= index],
                        "skipped", "config_changed",
                    )
                    final_target_status = (
                        "partial" if sent_images else "skipped"
                    )
                    await self._mark_target_safely(
                        execution_id, position, status=final_target_status,
                        error_code="config_changed",
                    )
                    target_status = final_target_status
                    break
                try:
                    result = await self._call_sender(message)
                except asyncio.CancelledError:
                    await self._mark_page_safely(
                        execution_id, position, index,
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
                            execution_id, position, index,
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
                    sent_images += 1
                    continue
                error_code = result.code or (
                    "send_unknown" if result.status is SendStatus.UNKNOWN
                    else "send_failed"
                )
                await self._mark_page_safely(
                    execution_id, position, index,
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
                await self._mark_images(
                    execution_id, position,
                    [item for item in sendable if item > index],
                    "skipped", "after_" + error_code,
                )
                break
            await self._mark_target_safely(
                execution_id, position, status=target_status,
            )
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

    async def _mark_images(
        self,
        execution_id: str,
        position: int,
        indexes: list[int],
        status: str,
        error_code: str,
    ) -> None:
        """按栏目图序号逐张写入终态；用于配置变化/失败后的剩余图片。"""
        for index in indexes:
            await self._store.set_delivery_page_status(
                execution_id, position, index,
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

    async def _mark_incomplete_if_owner(
        self,
        execution_id: str,
        owner_token: str,
    ) -> None:
        try:
            existing = await self._read_optional(execution_id)
            if existing is None or existing.owner_token != owner_token:
                return
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
    """一次投递的待发送内容：一个栏目一张图，文字/Markdown 只有一张。"""

    # (展示序号, 载荷)；载荷为 None 表示该栏目没有生成出可发送的图。
    slots: tuple[tuple[int, str | bytes | None], ...]
    content_hash: str
    content_status: str
    failed_sections: list[object]


ALLOWED_IMAGE_STATUSES = frozenset({"success", "empty", "failed"})
MAX_SECTION_IMAGES = 5
MAX_SECTION_IMAGE_BYTES = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _decode_section_images(
    raw: object,
) -> tuple[
    tuple[tuple[int, str | bytes | None], ...],
    tuple[str, ...],
] | None:
    """把按栏目返回的 images 解析为发送槽位；失败栏目保留为 None 槽位。"""
    if not isinstance(raw, list) or not raw:
        return None
    if len(raw) > MAX_SECTION_IMAGES:
        return None
    parsed: list[tuple[int, bytes | None, str | None]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        status = item.get("status")
        label = item.get("label")
        if type(index) is not int or index < 1:
            return None
        if not isinstance(status, str) or status not in ALLOWED_IMAGE_STATUSES:
            return None
        if not isinstance(label, str) or not label.strip():
            return None
        if status == "failed":
            parsed.append((index, None, label.strip()))
            continue
        encoded = item.get("png_base64")
        if not isinstance(encoded, str) or not encoded:
            return None
        try:
            png = base64.b64decode(encoded, validate=True)
        except Exception:
            return None
        if not png.startswith(_PNG_SIGNATURE):
            return None
        if len(png) > MAX_SECTION_IMAGE_BYTES:
            return None
        parsed.append((index, png, None))
    parsed.sort(key=lambda entry: entry[0])
    if [entry[0] for entry in parsed] != list(range(1, len(parsed) + 1)):
        return None
    slots = tuple(
        (index, png) for index, png, _ in parsed
    )
    labels = tuple(
        label for _, png, label in parsed if png is None and label
    )
    return slots, labels


def _merge_failed_sections(
    target: list[object], extra: list[object], labels: tuple[str, ...],
) -> None:
    """合并预览阶段与出图阶段的失败栏目，按标签去重。"""
    seen = {
        entry.get("label") if isinstance(entry, dict) else entry
        for entry in target
    }
    for entry in extra:
        key = entry.get("label") if isinstance(entry, dict) else entry
        if key in seen:
            continue
        target.append(entry)
        seen.add(key)
    for label in labels:
        if label in seen:
            continue
        target.append({"label": label, "reason": "image_failed"})
        seen.add(label)


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


def _safe_error_code(
    value: object,
    default: str = "internal_error",
) -> str | None:
    if value is None:
        return None
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
