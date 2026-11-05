"""内容组合 CRUD 和组合预览的后台 API 业务层。"""

from __future__ import annotations

import base64
import re
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
from .profiles import (
    ContentProfile,
    ContentProfileService,
    ProfileConflictError,
    ProfileCreate,
    ProfileNotFoundError,
    ProfileReferencedError,
    ProfileStorageError,
    ProfileUpdate,
    ProfileValidationError,
)
from .sites import (
    SiteConfig,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
    SiteValidationError,
)

_SECTION_IMAGE_STATUSES = frozenset({"success", "empty", "failed"})
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True, slots=True)
class ProfileApiResult:
    status_code: int
    body: dict[str, Any]


class ProfileApi:
    """内容组合持久化和组合预览。"""

    def __init__(
        self,
        profiles: ContentProfileService,
        content: ContentService,
        sites: SiteService,
        *,
        image_snapshots: ImageSnapshotStore | None = None,
        image_service: ProfileImageService | None = None,
    ) -> None:
        self._profiles = profiles
        self._content = content
        self._sites = sites
        self._image_snapshots = image_snapshots
        self._image_service = image_service

    async def list_profiles(self) -> ProfileApiResult:
        try:
            profiles = await self._profiles.list_profiles()
            sites = {site.id: site for site in await self._sites.list_sites()}
        except SiteStorageError:
            return _storage_error()
        except ProfileStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(
            {
                "profiles": [
                    _profile_dict(profile, sites.get(profile.site_id))
                    for profile in profiles
                ],
            },
        )

    async def get_profile(self, profile_id: str) -> ProfileApiResult:
        try:
            profile = await self._profiles.get_profile(profile_id)
            site = await self._sites.get_site(profile.site_id)
        except ProfileValidationError as exc:
            return _validation_error(exc)
        except ProfileNotFoundError:
            return _not_found_error()
        except SiteNotFoundError:
            return _site_not_found_error()
        except (SiteStorageError, ProfileStorageError):
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_profile_dict(profile, site))

    async def create_profile(self, payload: object) -> ProfileApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            profile = await self._profiles.create_profile(
                ProfileCreate(
                    name=data.get("name"),
                    site_id=data.get("site_id"),
                    items=data.get("items"),
                ),
            )
        except (ProfileValidationError, SiteValidationError) as exc:
            return _validation_error(exc)
        except SiteNotFoundError:
            return _site_not_found_error()
        except ProfileStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(
            _profile_dict(profile, await _safe_site(self._sites, profile.site_id)),
            status_code=201,
        )

    async def update_profile(
        self,
        profile_id: str,
        payload: object,
    ) -> ProfileApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            profile = await self._profiles.update_profile(
                profile_id,
                ProfileUpdate(
                    name=data.get("name"),
                    site_id=data.get("site_id"),
                    items=data.get("items"),
                    expected_revision=data.get("expected_revision"),
                ),
            )
        except (ProfileValidationError, SiteValidationError) as exc:
            return _validation_error(exc)
        except ProfileNotFoundError:
            return _not_found_error()
        except SiteNotFoundError:
            return _site_not_found_error()
        except ProfileConflictError as exc:
            return _conflict_error(exc)
        except ProfileStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(
            _profile_dict(profile, await _safe_site(self._sites, profile.site_id)),
        )

    async def delete_profile(
        self,
        profile_id: str,
        payload: object,
    ) -> ProfileApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            await self._profiles.delete_profile(
                profile_id,
                data.get("expected_revision"),
            )
        except (ProfileValidationError, SiteValidationError) as exc:
            return _validation_error(exc)
        except ProfileNotFoundError:
            return _not_found_error()
        except ProfileConflictError as exc:
            return _conflict_error(exc)
        except ProfileReferencedError as exc:
            return _error(
                409,
                "内容组合仍被推送任务引用，不能删除："
                + "、".join(exc.task_names)
                + "。",
                code="profile_referenced",
                data={"task_names": list(exc.task_names)},
            )
        except ProfileStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok({"deleted_id": profile_id})

    async def preview_profile(
        self,
        payload: object,
        *,
        owner: str | None = None,
    ) -> ProfileApiResult:
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
        profile_id = data.get("profile_id")
        try:
            profile, site = await self._profiles.load_for_preview(profile_id)
        except ProfileValidationError as exc:
            return _validation_error(exc)
        except ProfileNotFoundError:
            return _not_found_error()
        except SiteDisabledError:
            return _error(
                409,
                "组合引用的站点未启用，无法获取上游内容。",
                code="site_disabled",
            )
        except SiteNotFoundError:
            return _site_not_found_error()
        except ProfileStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()

        sections: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for item in profile.items:
            try:
                cached = await self._content.preview_cached(
                    item.content_type,
                    profile.site_id,
                    force_refresh=force_refresh,
                )
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
            snapshot["cache"] = {
                "hit": cached.hit,
                "expires_at": cached.expires_at,
                "ttl_seconds": self._content.cache_ttl_seconds,
            }
            sections.append(snapshot)

        success_count = len(sections) - len(failures)
        data = {
            "profile_id": profile.id,
            "profile_name": profile.name,
            "profile_revision": profile.revision,
            "site_id": site.id,
            "site_name": site.name,
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
        if self._image_snapshots is not None:
            try:
                snapshot = self._image_snapshots.put(
                    owner=owner or "",
                    profile_id=profile.id,
                    profile_revision=profile.revision,
                    site_id=site.id,
                    site_revision=site.revision,
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

    async def generate_image(
        self,
        profile_id: str,
        payload: object,
        *,
        owner: str | None = None,
    ) -> ProfileApiResult:
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
                "图片快照已过期或被淘汰，请重新预览组合。",
                code="snapshot_expired",
            )
        if (owner or "") != snapshot.owner:
            return _error(
                403,
                "无权使用该图片快照。",
                code="forbidden",
            )
        if snapshot.profile_id != profile_id:
            return _error(
                404,
                "图片快照不属于该组合。",
                code="snapshot_not_found",
            )
        try:
            profile = await self._profiles.get_profile(profile_id)
        except ProfileNotFoundError:
            return _stale_snapshot_error()
        except (ProfileStorageError, ProfileValidationError):
            return _storage_error()
        except Exception:
            return _internal_error()
        if (
            profile.revision != snapshot.profile_revision
            or profile.site_id != snapshot.site_id
        ):
            return _stale_snapshot_error()
        try:
            site = await self._sites.get_site(profile.site_id)
        except SiteNotFoundError:
            return _stale_snapshot_error()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        if not site.enabled or site.revision != snapshot.site_revision:
            return _stale_snapshot_error()

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
                "snapshot_id": snapshot.id,
            },
        )


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


