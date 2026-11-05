"""站点测试连接的后端专项边界测试。

覆盖：鉴权失败与插件停用零上游、请求中删除站点、配置读取期间关闭插件、旧 Web
请求跨生命周期、取消与异常后的并发名额恢复、缓存与已有配置/任务/运行记录
不变、发送器与图片渲染零调用，以及本地上游反射 Bearer 凭据时不泄漏。

Web 处理器测试使用假的 astrbot.api.web 适配层驱动真实 ContentWeb，
本仓库测试环境不包含 AstrBot 本体。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from core.content_api import ContentApi
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.delivery import (
    DeliveryLimits,
    DeliveryService,
    DeliverySendResult,
    SendStatus,
)
from core.image_service import ProfileImageService
from core.news import NewsInvalidJSONError
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteNotFoundError, SiteService, SiteTestBusyError
from core.tasks import PushTaskService, TaskCreate

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEST_PACKAGE = "_site_connection_boundary_pkg"
FAKE_UMO = "aiocqhttp:GroupMessage:fake-group:1"
SECRET_TOKEN = "reflect-secret-7f3c9a"


class FakeWebRequest:
    """替代 astrbot.api.web.request 的请求上下文。"""

    def __init__(self) -> None:
        self.username: str | None = None
        self.payload: object = None
        self.calls = 0
        self.gate: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None
        self.reset()

    def reset(self) -> None:
        self.username = "admin"
        self.payload = None
        self.calls = 0
        self.gate = None
        self.entered = asyncio.Event()

    async def json(self, default=None):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        gate = self.gate
        if gate is not None:
            await gate.wait()
            self.gate = None
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
    """以合成包名导入真实处理器模块，不污染插件自身的模块缓存。"""
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


def _daily_item() -> dict[str, object]:
    return {"content_type": "daily_news", "params": {}, "limit": None}


class FakeSender:
    """只记录调用的假发送器，不接触任何真实平台。"""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message) -> DeliverySendResult:
        self.calls.append(message)
        return DeliverySendResult(SendStatus.SUCCESS)


class StubProfileApi:
    """固定返回合法正文的组合预览替身，避免测试请求上游。"""

    async def preview_profile(self, payload, *, owner=None):
        from core.profile_api import ProfileApiResult

        return ProfileApiResult(
            200,
            {
                "status": "ok",
                "data": {
                    "outputs": {"text": "内容", "markdown": "# 内容"},
                    "failed_sections": [],
                },
            },
        )


class ReflectingUpstream:
    """本地 HTTP 上游：把 Authorization 头原样反射进响应体。"""

    def __init__(self) -> None:
        self.mode = "success"
        self.seen: list[tuple[str, str]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                authorization = self.headers.get("Authorization", "")
                owner.seen.append((self.path, authorization))
                if owner.mode == "http_401":
                    status = 401
                    payload = {"code": 401, "message": authorization}
                elif owner.mode == "business_error":
                    status = 200
                    payload = {
                        "code": 401,
                        "message": authorization,
                        "data": {"echo": authorization},
                    }
                else:
                    status = 200
                    payload = {
                        "code": 200,
                        "message": authorization,
                        "data": {
                            "date": "2026-10-24",
                            "news": [authorization],
                        },
                    }
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02),
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self):
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class SiteConnectionFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.sites = SiteService(
            Path(self._temporary_directory.name) / "sites.sqlite3",
        )
        await self.sites.open()
        self.clock = lambda: datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self._closers: list[object] = []

    async def asyncTearDown(self) -> None:
        for closer in reversed(self._closers):
            await closer()
        await self.sites.close()
        self._temporary_directory.cleanup()

    async def _site(
        self,
        name: str = "边界站点",
        *,
        enabled: bool = False,
        bearer_token: str | None = None,
    ):
        return await self.sites.create_site(
            SiteCreate(
                name=name,
                base_url="http://example.test/proxy",
                enabled=enabled,
                timeout_seconds=5,
                bearer_token=bearer_token,
            ),
        )

    async def _opened_content(self, handler, **client_kwargs) -> ContentService:
        client = ContentHttpClient(
            transport=httpx.MockTransport(handler),
            **client_kwargs,
        )
        content = ContentService(self.sites, client=client, clock=self.clock)
        self._closers.append(content.close)
        await content.open()
        return content

    async def _existing_configuration(self, site):
        profile = await self.profiles.create_profile(
            ProfileCreate(
                name="已有组合",
                site_id=site.id,
                items=[_daily_item()],
            ),
        )
        task = await self.tasks.create_task(
            TaskCreate(
                name="已有任务",
                profile_id=profile.id,
                output_format="text",
                umos=[FAKE_UMO],
                schedule={
                    "kind": "daily",
                    "timezone": "Asia/Shanghai",
                    "time": "08:30",
                },
            ),
        )
        sender = FakeSender()
        delivery = DeliveryService(
            tasks=self.tasks,
            profiles=self.profiles,
            sites=self.sites,
            profile_api=StubProfileApi(),
            store=self.sites.store,
            sender=sender,
            limits=DeliveryLimits(max_execution_seconds=5),
        )
        self._closers.append(delivery.close)
        execution = await delivery.execute(
            task.id,
            "existing-run",
            expected_task_revision=task.revision,
            expected_profile_revision=profile.revision,
            expected_site_revision=site.revision,
        )
        self.assertEqual(execution.status, "success")
        return profile, task, delivery, sender

    async def _snapshot(self):
        sites = [
            (
                item.id,
                item.name,
                item.base_url,
                item.enabled,
                item.timeout_seconds,
                item.has_bearer_token,
                item.revision,
                item.created_at,
                item.updated_at,
            )
            for item in await self.sites.list_sites()
        ]
        profiles = await self.profiles.list_profiles()
        tasks = await self.tasks.list_tasks()
        execution_ids = await self.sites.store.list_delivery_execution_ids(
            limit=20,
            offset=0,
        )
        runs = tuple([
            await self.sites.store.get_delivery_execution(execution_id)
            for execution_id in execution_ids
        ])
        return sites, profiles, tasks, runs


class SiteConnectionWebGuardTest(SiteConnectionFixture):
    """Web 处理器守卫：鉴权失败与旧代次请求都不得触达上游。"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        WEB_REQUEST.reset()
        self.upstream_calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.upstream_calls.append(request.url.path)
            return httpx.Response(
                200,
                json={"code": 200, "message": "ok", "data": {"news": []}},
            )

        self.content = await self._opened_content(handler)
        self.site = await self._site()
        self.state = {"epoch": 1, "active": True}
        self.web = CONTENT_WEB.ContentWeb(
            ContentApi(self.content),
            plugin_name="test-plugin",
            is_active=lambda: self.state["active"],
            lifecycle=lambda: self.state["epoch"],
        )

    def _payload(self) -> dict[str, object]:
        return {
            "expected_revision": self.site.revision,
            "content_type": "daily_news",
        }

    async def test_unauthenticated_or_inactive_request_makes_no_upstream_call(
        self,
    ) -> None:
        WEB_REQUEST.username = None
        result = await self.web.test_connection(self.site.id)
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.body["message"], "需要登录后才能获取内容预览。")
        self.assertEqual(WEB_REQUEST.calls, 0)
        self.assertEqual(self.upstream_calls, [])

        WEB_REQUEST.username = "admin"
        WEB_REQUEST.payload = self._payload()
        self.state["active"] = False
        inactive = await self.web.test_connection(self.site.id)
        self.assertEqual(inactive.status_code, 503)
        self.assertEqual(inactive.body["message"], "插件当前未启用。")
        self.assertEqual(WEB_REQUEST.calls, 0)
        self.assertEqual(self.upstream_calls, [])
        self.assertEqual(
            [item.id for item in await self.sites.list_sites()],
            [self.site.id],
        )

        self.state["active"] = True
        allowed = await self.web.test_connection(self.site.id)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(self.upstream_calls, ["/proxy/v2/60s"])

    async def test_old_request_blocked_on_request_json_makes_no_upstream_call(
        self,
    ) -> None:
        WEB_REQUEST.payload = self._payload()
        WEB_REQUEST.gate = asyncio.Event()
        old_request = asyncio.create_task(
            self.web.test_connection(self.site.id),
        )
        await asyncio.wait_for(WEB_REQUEST.entered.wait(), 1)
        self.assertEqual(self.upstream_calls, [])

        await self.content.close()
        await self.content.open()
        self.state["epoch"] += 1
        self.state["active"] = True
        WEB_REQUEST.gate.set()

        stale = await asyncio.wait_for(old_request, 5)
        self.assertEqual(stale.status_code, 503)
        self.assertEqual(
            stale.body["message"],
            "插件当前不可用，请重新打开页面后重试。",
        )
        self.assertEqual(self.upstream_calls, [])

        fresh = await self.web.test_connection(self.site.id)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(self.upstream_calls, ["/proxy/v2/60s"])


