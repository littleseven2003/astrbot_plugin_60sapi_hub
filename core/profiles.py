"""内容组合的领域模型、校验和持久化服务。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .content_types import CONTENT_LABELS, KNOWN_CONTENT_TYPES
from .site_store import (
    ProfileRecordNotFoundError,
    ProfileRevisionConflictError,
    StoredProfile,
    StoredProfileItem,
)
from .sites import (
    SiteConfig,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
    SiteValidationError,
    normalize_revision,
    normalize_site_id,
    normalize_site_name,
)

MAX_PROFILE_NAME_LENGTH = 80
MAX_PROFILE_ID_LENGTH = 64
MAX_PROFILE_ITEMS = 5
MAX_ITEM_LIMIT = 1000


class ProfileError(Exception):
    """内容组合错误基类。"""


class ProfileValidationError(ProfileError):
    """内容组合输入不合法。"""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} 无效：{reason}")


class ProfileNotFoundError(ProfileError):
    """内容组合不存在。"""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        super().__init__(f"内容组合 {profile_id} 不存在。")


class ProfileConflictError(ProfileError):
    """内容组合 revision 冲突。"""

    def __init__(
        self,
        profile_id: str,
        expected_revision: int,
        actual_revision: int | None,
    ) -> None:
        self.profile_id = profile_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        actual = "未知" if actual_revision is None else str(actual_revision)
        super().__init__(
            f"内容组合 {profile_id} 的 revision 冲突："
            f"期望 {expected_revision}，当前 {actual}。",
        )


class ProfileStorageError(ProfileError):
    """内容组合存储不可用。"""


@dataclass(frozen=True, slots=True)
class ProfileItem:
    """组合中的一个固定内容栏目。"""

    content_type: str
    params: tuple[tuple[str, str], ...] = ()
    limit: int | None = None

    @property
    def label(self) -> str:
        return CONTENT_LABELS[self.content_type]


@dataclass(frozen=True, slots=True)
class ContentProfile:
    """持久化的内容组合。"""

    id: str
    name: str
    site_id: str
    items: tuple[ProfileItem, ...]
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ProfileCreate:
    name: object
    site_id: object
    items: object


@dataclass(frozen=True, slots=True)
class ProfileUpdate:
    name: object
    site_id: object
    items: object
    expected_revision: object


class ContentProfileService:
    """内容组合 CRUD；与站点共用同一个 SQLite 连接和事务锁。"""

    def __init__(self, sites: SiteService) -> None:
        self._sites = sites
        self._store = sites.store

    async def list_profiles(self) -> list[ContentProfile]:
        try:
            stored = await self._store.list_profiles()
        except Exception as exc:
            raise ProfileStorageError("读取内容组合失败。") from exc
        return [_to_profile(item) for item in stored]

    async def get_profile(self, profile_id: object) -> ContentProfile:
        normalized_id = normalize_profile_id(profile_id)
        try:
            stored = await self._store.get_profile(normalized_id)
        except ProfileRecordNotFoundError as exc:
            raise ProfileNotFoundError(exc.profile_id) from exc
        except Exception as exc:
            raise ProfileStorageError("读取内容组合失败。") from exc
        return _to_profile(stored)

    async def create_profile(self, data: ProfileCreate) -> ContentProfile:
        name = _normalize_name(data.name)
        site_id = normalize_site_id(data.site_id)
        items = normalize_profile_items(data.items)
        await self._require_site(site_id)
        try:
            stored = await self._store.create_profile(
                profile_id=uuid.uuid4().hex,
                name=name,
                site_id=site_id,
                items=_to_stored_items(items),
            )
        except Exception as exc:
            raise ProfileStorageError("保存内容组合失败。") from exc
        return _to_profile(stored)

    async def update_profile(
        self,
        profile_id: object,
        data: ProfileUpdate,
    ) -> ContentProfile:
        normalized_id = normalize_profile_id(profile_id)
        expected_revision = normalize_revision(data.expected_revision)
        name = _normalize_name(data.name)
        site_id = normalize_site_id(data.site_id)
        items = normalize_profile_items(data.items)
        await self._require_site(site_id)
        try:
            stored = await self._store.update_profile(
                normalized_id,
                name=name,
                site_id=site_id,
                items=_to_stored_items(items),
                expected_revision=expected_revision,
            )
        except ProfileRecordNotFoundError as exc:
            raise ProfileNotFoundError(exc.profile_id) from exc
        except ProfileRevisionConflictError as exc:
            raise ProfileConflictError(
                normalized_id,
                expected_revision,
                exc.actual_revision,
            ) from exc
        except Exception as exc:
            raise ProfileStorageError("更新内容组合失败。") from exc
        return _to_profile(stored)

    async def delete_profile(
        self,
        profile_id: object,
        expected_revision: object,
    ) -> None:
        normalized_id = normalize_profile_id(profile_id)
        normalized_revision = normalize_revision(expected_revision)
        try:
            await self._store.delete_profile(
                normalized_id,
                expected_revision=normalized_revision,
            )
        except ProfileRecordNotFoundError as exc:
            raise ProfileNotFoundError(exc.profile_id) from exc
        except ProfileRevisionConflictError as exc:
            raise ProfileConflictError(
                normalized_id,
                normalized_revision,
                exc.actual_revision,
            ) from exc
        except Exception as exc:
            raise ProfileStorageError("删除内容组合失败。") from exc

    async def load_for_preview(
        self,
        profile_id: object,
    ) -> tuple[ContentProfile, SiteConfig]:
        profile = await self.get_profile(profile_id)
        try:
            site = await self._sites.get_site(profile.site_id)
        except SiteNotFoundError:
            raise ProfileStorageError("内容组合引用的站点不存在。") from None
        if not site.enabled:
            raise SiteDisabledError(site.id)
        return profile, site

    async def _require_site(self, site_id: str) -> None:
        try:
            await self._sites.get_site(site_id)
        except SiteNotFoundError:
            raise
        except SiteStorageError:
            raise


def normalize_profile_id(value: object) -> str:
    if not isinstance(value, str):
        raise ProfileValidationError("profile_id", "必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise ProfileValidationError("profile_id", "不能为空")
    if len(normalized) > MAX_PROFILE_ID_LENGTH:
        raise ProfileValidationError(
            "profile_id",
            f"长度不能超过 {MAX_PROFILE_ID_LENGTH} 个字符",
        )
    if not normalized.replace("_", "").replace("-", "").isalnum():
        raise ProfileValidationError(
            "profile_id",
            "只能包含字母、数字、下划线和连字符",
        )
    return normalized


def _normalize_name(value: object) -> str:
    try:
        return normalize_site_name(value)
    except SiteValidationError as exc:
        raise ProfileValidationError("name", exc.reason) from exc


def normalize_profile_items(value: object) -> tuple[ProfileItem, ...]:
    if not isinstance(value, list):
        raise ProfileValidationError("items", "必须是数组")
    if not 1 <= len(value) <= MAX_PROFILE_ITEMS:
        raise ProfileValidationError(
            "items",
            f"必须包含 1～{MAX_PROFILE_ITEMS} 个栏目",
        )
    items: list[ProfileItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        field = f"items[{index}]"
        if not isinstance(raw, dict):
            raise ProfileValidationError(field, "必须是对象")
        content_type = raw.get("content_type")
        if not isinstance(content_type, str) or content_type not in KNOWN_CONTENT_TYPES:
            raise ProfileValidationError(
                f"{field}.content_type",
                "不是支持的内容类型",
            )
        if content_type in seen:
            raise ProfileValidationError(
                f"{field}.content_type",
                "同一组合不能重复选择内容类型",
            )
        seen.add(content_type)
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise ProfileValidationError(
                f"{field}.params",
                "必须是空对象",
            )
        if params:
            raise ProfileValidationError(
                f"{field}.params",
                "当前接口不支持非空参数",
            )
        limit = raw.get("limit")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ProfileValidationError(
                    f"{field}.limit",
                    "必须是 null 或整数",
                )
            if not 1 <= limit <= MAX_ITEM_LIMIT:
                raise ProfileValidationError(
                    f"{field}.limit",
                    f"必须在 1～{MAX_ITEM_LIMIT} 之间",
                )
        items.append(ProfileItem(content_type=content_type, limit=limit))
    return tuple(items)


def _to_stored_items(items: tuple[ProfileItem, ...]) -> tuple[StoredProfileItem, ...]:
    return tuple(
        StoredProfileItem(
            content_type=item.content_type,
            params_json="{}",
            limit=item.limit,
        )
        for item in items
    )


def _to_profile(stored: StoredProfile) -> ContentProfile:
    return ContentProfile(
        id=stored.id,
        name=stored.name,
        site_id=stored.site_id,
        items=tuple(
            ProfileItem(
                content_type=item.content_type,
                limit=item.limit,
            )
            for item in stored.items
        ),
        revision=stored.revision,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


__all__ = [
    "ContentProfile",
    "ContentProfileService",
    "MAX_ITEM_LIMIT",
    "MAX_PROFILE_ITEMS",
    "ProfileConflictError",
    "ProfileCreate",
    "ProfileError",
    "ProfileItem",
    "ProfileNotFoundError",
    "ProfileStorageError",
    "ProfileUpdate",
    "ProfileValidationError",
    "normalize_profile_id",
    "normalize_profile_items",
]
