from __future__ import annotations

import json
import tempfile
import unittest
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
    snapshot_source_url,
    source_display_text,
)

SECRET = "simulated-secret-abc123"
EMOJI = "\U0001F600"
CJK = "\u4e2d\u6587"

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
            "data": {"date": "2026-11-11", "news": ["新闻一"]},
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


class _Harness(unittest.IsolatedAsyncioTestCase):
    """模拟上游记录 HTTP 客户端实际发出的请求地址。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "sites.sqlite3")
        await self.sites.open()
        self.seen: list[str] = []
        self._content = None

    async def asyncTearDown(self) -> None:
        if self._content is not None:
            await self._content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _content_service(self) -> ContentService:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.seen.append(str(request.url))
            return httpx.Response(
                200, content=json.dumps(_body(request.url.path)).encode(),
            )

        content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await content.open()
        self._content = content
        return content

    async def _site(self, base_url: str, source_kind: str = "public"):
        self._site_seq = getattr(self, "_site_seq", 0) + 1
        return await self.sites.create_site(
            SiteCreate(
                name=f"来源站点 {self._site_seq}",
                base_url=base_url,
                source_kind=source_kind,
                timeout_seconds=5,
            ),
        )


class SnapshotMatchesActualRequestTest(_Harness):
    """快照地址必须等于客户端本次实际使用的请求地址。"""

    async def test_encoding_and_host_variants_match_request(self) -> None:
        bases = [
            "http://example.test:8443/rev/" + SECRET,
            "http://[::1]:8080/rev",
            "http://example.test/" + CJK,
            "http://example.test/" + EMOJI,
            "http://example.test/a%20b",
            "http://example.test/a|b",
            'http://example.test/a<b>',
            "http://example.test/a%2Fb",
        ]
        content = await self._content_service()
        for base in bases:
            with self.subTest(base=base):
                site = await self._site(base)
                self.seen.clear()
                snapshot = await content.preview_daily(site.id)
                self.assertEqual(len(self.seen), 1)
                self.assertEqual(snapshot.source_url, self.seen[0])
                self.assertTrue(snapshot.source_url.endswith("/v2/60s"))

    async def test_dot_segments_and_ports_follow_client_rules(self) -> None:
        cases = {
            "http://example.test:8443/a/../b": ("/b/v2/60s", True),
            "http://example.test:8443/a/./b": ("/a/b/v2/60s", True),
            "http://example.test:80/a": ("/a/v2/60s", False),
            "https://example.test:443/a": ("/a/v2/60s", False),
        }
        content = await self._content_service()
        for base, (tail, keeps_port) in cases.items():
            with self.subTest(base=base):
                site = await self._site(base)
                snapshot = await content.preview_daily(site.id)
                self.assertTrue(snapshot.source_url.endswith(tail))
                self.assertEqual(":8443" in snapshot.source_url, keeps_port)
                self.assertNotIn(":80/", snapshot.source_url)
                self.assertNotIn(":443/", snapshot.source_url)


class FiveEndpointSecretTest(_Harness):
    """五类接口：请求、快照、后台投影与对外展示的关系。"""

    async def _run(self, source_kind: str):
        content = await self._content_service()
        site = await self._site(
            "https://api.example.test:8443/" + SECRET + "/reverse-proxy",
            source_kind=source_kind,
        )
        snapshots = {
            "daily_news": await content.preview_daily(site.id),
            "bilibili_hot": await content.preview_bili(site.id),
            "zhihu_hot": await content.preview_zhihu(site.id),
            "douyin_hot": await content.preview_douyin(site.id),
            "toutiao_hot": await content.preview_toutiao(site.id),
        }
        return snapshots

    async def test_public_endpoints_keep_full_address_but_show_domain(self) -> None:
        snapshots = await self._run("public")
        self.assertEqual(len(self.seen), 5)
        for content_type, endpoint in ENDPOINTS.items():
            with self.subTest(content_type=content_type):
                snapshot = snapshots[content_type]
                requested = [
                    url for url in self.seen
                    if httpx.URL(url).path.endswith(endpoint)
                ]
                self.assertEqual(len(requested), 1, self.seen)
                self.assertEqual(snapshot.source_url, requested[0])
                self.assertIn(SECRET, snapshot.source_url)
                self.assertIn(":8443", snapshot.source_url)
                projection = content_snapshot_dict(content_type, snapshot)
                # 后台投影保留管理员需要的完整地址。
                self.assertEqual(projection["source_url"], snapshot.source_url)
                # 对外展示只有域名，且不含路径秘密。
                self.assertEqual(projection["source_display"], "api.example.test")
                self.assertNotIn(SECRET, projection["source_display"])

    async def test_self_built_endpoints_show_label_only(self) -> None:
        snapshots = await self._run("self_built")
        for content_type in ENDPOINTS:
            with self.subTest(content_type=content_type):
                snapshot = snapshots[content_type]
                self.assertEqual(
                    source_display_text(snapshot.source_url, snapshot.source_kind),
                    SOURCE_SELF_BUILT_TEXT,
                )
                self.assertIn(SECRET, snapshot.source_url)


class UnicodeBudgetTest(_Harness):
    """合法 Unicode 长路径编码后仍要保留来源，不靠截断或只留主机。"""

    async def _snapshot_for(self, filler: str) -> object:
        content = await self._content_service()
        prefix = "http://h.test/"
        base = prefix + filler * (MAX_BASE_URL_LENGTH - len(prefix))
        self.assertEqual(len(base), MAX_BASE_URL_LENGTH)
        site = await self._site(base)
        return await content.preview_daily(site.id)

    async def test_worst_case_encodings_keep_source(self) -> None:
        for filler, name in (
            (EMOJI, "emoji"),
            (CJK[:1], "chinese"),
            ("a", "ascii"),
        ):
            with self.subTest(kind=name):
                snapshot = await self._snapshot_for(filler)
                self.assertIsNotNone(snapshot.source_url, name)
                url = snapshot.source_url
                self.assertTrue(url.endswith("/v2/60s"))
                self.assertLessEqual(len(url), MAX_SNAPSHOT_URL_LENGTH)
                self.assertEqual(source_display_text(url, "public"), "h.test")

    def test_snapshot_budget_covers_worst_case_growth(self) -> None:
        # 依据：一个字符最坏 4 字节 UTF-8，每字节编码成 %XX 共 3 字符。
        self.assertGreaterEqual(MAX_SNAPSHOT_URL_LENGTH, MAX_BASE_URL_LENGTH * 12)
        worst = "http://h.test/" + EMOJI * 100 + "/v2/60s"
        encoded = snapshot_source_url(worst)
        self.assertIsNotNone(encoded)
        # 编码确实膨胀，且与客户端使用的地址逐字一致。
        self.assertGreater(len(encoded), len(worst))
        self.assertEqual(encoded, str(httpx.URL(worst)))


class ProfileOutputSourceTest(unittest.TestCase):
    """组合文字与 Markdown 输出：只显示域名，路径秘密不出现。"""

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
                    "fetched_at": "2026-11-11T02:00:00+00:00",
                    "source_url": "https://api.example.test:8443/"
                    + SECRET + "/v2/60s",
                    "source_kind": source_kind,
                    "items": [{"title": "新闻一"}],
                },
            ],
        }

    def test_text_and_markdown_show_domain_only(self) -> None:
        payload = self._payload("public")
        text = render_profile_text(payload)
        markdown = render_profile_markdown(payload)
        for output in (text, markdown):
            self.assertIn("数据来源：api.example.test", output)
            self.assertNotIn(SECRET, output)
            self.assertNotIn(":8443", output)
            self.assertNotIn("/v2/60s", output)

    def test_text_and_markdown_hide_self_built_address(self) -> None:
        payload = self._payload("self_built")
        text = render_profile_text(payload)
        markdown = render_profile_markdown(payload)
        for output in (text, markdown):
            self.assertIn("数据来源：自建服务", output)
            self.assertNotIn(SECRET, output)
            self.assertNotIn("api.example.test", output)