class SiteConnectionLifecycleTest(SiteConnectionFixture):
    """请求进行中的删除、配置读取期间的关闭与并发名额恢复。"""

    async def test_delete_during_request_reports_not_found_and_frees_capacity(
        self,
    ) -> None:
        site = await self._site("删除中站点")
        started = asyncio.Event()
        release = asyncio.Event()
        upstream_calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request.url.path)
            started.set()
            await release.wait()
            return httpx.Response(
                200,
                json={"code": 200, "message": "ok", "data": {"news": []}},
            )

        content = await self._opened_content(handler)
        api = ContentApi(content)
        task = asyncio.create_task(
            api.test_connection(
                site.id,
                {
                    "expected_revision": site.revision,
                    "content_type": "daily_news",
                },
            ),
        )
        await asyncio.wait_for(started.wait(), 1)
        await self.sites.delete_site(site.id, site.revision)
        release.set()

        result = await asyncio.wait_for(task, 5)
        self.assertEqual(result.status_code, 404)
        self.assertEqual(result.body["data"]["code"], "site_not_found")
        self.assertEqual(upstream_calls, ["/proxy/v2/60s"])
        self.assertEqual(await self.sites.list_sites(), [])
        self.assertEqual(content._test_inflight, 0)
        self.assertEqual(content._test_tasks, set())
        self.assertEqual(content.client.active_request_count, 0)

        remaining = await self._site("后续站点")
        following = await content.test_connection(
            remaining.id,
            "daily_news",
            remaining.revision,
        )
        self.assertEqual(following["result"], "empty")

    async def test_close_during_config_read_cancels_without_dangling_task(
        self,
    ) -> None:
        site = await self._site()
        upstream_calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request.url.path)
            return httpx.Response(200, json={"code": 200, "data": {"news": []}})

        content = await self._opened_content(handler)
        started = asyncio.Event()
        release = asyncio.Event()
        original = self.sites.get_test_config

        async def blocking_get_test_config(site_id, expected_revision):
            started.set()
            await release.wait()
            return await original(site_id, expected_revision)

        self.sites.get_test_config = blocking_get_test_config
        try:
            task = asyncio.create_task(
                content.test_connection(site.id, "daily_news", site.revision),
            )
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(content.close(), 5)
            self.assertTrue(task.done(), "close 必须等待在途操作结束")
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            del self.sites.get_test_config
            release.set()

        self.assertEqual(upstream_calls, [])
        self.assertEqual(content._test_inflight, 0)
        self.assertEqual(content._test_tasks, set())
        self.assertEqual(content.client.active_request_count, 0)
        self.assertFalse(content.is_open)
        self.assertFalse(content._cache.is_open)
        await asyncio.sleep(0)
        self.assertEqual(upstream_calls, [])

    async def test_cancelled_tests_restore_concurrency_slots(self) -> None:
        site = await self._site()
        gate = asyncio.Event()
        both_started = asyncio.Event()
        upstream_calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request.url.path)
            if len(upstream_calls) >= 2:
                both_started.set()
            await gate.wait()
            return httpx.Response(200, json={"code": 200, "data": {"news": []}})

        content = await self._opened_content(handler)
        first = asyncio.create_task(
            content.test_connection(site.id, "daily_news", site.revision),
        )
        second = asyncio.create_task(
            content.test_connection(site.id, "daily_news", site.revision),
        )
        await asyncio.wait_for(both_started.wait(), 1)
        with self.assertRaises(SiteTestBusyError):
            await content.test_connection(site.id, "daily_news", site.revision)
        self.assertEqual(len(upstream_calls), 2)

        for task in (first, second):
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(content._test_inflight, 0)
        self.assertEqual(content._test_tasks, set())
        self.assertEqual(content.client.active_request_count, 0)
        self.assertEqual(len(upstream_calls), 2)

        gate.set()
        results = await asyncio.gather(
            content.test_connection(site.id, "daily_news", site.revision),
            content.test_connection(site.id, "daily_news", site.revision),
        )
        self.assertEqual([item["result"] for item in results], ["empty", "empty"])
        self.assertEqual(len(upstream_calls), 4)

    async def test_failed_tests_restore_concurrency_slots(self) -> None:
        site = await self._site()
        gate = asyncio.Event()
        both_started = asyncio.Event()
        upstream_calls: list[str] = []
        state = {"fail": True}

        async def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request.url.path)
            if state["fail"]:
                if len(upstream_calls) >= 2:
                    both_started.set()
                await gate.wait()
                return httpx.Response(200, text="not-json")
            return httpx.Response(200, json={"code": 200, "data": {"news": []}})

        content = await self._opened_content(handler)
        first = asyncio.create_task(
            content.test_connection(site.id, "daily_news", site.revision),
        )
        second = asyncio.create_task(
            content.test_connection(site.id, "daily_news", site.revision),
        )
        await asyncio.wait_for(both_started.wait(), 1)
        with self.assertRaises(SiteTestBusyError):
            await content.test_connection(site.id, "daily_news", site.revision)
        gate.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(
            all(isinstance(item, NewsInvalidJSONError) for item in results),
        )
        self.assertEqual(content._test_inflight, 0)
        self.assertEqual(content._test_tasks, set())
        self.assertEqual(content.client.active_request_count, 0)

        state["fail"] = False
        following = await content.test_connection(
            site.id,
            "daily_news",
            site.revision,
        )
        self.assertEqual(following["result"], "empty")
        self.assertEqual(len(upstream_calls), 3)


