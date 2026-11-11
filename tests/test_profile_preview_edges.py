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
from core.profiles import ContentProfileService, ProfileCreate, ProfileUpdate
from core.sites import SiteCreate, SiteService, SiteUpdate

SECRET = "simulated-secret-abc123"


class ProfilePreviewEdgeTest(unittest.IsolatedAsyncioTestCase):
    """组合预览版本复核的边界行为（停用、删除、修改、缓存预热）。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "edges.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.calls: list[str] = []
        self.generation = 0
        self.site = await self.sites.create_site(
            SiteCreate(
                name="来源站点",
                base_url=f"https://gen0.example.test/{SECRET}",
                source_kind="self_built",
                enabled=True,
                timeout_seconds=5,
            ),
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            await self._maybe_barrier_in_flight()
            return httpx.Response(
                200, content=json.dumps(self._body(request.url.path)).encode(),
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
        self.barrier = None
        self.barrier_fired = False

    async def asyncTearDown(self) -> None:
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    def _body(self, path: str) -> dict:
        tag = f"G{self.generation}"
        if path.endswith("/v2/60s"):
            return {
                "code": 200, "message": "ok",
                "data": {
                    "date": "2026-11-13",
                    "news": [f"{tag}-新闻一", f"{tag}-新闻二", f"{tag}-新闻三"],
                },
            }
        return {
            "code": 200, "message": "ok",
            "data": [
                {"title": f"{tag}-热榜一", "hot_value": 10},
                {"title": f"{tag}-热榜二", "hot_value": 20},
            ],
        }

    async def _maybe_barrier_in_flight(self) -> None:
        """在首个上游请求“在途”时执行一次屏障动作。"""
        if self.barrier is None or self.barrier_fired:
            return
        self.barrier_fired = True
        await self.barrier()

    async def _after_section_barrier(self) -> None:
        """在首个栏目取回后执行一次屏障动作。"""
        if self.barrier is None or self.barrier_fired:
            return
        self.barrier_fired = True
        await self.barrier()

    def _wrap_preview_cached(self) -> None:
        """栏目之间触发屏障：用于缓存预热、无上游请求的场景。"""
        original = self.content.preview_cached

        async def wrapped(content_type, site_id, **kwargs):
            result = await original(content_type, site_id, **kwargs)
            await self._after_section_barrier()
            return result

        self.content.preview_cached = wrapped  # type: ignore[assignment]

    async def _profile(self, items=None, name: str = "边界组合"):
        return await self.profiles.create_profile(
            ProfileCreate(
                name=name,
                site_id=self.site.id,
                items=items
                or [
                    {"content_type": "daily_news", "params": {}, "limit": 3},
                    {"content_type": "zhihu_hot", "params": {}, "limit": 2},
                    {"content_type": "douyin_hot", "params": {}, "limit": 2},
                ],
            ),
        )

    async def _disable_site(self) -> None:
        current = await self.sites.get_site(self.site.id)
        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name=current.name,
                base_url=current.base_url,
                enabled=False,
                timeout_seconds=current.timeout_seconds,
                source_kind=current.source_kind,
                expected_revision=current.revision,
            ),
        )

    async def _switch_site(self) -> None:
        current = await self.sites.get_site(self.site.id)
        self.generation += 1
        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name=current.name,
                base_url=f"https://gen{self.generation}.example.test/{SECRET}",
                enabled=True,
                timeout_seconds=current.timeout_seconds,
                source_kind="public" if self.generation % 2 else "self_built",
                expected_revision=current.revision,
            ),
        )

    # ---- 1. 预览进行中停用站点 ----

    async def test_site_disabled_mid_preview_returns_409_without_sections(
        self,
    ) -> None:
        profile = await self._profile()
        self.barrier = self._disable_site
        self._wrap_preview_cached()
        result = await self.api.preview_profile({"profile_id": profile.id})
        self.assertEqual(result.status_code, 409, result.body)
        data = result.body.get("data") or {}
        self.assertEqual(data.get("code"), "site_disabled")
        self.assertNotIn("sections", data)
        self.assertNotIn("outputs", data)
        self.assertEqual(self.snapshots.size, 0)
        # 至少有一个栏目已经开始获取，屏障才可能触发。
        self.assertTrue(self.barrier_fired)

    async def test_site_disabled_before_preview_returns_409(self) -> None:
        profile = await self._profile()
        await self._disable_site()
        result = await self.api.preview_profile({"profile_id": profile.id})
        self.assertEqual(result.status_code, 409, result.body)
        data = result.body.get("data") or {}
        self.assertEqual(data.get("code"), "site_disabled")
        self.assertNotIn("sections", data)
        self.assertEqual(self.snapshots.size, 0)
        self.assertEqual(self.calls, [])

    # ---- 2. 预览进行中删除组合 ----

    async def test_profile_deleted_mid_preview_returns_404_without_sections(
        self,
    ) -> None:
        profile = await self._profile()

        async def delete_profile() -> None:
            current = await self.profiles.get_profile(profile.id)
            await self.profiles.delete_profile(profile.id, current.revision)

        self.barrier = delete_profile
        self._wrap_preview_cached()
        result = await self.api.preview_profile({"profile_id": profile.id})
        self.assertEqual(result.status_code, 404, result.body)
        data = result.body.get("data") or {}
        self.assertNotIn("sections", data)
        self.assertNotIn("outputs", data)
        self.assertEqual(self.snapshots.size, 0)

    # ---- 3. 预览进行中修改组合 ----

    async def test_profile_updated_mid_preview_uses_final_configuration(
        self,
    ) -> None:
        profile = await self._profile()
        updated_items = [
            {"content_type": "douyin_hot", "params": {}, "limit": 1},
            {"content_type": "daily_news", "params": {}, "limit": 1},
        ]

        async def update_profile() -> None:
            current = await self.profiles.get_profile(profile.id)
            self.generation += 1
            await self.profiles.update_profile(
                profile.id,
                ProfileUpdate(
                    name=current.name,
                    site_id=current.site_id,
                    items=updated_items,
                    expected_revision=current.revision,
                ),
            )

        self.barrier = update_profile
        self._wrap_preview_cached()
        result = await self.api.preview_profile(
            {"profile_id": profile.id, "force_refresh": True},
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        final = await self.profiles.get_profile(profile.id)
        self.assertEqual(data["profile_revision"], final.revision)
        self.assertGreater(final.revision, profile.revision)
        # 栏目顺序与条数来自更新后的配置。
        self.assertEqual(
            [section["content_type"] for section in data["sections"]],
            ["douyin_hot", "daily_news"],
        )
        self.assertEqual(
            [section["limit"] for section in data["sections"]], [1, 1],
        )
        self.assertEqual(
            [section["displayed_count"] for section in data["sections"]], [1, 1],
        )
        # 只有更新后的数据，没有被丢弃那一轮的内容。
        text = data["outputs"]["text"]
        markdown = data["outputs"]["markdown"]
        self.assertIn("G1-热榜一", text)
        self.assertIn("G1-新闻一", text)
        self.assertNotIn("G0-", text)
        self.assertNotIn("G0-", markdown)
        self.assertLess(text.index("G1-热榜一"), text.index("G1-新闻一"))
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.profile_revision, final.revision)

    # ---- 4. force_refresh 两种取值 + 预热缓存 ----

    async def _warm_cache(self, profile) -> None:
        # 预热旧 revision 的缓存，确保后续 preview 可能直接命中。
        for item in profile.items:
            await self.content.preview_cached(item.content_type, self.site.id)
        self.calls.clear()

    async def _assert_no_mixed_sources(self, data) -> None:
        kinds = {section["source_kind"] for section in data["sections"]}
        self.assertEqual(len(kinds), 1, kinds)
        hosts = {
            section["source_display"] for section in data["sections"]
        }
        self.assertEqual(len(hosts), 1, hosts)
        text = data["outputs"]["text"]
        for host in hosts:
            self.assertIn(host, text)
        return kinds.pop()

    async def test_force_refresh_true_does_not_mix_in_flight_site_change(
        self,
    ) -> None:
        profile = await self._profile()
        await self._warm_cache(profile)
        self.barrier = self._switch_site
        result = await self.api.preview_profile(
            {"profile_id": profile.id, "force_refresh": True},
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        kind = await self._assert_no_mixed_sources(data)
        final_site = await self.sites.get_site(self.site.id)
        self.assertEqual(kind, final_site.source_kind)
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.site_revision, final_site.revision)
        # 两轮各自重新请求全部栏目，上界 2 × 栏目数。
        self.assertLessEqual(len(self.calls), 2 * len(profile.items))

    async def test_force_refresh_false_does_not_mix_with_warm_cache(
        self,
    ) -> None:
        profile = await self._profile()
        await self._warm_cache(profile)
        self.barrier = self._switch_site
        self._wrap_preview_cached()
        result = await self.api.preview_profile(
            {"profile_id": profile.id, "force_refresh": False},
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        kind = await self._assert_no_mixed_sources(data)
        final_site = await self.sites.get_site(self.site.id)
        self.assertEqual(kind, final_site.source_kind)
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.site_revision, final_site.revision)
        # 首轮可命中旧 revision 缓存，最终 revision 需要重新请求；
        # 上界仍是两轮的全部栏目。
        self.assertLessEqual(len(self.calls), 2 * len(profile.items))


if __name__ == "__main__":
    unittest.main()
