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
from core.zhihu import (
    ZhihuBusinessError,
    ZhihuHotItem,
    ZhihuHotSnapshot,
    ZhihuInvalidJSONError,
    ZhihuStructureError,
    build_zhihu_hot_url,
    parse_zhihu_hot,
)
from core.zhihu_service import ZhihuHotService

FETCHED_AT = datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)


def payload(items, *, code=200):
    return {"code": code, "message": "ok", "data": items}


class ZhihuParserTest(unittest.TestCase):
    def parse(self, items):
        return parse_zhihu_hot(
            payload(items),
            site_id="site-1",
            site_name="测试站点",
            fetched_at=FETCHED_AT,
        )

    def test_url_keeps_prefix_and_normalizes_trailing_slash(self) -> None:
        self.assertEqual(
            build_zhihu_hot_url("http://example.test/proxy/"),
            "http://example.test/proxy/v2/zhihu",
        )
        self.assertEqual(
            build_zhihu_hot_url("http://[::1]:8080/base"),
            "http://[::1]:8080/base/v2/zhihu",
        )

    def test_normal_items_keep_real_fields_and_order(self) -> None:
        snapshot = self.parse(
            [
                {
                    "title": "  第一条话题  ",
                    "detail": "  第一条摘要  ",
                    "hot_value_desc": "1670 万热度",
                    "link": "https://www.zhihu.com/question/1",
                },
                {
                    "title": "第二条话题",
                    "detail": "",
                    "hot_value_desc": "88 万热度",
                    "link": "http://[::1]:8080/question",
                },
                {"title": "第三条话题"},
            ],
        )
        self.assertIsInstance(snapshot, ZhihuHotSnapshot)
        self.assertEqual(snapshot.site_id, "site-1")
        self.assertEqual(snapshot.site_name, "测试站点")
        self.assertEqual(snapshot.endpoint, "/v2/zhihu")
        self.assertEqual(snapshot.fetched_at, "2026-10-24T04:00:00+00:00")
        self.assertEqual(
            [item.title for item in snapshot.items],
            ["第一条话题", "第二条话题", "第三条话题"],
        )
        self.assertEqual(snapshot.items[0].detail, "第一条摘要")
        self.assertEqual(snapshot.items[0].hot_value_desc, "1670 万热度")
        self.assertEqual(
            snapshot.items[0].link,
            "https://www.zhihu.com/question/1",
        )
        self.assertIsNone(snapshot.items[1].detail)
        self.assertEqual(snapshot.items[1].hot_value_desc, "88 万热度")
        self.assertEqual(
            snapshot.items[1].link,
            "http://[::1]:8080/question",
        )
        self.assertIsNone(snapshot.items[2].detail)
        self.assertIsNone(snapshot.items[2].hot_value_desc)
        self.assertIsNone(snapshot.items[2].link)

    def test_empty_list_is_valid(self) -> None:
        snapshot = self.parse([])
        self.assertEqual(snapshot.items, ())

    def test_order_and_count_are_not_capped_or_padded(self) -> None:
        snapshot = self.parse(
            [{"title": f"话题 {index}"} for index in range(17)],
        )
        self.assertEqual(len(snapshot.items), 17)
        self.assertEqual(snapshot.items[0].title, "话题 0")
        self.assertEqual(snapshot.items[-1].title, "话题 16")

    def test_optional_fields_and_malformed_links_are_ignored(self) -> None:
        snapshot = self.parse(
            [
                {"title": "无摘要", "detail": None, "hot_value_desc": None},
                {"title": "非字符串", "detail": 1, "hot_value_desc": ["热"]},
                {"title": "坏链接", "link": "javascript:alert(1)"},
                {"title": "未闭合方括号", "link": "http://[::1"},
                {"title": "Unicode 主机", "link": "http://a\u2100b.example"},
                {"title": "合法链接", "link": " https://www.zhihu.com/question/9 "},
            ],
        )
        self.assertIsNone(snapshot.items[0].detail)
        self.assertIsNone(snapshot.items[0].hot_value_desc)
        self.assertIsNone(snapshot.items[1].detail)
        self.assertIsNone(snapshot.items[1].hot_value_desc)
        self.assertIsNone(snapshot.items[2].link)
        self.assertIsNone(snapshot.items[3].link)
        self.assertIsNone(snapshot.items[4].link)
        self.assertEqual(
            snapshot.items[5].link,
            "https://www.zhihu.com/question/9",
        )

    def test_no_rank_heat_number_or_update_fields_are_fabricated(self) -> None:
        snapshot = self.parse(
            [{"title": "话题", "hot_value_desc": "1670 万热度"}],
        )
        item_fields = {field.name for field in dataclasses.fields(ZhihuHotItem)}
        snapshot_fields = {
            field.name for field in dataclasses.fields(ZhihuHotSnapshot)
        }
        self.assertEqual(
            item_fields,
            {"title", "detail", "hot_value_desc", "link"},
        )
        self.assertEqual(
            snapshot_fields,
            {"site_id", "site_name", "endpoint", "fetched_at", "items"},
        )
        serialized = repr(snapshot).lower()
        for forbidden in ("rank", "ranking", "heat_value", "updated_at"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(snapshot.items[0].hot_value_desc, "1670 万热度")

    def test_business_error_does_not_echo_upstream_message(self) -> None:
        value = payload([], code=503)
        value["message"] = "Bearer fictional-zhihu-token"
        with self.assertRaises(ZhihuBusinessError) as context:
            parse_zhihu_hot(
                value,
                site_id="site-1",
                site_name="测试站点",
                fetched_at=FETCHED_AT,
            )
        self.assertEqual(context.exception.code, 503)
        self.assertNotIn("fictional-zhihu-token", str(context.exception))

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
                with self.assertRaises(ZhihuStructureError) as context:
                    parse_zhihu_hot(
                        value,
                        site_id="site-1",
                        site_name="测试站点",
                        fetched_at=FETCHED_AT,
                    )
                self.assertIn(reason, str(context.exception))


class ZhihuServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _site(self, *, enabled=True, token="fictional-zhihu-token"):
        return await self.sites.create_site(
            SiteCreate(
                name="知乎测试站点",
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
                    payload(
                        [{"title": "知乎话题", "hot_value_desc": "100 万热度"}],
                    ),
                    ensure_ascii=False,
                ).encode(),
            )

        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        service = ZhihuHotService(self.sites, client=client)
        await service.open()
        try:
            snapshot = await service.preview(site.id)
        finally:
            await service.close()
        self.assertEqual(seen["url"], "http://example.test/proxy/v2/zhihu")
        self.assertEqual(seen["auth"], "Bearer fictional-zhihu-token")
        self.assertEqual(snapshot.items[0].title, "知乎话题")
        self.assertEqual(snapshot.items[0].hot_value_desc, "100 万热度")
        self.assertNotIn("fictional-zhihu-token", repr(snapshot))

    async def test_disabled_and_missing_sites_are_rejected(self) -> None:
        disabled = await self._site(enabled=False)
        service = ZhihuHotService(
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
        service = ZhihuHotService(self.sites, client=client)
        await service.open()
        try:
            with self.assertRaises(ZhihuInvalidJSONError):
                await service.preview(site.id)
        finally:
            await service.close()
