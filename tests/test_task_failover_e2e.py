from __future__ import annotations

import json
import tempfile
import unittest
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
from core.profile_api import ProfileApi
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteUpdate, SiteService
from core.tasks import PushTaskService, TaskCreate

SECRET = "simulated-secret-abc123"
UMO = "aiocqhttp:GroupMessage:g:1"


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[DeliveryMessage] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.messages.append(message)
        return DeliverySendResult(status=SendStatus.SUCCESS)


class TaskFailoverEndToEndTest(unittest.IsolatedAsyncioTestCase):
    """真实 DeliveryService + 模拟上游 + 记录型发送器。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "e2e.sqlite3"
        self.hosts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            rich = host.startswith("b.")
            if request.url.path.endswith("/v2/60s"):
                news = (
                    ["B新闻一", "B新闻二", "B新闻三"] if rich else ["A新闻一"]
                )
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-11-26", "news": news},
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
            limits=DeliveryLimits(),
        )
        self.a = await self.sites.create_site(SiteCreate(
            name="站点A", base_url=f"https://a.example.test/{SECRET}",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="站点B", base_url=f"https://b.example.test/{SECRET}",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _profile(self, site_id: str, limit: int = 2):
        return await self.profiles.create_profile(ProfileCreate(
            name="组合", site_id=site_id,
            items=[
                {"content_type": "daily_news", "params": {}, "limit": limit},
                {"content_type": "zhihu_hot", "params": {}, "limit": limit},
            ],
        ))

    async def _task(self, profile_id: str, sites, *, fmt: str = "text",
                    umos=(UMO,)):
        return await self.tasks.create_task(TaskCreate(
            name="任务", profile_id=profile_id, output_format=fmt,
            umos=list(umos), sites=list(sites),
        ))

    async def test_insufficient_first_site_falls_back_to_second(self) -> None:
        profile = await self._profile(self.a.id, limit=2)
        task = await self._task(profile.id, [self.a.id, self.b.id])
        self.hosts.clear()
        record = await self.delivery.execute(task.id, "exec-1")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("B新闻", text)
        self.assertIn("B热榜", text)
        self.assertNotIn("A新闻", text)
        # 记录反映实际使用的站点。
        self.assertEqual(record.site_id, self.b.id)

    async def test_only_candidate_b_is_used_even_if_profile_binds_a(self) -> None:
        profile = await self._profile(self.a.id, limit=2)
        task = await self._task(profile.id, [self.b.id])
        record = await self.delivery.execute(task.id, "exec-2")
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("B新闻", self.sender.messages[0].text or "")
        self.assertEqual(record.site_id, self.b.id)
        self.assertNotEqual(record.status, "failed")

    async def test_all_candidates_failing_sends_safe_alert(self) -> None:
        profile = await self._profile(self.a.id, limit=5)
        task = await self._task(profile.id, [self.a.id, self.b.id])
        record = await self.delivery.execute(task.id, "exec-3")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertTrue(text.startswith("60sAPI推送服务异常："), text)
        self.assertIsNone(self.sender.messages[0].image_png)
        # 记录能区分“内容采集失败”。
        self.assertEqual(record.content_status, "failed")

    async def test_image_task_alert_is_sent_as_text(self) -> None:
        profile = await self._profile(self.a.id, limit=9)
        task = await self._task(profile.id, [self.a.id], fmt="image")
        await self.delivery.execute(task.id, "exec-4")
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        self.assertIsNone(message.image_png)
        self.assertTrue((message.text or "").startswith("60sAPI推送服务异常："))

    async def test_empty_whitelist_sends_nothing(self) -> None:
        profile = await self._profile(self.a.id, limit=2)
        task = await self._task(profile.id, [self.b.id], umos=())
        await self.delivery.execute(task.id, "exec-5")
        self.assertEqual(self.sender.messages, [])

    async def test_disabled_candidate_is_skipped(self) -> None:
        profile = await self._profile(self.a.id, limit=2)
        task = await self._task(profile.id, [self.a.id, self.b.id])
        current = await self.sites.get_site(self.a.id)
        await self.sites.update_site(self.a.id, SiteUpdate(
            name=current.name, base_url=current.base_url, enabled=False,
            timeout_seconds=5, source_kind=current.source_kind,
            expected_revision=current.revision,
        ))
        record = await self.delivery.execute(task.id, "exec-6")
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("B新闻", self.sender.messages[0].text or "")
        self.assertEqual(record.site_id, self.b.id)


if __name__ == "__main__":
    unittest.main()