async def _safe_site(
    sites: SiteService,
    site_id: str,
) -> SiteConfig | None:
    try:
        return await sites.get_site(site_id)
    except Exception:
        return None


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
        "profile_name": data.get("profile_name"),
        "site_name": data.get("site_name"),
        "status": data.get("status"),
        "success_count": data.get("success_count"),
        "failure_count": data.get("failure_count"),
        "sections": data.get("sections"),
    }


def _stale_snapshot_error() -> ProfileApiResult:
    return _error(
        409,
        "组合或站点配置已变化，请重新预览后再生成图片。",
        code="snapshot_stale",
    )


def _profile_dict(
    profile: ContentProfile,
    site: SiteConfig | None,
) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "site_id": profile.site_id,
        "site_name": site.name if site else "未知站点",
        "site_enabled": site.enabled if site else False,
        "items": [
            {
                "content_type": item.content_type,
                "label": item.label,
                "params": {},
                "limit": item.limit,
            }
            for item in profile.items
        ],
        "revision": profile.revision,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _payload_dict(payload: object) -> dict[str, Any] | None:
    return payload if isinstance(payload, dict) else None


def _ok(data: Any, *, status_code: int = 200) -> ProfileApiResult:
    return ProfileApiResult(
        status_code=status_code,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    *,
    code: str,
    data: dict[str, Any] | None = None,
) -> ProfileApiResult:
    details = {"code": code}
    if data:
        details.update(data)
    return ProfileApiResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _invalid_body_error() -> ProfileApiResult:
    return _error(
        400,
        "请求体必须是 JSON 对象。",
        code="invalid_body",
    )


def _validation_error(exc: Exception) -> ProfileApiResult:
    field = getattr(exc, "field", "request")
    return _error(
        400,
        str(exc),
        code="validation_error",
        data={"field": field},
    )


def _not_found_error() -> ProfileApiResult:
    return _error(404, "内容组合不存在或已被删除。", code="not_found")


def _site_not_found_error() -> ProfileApiResult:
    return _error(404, "引用的站点不存在。", code="site_not_found")


def _conflict_error(exc: ProfileConflictError) -> ProfileApiResult:
    return _error(
        409,
        "内容组合已被其他操作修改，请重新读取后再保存。",
        code="revision_conflict",
        data={
            "expected_revision": exc.expected_revision,
            "actual_revision": exc.actual_revision,
        },
    )


def _storage_error() -> ProfileApiResult:
    return _error(
        503,
        "内容组合存储暂不可用，请稍后重试。",
        code="storage_unavailable",
    )


def _internal_error() -> ProfileApiResult:
    return _error(
        500,
        "服务器内部错误，请稍后重试。",
        code="internal_error",
    )


__all__ = ["ProfileApi", "ProfileApiResult"]
