"""多个内容 endpoint 共享站点配置和 HTTP 客户端的编排。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from .bili import BiliHotSnapshot
from .bili_service import BiliHotService
from .content_client import ContentHttpClient
from .douyin import DouyinHotSnapshot
from .douyin_service import DouyinHotService
from .news import NewsSnapshot
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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client or ContentHttpClient()
        clock = clock or (lambda: datetime.now(timezone.utc))
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

    async def open(self) -> None:
        await self._client.open()

    async def close(self) -> None:
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


__all__ = ["ContentService"]
