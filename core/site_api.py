"""站点管理的后台 API 业务层。

本模块不依赖 AstrBot 的请求对象；宿主适配层负责鉴权、请求解析和响应发送。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .sites import (
    DEFAULT_TIMEOUT_SECONDS,
    SiteConfig,
    SiteConflictError,
    SiteCreate,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
    SiteUpdate,
    SiteValidationError,
)


@dataclass(frozen=True, slots=True)
class SiteApiResult:
    """一个可直接序列化的 API 结果。"""

    status_code: int
    body: dict[str, Any]


class SiteApi:
    """站点管理接口的业务实现。"""

    def __init__(self, service: SiteService) -> None:
        self._service = service

    async def list_sites(self) -> SiteApiResult:
        try:
            sites = await self._service.list_sites()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok({"sites": [_site_dict(site) for site in sites]})

    async def get_site(self, site_id: str) -> SiteApiResult:
        try:
            site = await self._service.get_site(site_id)
        except SiteValidationError as exc:
            return _validation_error(exc)
        except SiteNotFoundError:
            return _not_found_error()
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_site_dict(site))

    async def create_site(self, payload: object) -> SiteApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            site = await self._service.create_site(
                SiteCreate(
                    name=data.get("name"),
                    base_url=data.get("base_url"),
                    enabled=data.get("enabled", False),
                    timeout_seconds=data.get(
                        "timeout_seconds",
                        DEFAULT_TIMEOUT_SECONDS,
                    ),
                    bearer_token=data.get("bearer_token"),
                ),
            )
        except SiteValidationError as exc:
            return _validation_error(exc)
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_site_dict(site), status_code=201)

    async def update_site(self, site_id: str, payload: object) -> SiteApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            site = await self._service.update_site(
                site_id,
                SiteUpdate(
                    name=data.get("name"),
                    base_url=data.get("base_url"),
                    enabled=data.get("enabled"),
                    timeout_seconds=data.get("timeout_seconds"),
                    expected_revision=data.get("expected_revision"),
                    credential_action=data.get("credential_action", "keep"),
                    bearer_token=data.get("bearer_token"),
                ),
            )
        except SiteValidationError as exc:
            return _validation_error(exc)
        except SiteNotFoundError:
            return _not_found_error()
        except SiteConflictError as exc:
            return _conflict_error(exc)
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok(_site_dict(site))

    async def delete_site(self, site_id: str, payload: object) -> SiteApiResult:
        data = _payload_dict(payload)
        if data is None:
            return _invalid_body_error()
        try:
            await self._service.delete_site(
                site_id,
                data.get("expected_revision"),
            )
        except SiteValidationError as exc:
            return _validation_error(exc)
        except SiteNotFoundError:
            return _not_found_error()
        except SiteConflictError as exc:
            return _conflict_error(exc)
        except SiteStorageError:
            return _storage_error()
        except Exception:
            return _internal_error()
        return _ok({"deleted_id": site_id})


def _payload_dict(payload: object) -> dict[str, Any] | None:
    return payload if isinstance(payload, dict) else None


def _site_dict(site: SiteConfig) -> dict[str, Any]:
    return {
        "id": site.id,
        "name": site.name,
        "base_url": site.base_url,
        "enabled": site.enabled,
        "timeout_seconds": site.timeout_seconds,
        "has_bearer_token": site.has_bearer_token,
        "revision": site.revision,
        "created_at": site.created_at,
        "updated_at": site.updated_at,
    }


def _ok(data: Any, *, status_code: int = 200) -> SiteApiResult:
    return SiteApiResult(
        status_code=status_code,
        body={"status": "ok", "data": data},
    )


def _error(
    status_code: int,
    message: str,
    *,
    code: str,
    data: dict[str, Any] | None = None,
) -> SiteApiResult:
    details = {"code": code}
    if data:
        details.update(data)
    return SiteApiResult(
        status_code=status_code,
        body={"status": "error", "message": message, "data": details},
    )


def _invalid_body_error() -> SiteApiResult:
    return _error(
        400,
        "请求体必须是 JSON 对象。",
        code="invalid_body",
    )


def _validation_error(exc: SiteValidationError) -> SiteApiResult:
    return _error(
        400,
        str(exc),
        code="validation_error",
        data={"field": exc.field},
    )


def _not_found_error() -> SiteApiResult:
    return _error(404, "站点不存在或已被删除。", code="not_found")


def _conflict_error(exc: SiteConflictError) -> SiteApiResult:
    return _error(
        409,
        "站点配置已被其他操作修改，请重新读取后再保存。",
        code="revision_conflict",
        data={
            "expected_revision": exc.expected_revision,
            "actual_revision": exc.actual_revision,
        },
    )


def _storage_error() -> SiteApiResult:
    return _error(
        503,
        "站点存储暂不可用，请稍后重试。",
        code="storage_unavailable",
    )


def _internal_error() -> SiteApiResult:
    return _error(
        500,
        "服务器内部错误，请稍后重试。",
        code="internal_error",
    )


__all__ = ["SiteApi", "SiteApiResult"]
