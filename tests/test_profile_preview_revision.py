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
from core.sites import SiteCreate, SiteService, SiteUpdate

SECRET = "simulated-secret-abc123"


def body_for(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200, "message": "ok",
            "data": {"date": "2026-11-12", "news": ["新闻一", "新闻二"]},
        }
    return {
        "code": 200, "message": "ok",
        "data": [{"title": "热榜一", "hot_value": 10}],
    }


class ProfilePreviewRevisionTest(unittest.IsolatedAsyncioTestCase):
    """组合预览中途改站点配置时，不得返回混合来源的成功结果。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "preview.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.mutate_on_call: int | None = None
        self.always_mutate = False
        self.generation = 0
        self.calls: list[str] = []
        self.site = await self.sites.create_site(
            SiteCreate(
                name="来源站点",
                base_url="https://old.example.test/" + SECRET,
                source_kind="self_built",
                enabled=True,
                timeout_seconds=5,
            ),
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if self.always_mutate or self.mutate_on_call == len(self.calls):
                await self._bump_site()
            return httpx.Response(
                200, content=json.dumps(body_for(request.url.path)).encode(),
            )

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.api = ProfileApi(
            self.profiles, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )

    async def asyncTearDown(self) -> None:
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _bump_site(self) -> None:
        """每次调用都推进一次站点 revision，并切换来源类型。"""
        current = await self.sites.get_site(self.site.id)
        self.generation += 1
        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name=current.name,
                base_url=f"https://gen{self.generation}.example.test/{SECRET}",
                enabled=True,
                timeout_seconds=5,
                source_kind="public" if self.generation % 2 else "self_built",
                expected_revision=current.revision,
            ),
        )

    async def _profile(self):
        return await self.profiles.create_profile(
            ProfileCreate(
                name="多栏目组合",
                site_id=self.site.id,
                items=[
                    {"content_type": "daily_news", "params": {}, "limit": 2},
                    {"content_type": "zhihu_hot", "params": {}, "limit": 1},
                    {"content_type": "douyin_hot", "params": {}, "limit": 1},
                ],
            ),
        )

    async def test_mid_preview_change_never_mixes_sources(self) -> None:
        profile = await self._profile()
        # 第一次上游请求返回前切换站点来源类型，制造“中途变化”。
        self.mutate_on_call = 1
        result = await self.api.preview_profile({"profile_id": profile.id})
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        # 整次结果必须来自同一个 revision：要么全旧，要么全新，且与新状态一致。
        kinds = {section["source_kind"] for section in data["sections"]}
        self.assertEqual(len(kinds), 1, kinds)
        self.assertEqual(kinds, {"public"})
        final_site = await self.sites.get_site(self.site.id)
        self.assertEqual(final_site.source_kind, "public")
        self.assertEqual(data["site_id"], final_site.id)
        # 文字与 Markdown 不得出现旧的自建来源。
        text = data["outputs"]["text"]
        markdown = data["outputs"]["markdown"]
        self.assertNotIn("自建服务", text)
        self.assertNotIn("自建服务", markdown)
        self.assertIn("gen1.example.test", text)
        self.assertIn("gen1.example.test", markdown)
        self.assertNotIn("old.example.test", text)
        self.assertNotIn("old.example.test", markdown)
        # 图片快照登记的站点 revision 必须与栏目一致，不再触发 stale。
        snapshot_id = data["image_snapshot"]["id"]
        snapshot = self.snapshots.get(snapshot_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.site_revision, final_site.revision)
        image = await self.api.generate_image(
            profile.id, {"snapshot_id": snapshot_id},
        )
        self.assertEqual(image.status_code, 200, image.body)
        images = image.body["data"]["images"]
        self.assertEqual(len(images), 3)
        self.assertTrue(all(item["status"] == "success" for item in images))

    async def test_persistent_change_fails_without_mixing(self) -> None:
        profile = await self._profile()
        # 每次请求前都改配置：无法收敛时必须明确失败，而不是拼接结果。
        self.always_mutate = True
        result = await self.api.preview_profile({"profile_id": profile.id})
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(
            result.body["data"]["code"], "preview_revision_changed",
        )
        self.assertNotIn("sections", result.body.get("data") or {})
        # 失败响应不带旧栏目，也不登记图片快照。
        self.assertEqual(self.snapshots.size, 0)

    async def test_single_section_upstream_failure_is_not_a_conflict(self) -> None:
        profile = await self._profile()
        original = self.content.preview_cached
        calls = {"count": 0}

        async def flaky(content_type, site_id, **kwargs):
            calls["count"] += 1
            if content_type == "zhihu_hot":
                raise httpx.ConnectError("upstream down")
            return await original(content_type, site_id, **kwargs)

        self.content.preview_cached = flaky  # type: ignore[assignment]
        result = await self.api.preview_profile({"profile_id": profile.id})
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["failure_count"], 1)
        failed = [s for s in data["sections"] if s["status"] != "ok"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["content_type"], "zhihu_hot")


if __name__ == "__main__":
    unittest.main()