class SiteConnectionStateTest(SiteConnectionFixture):
    """测试连接不得改动缓存、配置、任务、运行记录、发送和图片边界。"""

    @staticmethod
    async def _empty_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v2/60s"):
            body = {"code": 200, "message": "ok", "data": {"news": []}}
        else:
            body = {"code": 200, "message": "ok", "data": []}
        return httpx.Response(200, json=body)

    async def test_connection_test_keeps_cache_content_and_expiry(self) -> None:
        site = await self._site(
            "缓存站点",
            enabled=True,
            bearer_token="cache-token",
        )
        upstream_calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            upstream_calls.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "ok",
                    "data": [{"title": "热搜", "link": "https://example.test/i"}],
                },
            )

        content = await self._opened_content(handler)
        cache_ttl = content.cache_ttl_seconds
        first = await content.preview_cached("bilibili_hot", site.id)
        self.assertFalse(first.hit)
        self.assertEqual(upstream_calls, ["/proxy/v2/bili"])
        cached = await content.preview_cached("bilibili_hot", site.id)
        self.assertTrue(cached.hit)
        self.assertEqual(len(upstream_calls), 1)

        result = await content.test_connection(
            site.id,
            "bilibili_hot",
            site.revision,
        )
        self.assertEqual(result["result"], "success")
        self.assertEqual(len(upstream_calls), 2)

        again = await content.preview_cached("bilibili_hot", site.id)
        self.assertTrue(again.hit)
        self.assertEqual(again.expires_at, first.expires_at)
        self.assertIs(again.snapshot, first.snapshot)
        self.assertEqual([item.title for item in again.snapshot.items], ["热搜"])
        self.assertEqual(content._cache.size, 1)
        self.assertEqual(content.cache_ttl_seconds, cache_ttl)
        self.assertEqual(len(upstream_calls), 2)

    async def test_connection_test_keeps_config_tasks_and_run_records(
        self,
    ) -> None:
        site = await self._site(
            "保持配置站点",
            enabled=True,
            bearer_token="keep-token",
        )
        disabled_site = await self._site("停用站点")
        _profile, _task, _delivery, sender = (
            await self._existing_configuration(site)
        )
        content = await self._opened_content(self._empty_handler)
        before = await self._snapshot()
        self.assertEqual(len(before[3]), 1)

        for content_type in (
            "daily_news",
            "bilibili_hot",
            "zhihu_hot",
            "douyin_hot",
            "toutiao_hot",
        ):
            enabled_result = await content.test_connection(
                site.id,
                content_type,
                site.revision,
            )
            self.assertEqual(enabled_result["result"], "empty")
            disabled_result = await content.test_connection(
                disabled_site.id,
                content_type,
                disabled_site.revision,
            )
            self.assertEqual(disabled_result["result"], "empty")

        after = await self._snapshot()
        self.assertEqual(after, before)
        self.assertEqual(len(after[3]), 1)
        self.assertEqual(after[3][0].status, "success")
        self.assertEqual(len(sender.calls), 1)

    async def test_connection_test_never_sends_or_renders_images(self) -> None:
        site = await self._site("不触达站点", enabled=True)
        _profile, _task, _delivery, sender = (
            await self._existing_configuration(site)
        )
        self.assertEqual(len(sender.calls), 1)
        image_service = ProfileImageService()
        self._closers.append(image_service.close)
        content = await self._opened_content(self._empty_handler)

        with patch.object(
            ProfileImageService,
            "render",
            new_callable=AsyncMock,
        ) as render:
            await image_service.render({})
            render.assert_awaited_once()
            render.reset_mock()
            for content_type in (
                "daily_news",
                "bilibili_hot",
                "zhihu_hot",
                "douyin_hot",
                "toutiao_hot",
            ):
                result = await content.test_connection(
                    site.id,
                    content_type,
                    site.revision,
                )
                self.assertEqual(result["result"], "empty")
            render.assert_not_awaited()

        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(image_service.active_renders, 0)
        execution_ids = await self.sites.store.list_delivery_execution_ids(
            limit=20,
            offset=0,
        )
        self.assertEqual(execution_ids, ("existing-run",))

    async def test_missing_site_never_reaches_sender_or_image(self) -> None:
        delivery_site = await self._site("投递站点", enabled=True)
        _profile, _task, _delivery, sender = (
            await self._existing_configuration(delivery_site)
        )
        content = await self._opened_content(self._empty_handler)
        with patch.object(
            ProfileImageService,
            "render",
            new_callable=AsyncMock,
        ) as render:
            with self.assertRaises(SiteNotFoundError):
                await content.test_connection("missing-site", "daily_news", 1)
            render.assert_not_awaited()
        self.assertEqual(len(sender.calls), 1)


