"""Bilibili 热搜获取用例：读取站点配置、请求上游并解析为内容快照。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

from .bili import (
    BiliHotSnapshot,
    BiliInvalidJSONError,
    build_bili_hot_url,
    parse_bili_hot,
)
from .content_client import ContentHttpClient
from .sites import SiteService


class BiliHotService:
    """一次 Bilibili 热搜预览的业务编排；不持久化站点配置或内容缓存。"""

    def __init__(
        self,
        sites: SiteService,
        *,
        client: ContentHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
        manage_client: bool = True,
    ) -> None:
        self._sites = sites
        self._client = client or ContentHttpClient()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._manage_client = manage_client

    @property
    def is_open(self) -> bool:
        return self._client.is_open

    async def open(self) -> None:
        if self._manage_client:
            await self._client.open()

    async def close(self) -> None:
        if self._manage_client:
            await self._client.close()

    async def preview(self, site_id: object) -> BiliHotSnapshot:
        """按站点 ID 获取一次 Bilibili 热搜；只接受已启用站点。"""
        site = await self._sites.get_fetch_config(site_id)
        url = build_bili_hot_url(site.base_url)
        body = await self._client.fetch_json(
            url,
            bearer_token=site.bearer_token,
            timeout_seconds=site.timeout_seconds,
        )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BiliInvalidJSONError(
                "上游返回的内容不是有效 JSON。",
            ) from exc
        return parse_bili_hot(
            payload,
            site_id=site.id,
            site_name=site.name,
            fetched_at=self._clock(),
        )


__all__ = ["BiliHotService"]
