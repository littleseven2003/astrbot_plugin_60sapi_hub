from __future__ import annotations

import asyncio
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
from core.profile_image import FontUnavailableError, find_cjk_font
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

    async def _profile(self):
        result = await self.api.create_profile(
            {
                "name": "图片组合",
                "site_id": self.site.id,
                "items": [
                    {"content_type": "daily_news", "params": {}, "limit": 1},
                    {"content_type": "douyin_hot", "params": {}, "limit": None},
                ],
            },
        )
        assert result.status_code == 201, result.body
        return result.body["data"]

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
        self.assertEqual(data["page_count"], len(data["pages"]))
        self.assertGreaterEqual(data["page_count"], 1)
        for page in data["pages"]:
            raw = __import__("base64").b64decode(page["png_base64"])
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(raw)) as opened:
                self.assertEqual(opened.size, (page["width"], page["height"]))

        # Later upstream data changes do not change the stored snapshot image.
        first_hash = data["pages"][0]["sha256"]
        self.fail_all = True
        second = await self.api.generate_image(
            profile["id"],
            {"snapshot_id": snapshot["id"]},
            owner="alice",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.body["data"]["pages"][0]["sha256"], first_hash)
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

    async def test_image_limit_error_keeps_snapshot(self) -> None:
        from core.profile_image import ImageRenderLimits

        service = ProfileImageService(
            limits=ImageRenderLimits(max_page_bytes=100),
            timeout_seconds=10,
        )
        api = ProfileApi(
            self.profiles,
            self.content,
            self.sites,
            image_snapshots=self.snapshots,
            image_service=service,
        )
        try:
            profile = await self._profile()
            preview = await api.preview_profile(
                {"profile_id": profile["id"]},
                owner="alice",
            )
            snapshot_id = preview.body["data"]["image_snapshot"]["id"]
            image = await api.generate_image(
                profile["id"],
                {"snapshot_id": snapshot_id},
                owner="alice",
            )
            self.assertEqual(image.status_code, 422)
            self.assertEqual(image.body["data"]["code"], "image_limit_exceeded")
            # Text/Markdown still returned for the same preview.
            self.assertIn("outputs", preview.body["data"])
        finally:
            await service.close()
