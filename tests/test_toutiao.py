from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.sites import (
    SiteCreate,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
)
from core.toutiao import (
    ToutiaoBusinessError,
    ToutiaoHotItem,
    ToutiaoHotSnapshot,
    ToutiaoInvalidJSONError,
    ToutiaoStructureError,
    build_toutiao_hot_url,
    parse_toutiao_hot,
)
from core.toutiao_service import ToutiaoHotService

FETCHED_AT = datetime(2026, 10, 26, 1, 10, tzinfo=timezone.utc)


def payload(items, *, code=200):
    return {"code": code, "message": "ok", "data": items}


class ToutiaoParserTest(unittest.TestCase):
    def parse(self, items):
        return parse_toutiao_hot(
            payload(items),
            site_id="site-1",
            site_name="测试站点",
            fetched_at=FETCHED_AT,
        )

    def test_url_keeps_prefix_and_normalizes_trailing_slash(self) -> None:
        self.assertEqual(
            build_toutiao_hot_url("http://example.test/proxy/"),
            "http://example.test/proxy/v2/toutiao",
        )
        self.assertEqual(
            build_toutiao_hot_url("http://[::1]:8080/base"),
            "http://[::1]:8080/base/v2/toutiao",
        )

    def test_normal_items_keep_title_hot_value_and_order(self) -> None:
        snapshot = self.parse(
            [
                {
                    "title": "  第一条头条  ",
                    "hot_value": 112229604,
                    "link": "https://www.toutiao.com/trending/1",
                    "cover": "https://pic.example/one.webp",
                },
                {"title": "第二条头条", "hot_value": 0, "link": None},
                {"title": "第三条头条"},
            ],
        )
        self.assertIsInstance(snapshot, ToutiaoHotSnapshot)
        self.assertEqual(snapshot.site_id, "site-1")
        self.assertEqual(snapshot.site_name, "测试站点")
        self.assertEqual(snapshot.endpoint, "/v2/toutiao")
        self.assertEqual(snapshot.fetched_at, "2026-10-26T01:10:00+00:00")
        self.assertEqual(
            [item.title for item in snapshot.items],
            ["第一条头条", "第二条头条", "第三条头条"],
        )
        self.assertEqual(snapshot.items[0].hot_value, 112229604)
        self.assertEqual(
            snapshot.items[0].link,
            "https://www.toutiao.com/trending/1",
        )
        self.assertEqual(snapshot.items[1].hot_value, 0)
        self.assertIsNone(snapshot.items[1].link)
        self.assertIsNone(snapshot.items[2].hot_value)
        self.assertIsNone(snapshot.items[2].link)

    def test_empty_list_is_valid(self) -> None:
        snapshot = self.parse([])
        self.assertEqual(snapshot.items, ())

    def test_order_and_count_are_not_capped_or_padded(self) -> None:
        snapshot = self.parse(
            [
                {"title": f"头条 {index}", "hot_value": index}
                for index in range(50)
            ],
        )
        self.assertEqual(len(snapshot.items), 50)
        self.assertEqual(snapshot.items[0].title, "头条 0")
        self.assertEqual(snapshot.items[-1].title, "头条 49")
        self.assertEqual(snapshot.items[-1].hot_value, 49)

    def test_optional_heat_and_malformed_links_are_ignored(self) -> None:
        snapshot = self.parse(
            [
                {"title": "字符串热度", "hot_value": "1000"},
                {"title": "布尔热度", "hot_value": True},
                {"title": "负数热度", "hot_value": -1},
                {"title": "坏链接", "link": "javascript:alert(1)"},
                {"title": "未闭合方括号", "link": "http://[::1"},
                {"title": "Unicode 主机", "link": "http://a\u2100b.example"},
                {
                    "title": "合法 IPv6",
                    "link": "http://[::1]:8080/hot",
                },
            ],
        )
        for index in range(3):
            self.assertIsNone(snapshot.items[index].hot_value)
        for index in range(3, 6):
            self.assertIsNone(snapshot.items[index].link)
        self.assertEqual(
            snapshot.items[6].link,
            "http://[::1]:8080/hot",
        )
        self.assertEqual(len(snapshot.items), 7)

    def test_no_rank_or_extra_fields_are_fabricated(self) -> None:
        snapshot = self.parse(
            [{"title": "头条", "hot_value": 100, "label": "热"}],
        )
        item_fields = {field.name for field in dataclasses.fields(ToutiaoHotItem)}
        snapshot_fields = {
            field.name for field in dataclasses.fields(ToutiaoHotSnapshot)
        }
        self.assertEqual(item_fields, {"title", "hot_value", "link"})
        self.assertEqual(
            snapshot_fields,
            {"site_id", "site_name", "endpoint", "fetched_at", "items"},
        )
        serialized = repr(snapshot).lower()
        for forbidden in ("rank", "ranking", "updated_at", "label", "cover", "summary"):
            self.assertNotIn(forbidden, serialized)

    def test_business_error_does_not_echo_upstream_message(self) -> None:
        value = payload([], code=503)
        value["message"] = "Bearer fictional-toutiao-token"
        with self.assertRaises(ToutiaoBusinessError) as context:
            parse_toutiao_hot(
                value,
                site_id="site-1",
                site_name="测试站点",
                fetched_at=FETCHED_AT,
            )
        self.assertEqual(context.exception.code, 503)
        self.assertNotIn("fictional-toutiao-token", str(context.exception))

    def test_structure_errors(self) -> None:
        cases = [
            (None, "顶层"),
            ({}, "code"),
            ({"code": True, "data": []}, "code"),
            ({"code": 200}, "data"),
            ({"code": 200, "data": {}}, "data"),
            ({"code": 200, "data": [None]}, "对象"),
            ({"code": 200, "data": [{}]}, "title"),
            ({"code": 200, "data": [{"title": "  "}]}, "title"),
            ({"code": 200, "data": [{"title": 1}]}, "title"),
            ({"code": 200, "data": [{"link": "http://[::1"}]}, "title"),
        ]
        for value, reason in cases:
            with self.subTest(value=value):
                with self.assertRaises(ToutiaoStructureError) as context:
                    parse_toutiao_hot(
                        value,
                        site_id="site-1",
                        site_name="测试站点",
                        fetched_at=FETCHED_AT,
                    )
                self.assertIn(reason, str(context.exception))


class ToutiaoServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _site(self, *, enabled=True, token="fictional-toutiao-token"):
        return await self.sites.create_site(
            SiteCreate(
                name="头条测试站点",
                base_url="http://example.test/proxy",
                enabled=enabled,
                timeout_seconds=3,
                bearer_token=token,
            ),
        )

    async def test_service_joins_url_and_forwards_token(self) -> None:
        site = await self._site()
        seen = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200,
                content=json.dumps(
                    payload([{"title": "头条热搜", "hot_value": 100}]),
                    ensure_ascii=False,
                ).encode(),
            )

        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        service = ToutiaoHotService(self.sites, client=client)
        await service.open()
        try:
            snapshot = await service.preview(site.id)
        finally:
            await service.close()
        self.assertEqual(seen["url"], "http://example.test/proxy/v2/toutiao")
        self.assertEqual(seen["auth"], "Bearer fictional-toutiao-token")
        self.assertEqual(snapshot.items[0].title, "头条热搜")
        self.assertEqual(snapshot.items[0].hot_value, 100)
        self.assertNotIn("fictional-toutiao-token", repr(snapshot))

    async def test_disabled_and_missing_sites_are_rejected(self) -> None:
        disabled = await self._site(enabled=False)
        service = ToutiaoHotService(
            self.sites,
            client=ContentHttpClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(500),
                ),
            ),
        )
        await service.open()
        try:
            with self.assertRaises(SiteDisabledError):
                await service.preview(disabled.id)
            with self.assertRaises(SiteNotFoundError):
                await service.preview("missing-site-id")
        finally:
            await service.close()

    async def test_invalid_json_is_classified(self) -> None:
        site = await self._site()
        client = ContentHttpClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"<html>"),
            ),
        )
        service = ToutiaoHotService(self.sites, client=client)
        await service.open()
        try:
            with self.assertRaises(ToutiaoInvalidJSONError):
                await service.preview(site.id)
        finally:
            await service.close()
