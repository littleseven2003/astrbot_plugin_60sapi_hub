from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

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
from core.profile_api import ProfileApi
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[DeliveryMessage] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.messages.append(message)
        return DeliverySendResult(status=SendStatus.SUCCESS)


class SlowCandidateDeliveryTest(unittest.IsolatedAsyncioTestCase):
    """慢的首选站点不能把投递总时限耗尽，备用站点仍要能接上。

    上游用 httpx.MockTransport 模拟：slow.* 主机的请求长时间不返回，
    站点超时填 5 秒（真实站点的默认量级），投递总时限压到 2 秒来复现
    “总时限提前结束”。不需要等真实的 60 秒。
    """

    BUDGET = 2.0
    SLOW_SECONDS = 10.0

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "slow.sqlite3"
        self.hosts: list[str] = []
        self.slow_sites: set[str] = set()

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            if host in self.slow_sites:
                await asyncio.sleep(self.SLOW_SECONDS)
                return httpx.Response(504)
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
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.api = ProfileApi(
            self.profiles, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.sender = RecordingSender()
        self.delivery = DeliveryService(
            tasks=self.tasks,
            profiles=self.profiles,
            sites=self.sites,
            profile_api=self.api,
            store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(max_execution_seconds=self.BUDGET),
        )
        self.slow = await self.sites.create_site(SiteCreate(
            name="慢站点", base_url="https://slow.example.test/api",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.fast = await self.sites.create_site(SiteCreate(
            name="快站点", base_url="https://fast.example.test/api",
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

    async def _profile(self, limit: int = 2):
        return await self.profiles.create_profile(ProfileCreate(
            name="组合",
            items=[
                {"content_type": "daily_news", "params": {}, "limit": limit},
                {"content_type": "zhihu_hot", "params": {}, "limit": limit},
            ],
        ))

    async def _task(self, profile_id: str, sites, *, umos=(UMO,)):
        return await self.tasks.create_task(TaskCreate(
            name="任务", profile_id=profile_id, output_format="text",
            umos=list(umos), sites=list(sites),
        ))

    async def test_slow_first_candidate_leaves_room_for_backup(self) -> None:
        profile = await self._profile()
        task = await self._task(profile.id, [self.slow.id, self.fast.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "slow-exec-1")
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, self.BUDGET, "投递应在总时限内完成")
        # 慢站点确实被尝试过，而不是被直接跳过。
        self.assertIn("slow.example.test", self.hosts)
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("快新闻一", text)
        self.assertIn("快热榜一", text)
        self.assertNotIn("60sAPI推送服务异常", text)
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotEqual(record.status, "failed")

    async def test_all_candidates_slow_sends_safe_alert(self) -> None:
        self.slow_sites = {"slow.example.test", "fast.example.test"}
        profile = await self._profile()
        task = await self._task(profile.id, [self.slow.id, self.fast.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "slow-exec-2")
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, self.BUDGET, "全部超时也要在总时限内收场")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertTrue(text.startswith("60sAPI推送服务异常："), text)
        self.assertEqual(record.content_status, "failed")

    async def test_empty_whitelist_sends_nothing_even_when_slow(self) -> None:
        profile = await self._profile()
        task = await self._task(profile.id, [self.slow.id], umos=())
        record = await self.delivery.execute(task.id, "slow-exec-3")
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(record.status, "no_targets")

    async def test_candidate_order_prefers_first_satisfying_site(self) -> None:
        profile = await self._profile()
        task = await self._task(profile.id, [self.fast.id, self.slow.id])
        record = await self.delivery.execute(task.id, "slow-exec-4")
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotIn("slow.example.test", self.hosts[-1:] or [""])


class PreviewAttemptTimeoutTest(unittest.TestCase):
    """单站尝试预算：只看剩余时间的一半，且永不超过剩余时间。"""

    def test_half_of_remaining_time_is_granted(self) -> None:
        self.assertEqual(attempt_timeout_for(60.0, 0.0), 30.0)
        self.assertEqual(attempt_timeout_for(30.0, 0.0), 15.0)

    def test_short_remaining_time_keeps_no_floor(self) -> None:
        # 剩余不足两个最小尝试时长：不再保底 1 秒，否则第二个候选就无预算。
        self.assertEqual(attempt_timeout_for(1.0, 0.0), 0.5)
        self.assertLessEqual(attempt_timeout_for(0.4, 0.0), 0.4)

    def test_floor_keeps_slow_site_meaningful_while_budget_allows(self) -> None:
        self.assertEqual(attempt_timeout_for(2.0, 0.0), 1.0)
        self.assertEqual(attempt_timeout_for(1.99, 0.0), 0.995)

    def test_expired_deadline_yields_no_wait(self) -> None:
        self.assertEqual(attempt_timeout_for(1.0, 5.0), 0.0)

    def test_no_deadline_keeps_unbounded_attempt(self) -> None:
        self.assertIsNone(attempt_timeout_for(None))
