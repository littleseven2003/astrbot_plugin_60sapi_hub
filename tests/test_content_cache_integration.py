from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from core.content_api import ContentApi
from core.content_cache import ContentCache
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.sites import (
    CredentialAction,
    SiteCreate,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
    SiteUpdate,
)

CONTENT_TYPES = (
    "daily_news",
    "bilibili_hot",
    "zhihu_hot",
    "douyin_hot",
    "toutiao_hot",
)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall += timedelta(seconds=seconds)


def response_body(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200,
            "data": {"date": "2026-10-26", "news": ["新闻"]},
        }
    if path.endswith("/v2/bili"):
        return {"code": 200, "data": [{"title": "B站"}]}
    if path.endswith("/v2/zhihu"):
        return {
            "code": 200,
            "data": [{"title": "知乎", "hot_value_desc": "10 万热度"}],
        }
    if path.endswith("/v2/douyin"):
        return {"code": 200, "data": [{"title": "抖音", "hot_value": 10}]}
    if path.endswith("/v2/toutiao"):
        return {"code": 200, "data": [{"title": "头条", "hot_value": 20}]}
    return {"code": 500, "data": None}


class ContentCacheIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.clock = FakeClock()
        self.site = await self.sites.create_site(
            SiteCreate(
                name="缓存站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=3,
                bearer_token="cache-token",
            ),
        )
        self.calls: list[str] = []
        self.seen_auth: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block_first = False
        self.fail_requests = False

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            self.seen_auth.append(request.headers.get("authorization", ""))
            if self.block_first and len(self.calls) == 1:
                self.started.set()
                await self.release.wait()
            if self.fail_requests:
                return httpx.Response(500, content=b"error")
            return httpx.Response(
                200,
                content=json.dumps(response_body(request.url.path)).encode(),
            )

        self.client = ContentHttpClient(transport=httpx.MockTransport(handler))
        self.cache = ContentCache(
            ttl_seconds=300,
            max_entries=32,
            monotonic=self.clock.monotonic,
            wall_clock=self.clock.now,
            wait_timeout_seconds=2,
        )
        self.content = ContentService(
            self.sites,
            client=self.client,
            cache=self.cache,
            clock=self.clock.now,
        )
        await self.content.open()

    async def asyncTearDown(self) -> None:
        await self.content.close()
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def test_five_types_miss_hit_and_force_refresh(self) -> None:
        for content_type in CONTENT_TYPES:
            with self.subTest(content_type=content_type):
                first = await self.content.preview_cached(
                    content_type,
                    self.site.id,
                )
                second = await self.content.preview_cached(
                    content_type,
                    self.site.id,
                )
                self.assertFalse(first.hit)
                self.assertTrue(second.hit)
                self.assertEqual(
                    first.snapshot.fetched_at,
                    second.snapshot.fetched_at,
                )
                forced = await self.content.preview_cached(
                    content_type,
                    self.site.id,
                    force_refresh=True,
                )
                self.assertFalse(forced.hit)
                self.assertEqual(
                    forced.snapshot.fetched_at,
                    first.snapshot.fetched_at,
                )
        self.assertEqual(len(self.calls), 10)

    async def test_daily_crosses_china_date_and_refetches(self) -> None:
        # 23:59 in China is 15:59 UTC.
        self.clock.wall = datetime(2026, 10, 26, 15, 59, tzinfo=timezone.utc)
        first = await self.content.preview_cached("daily_news", self.site.id)
        second = await self.content.preview_cached("daily_news", self.site.id)
        self.assertTrue(second.hit)
        self.assertEqual(len(self.calls), 1)

        # 00:01 China: TTL has not expired, but yesterday's stale status is
        # no longer valid, so the cache entry must not be returned.
        self.clock.wall = datetime(2026, 10, 26, 16, 1, tzinfo=timezone.utc)
        third = await self.content.preview_cached("daily_news", self.site.id)
        self.assertFalse(third.hit)
        self.assertNotEqual(first.snapshot.fetched_at, third.snapshot.fetched_at)
        self.assertEqual(len(self.calls), 2)

    async def test_configuration_revision_isolates_cache(self) -> None:
        first = await self.content.preview_cached("daily_news", self.site.id)
        self.assertFalse(first.hit)
        updated = await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name="改名后的站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=3,
                expected_revision=1,
                credential_action=CredentialAction.KEEP,
            ),
        )
        second = await self.content.preview_cached("daily_news", self.site.id)
        self.assertFalse(second.hit)
        self.assertEqual(second.snapshot.site_name, "改名后的站点")
        self.assertEqual(len(self.calls), 2)

        # Setting a new token and address changes the revision again.
        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name="改名后的站点",
                base_url="http://example.test/new-proxy",
                enabled=True,
                timeout_seconds=3,
                expected_revision=updated.revision,
                credential_action=CredentialAction.SET,
                bearer_token="new-cache-token",
            ),
        )
        third = await self.content.preview_cached("daily_news", self.site.id)
        self.assertFalse(third.hit)
        self.assertEqual(self.seen_auth[-1], "Bearer new-cache-token")
        self.assertEqual(len(self.calls), 3)

    async def test_disabled_and_deleted_sites_never_use_cache(self) -> None:
        await self.content.preview_cached("bilibili_hot", self.site.id)
        disabled = await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name="缓存站点",
                base_url="http://example.test/proxy",
                enabled=False,
                timeout_seconds=3,
                expected_revision=1,
                credential_action=CredentialAction.KEEP,
            ),
        )
        with self.assertRaises(SiteDisabledError):
            await self.content.preview_cached("bilibili_hot", self.site.id)
        with self.assertRaises(SiteDisabledError):
            await self.content.preview_cached("daily_news", self.site.id)
        self.assertEqual(len(self.calls), 1)
        await self.sites.delete_site(self.site.id, disabled.revision)
        with self.assertRaises(SiteNotFoundError):
            await self.content.preview_cached("bilibili_hot", self.site.id)

    async def test_inflight_config_change_does_not_fill_new_revision(self) -> None:
        self.block_first = True
        first_task = asyncio.create_task(
            self.content.preview_cached("daily_news", self.site.id),
        )
        await asyncio.wait_for(self.started.wait(), timeout=1)
        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name="请求中改名",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=3,
                expected_revision=1,
                credential_action=CredentialAction.KEEP,
            ),
        )
        self.release.set()
        first = await first_task
        self.assertEqual(first.snapshot.site_name, "缓存站点")

        second = await self.content.preview_cached("daily_news", self.site.id)
        self.assertFalse(second.hit)
        self.assertEqual(second.snapshot.site_name, "请求中改名")
        self.assertEqual(len(self.calls), 2)

    async def test_failed_refresh_does_not_replace_valid_cache(self) -> None:
        api = ContentApi(self.content)
        first = await api.preview(
            {"site_id": self.site.id, "content_type": "bilibili_hot"},
        )
        self.assertEqual(first.status_code, 200)
        self.fail_requests = True
        failed = await api.preview(
            {
                "site_id": self.site.id,
                "content_type": "bilibili_hot",
                "force_refresh": True,
            },
        )
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(len(self.calls), 2)

        old = await api.preview(
            {"site_id": self.site.id, "content_type": "bilibili_hot"},
        )
        self.assertEqual(old.status_code, 200)
        self.assertTrue(old.body["data"]["cache"]["hit"])
        self.assertEqual(
            old.body["data"]["fetched_at"],
            first.body["data"]["fetched_at"],
        )
        self.assertEqual(len(self.calls), 2)

        self.fail_requests = False
        refreshed = await api.preview(
            {
                "site_id": self.site.id,
                "content_type": "bilibili_hot",
                "force_refresh": True,
            },
        )
        self.assertEqual(refreshed.status_code, 200)
        self.assertFalse(refreshed.body["data"]["cache"]["hit"])
        self.assertEqual(len(self.calls), 3)

    async def test_api_cache_metadata_and_strict_force_refresh(self) -> None:
        api = ContentApi(self.content)
        first = await api.preview(
            {"site_id": self.site.id, "content_type": "toutiao_hot"},
        )
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.body["data"]["cache"]["hit"])
        self.assertEqual(first.body["data"]["cache"]["ttl_seconds"], 300.0)
        self.assertIn("expires_at", first.body["data"]["cache"])

        second = await api.preview(
            {"site_id": self.site.id, "content_type": "toutiao_hot"},
        )
        self.assertTrue(second.body["data"]["cache"]["hit"])
        self.assertEqual(
            first.body["data"]["fetched_at"],
            second.body["data"]["fetched_at"],
        )

        forced = await api.preview(
            {
                "site_id": self.site.id,
                "content_type": "toutiao_hot",
                "force_refresh": True,
            },
        )
        self.assertFalse(forced.body["data"]["cache"]["hit"])

        before = len(self.calls)
        invalid = await api.preview(
            {
                "site_id": self.site.id,
                "content_type": "toutiao_hot",
                "force_refresh": "true",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.body["data"]["code"], "invalid_force_refresh")
        self.assertEqual(len(self.calls), before)


class FullChainCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.sites = SiteService(
            Path(self._temporary_directory.name) / "sites.sqlite3",
        )
        await self.sites.open()
        self.site = await self.sites.create_site(
            SiteCreate(
                name="取消链路站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=5,
            ),
        )
        self.first_started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests += 1
            if self.requests == 1:
                self.first_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cleanup_started.set()
                    await self.cleanup_release.wait()
                    raise
            return httpx.Response(
                200,
                content=json.dumps(response_body(request.url.path)).encode(),
            )

        self.client = ContentHttpClient(transport=httpx.MockTransport(handler))
        self.cache = ContentCache(
            ttl_seconds=300,
            max_entries=32,
            wait_timeout_seconds=2,
        )
        self.content = ContentService(
            self.sites,
            client=self.client,
            cache=self.cache,
        )
        await self.content.open()
        self.api = ContentApi(self.content)

    async def asyncTearDown(self) -> None:
        await self.content.close()
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def test_cancelled_api_request_does_not_poison_new_fetch(self) -> None:
        first = asyncio.create_task(
            self.api.preview(
                {"site_id": self.site.id, "content_type": "toutiao_hot"},
            ),
        )
        await asyncio.wait_for(self.first_started.wait(), timeout=1)
        old_cache_task = next(iter(self.cache._tasks))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(self.cleanup_started.wait(), timeout=1)

        second_result = await self.api.preview(
            {"site_id": self.site.id, "content_type": "toutiao_hot"},
        )
        self.assertEqual(second_result.status_code, 200)
        self.assertFalse(second_result.body["data"]["cache"]["hit"])
        self.assertEqual(
            second_result.body["data"]["items"][0]["title"],
            "头条",
        )
        self.assertEqual(self.requests, 2)

        # The old HTTP/cache cleanup may finish after the new result exists.
        # It must not overwrite the new cache or remove its record.
        self.cleanup_release.set()
        await asyncio.gather(old_cache_task, return_exceptions=True)
        hit = await self.api.preview(
            {"site_id": self.site.id, "content_type": "toutiao_hot"},
        )
        self.assertTrue(hit.body["data"]["cache"]["hit"])
        self.assertEqual(self.requests, 2)
        self.assertEqual(hit.body["data"]["items"][0]["title"], "头条")
