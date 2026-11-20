"""按一份内容方案在单个站点上取内容的预览与图片生成服务。

预览、聊天查询、手动/定时投递共用这里：调用方给出栏目方案与站点，
本类只负责「这一个站点上的这一套栏目」是否取全、版本是否稳定，
不判断候选站点是否满足要求（那是 candidate_preview 的事）。
"""

from __future__ import annotations

import asyncio
import base64
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .content_api import content_snapshot_dict
from .content_service import ContentService
from .content_types import CONTENT_LABELS
from .news import (
    NewsBusinessError,
    NewsClientClosedError,
    NewsHTTPStatusError,
    NewsInvalidJSONError,
    NewsRedirectError,
    NewsStructureError,
    NewsTimeoutError,
    NewsTooLargeError,
    NewsTransportError,
)
from .image_service import (
    ImageRenderFailedError,
    ImageServiceClosedError,
    ImageTimeoutError,
    ProfileImageService,
)
from .image_snapshots import (
    DEFAULT_MAX_RENDER_OUTPUT_BYTES,
    ImageSnapshotClosedError,
    ImageSnapshotError,
    ImageSnapshotStore,
)
from .profile_image import (
    FontUnavailableError,
    ImageLimitError,
    ProfileImageError,
)
from .profile_render import render_profile_outputs
from .site_assembly import SiteProbe, assemble
from .site_requirements import evaluate_sections, requirements_from_items
from .content_plan import PlanItem, plan_payload
from .image_styles import resolve_style_id
from .tasks import (
    PushTaskService,
    TaskNotFoundError,
    TaskStorageError,
    TaskValidationError,
)
from .sites import (
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
)

_SECTION_IMAGE_STATUSES = frozenset({"success", "empty", "failed"})
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
# 一次组合预览最多做两轮：首轮 + 一次按最终一致 revision 重试。
_MAX_PREVIEW_ATTEMPTS = 2


class _PreviewBudgetExceeded(Exception):
    """单个栏目没有分到本次预览剩余时间里的取数预算。"""


@dataclass(frozen=True, slots=True)
class PlanPreviewResult:
    status_code: int
    body: dict[str, Any]


