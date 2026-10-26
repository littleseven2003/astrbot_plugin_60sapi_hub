"""站点配置的领域模型、输入校验与站点管理服务。"""

from __future__ import annotations

import ipaddress
import math
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

from .site_store import (
    SCHEMA_VERSION,
    SiteRecordNotFoundError,
    SiteRevisionConflictError,
    SiteStore,
    SiteStoreClosedError,
    SiteStoreError,
    StoredSite,
)

DEFAULT_TIMEOUT_SECONDS = 10.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 120.0

MAX_SITE_NAME_LENGTH = 80
MAX_BASE_URL_LENGTH = 2048
MAX_BEARER_TOKEN_LENGTH = 4096
MAX_SITE_ID_LENGTH = 64

_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?")
_IPV4_LIKE_RE = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_SITE_ID_RE = re.compile(r"[A-Za-z0-9_-]+")


class SiteError(Exception):
    """站点服务错误基类。"""


class SiteValidationError(SiteError):
    """输入字段不合法。"""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} 无效：{reason}")


class SiteNotFoundError(SiteError):
    """指定站点不存在。"""

    def __init__(self, site_id: str) -> None:
        self.site_id = site_id
        super().__init__(f"站点 {site_id} 不存在。")


class SiteConflictError(SiteError):
    """编辑或删除使用了过期的 revision。"""

    def __init__(
        self,
        site_id: str,
        expected_revision: int,
        actual_revision: int | None,
    ) -> None:
        self.site_id = site_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        actual = "未知" if actual_revision is None else str(actual_revision)
        super().__init__(
            f"站点 {site_id} 的 revision 冲突：期望 {expected_revision}，当前 {actual}。",
        )


class SiteStorageError(SiteError):
    """站点持久化不可用或执行失败。"""


class SiteDisabledError(SiteError):
    """站点存在但未启用，不能用于上游获取。"""

    def __init__(self, site_id: str) -> None:
        self.site_id = site_id
        super().__init__(f"站点 {site_id} 未启用。")


class CredentialAction(str, Enum):
    """编辑站点时对 Bearer 凭据的处理方式。"""

    KEEP = "keep"
    SET = "set"
    CLEAR = "clear"


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """面向调用方的站点视图，不包含凭据原文。"""

    id: str
    name: str
    base_url: str
    enabled: bool
    timeout_seconds: float
    has_bearer_token: bool
    revision: int
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SiteFetchConfig:
    """仅供内部获取层使用的站点连接配置，凭据不进入公开视图。"""

    id: str
    name: str
    base_url: str
    timeout_seconds: float
    revision: int
    bearer_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SiteCreate:
    """新增站点输入。"""

    name: str
    base_url: str
    enabled: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    bearer_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SiteUpdate:
    """编辑站点输入；凭据通过 credential_action 单独控制。"""

    name: str
    base_url: str
    enabled: bool
    timeout_seconds: float
    expected_revision: int
    credential_action: CredentialAction = CredentialAction.KEEP
    bearer_token: str | None = field(default=None, repr=False)


