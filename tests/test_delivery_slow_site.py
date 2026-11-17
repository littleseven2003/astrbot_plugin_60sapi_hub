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
    “总时限提前结束”；delays 让站点慢但仍可用。不需要等真实的 60 秒。
    """

    BUDGET = 2.0
    SLOW_SECONDS = 10.0

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "slow.sqlite3"
        self.hosts: list[str] = []
        self.slow_sites: set[str] = set()
        # 主机 -> 该站每次上游请求的耗时（秒）：模拟“慢但最终可用”的站点。
        self.delays: dict[str, float] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
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
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.api = ProfileApi(
            self.profiles, self.content, self.sites,
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
        self.slow_sites = {"slow.example.test"}

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    def _make_delivery(self, budget: float) -> DeliveryService:
        return DeliveryService(
            tasks=self.tasks,
            profiles=self.profiles,
            sites=self.sites,
            profile_api=self.api,
            store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(max_execution_seconds=budget),
        )

    async def _news_profile(self, limit: int = 2):
        # 单栏目组合：一次上游请求耗时就是整次预览耗时，便于按秒数验证预算。
        return await self.profiles.create_profile(ProfileCreate(
            name="单栏目组合",
            items=[
                {"content_type": "daily_news", "params": {}, "limit": limit},
            ],
        ))

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

    async def test_single_candidate_may_use_almost_all_remaining_time(self) -> None:
        # 复现：总预算 1 秒、唯一站点 0.65 秒就返回满足条件的内容，不能被
        # “固定对半（0.5 秒）”提前判为不可用。
        self.delays = {"fast.example.test": 0.65}
        self.delivery = self._make_delivery(1.0)
        profile = await self._news_profile()
        task = await self._task(profile.id, [self.fast.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "single-budget")
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.0, "仍要给发送留出时间")
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("快新闻一", self.sender.messages[0].text or "")
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotEqual(record.status, "failed")

    async def test_first_candidate_slower_than_half_budget_is_still_used(self) -> None:
        # 总预算 2 秒、两个候选：首站 1.2 秒返回也属于正常可用，不应被
        # 固定对半（1.0 秒）跳过而落到后面的候选。
        self.delays = {"fast.example.test": 1.2}
        profile = await self._news_profile()
        task = await self._task(profile.id, [self.fast.id, self.slow.id])
        started = time.monotonic()
        record = await self.delivery.execute(task.id, "half-budget")
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, self.BUDGET)
        self.assertEqual(record.site_id, self.fast.id)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("快新闻一", self.sender.messages[0].text or "")

    async def test_candidate_order_prefers_first_satisfying_site(self) -> None:
        profile = await self._profile()
        task = await self._task(profile.id, [self.fast.id, self.slow.id])
        record = await self.delivery.execute(task.id, "slow-exec-4")
        self.assertEqual(record.site_id, self.fast.id)
        self.assertNotIn("slow.example.test", self.hosts[-1:] or [""])


class PreviewAttemptTimeoutTest(unittest.TestCase):
    """单站尝试预算：只预留发送与后续候选的底线，且永不越过剩余时间。"""

    def test_single_candidate_uses_almost_all_remaining_time(self) -> None:
        self.assertEqual(attempt_timeout_for(60.0, 0.0), 59.75)
        # 1 秒总预算留 0.25 秒发送：0.65 秒返回的站点仍可用。
        self.assertEqual(attempt_timeout_for(1.0, 0.0), 0.75)

    def test_later_candidates_keep_their_share(self) -> None:
        # 两个候选：给首站留下发送预留 + 后续候选的底线。
        self.assertEqual(attempt_timeout_for(2.0, 0.0, remaining_candidates=2), 1.25)
        # 预留最多占剩余时间的一半，候选再多也不把首站压到一半以下。
        self.assertEqual(attempt_timeout_for(2.0, 0.0, remaining_candidates=3), 1.0)
        self.assertEqual(attempt_timeout_for(1.0, 0.0, remaining_candidates=2), 0.5)

    def test_attempt_never_exceeds_remaining_time(self) -> None:
        self.assertEqual(attempt_timeout_for(0.4, 0.0), 0.2)
        self.assertLess(attempt_timeout_for(0.05, 0.0), 0.05)

    def test_expired_deadline_yields_no_wait(self) -> None:
        self.assertEqual(attempt_timeout_for(1.0, 5.0), 0.0)

    def test_no_deadline_keeps_unbounded_attempt(self) -> None:
        self.assertIsNone(attempt_timeout_for(None))
