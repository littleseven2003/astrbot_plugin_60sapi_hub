from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from core.candidate_preview import attempt_timeout_for
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.delivery import (
    DeliveryLimits,
    DeliveryMessage,
    DeliverySendResult,
    DeliveryService,
    SendStatus,
)
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"
UMO_SECOND = "aiocqhttp:GroupMessage:g:2"
PNG = b"\x89PNG\r\n\x1a\n" + b"slow-site-fake"


class RecordingSender:
    """有非零耗时的假发送器：把发送阶段的耗时做成可数的秒数。"""

    def __init__(self) -> None:
        self.messages: list[DeliveryMessage] = []
        self.sleep_seconds = 0.0
        self.calls = 0

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.calls += 1
        self.messages.append(message)
        if self.sleep_seconds:
            await asyncio.sleep(self.sleep_seconds)
        return DeliverySendResult(status=SendStatus.SUCCESS)


class SlowImageRenderer:
    """真实耗时但内容确定的图片渲染器，替代字体排版。"""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    async def render(self, data, *, created_at=None):
        self.calls += 1
        if self.seconds:
            await asyncio.sleep(self.seconds)
        images = []
        for position, section in enumerate(data.get("sections", []), start=1):
            images.append(SimpleNamespace(
                index=position,
                content_type=section.get("content_type", "daily_news"),
                label=section.get("label", "栏目"),
                status="success",
                width=600,
                height=800,
                png=PNG,
                error=None,
            ))
        return images