class SiteService:
    """站点的新增、读取、编辑、删除和持久化入口。"""

    def __init__(self, database_path: str | Path) -> None:
        self._store = SiteStore(database_path)

    @property
    def database_path(self) -> Path:
        return self._store.database_path

    @property
    def is_open(self) -> bool:
        return self._store.is_open

    async def open(self) -> None:
        try:
            await self._store.open()
        except SiteStoreError as exc:
            raise SiteStorageError(str(exc)) from exc

    async def close(self) -> None:
        try:
            await self._store.close()
        except Exception as exc:
            raise SiteStorageError("关闭站点数据库失败。") from exc

    async def create_site(self, data: SiteCreate) -> SiteConfig:
        name = normalize_site_name(data.name)
        base_url = normalize_base_url(data.base_url)
        enabled = normalize_enabled(data.enabled)
        timeout_seconds = normalize_timeout_seconds(data.timeout_seconds)
        bearer_token = normalize_bearer_token(data.bearer_token)

        try:
            stored = await self._store.create_site(
                site_id=uuid.uuid4().hex,
                name=name,
                base_url=base_url,
                enabled=enabled,
                timeout_seconds=timeout_seconds,
                bearer_token=bearer_token,
            )
        except SiteStoreClosedError as exc:
            raise SiteStorageError("站点存储尚未打开。") from exc
        except SiteStoreError as exc:
            raise SiteStorageError("站点写入失败。") from exc
        return _to_public(stored)

    async def list_sites(self) -> list[SiteConfig]:
        try:
            stored_sites = await self._store.list_sites()
        except SiteStoreClosedError as exc:
            raise SiteStorageError("站点存储尚未打开。") from exc
        except SiteStoreError as exc:
            raise SiteStorageError("站点读取失败。") from exc
        return [_to_public(stored) for stored in stored_sites]

    async def get_site(self, site_id: str) -> SiteConfig:
        normalized_id = normalize_site_id(site_id)
        async with self._translate_store_errors(normalized_id):
            stored = await self._store.get_site(normalized_id)
        return _to_public(stored)

    async def get_fetch_config(self, site_id: object) -> SiteFetchConfig:
        """读取已启用站点的内部连接配置；凭据只交给获取层。"""
        normalized_id = normalize_site_id(site_id)
        async with self._translate_store_errors(normalized_id):
            stored = await self._store.get_site(normalized_id)
        if not stored.enabled:
            raise SiteDisabledError(normalized_id)
        return SiteFetchConfig(
            id=stored.id,
            name=stored.name,
            base_url=stored.base_url,
            timeout_seconds=stored.timeout_seconds,
            revision=stored.revision,
            bearer_token=stored.bearer_token,
        )

    async def update_site(self, site_id: str, data: SiteUpdate) -> SiteConfig:
        normalized_id = normalize_site_id(site_id)
        expected_revision = normalize_revision(data.expected_revision)

        async with self._translate_store_errors(
            normalized_id,
            expected_revision=expected_revision,
        ):
            current = await self._store.get_site(normalized_id)
            if current.revision != expected_revision:
                raise SiteConflictError(
                    normalized_id,
                    expected_revision,
                    current.revision,
                )

            name = normalize_site_name(data.name)
            base_url = normalize_base_url(data.base_url)
            enabled = normalize_enabled(data.enabled)
            timeout_seconds = normalize_timeout_seconds(data.timeout_seconds)
            action = normalize_credential_action(data.credential_action)
            bearer_token = resolve_credential_update(
                action,
                data.bearer_token,
                current.bearer_token,
            )

            stored = await self._store.update_site(
                normalized_id,
                name=name,
                base_url=base_url,
                enabled=enabled,
                timeout_seconds=timeout_seconds,
                bearer_token=bearer_token,
                expected_revision=expected_revision,
            )
        return _to_public(stored)

    async def delete_site(self, site_id: str, expected_revision: int) -> None:
        normalized_id = normalize_site_id(site_id)
        normalized_revision = normalize_revision(expected_revision)
        async with self._translate_store_errors(
            normalized_id,
            expected_revision=normalized_revision,
        ):
            await self._store.delete_site(
                normalized_id,
                expected_revision=normalized_revision,
            )

    @asynccontextmanager
    async def _translate_store_errors(
        self,
        site_id: str,
        *,
        expected_revision: int | None = None,
    ) -> AsyncIterator[None]:
        try:
            yield
        except SiteRecordNotFoundError as exc:
            raise SiteNotFoundError(exc.site_id) from exc
        except SiteRevisionConflictError as exc:
            if expected_revision is None:
                raise SiteConflictError(site_id, 0, exc.actual_revision) from exc
            raise SiteConflictError(
                site_id,
                expected_revision,
                exc.actual_revision,
            ) from exc
        except SiteStoreClosedError as exc:
            raise SiteStorageError("站点存储尚未打开。") from exc
        except SiteStoreError as exc:
            raise SiteStorageError("站点存储操作失败。") from exc


