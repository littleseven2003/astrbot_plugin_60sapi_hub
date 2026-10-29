"""聊天纯文字查询：授权、配置复核、缓存预览和单条有界回复。"""

from __future__ import annotations

from dataclasses import dataclass

from .profile_api import ProfileApi
from .profiles import (
    ContentProfileService,
    ProfileNotFoundError,
    ProfileStorageError,
)
from .sites import SiteNotFoundError, SiteService, SiteStorageError
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
    site_id: str
    site_revision: int


class ChatContentQuery:
    """只读复用组合预览，不发送、不渲染图片、不写配置。"""

    def __init__(
        self,
        tasks: PushTaskService,
        profiles: ContentProfileService,
        sites: SiteService,
        previews: ProfileApi,
    ) -> None:
        self._tasks = tasks
        self._profiles = profiles
        self._sites = sites
        self._previews = previews

    async def query(self, umo: str, task_id: object) -> str:
        try:
            normalized_id = normalize_task_id(task_id)
        except TaskValidationError:
            return USAGE_TEXT
        try:
            authorized = await self._authorize(umo, normalized_id)
        except _Unauthorized:
            return AUTH_TEXT
        except _Unavailable:
            return CONFIG_TEXT
        try:
            result = await self._previews.preview_profile({
                "profile_id": authorized.profile_id,
                "force_refresh": False,
            })
        except Exception:
            return FAILED_TEXT
        if result.status_code != 200:
            return FAILED_TEXT
        data = result.body.get("data") if isinstance(result.body, dict) else None
        outputs = data.get("outputs") if isinstance(data, dict) else None
        text = outputs.get("text") if isinstance(outputs, dict) else None
        if not isinstance(text, str) or not text.strip():
            return FAILED_TEXT
        if await self._config_changed(umo, authorized):
            return CHANGED_TEXT
        return _truncate(text)

    async def _authorize(self, umo: str, task_id: str) -> _Authorized:
        try:
            task = await self._tasks.get_task(task_id)
        except TaskNotFoundError:
            raise _Unauthorized from None
        except TaskStorageError:
            raise _Unavailable from None
        if umo not in task.umos:
            raise _Unauthorized
        try:
            profile = await self._profiles.get_profile(task.profile_id)
            site = await self._sites.get_site(profile.site_id)
        except (ProfileNotFoundError, SiteNotFoundError):
            raise _Unavailable from None
        except (ProfileStorageError, SiteStorageError):
            raise _Unavailable from None
        if not site.enabled:
            raise _Unavailable
        return _Authorized(
            task_id=task.id,
            task_revision=task.revision,
            profile_id=profile.id,
            profile_revision=profile.revision,
            site_id=site.id,
            site_revision=site.revision,
        )

    async def _config_changed(self, umo: str, before: _Authorized) -> bool:
        try:
            task = await self._tasks.get_task(before.task_id)
            profile = await self._profiles.get_profile(before.profile_id)
            site = await self._sites.get_site(before.site_id)
        except Exception:
            return True
        return (
            umo not in task.umos
            or task.revision != before.task_revision
            or task.profile_id != before.profile_id
            or profile.revision != before.profile_revision
            or site.revision != before.site_revision
            or not site.enabled
        )


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
