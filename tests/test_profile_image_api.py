from __future__ import annotations

import asyncio
import base64
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from PIL import Image

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.profile_api import ProfileApi
from core.profile_image import FontUnavailableError, ImageLimitError, find_cjk_font
from core.profiles import ContentProfileService
from core.sites import SiteCreate, SiteService, SiteUpdate


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall += timedelta(seconds=seconds)


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


def payload(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200,
            "data": {"date": "2026-10-26", "news": ["新闻一", "新闻二"]},
        }
    if path.endswith("/v2/douyin"):
        return {"code": 200, "data": [{"title": "抖音零热度", "hot_value": 0}]}
    return {"code": 200, "data": [{"title": "栏目一"}]}


class FakeSectionImage:
    """与渲染器冻结的 SectionImage 同形的替身，用于验证 API 层结构。"""

    def __init__(
        self,
        index: int,
        content_type: str,
        label: str,
        status: str,
        *,
        png: bytes | None = None,
        error: str | None = None,
        height: int = 640,
    ) -> None:
        self.index = index
        self.content_type = content_type
        self.label = label
        self.status = status
        self.width = 900
        self.height = height
        self.png = png
        self.error = error


class FakeImageService:
    """只返回预置栏目图或预置异常，不执行真实渲染。"""

    def __init__(self, images=(), error: Exception | None = None) -> None:
        self.images = tuple(images)
        self.error = error
        self.calls = 0

    async def render(self, _data, *, created_at: str = ""):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.images


