from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from core.chat_content import ChatContentQuery, FAILED_TEXT
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.profile_api import ProfileApi
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate


class ChatContentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "content.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.calls: list[str] = []
        self.mode = "normal"

        async def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            self.calls.append(path)
            if self.mode == "all_fail":
                return httpx.Response(500, content=b"upstream body must not leak")
            if path.endswith("/v2/bili"):
                if self.mode == "partial":
                    return httpx.Response(503, content=b"bili unavailable")
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": [{"title": "热搜标题"}],
                }).encode())
            if self.mode == "empty":
                news = []
            else:
                news = ["新闻一", "新闻二"]
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok",
                "data": {"date": "2026-10-29", "news": news},
            }).encode())

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.preview = ProfileApi(self.profiles, self.content, self.sites)
        self.query = ChatContentQuery(self.tasks, self.preview)

    async def asyncTearDown(self) -> None:
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def make(self, prefix: str, content_types=("daily_news",)):
        site = await self.sites.create_site(SiteCreate(
            name=f"站点-{prefix}", base_url=f"http://example.test/{prefix}",
            enabled=True, timeout_seconds=5,
        ))
        profile = await self.profiles.create_profile(ProfileCreate(
            name=f"组合-{prefix}", site_id=site.id,
            items=[
                {"content_type": item, "params": {}, "limit": None}
                for item in content_types
            ],
        ))
        task = await self.tasks.create_task(TaskCreate(
            name=f"任务-{prefix}", profile_id=profile.id,
            output_format="text", umos=["bot:GroupMessage:g"],
        ))
        return site, profile, task

    async def test_cache_hit_and_empty_content(self) -> None:
        _, _, task = await self.make("cache")
        first = await self.query.query("bot:GroupMessage:g", task.id)
        self.assertIn("新闻一", first)
        self.assertIn("来源站点：站点-cache", first)
        self.assertEqual(len(self.calls), 1)
        second = await self.query.query("bot:GroupMessage:g", task.id)
        self.assertEqual(second, first)
        self.assertEqual(len(self.calls), 1, "second query must hit cache")

        self.mode = "empty"
        _, _, empty_task = await self.make("empty")
        empty = await self.query.query("bot:GroupMessage:g", empty_task.id)
        self.assertIn("空列表", empty)
        self.assertNotIn("新闻一", empty)

    async def test_partial_and_all_failed(self) -> None:
        self.mode = "partial"
        _, _, task = await self.make("partial", ("daily_news", "bilibili_hot"))
        partial = await self.query.query("bot:GroupMessage:g", task.id)
        self.assertIn("部分成功", partial)
        self.assertIn("获取失败", partial)
        self.assertIn("新闻一", partial)

        self.mode = "all_fail"
        _, _, failed_task = await self.make("fail", ("daily_news", "bilibili_hot"))
        failed = await self.query.query("bot:GroupMessage:g", failed_task.id)
        self.assertEqual(failed, FAILED_TEXT)
        self.assertNotIn("新闻一", failed)
        self.assertNotIn("upstream body", failed)
