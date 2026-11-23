from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.delivery_api import DeliveryApi
from core.scheduler import SchedulerService, SchedulerValidationError
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"


class _Inspector:
    def inspect_target(self, umo: str) -> dict:
        return {
            "umo": umo, "platform_id": "aiocqhttp", "message_type": "GroupMessage",
            "target": "g:1", "isolated": True, "valid": True, "reason": None,
        }


class _DeliveryStub:
    def supports_umo(self, umo: object) -> bool:
        return True


class CandidateChainTest(unittest.IsolatedAsyncioTestCase):
    """候选站点状态要贯穿任务快照、调度校验与手动确认。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "chain.sqlite3"
        self.sites = SiteService(self.path)
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.a = await self.sites.create_site(SiteCreate(
            name="A", base_url="https://a.example.test/p", timeout_seconds=5, source_kind="public",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="B", base_url="https://b.example.test/p", timeout_seconds=5, source_kind="public",
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="任务",
            items=[{"content_type": "daily_news", "params": {}, "limit": 1}],
            output_format="text",
            umos=[UMO], sites=[self.a.id, self.b.id],
            schedule={"kind": "daily", "timezone": "UTC", "time": "09:00"},
        ))

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._tmp.cleanup()

    async def test_snapshot_keeps_candidate_order_and_revisions(self) -> None:
        snapshot = await self.tasks.get_config_snapshot(self.task.id)
        self.assertEqual(
            [item.id for item in snapshot.candidates],
            [self.a.id, self.b.id],
        )
        self.assertTrue(all(isinstance(item.revision, int) for item in snapshot.candidates))

    async def test_scheduler_accepts_task_with_candidates(self) -> None:
        snapshot = await self.tasks.get_config_snapshot(self.task.id)
        scheduler = object.__new__(SchedulerService)
        scheduler._delivery = _DeliveryStub()
        # 站点级启用已移除：候选存在即可启用，取数失败交给投递层发异常提示。
        scheduler._validate_snapshot(snapshot)

    async def test_manual_preview_lists_candidates_and_allows_delivery(
        self,
    ) -> None:
        api = DeliveryApi(
            delivery=None, tasks=self.tasks,
            sites=self.sites, store=self.sites.store, inspector=_Inspector(),
        )
        result = await api.preview(self.task.id)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertTrue(data["can_deliver"])
        self.assertIsNone(data["reason"])
        self.assertEqual(
            [item["id"] for item in data["candidate_sites"]],
            [self.a.id, self.b.id],
        )
        self.assertNotIn("enabled", data["candidate_sites"][0])

    async def test_enable_task_with_existing_candidates(self) -> None:
        scheduler = SchedulerService(
            tasks=self.tasks, store=self.sites.store, delivery=_DeliveryStub(),
        )
        try:
            await scheduler.enable_task(self.task.id, self.task.revision)
            updated = await self.tasks.get_task(self.task.id)
            self.assertTrue(updated.enabled)
        finally:
            await scheduler.close()

if __name__ == "__main__":
    unittest.main()
