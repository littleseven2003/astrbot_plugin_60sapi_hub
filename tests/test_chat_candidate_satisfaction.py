"""聊天查询复用投递的候选判定：只取第一个真正满足组合的站点。

上游按站点域名区分内容，因此“来自哪个站点”可以直接从文本和图片页脚读出。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from core.chat_commands import ChatCommandService
from core.chat_content import FAILED_TEXT, ChatContentQuery
from core.chat_image import IMAGE_FAILED_TEXT, ChatImageQuery
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.content_plan import plan_payload
from core.plan_preview import PlanPreview
from core.profile_image import (
    FontUnavailableError,
    ProfileImageRenderer,
    find_cjk_font,
)
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate, TaskUpdate

UMO = "bot:GroupMessage:g"
OTHER_UMO = "bot:PrivateMessage:p"
SECRET = "chat-secret-token"


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


class ChatCandidateSatisfactionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "candidates.sqlite3")
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        # 每个上游域名的可用内容条数；值为负数表示该 endpoint 直接失败。
        self.upstream: dict[str, dict[str, int]] = {}
        self.calls: list[tuple[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            path = request.url.path
            self.calls.append((host, path))
            plan = self.upstream.get(host, {})
            if path.endswith("/v2/bili"):
                count = plan.get("bili", 0)
                if count < 0:
                    return httpx.Response(503, content=b"bili secret upstream body")
                payload = [
                    {"title": f"{host} 热榜 {index}"}
                    for index in range(1, count + 1)
                ]
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok", "data": payload,
                }).encode())
            count = plan.get("news", 0)
            if count < 0:
                return httpx.Response(
                    500, content=b"news secret upstream body",
                )
            news = [f"{host} 新闻 {index}" for index in range(1, count + 1)]
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok",
                "data": {"date": "2026-11-16", "news": news},
            }).encode())

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.snapshots = ImageSnapshotStore()
        self.image_service = ProfileImageService()
        self.api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.image_service,
        )
        self.content_query = ChatContentQuery(self.tasks, self.api)
        self.image_query = ChatImageQuery(self.tasks, self.api)

    async def asyncTearDown(self) -> None:
        await self.image_service.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _site(self, name: str, host: str, secret_path: str = "") -> object:
        return await self.sites.create_site(SiteCreate(
            name=name,
            base_url=f"http://{host}{secret_path}/60s",
            enabled=True,
            timeout_seconds=5,
        ))

    async def _task(
        self,
        sites: list[object],
        items: list[dict] | None = None,
        output_format: str = "text",
        umos: list[str] | None = None,
    ):
        return await self.tasks.create_task(TaskCreate(
            name="候选任务",
            items=items or [
                {"content_type": "daily_news", "params": {}, "limit": 3},
                {"content_type": "bilibili_hot", "params": {}, "limit": 2},
            ],
            sites=[site.id for site in sites],
            output_format=output_format,
            umos=umos or [UMO],
        ))

    def _hosts_called(self) -> list[str]:
        seen: list[str] = []
        for host, _path in self.calls:
            if not seen or seen[-1] != host:
                seen.append(host)
        return seen

    async def test_text_uses_first_satisfying_site(self) -> None:
        short = await self._site("内容不足站", "a.example.test")
        full = await self._site("内容充足站", "b.example.test")
        self.upstream = {
            # 只给 1 条新闻，达不到组合要求的 3 条。
            "a.example.test": {"news": 1, "bili": 2},
            "b.example.test": {"news": 4, "bili": 2},
        }
        task = await self._task([short, full])

        text = await self.content_query.query(UMO, task.id)

        self.assertIn("b.example.test 新闻 1", text)
        self.assertNotIn("a.example.test 新闻", text)
        self.assertNotIn("a.example.test 热榜", text)
        self.assertEqual(
            self._hosts_called(),
            ["a.example.test", "b.example.test"],
        )

    async def test_all_sites_short_of_limit_returns_failed_text(self) -> None:
        first = await self._site("缺口站", "a.example.test")
        second = await self._site("缺口站二", "c.example.test")
        self.upstream = {
            "a.example.test": {"news": 2, "bili": 2},
            "c.example.test": {"news": -1, "bili": 2},
        }
        task = await self._task([first, second])

        text = await self.content_query.query(UMO, task.id)

        self.assertEqual(text, FAILED_TEXT)
        self.assertNotIn("a.example.test 新闻", text)
        self.assertNotIn("secret upstream body", text)

    async def test_unlimited_limit_accepts_empty_list(self) -> None:
        empty = await self._site("空内容站", "d.example.test")
        self.upstream = {"d.example.test": {"news": 0, "bili": 0}}
        task = await self._task(
            [empty],
            items=[
                {"content_type": "daily_news", "params": {}, "limit": None},
            ],
        )

        text = await self.content_query.query(UMO, task.id)

        # limit 为空表示不限条数：合法空列表也算满足，不触发换站或失败。
        self.assertNotEqual(text, FAILED_TEXT)
        self.assertEqual(self._hosts_called(), ["d.example.test"])

    async def test_candidate_order_decides_source(self) -> None:
        first = await self._site("顺序站一", "e.example.test")
        second = await self._site("顺序站二", "f.example.test")
        self.upstream = {
            "e.example.test": {"news": 3, "bili": 2},
            "f.example.test": {"news": 5, "bili": 3},
        }
        task = await self._task([first, second])
        self.assertIn(
            "e.example.test 新闻",
            await self.content_query.query(UMO, task.id),
        )

        await self.tasks.update_task(task.id, TaskUpdate(
            name="候选任务",
            # 栏目本身不变，只调整候选站点顺序：任务 id 与方案 revision 都保持。
            items=[dict(entry) for entry in plan_payload(task.items)],
            output_format="text",
            umos=[UMO],
            expected_revision=task.revision,
            sites=[second.id, first.id],
        ))
        self.calls.clear()

        text = await self.content_query.query(UMO, task.id)

        self.assertIn("f.example.test 新闻", text)
        self.assertNotIn("e.example.test 新闻", text)
        self.assertEqual(self._hosts_called(), ["f.example.test"])

    async def test_disabled_candidate_is_skipped(self) -> None:
        disabled = await self._site("停用站", "g.example.test")
        usable = await self._site("可用站", "i.example.test")
        self.upstream = {
            "g.example.test": {"news": 9, "bili": 9},
            "i.example.test": {"news": 3, "bili": 2},
        }
        task = await self._task([disabled, usable])
        # 停用站点仍在候选里，但聊天不会请求它，也不会用它的内容。
        await self._disable(disabled)

        text = await self.content_query.query(UMO, task.id)

        self.assertIn("i.example.test 新闻 1", text)
        self.assertEqual(self._hosts_called(), ["i.example.test"])

    async def _disable(self, site) -> None:
        from core.sites import SiteUpdate

        await self.sites.update_site(site.id, SiteUpdate(
            name=site.name,
            base_url=site.base_url,
            source_kind=site.source_kind,
            enabled=False,
            timeout_seconds=site.timeout_seconds,
            expected_revision=site.revision,
        ))

    async def test_other_session_cannot_query(self) -> None:
        site = await self._site("授权站", "j.example.test")
        self.upstream = {"j.example.test": {"news": 3, "bili": 2}}
        task = await self._task([site])

        denied = await self.content_query.query(OTHER_UMO, task.id)

        self.assertNotIn("j.example.test 新闻", denied)
        self.assertEqual(self.calls, [])

    async def test_source_output_hides_secret_path(self) -> None:
        site = await self._site(
            "带路径站", "k.example.test", f"/{SECRET}",
        )
        self.upstream = {"k.example.test": {"news": 3, "bili": 2}}
        task = await self._task([site])

        text = await self.content_query.query(UMO, task.id)

        self.assertIn("k.example.test", text)
        self.assertNotIn(SECRET, text)

    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_image_uses_first_satisfying_site(self) -> None:
        short = await self._site("长图不足站", "l.example.test")
        full = await self._site("长图充足站", "m.example.test")
        self.upstream = {
            "l.example.test": {"news": 1, "bili": 2},
            "m.example.test": {"news": 3, "bili": 2},
        }
        task = await self._task([short, full], output_format="image")
        captured: list[str] = []
        original = ProfileImageRenderer._footer_lines

        def recording(renderer, section, site_name, created_at, width):
            lines = original(renderer, section, site_name, created_at, width)
            captured.append(
                "".join(part.text for line in lines for part in line.parts),
            )
            return lines

        with mock.patch.object(
            ProfileImageRenderer, "_footer_lines", recording,
        ):
            reply = await self.image_query.query(UMO, task.id)

        self.assertNotEqual(reply, IMAGE_FAILED_TEXT)
        self.assertGreaterEqual(reply.image_count, 1)
        self.assertTrue(captured)
        self.assertTrue(
            any("m.example.test" in footer for footer in captured),
        )
        self.assertFalse(
            any("l.example.test" in footer for footer in captured),
        )

    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_image_all_sites_short_returns_failed_text(self) -> None:
        short = await self._site("长图缺口站", "n.example.test")
        self.upstream = {"n.example.test": {"news": 1, "bili": 2}}
        task = await self._task([short], output_format="image")

        reply = await self.image_query.query(UMO, task.id)

        self.assertEqual(reply, IMAGE_FAILED_TEXT)


class ChatCandidateSatisfactionServiceTest(unittest.IsolatedAsyncioTestCase):
    """命令层：查询结果只走一遍判定，帮助与列表不触发上游。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "commands.sqlite3")
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.calls: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok",
                "data": {"date": "2026-11-16", "news": ["新闻一"]},
            }).encode())

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.snapshots = ImageSnapshotStore()
        self.image_service = ProfileImageService()
        site = await self.sites.create_site(SiteCreate(
            name="命令站", base_url="http://o.example.test/60s",
            enabled=True, timeout_seconds=5,
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="命令任务",
            items=[
                {"content_type": "daily_news", "params": {}, "limit": None},
            ],
            sites=[site.id],
            output_format="text", umos=[UMO],
        ))
        self.service = ChatCommandService(
            self.tasks, self.sites,
            content_query=ChatContentQuery(self.tasks, PlanPreview(
                self.tasks, self.content, self.sites,
                image_snapshots=self.snapshots,
                image_service=self.image_service,
            )),
        )

    async def asyncTearDown(self) -> None:
        await self.image_service.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def test_help_and_list_do_not_touch_upstream(self) -> None:
        await self.service.handle(UMO, "/60s help")
        await self.service.handle(UMO, "/60s list")
        self.assertEqual(self.calls, [])

    async def test_query_hits_upstream_once(self) -> None:
        text = await self.service.handle(UMO, "/60s get " + self.task.id)
        self.assertIn("新闻一", text)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
