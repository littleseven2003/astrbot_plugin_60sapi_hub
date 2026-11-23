"""schema v18：移除站点启用开关时，暂停引用历史停用站点的已启用任务。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.sites import SCHEMA_VERSION, SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"
NOW = "2026-03-02T00:30:00+00:00"


class SchemaV18PauseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "v18.sqlite3"
        self.sites = SiteService(self.path)
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.paused_site = await self.sites.create_site(SiteCreate(
            name="历史停用站点", base_url="https://off.example.test/proxy",
            timeout_seconds=5,
        ))
        self.active_site = await self.sites.create_site(SiteCreate(
            name="正常站点", base_url="https://on.example.test/proxy",
            timeout_seconds=5,
        ))

        async def make(name: str, site_id: str):
            task = await self.tasks.create_task(TaskCreate(
                name=name,
                items=[{"content_type": "daily_news", "params": {}, "limit": 2}],
                sites=[site_id],
                output_format="text",
                umos=[UMO],
                schedule={"kind": "daily", "timezone": "UTC", "time": "00:30"},
            ))
            await self.sites.store.set_task_enabled(
                task.id, enabled=True, expected_revision=task.revision,
                next_run_at="2026-03-02T00:30:00+00:00", now=NOW,
            )
            return await self.tasks.get_task(task.id)

        self.affected = await make("受影响任务", self.paused_site.id)
        self.unaffected = await make("普通任务", self.active_site.id)

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self._tmp.cleanup()

    async def _simulate_v17(self) -> None:
        await self.sites.close()
        with sqlite3.connect(self.path) as connection, connection:
            connection.execute(
                "UPDATE sites SET enabled = 0 WHERE id = ?",
                (self.paused_site.id,),
            )
            connection.execute("PRAGMA user_version = 17")

    async def test_only_tasks_using_legacy_disabled_site_are_paused(self) -> None:
        await self._simulate_v17()
        reopened = SiteService(self.path)
        await reopened.open()
        try:
            with sqlite3.connect(self.path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = [
                    row[1] for row in connection.execute("PRAGMA table_info(sites)")
                ]
            self.assertEqual(version, SCHEMA_VERSION)
            # 旧列保留供追溯，运行时不再过滤。
            self.assertIn("enabled", columns)

            tasks = PushTaskService(reopened.store)
            affected = await tasks.get_task(self.affected.id)
            unaffected = await tasks.get_task(self.unaffected.id)
            self.assertFalse(affected.enabled)
            self.assertTrue(unaffected.enabled)
            # 只暂停，不清计划、白名单、栏目与 revision 历史。
            self.assertEqual(affected.schedule.to_dict(), self.affected.schedule.to_dict())
            self.assertEqual(affected.umos, self.affected.umos)
            self.assertEqual(affected.items, self.affected.items)
            self.assertEqual(affected.revision, self.affected.revision + 1)
            self.assertIsNone(
                await reopened.store.get_schedule_state(affected.id),
            )
            self.assertIsNotNone(
                await reopened.store.get_schedule_state(unaffected.id),
            )
            # 历史停用站点升级后不再阻止取数。
            fetch = await reopened.get_fetch_config(self.paused_site.id)
            self.assertEqual(fetch.base_url, "https://off.example.test/proxy")
        finally:
            await reopened.close()

    async def test_second_open_does_not_repause_reenabled_task(self) -> None:
        await self._simulate_v17()
        reopened = SiteService(self.path)
        await reopened.open()
        try:
            tasks = PushTaskService(reopened.store)
            affected = await tasks.get_task(self.affected.id)
            await reopened.store.set_task_enabled(
                affected.id, enabled=True,
                expected_revision=affected.revision,
                next_run_at="2026-03-03T00:30:00+00:00",
                now="2026-03-03T00:00:00+00:00",
            )
        finally:
            await reopened.close()
        again = SiteService(self.path)
        await again.open()
        try:
            tasks = PushTaskService(again.store)
            self.assertTrue((await tasks.get_task(self.affected.id)).enabled)
        finally:
            await again.close()


if __name__ == "__main__":
    unittest.main()
