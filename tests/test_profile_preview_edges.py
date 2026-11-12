from __future__ import annotations

import json
import re
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
        self.responses: list[dict[str, object]] = []
        self.host_seq: dict[str, int] = {}
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
            # 数据版本只由本次请求实际访问的 host 决定，且在任何等待之前确定：
            # 请求已经发往旧 host 时，即使等待期间站点被改，返回的仍是旧数据。
            host = request.url.host
            tag = self._host_tag(host)
            seq = self.host_seq.get(host, 0) + 1
            self.host_seq[host] = seq
            self.calls.append(request.url.path)
            self.responses.append(
                {"host": host, "tag": tag, "seq": seq, "path": request.url.path},
            )
            await self._maybe_barrier_in_flight()
            return httpx.Response(
                200,
                content=json.dumps(self._body(tag, seq, request.url.path)).encode(),
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

    @staticmethod
    def _host_tag(host: str) -> str:
        """gen0 是初始 host，其余（gen1、gen2…）都是切换后的新 host。"""
        return "OLD" if host.startswith("gen0.") else "NEW"

    def _body(self, tag: str, seq: int, path: str) -> dict:
        prefix = f"{tag}#{seq}"
        if path.endswith("/v2/60s"):
            return {
                "code": 200, "message": "ok",
                "data": {
                    "date": "2026-11-13",
                    "news": [
                        f"{prefix}-新闻一",
                        f"{prefix}-新闻二",
                        f"{prefix}-新闻三",
                    ],
                },
            }
        return {
            "code": 200, "message": "ok",
            "data": [
                {"title": f"{prefix}-热榜一", "hot_value": 10},
                {"title": f"{prefix}-热榜二", "hot_value": 20},
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
        # 只有最终那一轮的上游响应，被丢弃那一轮的内容不得残留。
        final_round = self.responses[-2:]
        self.assertEqual(len(final_round), 2, self.responses)
        expected = {f"{item['tag']}#{item['seq']}" for item in final_round}
        self.assertEqual(len(expected), 2, final_round)
        hot_tag = next(
            f"{item['tag']}#{item['seq']}" for item in final_round
            if str(item["path"]).endswith("/v2/douyin")
        )
        news_tag = next(
            f"{item['tag']}#{item['seq']}" for item in final_round
            if str(item["path"]).endswith("/v2/60s")
        )
        text = data["outputs"]["text"]
        markdown = data["outputs"]["markdown"]
        self.assertEqual(set(re.findall(r"[A-Z]+#\d+", text)), expected)
        # Markdown 会转义 # 等字符，去掉转义后再比对标签。
        self.assertEqual(
            set(re.findall(r"[A-Z]+#\d+", markdown.replace("\\", ""))),
            expected,
        )
        self.assertLess(text.index(hot_tag), text.index(news_tag))
        for section in data["sections"]:
            titles = " ".join(
                str(entry.get("title")) for entry in section["items"]
            )
            self.assertIn(section["content_type"] == "douyin_hot" and hot_tag
                          or news_tag, titles)
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.profile_revision, final.revision)
        snap_tags = {
            str(entry.get("title"))
            for section in snapshot.data["sections"]
            for entry in section["items"]
        }
        self.assertTrue(snap_tags)
        for title in snap_tags:
            self.assertTrue(
                any(title.startswith(tag + "-") for tag in expected), title,
            )

    # ---- 4. force_refresh 两种取值 + 预热缓存 ----

    async def _warm_cache(self, profile) -> None:
        # 预热旧 revision 的缓存，确保后续 preview 可能直接命中。
        for item in profile.items:
            await self.content.preview_cached(item.content_type, self.site.id)
        self.calls.clear()

    async def _assert_only_tag(self, data, tag: str) -> None:
        """成功结果的每一处内容都必须来自同一个数据版本。"""
        for section in data["sections"]:
            for entry in section["items"]:
                title = str(entry.get("title"))
                self.assertTrue(
                    title.startswith(f"{tag}#"), (section["content_type"], title),
                )
        text = data["outputs"]["text"]
        markdown = data["outputs"]["markdown"].replace("\\", "")
        text_tags = set(re.findall(r"[A-Z]+#\d+", text))
        markdown_tags = set(re.findall(r"[A-Z]+#\d+", markdown))
        self.assertTrue(text_tags, text)
        self.assertEqual(text_tags, markdown_tags, (text_tags, markdown_tags))
        for found in text_tags:
            self.assertTrue(found.startswith(f"{tag}#"), found)
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertIsNotNone(snapshot)
        snap_tags = {
            str(entry.get("title")).split("-")[0]
            for section in snapshot.data["sections"]
            for entry in section["items"]
        }
        self.assertTrue(snap_tags)
        self.assertEqual(snap_tags, text_tags)

    async def _final_source(self, data) -> dict:
        kinds = {section["source_kind"] for section in data["sections"]}
        hosts = {section["source_display"] for section in data["sections"]}
        self.assertEqual(len(kinds), 1, kinds)
        self.assertEqual(len(hosts), 1, hosts)
        return {"kind": kinds.pop(), "host": hosts.pop()}

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
        final_site = await self.sites.get_site(self.site.id)
        source = await self._final_source(data)
        self.assertEqual(source["kind"], final_site.source_kind)
        self.assertEqual(source["host"], "gen1.example.test")
        # 每个成功栏目的内容、文字、Markdown 与图片快照都只对应新 host。
        await self._assert_only_tag(data, "NEW")
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertEqual(snapshot.site_revision, final_site.revision)
        # 夹具确实同时产生过两种响应，否则“只有新数据”没有区分力。
        tags = {str(item["tag"]) for item in self.responses}
        self.assertEqual(tags, {"OLD", "NEW"}, tags)
        # force_refresh 绕过缓存：两轮各请求全部栏目，上界 2 × 栏目数。
        self.assertLessEqual(len(self.calls), 2 * len(profile.items), self.calls)
        self.assertGreaterEqual(len(self.calls), len(profile.items), self.calls)

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
        final_site = await self.sites.get_site(self.site.id)
        source = await self._final_source(data)
        self.assertEqual(source["kind"], final_site.source_kind)
        await self._assert_only_tag(data, "NEW")
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertEqual(snapshot.site_revision, final_site.revision)
        # 首轮前段命中旧 revision 缓存，切换后各栏目各请求一次；
        # 夹具固定为 3 个栏目、切换发生在第 2 个栏目之前，上界 3。
        self.assertLessEqual(len(self.calls), len(profile.items), self.calls)

    async def test_force_refresh_false_in_flight_request_keeps_old_body(
        self,
    ) -> None:
        """请求已发出但未返回时切换站点：该响应必须是旧 host 的数据。"""
        profile = await self._profile()
        # 不预热缓存，确保第一个栏目真的发出上游请求。
        self.barrier = self._switch_site
        result = await self.api.preview_profile(
            {"profile_id": profile.id, "force_refresh": False},
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        final_site = await self.sites.get_site(self.site.id)
        source = await self._final_source(data)
        self.assertEqual(source["kind"], final_site.source_kind)
        await self._assert_only_tag(data, "NEW")
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertEqual(snapshot.site_revision, final_site.revision)
        # 在途请求确实落在旧 host，并且返回的是旧数据而不是新数据。
        first = self.responses[0]
        self.assertEqual(first["host"], "gen0.example.test")
        self.assertEqual(first["tag"], "OLD")
        # 两轮：首轮 3 次（1 旧 + 2 新），次轮补新 revision 的 1 次未命中。
        self.assertLessEqual(len(self.calls), len(profile.items) + 1, self.calls)


if __name__ == "__main__":
    unittest.main()
