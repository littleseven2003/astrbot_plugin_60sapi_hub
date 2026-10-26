"""多个内容 endpoint 共享站点配置和 HTTP 客户端的编排。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .bili import BiliHotSnapshot
from .content_cache import CacheKey, CacheResult, ContentCache
from .bili_service import BiliHotService
from .content_client import ContentHttpClient
from .douyin import DouyinHotSnapshot
from .douyin_service import DouyinHotService
from .news import CHINA_TIMEZONE, NewsSnapshot
from .news_service import NewsService
from .sites import SiteService
from .toutiao import ToutiaoHotSnapshot
from .toutiao_service import ToutiaoHotService
from .zhihu import ZhihuHotSnapshot
from .zhihu_service import ZhihuHotService


class ContentService:
    """持有一个共享 HTTP 客户端，并按内容类型调用对应适配器。"""

    def __init__(
        self,
        sites: SiteService,
        *,
        client: ContentHttpClient | None = None,
        cache: ContentCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sites = sites
        self._client = client or ContentHttpClient()
        clock = clock or (lambda: datetime.now(timezone.utc))
        self._clock = clock
        self._cache = cache or ContentCache(wall_clock=clock)
        self.news = NewsService(
            sites,
            client=self._client,
            clock=clock,
            manage_client=False,
        )
        self.bili = BiliHotService(
            sites,
            client=self._client,
            clock=clock,
            manage_client=False,
        )
        self.zhihu = ZhihuHotService(
            sites,
            client=self._client,
            clock=clock,
            manage_client=False,
        )
        self.douyin = DouyinHotService(
            sites,
            client=self._client,
            clock=clock,
            manage_client=False,
        )
        self.toutiao = ToutiaoHotService(
            sites,
            client=self._client,
            clock=clock,
            manage_client=False,
        )

    @property
    def client(self) -> ContentHttpClient:
        return self._client

    @property
    def is_open(self) -> bool:
        return self._client.is_open

    @property
    def cache_ttl_seconds(self) -> float:
        return self._cache.ttl_seconds

    async def open(self) -> None:
        await self._cache.open()
        try:
            await self._client.open()
        except Exception:
            await self._cache.close()
            raise

    async def close(self) -> None:
        try:
            await self._cache.close()
        finally:
            await self._client.close()

    async def preview_daily(self, site_id: object) -> NewsSnapshot:
        return await self.news.preview(site_id)

    async def preview_bili(self, site_id: object) -> BiliHotSnapshot:
        return await self.bili.preview(site_id)

    async def preview_zhihu(self, site_id: object) -> ZhihuHotSnapshot:
        return await self.zhihu.preview(site_id)

    async def preview_douyin(self, site_id: object) -> DouyinHotSnapshot:
        return await self.douyin.preview(site_id)

    async def preview_toutiao(self, site_id: object) -> ToutiaoHotSnapshot:
        return await self.toutiao.preview(site_id)

    async def preview_cached(
        self,
        content_type: str,
        site_id: object,
        *,
        force_refresh: bool = False,
    ) -> CacheResult:
        """统一预览的缓存入口；站点状态在读取缓存前重新校验。"""
        site = await self._sites.get_fetch_config(site_id)
        fetchers = {
            "daily_news": lambda: self.news.preview(site.id),
            "bilibili_hot": lambda: self.bili.preview(site.id),
            "zhihu_hot": lambda: self.zhihu.preview(site.id),
            "douyin_hot": lambda: self.douyin.preview(site.id),
            "toutiao_hot": lambda: self.toutiao.preview(site.id),
        }
        fetch = fetchers.get(content_type)
        if fetch is None:
            raise ValueError("unknown content type")
        key = CacheKey(
            site_id=site.id,
            content_type=content_type,
            params=(),
            revision=site.revision,
        )
        day_token = None
        if content_type == "daily_news":
            day_token = lambda: self._clock().astimezone(
                CHINA_TIMEZONE,
            ).date()
        return await self._cache.get(
            key,
            fetch,
            force_refresh=force_refresh,
            day_token=day_token,
        )


__all__ = ["ContentService"]