class SiteConnectionCredentialReflectionTest(SiteConnectionFixture):
    """本地上游反射 Authorization 头时，完整响应体不得包含凭据。"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.upstream = ReflectingUpstream().start()
        self.site = await self.sites.create_site(
            SiteCreate(
                name="反射站点",
                base_url=self.upstream.base_url,
                enabled=False,
                timeout_seconds=5,
                bearer_token=SECRET_TOKEN,
            ),
        )
        self.content = ContentService(self.sites, clock=self.clock)
        self._closers.append(self.content.close)
        await self.content.open()
        self.api = ContentApi(self.content)

    async def asyncTearDown(self) -> None:
        await super().asyncTearDown()
        self.upstream.stop()

    def _assert_no_credential(self, body: dict) -> None:
        dumped = json.dumps(body, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(SECRET_TOKEN, dumped)
        self.assertNotIn(f"Bearer {SECRET_TOKEN}", dumped)
        pending: list[object] = [body]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, (list, tuple)):
                pending.extend(value)
            elif isinstance(value, str):
                self.assertNotIn(SECRET_TOKEN, value)

    async def _test_connection(self):
        return await self.api.test_connection(
            self.site.id,
            {
                "expected_revision": self.site.revision,
                "content_type": "daily_news",
            },
        )

    async def test_reflecting_upstream_never_returns_bearer_credential(
        self,
    ) -> None:
        success = await self._test_connection()
        self.assertEqual(success.status_code, 200)
        self.assertEqual(success.body["data"]["result"], "success")
        self.assertEqual(
            success.body["data"]["message"],
            "连接成功，接口返回内容结构合法。",
        )
        self._assert_no_credential(success.body)
        self.assertEqual(
            self.upstream.seen,
            [("/v2/60s", f"Bearer {SECRET_TOKEN}")],
        )

        self.upstream.mode = "business_error"
        business_error = await self._test_connection()
        self.assertEqual(business_error.status_code, 502)
        self.assertEqual(
            business_error.body["data"]["code"],
            "test_business_error",
        )
        self._assert_no_credential(business_error.body)

        self.upstream.mode = "http_401"
        denied = await self._test_connection()
        self.assertEqual(denied.status_code, 502)
        self.assertEqual(denied.body["data"]["code"], "test_http_status")
        self._assert_no_credential(denied.body)
        self.assertEqual(len(self.upstream.seen), 3)


if __name__ == "__main__":
    unittest.main()
