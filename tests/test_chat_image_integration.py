from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from core.chat_image import ChatImageQuery, ChatImageReply, IMAGE_FAILED_TEXT
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshot, ImageSnapshotStore
from core.profile_api import ProfileApi, ProfileApiResult
from core.profile_image import FontUnavailableError, find_cjk_font
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate


class RecordingStore(ImageSnapshotStore):
    def __init__(self):
        super().__init__()
        self.records: list[ImageSnapshot] = []

    def put(self, **kwargs):
        snapshot = super().put(**kwargs)
        if snapshot is not None:
            self.records.append(snapshot)
        return snapshot


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
        self.fail_upstream = False
        self.partial = False

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if self.partial and request.url.path.endswith("/v2/bili"):
                return httpx.Response(503, content=b"partial upstream")
            if self.fail_upstream:
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
        self.snapshots = RecordingStore()
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
    async def test_real_section_long_image_cache_and_order(self) -> None:
        reply = await self.query.query("bot:GroupMessage:g", self.task.id)
        self.assertIsInstance(reply, object)
        # 一个栏目 = 一张长图：该组合只有一个栏目，因此只有一张图。
        self.assertEqual(reply.image_count, 1)
        self.assertIn(f"共 {reply.image_count} 张栏目长图", reply.text)
        self.assertNotIn("页", reply.text)
        sizes = []
        for index, png in enumerate(reply.images, start=1):
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(png)) as image:
                sizes.append(image.size)
                image.load()
                path = self.png_dir / f"page-{index}.png"
                image.save(path)
        # 每个栏目一张长图：宽度固定 900，高度随内容增长（不再固定分页）。
        self.assertTrue(all(size[0] == 900 for size in sizes))
        self.assertTrue(all(size[1] > 1200 for size in sizes))
        self.assertEqual(len(set(reply.images)), reply.image_count)
        first_calls = len(self.calls)
        self.assertGreaterEqual(first_calls, 1)

        second = await self.query.query("bot:GroupMessage:g", self.task.id)
        self.assertEqual(second.image_count, reply.image_count)
        self.assertEqual(len(self.calls), first_calls, "cache must avoid upstream")
        self.assertEqual(len(self.snapshots.records), 2)
        self.assertEqual(len({record.owner for record in self.snapshots.records}), 2)
        self.assertTrue(all(record.owner.startswith("chat:") for record in self.snapshots.records))
        snapshot_id = self.snapshots.records[-1].id
        denied = await self.api.generate_image(
            self.profile.id, {"snapshot_id": snapshot_id}, owner="dashboard-owner",
        )
        self.assertEqual(denied.status_code, 403)

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
        self.assertGreaterEqual(reply.image_count, 1)
        self.assertTrue(any(path.endswith("/v2/bili") for path in self.calls))

    async def test_all_failed_returns_safe_text(self) -> None:
        self.fail_upstream = True
        result = await self.query.query("bot:GroupMessage:g", self.task.id)
        self.assertEqual(result, IMAGE_FAILED_TEXT)
        self.assertNotIn("secret upstream", result)

    async def test_font_missing_returns_safe_text(self) -> None:
        from core.image_snapshots import ImageSnapshot, ImageSnapshotStore
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

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def section_png(tag: bytes) -> bytes:
    return PNG_SIGNATURE + tag


class StubImageApi:
    """只返回冻结契约的 images 结构，不触达渲染器或上游。"""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.preview_calls = 0
        self.image_calls = 0

    async def preview_profile(self, payload, *, owner=None):
        self.preview_calls += 1
        return ProfileApiResult(200, {"status": "ok", "data": {
            "status": "complete",
            "outputs": {"text": "正文", "markdown": "# 正文"},
            "failed_sections": [],
            "image_snapshot": {"id": "snap-contract"},
        }})

    async def generate_image(self, profile_id, payload, *, owner=None):
        self.image_calls += 1
        return ProfileApiResult(200, {"status": "ok", "data": self.data})