class PlanPreview:
    """把一份栏目方案落到一个站点上取内容，并保存可渲染的图片快照。"""

    def __init__(
        self,
        tasks: PushTaskService,
        content: ContentService,
        sites: SiteService,
        *,
        image_snapshots: ImageSnapshotStore | None = None,
        image_service: ProfileImageService | None = None,
    ) -> None:
        self._tasks = tasks
        self._content = content
        self._sites = sites
        self._image_snapshots = image_snapshots
        self._image_service = image_service


    async def preview_site(
        self,
        payload: object,
        *,
        owner: str | None = None,
        site_id: object = None,
        _with_snapshot: bool = True,
        deadline: float | None = None,
    ) -> PlanPreviewResult:
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
        task_id = data.get("task_id")
        if task_id is None:
            # 旧客户端仍可能发送 profile_id；方案行 id 与任务 id 相同。
            task_id = data.get("profile_id")
        if site_id is None:
            site_id = data.get("site_id")
        if site_id is None or site_id == "":
            return _error(
                400,
                "请先选择用于预览的站点。",
                code="site_required",
            )
        try:
            config = await self._tasks.get_config_snapshot(task_id)
            site = await self._sites.get_site(site_id)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _task_not_found_error()
        except TaskStorageError:
            return _storage_error()
        except SiteNotFoundError:
            return _site_not_found_error()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        plan_items = config.task.items
        plan_revision = config.plan_revision

        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        # 一次预览的每个栏目都各自重新读取站点，若中途改过站点或组合配置，
        # 收上来的栏目会来自不同 revision。这里在每一轮结束后复核版本：
        # 只要变过就丢弃整轮结果，按最终一致版本重做；超过上限明确失败，
        # 绝不把不同版本的栏目拼进同一个 200 响应。
        for attempt in range(_MAX_PREVIEW_ATTEMPTS):
            try:
                sections, failures = await self._collect_sections(
                    plan_items, force_refresh, site.id, deadline=deadline,
                )
            except SiteStorageError:
                return _storage_error()
            try:
                latest = await self._tasks.get_config_snapshot(task_id)
                latest_site = await self._sites.get_site(site_id)
            except TaskValidationError as exc:
                return _validation_error(exc)
            except TaskNotFoundError:
                return _task_not_found_error()
            except SiteNotFoundError:
                return _site_not_found_error()
            except (TaskStorageError, SiteStorageError):
                return _storage_error()
            except Exception:
                return _internal_error()
            # 重试与最终响应都使用这一版已确认配置：否则重试拿到的栏目
            # 属于新配置，响应里的任务 revision 和样式却还是首轮的旧值。
            config = latest
            if (
                latest.plan_revision == plan_revision
                and latest_site.id == site.id
                and latest_site.revision == site.revision
            ):
                site = latest_site
                break
            if attempt + 1 >= _MAX_PREVIEW_ATTEMPTS:
                return _error(
                    409,
                    "任务配置在获取期间发生变化，本次预览已取消，请重试。",
                    code="preview_revision_changed",
                )
            plan_items = latest.task.items
            plan_revision = latest.plan_revision
            site = latest_site

        success_count = len(sections) - len(failures)
        data = {
            "task_id": config.task.id,
            "task_name": config.task.name,
            "task_revision": config.task.revision,
            "image_style": config.task.image_style,
            "plan_revision": plan_revision,
            "items": plan_payload(plan_items),
            "site_id": site.id,
            "site_name": site.name,
            # 内容实际取数时的站点版本：拼合侧不能再用稍后读取的
            # revision 标注这批旧栏目。
            "site_revision": site.revision,
            "success_count": success_count,
            "failure_count": len(failures),
            "sections": sections,
            "failed_sections": [
                {
                    "content_type": section["content_type"],
                    "label": section["label"],
                    "message": section["message"],
                }
                for section in failures
            ],
        }
        if success_count == 0:
            data["status"] = "failed"
        elif failures:
            data["status"] = "partial"
        else:
            data["status"] = "complete"

        if success_count == 0:
            failed_labels = "、".join(
                section["label"] for section in failures
            ) or "未知栏目"
            return _error(
                502,
                f"所有栏目获取失败：{failed_labels}。",
                code="all_sections_failed",
                data=data,
            )

        data["outputs"] = render_profile_outputs(data)
        if _with_snapshot and self._image_snapshots is not None:
            try:
                snapshot = self._image_snapshots.put(
                    owner=owner or "",
                    profile_id=config.task.id,
                    profile_revision=plan_revision,
                    sites=((site.id, site.revision),),
                    data=_image_snapshot_data(data),
                )
            except ImageSnapshotError:
                snapshot = None
            if snapshot is not None:
                data["image_snapshot"] = {
                    "id": snapshot.id,
                    "expires_at": snapshot.expires_at,
                }
        return _ok(data)

    async def _read_revisions(
        self,
        site_ids: Iterable[str],
    ) -> dict[str, int]:
        """一次读取全部来源的 revision；旧桩回退到逐个读取。"""
        ids = tuple(dict.fromkeys(site_ids))
        getter = getattr(self._sites, "get_revisions", None)
        if callable(getter):
            return await getter(list(ids))
        revisions: dict[str, int] = {}
        for site_id in ids:
            site = await self._sites.get_site(site_id)
            revisions[site.id] = site.revision
        return revisions

    async def _check_snapshot_sites(self, snapshot: Any) -> PlanPreviewResult | None:
        """快照里的每个来源站点都仍存在且 revision 未变。"""
        expected = tuple(getattr(snapshot, "sites", ()))
        if not expected:
            return None
        try:
            revisions = await self._read_revisions(
                site_id for site_id, _ in expected
            )
        except SiteStorageError:
            return _storage_error()
        except SiteNotFoundError:
            return _stale_snapshot_error()
        except Exception:
            return _internal_error()
        for site_id, revision in expected:
            if revisions.get(site_id) != revision:
                return _stale_snapshot_error()
        return None

    async def _verify_sources(
        self,
        sources: Iterable[tuple[str, int]],
    ) -> PlanPreviewResult | None:
        """本次预览实际使用的来源站点必须仍存在且 revision 一致。

        一次查询读取全部来源版本，避免逐个读取时“读完 A、读 B 前 A
        被改”的间隙把旧内容当成新版本。
        """
        expected = tuple(sources)
        if not expected:
            return None
        try:
            revisions = await self._read_revisions(
                site_id for site_id, _ in expected
            )
        except SiteStorageError:
            return _storage_error()
        except SiteNotFoundError:
            return _preview_conflict_error()
        except Exception:
            return _internal_error()
        for site_id, revision in expected:
            if revisions.get(site_id) != revision:
                return _preview_conflict_error()
        return None

    async def preview_assembled(
        self,
        payload: object,
        *,
        site_ids: object,
        owner: str | None = None,
        deadline: float | None = None,
    ) -> PlanPreviewResult:
        """按候选顺序单站优先、缺栏时按栏目拼合一次预览。

        每个候选只取一次完整栏目；先判断是否有单站满足全部要求，没有才
        用已取到的结果按栏目选最靠前的合格站点。仍缺栏目时保留已选内容
        并返回缺项，调用方据此提示或告警。
        """
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
        task_id = data.get("task_id")
        if task_id is None:
            task_id = data.get("profile_id")
        try:
            config = await self._tasks.get_config_snapshot(task_id)
        except TaskValidationError as exc:
            return _validation_error(exc)
        except TaskNotFoundError:
            return _task_not_found_error()
        except TaskStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        requirements = requirements_from_items(config.task.items)
        ordered: list[str] = []
        if isinstance(site_ids, (list, tuple)):
            for value in site_ids:
                if isinstance(value, str) and value and value not in ordered:
                    ordered.append(value)
        probes: list[SiteProbe] = []
        for index, site_id in enumerate(ordered):
            site_deadline: float | None = None
            if deadline is not None:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                # 每个候选只分到剩余时间的一份；preview_site 再把这
                # 份预算分给各栏目，单栏超时只标记该栏失败，已经取到的
                # 栏目仍保留在探测结果里，供按栏拼合使用。
                site_deadline = (
                    asyncio.get_running_loop().time()
                    + remaining / max(1, len(ordered) - index)
                )
            call = self.preview_site(
                {
                    "task_id": config.task.id,
                    "force_refresh": force_refresh,
                    "site_id": site_id,
                },
                owner=owner,
                site_id=site_id,
                _with_snapshot=False,
                deadline=site_deadline,
            )
            try:
                result = await call
            except asyncio.TimeoutError:
                # preview_site 自己按栏目预算返回部分结果；这里只兜底
                # 意外的取消，避免整次预览直接失败。
                continue
            if result.status_code not in (200, 502):
                # 配置冲突、存储错误等明确失败直接中止整次预览。
                if result.status_code >= 400:
                    return result
                continue
            body = result.body.get("data") or {}
            sections = body.get("sections")
            if not isinstance(sections, list):
                sections = []
            satisfied, _ = evaluate_sections(sections, requirements)
            fetched_id = body.get("site_id")
            fetched_name = body.get("site_name")
            fetched_revision = body.get("site_revision")
            if (
                isinstance(fetched_id, str) and fetched_id
                and type(fetched_revision) is int
            ):
                # 使用预览实际取数时记录的版本，而不是返回后重新读取的
                # 站点版本：两步之间改过站点时，旧栏目必须按旧版本复核。
                probe_id = fetched_id
                probe_name = (
                    fetched_name if isinstance(fetched_name, str) else None
                )
                probe_revision = fetched_revision
            else:
                # 旧预览桩没有返回版本：回退到当前站点对象。
                try:
                    site = await self._sites.get_site(site_id)
                except SiteNotFoundError:
                    continue
                except (SiteStorageError, Exception):
                    return _storage_error()
                probe_id, probe_name = site.id, site.name
                probe_revision = site.revision
            probes.append(SiteProbe(
                site_id=probe_id,
                site_name=probe_name,
                revision=probe_revision,
                sections=tuple(sections),
                satisfied=satisfied,
            ))
            if satisfied:
                # 单站优先：候选顺序里第一个满足全部要求的站点直接采用，
                # 不再请求后面的候选。
                break
        if not probes:
            return _error(
                502,
                "没有候选站点返回可用内容，请调整栏目或候选站点。",
                code="content_unavailable",
            )
        assembly = assemble(probes, requirements)
        # 每个候选只在自己的 preview_site 内复核了版本；整次拼合期间前面
        # 已取完的站点仍可能被修改。返回 200 前必须复核本次实际使用的
        # 全部来源，否则“A 取完、B 取数时改 A”会把旧内容当成新配置。
        stale = await self._verify_sources(assembly.sites)
        if stale is not None:
            return stale
        sections = list(assembly.sections)
        ok_sections = [item for item in sections if item.get("status") == "ok"]
        failed_sections = [
            {
                "content_type": item.get("content_type"),
                "label": item.get("label"),
                "message": item.get("message") or "栏目不可用。",
            }
            for item in sections
            if item.get("status") != "ok"
        ]
        for missing in assembly.missing:
            failed_sections.append({
                "content_type": missing["content_type"],
                "label": missing["label"],
                "message": (
                    f"没有候选站点能提供所需条数（需要 "
                    f"{missing['required']} 条，最多 {missing['available']} 条）。"
                    if missing.get("required")
                    else "没有候选站点提供该栏目。"
                ),
            })
        success_count = len(ok_sections)
        result = {
            "task_id": config.task.id,
            "task_name": config.task.name,
            "task_revision": config.task.revision,
            "image_style": config.task.image_style,
            "plan_revision": config.plan_revision,
            "items": plan_payload(config.task.items),
            "site_id": assembly.site_id if assembly.mode == "single" else "",
            "site_name": (
                assembly.site_name if assembly.mode == "single"
                else ("多站点" if assembly.mode == "multi" else None)
            ),
            "source_mode": assembly.mode,
            "sites": [
                {"site_id": site_id, "revision": revision}
                for site_id, revision in assembly.sites
            ],
            "assignments": list(assembly.assignments),
            "missing": list(assembly.missing),
            "success_count": success_count,
            "failure_count": len(failed_sections),
            "sections": sections,
            "failed_sections": failed_sections,
        }
        if success_count == 0:
            result["status"] = "failed"
        elif failed_sections:
            result["status"] = "partial"
        else:
            result["status"] = "complete"
        if success_count == 0:
            labels = "、".join(
                str(item.get("label")) for item in failed_sections
            ) or "未知栏目"
            return _error(
                502,
                f"没有候选站点能提供所需栏目：{labels}。",
                code="content_unavailable",
                data=result,
            )
        result["outputs"] = render_profile_outputs(result)
        if self._image_snapshots is not None:
            try:
                snapshot = self._image_snapshots.put(
                    owner=owner or "",
                    profile_id=config.task.id,
                    profile_revision=config.plan_revision,
                    sites=assembly.sites,
                    data=_image_snapshot_data(result),
                )
            except ImageSnapshotError:
                snapshot = None
            if snapshot is not None:
                result["image_snapshot"] = {
                    "id": snapshot.id,
                    "expires_at": snapshot.expires_at,
                }
        return _ok(result)

    async def preview_draft(
        self,
        items: tuple[Any, ...],
        *,
        name: str,
        image_style: str,
        site_id: str,
        owner: str | None = None,
        force_refresh: bool = False,
        _with_snapshot: bool = True,
    ) -> PlanPreviewResult:
        """按未保存的表单草稿取一次内容；不写任务、不发送。"""
        try:
            site = await self._sites.get_site(site_id)
        except SiteNotFoundError:
            return _site_not_found_error()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for attempt in range(_MAX_PREVIEW_ATTEMPTS):
            try:
                sections, failures = await self._collect_sections(
                    items, force_refresh, site.id, use_cache=False,
                )
            except SiteStorageError:
                return _storage_error()
            try:
                latest_site = await self._sites.get_site(site_id)
            except SiteNotFoundError:
                return _site_not_found_error()
            except SiteStorageError:
                return _storage_error()
            except Exception:
                return _internal_error()
            if latest_site.revision == site.revision:
                site = latest_site
                break
            if attempt + 1 >= _MAX_PREVIEW_ATTEMPTS:
                return _error(
                    409,
                    "站点配置在获取期间发生变化，本次预览已取消，请重试。",
                    code="preview_revision_changed",
                )
            site = latest_site

        success_count = len(sections) - len(failures)
        data = {
            "draft": True,
            "task_name": name,
            "image_style": image_style,
            "items": plan_payload(items),
            "site_id": site.id,
            "site_name": site.name,
            # 内容实际取数时的站点版本：拼合侧不能再用稍后读取的
            # revision 标注这批旧栏目。
            "site_revision": site.revision,
            "success_count": success_count,
            "failure_count": len(failures),
            "sections": sections,
            "failed_sections": [
                {
                    "content_type": section["content_type"],
                    "label": section["label"],
                    "message": section["message"],
                }
                for section in failures
            ],
        }
        if success_count == 0:
            data["status"] = "failed"
        elif failures:
            data["status"] = "partial"
        else:
            data["status"] = "complete"
        if success_count == 0:
            failed_labels = "、".join(
                section["label"] for section in failures
            ) or "未知栏目"
            return _error(
                502,
                f"所有栏目获取失败：{failed_labels}。",
                code="all_sections_failed",
                data=data,
            )
        data["outputs"] = render_profile_outputs(data)
        if _with_snapshot and self._image_snapshots is not None:
            try:
                snapshot = self._image_snapshots.put(
                    owner=owner or "",
                    # 草稿没有任务行：空 id 与 0 版本配合 data.draft 标记，
                    # 与已保存任务的图片入口严格隔离。
                    profile_id="",
                    profile_revision=0,
                    sites=((site.id, site.revision),),
                    data={**_image_snapshot_data(data), "draft": True},
                    scope="draft",
                )
            except ImageSnapshotError:
                snapshot = None
            if snapshot is not None:
                data["image_snapshot"] = {
                    "id": snapshot.id,
                    "expires_at": snapshot.expires_at,
                }
        return _ok(data)

    async def preview_draft_assembled(
        self,
        items: tuple[Any, ...],
        *,
        name: str,
        image_style: str,
        site_ids: object,
        owner: str | None = None,
        force_refresh: bool = False,
    ) -> PlanPreviewResult:
        """草稿表单的单站优先 + 按栏拼合预览；不写任务、不发送。"""
        requirements = requirements_from_items(items)
        ordered: list[str] = []
        if isinstance(site_ids, (list, tuple)):
            for value in site_ids:
                if isinstance(value, str) and value and value not in ordered:
                    ordered.append(value)
        probes: list[SiteProbe] = []
        for site_id in ordered:
            try:
                site = await self._sites.get_site(site_id)
            except SiteNotFoundError:
                continue
            except SiteStorageError:
                return _storage_error()
            result = await self.preview_draft(
                items,
                name=name,
                image_style=image_style,
                site_id=site_id,
                owner=owner,
                force_refresh=force_refresh,
                _with_snapshot=False,
            )
            if result.status_code not in (200, 502):
                if result.status_code >= 400:
                    return result
                continue
            body = result.body.get("data") or {}
            sections = body.get("sections")
            if not isinstance(sections, list):
                sections = []
            satisfied, _ = evaluate_sections(sections, requirements)
            fetched_id = body.get("site_id")
            fetched_revision = body.get("site_revision")
            fetched_name = body.get("site_name")
            if (
                isinstance(fetched_id, str) and fetched_id
                and type(fetched_revision) is int
            ):
                probe_id = fetched_id
                probe_name = (
                    fetched_name if isinstance(fetched_name, str) else None
                )
                probe_revision = fetched_revision
            else:
                probe_id, probe_name, probe_revision = (
                    site.id, site.name, site.revision,
                )
            probes.append(SiteProbe(
                site_id=probe_id,
                site_name=probe_name,
                revision=probe_revision,
                sections=tuple(sections),
                satisfied=satisfied,
            ))
            if satisfied:
                break
        if not probes:
            return _error(
                502,
                "没有候选站点返回可用内容，请调整栏目或候选站点。",
                code="content_unavailable",
            )
        assembly = assemble(probes, requirements)
        stale = await self._verify_sources(assembly.sites)
        if stale is not None:
            return stale
        sections = list(assembly.sections)
        failed_sections = [
            {
                "content_type": item.get("content_type"),
                "label": item.get("label"),
                "message": item.get("message") or "栏目不可用。",
            }
            for item in sections
            if item.get("status") != "ok"
        ]
        for missing in assembly.missing:
            failed_sections.append({
                "content_type": missing["content_type"],
                "label": missing["label"],
                "message": (
                    f"没有候选站点能提供所需条数（需要 "
                    f"{missing['required']} 条，最多 {missing['available']} 条）。"
                    if missing.get("required")
                    else "没有候选站点提供该栏目。"
                ),
            })
        success_count = len(
            [item for item in sections if item.get("status") == "ok"],
        )
        data = {
            "draft": True,
            "task_name": name,
            "image_style": image_style,
            "items": plan_payload(items),
            "site_id": assembly.site_id if assembly.mode == "single" else "",
            "site_name": (
                assembly.site_name if assembly.mode == "single"
                else ("多站点" if assembly.mode == "multi" else None)
            ),
            "source_mode": assembly.mode,
            "sites": [
                {"site_id": site_id, "revision": revision}
                for site_id, revision in assembly.sites
            ],
            "assignments": list(assembly.assignments),
            "missing": list(assembly.missing),
            "success_count": success_count,
            "failure_count": len(failed_sections),
            "sections": sections,
            "failed_sections": failed_sections,
        }
        if success_count == 0:
            data["status"] = "failed"
        elif failed_sections:
            data["status"] = "partial"
        else:
            data["status"] = "complete"
        if success_count == 0:
            return _error(
                502,
                "没有候选站点能提供所需栏目。",
                code="content_unavailable",
                data=data,
            )
        data["outputs"] = render_profile_outputs(data)
        if self._image_snapshots is not None:
            try:
                snapshot = self._image_snapshots.put(
                    owner=owner or "",
                    profile_id="",
                    profile_revision=0,
                    sites=assembly.sites,
                    data={**_image_snapshot_data(data), "draft": True},
                    scope="draft",
                )
            except ImageSnapshotError:
                snapshot = None
            if snapshot is not None:
                data["image_snapshot"] = {
                    "id": snapshot.id,
                    "expires_at": snapshot.expires_at,
                }
        return _ok(data)

    async def generate_draft_image(
        self,
        payload: object,
        *,
        owner: str | None = None,
    ) -> PlanPreviewResult:
        """渲染草稿预览的快照；不触碰任何已保存任务。"""
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return _error(
                400,
                "snapshot_id 必须是非空字符串。",
                code="invalid_snapshot_id",
            )
        if self._image_snapshots is None or self._image_service is None:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        try:
            snapshot = self._image_snapshots.get(snapshot_id.strip())
        except ImageSnapshotClosedError:
            return _error(
                503,
                "图片快照当前不可用。",
                code="snapshot_unavailable",
            )
        if snapshot is None:
            return _error(
                410,
                "图片快照已过期或被淘汰，请重新预览。",
                code="snapshot_expired",
            )
        if (owner or "") != snapshot.owner:
            return _error(
                403,
                "无权使用该图片快照。",
                code="forbidden",
            )
        if not snapshot.data.get("draft"):
            # 已保存任务的快照只能走任务图片入口。
            return _error(
                404,
                "图片快照不属于当前预览。",
                code="snapshot_not_found",
            )
        stale = await self._check_snapshot_sites(snapshot)
        if stale is not None:
            return stale
        try:
            section_images = await self._image_service.render(
                snapshot.data,
                created_at=snapshot.created_at,
            )
        except ImageTimeoutError:
            return _error(
                504,
                "图片生成超时，请稍后重试。",
                code="image_timeout",
            )
        except FontUnavailableError:
            return _error(
                503,
                "宿主缺少可用的中文字体，无法生成图片。",
                code="font_unavailable",
            )
        except ImageLimitError:
            return _error(
                422,
                "图片内容超过高度或大小限制，请减少栏目或条数后重试。",
                code="image_limit_exceeded",
            )
        except ImageServiceClosedError:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        except (ImageRenderFailedError, ProfileImageError):
            return _error(
                422,
                "图片渲染失败，文字和 Markdown 仍可使用。",
                code="image_render_failed",
            )
        except Exception:
            return _error(
                500,
                "图片渲染失败，文字和 Markdown 仍可使用。",
                code="image_internal_error",
            )
        # 渲染期间来源站点被改配置：这张图不再对应当前内容。
        stale = await self._check_snapshot_sites(snapshot)
        if stale is not None:
            return stale
        return self._image_result(section_images, snapshot.id)

    async def _collect_sections(
        self,
        plan_items: tuple[PlanItem, ...],
        force_refresh: bool,
        site_id: str,
        *,
        use_cache: bool = True,
        deadline: float | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """按给定栏目方案获取全部栏目；失败栏目单独记录，不中断其余栏目。

        给出 deadline 时，每个栏目只使用剩余时间在尚未取数的栏目间的一份：
        某个栏目超时或耗尽预算只把该栏目标记为失败，已经成功取到的栏目
        仍保留在结果里，供按栏拼合使用。
        """
        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        total = len(plan_items)
        for position, item in enumerate(plan_items):
            try:
                budget: float | None = None
                if deadline is not None:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise _PreviewBudgetExceeded()
                    budget = remaining / max(1, total - position)
                # 必须使用本次实际选中的站点，否则会出现“标记 B、内容来自 A”。
                call = self._content.preview_cached(
                    item.content_type,
                    site_id,
                    force_refresh=force_refresh,
                    use_cache=use_cache,
                )
                cached = (
                    await asyncio.wait_for(call, timeout=budget)
                    if budget is not None else await call
                )
            except (asyncio.TimeoutError, _PreviewBudgetExceeded):
                section = _section_error(
                    item.content_type,
                    item.label,
                    _PreviewBudgetExceeded(),
                )
                sections.append(section)
                failures.append(section)
                continue
            except SiteStorageError:
                # 存储故障不是“该栏没有内容”：向上传播为整次预览失败，
                # 绝不能降级成缺栏后触发异常提示/发送。
                raise
            except Exception as exc:
                section = _section_error(item.content_type, item.label, exc)
                sections.append(section)
                failures.append(section)
                continue

            try:
                snapshot = content_snapshot_dict(
                    item.content_type,
                    cached.snapshot,
                )
            except Exception:
                section = _section_error(
                    item.content_type,
                    item.label,
                    ValueError("serialize failed"),
                )
                sections.append(section)
                failures.append(section)
                continue

            all_items = snapshot.get("items")
            total = len(all_items) if isinstance(all_items, list) else 0
            if item.limit is not None and isinstance(all_items, list):
                snapshot["items"] = all_items[: item.limit]
            shown = (
                len(snapshot["items"])
                if isinstance(snapshot.get("items"), list)
                else 0
            )
            snapshot["item_count"] = shown
            snapshot["total_item_count"] = total
            snapshot["displayed_count"] = shown
            snapshot["limit"] = item.limit
            snapshot["label"] = item.label
            snapshot["status"] = "ok"
            if use_cache:
                snapshot["cache"] = {
                    "hit": cached.hit,
                    "expires_at": cached.expires_at,
                    "ttl_seconds": self._content.cache_ttl_seconds,
                }
            else:
                # 草稿直接取数：明确标注未使用生产缓存。
                snapshot["cache"] = {"used": False}
            sections.append(snapshot)

        return sections, failures

    async def generate_image(
        self,
        plan_id: str,
        payload: object,
        *,
        owner: str | None = None,
    ) -> PlanPreviewResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id.strip():
            return _error(
                400,
                "snapshot_id 必须是非空字符串。",
                code="invalid_snapshot_id",
            )
        if self._image_snapshots is None or self._image_service is None:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        try:
            snapshot = self._image_snapshots.get(snapshot_id.strip())
        except ImageSnapshotClosedError:
            return _error(
                503,
                "图片快照当前不可用。",
                code="snapshot_unavailable",
            )
        if snapshot is None:
            return _error(
                410,
                "图片快照已过期或被淘汰，请重新预览。",
                code="snapshot_expired",
            )
        if (owner or "") != snapshot.owner:
            return _error(
                403,
                "无权使用该图片快照。",
                code="forbidden",
            )
        if snapshot.profile_id != plan_id:
            return _error(
                404,
                "图片快照不属于该任务。",
                code="snapshot_not_found",
            )
        stale = await self._snapshot_outdated(plan_id, snapshot)
        if stale is not None:
            return stale

        try:
            section_images = await self._image_service.render(
                snapshot.data,
                created_at=snapshot.created_at,
            )
        except ImageTimeoutError:
            return _error(
                504,
                "图片生成超时，请稍后重试。",
                code="image_timeout",
            )
        except FontUnavailableError:
            return _error(
                503,
                "宿主缺少可用的中文字体，无法生成图片。",
                code="font_unavailable",
            )
        except ImageLimitError:
            return _error(
                422,
                "图片内容超过高度或大小限制，请减少栏目或条数后重试。",
                code="image_limit_exceeded",
            )
        except ImageServiceClosedError:
            return _error(
                503,
                "图片渲染当前不可用。",
                code="image_unavailable",
            )
        except (ImageRenderFailedError, ProfileImageError):
            return _error(
                422,
                "图片渲染失败，文字和 Markdown 仍可使用。",
                code="image_render_failed",
            )
        except Exception:
            return _error(
                500,
                "图片渲染失败，文字和 Markdown 仍可使用。",
                code="image_internal_error",
            )

        # 渲染期间任务可能被保存成别的一版：此时手里这张图属于旧样式或旧栏目，
        # 不能再按 200 返回；必须与前置校验一致地判定快照过期。
        stale = await self._snapshot_outdated(plan_id, snapshot)
        if stale is not None:
            return stale

        return self._image_result(section_images, snapshot.id)

    @staticmethod
    def _image_result(
        section_images: Any,
        snapshot_id: str,
    ) -> PlanPreviewResult:
        """把渲染结果整理成图片响应；成功/失败的栏目都保留具体原因。"""
        images: list[dict[str, Any]] = []
        failed_sections: list[str] = []
        total_png_bytes = 0
        for position, section_image in enumerate(section_images, start=1):
            entry = _section_image_payload(section_image, position)
            if entry["status"] == "failed":
                failed_sections.append(entry["label"])
                images.append(entry)
                continue
            png = getattr(section_image, "png", None)
            if not isinstance(png, bytes) or not png:
                # 渲染器声明成功却没有图片：按该栏目失败处理，不伪造成功。
                entry["status"] = "failed"
                entry["error"] = "render_failed"
                failed_sections.append(entry["label"])
                images.append(entry)
                continue
            total_png_bytes += len(png)
            if total_png_bytes > DEFAULT_MAX_RENDER_OUTPUT_BYTES:
                return _error(
                    422,
                    "图片总大小超过上限，请减少栏目或条数后重试。",
                    code="image_limit_exceeded",
                )
            entry["png_base64"] = base64.b64encode(png).decode("ascii")
            images.append(entry)
        if not any(entry.get("png_base64") for entry in images):
            # 全部栏目都失败时和「全部栏目获取失败」一致：不返回成功图片。
            return _error(
                422,
                "所有栏目图片生成失败，请减少栏目或条数后重试；文字和 Markdown 仍可使用。",
                code="image_render_failed",
                data={"images": images, "failed_sections": failed_sections},
            )
        return _ok(
            {
                "images": images,
                "failed_sections": failed_sections,
                "snapshot_id": snapshot_id,
            },
        )

    async def _snapshot_outdated(
        self,
        plan_id: str,
        snapshot: Any,
    ) -> PlanPreviewResult | None:
        """按最新任务与站点配置复核快照；返回 None 表示仍然有效。

        方案 revision 必须一致；站点则校验快照记录的那一个，这样故障切换
        选中的备用站点快照不会被误判为过期。样式也属于这一版配置。
        """
        try:
            config = await self._tasks.get_config_snapshot(plan_id)
        except (TaskNotFoundError, TaskValidationError):
            return _stale_snapshot_error()
        except (TaskStorageError, SiteStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        if config.plan_revision != snapshot.profile_revision:
            return _stale_snapshot_error()
        if resolve_style_id(config.task.image_style) != resolve_style_id(
            snapshot.data.get("image_style"),
        ):
            return _stale_snapshot_error()
        stale = await self._check_snapshot_sites(snapshot)
        if stale is not None:
            return stale
        return None


def _section_error(
    content_type: str,
    label: str,
    exc: Exception,
) -> dict[str, Any]:
    code, message = _classify_error(exc)
    return {
        "content_type": content_type,
        "label": label,
        "status": "error",
        "code": code,
        "message": message,
    }


def _classify_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, _PreviewBudgetExceeded):
        return "section_timeout", "栏目获取超出本次预览时限。"
    if isinstance(exc, SiteDisabledError):
        return "site_disabled", "站点未启用。"
    if isinstance(exc, SiteNotFoundError):
        return "site_not_found", "站点不存在。"
    if isinstance(exc, NewsTimeoutError):
        return "upstream_timeout", "上游请求超时。"
    if isinstance(exc, NewsTooLargeError):
        return "upstream_too_large", "上游响应超过大小限制。"
    if isinstance(exc, NewsInvalidJSONError):
        return "upstream_invalid_json", "上游返回的内容不是有效 JSON。"
    if isinstance(exc, NewsBusinessError):
        return "upstream_business_error", f"上游返回业务错误（code {exc.code}）。"
    if isinstance(exc, NewsStructureError):
        return "upstream_structure", "上游 JSON 结构不符合接口约定。"
    if isinstance(exc, NewsHTTPStatusError):
        return (
            "upstream_http_error",
            f"上游站点返回 HTTP {exc.status_code}。",
        )
    if isinstance(exc, NewsRedirectError):
        return "upstream_redirect", "上游站点返回重定向，已拒绝跟随。"
    if isinstance(exc, NewsTransportError):
        return "upstream_unreachable", "无法连接上游站点。"
    if isinstance(exc, NewsClientClosedError):
        return "upstream_unavailable", "上游客户端当前不可用。"
    return "internal_error", "栏目获取失败。"


def _section_image_payload(section_image: Any, position: int) -> dict[str, Any]:
    """把一个栏目长图整理成冻结的响应结构，不回显渲染器内部异常。"""
    raw_index = getattr(section_image, "index", position)
    index = raw_index if type(raw_index) is int and raw_index >= 1 else position
    content_type = _safe_text(getattr(section_image, "content_type", ""))
    label = _safe_text(getattr(section_image, "label", "")) or CONTENT_LABELS.get(
        content_type,
        "栏目",
    )
    raw_status = getattr(section_image, "status", "")
    known_status = raw_status in _SECTION_IMAGE_STATUSES
    status = raw_status if known_status else "failed"
    payload: dict[str, Any] = {
        "index": index,
        "content_type": content_type,
        "label": label,
        "status": status,
        "width": _safe_size(getattr(section_image, "width", 0)),
        "height": _safe_size(getattr(section_image, "height", 0)),
    }
    if status == "failed":
        payload["error"] = (
            _safe_error_code(getattr(section_image, "error", None))
            if known_status else "render_failed"
        )
    else:
        # 成功出图但摘要被有界缩略时带上提示；正常排版不带该字段。
        note = _safe_text(getattr(section_image, "note", None))
        if note:
            payload["note"] = note
    return payload


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if ord(char) < 32 or ord(char) == 127 else char
        for char in value
    )
    return " ".join(text.split())[:200]


