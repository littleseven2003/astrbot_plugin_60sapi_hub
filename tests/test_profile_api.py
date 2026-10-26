from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.profile_api import ProfileApi
from core.profiles import ContentProfileService
from core.sites import SiteCreate, SiteService, SiteUpdate


def profile_item(content_type, limit=None):
    return {"content_type": content_type, "params": {}, "limit": limit}


class ProfileApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "plugin.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.site = await self.sites.create_site(
            SiteCreate(
                name="组合 API 站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=5,
                bearer_token="profile-secret",
            ),
        )
        self.calls: list[str] = []
        self.mode = "normal"
        self.seen_auth: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            self.seen_auth.append(request.headers.get("authorization", ""))
            if self.mode == "all_error":
                raise httpx.ConnectError("cannot connect")
            if self.mode == "empty":
                return httpx.Response(
                    200,
                    content=json.dumps(_empty_payload(request.url.path)).encode(),
                )
            if self.mode == "zhihu_error" and request.url.path.endswith(
                "/v2/zhihu",
            ):
                return httpx.Response(
                    500,
                    content=b"echo profile-secret",
                )
            return httpx.Response(
                200,
                content=json.dumps(_payload(request.url.path)).encode(),
            )

        self.client = ContentHttpClient(transport=httpx.MockTransport(handler))
        self.content = ContentService(self.sites, client=self.client)
        await self.content.open()
        self.profiles = ContentProfileService(self.sites)
        self.api = ProfileApi(self.profiles, self.content, self.sites)

    async def asyncTearDown(self) -> None:
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def _create(self, items=None, name="组合"):
        result = await self.api.create_profile(
            {
                "name": name,
                "site_id": self.site.id,
                "items": items
                or [
                    profile_item("daily_news", 2),
                    profile_item("zhihu_hot", 1),
                    profile_item("douyin_hot"),
                ],
            },
        )
        self.assertEqual(result.status_code, 201, result.body)
        return result.body["data"]

    async def test_crud_and_revision_conflict(self) -> None:
        created = await self._create()
        profile_id = created["id"]
        self.assertEqual(created["site_name"], "组合 API 站点")
        self.assertEqual(
            [item["content_type"] for item in created["items"]],
            ["daily_news", "zhihu_hot", "douyin_hot"],
        )
        listed = await self.api.list_profiles()
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.body["data"]["profiles"]), 1)
        loaded = await self.api.get_profile(profile_id)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.body["data"]["revision"], 1)

        updated = await self.api.update_profile(
            profile_id,
            {
                "name": "改名组合",
                "site_id": self.site.id,
                "items": [profile_item("toutiao_hot", 1000)],
                "expected_revision": 1,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.body)
        self.assertEqual(updated.body["data"]["revision"], 2)
        self.assertEqual(updated.body["data"]["name"], "改名组合")
        conflict = await self.api.update_profile(
            profile_id,
            {
                "name": "冲突",
                "site_id": self.site.id,
                "items": [profile_item("daily_news")],
                "expected_revision": 1,
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.body["data"]["code"], "revision_conflict")

        deleted = await self.api.delete_profile(
            profile_id,
            {"expected_revision": 2},
        )
        self.assertEqual(deleted.status_code, 200)
        missing = await self.api.get_profile(profile_id)
        self.assertEqual(missing.status_code, 404)

    async def test_validation_and_site_existence(self) -> None:
        invalid_cases = [
            {"name": "", "site_id": self.site.id, "items": [profile_item("daily_news")]},
            {"name": "x", "site_id": self.site.id, "items": []},
            {"name": "x", "site_id": self.site.id, "items": [profile_item("daily_news")] * 2},
            {"name": "x", "site_id": self.site.id, "items": [profile_item("bilibili_hot", True)]},
            {"name": "x", "site_id": self.site.id, "items": [profile_item("daily_news", 0)]},
            {"name": "x", "site_id": self.site.id, "items": [profile_item("zhihu_hot", "1")]},
        ]
        for payload in invalid_cases:
            with self.subTest(payload=payload):
                result = await self.api.create_profile(payload)
                self.assertEqual(result.status_code, 400, result.body)
                self.assertEqual(result.body["data"]["code"], "validation_error")
        missing = await self.api.create_profile(
            {
                "name": "x",
                "site_id": "missing",
                "items": [profile_item("daily_news")],
            },
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.body["data"]["code"], "site_not_found")

    async def test_preview_order_limits_cache_and_force(self) -> None:
        created = await self._create()
        result = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["success_count"], 3)
        self.assertEqual(
            [section["content_type"] for section in data["sections"]],
            ["daily_news", "zhihu_hot", "douyin_hot"],
        )
        self.assertEqual(
            [section["displayed_count"] for section in data["sections"]],
            [2, 1, 3],
        )
        self.assertEqual(
            [section["total_item_count"] for section in data["sections"]],
            [3, 3, 3],
        )
        self.assertEqual(
            sorted(set(self.calls)),
            ["/proxy/v2/60s", "/proxy/v2/douyin", "/proxy/v2/zhihu"],
        )
        outputs = data["outputs"]
        self.assertEqual(set(outputs), {"text", "markdown"})
        self.assertIn("组合：组合", outputs["text"])
        self.assertIn("【每日新闻】", outputs["text"])
        self.assertIn("展示条数：2 / 3（最多 2 条）", outputs["text"])
        self.assertIn("热度：1", outputs["text"])
        self.assertTrue(outputs["markdown"].startswith("# 组合"))
        self.assertIn("## 知乎热榜", outputs["markdown"])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(not section["cache"]["hit"] for section in data["sections"]))

        cached = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(
            all(section["cache"]["hit"] for section in cached.body["data"]["sections"]),
        )
        self.assertEqual(
            [section["total_item_count"] for section in cached.body["data"]["sections"]],
            [3, 3, 3],
        )

        refreshed = await self.api.preview_profile(
            {"profile_id": created["id"], "force_refresh": True},
        )
        self.assertFalse(
            any(section["cache"]["hit"] for section in refreshed.body["data"]["sections"]),
        )
        self.assertEqual(len(self.calls), 6)

        # Profile truncation must not modify the full single-content cache.
        full = await self.content.preview_cached("daily_news", self.site.id)
        self.assertEqual(len(full.snapshot.items), 3)
        self.assertTrue(full.hit)

    async def test_profile_update_uses_new_order_and_site_revision(self) -> None:
        created = await self._create(
            items=[profile_item("daily_news", 1), profile_item("zhihu_hot", 1)],
        )
        first = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(len(self.calls), 2)
        updated = await self.api.update_profile(
            created["id"],
            {
                "name": "新顺序",
                "site_id": self.site.id,
                "items": [
                    profile_item("zhihu_hot", 2),
                    profile_item("daily_news", 1),
                ],
                "expected_revision": created["revision"],
            },
        )
        self.assertEqual(updated.status_code, 200)
        second = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(
            [section["content_type"] for section in second.body["data"]["sections"]],
            ["zhihu_hot", "daily_news"],
        )
        self.assertEqual(
            [section["displayed_count"] for section in second.body["data"]["sections"]],
            [2, 1],
        )
        self.assertEqual(len(self.calls), 2)

        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name="站点改名",
                base_url=self.site.base_url,
                enabled=True,
                timeout_seconds=self.site.timeout_seconds,
                expected_revision=self.site.revision,
            ),
        )
        revised = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(
            all(not section["cache"]["hit"] for section in revised.body["data"]["sections"]),
        )

    async def test_partial_failure_and_all_failure(self) -> None:
        created = await self._create(
            items=[
                profile_item("daily_news"),
                profile_item("zhihu_hot"),
                profile_item("douyin_hot"),
            ],
        )
        self.mode = "zhihu_error"
        partial = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(partial.status_code, 200)
        data = partial.body["data"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["success_count"], 2)
        self.assertEqual(data["failure_count"], 1)
        failed = [section for section in data["sections"] if section["status"] == "error"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["content_type"], "zhihu_hot")
        self.assertEqual(failed[0]["code"], "upstream_http_error")
        outputs = data["outputs"]
        self.assertIn("获取失败", outputs["text"])
        self.assertIn("知乎热榜", outputs["text"])
        self.assertIn("## 知乎热榜", outputs["markdown"])
        serialized = json.dumps(partial.body, ensure_ascii=False)
        self.assertNotIn("profile-secret", serialized)
        self.assertNotIn("echo profile-secret", serialized)
        self.assertEqual(self.seen_auth[0], "Bearer profile-secret")

        self.mode = "all_error"
        all_failed = await self.api.preview_profile(
            {"profile_id": created["id"], "force_refresh": True},
        )
        self.assertEqual(all_failed.status_code, 502)
        self.assertEqual(all_failed.body["data"]["status"], "failed")
        self.assertEqual(all_failed.body["data"]["success_count"], 0)
        self.assertEqual(len(all_failed.body["data"]["sections"]), 3)
        self.assertTrue(
            all(
                section["status"] == "error"
                for section in all_failed.body["data"]["sections"]
            ),
        )
        self.assertNotIn("outputs", all_failed.body["data"])

    async def test_all_five_types_and_legitimate_empty_section(self) -> None:
        created = await self._create(
            items=[
                profile_item("daily_news", 1),
                profile_item("bilibili_hot", 1),
                profile_item("zhihu_hot", 1),
                profile_item("douyin_hot", 1),
                profile_item("toutiao_hot", 1),
            ],
        )
        result = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["status"], "complete")
        self.assertEqual(
            [section["content_type"] for section in result.body["data"]["sections"]],
            [
                "daily_news",
                "bilibili_hot",
                "zhihu_hot",
                "douyin_hot",
                "toutiao_hot",
            ],
        )
        self.mode = "empty"
        empty = await self.api.preview_profile(
            {"profile_id": created["id"], "force_refresh": True},
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.body["data"]["status"], "complete")
        self.assertTrue(
            all(section["displayed_count"] == 0 for section in empty.body["data"]["sections"]),
        )
        self.assertIn("（空列表，0 条）", empty.body["data"]["outputs"]["text"])

    async def test_disabled_site_rejects_before_upstream(self) -> None:
        created = await self._create()
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
        result = await self.api.preview_profile({"profile_id": created["id"]})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.body["data"]["code"], "site_disabled")
        self.assertEqual(self.calls, [])

    async def test_invalid_force_refresh_does_not_fetch(self) -> None:
        created = await self._create()
        for value in ("true", 1, 0, None, []):
            with self.subTest(value=value):
                result = await self.api.preview_profile(
                    {
                        "profile_id": created["id"],
                        "force_refresh": value,
                    },
                )
                self.assertEqual(result.status_code, 400)
                self.assertEqual(
                    result.body["data"]["code"],
                    "invalid_force_refresh",
                )
        self.assertEqual(self.calls, [])


