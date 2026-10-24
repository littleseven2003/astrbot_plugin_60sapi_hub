from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from core.news import (
    NewsBusinessError,
    NewsClientClosedError,
    NewsHTTPStatusError,
    NewsInvalidJSONError,
    NewsItem,
    NewsRedirectError,
    NewsSnapshot,
    NewsStructureError,
    NewsTimeoutError,
    NewsTooLargeError,
    NewsTransportError,
)
from core.news_api import NewsApi
from core.news_client import NewsHttpClient
from core.news_service import NewsService
from core.sites import (
    SiteCreate,
    SiteDisabledError,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
    SiteValidationError,
)


class FakeNewsService:
    def __init__(self, *, snapshot=None, error: Exception | None = None) -> None:
        self.snapshot = snapshot
        self.error = error
        self.calls: list[object] = []

    async def preview(self, site_id):
        self.calls.append(site_id)
        if self.error is not None:
            raise self.error
        return self.snapshot


def make_snapshot() -> NewsSnapshot:
    return NewsSnapshot(
        site_id="site-1",
        site_name="虚构站点",
        endpoint="/v2/60s",
        fetched_at="2026-10-23T08:30:00+00:00",
        content_date="2026-10-22",
        date_is_unknown=False,
        is_stale=True,
        warnings=("旧内容提示",),
        items=(
            NewsItem("第一条", "https://example.com/1"),
            NewsItem("第二条"),
        ),
        tip="虚构微语",
        source_link="https://example.com/daily",
    )


class NewsApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_no_secret_fields(self) -> None:
        service = FakeNewsService(snapshot=make_snapshot())
        api = NewsApi(service)
        result = await api.preview({"site_id": "site-1"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body["status"], "ok")
        self.assertEqual(service.calls, ["site-1"])
        data = result.body["data"]
        self.assertEqual(data["site_id"], "site-1")
        self.assertEqual(data["items"][0]["title"], "第一条")
        self.assertEqual(data["items"][0]["link"], "https://example.com/1")
        self.assertEqual(data["items"][1]["link"], None)
        self.assertTrue(data["is_stale"])
        self.assertEqual(data["warnings"], ["旧内容提示"])
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn("bearer", serialized.lower())
        self.assertNotIn("token", serialized.lower())

    async def test_invalid_body_is_rejected(self) -> None:
        service = FakeNewsService(snapshot=make_snapshot())
        api = NewsApi(service)
        for payload in (None, [], "site-1", 1):
            with self.subTest(payload=payload):
                result = await api.preview(payload)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(result.body["data"]["code"], "invalid_body")
        self.assertEqual(service.calls, [])

    async def test_site_validation_and_state_errors(self) -> None:
        cases = [
            (SiteValidationError("site_id", "必须是字符串"), 400, "validation_error"),
            (SiteNotFoundError("a" * 32), 404, "not_found"),
            (SiteDisabledError("a" * 32), 409, "site_disabled"),
            (SiteStorageError("closed"), 503, "storage_unavailable"),
        ]
        for error, status, code in cases:
            with self.subTest(error=error):
                api = NewsApi(FakeNewsService(error=error))
                result = await api.preview({"site_id": "a" * 32})
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.body["data"]["code"], code)
                self.assertEqual(result.body["status"], "error")

    async def test_upstream_error_mapping(self) -> None:
        cases = [
            (NewsTimeoutError("timeout"), 504, "upstream_timeout"),
            (NewsTooLargeError(1024), 502, "upstream_too_large"),
            (NewsInvalidJSONError("bad json"), 502, "upstream_invalid_json"),
            (NewsBusinessError(500), 502, "upstream_business_error"),
            (NewsStructureError("结构变化"), 502, "upstream_structure"),
            (NewsHTTPStatusError(500), 502, "upstream_http_error"),
            (NewsRedirectError(302), 502, "upstream_redirect"),
            (NewsTransportError("network"), 502, "upstream_unreachable"),
            (NewsClientClosedError("closed"), 503, "upstream_unavailable"),
        ]
        for error, status, code in cases:
            with self.subTest(error=error):
                api = NewsApi(FakeNewsService(error=error))
                result = await api.preview({"site_id": "site-1"})
                self.assertEqual(result.status_code, status)
                self.assertEqual(result.body["data"]["code"], code)
                self.assertEqual(result.body["status"], "error")

    async def test_unexpected_error_is_generic(self) -> None:
        api = NewsApi(FakeNewsService(error=RuntimeError("/secret/db")))
        result = await api.preview({"site_id": "site-1"})
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.body["data"]["code"], "internal_error")
        self.assertNotIn("/secret/db", json.dumps(result.body))


class NewsApiSecretLeakTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.token = "fictional-bearer-token-for-leak-test"
        self.site = await self.sites.create_site(
            SiteCreate(
                name="泄露测试站点",
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

        client = NewsHttpClient(
            transport=httpx.MockTransport(handler),
            retry_delay_seconds=0,
        )
        service = NewsService(self.sites, client=client)
        await service.open()
        try:
            result = await NewsApi(service).preview({"site_id": self.site.id})
        finally:
            await service.close()
        self.assertEqual(seen_auth, [f"Bearer {self.token}"])
        return result

    async def test_http_error_does_not_echo_bearer_token(self) -> None:
        result = await self._preview("http")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.body["data"]["code"], "upstream_http_error")
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("upstream echoed", serialized)

    async def test_business_error_does_not_echo_bearer_token(self) -> None:
        result = await self._preview("business")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.body["data"]["code"], "upstream_business_error")
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("upstream echoed", serialized)


if __name__ == "__main__":
    unittest.main()
