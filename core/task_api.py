"""推送任务配置的后台 API 业务层。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .scheduler import (
    SchedulerUnavailableError,
    SchedulerValidationError,
)
from .schedule import (
    ScheduleCalculationError,
    ScheduleError,
    ScheduleTimezoneUnavailableError,
    ScheduleValidationError,
    next_scheduled_run,
    parse_schedule,
)
from .candidate_preview import first_satisfying_preview
from .content_api import content_snapshot_dict
from .content_plan import plan_payload
from .site_requirements import evaluate_sections, requirements_from_items
from .site_assembly import SiteProbe, assemble
from .sites import SiteService, SiteStorageError

from .image_styles import style_options
from .tasks import (
    IMAGE_STYLE_UNSET,
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
        sites: SiteService,
        *,
        clock=None,
        scheduler=None,
        content=None,
        plan_preview=None,
    ) -> None:
        self._tasks = tasks
        self._sites = sites
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._scheduler = scheduler
        self._content = content
        self._plan_preview = plan_preview

    async def list_tasks(self) -> TaskApiResult:
        now = self._clock()
        try:
            tasks = await self._tasks.list_tasks()
            sites = {site.id: site for site in await self._sites.list_sites()}
            runtime = (
                await self._scheduler.runtime_status()
                if self._scheduler is not None else {}
            )
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(
            {
                "image_styles": list(style_options()),
                "default_image_style": "default",
                "tasks": [
                    _task_dict(
                        task,
                        sites,
                        now,
                        runtime.get(task.id),
                    )
                    for task in tasks
                ],
            },
        )

    async def get_task(self, task_id: str) -> TaskApiResult:
        now = self._clock()
        try:
            task = await self._tasks.get_task(task_id)
            sites = {site.id: site for site in await self._sites.list_sites()}
            runtime = (
                await self._scheduler.runtime_status()
                if self._scheduler is not None else {}
            )
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _not_found_error()
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(
            task, sites, now, runtime.get(task.id),
        ))

    async def test_task(self, task_id: str) -> TaskApiResult:
        """按任务配置的类别与条数，判断是否有单个站点能满足整份配置。"""
        try:
            task = await self._tasks.get_task(task_id)
            sites = {site.id: site for site in await self._sites.list_sites()}
        except TaskNotFoundError:
            return _not_found_error()
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        if self._content is None:
            return _error(503, "内容获取不可用。", code="content_unavailable")

        baseline = (task.revision, tuple(task.sites))
        checked, site_snapshot, winner, truncated, assembly = (
            await self._scan_capability(task.items, task.sites, sites)
        )

        # 测试期间配置发生变化时，不能把旧结果报告为当前配置可用。
        try:
            latest_task = await self._tasks.get_task(task.id)
        except Exception:
            return _storage_error()
        if (latest_task.revision, tuple(latest_task.sites)) != baseline:
            return _error(
                409,
                "任务在测试期间发生变化，请重新测试。",
                code="config_changed",
            )
        # 所有候选的 revision 都必须与测试开始时一致。
        for site_id, revision in site_snapshot.items():
            try:
                latest_site = await self._sites.get_site(site_id)
            except Exception:
                return _storage_error()
            if latest_site.revision != revision:
                return _error(
                    409,
                    "站点配置在测试期间发生变化，请重新测试。",
                    code="config_changed",
                )

        message = _capability_message(assembly, checked)
        ttl = getattr(self._content, "cache_ttl_seconds", None)
        return _ok({
            "task_id": task.id,
            "satisfied": winner is not None,
            "site_id": winner["site_id"] if winner is not None else None,
            "site_name": winner["site_name"] if winner is not None else None,
            "source_mode": assembly.mode,
            "assignments": list(assembly.assignments),
            "missing": list(assembly.missing),
            "message": message,
            "checked": checked,
            "checked_at": self._clock().isoformat(),
            # 能力测试复用执行期缓存：结果是缓存时效内的判断，
            # 不是刚完成的实时连通性测试。
            "cache": {"used": True, "ttl_seconds": ttl},
            "truncated": truncated,
            "checked_count": len(checked),
        })

    async def _scan_capability(
        self,
        items: tuple[Any, ...],
        site_ids: tuple[str, ...],
        sites: dict[str, Any],
        *,
        use_cache: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[int, bool]], dict[str, Any] | None, bool]:
        """按候选顺序判断单站/拼合能否满足全部栏目；草稿与已保存任务共用。

        历史任务可能保存了超过新选上限的长候选列表：能力测试必须和预览
        一样检查完整列表，否则第 6 个候选能满足时会出现“测试不满足、预览
        却能成功”的口径分叉。
        """
        requirements = requirements_from_items(items)
        checked: list[dict[str, Any]] = []
        site_snapshot: dict[str, int] = {}
        probes: list[SiteProbe] = []
        candidates = tuple(site_ids)
        truncated = False
        for site_id in candidates:
            site = sites.get(site_id)
            entry: dict[str, Any] = {
                "site_id": site_id,
                "site_name": site.name if site is not None else None,
                "satisfied": False,
                "reason": None,
                "missing": [],
            }
            if site is None:
                entry["reason"] = "site_missing"
                checked.append(entry)
                continue
            # 记录所有候选当时的 revision，结束后复核，避免期间被修改
            # 的站点让结论失真。
            site_snapshot[site_id] = site.revision
            sections: list[dict[str, Any]] = []
            for item in items:
                try:
                    cached = await self._content.preview_cached(
                        item.content_type, site_id, use_cache=use_cache,
                    )
                    snapshot = content_snapshot_dict(
                        item.content_type, cached.snapshot,
                    )
                    raw_items = snapshot.get("items")
                    sections.append({
                        "content_type": item.content_type,
                        "status": "ok",
                        "items": raw_items if isinstance(raw_items, list) else [],
                    })
                except Exception:
                    sections.append({
                        "content_type": item.content_type,
                        "status": "error",
                        "items": [],
                    })
            satisfied, shortfalls = evaluate_sections(sections, requirements)
            entry["satisfied"] = satisfied
            entry["reason"] = None if satisfied else "requirements_not_met"
            entry["missing"] = [
                {
                    "content_type": item.content_type,
                    "required": item.required,
                    "available": item.available,
                    "reason": item.reason,
                }
                for item in shortfalls
            ]
            checked.append(entry)
            probes.append(SiteProbe(
                site_id=site.id,
                site_name=site.name,
                revision=site.revision,
                sections=tuple(sections),
                satisfied=satisfied,
            ))
        assembly = assemble(probes, requirements)
        winner: dict[str, Any] | None = None
        if assembly.mode == "single":
            winner = next(
                item for item in checked if item["site_id"] == assembly.site_id
            )
        elif assembly.mode == "multi":
            winner = {
                "site_id": "",
                "site_name": "多站点",
                "assembly": assembly.assignments,
            }
        return checked, site_snapshot, winner, truncated, assembly

    async def _draft_sites_changed(
        self,
        site_snapshot: dict[str, int],
    ) -> bool:
        for site_id, revision in site_snapshot.items():
            try:
                latest_site = await self._sites.get_site(site_id)
            except Exception:
                return True
            if latest_site.revision != revision:
                return True
        return False

    async def test_draft(self, payload: object) -> TaskApiResult:
        """用表单草稿判断候选站点能力；不保存、不投递、不请求真实消息。"""
        if self._content is None:
            return _error(503, "内容获取不可用。", code="content_unavailable")
        try:
            draft = await self._tasks.normalize_draft(payload)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _error(
                400, "候选站点不存在，请重新选择。", code="invalid_sites",
            )
        except TaskStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        try:
            sites = {site.id: site for site in await self._sites.list_sites()}
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        checked, site_snapshot, winner, truncated, assembly = (
            await self._scan_capability(
                draft.items, draft.sites, sites, use_cache=False,
            )
        )
        if await self._draft_sites_changed(site_snapshot):
            return _error(
                409,
                "站点配置在测试期间发生变化，请重新测试。",
                code="config_changed",
            )
        message = _capability_message(assembly, checked)
        ttl = getattr(self._content, "cache_ttl_seconds", None)
        return _ok({
            "draft": True,
            "satisfied": winner is not None,
            "site_id": winner["site_id"] if winner is not None else None,
            "site_name": winner["site_name"] if winner is not None else None,
            "source_mode": assembly.mode,
            "assignments": list(assembly.assignments),
            "missing": list(assembly.missing),
            "message": message,
            "checked": checked,
            "checked_at": self._clock().isoformat(),
            # 草稿直接取数，不读也不写生产缓存，结论是本次实时判断。
            "cache": {"used": False},
            "truncated": truncated,
            "checked_count": len(checked),
        })

    async def preview_draft(
        self,
        payload: object,
        *,
        owner: str | None = None,
    ) -> TaskApiResult:
        """按表单草稿取一次内容并保存草稿图片快照；不改任务或调度。"""
        if self._plan_preview is None:
            return _error(
                503, "内容预览当前不可用。", code="preview_unavailable",
            )
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        force_refresh = data.get("force_refresh", False)
        if type(force_refresh) is not bool:
            return _error(
                400,
                "force_refresh 必须是布尔值。",
                code="invalid_force_refresh",
            )
        try:
            draft = await self._tasks.normalize_draft(data)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _error(
                400, "候选站点不存在，请重新选择。", code="invalid_sites",
            )
        except TaskStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        try:
            sites = {site.id: site for site in await self._sites.list_sites()}
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        # 草稿预览按候选顺序尝试；已删除的站点不请求上游。
        candidates = [
            site_id for site_id in draft.sites if site_id in sites
        ]
        assembled = getattr(
            self._plan_preview, "preview_draft_assembled", None,
        )
        try:
            if callable(assembled):
                response = await assembled(
                    draft.items,
                    name=draft.name,
                    image_style=draft.image_style,
                    site_ids=candidates,
                    owner=owner,
                    force_refresh=force_refresh,
                )
                if response.status_code != 200:
                    return TaskApiResult(response.status_code, response.body)
                preview_data = (
                    response.body.get("data")
                    if isinstance(response.body, dict) else None
                )
                if not isinstance(preview_data, dict):
                    return _internal_error()
                found = (preview_data.get("site_id") or "", preview_data)
            else:
                adapter = _DraftPreviewAdapter(self._plan_preview, draft)
                found = await first_satisfying_preview(
                    adapter,
                    plan_id="draft",
                    requirements=requirements_from_items(draft.items),
                    site_ids=candidates,
                    owner=owner,
                    catch_errors=True,
                    force_refresh=force_refresh,
                )
        except Exception:
            return _internal_error()
        if found is None:
            return _error(
                502,
                "没有候选站点能同时提供全部栏目，请调整栏目或候选站点。",
                code="content_unavailable",
            )
        _, preview_data = found
        result = dict(preview_data)
        result["draft"] = True
        result["task_name"] = draft.name
        result["items"] = plan_payload(draft.items)
        result["image_style"] = draft.image_style
        result["image_styles"] = list(style_options())
        return _ok(result)

    async def generate_draft_image(
        self,
        payload: object,
        *,
        owner: str | None = None,
    ) -> TaskApiResult:
        """渲染草稿快照；快照与已保存任务入口互相不可用。"""
        if self._plan_preview is None:
            return _error(
                503, "图片渲染当前不可用。", code="image_unavailable",
            )
        result = await self._plan_preview.generate_draft_image(
            payload, owner=owner,
        )
        return TaskApiResult(result.status_code, result.body)

    async def list_schedule_runs(self, *, limit: int = 20, offset: int = 0) -> TaskApiResult:
        if self._scheduler is None:
            return _error(503, "调度服务不可用。", code="scheduler_unavailable")
        try:
            return _ok(await self._scheduler.list_tracking(
                limit=limit, offset=offset,
            ))
        except Exception:
            return _storage_error()

    async def enable_task(self, task_id: str, payload: object) -> TaskApiResult:
        return await self._set_enabled(task_id, payload, True)

    async def disable_task(self, task_id: str, payload: object) -> TaskApiResult:
        return await self._set_enabled(task_id, payload, False)

    async def _set_enabled(
        self, task_id: str, payload: object, enabled: bool,
    ) -> TaskApiResult:
        if self._scheduler is None:
            return _error(503, "调度服务不可用。", code="scheduler_unavailable")
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        if set(data) - {"expected_revision"}:
            return _error(400, "只接受 expected_revision。", code="invalid_body")
        try:
            if enabled:
                task = await self._scheduler.enable_task(
                    task_id, data.get("expected_revision"),
                )
            else:
                task = await self._scheduler.disable_task(
                    task_id, data.get("expected_revision"),
                )
            sites = {site.id: site for site in await self._sites.list_sites()}
            runtime = await self._scheduler.runtime_status()
        except TaskValidationError as exc:
            return _validation_error(exc)
        except SchedulerValidationError as exc:
            return _validation_error(exc)
        except SchedulerUnavailableError:
            return _error(503, "调度服务不可用。", code="scheduler_unavailable")
        except TaskNotFoundError:
            return _not_found_error()
        except TaskConflictError as exc:
            return _conflict_error(exc)
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(
            task, sites, self._clock(), runtime.get(task.id),
        ))

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
                    items=data.get("items"),
                    output_format=data.get("output_format"),
                    umos=data.get("umos"),
                    schedule=data.get("schedule"),
                    sites=data.get("sites"),
                    image_style=(
                        data["image_style"] if "image_style" in data
                        else IMAGE_STYLE_UNSET
                    ),
                ),
            )
            sites = {site.id: site for site in await self._sites.list_sites()}
        except (TaskValidationError, ScheduleValidationError) as exc:
            return _validation_error(exc)
        except ScheduleTimezoneUnavailableError as exc:
            return _error(400, str(exc), code="schedule_timezone_unavailable")
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(task, sites, now), status_code=201)

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
                    items=data.get("items"),
                    output_format=data.get("output_format"),
                    umos=data.get("umos"),
                    expected_revision=data.get("expected_revision"),
                    schedule=(
                        data["schedule"]
                        if "schedule" in data
                        else SCHEDULE_UNSET
                    ),
                    sites=data.get("sites"),
                    image_style=(
                        data["image_style"] if "image_style" in data
                        else IMAGE_STYLE_UNSET
                    ),
                ),
            )
            sites = {site.id: site for site in await self._sites.list_sites()}
        except (TaskValidationError, ScheduleValidationError) as exc:
            return _validation_error(exc)
        except ScheduleTimezoneUnavailableError as exc:
            return _error(400, str(exc), code="schedule_timezone_unavailable")
        except TaskNotFoundError:
            return _not_found_error()
        except TaskConflictError as exc:
            return _conflict_error(exc)
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_task_dict(task, sites, now))

    async def preview_task(
        self,
        task_id: str,
        payload: object,
        *,
        owner: str | None = None,
    ) -> TaskApiResult:
        """按任务已保存的栏目与候选顺序取一次内容；不发送、不改配置。"""
        data = _payload_dict(payload)
        if data is None:
            data = {}
        force_refresh = data.get("force_refresh", False)
        if type(force_refresh) is not bool:
            return _error(
                400,
                "force_refresh 必须是布尔值。",
                code="invalid_force_refresh",
            )
        if self._plan_preview is None:
            return _error(
                503,
                "内容预览当前不可用。",
                code="preview_unavailable",
            )
        try:
            task = await self._tasks.get_task(task_id)
            sites = {site.id: site for site in await self._sites.list_sites()}
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _not_found_error()
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        # 预览按候选顺序尝试；已删除的站点不请求上游。
        candidates = [
            site_id for site_id in task.sites if site_id in sites
        ]
        try:
            assembled = getattr(self._plan_preview, "preview_assembled", None)
            if callable(assembled):
                response = await assembled(
                    {
                        "task_id": task.id,
                        "force_refresh": force_refresh,
                    },
                    site_ids=candidates,
                    owner=owner,
                )
                if response.status_code != 200:
                    return TaskApiResult(response.status_code, response.body)
                preview_data = (
                    response.body.get("data")
                    if isinstance(response.body, dict) else None
                )
                if not isinstance(preview_data, dict):
                    return _internal_error()
                found = (preview_data.get("site_id") or "", preview_data)
            else:
                found = await first_satisfying_preview(
                    self._plan_preview,
                    plan_id=task.id,
                    requirements=requirements_from_items(task.items),
                    site_ids=candidates,
                    owner=owner,
                    catch_errors=True,
                    force_refresh=force_refresh,
                )
        except Exception:
            return _internal_error()
        if found is None:
            return _error(
                502,
                "没有候选站点能同时提供全部栏目，请调整栏目或候选站点。",
                code="content_unavailable",
            )
        try:
            latest = await self._tasks.get_task(task.id)
        except Exception:
            return _storage_error()
        if latest.revision != task.revision:
            return _error(
                409,
                "任务配置在获取期间发生变化，本次预览已取消，请重试。",
                code="preview_revision_changed",
            )
        _, preview_data = found
        result = dict(preview_data)
        result["task_id"] = task.id
        result["task_name"] = task.name
        result["task_revision"] = task.revision
        result["items"] = plan_payload(task.items)
        result["image_style"] = task.image_style
        result["image_styles"] = list(style_options())
        return _ok(result)

    async def generate_task_image(
        self,
        task_id: str,
        payload: object,
        *,
        owner: str | None = None,
    ) -> TaskApiResult:
        """把一次任务预览得到的快照渲染成栏目长图。"""
        if self._plan_preview is None:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        try:
            task = await self._tasks.get_task(task_id)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _not_found_error()
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        result = await self._plan_preview.generate_image(
            task.id, payload, owner=owner,
        )
        return TaskApiResult(result.status_code, result.body)

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


def _capability_message(assembly, checked: list[dict[str, Any]]) -> str:
    """能力测试的人话结论：单站、多站拼合或仍缺栏目。"""
    if assembly.mode == "single":
        return f"站点「{assembly.site_name}」可以满足全部配置。"
    if assembly.mode == "multi":
        parts = [
            f"{item['label']}→{item['site_name']}"
            for item in assembly.assignments
        ]
        return "需要多站点拼合：" + "、".join(parts) + "。"
    if not checked:
        return "任务没有配置候选站点。"
    missing = "、".join(item["label"] for item in assembly.missing)
    return f"所选站点无法提供全部栏目，仍缺：{missing}。"


def _task_dict(
    task: PushTask,
    sites: dict[str, Any],
    now: datetime,
    next_run_at: str | None = None,
) -> dict[str, Any]:
    candidates = []
    for site_id in task.sites:
        candidate = sites.get(site_id)
        candidates.append({
            "id": site_id,
            "name": candidate.name if candidate is not None else None,
            "exists": candidate is not None,
        })
    schedule = task.schedule
    preview = None
    evaluated_at = now.astimezone(timezone.utc).isoformat()
    if schedule is not None:
        try:
            next_run = next_scheduled_run(schedule, now)
            preview = {
                "evaluated_at": evaluated_at,
                "next_run_at": next_run.isoformat(),
            }
        except ScheduleTimezoneUnavailableError:
            preview = {
                "evaluated_at": evaluated_at, "next_run_at": None,
                "error": "schedule_timezone_unavailable",
            }
        except ScheduleValidationError:
            preview = {
                "evaluated_at": evaluated_at, "next_run_at": None,
                "error": "schedule_invalid",
            }
        except ScheduleCalculationError:
            preview = {
                "evaluated_at": evaluated_at, "next_run_at": None,
                "error": "schedule_calculation_error",
            }
    elif task.schedule_raw is not None:
        preview = {
            "evaluated_at": evaluated_at,
            "next_run_at": None,
            "error": task.schedule_error,
        }
    return {
        "id": task.id,
        "name": task.name,
        "items": plan_payload(task.items),
        # 任务自带栏目；site_id 保留为 null 供旧前端读取。
        "site_id": None,
        # 站点级启用已移除；保留字段供旧前端识别，不再有布尔含义。
        "site_enabled": None,
        "sites": candidates,
        "output_format": task.output_format,
        "output_label": task.output_label,
        "image_style": task.image_style,
        "umos": list(task.umos),
        "target_count": task.target_count,
        "enabled": task.enabled,
        "can_send": task.enabled,
        "next_run_at": next_run_at,
        "revision": task.revision,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "schedule": (
            schedule.to_dict() if schedule else task.schedule_raw
        ),
        "schedule_error": task.schedule_error,
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


class _DraftPreviewAdapter:
    """把草稿参数适配成 first_satisfying_preview 需要的 preview_site 接口。"""

    def __init__(self, preview, draft) -> None:
        self._preview = preview
        self._draft = draft

    async def preview_site(self, payload, *, site_id=None, owner=None):
        return await self._preview.preview_draft(
            self._draft.items,
            name=self._draft.name,
            image_style=self._draft.image_style,
            site_id=site_id,
            owner=owner,
            force_refresh=bool(
                payload.get("force_refresh") if isinstance(payload, dict) else False
            ),
        )
