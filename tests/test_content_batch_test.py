"""批量站点连接测试的真实链路测试。

覆盖：实际鉴权 Web 处理器 + ContentApi + ContentService + 站点存储 + httpx
模拟上游；五类内容、显式选择停用站点、部分失败不影响其它结果、不发送消息、
不改配置、不写缓存，以及请求体与鉴权校验。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.content_api import BATCH_STALE_MESSAGE, ContentApi, ContentApiResult
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.sites import SiteCreate, SiteService, SiteUpdate

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEST_PACKAGE = "_content_batch_test_pkg"
FIVE_TYPES = (
    "daily_news", "bilibili_hot", "zhihu_hot", "douyin_hot", "toutiao_hot",
)


class FakeWebRequest:
    def __init__(self) -> None:
        self.username: str | None = "admin"
        self.payload: object = None

    async def json(self, default=None):
        return self.payload


class FakeWebResult:
    def __init__(self, body: dict, status_code: int) -> None:
        self.body = body
        self.status_code = status_code


def _fake_error_response(message, status_code=400, **_kwargs):
    return FakeWebResult({"status": "error", "message": message}, status_code)


def _fake_json_response(body, status_code=200, **_kwargs):
    return FakeWebResult(body, status_code)


@contextmanager
def _fake_astrbot_modules(request):
    names = ("astrbot", "astrbot.api", "astrbot.api.star", "astrbot.api.web")
    saved = {name: sys.modules.get(name) for name in names}
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    star = types.ModuleType("astrbot.api.star")
    web = types.ModuleType("astrbot.api.web")
    star.Context = type("Context", (), {})
    web.request = request
    web.error_response = _fake_error_response
    web.json_response = _fake_json_response
    api.star = star
    api.web = web
    astrbot.api = api
    sys.modules.update({
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.star": star,
        "astrbot.api.web": web,
    })
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _load_content_web(request):
    package = types.ModuleType(TEST_PACKAGE)
    package.__path__ = [str(PLUGIN_ROOT)]
    sys.modules[TEST_PACKAGE] = package
    with _fake_astrbot_modules(request):
        spec = importlib.util.spec_from_file_location(
            f"{TEST_PACKAGE}.content_web",
            PLUGIN_ROOT / "content_web.py",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


WEB_REQUEST = FakeWebRequest()
CONTENT_WEB = _load_content_web(WEB_REQUEST)


class RecordingContext:
    """记录注册路由的假宿主上下文。"""

    def __init__(self) -> None:
        self.routes: list[tuple[str, list[str]]] = []

    def register_web_api(self, path, handler, methods, description) -> None:
        self.routes.append((path, list(methods), description))


class BatchUpstream:
    """按路径返回合法五类内容；可指定某站点某类型失败。"""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.failing: set[str] = set()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.seen.append(path)
        if path in self.failing:
            return httpx.Response(500, json={"code": 500, "message": "boom"})
        if path.endswith("/v2/60s"):
            body = {"code": 200, "message": "ok", "data": {"news": []}}
        else:
            body = {"code": 200, "message": "ok", "data": []}
        return httpx.Response(200, json=body)


class BatchTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.upstream = BatchUpstream()
        self.client = ContentHttpClient(transport=httpx.MockTransport(self.upstream))
        self.service = ContentService(
            self.sites,
            client=self.client,
            clock=lambda: datetime(2026, 11, 16, 12, tzinfo=timezone.utc),
        )
        await self.service.open()
        self.api = ContentApi(self.service)
        self.web = CONTENT_WEB.ContentWeb(
            self.api, plugin_name="60sapi_hub", is_active=lambda: True,
        )
        WEB_REQUEST.username = "admin"
        WEB_REQUEST.payload = None

    async def asyncTearDown(self) -> None:
        await self.service.close()
        await self.sites.close()
        self._temporary_directory.cleanup()


class ContentWebRegistrationTest(BatchTestBase):
    async def test_batch_route_is_registered_under_plugin_prefix(self) -> None:
        context = RecordingContext()
        self.web.register(context)
        paths = {path: methods for path, methods, _ in context.routes}
        self.assertEqual(paths["/60sapi_hub/sites/test-batch"], ["POST"])
        self.assertEqual(paths["/60sapi_hub/sites/<site_id>/test"], ["POST"])


class BatchTestRunnerTest(BatchTestBase):
    async def _site(self, name: str) -> dict:
        site = await self.sites.create_site(SiteCreate(
            name=name, base_url="http://upstream.test/proxy",
            timeout_seconds=5,
        ))
        return site

    def _post(self, payload: object, *, username: str | None = "admin"):
        WEB_REQUEST.username = username
        WEB_REQUEST.payload = payload
        return self.web

    async def test_five_types_for_each_selected_site(self) -> None:
        first = await self._site("甲站点")
        second = await self._site("乙站点")
        third = await self._site("第三站点")

        self._post({
            "site_ids": [first.id, second.id, third.id],
            "content_types": list(FIVE_TYPES),
        })
        result = await self.web.test_sites_batch()

        self.assertEqual(result.status_code, 200, result.body)
        results = result.body["results"]
        self.assertEqual(len(results), 15)
        self.assertEqual(result.body["failed"], 0)
        self.assertEqual(
            {(item["site_id"], item["content_type"]) for item in results},
            {(site, kind) for site in (first.id, second.id, third.id)
             for kind in FIVE_TYPES},
        )
        self.assertEqual({item["result"] for item in results}, {"empty"})
        self.assertEqual(len(self.upstream.seen), 15)


    async def test_partial_failure_keeps_other_results(self) -> None:
        good = await self._site("正常站点")
        broken = await self._site("部分失败站点")
        self.upstream.failing = {"/proxy/v2/toutiao"}

        self._post({
            "site_ids": [good.id, broken.id],
            "content_types": list(FIVE_TYPES),
        })
        result = await self.web.test_sites_batch()

        self.assertEqual(result.status_code, 200, result.body)
        results = result.body["results"]
        self.assertEqual(len(results), 10)
        self.assertEqual(result.body["failed"], 2)
        failed = [item for item in results if item["result"] == "error"]
        self.assertEqual({item["content_type"] for item in failed}, {"toutiao_hot"})
        self.assertEqual({item["site_id"] for item in failed}, {good.id, broken.id})
        for item in failed:
            self.assertTrue(item["message"])
            self.assertGreaterEqual(item["elapsed_ms"], 0)
        self.assertEqual(
            sum(1 for item in results if item["result"] == "empty"), 8,
        )

    async def test_single_type_selection_only_tests_that_type(self) -> None:
        site = await self._site("单类站点")
        self._post({"site_ids": [site.id], "content_types": ["bilibili_hot"]})
        result = await self.web.test_sites_batch()
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(len(result.body["results"]), 1)
        self.assertEqual(
            result.body["results"][0]["content_type"], "bilibili_hot",
        )

    async def test_batch_does_not_touch_cache_or_configuration(self) -> None:
        site = await self._site("缓存站点")
        async def forbidden_cache(*args, **kwargs):
            raise AssertionError("批量测试不能读取内容缓存")
        self.service._cache.get = forbidden_cache

        self._post({"site_ids": [site.id], "content_types": list(FIVE_TYPES)})
        result = await self.web.test_sites_batch()
        self.assertEqual(result.status_code, 200, result.body)
        current = await self.sites.get_site(site.id)
        self.assertEqual(current.revision, site.revision)

    async def test_invalid_payloads_are_rejected_before_any_request(self) -> None:
        site = await self._site("校验站点")
        cases = (
            (None, "invalid_body"),
            ({"site_ids": [], "content_types": ["daily_news"]}, "validation_error"),
            ({"site_ids": "abc", "content_types": ["daily_news"]}, "validation_error"),
            ({"site_ids": [site.id], "content_types": []}, "validation_error"),
            ({"site_ids": [site.id], "content_types": ["weibo_hot"]}, "validation_error"),
            ({"site_ids": [site.id], "content_types": ["daily_news"], "x": 1},
             "validation_error"),
            ({"site_ids": [f"site-{index}" for index in range(21)],
              "content_types": ["daily_news"]}, "validation_error"),
        )
        for payload, code in cases:
            with self.subTest(payload=payload):
                self._post(payload)
                result = await self.web.test_sites_batch()
                self.assertEqual(result.status_code, 400, result.body)
                self.assertEqual(result.body["data"]["code"], code)
        self.assertEqual(self.upstream.seen, [])

    async def test_each_site_reports_the_revision_used(self) -> None:
        site = await self._site("版本站点")
        self._post({"site_ids": [site.id], "content_types": list(FIVE_TYPES)})
        result = await self.web.test_sites_batch()
        self.assertEqual(result.status_code, 200, result.body)
        revisions = {item["site_revision"] for item in result.body["results"]}
        self.assertEqual(revisions, {site.revision})
        self.assertEqual(result.body["stale"], 0)

    async def test_expected_revision_mismatch_marks_every_type_stale(self) -> None:
        site = await self._site("已改动站点")
        seen_revision = site.revision
        updated = await self.sites.update_site(
            site.id,
            SiteUpdate(
                name="已改动站点", base_url="http://upstream.test/proxy2",
                timeout_seconds=5,
                expected_revision=seen_revision,
            ),
        )
        self.assertGreater(updated.revision, seen_revision)
        before = len(self.upstream.seen)

        self._post({
            "site_ids": [site.id],
            "content_types": list(FIVE_TYPES),
            "expected_revisions": {site.id: seen_revision},
        })
        result = await self.web.test_sites_batch()

        self.assertEqual(result.status_code, 200, result.body)
        results = result.body["results"]
        self.assertEqual(len(results), 5)
        self.assertEqual({item["result"] for item in results}, {"stale"})
        self.assertEqual(
            {item["message"] for item in results}, {BATCH_STALE_MESSAGE},
        )
        self.assertEqual(
            {item["site_revision"] for item in results}, {updated.revision},
        )
        self.assertEqual(result.body["stale"], 5)
        self.assertEqual(result.body["failed"], 0)
        self.assertEqual(len(self.upstream.seen), before, "版本变化后不得请求上游")

    async def test_conflict_during_test_is_reported_as_stale(self) -> None:
        site = await self._site("中途改动站点")
        async def conflicting(site_id, payload, *, inflight_limit=2):
            return ContentApiResult(
                409, {"message": "站点配置已变化，请刷新后重试。"},
            )
        self.api.test_connection = conflicting
        # 预检读到的仍是当前版本，因此每类都会进入测试并拿到 409。
        self._post({
            "site_ids": [site.id],
            "content_types": list(FIVE_TYPES),
            "expected_revisions": {site.id: site.revision},
        })
        result = await self.web.test_sites_batch()
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual({item["result"] for item in result.body["results"]},
                         {"stale"})
        self.assertEqual(result.body["stale"], 5)

    async def test_expected_revisions_are_validated(self) -> None:
        site = await self._site("版本校验站点")
        cases = (
            {"site_ids": [site.id], "content_types": ["daily_news"],
             "expected_revisions": []},
            {"site_ids": [site.id], "content_types": ["daily_news"],
             "expected_revisions": {site.id: "1"}},
            {"site_ids": [site.id], "content_types": ["daily_news"],
             "expected_revisions": {site.id: 0}},
            {"site_ids": [site.id], "content_types": ["daily_news"],
             "expected_revisions": {"other-site": 1}},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self._post(payload)
                result = await self.web.test_sites_batch()
                self.assertEqual(result.status_code, 400, result.body)
                self.assertEqual(result.body["data"]["code"], "validation_error")
        self.assertEqual(self.upstream.seen, [])

    async def test_missing_login_and_deleted_site(self) -> None:
        site = await self._site("鉴权站点")
        self._post({"site_ids": [site.id], "content_types": ["daily_news"]},
                   username=None)
        unauthorized = await self.web.test_sites_batch()
        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(self.upstream.seen, [])

        await self.sites.delete_site(site.id, site.revision)
        self._post({"site_ids": [site.id], "content_types": ["daily_news"]})
        missing = await self.web.test_sites_batch()
        self.assertEqual(missing.status_code, 200, missing.body)
        self.assertEqual(missing.body["results"][0]["result"], "error")
        self.assertEqual(missing.body["failed"], 1)


if __name__ == "__main__":
    unittest.main()
