from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

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
from core.scheduler import SchedulerService
from core.sites import SiteCreate, SiteUpdate, SiteService
from core.tasks import PushTaskService, TaskCreate

SECRET = "simulated-secret-abc123"
UMO = "aiocqhttp:GroupMessage:g:1"


class RecordingSender:
    def __init__(self, status: SendStatus = SendStatus.SUCCESS) -> None:
        self.messages: list[DeliveryMessage] = []
        self.status = status

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        if message.kind == "image" and message.image_png is None:
            # 真实适配器不会接受缺少 PNG 的 image 消息。
            raise AssertionError("image 消息缺少 PNG")
        self.messages.append(message)
        return DeliverySendResult(status=self.status)


class SchedulerAlertChainTest(unittest.IsolatedAsyncioTestCase):
    """真实调度链：到期执行 → 投递 → 异常提示或内容。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "sched.sqlite3"
        self.now = datetime(2026, 3, 2, 0, 30, tzinfo=timezone.utc)
        # 站点级启用已移除；用上游失败模拟“该候选本次不可用”。
        self.fail_hosts: set[str] = set()

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host in self.fail_hosts:
                return httpx.Response(500, content=b"upstream down")
            rich = host.startswith("b.")
            if request.url.path.endswith("/v2/60s"):
                news = ["B新闻一", "B新闻二"] if rich else ["A新闻一"]
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-03-02", "news": news},
                }).encode())
            hot = (
                [{"title": "B热榜一", "hot_value": 1},
                 {"title": "B热榜二", "hot_value": 2}]
                if rich else [{"title": "A热榜一", "hot_value": 1}]
            )
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok", "data": hot,
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
        self.delivery = DeliveryService(
            tasks=self.tasks, sites=self.sites,
            plan_preview=self.api, store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(),
        )
        self.scheduler = SchedulerService(
            tasks=self.tasks, store=self.sites.store, delivery=self.delivery,
            clock=lambda: self.now,
        )
        self.a = await self.sites.create_site(SiteCreate(
            name="A", base_url=f"https://a.example.test/{SECRET}",
            timeout_seconds=5, source_kind="public",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="B", base_url=f"https://b.example.test/{SECRET}",
            timeout_seconds=5, source_kind="public",
        ))

    async def asyncTearDown(self) -> None:
        await self.scheduler.close()
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _disable(self, site) -> None:
        """兼容旧用例名：表示该候选上游失败，而不是站点停用。"""
        host = (await self.sites.get_site(site.id)).base_url.split("/")[2]
        self.fail_hosts.add(host)

    async def _task(self, sites, *, limit=2, fmt="text", umos=(UMO,)):
        task = await self.tasks.create_task(TaskCreate(
            name="任务", output_format=fmt,
            items=[
                {"content_type": "daily_news", "params": {}, "limit": limit},
                {"content_type": "zhihu_hot", "params": {}, "limit": limit},
            ],
            umos=list(umos), sites=list(sites),
            schedule={"kind": "daily", "timezone": "UTC", "time": "00:30"},
        ))
        await self.scheduler.enable_task(task.id, task.revision)
        # 把时钟推进到刚过计划时刻，让下一次 check_once 真的到期。
        states = await self.sites.store.list_schedule_states()
        state = next(item for item in states if item.task_id == task.id)
        due = datetime.fromisoformat(state.next_run_at)
        self.now = due.replace(microsecond=0)
        return await self.tasks.get_task(task.id)

    async def test_all_candidates_disabled_still_sends_alert(self) -> None:
        task = await self._task([self.a.id, self.b.id])
        await self._disable(self.a)
        await self._disable(self.b)
        await self.scheduler.check_once()
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        self.assertEqual(message.kind, "text")
        self.assertEqual(message.umo, UMO)
        self.assertTrue((message.text or "").startswith("60sAPI推送服务异常："))

    async def test_task_site_disabled_and_b_insufficient_sends_alert(self) -> None:
        task = await self._task([self.a.id, self.b.id], limit=5)
        await self._disable(self.a)
        await self.scheduler.check_once()
        self.assertEqual(len(self.sender.messages), 1)
        self.assertTrue(
            (self.sender.messages[0].text or "").startswith("60sAPI推送服务异常："),
        )

    async def test_scheduled_run_uses_second_candidate(self) -> None:
        task = await self._task([self.a.id, self.b.id])
        await self._disable(self.a)
        await self.scheduler.check_once()
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("B新闻", text)
        self.assertNotIn("60sAPI推送服务异常", text)

    async def test_image_alert_on_schedule_is_text(self) -> None:
        task = await self._task([self.a.id], limit=9, fmt="image")
        await self.scheduler.check_once()
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        self.assertEqual(message.kind, "text")
        self.assertIsNone(message.image_png)


    async def test_alert_claim_marks_content_failure(self) -> None:
        task = await self._task([self.a.id])
        await self._disable(self.a)
        await self.scheduler.check_once()
        claims = await self.sites.store.list_schedule_claims(limit=10, offset=0)
        claim = next(item for item in claims if item.task_id == task.id)
        # 提示送达，但内容采集失败：计划记录必须带上这个标记。
        self.assertEqual(claim.status, "succeeded")
        self.assertEqual(claim.error_code, "content_unavailable")

    async def test_unknown_send_keeps_claim_unknown(self) -> None:
        self.sender.status = SendStatus.UNKNOWN
        task = await self._task([self.a.id], limit=9)
        await self.scheduler.check_once()
        claims = await self.sites.store.list_schedule_claims(limit=10, offset=0)
        claim = next(item for item in claims if item.task_id == task.id)
        self.assertEqual(claim.status, "unknown")
        # 不自动重试：再次扫描不会重复发送。
        sent = len(self.sender.messages)
        self.now = self.now.replace(hour=1)
        await self.scheduler.check_once()
        self.assertEqual(len(self.sender.messages), sent)


if __name__ == "__main__":
    unittest.main()
