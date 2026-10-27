"""推送任务的领域模型、UMO 白名单校验和持久化服务。

本轮只保存配置，不发送消息、不调度、不执行上游请求。任务固定保持未启用。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .profiles import (
    ContentProfileService,
    ProfileNotFoundError,
    ProfileStorageError,
    normalize_profile_id,
)
from .site_store import (
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
    profile_id: str
    output_format: str
    umos: tuple[str, ...]
    enabled: bool
    revision: int
    created_at: str
    updated_at: str

    @property
    def output_label(self) -> str:
        return OUTPUT_FORMAT_LABELS[self.output_format]

    @property
    def target_count(self) -> int:
        return len(self.umos)


@dataclass(frozen=True, slots=True)
class TaskCreate:
    name: object
    profile_id: object
    output_format: object
    umos: object


@dataclass(frozen=True, slots=True)
class TaskUpdate:
    name: object
    profile_id: object
    output_format: object
    umos: object
    expected_revision: object


class PushTaskService:
    """推送任务 CRUD；与站点/组合共用同一个 SQLite 连接和事务锁。"""

    def __init__(
        self,
        profiles: ContentProfileService,
        store: SiteStore,
    ) -> None:
        self._profiles = profiles
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

    async def create_task(self, data: TaskCreate) -> PushTask:
        name = _normalize_task_name(data.name)
        profile_id = _normalize_profile_id(data.profile_id)
        output_format = normalize_output_format(data.output_format)
        umos = normalize_umos(data.umos)
        await self._require_profile(profile_id)
        try:
            stored = await self._store.create_task(
                task_id=uuid.uuid4().hex,
                name=name,
                profile_id=profile_id,
                output_format=output_format,
                umos=umos,
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
        expected_revision = normalize_revision(data.expected_revision)
        name = _normalize_task_name(data.name)
        profile_id = _normalize_profile_id(data.profile_id)
        output_format = normalize_output_format(data.output_format)
        umos = normalize_umos(data.umos)
        await self._require_profile(profile_id)
        try:
            stored = await self._store.update_task(
                normalized_id,
                name=name,
                profile_id=profile_id,
                output_format=output_format,
                umos=umos,
                expected_revision=expected_revision,
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
        normalized_revision = normalize_revision(expected_revision)
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

    async def _require_profile(self, profile_id: str) -> None:
        try:
            await self._profiles.get_profile(profile_id)
        except ProfileNotFoundError:
            raise
        except ProfileStorageError:
            raise


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


def _normalize_task_name(value: object) -> str:
    try:
        return normalize_site_name(value)
    except SiteValidationError as exc:
        raise TaskValidationError("name", exc.reason) from exc


def _normalize_profile_id(value: object) -> str:
    try:
        return normalize_profile_id(value)
    except Exception as exc:
        reason = getattr(exc, "reason", "必须是有效的内容组合 ID")
        raise TaskValidationError("profile_id", str(reason)) from exc


def _to_task(stored: StoredTask) -> PushTask:
    return PushTask(
        id=stored.id,
        name=stored.name,
        profile_id=stored.profile_id,
        output_format=stored.output_format,
        umos=tuple(stored.umos),
        enabled=False,
        revision=stored.revision,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


__all__ = [
    "MAX_TASK_UMOS",
    "MAX_UMO_LENGTH",
    "OUTPUT_FORMAT_LABELS",
    "SUPPORTED_OUTPUT_FORMATS",
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
