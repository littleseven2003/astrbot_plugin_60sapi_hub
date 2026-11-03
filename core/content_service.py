"""多个内容 endpoint 共享站点配置和 HTTP 客户端的编排。"""

from __future__ import annotations

import asyncio
import json
import time as monotonic_time
from collections.abc import Callable
from datetime import datetime, timezone

from .bili import BiliHotSnapshot
from .content_cache import CacheKey, CacheResult, ContentCache
from .bili import build_bili_hot_url, parse_bili_hot
from .bili_service import BiliHotService
from .content_client import ContentHttpClient
from .douyin import DouyinHotSnapshot
from .douyin import build_douyin_hot_url, parse_douyin_hot
from .douyin_service import DouyinHotService
from .news import CHINA_TIMEZONE, NewsSnapshot
from .news import build_daily_news_url, parse_daily_news
from .news_service import NewsService
from .sites import SiteService
from .toutiao import ToutiaoHotSnapshot
from .toutiao import build_toutiao_hot_url, parse_toutiao_hot
from .toutiao_service import ToutiaoHotService
from .zhihu import ZhihuHotSnapshot
from .zhihu import build_zhihu_hot_url, parse_zhihu_hot
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
        self._test_inflight = 0
        self._test_tasks: set[asyncio.Task[object]] = set()
        self._test_generation = 0
        self._test_admitted = False
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
        self._test_admitted = False
        self._test_generation += 1
        await self._cache.open()
        try:
            await self._client.open()
        except Exception:
            await self._cache.close()
            self._test_generation += 1
            raise
        self._test_admitted = True

    async def close(self) -> None:
        self._test_admitted = False
        self._test_generation += 1
        tasks = list(self._test_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self._cache.close()
        finally:
            await self._client.close()

    async def test_connection(
        self, site_id: object, content_type: object, expected_revision: object,
    ) -> dict[str, object]:
        """按已保存站点快照测试一个固定 endpoint；不使用缓存。"""
        from .sites import SiteNotFoundError, SiteValidationError
        adapters = {
            "daily_news": (build_daily_news_url, parse_daily_news),
            "bilibili_hot": (build_bili_hot_url, parse_bili_hot),
            "zhihu_hot": (build_zhihu_hot_url, parse_zhihu_hot),
            "douyin_hot": (build_douyin_hot_url, parse_douyin_hot),
            "toutiao_hot": (build_toutiao_hot_url, parse_toutiao_hot),
        }
        if content_type not in adapters:
            raise SiteValidationError("content_type", "不支持的内容类型")
        if not self._test_admitted:
            from .sites import SiteTestUnavailableError
            raise SiteTestUnavailableError()
        if self._test_inflight >= 2:
            from .sites import SiteTestBusyError
            raise SiteTestBusyError()
        self._test_inflight += 1
        task = asyncio.current_task()
        generation = self._test_generation
        if task is not None:
            self._test_tasks.add(task)
        try:
            if not isinstance(expected_revision, int) or type(expected_revision) is bool:
                raise SiteValidationError("expected_revision", "必须是整数")
            site = await self._sites.get_test_config(site_id, expected_revision)
            if not self._test_admitted or generation != self._test_generation:
                from .sites import SiteTestUnavailableError
                raise SiteTestUnavailableError()
            build_url, parser = adapters[content_type]
            started = monotonic_time.monotonic()
            body = await self._client.fetch_json(
                build_url(site.base_url), bearer_token=site.bearer_token,
                timeout_seconds=site.timeout_seconds,
            )
            if generation != self._test_generation:
                from .sites import SiteTestUnavailableError
                raise SiteTestUnavailableError()
            try:
                payload = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                from .news import NewsInvalidJSONError
                raise NewsInvalidJSONError("上游返回的内容不是有效 JSON。") from exc
            snapshot = parser(
                payload, site_id=site.id, site_name=site.name,
                fetched_at=self._clock(),
            )
            elapsed_ms = int((monotonic_time.monotonic() - started) * 1000)
            empty = not getattr(snapshot, "items", ())
            result = {
                "site_id": site.id, "revision": site.revision,
                "content_type": content_type,
                "endpoint": snapshot.endpoint,
                "tested_at": self._clock().astimezone(timezone.utc).isoformat(),
                "elapsed_ms": elapsed_ms,
                "result": "empty" if empty else "success",
                "message": "连接成功，接口返回合法空内容。" if empty else "连接成功，接口返回内容结构合法。",
            }
            if generation != self._test_generation or not self._test_admitted:
                from .sites import SiteTestUnavailableError
                raise SiteTestUnavailableError()
            await self._sites.get_test_config(site.id, site.revision)
            if generation != self._test_generation or not self._test_admitted:
                from .sites import SiteTestUnavailableError
                raise SiteTestUnavailableError()
            return result
        finally:
            self._test_inflight -= 1
            if task is not None:
                self._test_tasks.discard(task)


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
