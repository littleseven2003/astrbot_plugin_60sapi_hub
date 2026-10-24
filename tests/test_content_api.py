from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.bili import BiliHotItem, BiliHotSnapshot
from core.content_api import ContentApi
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.news import NewsItem, NewsSnapshot
from core.sites import SiteCreate, SiteService


class FakeContentService:
    def __init__(self, *, daily=None, bili=None, error: Exception | None = None) -> None:
        self.daily = daily
        self.bili = bili
        self.error = error
        self.calls: list[tuple[str, object]] = []

    async def preview_daily(self, site_id):
        self.calls.append(("daily", site_id))
        if self.error:
            raise self.error
        return self.daily

    async def preview_bili(self, site_id):
        self.calls.append(("bili", site_id))
        if self.error:
            raise self.error
        return self.bili


DAILY = NewsSnapshot(
    site_id="site-1",
    site_name="站点",
    endpoint="/v2/60s",
    fetched_at="2026-10-24T02:00:00+00:00",
    content_date="2026-10-24",
    date_is_unknown=False,
    is_stale=False,
    warnings=(),
    items=(NewsItem("新闻", "https://example.test/news"),),
)
BILI = BiliHotSnapshot(
    site_id="site-1",
    site_name="站点",
    endpoint="/v2/bili",
    fetched_at="2026-10-24T02:00:00+00:00",
    items=(
        BiliHotItem("热搜一", "https://search.example/one"),
        BiliHotItem("热搜二", None),
    ),
)


class ContentApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_content_type_is_rejected_without_service_call(self) -> None:
        service = FakeContentService(daily=DAILY, bili=BILI)
        result = await ContentApi(service).preview(
            {"site_id": "site-1", "content_type": "other"},
        )
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.body["data"]["code"], "unknown_content_type")
        self.assertEqual(service.calls, [])

    async def test_missing_or_non_string_content_type_is_rejected(self) -> None:
        for payload in ({"site_id": "site-1"}, {"site_id": "site-1", "content_type": 1}):
            with self.subTest(payload=payload):
                service = FakeContentService(daily=DAILY, bili=BILI)
                result = await ContentApi(service).preview(payload)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.body["data"]["code"], "unknown_content_type")
                self.assertEqual(service.calls, [])

    async def test_daily_preview_keeps_existing_daily_semantics(self) -> None:
        result = await ContentApi(
            FakeContentService(daily=DAILY, bili=BILI),
        ).preview({"site_id": "site-1", "content_type": "daily_news"})
        self.assertEqual(result.status_code, 200)
        data = result.body["data"]
        self.assertEqual(data["content_type"], "daily_news")
        self.assertEqual(data["content_date"], "2026-10-24")
        self.assertEqual(data["items"][0]["title"], "新闻")

    async def test_bili_preview_has_order_and_no_fabricated_metrics(self) -> None:
        result = await ContentApi(
            FakeContentService(daily=DAILY, bili=BILI),
        ).preview({"site_id": "site-1", "content_type": "bilibili_hot"})
        self.assertEqual(result.status_code, 200)
        data = result.body["data"]
        self.assertEqual(data["content_type"], "bilibili_hot")
        self.assertEqual(data["endpoint"], "/v2/bili")
        self.assertEqual(data["item_count"], 2)
        self.assertEqual(
            [item["title"] for item in data["items"]],
            ["热搜一", "热搜二"],
        )
        self.assertNotIn("content_date", data)
        serialized = json.dumps(data, ensure_ascii=False).lower()
        for key in ("rank", "ranking", "heat", "hot_score", "updated_at", "update_time"):
            self.assertNotIn(key, serialized)

    async def test_invalid_body_is_rejected(self) -> None:
        result = await ContentApi(FakeContentService()).preview([])
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.body["data"]["code"], "invalid_body")


class ContentApiSecretLeakTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.token = "fictional-bili-bearer-token"
        self.site = await self.sites.create_site(
            SiteCreate(
                name="B 站泄露测试",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=2,
                bearer_token=self.token,
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _preview(self, mode: str):
        seen_auth: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("authorization", "")
            seen_auth.append(auth)
            body = json.dumps(
                {
                    "code": 500,
                    "message": f"upstream echoed {auth}",
                    "data": None,
                },
                ensure_ascii=False,
            ).encode()
            if mode == "business":
                return httpx.Response(200, content=body)
            return httpx.Response(500, content=body)

        client = ContentHttpClient(
            transport=httpx.MockTransport(handler),
            retry_delay_seconds=0,
        )
        content = ContentService(
            self.sites,
            client=client,
            clock=lambda: datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc),
        )
        await content.open()
        try:
            result = await ContentApi(content).preview(
                {"site_id": self.site.id, "content_type": "bilibili_hot"},
            )
        finally:
            await content.close()
        self.assertEqual(seen_auth, [f"Bearer {self.token}"])
        return result

    async def test_http_error_does_not_echo_bili_token(self) -> None:
        result = await self._preview("http")
        self.assertEqual(result.status_code, 502)
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("upstream echoed", serialized)

    async def test_business_error_does_not_echo_bili_token(self) -> None:
        result = await self._preview("business")
        self.assertEqual(result.status_code, 502)
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("upstream echoed", serialized)


if __name__ == "__main__":
    unittest.main()