def _empty_payload(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {"code": 200, "data": {"date": "2026-10-26", "news": []}}
    return {"code": 200, "data": []}


def _payload(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200,
            "data": {"date": "2026-10-26", "news": ["一", "二", "三"]},
        }
    if path.endswith("/v2/zhihu"):
        return {
            "code": 200,
            "data": [
                {"title": "知乎一", "hot_value_desc": "10 万热度"},
                {"title": "知乎二", "hot_value_desc": "9 万热度"},
                {"title": "知乎三", "hot_value_desc": "8 万热度"},
            ],
        }
    if path.endswith("/v2/douyin"):
        return {
            "code": 200,
            "data": [
                {"title": "抖音一", "hot_value": 1},
                {"title": "抖音二", "hot_value": 2},
                {"title": "抖音三", "hot_value": 3},
            ],
        }
    if path.endswith("/v2/bili"):
        return {
            "code": 200,
            "data": [
                {"title": "B站一", "link": "https://www.bilibili.com/video/1"},
                {"title": "B站二"},
                {"title": "B站三"},
            ],
        }
    if path.endswith("/v2/toutiao"):
        return {
            "code": 200,
            "data": [
                {"title": "头条一", "hot_value": 11},
                {"title": "头条二", "hot_value": 12},
                {"title": "头条三", "hot_value": 13},
            ],
        }
    return {"code": 500, "data": None}
