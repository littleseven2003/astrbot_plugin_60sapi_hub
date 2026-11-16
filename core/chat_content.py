"""聊天纯文字查询：授权、配置复核、缓存预览和单条有界回复。"""

from __future__ import annotations

from dataclasses import dataclass

from .candidate_preview import satisfied_previews
from .profile_api import ProfileApi
from .site_requirements import Requirement, requirements_from_items
from .tasks import (
    PushTaskService,
    TaskNotFoundError,
    TaskStorageError,
    TaskValidationError,
    normalize_task_id,
)

MAX_REPLY_CHARS = 2000
AUTH_TEXT = "任务不存在或当前会话无权查询。"
USAGE_TEXT = "用法：/60s get <任务ID>。"
CONFIG_TEXT = "任务配置不可用，请检查组合或站点状态。"
CHANGED_TEXT = "任务或配置已变化，请重新查询。"
FAILED_TEXT = "内容暂时无法获取，请稍后重试。"
TRUNCATED_NOTICE = "\n……（内容过长，已截断，剩余部分未显示）"


@dataclass(frozen=True, slots=True)
class _Authorized:
    task_id: str
    task_revision: int
    profile_id: str
    profile_revision: int
    # 启用候选按尝试顺序排列：(site_id, revision)。顺序变化也算配置变化。
    candidates: tuple[tuple[str, int], ...]
    # 组合的栏目与条数要求；同一站点必须全部满足才算可用。
    requirements: tuple[Requirement, ...]


class ChatContentQuery:
    """只读复用组合预览，不发送、不渲染图片、不写配置。"""

    def __init__(
        self,
        tasks: PushTaskService,
        previews: ProfileApi,
    ) -> None:
        self._tasks = tasks
        self._previews = previews

    async def query(self, umo: str, task_id: object) -> str:
        try:
            normalized_id = normalize_task_id(task_id)
        except TaskValidationError:
            return USAGE_TEXT
        try:
            authorized = await authorize_task(self._tasks, umo, normalized_id)
        except _Unauthorized:
            return AUTH_TEXT
        except _Unavailable:
            return CONFIG_TEXT
        text = await _first_available(self._previews, authorized)
        if text is None:
            return FAILED_TEXT
        if await config_changed(self._tasks, umo, authorized):
            return CHANGED_TEXT
        return _truncate(text)

async def authorize_task(
    tasks: PushTaskService,
    umo: str,
    task_id: str,
) -> _Authorized:
    try:
        snapshot = await tasks.get_config_snapshot(task_id)
    except TaskNotFoundError:
        raise _Unauthorized from None
    except TaskStorageError:
        raise _Unavailable from None
    task = snapshot.task
    if umo not in task.umos:
        raise _Unauthorized
    candidates = tuple(
        (item.id, item.revision)
        for item in snapshot.candidates
        if item.enabled is True and item.revision is not None
    )
    if (snapshot.profile_id is None
            or snapshot.profile_revision is None
            or not candidates):
        raise _Unavailable
    # revision 为 None 的候选已被删除，无法复核版本，不能用于查询。
    resolved = tuple(
        (site_id, int(revision)) for site_id, revision in candidates
    )
    try:
        profile = await tasks.get_profile(snapshot.profile_id)
        requirements = requirements_from_items(profile.items)
    except Exception:
        raise _Unavailable from None
    return _Authorized(
        task_id=task.id,
        task_revision=task.revision,
        profile_id=snapshot.profile_id,
        profile_revision=snapshot.profile_revision,
        candidates=resolved,
        requirements=requirements,
    )


async def config_changed(
    tasks: PushTaskService,
    umo: str,
    before: _Authorized,
) -> bool:
    try:
        snapshot = await tasks.get_config_snapshot(before.task_id)
    except Exception:
        return True
    current = tuple(
        (item.id, item.revision)
        for item in snapshot.candidates
        if item.enabled is True and item.revision is not None
    )
    return (
        umo not in snapshot.task.umos
        or snapshot.task.revision != before.task_revision
        or snapshot.profile_id != before.profile_id
        or snapshot.profile_revision != before.profile_revision
        # 顺序、成员、启用状态或站点版本任一变化都要求重新查询。
        or current != before.candidates
    )


async def _first_available(
    previews: ProfileApi,
    authorized: _Authorized,
) -> str | None:
    """按任务候选顺序取第一份站内满足全部栏目的内容；全部失败返回 None。"""
    async for _site_id, data in satisfied_previews(
        previews,
        profile_id=authorized.profile_id,
        requirements=authorized.requirements,
        site_ids=[site_id for site_id, _ in authorized.candidates],
        catch_errors=True,
    ):
        outputs = data.get("outputs")
        text = outputs.get("text") if isinstance(outputs, dict) else None
        if isinstance(text, str) and text.strip():
            return text
    return None


class _Unauthorized(Exception):
    pass


class _Unavailable(Exception):
    pass


def _truncate(text: str) -> str:
    if len(text) <= MAX_REPLY_CHARS:
        return text
    keep = MAX_REPLY_CHARS - len(TRUNCATED_NOTICE)
    return text[:keep].rstrip() + TRUNCATED_NOTICE


__all__ = [
    "AUTH_TEXT",
    "CONFIG_TEXT",
    "FAILED_TEXT",
    "MAX_REPLY_CHARS",
    "TRUNCATED_NOTICE",
    "USAGE_TEXT",
    "ChatContentQuery",
]
