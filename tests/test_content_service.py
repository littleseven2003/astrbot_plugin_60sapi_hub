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

    async def test_three_content_types_share_one_client_and_budget(self) -> None:
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
            await asyncio.wait_for(started.wait(), timeout=1)
            await asyncio.sleep(0)
            release.set()
            daily, bili, zhihu = await asyncio.gather(
                daily_task,
                bili_task,
                zhihu_task,
            )
        finally:
            await content.close()
        self.assertEqual(peak, 1)
        self.assertEqual(
            set(seen),
            {"/proxy/v2/60s", "/proxy/v2/bili", "/proxy/v2/zhihu"},
        )
        self.assertEqual(daily.items[0].title, "新闻")
        self.assertEqual(bili.items[0].title, "热搜")
        self.assertEqual(zhihu.items[0].title, "知乎")
        self.assertEqual(zhihu.items[0].hot_value_desc, "10 万热度")
        self.assertIs(content.news._client, content.bili._client)
        self.assertIs(content.news._client, content.zhihu._client)

    async def test_content_types_use_their_own_site_credentials(self) -> None:
        daily_site = await self._site("每日新闻站点", "daily-token")
        bili_site = await self._site("B 站站点", "bili-token")
        zhihu_site = await self._site("知乎站点", "zhihu-token")
        seen: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.path] = request.headers.get("authorization", "")
            if request.url.path.endswith("/v2/bili"):
                body = {"code": 200, "data": [{"title": "热搜"}]}
            elif request.url.path.endswith("/v2/zhihu"):
                body = {"code": 200, "data": [{"title": "知乎"}]}
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
        finally:
            await content.close()
        self.assertEqual(seen["/proxy/v2/60s"], "Bearer daily-token")
        self.assertEqual(seen["/proxy/v2/bili"], "Bearer bili-token")
        self.assertEqual(seen["/proxy/v2/zhihu"], "Bearer zhihu-token")