class SlowCandidateDeliveryTest(unittest.IsolatedAsyncioTestCase):
    """两段预算：内容准备按候选等份，发送另有独立预算。

    上游用 httpx.MockTransport 模拟：slow.* 主机的请求长时间不返回，
    delays 给“慢但最终可用”的站点真实耗时，failing 让候选快速不满足；
    站点超时填 5 秒（真实站点的默认量级），内容预算压到 2 秒来复现边界，
    不需要等真实的 60 秒。
    """

    BUDGET = 2.0
    SLOW_SECONDS = 10.0
    SEND_BUDGET = 5.0

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "slow.sqlite3"
        self.hosts: list[str] = []
        self.slow_sites: set[str] = set()
        # 主机 -> 该站每次上游请求的耗时（秒）：模拟“慢但最终可用”的站点。
        self.delays: dict[str, float] = {}
        # 主机 -> 直接 5xx：模拟快速失败、拿不到内容的候选。
        self.failing: set[str] = {"broken.example.test"}

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            if host in self.failing:
                return httpx.Response(500)
            if host in self.slow_sites:
                await asyncio.sleep(self.SLOW_SECONDS)
                return httpx.Response(504)
            if host in self.delays:
                await asyncio.sleep(self.delays[host])
            if request.url.path.endswith("/v2/60s"):
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-11-26",
                             "news": ["快新闻一", "快新闻二", "快新闻三"]},
                }).encode())
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok",
                "data": [{"title": "快热榜一", "hot_value": 1},
                         {"title": "快热榜二", "hot_value": 2}],
            }).encode())

        self.sites = SiteService(self.path)
        await self.sites.open()
        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.tasks = PushTaskService(self.sites.store)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.sender = RecordingSender()
        self.delivery = self._make_delivery(self.BUDGET)
        self.slow = await self.sites.create_site(SiteCreate(
            name="慢站点", base_url="https://slow.example.test/api",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.fast = await self.sites.create_site(SiteCreate(
            name="快站点", base_url="https://fast.example.test/api",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.broken = await self.sites.create_site(SiteCreate(
            name="坏站点", base_url="https://broken.example.test/api",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.slow_sites = {"slow.example.test"}

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    def _make_delivery(
        self, budget: float, *, send: float | None = None,
    ) -> DeliveryService:
        return DeliveryService(
            tasks=self.tasks,
            sites=self.sites,
            plan_preview=self.api,
            store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(
                max_execution_seconds=budget,
                max_send_seconds=self.SEND_BUDGET if send is None else send,
            ),
        )

    async def _task(
        self, sites, *, items=("daily_news", "zhihu_hot"), limit: int = 2,
        umos=(UMO,), output_format: str = "text",
    ):
        # 栏目方案是任务自身的一部分：单栏目时一次上游请求耗时就是整次
        # 预览耗时，便于按秒数验证预算。
        return await self.tasks.create_task(TaskCreate(
            name="任务",
            items=[
                {"content_type": content_type, "params": {}, "limit": limit}
                for content_type in items
            ],
            output_format=output_format,
            umos=list(umos), sites=list(sites),
        ))

    async def test_slow_first_candidate_leaves_room_for_backup(self) -> None:
        task = await self._task([self.slow.id, self.fast.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "slow-exec-1")
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, self.BUDGET, "投递应在内容预算内拿到内容")
        # 慢站点确实被尝试过，而不是被直接跳过。
        self.assertIn("slow.example.test", self.hosts)
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("快新闻一", text)
        self.assertIn("快热榜一", text)
        self.assertNotIn("60sAPI推送服务异常", text)
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotEqual(record.status, "failed")

    async def test_backup_site_with_real_delay_takes_over(self) -> None:
        # 首站挂住吃掉自己那一份（约 1 秒），备用站两份栏目请求各 0.3 秒
        # 仍要落在内容预算内，并且全部栏目都来自备用站。
        self.delays = {"fast.example.test": 0.3}
        task = await self._task([self.slow.id, self.fast.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "slow-exec-backup")
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 1.5, "备用站的真实耗时不能被跳过")
        self.assertLess(elapsed, self.BUDGET + self.SEND_BUDGET)
        self.assertEqual(record.site_id, self.fast.id)
        self.assertEqual(record.status, "success")
        self.assertEqual(self.hosts.count("fast.example.test"), 2)
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("快新闻一", text)
        self.assertIn("快热榜一", text)
        self.assertNotIn("60sAPI推送服务异常", text)

    async def test_single_slow_but_usable_site_delivers(self) -> None:
        # 唯一候选拿到整段内容预算：1.2 秒的内容获取在 2 秒预算内可用。
        self.delays = {"fast.example.test": 1.2}
        task = await self._task([self.fast.id], items=("daily_news",))
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "single-slow-site")
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 1.2)
        self.assertLess(elapsed, self.BUDGET + self.SEND_BUDGET)
        self.assertEqual(record.status, "success")
        self.assertEqual(record.site_id, self.fast.id)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("快新闻一", self.sender.messages[0].text or "")

    async def test_last_candidate_can_use_all_remaining_content_budget(self) -> None:
        # 首个候选快速失败后，最后一个候选独占剩余预算：1.2 秒仍可用，
        # 而不是被“固定对半（1.0 秒）”提前判为不可用。
        self.delays = {"fast.example.test": 1.2}
        task = await self._task(
            [self.broken.id, self.fast.id], items=("daily_news",),
        )
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "last-candidate")
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 1.2)
        self.assertLess(elapsed, self.BUDGET + self.SEND_BUDGET)
        self.assertEqual(record.site_id, self.fast.id)
        self.assertEqual(record.status, "success")
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("快新闻一", self.sender.messages[0].text or "")

    async def test_all_candidates_slow_sends_safe_alert(self) -> None:
        # 告警发送走发送预算：0.35 秒的发送不会被耗尽的内容预算取消。
        self.slow_sites = {"slow.example.test", "fast.example.test"}
        self.sender.sleep_seconds = 0.35
        self.delivery = self._make_delivery(self.BUDGET, send=1.0)
        task = await self._task([self.slow.id, self.fast.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "slow-exec-2")
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, self.BUDGET * 0.9)
        self.assertLess(elapsed, self.BUDGET + 1.0)
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertTrue(text.startswith("60sAPI推送服务异常："), text)
        self.assertEqual(record.content_status, "failed")
        self.assertEqual(self.sender.calls, 1)

    async def test_empty_whitelist_sends_nothing_even_when_slow(self) -> None:
        task = await self._task([self.slow.id], umos=())
        record = await self.delivery.execute(task.id, "slow-exec-3")
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(record.status, "no_targets")

    async def test_single_candidate_may_use_almost_all_remaining_time(self) -> None:
        # 唯一站点 0.65 秒就返回满足条件的内容：内容预算 1 秒全部给它。
        self.delays = {"fast.example.test": 0.65}
        self.delivery = self._make_delivery(1.0)
        task = await self._task([self.fast.id], items=("daily_news",))
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "single-budget")
        elapsed = time.monotonic() - started

        self.assertGreaterEqual(elapsed, 0.65)
        self.assertLess(elapsed, 1.0 + self.SEND_BUDGET)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("快新闻一", self.sender.messages[0].text or "")
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotEqual(record.status, "failed")

    async def test_candidate_order_prefers_first_satisfying_site(self) -> None:
        task = await self._task([self.fast.id, self.slow.id])
        record = await self.delivery.execute(task.id, "slow-exec-4")
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotIn("slow.example.test", self.hosts[-1:] or [""])

    async def test_image_sections_fit_in_send_budget(self) -> None:
        # 两个栏目图各发两个目标：4 次 x 0.4 秒远超内容预算 1 秒，
        # 发送必须由发送预算接住，栏目图生成也在这之前完成。
        renderer = SlowImageRenderer(0.2)
        api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=renderer,
        )
        self.sender.sleep_seconds = 0.4
        service = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=api,
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(
                max_images=2, max_execution_seconds=1.0, max_send_seconds=2.0,
            ),
        )
        try:
            task = await self._task(
                [self.fast.id], output_format="image",
                umos=(UMO, UMO_SECOND),
            )
            started = time.monotonic()
            record = await service.execute(task.id, "image-send-budget")
            elapsed = time.monotonic() - started
        finally:
            await service.close()

        self.assertEqual(renderer.calls, 1)
        self.assertEqual(len(self.sender.messages), 4)
        self.assertGreaterEqual(elapsed, 1.6, "发送耗时不受内容预算限制")
        self.assertLess(elapsed, 3.0)
        self.assertEqual(record.status, "success")
        self.assertEqual(
            [message.page_index for message in self.sender.messages],
            [1, 2, 1, 2],
        )
        for message in self.sender.messages:
            self.assertTrue((message.image_png or b"").startswith(b"\x89PNG"))

    async def test_send_budget_timeout_marks_unknown_without_resend(self) -> None:
        # 发送预算 0.2 秒、假发送器 0.5 秒：第一阶段超时记 unknown，
        # 预算耗尽后第二个目标不再调用发送器，重复执行也不重发。
        self.delivery = self._make_delivery(5.0, send=0.2)
        self.sender.sleep_seconds = 0.5
        task = await self._task(
            [self.fast.id], items=("daily_news",), umos=(UMO, UMO_SECOND),
        )
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "send-timeout-1")
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0)
        self.assertEqual(record.status, "unknown")
        self.assertEqual(
            [page.status for page in record.pages], ["unknown", "unknown"],
        )
        # 第一个目标真的调用了发送器后超时；第二个目标在预算耗尽后没有
        # 调用发送器，错误码必须能区分这两种情况。
        self.assertEqual(
            [page.error_code for page in record.pages],
            ["sender_timeout", "sender_budget_exhausted"],
        )
        self.assertEqual(self.sender.calls, 1, "预算耗尽后不再调用发送器")

        again = await self.delivery.execute(task.id, "send-timeout-1")
        self.assertEqual(again.status, "unknown")
        self.assertEqual(self.sender.calls, 1, "结果未知的投递不得自动重发")
        self.assertEqual(len(self.sender.messages), 1)


    async def test_slow_section_images_stop_at_content_budget(self) -> None:
        # 栏目图生成属于内容准备：渲染 2 秒、内容预算 0.5 秒时必须在预算内
        # 结束，记为明确失败，且一次发送都不发起。
        renderer = SlowImageRenderer(2.0)
        api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=renderer,
        )
        service = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=api,
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(
                max_execution_seconds=0.5, max_send_seconds=1.0,
            ),
        )
        try:
            task = await self._task([self.fast.id], output_format="image")
            started = time.monotonic()
            record = await service.execute(task.id, "image-content-budget")
            elapsed = time.monotonic() - started
        finally:
            await service.close()

        self.assertEqual(renderer.calls, 1)
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertLess(elapsed, 1.0, "栏目图生成必须受内容预算约束")
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.error_code, "execution_timeout")
        self.assertEqual(self.sender.calls, 0, "内容准备超时不得发送任何消息")

    async def test_render_that_fills_content_budget_still_gets_send_budget(
        self,
    ) -> None:
        # 渲染几乎用掉整段内容预算（0.8 / 1.0 秒）后，发送仍有自己的预算：
        # 两次各 0.4 秒的发送必须完成，不能被内容预算剩下的时间截断。
        renderer = SlowImageRenderer(0.8)
        api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=renderer,
        )
        self.sender.sleep_seconds = 0.4
        service = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=api,
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(
                max_images=2, max_execution_seconds=1.0, max_send_seconds=1.0,
            ),
        )
        try:
            task = await self._task([self.fast.id], output_format="image")
            started = time.monotonic()
            record = await service.execute(task.id, "image-budget-handover")
            elapsed = time.monotonic() - started
        finally:
            await service.close()

        self.assertEqual(renderer.calls, 1)
        self.assertEqual(len(self.sender.messages), 2)
        self.assertGreaterEqual(elapsed, 1.2)
        self.assertEqual(record.status, "success")
        self.assertEqual(
            [page.status for page in record.pages], ["success", "success"],
        )

    async def test_text_and_markdown_never_wait_for_image_timeout(self) -> None:
        # 文字与 Markdown 不经过图片渲染：即使渲染器要 5 秒也不影响它们。
        renderer = SlowImageRenderer(5.0)
        api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=renderer,
        )
        self.delays = {"fast.example.test": 0.3}
        service = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=api,
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(
                max_execution_seconds=1.0, max_send_seconds=1.0,
            ),
        )
        try:
            for index, output_format in enumerate(("text", "markdown"), start=1):
                self.sender.messages = []
                task = await self._task(
                    [self.fast.id], items=("daily_news",),
                    output_format=output_format,
                )
                record = await service.execute(
                    task.id, "text-ignores-image-" + str(index),
                )
                self.assertEqual(record.status, "success")
                self.assertEqual(record.error_code, None)
                self.assertEqual(len(self.sender.messages), 1)
                self.assertIn("快新闻一", self.sender.messages[0].text or "")
        finally:
            await service.close()
        self.assertEqual(renderer.calls, 0)


