"""推送任务的领域模型、内容栏目校验、UMO 白名单和持久化服务。

内容栏目属于任务自己：每个任务保存一份独立的栏目顺序与每栏条数，
编辑一个任务不会改变其他任务。本轮只保存配置，不发送消息、不调度、
不执行上游请求；新任务固定保持未启用。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .schedule import (
    DailySchedule,
    CronSchedule,
    IntervalSchedule,
    WeeklySchedule,
    ScheduleTimezoneUnavailableError,
    ScheduleValidationError,
    parse_schedule,
)
from .content_plan import (
    PlanItem,
    PlanValidationError,
    normalize_plan_items,
    plan_items_from_stored,
    plan_items_to_stored,
)
from .site_store import (
    SiteRecordNotFoundError,
    SiteStore,
    StoredTask,
    TaskRecordNotFoundError,
    TaskRevisionConflictError,
)
from .sites import (
    SiteValidationError,
    normalize_revision,
    normalize_site_name,
)

MAX_TASK_NAME_LENGTH = 80
MAX_TASK_ID_LENGTH = 64
MAX_TASK_UMOS = 20
MAX_UMO_LENGTH = 512
MAX_PLATFORM_ID_LENGTH = 128
MAX_SESSION_ID_LENGTH = 384
SCHEDULE_UNSET = object()
SUPPORTED_OUTPUT_FORMATS = ("text", "markdown", "image")
OUTPUT_FORMAT_LABELS = {
    "text": "纯文字",
    "markdown": "Markdown",
    "image": "图片",
}
SUPPORTED_MESSAGE_TYPES = ("GroupMessage", "FriendMessage")


class TaskError(Exception):
    """推送任务错误基类。"""


class TaskValidationError(TaskError):
    """推送任务输入不合法。"""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} 无效：{reason}")


class TaskNotFoundError(TaskError):
    """推送任务不存在。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"推送任务 {task_id} 不存在。")


class TaskConflictError(TaskError):
    """推送任务 revision 冲突。"""

    def __init__(
        self,
        task_id: str,
        expected_revision: int,
        actual_revision: int | None,
    ) -> None:
        self.task_id = task_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        actual = "未知" if actual_revision is None else str(actual_revision)
        super().__init__(
            f"推送任务 {task_id} 的 revision 冲突："
            f"期望 {expected_revision}，当前 {actual}。",
        )


class TaskStorageError(TaskError):
    """推送任务存储不可用。"""


@dataclass(frozen=True, slots=True)
class PushTask:
    id: str
    name: str
    items: tuple[PlanItem, ...]
    output_format: str
    umos: tuple[str, ...]
    enabled: bool
    revision: int
    created_at: str
    updated_at: str
    schedule: DailySchedule | WeeklySchedule | IntervalSchedule | CronSchedule | None = None
    schedule_raw: dict[str, object] | None = None
    schedule_error: str | None = None
    sites: tuple[str, ...] = ()

    @property
    def profile_id(self) -> str:
        """兼容字段：执行记录仍以 profile_id 列保存本任务的内容方案 id。

        方案行与任务同 id，且只有这个任务会读取它，因此不存在共享可变组合。
        """
        return self.id

    @property
    def output_label(self) -> str:
        return OUTPUT_FORMAT_LABELS[self.output_format]

    @property
    def target_count(self) -> int:
        return len(self.umos)


