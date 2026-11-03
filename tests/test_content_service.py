from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.sites import SiteCreate, SiteService


class ContentServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.clock = lambda: datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _site(self, name: str, token: str) -> object:
        return await self.sites.create_site(
            SiteCreate(
                name=name,
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=5,
                bearer_token=token,
            ),
        )

    async def test_five_content_types_share_one_client_and_budget(self) -> None:
        await self._site("共享预算站点", "shared-token")
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        seen: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            seen.append(request.url.path)
            if len(seen) == 1:
                started.set()
                await release.wait()
            active -= 1
            if request.url.path.endswith("/v2/bili"):
                body = {"code": 200, "message": "ok", "data": [{"title": "热搜"}]}
            elif request.url.path.endswith("/v2/zhihu"):
                body = {
                    "code": 200,
                    "message": "ok",
                    "data": [{"title": "知乎", "hot_value_desc": "10 万热度"}],
                }
            elif request.url.path.endswith("/v2/douyin"):
                body = {
                    "code": 200,
                    "message": "ok",
                    "data": [{"title": "抖音", "hot_value": 123}],
                }
            elif request.url.path.endswith("/v2/toutiao"):
                body = {
                    "code": 200,
                    "message": "ok",
                    "data": [{"title": "头条", "hot_value": 456}],
                }
            else:
                body = {
                    "code": 200,
                    "message": "ok",
                    "data": {"date": "2026-10-24", "news": ["新闻"]},
                }
            return httpx.Response(200, content=json.dumps(body).encode())

        client = ContentHttpClient(
            transport=httpx.MockTransport(handler),
            max_concurrent_requests=1,
        )
        content = ContentService(self.sites, client=client, clock=self.clock)
        await content.open()
        try:
            daily_task = asyncio.create_task(content.preview_daily(
                (await self.sites.list_sites())[0].id,
            ))
            bili_task = asyncio.create_task(content.preview_bili(
                (await self.sites.list_sites())[0].id,
            ))
            zhihu_task = asyncio.create_task(content.preview_zhihu(
                (await self.sites.list_sites())[0].id,
            ))
            douyin_task = asyncio.create_task(content.preview_douyin(
                (await self.sites.list_sites())[0].id,
            ))
            toutiao_task = asyncio.create_task(content.preview_toutiao(
                (await self.sites.list_sites())[0].id,
            ))
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.sleep(0)
            release.set()
            daily, bili, zhihu, douyin, toutiao = await asyncio.gather(
                daily_task,
                bili_task,
                zhihu_task,
                douyin_task,
                toutiao_task,
            )
        finally:
            await content.close()
        self.assertEqual(peak, 1)
        self.assertEqual(
            set(seen),
            {
                "/proxy/v2/60s",
                "/proxy/v2/bili",
                "/proxy/v2/zhihu",
                "/proxy/v2/douyin",
                "/proxy/v2/toutiao",
            },
        )
        self.assertEqual(daily.items[0].title, "新闻")
        self.assertEqual(bili.items[0].title, "热搜")
        self.assertEqual(zhihu.items[0].title, "知乎")
        self.assertEqual(zhihu.items[0].hot_value_desc, "10 万热度")
        self.assertEqual(douyin.items[0].title, "抖音")
        self.assertEqual(douyin.items[0].hot_value, 123)
        self.assertEqual(toutiao.items[0].title, "头条")
        self.assertEqual(toutiao.items[0].hot_value, 456)
        self.assertIs(content.news._client, content.bili._client)
        self.assertIs(content.news._client, content.zhihu._client)
        self.assertIs(content.news._client, content.douyin._client)
        self.assertIs(content.news._client, content.toutiao._client)

    async def test_five_content_types_use_their_own_site_credentials(self) -> None:
        daily_site = await self._site("每日新闻站点", "daily-token")
        bili_site = await self._site("B 站站点", "bili-token")
        zhihu_site = await self._site("知乎站点", "zhihu-token")
        douyin_site = await self._site("抖音站点", "douyin-token")
        toutiao_site = await self._site("头条站点", "toutiao-token")
        seen: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.path] = request.headers.get("authorization", "")
            if request.url.path.endswith("/v2/bili"):
                body = {"code": 200, "data": [{"title": "热搜"}]}
            elif request.url.path.endswith("/v2/zhihu"):
                body = {"code": 200, "data": [{"title": "知乎"}]}
            elif request.url.path.endswith("/v2/douyin"):
                body = {"code": 200, "data": [{"title": "抖音"}]}
            elif request.url.path.endswith("/v2/toutiao"):
                body = {"code": 200, "data": [{"title": "头条"}]}
            else:
                body = {"code": 200, "data": {"news": ["新闻"]}}
            return httpx.Response(200, content=json.dumps(body).encode())

        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        content = ContentService(self.sites, client=client, clock=self.clock)
        await content.open()
        try:
            await content.preview_daily(daily_site.id)
            await content.preview_bili(bili_site.id)
            await content.preview_zhihu(zhihu_site.id)
            await content.preview_douyin(douyin_site.id)
            await content.preview_toutiao(toutiao_site.id)
        finally:
            await content.close()
        self.assertEqual(seen["/proxy/v2/60s"], "Bearer daily-token")
        self.assertEqual(seen["/proxy/v2/bili"], "Bearer bili-token")
        self.assertEqual(seen["/proxy/v2/zhihu"], "Bearer zhihu-token")
        self.assertEqual(seen["/proxy/v2/douyin"], "Bearer douyin-token")
        self.assertEqual(seen["/proxy/v2/toutiao"], "Bearer toutiao-token")

    async def test_site_connection_probe_five_types_without_writes(self) -> None:
        site = await self.sites.create_site(SiteCreate(
            name="测试连接站点", base_url="http://example.test/proxy",
            enabled=False, timeout_seconds=5, bearer_token="secret-token",
        ))
        seen = []
        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            path = request.url.path
            if path.endswith("/v2/60s"):
                body = {"code": 200, "message": "ok", "data": {"news": []}}
            elif path.endswith("/v2/bili"):
                body = {"code": 200, "message": "ok", "data": []}
            elif path.endswith("/v2/zhihu"):
                body = {"code": 200, "message": "ok", "data": []}
            elif path.endswith("/v2/douyin"):
                body = {"code": 200, "message": "ok", "data": []}
            else:
                body = {"code": 200, "message": "ok", "data": []}
            return httpx.Response(200, json=body)
        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        service = ContentService(self.sites, client=client, clock=self.clock)
        await service.open()
        try:
            for content_type in (
                "daily_news", "bilibili_hot", "zhihu_hot",
                "douyin_hot", "toutiao_hot",
            ):
                result = await service.test_connection(
                    site.id, content_type, site.revision,
                )
                self.assertEqual(result["result"], "empty")
                self.assertEqual(result["revision"], site.revision)
            self.assertEqual(len(seen), 5)
            current = await self.sites.get_site(site.id)
            self.assertFalse(current.enabled)
            self.assertEqual(current.revision, site.revision)
        finally:
            await service.close()

    async def test_site_connection_errors_and_concurrency_admission(self) -> None:
        from core.news import NewsInvalidJSONError
        from core.sites import (
            SiteConflictError, SiteTestBusyError, SiteValidationError,
        )
        site = await self.sites.create_site(SiteCreate(
            name="边界站点", base_url="http://example.test/proxy",
            enabled=False, timeout_seconds=5,
        ))
        calls = []
        release = asyncio.Event()
        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path.endswith("/v2/60s"):
                await release.wait()
                return httpx.Response(200, text="not-json")
            return httpx.Response(200, json={"code": 200, "message": "ok", "data": []})
        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        service = ContentService(self.sites, client=client, clock=self.clock)
        await service.open()
        try:
            with self.assertRaises(SiteValidationError):
                await service.test_connection(site.id, "unknown", site.revision)
            with self.assertRaises(SiteConflictError):
                await service.test_connection(site.id, "daily_news", site.revision + 1)
            self.assertEqual(calls, [])
            blocking = [
                asyncio.create_task(service.test_connection(site.id, "daily_news", site.revision))
                for _ in range(2)
            ]
            await asyncio.sleep(0)
            with self.assertRaises(SiteTestBusyError):
                await service.test_connection(site.id, "daily_news", site.revision)
            release.set()
            results = await asyncio.gather(*blocking, return_exceptions=True)
            self.assertTrue(all(isinstance(item, NewsInvalidJSONError) for item in results))
            with self.assertRaises(NewsInvalidJSONError):
                await service.test_connection(site.id, "daily_news", site.revision)
        finally:
            await service.close()

    async def test_site_connection_init_cancel_cleans_cache_and_reopens(self) -> None:
        import asyncio
        from core.sites import SiteTestUnavailableError
        class SlowClient:
            def __init__(self): self.is_open = False; self.started = asyncio.Event()
            async def open(self):
                self.started.set()
                await asyncio.Event().wait()
            async def close(self): self.is_open = False
            async def fetch_json(self, *args, **kwargs):
                raise AssertionError("must not fetch")
        class FakeCache:
            def __init__(self): self.is_open = False
            async def open(self): self.is_open = True
            async def close(self): self.is_open = False
        service = ContentService(self.sites, client=SlowClient())
        service._cache = FakeCache()
        task = asyncio.create_task(service.open())
        await service._client.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(service._test_admitted)
        self.assertFalse(service._cache.is_open)
        with self.assertRaises(SiteTestUnavailableError):
            await service.test_connection("missing", "daily_news", 1)

    async def test_site_connection_revision_change_during_http_invalidates(self) -> None:
        from core.sites import SiteConflictError, SiteUpdate
        site = await self.sites.create_site(SiteCreate(
            name="并发修改", base_url="http://example.test/proxy",
            enabled=False, timeout_seconds=5,
        ))
        started = asyncio.Event(); release = asyncio.Event()
        async def handler(request: httpx.Request) -> httpx.Response:
            started.set(); await release.wait()
            return httpx.Response(200, json={"code": 200, "message": "ok", "data": []})
        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        service = ContentService(self.sites, client=client, clock=self.clock)
        await service.open()
        try:
            task = asyncio.create_task(
                service.test_connection(site.id, "bilibili_hot", site.revision),
            )
            await started.wait()
            await self.sites.update_site(site.id, SiteUpdate(
                name="并发修改后", base_url="http://example.test/proxy",
                enabled=False, timeout_seconds=5, expected_revision=site.revision,
            ))
            release.set()
            with self.assertRaises(SiteConflictError):
                await task
        finally:
            await service.close()
