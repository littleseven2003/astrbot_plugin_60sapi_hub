from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.delivery_api import DeliveryApi
from core.profiles import ContentProfileService, ProfileCreate
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
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.a = await self.sites.create_site(SiteCreate(
            name="A", base_url="https://a.example.test/p", enabled=True,
            timeout_seconds=5, source_kind="public",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="B", base_url="https://b.example.test/p", enabled=True,
            timeout_seconds=5, source_kind="public",
        ))
        self.profile = await self.profiles.create_profile(ProfileCreate(
            name="组合", site_id=self.a.id,
            items=[{"content_type": "daily_news", "params": {}, "limit": 1}],
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="任务", profile_id=self.profile.id, output_format="text",
            umos=[UMO], sites=[self.a.id, self.b.id],
            schedule={"kind": "daily", "timezone": "UTC", "time": "09:00"},
        ))

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._tmp.cleanup()

    async def _disable(self, site) -> None:
        from core.sites import SiteUpdate

        current = await self.sites.get_site(site.id)
        await self.sites.update_site(site.id, SiteUpdate(
            name=current.name, base_url=current.base_url, enabled=False,
            timeout_seconds=5, source_kind=current.source_kind,
            expected_revision=current.revision,
        ))

    async def test_snapshot_carries_candidate_enablement(self) -> None:
        await self._disable(self.a)
        snapshot = await self.tasks.get_config_snapshot(self.task.id)
        self.assertEqual(snapshot.candidate_enabled, (False, True))

    async def test_scheduler_accepts_task_when_any_candidate_is_enabled(
        self,
    ) -> None:
        await self._disable(self.a)
        snapshot = await self.tasks.get_config_snapshot(self.task.id)
        scheduler = object.__new__(SchedulerService)
        scheduler._delivery = _DeliveryStub()
        # A 停用但 B 可用：不应被拦截。
        scheduler._validate_snapshot(snapshot)
        await self._disable(self.b)
        snapshot = await self.tasks.get_config_snapshot(self.task.id)
        with self.assertRaises(SchedulerValidationError):
            scheduler._validate_snapshot(snapshot)

    async def test_manual_preview_lists_candidates_and_allows_delivery(
        self,
    ) -> None:
        await self._disable(self.a)
        api = DeliveryApi(
            delivery=None, tasks=self.tasks, profiles=self.profiles,
            sites=self.sites, store=self.sites.store, inspector=_Inspector(),
        )
        result = await api.preview(self.task.id)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertTrue(data["can_deliver"])
        self.assertIsNone(data["reason"])
        self.assertEqual(
            [(item["id"], item["enabled"]) for item in data["candidate_sites"]],
            [(self.a.id, False), (self.b.id, True)],
        )

    async def test_manual_preview_reports_all_disabled(self) -> None:
        await self._disable(self.a)
        await self._disable(self.b)
        api = DeliveryApi(
            delivery=None, tasks=self.tasks, profiles=self.profiles,
            sites=self.sites, store=self.sites.store, inspector=_Inspector(),
        )
        result = await api.preview(self.task.id)
        data = result.body["data"]
        self.assertFalse(data["can_deliver"])
        self.assertEqual(data["reason"], "site_disabled")


if __name__ == "__main__":
    unittest.main()
