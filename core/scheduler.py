"""任务调度：显式启停、到期认领和单循环执行。

支持每日、每周、固定间隔和五字段 Cron，复用投递服务；不自动重试或离线补发。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .delivery import (
    DeliveryCapacityError,
    DeliveryClosedError,
    DeliveryError,
    DeliveryService,
)
from .profiles import ProfileStorageError
from .schedule import (
    DailySchedule,
    ScheduleCalculationError,
    ScheduleTimezoneUnavailableError,
    ScheduleValidationError,
    next_scheduled_run,
)
from .site_store import (
    ScheduleState,
    SiteStore,
    StoredTaskConfigSnapshot,
    TaskOperationStaleError,
    TaskRecordNotFoundError,
    TaskRevisionConflictError,
)
from .tasks import (
    PushTaskService,
    TaskConflictError,
    TaskNotFoundError,
    TaskStorageError,
)

LATE_WINDOW_SECONDS = 60
LOOP_WAIT_SECONDS = 30.0


class SchedulerError(Exception):
    """调度错误基类。"""


class SchedulerValidationError(SchedulerError):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} 无效：{reason}")


class SchedulerUnavailableError(SchedulerError):
    """运行所需依赖不可用。"""


class SchedulerService:
    """单个有界循环；核心 check_once 可直接用固定时钟测试。"""

    def __init__(
        self,
        *,
        tasks: PushTaskService,
        store: SiteStore,
        delivery: DeliveryService,
        clock=None,
        late_window_seconds: int = LATE_WINDOW_SECONDS,
        max_concurrent: int = 1,
    ) -> None:
        self._tasks = tasks
        self._store = store
        self._delivery = delivery
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._late_window = timedelta(seconds=late_window_seconds)
        self._max_concurrent = max(1, max_concurrent)
        self._active: set[str] = set()
        self._operations: set[asyncio.Task[object]] = set()
        self._checking = False
        self._loop_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def next_run_at(self) -> str | None:
        """仅用于测试/诊断；真实值以 store 查询为准。"""
        return None

    async def enable_task(self, task_id: object, expected_revision: object) -> object:
        return await self._owned_operation(self._enable_task, task_id, expected_revision)

    async def _enable_task(self, task_id: object, expected_revision: object) -> object:
        self._ensure_open()
        task = await self._tasks.get_task(task_id)
        revision = _revision(expected_revision)
        if task.schedule is None:
            raise SchedulerValidationError(
                "schedule", task.schedule_error or "每日计划未配置",
            )
        if not task.umos:
            raise SchedulerValidationError("umos", "白名单不能为空")
        try:
            snapshot = await self._tasks.get_config_snapshot(task.id)
        except (TaskStorageError, ProfileStorageError) as exc:
            raise SchedulerUnavailableError("无法读取任务配置快照。") from exc
        self._validate_snapshot(snapshot)
        next_run = self._next_run(task.schedule, self._now())
        try:
            enabled = await self._store.set_task_enabled(
                task.id,
                enabled=True,
                expected_revision=revision,
                next_run_at=next_run.isoformat(),
                now=self._now().isoformat(),
                is_valid=self._valid_now,
            )
        except TaskRevisionConflictError as exc:
            raise TaskConflictError(task.id, revision, exc.actual_revision) from exc
        except TaskOperationStaleError as exc:
            raise SchedulerUnavailableError("调度服务已关闭。") from exc
        except TaskOperationStaleError as exc:
            raise SchedulerUnavailableError("调度服务已关闭。") from exc
        except TaskRecordNotFoundError as exc:
            raise TaskNotFoundError(exc.task_id) from exc
        self._wake.set()
        return await self._tasks.get_task(task.id)

    async def disable_task(self, task_id: object, expected_revision: object) -> object:
        return await self._owned_operation(self._disable_task, task_id, expected_revision)

    async def _disable_task(self, task_id: object, expected_revision: object) -> object:
        self._ensure_open()
        task = await self._tasks.get_task(task_id)
        revision = _revision(expected_revision)
        try:
            disabled = await self._store.set_task_enabled(
                task.id,
                enabled=False,
                expected_revision=revision,
                next_run_at=None,
                now=self._now().isoformat(),
                is_valid=self._valid_now,
            )
        except TaskRevisionConflictError as exc:
            raise TaskConflictError(task.id, revision, exc.actual_revision) from exc
        except TaskRecordNotFoundError as exc:
            raise TaskNotFoundError(exc.task_id) from exc
        self._wake.set()
        return await self._tasks.get_task(task.id)

    async def list_tracking(self, *, limit: int, offset: int) -> dict:
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        claims = await self._store.list_schedule_claims(
            limit=limit, offset=offset,
        )
        skips = await self._store.list_schedule_skips(
            limit=limit, offset=offset,
        )
        return {
            "limit": limit,
            "offset": offset,
            "claims": [
                {
                    "task_id": item.task_id,
                    "scheduled_for": item.scheduled_for,
                    "execution_id": item.execution_id,
                    "status": item.status,
                    "task_revision": item.task_revision,
                    "profile_revision": item.profile_revision,
                    "site_revision": item.site_revision,
                    "error_code": item.error_code,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                }
                for item in claims
            ],
            "skips": [
                {
                    "task_id": item.task_id,
                    "scheduled_for": item.scheduled_for,
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
                for item in skips
            ],
        }

    async def _owned_operation(self, operation, *args) -> object:
        self._ensure_open()
        current = asyncio.current_task()
        if current is None:
            raise SchedulerUnavailableError("调度服务不可用。")
        self._operations.add(current)
        try:
            return await operation(*args)
        finally:
            self._operations.discard(current)

    def _valid_now(self) -> bool:
        return not self._closed

    async def _ensure_open_after_wait(self) -> None:
        if self._closed:
            raise SchedulerUnavailableError("调度服务已关闭。")

    def _ensure_open(self) -> None:
        if self._closed:
            raise SchedulerUnavailableError("调度服务已关闭。")

    async def runtime_status(self) -> dict[str, str]:
        try:
            states = await self._store.list_schedule_states()
        except Exception:
            return {}
        return {state.task_id: state.next_run_at for state in states}

    async def check_once(self, now: datetime | None = None) -> int:
        """检查一次到期任务，返回认领数量。调用方可注入固定时钟。"""
        scan_now = _as_utc(now or self._clock())
        due = await self._store.list_due_schedule_states(scan_now.isoformat())
        if self._checking:
            current = self._now()
            for state in due:
                if _state_time(state.next_run_at) <= current:
                    await self._skip(state, "capacity", current)
            return 0
        self._checking = True
        claimed_count = 0
        try:
            for state in due:
                if self._closed:
                    break
                # Do not reuse the batch snapshot. Configuration reads and the
                # previous task may take time; the late window is per task.
                current = self._now()
                if _state_time(state.next_run_at) > current:
                    # The scan saw a due slot, but the clock moved backward
                    # while earlier work was running. Keep the future plan.
                    continue
                if len(self._active) >= self._max_concurrent:
                    await self._skip(state, "capacity", current)
                    continue
                if state.task_id in self._active:
                    await self._skip(state, "overlap", current)
                    continue
                claimed = await self._process_state(state, current)
                if claimed:
                    claimed_count += 1
            return claimed_count
        finally:
            self._checking = False

    async def _process_state(self, state: ScheduleState, now: datetime) -> bool:
        try:
            snapshot = await self._tasks.get_config_snapshot(state.task_id)
        except Exception:
            await self._skip(state, "config_unavailable", now)
            return False
        task = snapshot.task
        schedule = task.schedule
        current = self._now()
        if schedule is None:
            await self._skip(
                state,
                "schedule_invalid" if task.schedule_error else "disabled",
                current,
            )
            return False
        if not task.enabled:
            await self._skip(state, "disabled", current)
            return False
        try:
            self._validate_snapshot(snapshot)
        except SchedulerValidationError as exc:
            await self._skip(state, exemplify(exc.field), current)
            return False
        scheduled = _state_time(state.next_run_at)
        if current < scheduled:
            # Configuration reads may overlap a backward clock adjustment.
            # Do not claim or skip a future slot.
            return False
        late = current - scheduled
        if late > self._late_window:
            await self._skip(state, "late", current)
            return False
        next_run = self._next_run(schedule, current)
        execution_id = _execution_id(task.id, scheduled)
        self._active.add(task.id)
        try:
            claimed = await self._store.claim_schedule(
                task_id=task.id,
                scheduled_for=state.next_run_at,
                execution_id=execution_id,
                task_revision=task.revision,
                profile_revision=snapshot.profile_revision or 0,
                site_revision=snapshot.site_revision or 0,
                next_run_at=next_run.isoformat(),
                now=current.isoformat(),
            )
            if not claimed:
                return False
            try:
                result = await self._delivery.execute(
                    task.id,
                    execution_id,
                    expected_task_revision=task.revision,
                    expected_profile_revision=snapshot.profile_revision,
                    expected_site_revision=snapshot.site_revision,
                )
                if any(target.status == "unknown" for target in result.targets):
                    status = "unknown"
                elif result.status == "success":
                    status = "succeeded"
                else:
                    status = "failed"
                error_code = result.error_code
            except asyncio.CancelledError:
                await self._store.set_schedule_claim_status(
                    execution_id, status="unknown", error_code="cancelled",
                    now=self._now().isoformat(),
                )
                raise
            except DeliveryCapacityError:
                await self._store.set_schedule_claim_status(
                    execution_id, status="skipped", error_code="capacity",
                    now=self._now().isoformat(),
                )
                await self._record_claim_skip(task.id, state, "capacity", now)
                return False
            except DeliveryClosedError:
                await self._store.set_schedule_claim_status(
                    execution_id, status="unknown", error_code="interrupted",
                    now=self._now().isoformat(),
                )
                raise
            except DeliveryError as exc:
                code = "delivery_failed"
                if getattr(exc, "field", "") == "revision":
                    code = "config_changed"
                await self._store.set_schedule_claim_status(
                    execution_id, status="unknown", error_code=code,
                    now=self._now().isoformat(),
                )
                return False
            except Exception:
                await self._store.set_schedule_claim_status(
                    execution_id, status="unknown", error_code="internal_error",
                    now=self._now().isoformat(),
                )
                return False
            await self._store.set_schedule_claim_status(
                execution_id, status=status, error_code=error_code,
                now=self._now().isoformat(),
            )
            return True
        finally:
            self._active.discard(task.id)

    async def _skip(self, state: ScheduleState, reason: str, now: datetime) -> None:
        if _state_time(state.next_run_at) > now:
            # Stale scan after a backward clock adjustment: preserve future.
            return
        try:
            task = await self._tasks.get_task(state.task_id)
        except Exception:
            return
        schedule = task.schedule
        if schedule is None:
            next_run = now + timedelta(days=1)
        else:
            try:
                next_run = self._next_run(schedule, now)
            except SchedulerError:
                next_run = now + timedelta(days=1)
        try:
            await self._store.record_schedule_skip(
                task_id=state.task_id,
                scheduled_for=state.next_run_at,
                next_run_at=next_run.isoformat(),
                reason=reason,
                now=now.isoformat(),
            )
        except Exception:
            pass

    async def _record_claim_skip(
        self, task_id: str, state: ScheduleState, reason: str, now: datetime,
    ) -> None:
        try:
            await self._store.record_schedule_skip(
                task_id=task_id,
                scheduled_for=state.next_run_at,
                next_run_at=state.next_run_at,
                reason=reason,
                now=now.isoformat(),
            )
        except Exception:
            pass

    def _validate_snapshot(self, snapshot: StoredTaskConfigSnapshot) -> None:
        task = snapshot.task
        if snapshot.profile_id is None:
            raise SchedulerValidationError("profile", "内容组合不存在")
        if snapshot.site_id is None or not snapshot.site_enabled:
            raise SchedulerValidationError("site", "站点不存在或已停用")
        if not task.umos:
            raise SchedulerValidationError("umos", "白名单不能为空")
        if task.schedule is None:
            raise SchedulerValidationError(
                "schedule", task.schedule_error or "每日计划无效",
            )
        for umo in task.umos:
            if not self._delivery.supports_umo(umo):
                raise SchedulerValidationError("umos", "发送适配器不支持目标")

    def _next_run(self, schedule: DailySchedule, now: datetime) -> datetime:
        try:
            return next_scheduled_run(schedule, now)
        except ScheduleTimezoneUnavailableError as exc:
            raise SchedulerValidationError("schedule", str(exc)) from exc
        except ScheduleCalculationError as exc:
            raise SchedulerValidationError("schedule", str(exc)) from exc
        except ScheduleValidationError as exc:
            raise SchedulerValidationError("schedule", str(exc)) from exc

    def _now(self) -> datetime:
        return _as_utc(self._clock())

    async def start(self) -> None:
        if self._started:
            return
        self._closed = False
        self._started = True
        await self._resync_after_start()
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="60sapi-scheduler-loop",
        )

    async def _resync_after_start(self) -> None:
        """启动/重载恢复：丢弃已经过去的计划，不补发。"""
        now = self._now()
        try:
            states = await self._store.list_schedule_states()
        except Exception:
            return
        for state in states:
            try:
                task = await self._tasks.get_task(state.task_id)
            except Exception:
                continue
            if task.schedule is None:
                continue
            try:
                next_run = self._next_run(task.schedule, now)
            except SchedulerError:
                continue
            if _state_time(state.next_run_at) <= now:
                try:
                    await self._store.advance_schedule(
                        task.id,
                        scheduled_for=state.next_run_at,
                        next_run_at=next_run.isoformat(),
                        now=now.isoformat(),
                    )
                except Exception:
                    pass

    async def _run_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    await self.check_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                if self._closed:
                    break
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=LOOP_WAIT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    raise
                self._wake.clear()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        self._wake.set()
        loop_task = self._loop_task
        self._loop_task = None
        operations = tuple(self._operations)
        for operation in operations:
            if operation is not asyncio.current_task():
                operation.cancel()
        pending = [
            item for item in operations if item is not asyncio.current_task()
        ]
        if loop_task is not None:
            loop_task.cancel()
            pending.append(loop_task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._active.clear()


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("调度时钟必须返回带时区的 datetime")
    return value.astimezone(timezone.utc)


def _state_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise SchedulerValidationError("expected_revision", "必须是正整数")
    return value


def _execution_id(task_id: str, scheduled: datetime) -> str:
    return f"sched-{task_id}-{scheduled.strftime('%Y%m%d%H%M%S')}"


def exemplify(field: str) -> str:
    return {
        "profile": "profile_invalid",
        "site": "site_disabled",
        "umos": "umos_invalid",
        "schedule": "schedule_invalid",
        "enabled": "disabled",
    }.get(field, "config_invalid")
