from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.news import (
    NewsBusinessError,
    NewsInvalidJSONError,
    NewsItem,
    NewsStructureError,
    build_daily_news_url,
    parse_daily_news,
)
from core.news_service import NewsService
from core.sites import (
    SiteCreate,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
)

FETCHED_AT = datetime(2026, 10, 23, 8, 30, tzinfo=timezone.utc)


def payload(**overrides: object) -> dict:
    data = {
        "date": "2026-10-23",
        "news": ["第一条虚构新闻", "第二条虚构新闻"],
        "tip": "虚构微语",
        "link": "https://example.com/daily/2026-10-23",
    }
    data.update(overrides.pop("data", {}))
    base = {
        "code": 200,
        "message": "获取成功。",
        "data": data,
    }
    base.update(overrides)
    return base


def parse(data: dict, *, fetched_at: datetime = FETCHED_AT):
    return parse_daily_news(
        data,
        site_id="site-1",
        site_name="虚构站点",
        fetched_at=fetched_at,
    )


class DailyNewsParserTest(unittest.TestCase):
    def test_parses_string_and_object_items_in_order(self) -> None:
        snapshot = parse(
            payload(
                data={
                    "news": [
                        "字符串新闻",
                        {
                            "title": "对象新闻",
                            "link": "https://example.com/news/1",
                        },
                        "第三条新闻",
                    ],
                },
            ),
        )
        self.assertEqual(
            snapshot.items,
            (
                NewsItem("字符串新闻"),
                NewsItem(
                    "对象新闻",
                    "https://example.com/news/1",
                ),
                NewsItem("第三条新闻"),
            ),
        )
        self.assertEqual(snapshot.content_date, "2026-10-23")
        self.assertFalse(snapshot.date_is_unknown)
        self.assertFalse(snapshot.is_stale)
        self.assertEqual(snapshot.endpoint, "/v2/60s")
        self.assertEqual(snapshot.site_id, "site-1")
        self.assertEqual(snapshot.site_name, "虚构站点")
        self.assertEqual(snapshot.fetched_at, "2026-10-23T08:30:00+00:00")
        self.assertEqual(snapshot.tip, "虚构微语")
        self.assertEqual(
            snapshot.source_link,
            "https://example.com/daily/2026-10-23",
        )

    def test_does_not_hardcode_or_pad_item_count(self) -> None:
        for count in (0, 1, 3, 15, 17):
            with self.subTest(count=count):
                snapshot = parse(
                    payload(
                        data={
                            "news": [
                                f"虚构新闻 {index}"
                                for index in range(count)
                            ],
                        },
                    ),
                )
                self.assertEqual(len(snapshot.items), count)
                self.assertEqual(
                    [item.title for item in snapshot.items],
                    [f"虚构新闻 {index}" for index in range(count)],
                )

    def test_empty_news_is_valid(self) -> None:
        snapshot = parse(payload(data={"news": []}))
        self.assertEqual(snapshot.items, ())
        self.assertEqual(snapshot.content_date, "2026-10-23")
        self.assertEqual(snapshot.warnings, ())

    def test_missing_date_stays_unknown(self) -> None:
        data = payload(data={"news": ["虚构新闻"]})
        del data["data"]["date"]
        snapshot = parse(data)
        self.assertIsNone(snapshot.content_date)
        self.assertTrue(snapshot.date_is_unknown)
        self.assertFalse(snapshot.is_stale)
        self.assertIn("上游未提供内容日期", snapshot.warnings[0])

    def test_invalid_date_stays_unknown(self) -> None:
        snapshot = parse(payload(data={"date": "2026/13/99"}))
        self.assertIsNone(snapshot.content_date)
        self.assertTrue(snapshot.date_is_unknown)
        self.assertIn("无法识别", snapshot.warnings[0])

    def test_slash_date_is_normalized(self) -> None:
        snapshot = parse(payload(data={"date": "2026/10/23"}))
        self.assertEqual(snapshot.content_date, "2026-10-23")
        self.assertFalse(snapshot.date_is_unknown)

    def test_old_content_is_flagged_but_preserved(self) -> None:
        snapshot = parse(
            payload(data={"date": "2026-10-21", "news": ["旧新闻"]}),
        )
        self.assertEqual(snapshot.content_date, "2026-10-21")
        self.assertTrue(snapshot.is_stale)
        self.assertIn("2026-10-21", snapshot.warnings[0])
        self.assertIn("未替换为今天", snapshot.warnings[0])
        self.assertNotEqual(snapshot.content_date, "2026-10-23")

    def test_optional_tip_and_bad_links_are_omitted(self) -> None:
        snapshot = parse(
            payload(
                data={
                    "tip": 123,
                    "link": "javascript:alert(1)",
                    "news": [
                        {"title": "新闻", "link": "javascript:alert(1)"},
                        {"title": "新闻二", "link": None},
                    ],
                },
            ),
        )
        self.assertIsNone(snapshot.tip)
        self.assertIsNone(snapshot.source_link)
        self.assertIsNone(snapshot.items[0].link)
        self.assertIsNone(snapshot.items[1].link)

    def test_business_error(self) -> None:
        with self.assertRaises(NewsBusinessError) as context:
            parse({"code": 500, "message": "上游业务失败", "data": None})
        self.assertEqual(context.exception.code, 500)
        self.assertNotIn("上游业务失败", str(context.exception))

    def test_required_news_and_item_errors(self) -> None:
        missing_news = payload()
        del missing_news["data"]["news"]
        cases = [
            missing_news,
            payload(data={"news": "not-a-list"}),
            payload(data={"news": [123]}),
            payload(data={"news": [""]}),
            payload(data={"news": [{"title": ""}]}),
            payload(data={"news": [{"link": "https://example.com"}]}),
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(NewsStructureError):
                    parse(case)

    def test_envelope_type_errors(self) -> None:
        for data in (
            [],
            {"message": "no code", "data": {}},
            {"code": "200", "data": {}},
            {"code": True, "data": {}},
            {"code": 200, "data": []},
        ):
            with self.subTest(data=data):
                with self.assertRaises(NewsStructureError):
                    parse(data)

    def test_fetched_at_naive_is_interpreted_as_utc(self) -> None:
        snapshot = parse(
            payload(),
            fetched_at=datetime(2026, 10, 23, 8, 30),
        )
        self.assertEqual(snapshot.fetched_at, "2026-10-23T08:30:00+00:00")


class DailyNewsUrlTest(unittest.TestCase):
    def test_reverse_proxy_prefix_is_preserved(self) -> None:
        self.assertEqual(
            build_daily_news_url("http://10.0.0.5:8080/api/60s"),
            "http://10.0.0.5:8080/api/60s/v2/60s",
        )

    def test_ipv6_and_trailing_slash(self) -> None:
        self.assertEqual(
            build_daily_news_url("http://[::1]:8080/"),
            "http://[::1]:8080/v2/60s",
        )


class FakeNewsClient:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.calls: list[dict] = []
        self.opened = False
        self.closed = False

    @property
    def is_open(self) -> bool:
        return self.opened and not self.closed

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    async def fetch_json(self, url, *, bearer_token, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "bearer_token": bearer_token,
                "timeout_seconds": timeout_seconds,
            },
        )
        return self.body


class NewsServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _create(self, *, enabled: bool = True, token: str | None = None):
        return await self.sites.create_site(
            SiteCreate(
                name="虚构内网站点",
                base_url="http://[::1]:8080/proxy//",
                enabled=enabled,
                timeout_seconds=7,
                bearer_token=token,
            ),
        )

    def _client(self, body: bytes | None = None) -> FakeNewsClient:
        if body is None:
            body = json.dumps(
                payload(data={"news": ["服务层新闻"]}),
                ensure_ascii=False,
            ).encode()
        return FakeNewsClient(body)

    async def test_fetch_config_requires_enabled_site_and_hides_token(self) -> None:
        site = await self._create(token="sensitive-token")
        config = await self.sites.get_fetch_config(site.id)
        self.assertEqual(config.base_url, "http://[::1]:8080/proxy")
        self.assertEqual(config.bearer_token, "sensitive-token")
        self.assertNotIn("sensitive-token", repr(config))

        disabled = await self._create(enabled=False)
        with self.assertRaises(SiteDisabledError):
            await self.sites.get_fetch_config(disabled.id)
        with self.assertRaises(SiteNotFoundError):
            await self.sites.get_fetch_config("a" * 32)

    async def test_preview_uses_site_config_and_passes_token(self) -> None:
        site = await self._create(token="sensitive-token")
        client = self._client()
        service = NewsService(
            self.sites,
            client=client,
            clock=lambda: FETCHED_AT,
        )
        await service.open()
        snapshot = await service.preview(site.id)
        self.assertEqual(
            client.calls,
            [
                {
                    "url": "http://[::1]:8080/proxy/v2/60s",
                    "bearer_token": "sensitive-token",
                    "timeout_seconds": 7.0,
                },
            ],
        )
        self.assertEqual(snapshot.items, (NewsItem("服务层新闻"),))
        serialized = json.dumps(asdict(snapshot), ensure_ascii=False)
        self.assertNotIn("sensitive-token", serialized)
        self.assertNotIn("sensitive-token", repr(snapshot))
        await service.close()
        self.assertTrue(client.closed)

    async def test_disabled_and_missing_sites_do_not_call_client(self) -> None:
        disabled = await self._create(enabled=False)
        client = self._client()
        service = NewsService(self.sites, client=client, clock=lambda: FETCHED_AT)
        await service.open()
        with self.assertRaises(SiteDisabledError):
            await service.preview(disabled.id)
        with self.assertRaises(SiteNotFoundError):
            await service.preview("a" * 32)
        self.assertEqual(client.calls, [])
        await service.close()

    async def test_invalid_json_is_classified(self) -> None:
        site = await self._create()
        client = self._client(b"not-json")
        service = NewsService(self.sites, client=client, clock=lambda: FETCHED_AT)
        await service.open()
        with self.assertRaises(NewsInvalidJSONError):
            await service.preview(site.id)
        await service.close()


if __name__ == "__main__":
    unittest.main()
