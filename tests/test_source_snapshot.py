from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.content_api import content_snapshot_dict
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.profile_render import render_profile_markdown, render_profile_text
from core.sites import MAX_BASE_URL_LENGTH, SiteCreate, SiteService
from core.source_url import (
    MAX_SNAPSHOT_URL_LENGTH,
    SOURCE_SELF_BUILT_TEXT,
    source_display_text,
    snapshot_source_url,
)

SECRET = "simulated-secret-abc123"
PREFIX = "https://api.example.test:8443/reverse-proxy/"
LONG_BASE = PREFIX + "p" * (MAX_BASE_URL_LENGTH - len(PREFIX))

ENDPOINTS = {
    "daily_news": "/v2/60s",
    "bilibili_hot": "/v2/bili",
    "zhihu_hot": "/v2/zhihu",
    "douyin_hot": "/v2/douyin",
    "toutiao_hot": "/v2/toutiao",
}


def _body(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200, "message": "ok",
            "data": {"date": "2026-10-24", "news": ["新闻一"]},
        }
    if path.endswith("/v2/bili"):
        return {"code": 200, "message": "ok", "data": [{"title": "热搜"}]}
    if path.endswith("/v2/zhihu"):
        return {
            "code": 200, "message": "ok",
            "data": [{"title": "知乎", "hot_value_desc": "10 万热度"}],
        }
    if path.endswith("/v2/douyin"):
        return {"code": 200, "message": "ok", "data": [{"title": "抖音", "hot_value": 1}]}
    return {"code": 200, "message": "ok", "data": [{"title": "头条", "hot_value": 2}]}


class SnapshotSourceUrlTest(unittest.TestCase):
    """快照地址必须能完整表达本次请求的端口、路径与 endpoint。"""

    def test_keeps_port_and_path(self) -> None:
        for url in (
            "http://example.test:8443/proxy/v2/60s",
            "http://[::1]:8080/proxy/v2/bili",
            "https://example.test/p/v2/zhihu",
        ):
            with self.subTest(url=url):
                self.assertEqual(snapshot_source_url(url), url)

    def test_long_base_url_is_not_shortened(self) -> None:
        url = LONG_BASE + "/v2/zhihu"
        self.assertGreater(len(url), 2048)
        snapshot = snapshot_source_url(url)
        self.assertEqual(snapshot, url)
        self.assertIn(":8443", snapshot)
        self.assertTrue(snapshot.endswith("/v2/zhihu"))
        self.assertLessEqual(len(snapshot), MAX_SNAPSHOT_URL_LENGTH)

    def test_special_path_matches_actual_request_target(self) -> None:
        # 路径里的特殊字符按 HTTP 客户端的编码写入快照，与实际请求一致。
        for raw_path in ("/a b", "/a<b>", "/中文", "/a{b}", '/a"b'):
            url = "http://example.test:8443" + raw_path + "/v2/60s"
            with self.subTest(path=raw_path):
                expected = str(httpx.URL(url))
                self.assertEqual(snapshot_source_url(url), expected)
                self.assertIn(httpx.URL(url).raw_path.decode(), expected)

    def test_userinfo_query_and_fragment_stay_out(self) -> None:
        url = "https://user:pass@example.test:8443/p/v2/60s?token=x#frag"
        self.assertEqual(
            snapshot_source_url(url), "https://example.test:8443/p/v2/60s",
        )

    def test_unrepresentable_values_return_none_instead_of_shortening(self) -> None:
        for value in (
            None,
            "",
            "ftp://example.test/v2/60s",
            "http://example.test/" + "a" * MAX_SNAPSHOT_URL_LENGTH,
            "http://example.test/a\\b/v2/60s",
            "http://example.test/v2/60s\ninjected",
        ):
            with self.subTest(value=value):
                self.assertIsNone(snapshot_source_url(value))


