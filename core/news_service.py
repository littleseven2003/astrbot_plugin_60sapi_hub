"""每日新闻获取用例：读取站点配置、请求上游并解析为内容快照。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

from .news import (
    NewsInvalidJSONError,
    NewsSnapshot,
    build_daily_news_url,
    parse_daily_news,
)
from .news_client import NewsHttpClient
from .sites import SiteService


class NewsService:
    """一次每日新闻预览的业务编排；不持久化站点配置或内容缓存。"""

    def __init__(
        self,
        sites: SiteService,
        *,
        client: NewsHttpClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sites = sites
        self._client = client or NewsHttpClient()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def is_open(self) -> bool:
        return self._client.is_open

    async def open(self) -> None:
        await self._client.open()

    async def close(self) -> None:
        await self._client.close()

    async def preview(self, site_id: object) -> NewsSnapshot:
        """按站点 ID 获取一次每日新闻；只接受已启用站点。"""
        site = await self._sites.get_fetch_config(site_id)
        url = build_daily_news_url(site.base_url)
        body = await self._client.fetch_json(
            url,
            bearer_token=site.bearer_token,
            timeout_seconds=site.timeout_seconds,
        )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NewsInvalidJSONError("上游返回的内容不是有效 JSON。") from exc
        return parse_daily_news(
            payload,
            site_id=site.id,
            site_name=site.name,
            fetched_at=self._clock(),
        )


__all__ = ["NewsService"]