def _safe_size(value: Any) -> int:
    if type(value) is int and value >= 0:
        return value
    return 0


def _safe_error_code(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if _SAFE_ERROR_CODE.fullmatch(candidate):
            return candidate
    return "render_failed"


def _image_snapshot_data(data: dict[str, Any]) -> dict[str, Any]:
    """只保存渲染需要的已验证数据，不保存 HTML 或前端路径。"""
    return {
        # 渲染器沿用 profile_name 作为标题，这里由任务名映射过去。
        "profile_name": data.get("task_name"),
        # 样式随同一版快照固定，之后改任务样式不会改写已生成的快照。
        "image_style": data.get("image_style") or "default",
        "site_name": data.get("site_name"),
        "status": data.get("status"),
        "success_count": data.get("success_count"),
        "failure_count": data.get("failure_count"),
        "sections": data.get("sections"),
    }


def _stale_snapshot_error() -> PlanPreviewResult:
    return _error(
        409,
        "任务或站点配置已变化，请重新预览后再生成图片。",
        code="snapshot_stale",
    )


def _preview_conflict_error() -> PlanPreviewResult:
    return _error(
        409,
        "站点配置在获取期间发生变化，本次预览已取消，请重试。",
        code="preview_revision_changed",
    )


def _payload_dict(payload: object) -> dict[str, Any] | None:
    return payload if isinstance(payload, dict) else None


def _ok(data: Any, *, status_code: int = 200) -> PlanPreviewResult:
    return PlanPreviewResult(
        status_code=status_code,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    *,
    code: str,
    data: dict[str, Any] | None = None,
) -> PlanPreviewResult:
    details = {"code": code}
    if data:
        details.update(data)
    return PlanPreviewResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _invalid_body_error() -> PlanPreviewResult:
    return _error(
        400,
        "请求体必须是 JSON 对象。",
        code="invalid_body",
    )


def _validation_error(exc: Exception) -> PlanPreviewResult:
    field = getattr(exc, "field", "request")
    return _error(
        400,
        str(exc),
        code="validation_error",
        data={"field": field},
    )


def _task_not_found_error() -> PlanPreviewResult:
    return _error(404, "推送任务不存在或已被删除。", code="not_found")


def _site_not_found_error() -> PlanPreviewResult:
    return _error(404, "引用的站点不存在。", code="site_not_found")


def _storage_error() -> PlanPreviewResult:
    return _error(
        503,
        "配置存储暂不可用，请稍后重试。",
        code="storage_unavailable",
    )


def _internal_error() -> PlanPreviewResult:
    return _error(
        500,
        "服务器内部错误，请稍后重试。",
        code="internal_error",
    )


__all__ = ["PlanPreview", "PlanPreviewResult"]