@unittest.skipUnless(has_font(), "no CJK font available")
class ProfileImageApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "db.sqlite3")
        await self.sites.open()
        self.site = await self.sites.create_site(
            SiteCreate(
                name="图片站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=5,
            ),
        )
        self.calls: list[str] = []
        self.fail_all = False

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if self.fail_all:
                return httpx.Response(500, content=b"error")
            return httpx.Response(
                200,
                content=json.dumps(payload(request.url.path)).encode(),
            )

        self.client = ContentHttpClient(transport=httpx.MockTransport(handler))
        self.content = ContentService(self.sites, client=self.client)
        await self.content.open()
        self.profiles = ContentProfileService(self.sites)
        self.clock = FakeClock()
        self.snapshots = ImageSnapshotStore(
            ttl_seconds=10,
            max_snapshots=4,
            monotonic=self.clock.monotonic,
            wall_clock=self.clock.now,
        )
        self.image_service = ProfileImageService(timeout_seconds=10)
        self.api = ProfileApi(
            self.profiles,
            self.content,
            self.sites,
            image_snapshots=self.snapshots,
            image_service=self.image_service,
        )

    async def asyncTearDown(self) -> None:
        await self.image_service.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def _profile(self, items=None):
        if items is None:
            items = [
                {"content_type": "daily_news", "params": {}, "limit": 1},
                {"content_type": "douyin_hot", "params": {}, "limit": None},
            ]
        result = await self.api.create_profile(
            {
                "name": "图片组合",
                "site_id": self.site.id,
                "items": items,
            },
        )
        assert result.status_code == 201, result.body
        return result.body["data"]

    async def _snapshot_for(self, api, profile):
        preview = await api.preview_profile(
            {"profile_id": profile["id"]},
            owner="alice",
        )
        self.assertEqual(preview.status_code, 200, preview.body)
        return preview, preview.body["data"]["image_snapshot"]["id"]

    def _api_with(self, image_service):
        return ProfileApi(
            self.profiles,
            self.content,
            self.sites,
            image_snapshots=self.snapshots,
            image_service=image_service,
        )

    async def test_preview_snapshot_and_image_same_content_without_fetch(self) -> None:
        profile = await self._profile()
        preview = await self.api.preview_profile(
            {"profile_id": profile["id"]},
            owner="alice",
        )
        self.assertEqual(preview.status_code, 200, preview.body)
        snapshot = preview.body["data"]["image_snapshot"]
        self.assertIn("id", snapshot)
        self.assertIn("expires_at", snapshot)
        calls_before = list(self.calls)
        image = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot["id"]},
            owner="alice",
        )
        self.assertEqual(image.status_code, 200, image.body)
        self.assertEqual(self.calls, calls_before)
        data = image.body["data"]
        self.assertNotIn("pages", data)
        self.assertEqual(set(data), {"images", "failed_sections", "snapshot_id"})
        self.assertEqual(data["snapshot_id"], snapshot["id"])
        images = data["images"]
        self.assertEqual([item["index"] for item in images], [1, 2])
        self.assertEqual(
            [item["content_type"] for item in images],
            ["daily_news", "douyin_hot"],
        )
        self.assertEqual([item["label"] for item in images], ["每日新闻", "抖音热搜"])
        self.assertEqual(data["failed_sections"], [])
        for item in images:
            self.assertIn(item["status"], ("success", "empty"))
            self.assertEqual(item["width"], 900)
            self.assertGreater(item["height"], 0)
            self.assertNotIn("error", item)
            raw = base64.b64decode(item["png_base64"], validate=True)
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(raw)) as opened:
                self.assertEqual(opened.size, (item["width"], item["height"]))

        # 上游变化不替换已保存快照，也不增加上游请求。
        first_png = images[0]["png_base64"]
        self.fail_all = True
        second = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot["id"]},
            owner="alice",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.body["data"]["images"][0]["png_base64"], first_png)
        self.assertEqual(self.calls, calls_before)

    async def test_owner_expiry_and_configuration_invalidation(self) -> None:
        profile = await self._profile()
        preview = await self.api.preview_profile(
            {"profile_id": profile["id"]},
            owner="alice",
        )
        snapshot_id = preview.body["data"]["image_snapshot"]["id"]
        wrong_owner = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="bob",
        )
        self.assertEqual(wrong_owner.status_code, 403)
        self.assertEqual(wrong_owner.body["data"]["code"], "forbidden")

        updated = await self.api.update_profile(
            profile["id"],
            {
                "name": "改名",
                "site_id": self.site.id,
                "items": [
                    {"content_type": "daily_news", "params": {}, "limit": 1},
                ],
                "expected_revision": profile["revision"],
            },
        )
        self.assertEqual(updated.status_code, 200)
        stale = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.body["data"]["code"], "snapshot_stale")

        # New snapshot, then disable the site.
        preview2 = await self.api.preview_profile(
            {"profile_id": profile["id"]},
            owner="alice",
        )
        snapshot2 = preview2.body["data"]["image_snapshot"]["id"]
        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name=self.site.name,
                base_url=self.site.base_url,
                enabled=False,
                timeout_seconds=self.site.timeout_seconds,
                expected_revision=self.site.revision,
            ),
        )
        stale_disabled = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot2},
            owner="alice",
        )
        self.assertEqual(stale_disabled.status_code, 409)

        self.clock.advance(11)
        expired = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot2},
            owner="alice",
        )
        self.assertEqual(expired.status_code, 410)
        self.assertEqual(expired.body["data"]["code"], "snapshot_expired")

    async def test_all_failure_has_no_snapshot_and_invalid_id(self) -> None:
        profile = await self._profile()
        self.fail_all = True
        failed = await self.api.preview_profile(
            {"profile_id": profile["id"]},
            owner="alice",
        )
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("image_snapshot", failed.body["data"])
        self.assertNotIn("outputs", failed.body["data"])
        invalid = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": "unknown"},
            owner="alice",
        )
        self.assertEqual(invalid.status_code, 410)
        bad = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": 123},
            owner="alice",
        )
        self.assertEqual(bad.status_code, 400)
    async def test_deleted_profile_snapshot_is_stale(self) -> None:
        profile = await self._profile()
        preview = await self.api.preview_profile(
            {"profile_id": profile["id"]},
            owner="alice",
        )
        snapshot_id = preview.body["data"]["image_snapshot"]["id"]
        await self.profiles.delete_profile(
            profile["id"],
            profile["revision"],
        )
        image = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(image.status_code, 409)
        self.assertEqual(image.body["data"]["code"], "snapshot_stale")

    async def test_section_statuses_and_failed_labels(self) -> None:
        profile = await self._profile(items=[
            {"content_type": "daily_news", "params": {}, "limit": 1},
            {"content_type": "douyin_hot", "params": {}, "limit": None},
            {"content_type": "zhihu_hot", "params": {}, "limit": None},
        ])
        _, snapshot_id = await self._snapshot_for(self.api, profile)
        calls_before = list(self.calls)
        service = FakeImageService(images=(
            FakeSectionImage(1, "daily_news", "每日新闻", "success", png=b"\x89PNG-one"),
            FakeSectionImage(2, "douyin_hot", "抖音热搜", "empty", png=b"\x89PNG-two"),
            FakeSectionImage(3, "zhihu_hot", "知乎热榜", "failed", error="too_tall"),
        ))
        api = self._api_with(service)
        result = await api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        images = data["images"]
        self.assertEqual(
            [item["status"] for item in images],
            ["success", "empty", "failed"],
        )
        self.assertEqual(data["failed_sections"], ["知乎热榜"])
        self.assertEqual(data["snapshot_id"], snapshot_id)
        self.assertNotIn("png_base64", images[2])
        self.assertEqual(images[2]["error"], "too_tall")
        self.assertNotIn("error", images[0])
        self.assertEqual(base64.b64decode(images[0]["png_base64"]), b"\x89PNG-one")
        # 同一快照渲染不增加上游请求。
        self.assertEqual(self.calls, calls_before)
        self.assertEqual(service.calls, 1)

    async def test_unknown_status_and_unsafe_error_code_are_normalised(self) -> None:
        profile = await self._profile()
        _, snapshot_id = await self._snapshot_for(self.api, profile)
        service = FakeImageService(images=(
            FakeSectionImage(1, "daily_news", "每日新闻", "weird", png=b"\x89PNG"),
            FakeSectionImage(
                2, "douyin_hot", "抖音热搜", "failed",
                error="line1\n<b>unsafe</b>",
            ),
        ))
        api = self._api_with(service)
        result = await api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        # 未知状态不得冒充成功：该栏目降级为 failed，两张图都失败时整体返回错误。
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.body["data"]["code"], "image_render_failed")
        images = result.body["data"]["images"]
        self.assertEqual([item["status"] for item in images], ["failed", "failed"])
        self.assertEqual(images[0]["error"], "render_failed")
        self.assertNotIn("png_base64", images[0])
        self.assertEqual(images[1]["error"], "render_failed")
        self.assertEqual(
            result.body["data"]["failed_sections"],
            ["每日新闻", "抖音热搜"],
        )

    async def test_unknown_status_alone_does_not_block_other_sections(self) -> None:
        profile = await self._profile()
        _, snapshot_id = await self._snapshot_for(self.api, profile)
        service = FakeImageService(images=(
            FakeSectionImage(1, "daily_news", "每日新闻", "weird", png=b"\x89PNG"),
            FakeSectionImage(2, "douyin_hot", "抖音热搜", "empty", png=b"\x89PNG-two"),
        ))
        api = self._api_with(service)
        result = await api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(result.status_code, 200, result.body)
        images = result.body["data"]["images"]
        self.assertEqual(images[0]["status"], "failed")
        self.assertEqual(images[0]["error"], "render_failed")
        self.assertEqual(images[1]["status"], "empty")
        self.assertIn("png_base64", images[1])
        self.assertEqual(result.body["data"]["failed_sections"], ["每日新闻"])

    async def test_all_sections_failed_returns_error_without_png(self) -> None:
        profile = await self._profile()
        _, snapshot_id = await self._snapshot_for(self.api, profile)
        service = FakeImageService(images=(
            FakeSectionImage(1, "daily_news", "每日新闻", "failed", error="too_tall"),
            FakeSectionImage(2, "douyin_hot", "抖音热搜", "failed", error="too_wide"),
        ))
        api = self._api_with(service)
        result = await api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.body["data"]["code"], "image_render_failed")
        self.assertEqual(
            result.body["data"]["failed_sections"],
            ["每日新闻", "抖音热搜"],
        )
        for item in result.body["data"]["images"]:
            self.assertNotIn("png_base64", item)

    async def test_render_limit_error_keeps_text_outputs(self) -> None:
        service = FakeImageService(error=ImageLimitError("too tall"))
        api = self._api_with(service)
        profile = await self._profile()
        preview, snapshot_id = await self._snapshot_for(api, profile)
        image = await api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(image.status_code, 422)
        self.assertEqual(image.body["data"]["code"], "image_limit_exceeded")
        # 同一预览的纯文字/Markdown 仍然可用。
        self.assertIn("outputs", preview.body["data"])

    async def test_total_output_bytes_limit_is_enforced(self) -> None:
        profile = await self._profile()
        _, snapshot_id = await self._snapshot_for(self.api, profile)
        oversized = b"x" * (5 * 1024 * 1024)
        service = FakeImageService(images=(
            FakeSectionImage(1, "daily_news", "每日新闻", "success", png=oversized),
            FakeSectionImage(2, "douyin_hot", "抖音热搜", "success", png=oversized),
        ))
        api = self._api_with(service)
        result = await api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot_id},
            owner="alice",
        )
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.body["data"]["code"], "image_limit_exceeded")
