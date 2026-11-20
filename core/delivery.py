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
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .candidate_preview import assembled_preview
from .plan_preview import PlanPreview, PlanPreviewResult
from .site_store import (
    DeliveryExecutionExistsError,
    DeliveryRecordNotFoundError,
    SiteStore,
    StoredDeliveryExecution,
)
from .site_requirements import requirements_from_items
from .sites import SiteService
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


class DeliveryConfirmationStaleError(DeliveryError):
    """确认后候选站点状态已变化，必须重新审阅，不得发送。"""


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
    # 内容准备预算：只覆盖候选站点取得内容（含栏目图生成）。
    max_execution_seconds: float = 60.0
    sender_timeout_seconds: float = 15.0
    # 发送预算：内容准备完成后重新计时，不与内容阶段互相挤占。
    max_send_seconds: float = 30.0


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
    # 全部候选都取不到所需内容：提示已发送，但内容采集失败。
    "content_unavailable", "confirmation_stale",
    "preview_failed", "preview_invalid", "profile_not_found",
    "record_after_send_failed", "render_failed",
    "section_image_failed", "send_failed",
    "send_unknown", "sender_budget_exhausted", "sender_cancelled",
    "sender_exception",
    "sender_invalid_payload", "sender_not_found",
    "sender_not_supported", "sender_route_rejected",
    "sender_invalid_result", "sender_timeout", "site_disabled",
    "site_not_found",
    # 发送中断后，同一目标的剩余图片因前一张失败/结果未知而跳过。
    "skipped_after_send_failed", "skipped_after_send_unknown",
    "task_not_found", "too_many_images",
    "too_many_targets",
})


PAGE_STATUS_SUCCESS = "success"
PAGE_STATUS_FAILED = "failed"
PAGE_STATUS_UNKNOWN = "unknown"
PAGE_STATUS_SKIPPED = "skipped"

# 新版图片投递的持久化格式标记：一个栏目一张长图。历史行保持 NULL。
IMAGE_MODEL_SECTIONS = "sections"

# 外层总守卫只在两段预算之外留出收尾余量：阶段内的超时先自行生效，
# 记录落库也还有一点时间，不会被守卫抢先取消。
TOTAL_GUARD_EPSILON_SECONDS = 0.05