class FiveEndpointSnapshotTest(unittest.IsolatedAsyncioTestCase):
    """五类接口在模拟上游下验证实际请求、快照与后台投影的对应关系。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "sites.sqlite3")
        await self.sites.open()
        self.seen: list[str] = []

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._tmp.cleanup()

    async def _run_all(self, base_url: str, source_kind: str) -> dict:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.seen.append(str(request.url))
            body = _body(request.url.path)
            return httpx.Response(200, content=json.dumps(body).encode())

        site = await self.sites.create_site(
            SiteCreate(
                name="来源快照站点",
                base_url=base_url,
                source_kind=source_kind,
                enabled=True,
                timeout_seconds=5,
            ),
        )
        client = ContentHttpClient(transport=httpx.MockTransport(handler))
        content = ContentService(self.sites, client=client)
        await content.open()
        try:
            snapshots = {
                "daily_news": await content.preview_daily(site.id),
                "bilibili_hot": await content.preview_bili(site.id),
                "zhihu_hot": await content.preview_zhihu(site.id),
                "douyin_hot": await content.preview_douyin(site.id),
                "toutiao_hot": await content.preview_toutiao(site.id),
            }
        finally:
            await content.close()
        return snapshots

    async def test_long_public_base_keeps_request_address(self) -> None:
        snapshots = await self._run_all(LONG_BASE, "public")
        self.assertEqual(len(self.seen), 5)
        for content_type, endpoint in ENDPOINTS.items():
            with self.subTest(content_type=content_type):
                snapshot = snapshots[content_type]
                # 上游实际收到的请求地址里，必有一条与本快照完全一致。
                requested = [
                    url for url in self.seen
                    if httpx.URL(url).path.endswith(endpoint)
                ]
                self.assertEqual(len(requested), 1, self.seen)
                self.assertEqual(snapshot.source_url, requested[0])
                self.assertIn(":8443", snapshot.source_url)
                self.assertIn("/reverse-proxy/", snapshot.source_url)
                self.assertGreater(len(snapshot.source_url), 2048)
                projection = content_snapshot_dict(content_type, snapshot)
                # 后台投影保留完整地址，对外只显示域名。
                self.assertEqual(projection["source_url"], snapshot.source_url)
                self.assertEqual(projection["source_display"], "api.example.test")
                self.assertNotIn(SECRET, projection["source_display"])

    async def test_public_and_self_built_display(self) -> None:
        public = await self._run_all(LONG_BASE, "public")
        self.assertEqual(
            source_display_text(public["daily_news"].source_url, "public"),
            "api.example.test",
        )
        built = await self._run_all(LONG_BASE, "self_built")
        self.assertEqual(
            source_display_text(built["daily_news"].source_url, "self_built"),
            SOURCE_SELF_BUILT_TEXT,
        )


class ChatOutputSourceTest(unittest.TestCase):
    """聊天文字出口只显示域名或自建标签，路径秘密不出现。"""

    def _payload(self, source_kind: str) -> dict:
        return {
            "profile_name": "组合",
            "site_name": "站点",
            "status": "complete",
            "success_count": 1,
            "failure_count": 0,
            "sections": [
                {
                    "content_type": "daily_news",
                    "label": "每日新闻",
                    "status": "ok",
                    "fetched_at": "2026-10-24T02:00:00+00:00",
                    "source_url": "https://api.example.test:8443/"
                    + SECRET + "/v2/60s",
                    "source_kind": source_kind,
                    "items": [{"title": "新闻一"}],
                },
            ],
        }

    def test_public_chat_text_shows_domain_only(self) -> None:
        text = render_profile_text(self._payload("public"))
        markdown = render_profile_markdown(self._payload("public"))
        self.assertIn("数据来源：api.example.test", text)
        self.assertIn("数据来源：api.example.test", markdown)
        for output in (text, markdown):
            self.assertNotIn(SECRET, output)
            self.assertNotIn(":8443", output)
            self.assertNotIn("/v2/60s", output)

    def test_self_built_chat_text_hides_address(self) -> None:
        text = render_profile_text(self._payload("self_built"))
        markdown = render_profile_markdown(self._payload("self_built"))
        self.assertIn("数据来源：自建服务", text)
        self.assertIn("数据来源：自建服务", markdown)
        for output in (text, markdown):
            self.assertNotIn(SECRET, output)
            self.assertNotIn("api.example.test", output)


if __name__ == "__main__":
    unittest.main()
