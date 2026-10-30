from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from core.chat_image import ChatImageQuery, IMAGE_FAILED_TEXT
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.profile_api import ProfileApi
from core.profile_image import FontUnavailableError, find_cjk_font
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


class ChatImageIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.png_dir = Path(tempfile.mkdtemp(prefix="60sapi-chat-image-pages-"))
        self.sites = SiteService(Path(self.tmp.name) / "image.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.calls = []
        self.fail = False
        self.partial = False

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if self.partial and request.url.path.endswith("/v2/bili"):
                return httpx.Response(503, content=b"partial upstream")
            if self.fail:
                return httpx.Response(500, content=b"secret upstream")
            body = {
                "code": 200, "message": "ok",
                "data": {
                    "date": "2026-10-30",
                    "news": [f"第{i}条中文新闻内容" for i in range(100)],
                },
            }
            return httpx.Response(200, content=json.dumps(body).encode())

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.snapshots = ImageSnapshotStore()
        self.image_service = ProfileImageService()
        self.api = ProfileApi(
            self.profiles, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.image_service,
        )
        self.site = await self.sites.create_site(SiteCreate(
            name="图片查询站点", base_url="http://example.test/img",
            enabled=True, timeout_seconds=5,
        ))
        self.profile = await self.profiles.create_profile(ProfileCreate(
            name="图片查询组合", site_id=self.site.id,
            items=[{"content_type": "daily_news", "params": {}, "limit": 100}],
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="图片查询任务", profile_id=self.profile.id,
            output_format="markdown", umos=["bot:GroupMessage:g"],
        ))
        self.query = ChatImageQuery(self.tasks, self.api)

    async def asyncTearDown(self) -> None:
        await self.image_service.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_real_two_page_png_cache_and_order(self) -> None:
        reply = await self.query.query("bot:GroupMessage:g", self.task.id)
        self.assertIsInstance(reply, object)
        self.assertGreaterEqual(reply.page_count, 2)
        self.assertIn(f"共 {reply.page_count} 页", reply.text)
        sizes = []
        for index, png in enumerate(reply.pages, start=1):
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(png)) as image:
                sizes.append(image.size)
                image.load()
                path = self.png_dir / f"page-{index}.png"
                image.save(path)
        self.assertTrue(all(size == (900, 1200) for size in sizes))
        print("PNG_OUTPUT", self.png_dir, [p.name for p in sorted(self.png_dir.glob("*.png"))])
        first_calls = len(self.calls)
        self.assertGreaterEqual(first_calls, 1)

        second = await self.query.query("bot:GroupMessage:g", self.task.id)
        self.assertEqual(second.page_count, reply.page_count)
        self.assertEqual(len(self.calls), first_calls, "cache must avoid upstream")

    @unittest.skipUnless(has_font(), "no CJK font available")
    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_partial_failure_still_renders_images(self) -> None:
        self.partial = True
        profile = await self.profiles.create_profile(ProfileCreate(
            name="部分失败组合", site_id=self.site.id,
            items=[
                {"content_type": "daily_news", "params": {}, "limit": 20},
                {"content_type": "bilibili_hot", "params": {}, "limit": 20},
            ],
        ))
        task = await self.tasks.create_task(TaskCreate(
            name="部分失败任务", profile_id=profile.id,
            output_format="text", umos=["bot:GroupMessage:g"],
        ))
        reply = await self.query.query("bot:GroupMessage:g", task.id)
        self.assertGreaterEqual(reply.page_count, 1)
        self.assertTrue(any(path.endswith("/v2/bili") for path in self.calls))

    async def test_all_failed_returns_safe_text(self) -> None:
        self.fail = True
        result = await self.query.query("bot:GroupMessage:g", self.task.id)
        self.assertEqual(result, IMAGE_FAILED_TEXT)
        self.assertNotIn("secret upstream", result)

    async def test_font_missing_returns_safe_text(self) -> None:
        from core.image_snapshots import ImageSnapshotStore
        broken_service = ProfileImageService(font_path="/definitely/missing/font.ttf")
        broken_api = ProfileApi(
            self.profiles, self.content, self.sites,
            image_snapshots=ImageSnapshotStore(), image_service=broken_service,
        )
        try:
            result = await ChatImageQuery(self.tasks, broken_api).query(
                "bot:GroupMessage:g", self.task.id,
            )
            self.assertEqual(result, IMAGE_FAILED_TEXT)
        finally:
            await broken_service.close()
