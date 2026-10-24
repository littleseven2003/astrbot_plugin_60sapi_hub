from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.bili import (
    BiliBusinessError,
    BiliHotItem,
    BiliHotSnapshot,
    BiliInvalidJSONError,
    BiliStructureError,
    build_bili_hot_url,
    parse_bili_hot,
)
from core.bili_service import BiliHotService
from core.content_client import ContentHttpClient
from core.sites import (
    SiteCreate,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
)


def payload(items, *, code=200):
    return {"code": code, "message": "ok", "data": items}


FETCHED_AT = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)


class BiliParserTest(unittest.TestCase):
    def parse(self, items):
        return parse_bili_hot(
            payload(items),
            site_id="site-1",
            site_name="测试站点",
            fetched_at=FETCHED_AT,
        )

    def test_url_keeps_prefix_and_normalizes_trailing_slash(self) -> None:
        self.assertEqual(
            build_bili_hot_url("http://example.test/proxy/"),
            "http://example.test/proxy/v2/bili",
        )
        self.assertEqual(
            build_bili_hot_url("http://[::1]:8080/base"),
            "http://[::1]:8080/base/v2/bili",
        )

    def test_normal_items_and_actual_count(self) -> None:
        snapshot = self.parse(
            [
                {"title": "  第一条  ", "link": "https://search.example/one"},
                {"title": "第二条", "link": "http://example.test/two"},
                {"title": "第三条", "link": None},
            ],
        )
        self.assertIsInstance(snapshot, BiliHotSnapshot)
        self.assertEqual(snapshot.site_id, "site-1")
        self.assertEqual(snapshot.site_name, "测试站点")
        self.assertEqual(snapshot.endpoint, "/v2/bili")
        self.assertEqual(snapshot.fetched_at, "2026-10-24T02:00:00+00:00")
        self.assertEqual(
            [item.title for item in snapshot.items],
            ["第一条", "第二条", "第三条"],
        )
        self.assertEqual(snapshot.items[0].link, "https://search.example/one")
        self.assertEqual(snapshot.items[1].link, "http://example.test/two")
        self.assertIsNone(snapshot.items[2].link)

    def test_empty_list_is_valid(self) -> None:
        snapshot = self.parse([])
        self.assertEqual(snapshot.items, ())

    def test_order_and_count_are_not_capped_or_padded(self) -> None:
        snapshot = self.parse(
            [{"title": f"条目 {index}"} for index in range(17)],
        )
        self.assertEqual(len(snapshot.items), 17)
        self.assertEqual(snapshot.items[0].title, "条目 0")
        self.assertEqual(snapshot.items[-1].title, "条目 16")

    def test_optional_fields_and_malformed_links_are_ignored(self) -> None:
        snapshot = self.parse(
            [
                {"title": "无链接"},
                {"title": "坏链接", "link": "javascript:alert(1)"},
                {"title": "相对链接", "link": "/search?q=1"},
                {"title": "控制字符", "link": "https://example.test/\x00"},
                {"title": "非字符串", "link": 42},
                {"title": "合法链接", "link": " https://example.test/ok "},
            ],
        )
        self.assertIsNone(snapshot.items[0].link)
        self.assertIsNone(snapshot.items[1].link)
        self.assertIsNone(snapshot.items[2].link)
        self.assertIsNone(snapshot.items[3].link)
        self.assertIsNone(snapshot.items[4].link)
        self.assertEqual(snapshot.items[5].link, "https://example.test/ok")

    def test_malformed_links_keep_title_order_and_count(self) -> None:
        snapshot = self.parse(
            [
                {"title": "未闭合方括号", "link": "http://[::1"},
                {"title": "Unicode 主机", "link": "http://a\u2100b.example"},
                {
                    "title": "合法 IPv6",
                    "link": "http://[::1]:8080/hot",
                },
            ],
        )
        self.assertEqual(
            [item.title for item in snapshot.items],
            ["未闭合方括号", "Unicode 主机", "合法 IPv6"],
        )
        self.assertEqual(len(snapshot.items), 3)
        self.assertIsNone(snapshot.items[0].link)
        self.assertIsNone(snapshot.items[1].link)
        self.assertEqual(
            snapshot.items[2].link,
            "http://[::1]:8080/hot",
        )

    def test_no_rank_heat_or_update_fields_are_fabricated(self) -> None:
        snapshot = self.parse(
            [{"title": "条目", "link": "https://example.test/a"}],
        )
        item_fields = {field.name for field in dataclasses.fields(BiliHotItem)}
        snapshot_fields = {
            field.name for field in dataclasses.fields(BiliHotSnapshot)
        }
        self.assertEqual(item_fields, {"title", "link"})
        self.assertEqual(
            snapshot_fields,
            {"site_id", "site_name", "endpoint", "fetched_at", "items"},
        )
        serialized = repr(snapshot).lower()
        for forbidden in ("rank", "heat", "hot_score", "updated_at"):
            self.assertNotIn(forbidden, serialized)

    def test_business_error_does_not_echo_upstream_message(self) -> None:
        payload_with_secret = payload([], code=503)
        payload_with_secret["message"] = "Bearer fictional-token"
        with self.assertRaises(BiliBusinessError) as context:
            parse_bili_hot(
                payload_with_secret,
                site_id="site-1",
                site_name="测试站点",
                fetched_at=FETCHED_AT,
            )
        self.assertEqual(context.exception.code, 503)
        self.assertNotIn("fictional-token", str(context.exception))

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
                with self.assertRaises(BiliStructureError) as context:
                    parse_bili_hot(
                        value,
                        site_id="site-1",
                        site_name="测试站点",
                        fetched_at=FETCHED_AT,
                    )
                self.assertIn(reason, str(context.exception))


class BiliServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _site(self, *, enabled=True, token="fictional-bili-token"):
        return await self.sites.create_site(
            SiteCreate(
                name="Bilibili 测试站点",
                base_url="http://example.test/proxy",
                enabled=enabled,
                timeout_seconds=3,
                bearer_token=token,
            ),
        )

    async def test_service_joins_url_and_forwards_token_timeout(self) -> None:
        site = await self._site()
        seen = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["timeout"] = request.extensions.get("timeout")
            return httpx.Response(
                200,
                content=json.dumps(
                    payload([{"title": "热搜", "link": "https://example.test/s"}]),
                    ensure_ascii=False,
                ).encode(),
            )

        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        service = BiliHotService(self.sites, client=client)
        await service.open()
        try:
            snapshot = await service.preview(site.id)
        finally:
            await service.close()
        self.assertEqual(seen["url"], "http://example.test/proxy/v2/bili")
        self.assertEqual(seen["auth"], "Bearer fictional-bili-token")
        self.assertEqual(snapshot.items[0].title, "热搜")
        self.assertEqual(snapshot.items[0].link, "https://example.test/s")
        self.assertNotIn("fictional-bili-token", repr(snapshot))

    async def test_disabled_and_missing_sites_are_rejected(self) -> None:
        disabled = await self._site(enabled=False)
        service = BiliHotService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(500),
            )),
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
        service = BiliHotService(self.sites, client=client)
        await service.open()
        try:
            with self.assertRaises(BiliInvalidJSONError):
                await service.preview(site.id)
        finally:
            await service.close()