def normalize_site_id(value: object) -> str:
    if not isinstance(value, str):
        raise SiteValidationError("site_id", "必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise SiteValidationError("site_id", "不能为空")
    if len(normalized) > MAX_SITE_ID_LENGTH:
        raise SiteValidationError(
            "site_id",
            f"长度不能超过 {MAX_SITE_ID_LENGTH} 个字符",
        )
    if _SITE_ID_RE.fullmatch(normalized) is None:
        raise SiteValidationError("site_id", "只能包含字母、数字、下划线和连字符")
    return normalized


def normalize_site_name(value: object) -> str:
    if not isinstance(value, str):
        raise SiteValidationError("name", "必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise SiteValidationError("name", "不能为空")
    if len(normalized) > MAX_SITE_NAME_LENGTH:
        raise SiteValidationError(
            "name",
            f"长度不能超过 {MAX_SITE_NAME_LENGTH} 个字符",
        )
    if any(
        ord(char) < 32
        or ord(char) == 127
        or char in "\u2028\u2029"
        for char in normalized
    ):
        raise SiteValidationError("name", "不能包含换行或控制字符")
    return normalized


def normalize_enabled(value: object) -> bool:
    if not isinstance(value, bool):
        raise SiteValidationError("enabled", "必须是布尔值")
    return value


def normalize_timeout_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SiteValidationError("timeout_seconds", "必须是数字")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise SiteValidationError("timeout_seconds", "数值超出范围") from exc
    if not math.isfinite(normalized):
        raise SiteValidationError("timeout_seconds", "必须是有限数值")
    if not MIN_TIMEOUT_SECONDS <= normalized <= MAX_TIMEOUT_SECONDS:
        raise SiteValidationError(
            "timeout_seconds",
            f"必须在 {int(MIN_TIMEOUT_SECONDS)}～{int(MAX_TIMEOUT_SECONDS)} 秒之间",
        )
    return normalized


def normalize_bearer_token(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SiteValidationError("bearer_token", "必须是字符串")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > MAX_BEARER_TOKEN_LENGTH:
        raise SiteValidationError(
            "bearer_token",
            f"长度不能超过 {MAX_BEARER_TOKEN_LENGTH} 个字符",
        )
    if any(not 0x21 <= ord(char) <= 0x7E for char in normalized):
        raise SiteValidationError(
            "bearer_token",
            "只能包含可见 ASCII 字符，且不能包含空白",
        )
    return normalized


def normalize_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SiteValidationError("expected_revision", "必须是整数")
    if value < 1:
        raise SiteValidationError("expected_revision", "必须大于等于 1")
    return value


def normalize_credential_action(value: object) -> CredentialAction:
    if isinstance(value, CredentialAction):
        return value
    if isinstance(value, str):
        try:
            return CredentialAction(value.strip().lower())
        except ValueError:
            pass
    raise SiteValidationError(
        "credential_action",
        "必须为 keep、set 或 clear",
    )


def resolve_credential_update(
    action: CredentialAction,
    bearer_token: object,
    current_token: str | None,
) -> str | None:
    if action is CredentialAction.KEEP:
        if bearer_token not in (None, ""):
            raise SiteValidationError(
                "bearer_token",
                "保留原凭据时不能提交新值",
            )
        return current_token
    if action is CredentialAction.CLEAR:
        if bearer_token not in (None, ""):
            raise SiteValidationError(
                "bearer_token",
                "清除凭据时不能同时提交新值",
            )
        return None

    token = normalize_bearer_token(bearer_token)
    if token is None:
        raise SiteValidationError("bearer_token", "设置凭据时不能为空")
    return token


def normalize_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise SiteValidationError("base_url", "必须是字符串")
    raw = value.strip()
    if not raw:
        raise SiteValidationError("base_url", "不能为空")
    if any(unicodedata.category(char) == "Cc" for char in raw):
        raise SiteValidationError("base_url", "不能包含控制字符")
    if len(raw) > MAX_BASE_URL_LENGTH:
        raise SiteValidationError(
            "base_url",
            f"长度不能超过 {MAX_BASE_URL_LENGTH} 个字符",
        )
    if "\\" in raw:
        raise SiteValidationError("base_url", "不能包含反斜杠")
    if "?" in raw or "#" in raw:
        raise SiteValidationError("base_url", "不能包含查询串或 fragment")

    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise SiteValidationError("base_url", "不是合法的 URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise SiteValidationError("base_url", "仅允许 http 或 https 协议")
    if not parsed.netloc:
        raise SiteValidationError("base_url", "必须包含主机名")
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise SiteValidationError("base_url", "不能包含用户名或密码")
    if parsed.netloc.endswith(":"):
        raise SiteValidationError("base_url", "端口不能为空")

    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SiteValidationError("base_url", "主机或端口不合法") from exc
    if not host:
        raise SiteValidationError("base_url", "必须包含主机名")
    host = host.lower()

    if ":" in host:
        if "%" in host:
            raise SiteValidationError("base_url", "不支持带 zone ID 的 IPv6 地址")
        try:
            ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise SiteValidationError("base_url", "IPv6 地址不合法") from exc
        if not parsed.netloc.startswith("["):
            raise SiteValidationError("base_url", "IPv6 地址必须使用方括号")
        host_for_url = f"[{host}]"
    else:
        if _IPV4_LIKE_RE.fullmatch(host):
            try:
                ipaddress.IPv4Address(host)
            except ValueError as exc:
                raise SiteValidationError("base_url", "IPv4 地址不合法") from exc
        else:
            _validate_hostname(host)
        host_for_url = host

    if port is not None and not 1 <= port <= 65535:
        raise SiteValidationError("base_url", "端口必须在 1～65535 之间")

    path = parsed.path
    if any(char.isspace() for char in path):
        raise SiteValidationError("base_url", "路径不能包含空白字符")
    path = path.rstrip("/")
    if path.lower().endswith("/v2"):
        raise SiteValidationError(
            "base_url",
            "不应以 /v2 结尾；请求时会自行拼接 endpoint 路径",
        )

    netloc = host_for_url if port is None else f"{host_for_url}:{port}"
    return urlunsplit((scheme, netloc, path, "", ""))


def _validate_hostname(host: str) -> None:
    if len(host) > 253:
        raise SiteValidationError("base_url", "主机名过长")
    candidate = host[:-1] if host.endswith(".") else host
    if not candidate:
        raise SiteValidationError("base_url", "主机名不能为空")
    for label in candidate.split("."):
        if not label:
            raise SiteValidationError("base_url", "主机名包含空标签")
        if len(label) > 63:
            raise SiteValidationError("base_url", "主机名标签过长")
        if _HOST_LABEL_RE.fullmatch(label) is None:
            raise SiteValidationError("base_url", "主机名不合法")


def _to_public(stored: StoredSite) -> SiteConfig:
    return SiteConfig(
        id=stored.id,
        name=stored.name,
        base_url=stored.base_url,
        enabled=stored.enabled,
        timeout_seconds=stored.timeout_seconds,
        has_bearer_token=stored.bearer_token is not None,
        revision=stored.revision,
        created_at=stored.created_at,
        updated_at=stored.updated_at,
    )


__all__ = [
    "CredentialAction",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_BASE_URL_LENGTH",
    "MAX_BEARER_TOKEN_LENGTH",
    "MAX_SITE_ID_LENGTH",
    "MAX_SITE_NAME_LENGTH",
    "MAX_TIMEOUT_SECONDS",
    "MIN_TIMEOUT_SECONDS",
    "SCHEMA_VERSION",
    "SiteConfig",
    "SiteConflictError",
    "SiteCreate",
    "SiteDisabledError",
    "SiteError",
    "SiteFetchConfig",
    "SiteNotFoundError",
    "SiteService",
    "SiteStorageError",
    "SiteUpdate",
    "SiteValidationError",
    "normalize_base_url",
    "normalize_bearer_token",
    "normalize_credential_action",
    "normalize_enabled",
    "normalize_revision",
    "normalize_site_id",
    "normalize_site_name",
    "normalize_timeout_seconds",
    "resolve_credential_update",
]