class DeliveryService:
    def __init__(
        self,
        *,
        tasks: PushTaskService,
        sites: SiteService,
        plan_preview: PlanPreview,
        store: SiteStore,
        sender: DeliverySender,
        limits: DeliveryLimits | None = None,
    ) -> None:
        self._tasks = tasks
        self._sites = sites
        self._api = plan_preview
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
        expected_candidates: list | None = None,
        expected_alert_only: bool | None = None,
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
                expected_candidates,
                expected_alert_only,
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
        expected_candidates: list | None = None,
        expected_alert_only: bool | None = None,
    ) -> StoredDeliveryExecution:
        await start_gate.wait()
        if self._closed:
            raise DeliveryClosedError("投递服务已关闭。")
        owner_token = uuid.uuid4().hex
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise DeliveryTimeoutError("投递总耗时已超限。")
        # 内容准备和发送各有预算：总守卫放行“内容阶段剩余 + 发送预算”，
        # 两段都不必为对方留时间。
        total_timeout = (
            remaining
            + self._limits.max_send_seconds
            + TOTAL_GUARD_EPSILON_SECONDS
        )
        try:
            return await asyncio.wait_for(
                self._execute_work(
                    task_key, execution_key, owner_token,
                    expected_task_revision,
                    expected_candidates,
                    expected_alert_only,
                    deadline=deadline,
                ),
                timeout=total_timeout,
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
        expected_candidates: list | None = None,
        expected_alert_only: bool | None = None,
        deadline: float | None = None,
    ) -> StoredDeliveryExecution:
        async with self._admission(deadline):
            existing = await self._existing_for_task(execution_key, task_key)
            if existing is not None:
                return existing
            config = await self._load_config(task_key)
            task = config.task
            self._check_revisions(task, expected_task_revision)
            # 首个候选只作为记录里的初始站点占位；真正取用内容的站点在准备
            # 完成后写回，提示内容保持占位以免误导成「该站点已可用」。
            candidates = await self._candidate_sites(task)
            primary = candidates[0] if candidates else None
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
                    profile_id=task.id,
                    profile_revision=config.plan_revision,
                    site_id=primary.id if primary is not None else "",
                    site_revision=primary.revision if primary is not None else 0,
                    output_format=task.output_format,
                    umos=task.umos,
                    owner_token=owner_token,
                    image_model=(
                        IMAGE_MODEL_SECTIONS
                        if task.output_format == "image" else None
                    ),
                )
            except DeliveryExecutionExistsError:
                return await self._existing_for_task(execution_key, task_key)
            except Exception as exc:
                raise DeliveryStorageError("无法创建投递记录。") from exc
            return await self._run(
                execution_key, task, primary,
                expected_candidates, expected_alert_only,
                deadline=deadline,
            )

    @asynccontextmanager
    async def _admission(self, deadline: float | None = None):
        async with self._admission_condition:
            while self._running >= self._limits.max_concurrent_executions:
                if self._pending >= self._limits.max_pending_executions:
                    raise DeliveryCapacityError("投递排队已满。")
                self._pending += 1
                try:
                    if deadline is None:
                        await self._admission_condition.wait()
                    else:
                        # 排队也属于内容准备：内容预算耗尽就结束等待，发送预算
                        # 不替它续命。
                        await asyncio.wait_for(
                            self._admission_condition.wait(),
                            timeout=max(
                                0.0,
                                deadline - asyncio.get_running_loop().time(),
                            ),
                        )
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
            or not valid_timeout(limits.max_send_seconds)
        ):
            raise DeliveryValidationError("limits", "容量/超时配置不合法")

    @staticmethod
    def _check_revisions(task: PushTask, task_revision: int | None) -> None:
        # 站点不上送版本：候选的 id/版本/启用状态由确认快照整体校验。
        # 栏目已属于任务本身，任务 revision 变化即代表内容配置变化。
        if task_revision is not None and task_revision != task.revision:
            raise DeliveryRevisionConflictError(
                "task_revision", task_revision, task.revision,
            )

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
        primary: object,
        expected_candidates: list | None = None,
        expected_alert_only: bool | None = None,
        deadline: float | None = None,
    ) -> StoredDeliveryExecution:
        if task.target_count == 0:
            await self._finish(execution_id, "no_targets", None)
            return await self._read(execution_id)
        if len(task.umos) > self._limits.max_targets:
            await self._finish(execution_id, "failed", "too_many_targets")
            return await self._read(execution_id)
        # 先按完整候选顺序取回全部站点（含停用的），用于与确认快照比对；
        # 只有真正选择内容时才跳过停用站点。
        resolved = await self._candidate_sites(task)
        try:
            self._assert_confirmation(resolved, expected_candidates)
        except DeliveryConfirmationStaleError:
            await self._finish(execution_id, "failed", "confirmation_stale")
            raise
        # 没有候选时同样走告警路径：向任务白名单发送一条文字提示。
        candidates = list(resolved)

        try:
            prepared = await self._prepare(
                execution_id, task, candidates,
                deadline=deadline,
            )
        except DeliveryConfirmationStaleError:
            # 确认后候选状态变了：不发送，记录为明确失败并附安全错误码。
            await self._finish(execution_id, "failed", "confirmation_stale")
            raise
        except DeliveryNoContentError as exc:
            await self._finish(execution_id, "failed", str(exc))
            return await self._read(execution_id)
        if (
            # 只有“确认时为异常提示、现在却拿到了内容”才说明配置变了。
            # 确认时为正常内容、运行时同站全部失败 → 这是内容采集失败，
            # 按确认页已说明的规则发送异常提示，不能误报成配置变化。
            expected_alert_only is True
            and prepared.site is not None
        ):
            await self._finish(execution_id, "failed", "confirmation_stale")
            raise DeliveryConfirmationStaleError()
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
            generated_flags = _slot_image_generated(
                task.output_format, prepared.slots,
            )
            for position, _ in enumerate(task.umos):
                await self._store.add_delivery_pages(
                    execution_id,
                    position=position,
                    page_count=len(prepared.slots),
                    image_generated=generated_flags,
                )
        except Exception as exc:
            raise DeliveryStorageError("无法保存投递计划。") from exc

        # 拼合内容没有单一来源站点：执行记录标记为空站点，表示“多站点”。
        if prepared.multi_site:
            try:
                await self._store.update_delivery_site(
                    execution_id, site_id="", site_revision=0,
                )
            except Exception as exc:
                raise DeliveryStorageError("无法记录投递站点。") from exc
        # 实际选中的站点优先；提示内容没有站点，此时不校验站点配置。
        used_site = prepared.site
        if (
            used_site is not None
            and (primary is None or used_site.id != primary.id)
        ):
            try:
                await self._store.update_delivery_site(
                    execution_id,
                    site_id=used_site.id,
                    site_revision=used_site.revision,
                )
            except Exception as exc:
                raise DeliveryStorageError("无法记录投递站点。") from exc
        record = await self._send_targets(
            execution_id, task, used_site, prepared,
        )
        if prepared.site is None:
            # 采集失败已由 content_status=failed 记录；这里不改写投递结果，
            # 否则会把 unknown/成功统一覆盖成会误导重试的明确失败。
            return record
        return record

    @staticmethod
    def _candidate_shape(candidates: list[object]) -> list[dict]:
        return [
            {
                "id": item.id,
                "revision": item.revision,
            }
            for item in candidates
        ]

    def _assert_confirmation(self, candidates, expected) -> None:
        """候选状态必须与管理员确认时完全一致，否则不发送。"""
        if not isinstance(expected, list):
            return
        confirmed: list[dict] = []
        for item in expected:
            if not isinstance(item, dict):
                raise DeliveryConfirmationStaleError()
            confirmed.append({
                "id": item.get("id"),
                "revision": item.get("revision"),
            })
        if self._candidate_shape(candidates) != confirmed:
            raise DeliveryConfirmationStaleError()
    async def _candidate_sites(self, task: PushTask) -> list[object]:
        """按任务保存的顺序返回仍存在的候选站点。"""
        site_ids = tuple(task.sites)
        resolved: list[object] = []
        for site_id in site_ids:
            try:
                candidate = await self._sites.get_site(site_id)
            except Exception:
                continue
            resolved.append(candidate)
        return resolved

    def _alert_prepared(self) -> "_Prepared":
        # 安全、可理解，且不含凭据、路径或原始上游错误。
        text = (
            "60sAPI推送服务异常：候选站点均无法提供本次任务所需的内容，"
            "请稍后重试或检查站点配置。"
        )
        digest = hashlib.sha256()
        digest.update(b"alert")
        digest.update(text.encode("utf-8"))
        return _Prepared(
            slots=((1, text),),
            content_hash=digest.hexdigest(),
            content_status="failed",
            failed_sections=[],
            site=None,
        )

    async def _prepare(
        self,
        execution_id: str,
        task: PushTask,
        candidates: list[object],
        *,
        deadline: float | None = None,
    ) -> "_Prepared":
        """按顺序寻找同一个站点同时满足全部类别与条数。"""
        prepared = await self._prepare_with_failover(
            execution_id, task, candidates, deadline=deadline,
        )
        if prepared is not None:
            return prepared
        # 全部候选都不满足：发送一条安全提示，不拼凑多站点内容。
        return self._alert_prepared()

    async def _prepare_with_failover(
        self,
        execution_id: str,
        task: PushTask,
        candidates: list[object],
        *,
        deadline: float | None = None,
    ) -> "_Prepared | None":
        """按任务保存的顺序，寻找同一个站点满足全部类别与条数。"""
        requirements = requirements_from_items(task.items)
        by_id = {candidate.id: candidate for candidate in candidates}
        found = await assembled_preview(
            self._api,
            plan_id=task.id,
            requirements=requirements,
            site_ids=[candidate.id for candidate in candidates],
            owner=execution_id,
            # 每站只用剩余时间的一份：慢的首选站点不会吃满总时限。
            deadline=deadline,
        )
        if found is None:
            return None
        site_id, data = found
        multi = data.get("source_mode") == "multi"
        return await self._build_prepared(
            execution_id, task, data,
            None if multi else by_id.get(site_id), deadline=deadline,
        )

    async def _build_prepared(
        self,
        execution_id: str,
        task: PushTask,
        data: dict,
        site: object = None,
        *,
        deadline: float | None = None,
    ) -> "_Prepared":
        """把一次预览结果转换为待发送内容。

        栏目图生成也属于内容准备：它必须落在同一个内容预算内，不能等到
        发送阶段才开始等待，也不能让渲染把发送预算一起吃掉。
        """
        outputs = data.get("outputs")
        raw_failed = data.get("failed_sections") or []
        if not isinstance(raw_failed, list):
            raise DeliveryNoContentError("preview_invalid")
        if task.output_format in ("text", "markdown"):
            if not isinstance(outputs, dict):
                raise DeliveryNoContentError("render_failed")
            body = outputs.get(task.output_format)
            if not isinstance(body, str):
                raise DeliveryNoContentError("render_failed")
            # 文字/Markdown 是一条逻辑消息，只有一张“栏目图”槽位。
            slots: tuple[tuple[int, str | bytes | None], ...] = ((1, body),)
            failed = _merge_failed_sections(raw_failed, [], (), ())
        else:
            snapshot = data.get("image_snapshot")
            if not isinstance(snapshot, dict) or not snapshot.get("id"):
                raise DeliveryNoContentError("image_snapshot_unavailable")
            image = await self._generate_section_images(
                execution_id, task, snapshot["id"], deadline=deadline,
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
            slots, image_failed_entries, image_refs = decoded
            if not any(payload is not None for _, payload in slots):
                # 全部栏目都没生成出图：不发送、不降级、不伪造成功。
                raise DeliveryNoContentError("image_failed")
            failed = _merge_failed_sections(
                raw_failed,
                raw_image_failed,
                image_failed_entries,
                image_refs,
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
            site=site,
            multi_site=data.get("source_mode") == "multi",
        )

    async def _generate_section_images(
        self,
        execution_id: str,
        task: PushTask,
        snapshot_id: object,
        *,
        deadline: float | None,
    ) -> PlanPreviewResult:
        """在内容准备预算内生成栏目图。

        渲染器自己还有一份固定上限；这里只负责“整次内容准备不得超时”。
        等待超时只取消协程，渲染器会设置取消标志，已经进入线程池的同步
        渲染不会被强行终止；失败按既有内容失败语义记录，不发送任何消息。
        """
        if deadline is None:
            return await self._api.generate_image(
                task.id, {"snapshot_id": snapshot_id}, owner=execution_id,
            )
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise DeliveryNoContentError("execution_timeout")
        try:
            return await asyncio.wait_for(
                self._api.generate_image(
                    task.id, {"snapshot_id": snapshot_id},
                    owner=execution_id,
                ),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            raise DeliveryNoContentError("execution_timeout") from None

    async def _send_targets(
        self,
        execution_id: str,
        task: PushTask,
        site: object,
        prepared: "_Prepared",
    ) -> StoredDeliveryExecution:
        statuses: dict[int, str] = {}
        any_unknown = False
        any_failed = False
        any_success = False
        partial_reasons: set[str] = set()
        # 内容已经准备好，从这里开始单独计发送预算；内容阶段没有花完的时间
        # 不顺延，避免慢站点把发送机会挤掉。
        send_deadline = (
            asyncio.get_running_loop().time() + self._limits.max_send_seconds
        )
        slot_total = len(prepared.slots)
        image_failed_slot_count = sum(
            1 for _, payload in prepared.slots if payload is None
        )
        for position, umo in enumerate(task.umos):
            unchanged = await self._config_unchanged(task, site, umo)
            if not unchanged:
                await self._mark_skipped_remainder(
                    execution_id, position, prepared.slots,
                    1, "config_changed",
                )
                await self._mark_target_safely(
                    execution_id, position, status="skipped",
                    error_code="config_changed",
                )
                statuses[position] = "skipped"
                partial_reasons.add("config_changed")
                continue
            try:
                await self._store.set_delivery_target_status(
                    execution_id, position, status="sending",
                )
            except Exception as exc:
                raise DeliveryStorageError("无法保存发送计划。") from exc
            target_status = "success"
            target_error: str | None = None
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
                        task, site, umo,
                    )
                    if not unchanged:
                        await self._mark_skipped_remainder(
                            execution_id, position, prepared.slots,
                            index, "config_changed",
                        )
                        target_status = (
                            "partial" if sent_images else "skipped"
                        )
                        target_error = "config_changed"
                        partial_reasons.add("config_changed")
                        break
                try:
                    await self._store.set_delivery_page_status(
                        execution_id, position, index, status="sending",
                    )
                except Exception as exc:
                    raise DeliveryStorageError("无法保存发送计划。") from exc
                message = DeliveryMessage(
                    umo=umo,
                    # 文字载荷（含异常提示）必须是 text；图片任务也一样，
                    # 不能把提示当成 PNG 交给发送适配器。
                    kind="text" if isinstance(payload, str) else "image",
                    page_index=index,
                    page_count=slot_total,
                    text=payload if isinstance(payload, str) else None,
                    image_png=payload if isinstance(payload, bytes) else None,
                )
                # 最后一次生命周期/配置校验：必须紧邻外部调用，之后不能再写入。
                if self._closed or not await self._config_unchanged(
                    task, site, umo,
                ):
                    await self._mark_skipped_remainder(
                        execution_id, position, prepared.slots,
                        index, "config_changed",
                    )
                    final_target_status = (
                        "partial" if sent_images else "skipped"
                    )
                    await self._mark_target_safely(
                        execution_id, position, status=final_target_status,
                        error_code="config_changed",
                    )
                    target_status = final_target_status
                    target_error = "config_changed"
                    partial_reasons.add("config_changed")
                    break
                try:
                    result = await self._call_sender(
                        message, send_deadline=send_deadline,
                    )
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
                # 目标记录必须能解释自己的终态：已调用后超时/异常与预算
                # 耗尽（未调用）要能从记录区分，且同样经过白名单。
                target_error = _safe_error_code(error_code)
                await self._mark_skipped_remainder(
                    execution_id, position, prepared.slots,
                    index + 1,
                    # 剩余图片是因为这一张失败/结果未知才没有尝试：给稳定、
                    # 可读的原因，不再拼出未登记的错误码被换成 internal_error。
                    "skipped_after_send_unknown"
                    if result.status is SendStatus.UNKNOWN
                    else "skipped_after_send_failed",
                )
                break
            if (
                target_status == "success"
                and sent_images > 0
                and image_failed_slot_count > 0
            ):
                # 部分栏目没有长图、其余已成功送达：目标是部分成功，而不是
                # 全部成功；错误码说明原因，未送达槽位仍逐张留痕。
                target_status = "partial"
                target_error = "section_image_failed"
                partial_reasons.add("section_image_failed")
            await self._mark_target_safely(
                execution_id, position, status=target_status,
                error_code=target_error,
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
        finish_code = None
        if (
            final_status in ("partial", "stopped")
            and partial_reasons == {"config_changed"}
            and not any_failed
            and not any_unknown
        ):
            finish_code = "config_changed"
        elif (
            final_status == "partial"
            and partial_reasons == {"section_image_failed"}
            and not any_failed
            and not any_unknown
        ):
            finish_code = "section_image_failed"
        await self._finish(execution_id, final_status, finish_code)
        return await self._read(execution_id)

    async def _call_sender(
        self,
        message: DeliveryMessage,
        *,
        send_deadline: float,
    ) -> DeliverySendResult:
        # 单次等待取“单次上限”和“发送阶段剩余”的较小值：阶段预算耗尽后
        # 结果未知，按既有 sender timeout 路径处理，绝不重发。
        remaining = send_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            # 发送预算已经用完，这次根本没有调用发送适配器：状态仍是
            # unknown（可能已发出，绝不自动重发），但错误码要能和“已调用
            # 后超时”区分开，否则记录无法说明到底发生了什么。
            return DeliverySendResult(
                SendStatus.UNKNOWN, "sender_budget_exhausted",
            )
        try:
            result = await asyncio.wait_for(
                self._sender.send(message),
                timeout=min(self._limits.sender_timeout_seconds, remaining),
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
        site: object,
        umo: str,
    ) -> bool:
        try:
            current = await self._tasks.get_task(task.id)
            current_site = (
                await self._sites.get_site(site.id) if site is not None else None
            )
        except Exception:
            return False
        # 栏目以任务 revision 与栏目本身双重校验；选中站点单独校验自身
        # revision，版本变化后不得继续发送剩余内容。
        site_ok = True if site is None else (
            current_site is not None
            and current_site.revision == site.revision
        )
        return (
            not self._closed
            and current.revision == task.revision
            and current.items == task.items
            and current.output_format == task.output_format
            and umo in current.umos
            and site_ok
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

    async def _load_config(self, task_id: str):
        """读取任务及其私有栏目方案版本，作为本次投递的配置快照。"""
        try:
            return await self._tasks.get_config_snapshot(task_id)
        except TaskValidationError as exc:
            raise DeliveryValidationError("task_id", exc.reason) from exc
        except TaskNotFoundError as exc:
            raise DeliveryNoContentError("task_not_found") from exc
        except Exception as exc:
            raise DeliveryStorageError("无法读取任务配置。") from exc

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

    async def _mark_skipped_remainder(
        self,
        execution_id: str,
        position: int,
        slots: tuple[tuple[int, str | bytes | None], ...],
        start: int,
        skip_code: str,
    ) -> None:
        """终止发送后统一落盘剩余槽位：图片失败与配置跳过原因分开。"""
        pending_images = [
            index for index, payload in slots
            if index >= start and payload is not None
        ]
        failed_images = [
            index for index, payload in slots
            if index >= start and payload is None
        ]
        if pending_images:
            await self._mark_images(
                execution_id, position, pending_images, "skipped", skip_code,
            )
        if failed_images:
            await self._mark_images(
                execution_id, position, failed_images,
                "skipped", "section_image_failed",
            )

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
    site: object = None
    multi_site: bool = False


ALLOWED_IMAGE_STATUSES = frozenset({"success", "empty", "failed"})
MAX_SECTION_IMAGES = 5
MAX_SECTION_IMAGE_BYTES = 2 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


_MAX_FAILED_SECTIONS = 16
_FAILURE_CODE_RE = re.compile(r"[a-z0-9_]{1,32}")


def _clean_failure_text(value: object, *, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split()).strip()[:limit]


def _failure_reason(value: object) -> str:
    if not isinstance(value, str):
        return ""
    code = value.strip().lower()
    return code if _FAILURE_CODE_RE.fullmatch(code) else ""


def _failure_index(value: object) -> int | None:
    if type(value) is not int or value < 1:
        return None
    return value


def _slot_image_generated(
    output_format: str,
    slots: tuple[tuple[int, str | bytes | None], ...],
) -> tuple[int | None, ...]:
    """建槽位时记录准备阶段事实：有 PNG=1，无图=0，非图片输出/未知=NULL。"""
    if output_format != "image":
        return tuple(None for _ in slots)
    return tuple(
        1 if isinstance(payload, bytes) else 0
        for _, payload in slots
    )


def _decode_section_images(
    raw: object,
) -> tuple[
    tuple[tuple[int, str | bytes | None], ...],
    tuple[dict[str, object], ...],
    tuple[tuple[int, str, str], ...],
] | None:
    """解析 images 为槽位、失败条目与栏目索引表；失败栏目保留 None 槽位。"""
    if not isinstance(raw, list) or not raw:
        return None
    if len(raw) > MAX_SECTION_IMAGES:
        return None
    parsed: list[tuple[int, bytes | None, str | None, str, str | None]] = []
    refs: list[tuple[int, str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        status = item.get("status")
        label = item.get("label")
        content_type = item.get("content_type")
        if type(index) is not int or index < 1:
            return None
        if not isinstance(status, str) or status not in ALLOWED_IMAGE_STATUSES:
            return None
        if not isinstance(label, str) or not label.strip():
            return None
        if not isinstance(content_type, str):
            content_type = ""
        refs.append((index, label.strip(), content_type.strip()[:64]))
        if status == "failed":
            # 渲染器已给出具体原因（高度/字节/字符超限等），失败栏目保留它，
            # 页面才能显示“知乎热榜 · 图片高度超限”这类可行动提示。
            reason = _failure_reason(item.get("error")) or None
            parsed.append((index, None, label.strip(), content_type, reason))
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
        parsed.append((index, png, None, content_type, None))
    parsed.sort(key=lambda entry: entry[0])
    if [entry[0] for entry in parsed] != list(range(1, len(parsed) + 1)):
        return None
    slots = tuple((index, png) for index, png, _, _, _ in parsed)
    failed_entries = tuple(
        {
            "label": label,
            "reason": reason or "image_failed",
            "image_index": index,
        }
        for index, png, label, _, reason in parsed
        if png is None and label
    )
    refs.sort(key=lambda entry: entry[0])
    return slots, failed_entries, tuple(refs)


def _merge_failed_sections(
    preview: list[object],
    image_labels: list[object],
    image_failed: tuple[dict[str, object], ...],
    image_refs: tuple[tuple[int, str, str], ...],
) -> list[dict[str, object]]:
    """合并预览与出图阶段失败栏目，并做安全字段白名单。

    只保留 label / reason / image_index；上游 message、响应正文等一律不落库。
    image_index 仅在能从 images[] 的唯一条目（优先 label，必要时 content_type）
    或条目自身确认时填写，匹配不到就不填。
    """
    refs = [
        (index, label, content_type)
        for index, label, content_type in image_refs
        if _failure_index(index) is not None and label
    ]

    def lookup(label: str, content_type: str) -> int | None:
        matches = [ref for ref in refs if ref[1] == label]
        if len(matches) == 1:
            return matches[0][0]
        if content_type:
            filtered = [
                ref for ref in matches if ref[2] == content_type
            ]
            if len(filtered) == 1:
                return filtered[0][0]
        return None

    merged: list[dict[str, object]] = []

    def merge(raw: object, default_reason: str = "") -> None:
        if isinstance(raw, str):
            label = _clean_failure_text(raw)
            content_type = ""
            reason = ""
            explicit_index = None
        elif isinstance(raw, dict):
            label = _clean_failure_text(raw.get("label"))
            content_type = _clean_failure_text(
                raw.get("content_type"), limit=64,
            )
            reason = _failure_reason(
                raw.get("reason") or raw.get("code"),
            )
            explicit_index = _failure_index(raw.get("image_index"))
        else:
            return
        if not label:
            return
        reason = reason or default_reason
        image_index = (
            explicit_index
            if explicit_index is not None
            else lookup(label, content_type)
        )
        for entry in merged:
            if entry.get("label") != label:
                continue
            known_index = entry.get("image_index")
            if (
                known_index is not None
                and image_index is not None
                and known_index != image_index
            ):
                continue
            if "reason" not in entry and reason:
                entry["reason"] = reason
            if known_index is None and image_index is not None:
                entry["image_index"] = image_index
            return
        if len(merged) >= _MAX_FAILED_SECTIONS:
            return
        item: dict[str, object] = {"label": label}
        if reason:
            item["reason"] = reason
        if image_index is not None:
            item["image_index"] = image_index
        merged.append(item)

    for entry in preview:
        merge(entry)
    for entry in image_labels:
        merge(entry)
    for entry in image_failed:
        merge(entry, default_reason="image_failed")
    return merged


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
