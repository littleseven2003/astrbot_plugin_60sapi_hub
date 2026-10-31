from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.delivery import DeliveryLimits, DeliveryMessage, DeliverySendResult, DeliveryService, SendStatus
from core.profiles import ContentProfileService, ProfileCreate
from core.scheduler import SchedulerService, SchedulerValidationError
from core.task_api import TaskApi
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate, TaskUpdate, TaskConflictError


def item():
    return {"content_type": "daily_news", "params": {}, "limit": None}


class FakeSender:
    def __init__(self):
        self.calls = []
        self.modes = []
        self.block = asyncio.Event()

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.calls.append(message)
        mode = self.modes.pop(0) if self.modes else "success"
        if mode == "unknown":
            return DeliverySendResult(SendStatus.UNKNOWN, "send_unknown")
        if mode == "failed":
            return DeliverySendResult(SendStatus.FAILED, "send_failed")
        return DeliverySendResult(SendStatus.SUCCESS)


class StubApi:
    async def preview_profile(self, payload, *, owner=None):
        from core.profile_api import ProfileApiResult
        return ProfileApiResult(200, {"status": "ok", "data": {
            "outputs": {"text": "内容", "markdown": "# 内容"},
            "failed_sections": [],
        }})


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "db.sqlite3"
        self.sites = SiteService(self.path)
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.site = await self.sites.create_site(SiteCreate(
            name="调度站点", base_url="http://example.test/proxy",
            enabled=True, timeout_seconds=5,
        ))
        self.profile = await self.profiles.create_profile(
            ProfileCreate(name="调度组合", site_id=self.site.id, items=[item()]),
        )
        self.clock_now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.sender = FakeSender()
        self.delivery = DeliveryService(
            tasks=self.tasks, profiles=self.profiles, sites=self.sites,
            profile_api=StubApi(), store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(max_execution_seconds=5),
        )
        self.scheduler = SchedulerService(
            tasks=self.tasks, store=self.sites.store, delivery=self.delivery,
            clock=lambda: self.clock_now,
        )

    async def asyncTearDown(self):
        await self.scheduler.close()
        await self.delivery.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def task(self, umos=None, time="08:30", timezone_name="Asia/Shanghai"):
        return await self.tasks.create_task(TaskCreate(
            name="每日任务", profile_id=self.profile.id, output_format="text",
            umos=umos if umos is not None else ["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "daily", "timezone": timezone_name, "time": time},
        ))

    async def test_enable_due_once_advance_and_disable(self):
        task = await self.task()
        enabled = await self.scheduler.enable_task(task.id, 1)
        self.assertTrue(enabled.enabled)
        state = await self.sites.store.get_schedule_state(task.id)
        self.assertEqual(state.next_run_at, "2026-01-01T00:30:00+00:00")
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(await self.scheduler.check_once(), 1)
        self.assertEqual(len(self.sender.calls), 1)
        claims = await self.sites.store.list_schedule_claims(limit=10, offset=0)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].status, "succeeded")
        tracking = await self.scheduler.list_tracking(limit=5, offset=0)
        self.assertEqual(len(tracking["claims"]), 1)
        self.assertEqual(tracking["claims"][0]["execution_id"], claims[0].execution_id)
        self.assertEqual(await self.scheduler.check_once(), 0)
        await self.scheduler.disable_task(task.id, 2)
        self.assertFalse((await self.tasks.get_task(task.id)).enabled)
        self.assertIsNone(await self.sites.store.get_schedule_state(task.id))

    async def test_enable_rejects_invalid_configuration_and_conflict(self):
        empty = await self.task(umos=[])
        with self.assertRaises(SchedulerValidationError):
            await self.scheduler.enable_task(empty.id, 1)
        task = await self.task()
        with self.assertRaises(TaskConflictError):
            await self.scheduler.enable_task(task.id, 99)

    async def test_edit_disables_and_removes_plan(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        updated = await self.tasks.update_task(task.id, TaskUpdate(
            name="改名", profile_id=self.profile.id, output_format="text",
            umos=list(task.umos), expected_revision=2,
        ))
        self.assertFalse(updated.enabled)
        self.assertIsNone(await self.sites.store.get_schedule_state(task.id))

    async def test_late_window_skips_without_sending(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        self.clock_now = datetime(2026, 1, 1, 0, 31, 1, tzinfo=timezone.utc)
        self.assertEqual(await self.scheduler.check_once(), 0)
        self.assertEqual(self.sender.calls, [])
        skips = await self.sites.store.list_schedule_skips(limit=10, offset=0)
        self.assertEqual(skips[0].reason, "late")

    async def test_exact_late_window_executes(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        self.clock_now = datetime(2026, 1, 1, 0, 31, tzinfo=timezone.utc)
        self.assertEqual(await self.scheduler.check_once(), 1)
        self.assertEqual(len(self.sender.calls), 1)

    async def test_disabled_and_site_disabled_do_not_send(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        from core.sites import SiteUpdate
        await self.sites.update_site(self.site.id, SiteUpdate(
            name="调度站点", base_url="http://example.test/proxy",
            enabled=False, timeout_seconds=5, expected_revision=1,
        ))
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(await self.scheduler.check_once(), 0)
        self.assertEqual(self.sender.calls, [])

    async def test_unknown_result_is_not_retried(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        self.sender.modes = ["unknown"]
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(await self.scheduler.check_once(), 1)
        claims = await self.sites.store.list_schedule_claims(limit=10, offset=0)
        self.assertEqual(claims[0].status, "unknown")
        self.assertEqual(await self.scheduler.check_once(), 0)
        self.assertEqual(len(self.sender.calls), 1)

    async def test_concurrent_checks_claim_once(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        other = SchedulerService(
            tasks=self.tasks, store=self.sites.store, delivery=self.delivery,
            clock=lambda: self.clock_now,
        )
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        results = await asyncio.gather(
            self.scheduler.check_once(), other.check_once(),
        )
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.sender.calls), 1)
        await other.close()

    async def test_capacity_records_skip_without_sending(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        self.scheduler._active.add(task.id)
        self.assertEqual(await self.scheduler.check_once(), 0)
        self.assertEqual(self.sender.calls, [])
        skips = await self.sites.store.list_schedule_skips(limit=10, offset=0)
        self.assertEqual(skips[0].reason, "capacity")
        self.scheduler._active.clear()

    async def test_start_stop_loop_is_single_and_restartable(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        await self.scheduler.start()
        first = self.scheduler._loop_task
        self.assertIsNotNone(first)
        await self.scheduler.start()
        self.assertIs(self.scheduler._loop_task, first)
        await self.scheduler.close()
        self.assertIsNone(self.scheduler._loop_task)
        await self.scheduler.start()
        second = self.scheduler._loop_task
        self.assertIsNotNone(second)
        self.assertIsNot(second, first)
        await self.scheduler.close()

    async def test_task_api_enable_disable_and_revision(self):
        task = await self.task()
        api = TaskApi(
            self.tasks, self.profiles, self.sites,
            clock=lambda: self.clock_now, scheduler=self.scheduler,
        )
        result = await api.enable_task(task.id, {"expected_revision": 1})
        self.assertEqual(result.status_code, 200, result.body)
        self.assertTrue(result.body["data"]["enabled"])
        self.assertIsNotNone(result.body["data"]["next_run_at"])
        conflict = await api.disable_task(task.id, {"expected_revision": 1})
        self.assertEqual(conflict.status_code, 409)
        disabled = await api.disable_task(task.id, {"expected_revision": 2})
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.body["data"]["enabled"])
        self.assertIsNone(disabled.body["data"]["next_run_at"])

    async def test_restart_does_not_backfill_and_claims_are_unique(self):
        task = await self.task()
        await self.scheduler.enable_task(task.id, 1)
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        second = SchedulerService(
            tasks=self.tasks, store=self.sites.store, delivery=self.delivery,
            clock=lambda: self.clock_now,
        )
        await self.scheduler.close()
        await second.start()
        # startup resync discards the already-due plan
        self.assertEqual(await second.check_once(), 0)
        self.assertEqual(self.sender.calls, [])
        states = await self.sites.store.list_schedule_states()
        self.assertEqual(len(states), 1)
        self.assertGreater(states[0].next_run_at, self.clock_now.isoformat())
        await second.close()


if __name__ == "__main__":
    unittest.main()