def image_entry(index, label, *, png=None, status="success", error=None):
    entry = {
        "index": index, "content_type": "daily_news", "label": label,
        "status": status, "width": 900, "height": 1500,
    }
    if png is not None:
        entry["png_base64"] = base64.b64encode(png).decode("ascii")
    if error is not None:
        entry["error"] = error
    return entry


class ChatImageSectionContractTest(unittest.IsolatedAsyncioTestCase):
    """图片生成接口改为按栏目返回 images 后的消费方契约。"""

    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "contract.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.site = await self.sites.create_site(SiteCreate(
            name="契约站点", base_url="http://example.test/contract",
            enabled=True, timeout_seconds=5,
        ))
        self.profile = await self.profiles.create_profile(ProfileCreate(
            name="契约组合", site_id=self.site.id,
            items=[{"content_type": "daily_news", "params": {}, "limit": None}],
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="契约任务", profile_id=self.profile.id,
            output_format="image", umos=["bot:GroupMessage:g"],
        ))

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self.tmp.cleanup()

    async def _query(self, data):
        api = StubImageApi(data)
        reply = await ChatImageQuery(self.tasks, api).query(
            "bot:GroupMessage:g", self.task.id,
        )
        return reply, api

    async def test_two_sections_become_two_images_in_order(self) -> None:
        first = section_png(b"one")
        second = section_png(b"two")
        reply, api = await self._query({"images": [
            image_entry(1, "每日新闻", png=first),
            image_entry(2, "知乎热榜", png=second),
        ], "failed_sections": [], "snapshot_id": "snap-contract"})
        self.assertIsInstance(reply, ChatImageReply)
        self.assertEqual(reply.images, (first, second))
        self.assertEqual(reply.image_count, 2)
        self.assertIn("共 2 张栏目长图", reply.text)
        self.assertNotIn("页", reply.text)
        self.assertEqual((api.preview_calls, api.image_calls), (1, 1))

    async def test_failed_section_reported_without_fake_image(self) -> None:
        png = section_png(b"only")
        reply, _ = await self._query({"images": [
            image_entry(1, "每日新闻", png=png),
            image_entry(2, "知乎热榜", status="failed",
                        error="section_too_tall"),
        ], "failed_sections": ["知乎热榜"], "snapshot_id": "snap-contract"})
        self.assertEqual(reply.images, (png,))
        self.assertEqual(reply.image_count, 1)
        self.assertIn("以下栏目未生成图片：知乎热榜。", reply.text)
        self.assertIn("共 1 张栏目长图", reply.text)

    async def test_empty_section_keeps_its_image(self) -> None:
        empty = section_png(b"empty")
        reply, _ = await self._query({"images": [
            image_entry(1, "Bilibili 热搜", png=empty, status="empty"),
        ], "failed_sections": [], "snapshot_id": "snap-contract"})
        self.assertEqual(reply.images, (empty,))
        self.assertNotIn("未生成图片", reply.text)

    async def test_all_failed_sections_return_safe_text(self) -> None:
        result, _ = await self._query({"images": [
            image_entry(1, "每日新闻", status="failed", error="too_tall"),
            image_entry(2, "知乎热榜", status="failed", error="too_tall"),
        ], "failed_sections": ["每日新闻", "知乎热榜"],
            "snapshot_id": "snap-contract"})
        self.assertEqual(result, IMAGE_FAILED_TEXT)

    async def test_malformed_images_are_rejected(self) -> None:
        cases = [
            {"images": [], "failed_sections": []},
            {"images": [image_entry(2, "每日新闻", png=section_png(b"x"))]},
            {"images": [
                image_entry(1, "每日新闻", png=section_png(b"x")),
                image_entry(1, "知乎热榜", png=section_png(b"y")),
            ], "failed_sections": []},
            {"images": [{
                "index": 1, "content_type": "daily_news",
                "label": "每日新闻", "status": "success",
                "width": 900, "height": 1500,
                "png_base64": "bm90LWEtcG5n",
            }], "failed_sections": []},
        ]
        for data in cases:
            result, api = await self._query(data)
            self.assertEqual(result, IMAGE_FAILED_TEXT, data)
            self.assertEqual(api.image_calls, 1)