class PreviewAttemptTimeoutTest(unittest.TestCase):
    """单站尝试预算：内容准备阶段的剩余时间在未试候选之间等份。"""

    def test_single_candidate_gets_whole_remaining_budget(self) -> None:
        self.assertEqual(attempt_timeout_for(60.0, 0.0), 60.0)
        self.assertEqual(attempt_timeout_for(1.0, 0.0), 1.0)

    def test_candidates_split_remaining_equally(self) -> None:
        self.assertEqual(attempt_timeout_for(2.0, 0.0, remaining_candidates=2), 1.0)
        self.assertEqual(
            attempt_timeout_for(2.0, 0.0, remaining_candidates=3), 2.0 / 3,
        )
        self.assertEqual(attempt_timeout_for(1.0, 0.0, remaining_candidates=2), 0.5)

    def test_attempt_equals_its_share(self) -> None:
        self.assertEqual(attempt_timeout_for(0.4, 0.0), 0.4)
        self.assertEqual(attempt_timeout_for(0.05, 0.0), 0.05)

    def test_expired_deadline_yields_no_wait(self) -> None:
        self.assertEqual(attempt_timeout_for(1.0, 5.0), 0.0)

    def test_no_deadline_keeps_unbounded_attempt(self) -> None:
        self.assertIsNone(attempt_timeout_for(None))
