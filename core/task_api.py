"""推送任务配置的后台 API 业务层。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .profiles import (
    ContentProfileService,
    ProfileNotFoundError,
    ProfileStorageError,
    ProfileValidationError,
)
from .schedule import (
    ScheduleError,
    ScheduleTimezoneUnavailableError,
    ScheduleValidationError,
    next_daily_run,
    parse_schedule,
)
from .sites import SiteService, SiteStorageError
from .tasks import (
    PushTask,
    PushTaskService,
    TaskConflictError,
    TaskCreate,
    TaskNotFoundError,
    TaskStorageError,
    TaskUpdate,
    SCHEDULE_UNSET,
    TaskValidationError,
)


@dataclass(frozen=True, slots=True)
class TaskApiResult:
    status_code: int
    body: dict[str, Any]


class TaskApi:
    """推送任务 CRUD。本层不发送消息、不调度、不请求上游。"""

    def __init__(
        self,
        tasks: PushTaskService,
        profiles: ContentProfileService,
        sites: SiteService,
        *,
        clock=None,
    ) -> None:
        self._tasks = tasks
        self._profiles = profiles
        self._sites = sites
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def list_tasks(self) -> TaskApiResult:
        now = self._clock()
        try:
            tasks = await self._tasks.list_tasks()
            profiles = {
                profile.id: profile
                for profile in await self._profiles.list_profiles()
            }
            sites = {site.id: site for site in await self._sites.list_sites()}
        except (TaskStorageError, ProfileStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(
            {
                "tasks": [
                    _task_dict(
                        task,
                        profiles.get(task.profile_id),
                        sites,
                        now,
                    )
                    for task in tasks
                ],
            },
        )

    async def get_task(self, task_id: str) -> TaskApiResult:
        now = self._clock()
        try:
            task = await self._tasks.get_task(task_id)
            profile = await self._profiles.get_profile(task.profile_id)
            sites = {site.id: site for site in await self._sites.list_sites()}
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _not_found_error()
        except ProfileNotFoundError:
            return _profile_not_found_error()
        except (TaskStorageError, ProfileStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(task, profile, sites, now))

    async def create_task(self, payload: object) -> TaskApiResult:
        now = self._clock()
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        enabled_error = _reject_enabled(data)
        if enabled_error is not None:
            return enabled_error
        try:
            task = await self._tasks.create_task(
                TaskCreate(
                    name=data.get("name"),
                    profile_id=data.get("profile_id"),
                    output_format=data.get("output_format"),
                    umos=data.get("umos"),
                    schedule=data.get("schedule"),
                ),
            )
            profile = await self._profiles.get_profile(task.profile_id)
            sites = {site.id: site for site in await self._sites.list_sites()}
        except (TaskValidationError, ProfileValidationError, ScheduleValidationError) as exc:
            return _validation_error(exc)
        except ScheduleTimezoneUnavailableError as exc:
            return _error(400, str(exc), code="schedule_timezone_unavailable")
        except ProfileNotFoundError:
            return _profile_not_found_error()
        except TaskStorageError:
            return _storage_error()
        except (ProfileStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(task, profile, sites, now), status_code=201)

    async def update_task(
        self,
        task_id: str,
        payload: object,
    ) -> TaskApiResult:
        now = self._clock()
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        enabled_error = _reject_enabled(data)
        if enabled_error is not None:
            return enabled_error
        try:
            task = await self._tasks.update_task(
                task_id,
                TaskUpdate(
                    name=data.get("name"),
                    profile_id=data.get("profile_id"),
                    output_format=data.get("output_format"),
                    umos=data.get("umos"),
                    expected_revision=data.get("expected_revision"),
                    schedule=(
                        data["schedule"]
                        if "schedule" in data
                        else SCHEDULE_UNSET
                    ),
                ),
            )
            profile = await self._profiles.get_profile(task.profile_id)
            sites = {site.id: site for site in await self._sites.list_sites()}
        except (TaskValidationError, ProfileValidationError, ScheduleValidationError) as exc:
            return _validation_error(exc)
        except ScheduleTimezoneUnavailableError as exc:
            return _error(400, str(exc), code="schedule_timezone_unavailable")
        except TaskNotFoundError:
            return _not_found_error()
        except ProfileNotFoundError:
            return _profile_not_found_error()
        except TaskConflictError as exc:
            return _conflict_error(exc)
        except TaskStorageError:
            return _storage_error()
        except (ProfileStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(task, profile, sites, now))

    async def delete_task(
        self,
        task_id: str,
        payload: object,
    ) -> TaskApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            await self._tasks.delete_task(
                task_id,
                data.get("expected_revision"),
            )
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _not_found_error()
        except TaskConflictError as exc:
            return _conflict_error(exc)
        except TaskStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok({"deleted_id": task_id})


def _task_dict(
    task: PushTask,
    profile: Any,
    sites: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    site = sites.get(profile.site_id) if profile is not None else None
    schedule = task.schedule
    preview = None
    if schedule is not None:
        try:
            next_run = next_daily_run(schedule, now)
            preview = {
                "evaluated_at": now.astimezone(timezone.utc).isoformat(),
                "next_run_at": next_run.isoformat(),
            }
        except ScheduleError:
            preview = {
                "evaluated_at": now.astimezone(timezone.utc).isoformat(),
                "next_run_at": None,
                "error": "schedule_timezone_unavailable",
            }
    return {
        "id": task.id,
        "name": task.name,
        "profile_id": task.profile_id,
        "profile_name": profile.name if profile else "未知组合",
        "site_id": profile.site_id if profile else None,
        "site_enabled": site.enabled if site else False,
        "output_format": task.output_format,
        "output_label": task.output_label,
        "umos": list(task.umos),
        "target_count": task.target_count,
        "enabled": False,
        "can_send": False,
        "revision": task.revision,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "schedule": schedule.to_dict() if schedule else None,
        "schedule_preview": preview,
    }


def _payload_dict(payload: object) -> dict[str, Any] | None:
    return payload if isinstance(payload, dict) else None


def _reject_enabled(data: dict[str, Any]) -> TaskApiResult | None:
    forbidden = {
        "enabled", "next_run", "next_run_at", "schedule_preview",
        "running", "last_run", "last_run_at",
    }
    if any(field in data for field in forbidden):
        return _error(
            400,
            "不接受启用、运行状态或调度状态字段。",
            code="unsupported_enable_request",
        )
    return None


def _ok(data: Any, *, status_code: int = 200) -> TaskApiResult:
    return TaskApiResult(
        status_code=status_code,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    *,
    code: str,
    data: dict[str, Any] | None = None,
) -> TaskApiResult:
    details = {"code": code}
    if data:
        details.update(data)
    return TaskApiResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _invalid_body_error() -> TaskApiResult:
    return _error(400, "请求体必须是 JSON 对象。", code="invalid_body")


def _validation_error(exc: Exception) -> TaskApiResult:
    field = getattr(exc, "field", "request")
    return _error(
        400,
        str(exc),
        code="validation_error",
        data={"field": field},
    )


def _not_found_error() -> TaskApiResult:
    return _error(404, "推送任务不存在或已被删除。", code="not_found")


def _profile_not_found_error() -> TaskApiResult:
    return _error(
        404,
        "引用的内容组合不存在或已被删除。",
        code="profile_not_found",
    )


def _conflict_error(exc: TaskConflictError) -> TaskApiResult:
    return _error(
        409,
        "推送任务已被其他操作修改，请重新读取后再保存。",
        code="revision_conflict",
        data={
            "expected_revision": exc.expected_revision,
            "actual_revision": exc.actual_revision,
        },
    )


def _storage_error() -> TaskApiResult:
    return _error(
        503,
        "推送任务存储暂不可用，请稍后重试。",
        code="storage_unavailable",
    )


def _internal_error() -> TaskApiResult:
    return _error(
        500,
        "服务器内部错误，请稍后重试。",
        code="internal_error",
    )


__all__ = ["TaskApi", "TaskApiResult"]