@dataclass(frozen=True, slots=True)
class TaskCandidateSite:
    """任务的一个候选站点；顺序即尝试顺序。"""

    id: str
    revision: int | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class TaskConfigSnapshot:
    task: PushTask
    plan_revision: int | None
    candidates: tuple[TaskCandidateSite, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskCreate:
    name: object
    items: object
    output_format: object
    umos: object
    schedule: object = None
    sites: object = None


@dataclass(frozen=True, slots=True)
class TaskUpdate:
    name: object
    items: object
    output_format: object
    umos: object
    expected_revision: object
    schedule: object = SCHEDULE_UNSET
    sites: object = None


class PushTaskService:
    """推送任务 CRUD；与站点共用同一个 SQLite 连接和事务锁。"""

    def __init__(self, store: SiteStore) -> None:
        self._store = store

    async def list_tasks(self) -> list[PushTask]:
        try:
            stored = await self._store.list_tasks()
        except Exception as exc:
            raise TaskStorageError("读取推送任务失败。") from exc
        return [_to_task(item) for item in stored]

    async def get_task(self, task_id: object) -> PushTask:
        normalized_id = normalize_task_id(task_id)
        try:
            stored = await self._store.get_task(normalized_id)
        except TaskRecordNotFoundError as exc:
            raise TaskNotFoundError(exc.task_id) from exc
        except Exception as exc:
            raise TaskStorageError("读取推送任务失败。") from exc
        return _to_task(stored)

    async def get_config_snapshot(self, task_id: object) -> TaskConfigSnapshot:
        normalized_id = normalize_task_id(task_id)
        try:
            stored = await self._store.get_task_config_snapshot(normalized_id)
        except TaskRecordNotFoundError as exc:
            raise TaskNotFoundError(exc.task_id) from exc
        except Exception as exc:
            raise TaskStorageError("读取任务配置快照失败。") from exc
        return TaskConfigSnapshot(
            task=_to_task(stored.task),
            plan_revision=stored.profile_revision,
            candidates=tuple(
                TaskCandidateSite(
                    id=candidate.id,
                    revision=candidate.revision,
                    enabled=candidate.enabled,
                )
                for candidate in stored.candidates
            ),
        )

    async def create_task(self, data: TaskCreate) -> PushTask:
        name = _normalize_task_name(data.name)
        items = _normalize_plan_items(data.items)
        output_format = normalize_output_format(data.output_format)
        umos = normalize_umos(data.umos)
        schedule = parse_schedule(data.schedule)
        site_ids = await self._resolve_site_ids(data.sites)
        try:
            stored = await self._store.create_task(
                task_id=uuid.uuid4().hex,
                name=name,
                items=plan_items_to_stored(items),
                output_format=output_format,
                umos=umos,
                site_ids=site_ids,
                schedule_kind=schedule.kind if schedule else None,
                schedule_timezone=schedule.timezone if schedule else None,
                schedule_time=(
                    schedule.time.strftime("%H:%M")
                    if schedule is not None and hasattr(schedule, "time") else None
                ),
                schedule_weekdays=(
                    ",".join(str(day) for day in schedule.weekdays)
                    if isinstance(schedule, WeeklySchedule) else None
                ),
                schedule_start_at=(
                    schedule.start_at
                    if isinstance(schedule, IntervalSchedule) else None
                ),
                schedule_interval_minutes=(
                    schedule.interval_minutes
                    if isinstance(schedule, IntervalSchedule) else None
                ),
                schedule_expression=(
                    schedule.expression
                    if isinstance(schedule, CronSchedule) else None
                ),
            )
        except Exception as exc:
            raise TaskStorageError("保存推送任务失败。") from exc
        return _to_task(stored)

    async def update_task(
        self,
        task_id: object,
        data: TaskUpdate,
    ) -> PushTask:
        normalized_id = normalize_task_id(task_id)
        expected_revision = _normalize_expected_revision(data.expected_revision)
        name = _normalize_task_name(data.name)
        items = _normalize_plan_items(data.items)
        output_format = normalize_output_format(data.output_format)
        umos = normalize_umos(data.umos)
        schedule_unchanged = data.schedule is SCHEDULE_UNSET
        schedule = None if schedule_unchanged else parse_schedule(data.schedule)
        site_ids = await self._resolve_updated_site_ids(
            normalized_id, data.sites,
        )
        try:
            stored = await self._store.update_task(
                normalized_id,
                name=name,
                items=plan_items_to_stored(items),
                output_format=output_format,
                umos=umos,
                expected_revision=expected_revision,
                site_ids=site_ids,
                schedule_unchanged=schedule_unchanged,
                schedule_kind=schedule.kind if schedule else None,
                schedule_timezone=schedule.timezone if schedule else None,
                schedule_time=(
                    schedule.time.strftime("%H:%M")
                    if schedule is not None and hasattr(schedule, "time") else None
                ),
                schedule_weekdays=(
                    ",".join(str(day) for day in schedule.weekdays)
                    if isinstance(schedule, WeeklySchedule) else None
                ),
                schedule_start_at=(
                    schedule.start_at
                    if isinstance(schedule, IntervalSchedule) else None
                ),
                schedule_interval_minutes=(
                    schedule.interval_minutes
                    if isinstance(schedule, IntervalSchedule) else None
                ),
                schedule_expression=(
                    schedule.expression
                    if isinstance(schedule, CronSchedule) else None
                ),
            )
        except TaskRecordNotFoundError as exc:
            raise TaskNotFoundError(exc.task_id) from exc
        except TaskRevisionConflictError as exc:
            raise TaskConflictError(
                normalized_id,
                expected_revision,
                exc.actual_revision,
            ) from exc
        except Exception as exc:
            raise TaskStorageError("更新推送任务失败。") from exc
        return _to_task(stored)

    async def delete_task(
        self,
        task_id: object,
        expected_revision: object,
    ) -> None:
        normalized_id = normalize_task_id(task_id)
        normalized_revision = _normalize_expected_revision(expected_revision)
        try:
            await self._store.delete_task(
                normalized_id,
                expected_revision=normalized_revision,
            )
        except TaskRecordNotFoundError as exc:
            raise TaskNotFoundError(exc.task_id) from exc
        except TaskRevisionConflictError as exc:
            raise TaskConflictError(
                normalized_id,
                normalized_revision,
                exc.actual_revision,
            ) from exc
        except Exception as exc:
            raise TaskStorageError("删除推送任务失败。") from exc

    async def _resolve_updated_site_ids(
        self,
        task_id: str,
        raw: object,
    ) -> tuple[str, ...] | None:
        """省略 sites 时保持原有候选与顺序；显式传入才替换。"""
        if raw is not None:
            return await self._resolve_site_ids(raw)
        current = await self.get_task(task_id)
        if current.sites:
            return None
        # 组合不再绑定站点：没有候选就无从执行，要求管理员明确选择。
        raise TaskValidationError("sites", "请至少选择一个候选站点")

    async def _resolve_site_ids(
        self,
        raw: object,
    ) -> tuple[str, ...]:
        """候选站点顺序即执行顺序；组合不再提供默认站点。"""
        if raw is None:
            raise TaskValidationError("sites", "请至少选择一个候选站点")
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
            raise TaskValidationError("sites", "必须是站点 id 列表")
        ordered: list[str] = []
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                raise TaskValidationError("sites", "站点 id 必须是非空字符串")
            site_id = item.strip()
            if site_id in ordered:
                continue
            try:
                await self._store.get_site(site_id)
            except SiteRecordNotFoundError:
                raise TaskNotFoundError(site_id) from None
            except Exception as exc:
                # 存储故障不能被伪装成“站点不存在”。
                raise TaskStorageError("读取候选站点失败。") from exc
            ordered.append(site_id)
        if not ordered:
            raise TaskValidationError("sites", "至少需要一个候选站点")
        return tuple(ordered)


def normalize_task_id(value: object) -> str:
    if not isinstance(value, str):
        raise TaskValidationError("task_id", "必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise TaskValidationError("task_id", "不能为空")
    if len(normalized) > MAX_TASK_ID_LENGTH:
        raise TaskValidationError(
            "task_id",
            f"长度不能超过 {MAX_TASK_ID_LENGTH} 个字符",
        )
    if not normalized.replace("_", "").replace("-", "").isalnum():
        raise TaskValidationError(
            "task_id",
            "只能包含字母、数字、下划线和连字符",
        )
    return normalized


def normalize_output_format(value: object) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_OUTPUT_FORMATS:
        raise TaskValidationError(
            "output_format",
            "必须是 text、markdown 或 image",
        )
    return value


def normalize_umos(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TaskValidationError("umos", "必须是字符串数组")
    if len(value) > MAX_TASK_UMOS:
        raise TaskValidationError(
            "umos",
            f"最多填写 {MAX_TASK_UMOS} 个 UMO",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        umo = normalize_umo(raw, field=f"umos[{index}]")
        if umo in seen:
            continue
        seen.add(umo)
        normalized.append(umo)
    return tuple(normalized)


def normalize_umo(value: object, *, field: str = "umo") -> str:
    if not isinstance(value, str):
        raise TaskValidationError(field, "必须是字符串")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TaskValidationError(field, "不能包含控制字符")
    raw = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise TaskValidationError(field, "不能包含控制字符")
    if len(raw) > MAX_UMO_LENGTH:
        raise TaskValidationError(
            field,
            f"长度不能超过 {MAX_UMO_LENGTH} 个字符",
        )
    parts = raw.split(":", 2)
    if len(parts) != 3:
        raise TaskValidationError(
            field,
            "必须是 platform_id:MessageType:session_id 格式，不能填写裸群号或 UID",
        )
    platform_id, message_type, session_id = parts
    if not platform_id or not message_type or not session_id:
        raise TaskValidationError(field, "平台、消息类型和会话 ID 都不能为空")
    if ":" in platform_id or "!" in platform_id:
        raise TaskValidationError(field, "平台适配器实例 ID 不能包含冒号或感叹号")
    if any(char.isspace() for char in platform_id):
        raise TaskValidationError(field, "平台适配器实例 ID 不能包含空白")
    if len(platform_id) > MAX_PLATFORM_ID_LENGTH:
        raise TaskValidationError(
            field,
            f"平台适配器实例 ID 不能超过 {MAX_PLATFORM_ID_LENGTH} 个字符",
        )
    if message_type not in SUPPORTED_MESSAGE_TYPES:
        raise TaskValidationError(
            field,
            "只支持 GroupMessage 或 FriendMessage 消息类型",
        )
    if len(session_id) > MAX_SESSION_ID_LENGTH:
        raise TaskValidationError(
            field,
            f"会话 ID 不能超过 {MAX_SESSION_ID_LENGTH} 个字符",
        )
    # 不重新拼接字段：保存值严格等于整条输入去掉首尾空白后的内容。
    # 因此 session_id 的前导空白、内部空白、冒号和大小写都不会被改写。
    return raw


def _normalize_expected_revision(value: object) -> int:
    try:
        return normalize_revision(value)
    except SiteValidationError as exc:
        raise TaskValidationError("expected_revision", exc.reason) from exc


def _normalize_task_name(value: object) -> str:
    try:
        return normalize_site_name(value)
    except SiteValidationError as exc:
        raise TaskValidationError("name", exc.reason) from exc


def _normalize_plan_items(value: object) -> tuple[PlanItem, ...]:
    try:
        return normalize_plan_items(value)
    except PlanValidationError as exc:
        raise TaskValidationError(exc.field, exc.reason) from exc


def _to_task(stored: StoredTask) -> PushTask:
    sites = tuple(stored.sites)
    schedule = None
    raw = None
    error = None
    if any((
        stored.schedule_kind,
        stored.schedule_timezone,
        stored.schedule_time,
        stored.schedule_weekdays,
        stored.schedule_start_at,
        stored.schedule_interval_minutes,
        stored.schedule_expression,
    )):
        raw = {
            "kind": stored.schedule_kind,
            "timezone": stored.schedule_timezone,
        }
        if stored.schedule_time:
            raw["time"] = stored.schedule_time
        if stored.schedule_weekdays:
            raw["weekdays"] = stored.schedule_weekdays
        if stored.schedule_start_at:
            raw["start_at"] = stored.schedule_start_at
        if stored.schedule_interval_minutes is not None:
            raw["interval_minutes"] = stored.schedule_interval_minutes
        if stored.schedule_expression:
            raw["expression"] = stored.schedule_expression
        try:
            if stored.schedule_weekdays:
                parts = stored.schedule_weekdays.split(",")
                if any(not part or not part.isdigit() for part in parts):
                    raise ValueError("invalid stored weekdays")
                raw["weekdays"] = [int(part) for part in parts]
            schedule = parse_schedule(raw)
        except ValueError:
            error = "schedule_invalid"
            # Keep the original string exactly as stored.
            if stored.schedule_weekdays:
                raw["weekdays"] = stored.schedule_weekdays
        except ScheduleTimezoneUnavailableError:
            error = "schedule_timezone_unavailable"
        except ScheduleValidationError:
            error = "schedule_invalid"
        # Any unexpected exception must propagate rather than masquerade as
        # a missing plan or be silently repaired.
    return PushTask(
        id=stored.id,
        name=stored.name,
        items=plan_items_from_stored(stored.items),
        output_format=stored.output_format,
        umos=tuple(stored.umos),
        sites=sites,
        enabled=stored.enabled,
        revision=stored.revision,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
        schedule=schedule,
        schedule_raw=raw,
        schedule_error=error,
    )


__all__ = [
    "MAX_TASK_UMOS",
    "MAX_UMO_LENGTH",
    "OUTPUT_FORMAT_LABELS",
    "SUPPORTED_OUTPUT_FORMATS",
    "PlanItem",
    "PushTask",
    "PushTaskService",
    "TaskConflictError",
    "TaskCreate",
    "TaskError",
    "TaskNotFoundError",
    "TaskStorageError",
    "TaskUpdate",
    "TaskValidationError",
    "normalize_output_format",
    "normalize_task_id",
    "normalize_umo",
    "normalize_umos",
]
