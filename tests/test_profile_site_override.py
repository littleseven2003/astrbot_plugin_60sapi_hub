from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.profile_api import ProfileApi
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService

SECRET = "simulated-secret-abc123"


class ProfileSiteOverrideTest(unittest.IsolatedAsyncioTestCase):
    """指定候选站点后，栏目与输出必须真的来自该站点。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "override.sqlite3"
        self.hosts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            if request.url.path.endswith("/v2/60s"):
                news = ["A新闻"] if host.startswith("a.") else ["B新闻一", "B新闻二"]
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-11-25", "news": news},
                }).encode())
            hot = [{"title": "A热榜", "hot_value": 1}] if host.startswith("a.") else [
                {"title": "B热榜一", "hot_value": 1},
                {"title": "B热榜二", "hot_value": 2},
            ]
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok", "data": hot,
            }).encode())

        self.sites = SiteService(self.path)
        await self.sites.open()
        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.profiles = ContentProfileService(self.sites)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.api = ProfileApi(
            self.profiles, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.a = await self.sites.create_site(SiteCreate(
            name="站点A", base_url=f"https://a.example.test/{SECRET}",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="站点B", base_url=f"https://b.example.test/{SECRET}",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.profile = await self.profiles.create_profile(ProfileCreate(
            name="组合", site_id=self.a.id,
            items=[
                {"content_type": "daily_news", "params": {}, "limit": 2},
                {"content_type": "zhihu_hot", "params": {}, "limit": 2},
            ],
        ))

    async def asyncTearDown(self) -> None:
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def test_sections_and_outputs_come_from_requested_site(self) -> None:
        self.hosts.clear()
        result = await self.api.preview_profile(
            {"profile_id": self.profile.id}, site_id=self.b.id,
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["site_id"], self.b.id)
        for section in data["sections"]:
            self.assertIn("b.example.test", section["source_url"])
            self.assertEqual(section["source_display"], "b.example.test")
        text = data["outputs"]["text"]
        self.assertIn("B新闻", text)
        self.assertIn("B热榜", text)
        self.assertNotIn("A新闻", text)
        self.assertNotIn("A热榜", text)
        # 上游请求也确实只打到 B。
        self.assertTrue(self.hosts)
        self.assertTrue(all(h.startswith("b.") for h in self.hosts), self.hosts)

    async def test_snapshot_from_selected_site_can_generate_image(self) -> None:
        result = await self.api.preview_profile(
            {"profile_id": self.profile.id}, site_id=self.b.id,
        )
        data = result.body["data"]
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertEqual(snapshot.site_id, self.b.id)
        image = await self.api.generate_image(
            self.profile.id, {"snapshot_id": snapshot.id},
        )
        self.assertEqual(image.status_code, 200, image.body)

    async def test_default_site_still_used_without_override(self) -> None:
        result = await self.api.preview_profile({"site_id": self.a.id, "profile_id": self.profile.id})
        data = result.body["data"]
        self.assertEqual(data["site_id"], self.a.id)
        self.assertIn("A新闻", data["outputs"]["text"])


if __name__ == "__main__":
    unittest.main()
