from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from core.profiles import ContentProfileService, ProfileCreate, ProfileReferencedError
from core.site_store import SCHEMA_VERSION, SiteStore
from core.sites import SiteCreate, SiteService, SiteStorageError
from core.tasks import (
    PushTaskService,
    TaskConflictError,
    TaskCreate,
    TaskNotFoundError,
    TaskUpdate,
    TaskValidationError,
    normalize_umo,
    normalize_umos,
)


def item(content_type="daily_news", limit=None):
    return {"content_type": content_type, "params": {}, "limit": limit}


class UmoValidationTest(unittest.TestCase):
    def test_group_and_friend_umo_mixed_dedupe_and_colons(self) -> None:
        raw = [
            " aiocqhttp:GroupMessage:123:456 ",
            "aiocqhttp:FriendMessage:User:With:Colons",
            "aiocqhttp:GroupMessage:123:456",
            "lark:GroupMessage:g1",
        ]
        self.assertEqual(
            normalize_umos(raw),
            (
                "aiocqhttp:GroupMessage:123:456",
                "aiocqhttp:FriendMessage:User:With:Colons",
                "lark:GroupMessage:g1",
            ),
        )
        self.assertEqual(normalize_umos([]), ())
        self.assertEqual(
            normalize_umo("aiocqhttp:GroupMessage:Case:Kept"),
            "aiocqhttp:GroupMessage:Case:Kept",
        )

    def test_internal_whitespace_and_exact_value_preservation(self) -> None:
        # Only the whole UMO is stripped. A leading space in session_id is
        # internal, so it must be preserved rather than silently removed.
        source = " DemoBot:GroupMessage: user:member "
        self.assertEqual(
            normalize_umo(source),
            "DemoBot:GroupMessage: user:member",
        )
        self.assertEqual(
            normalize_umos([source, " DemoBot:GroupMessage: user:member "]),
            ("DemoBot:GroupMessage: user:member",),
        )
        # Session IDs are case-sensitive and include all internal colons.
        self.assertEqual(
            normalize_umos([
                "DemoBot:GroupMessage:User:One",
                "DemoBot:GroupMessage:user:one",
            ]),
            (
                "DemoBot:GroupMessage:User:One",
                "DemoBot:GroupMessage:user:one",
            ),
        )
        # Platform edge/internal whitespace is rejected, never trimmed.
        for value in (
            "DemoBot :GroupMessage:user",
            "DemoBot:GroupMessage: user",
            "Demo Bot:GroupMessage:user",
            "DemoBot: GroupMessage:user",
        ):
            if value == "DemoBot:GroupMessage: user":
                continue
            with self.subTest(value=value):
                with self.assertRaises(TaskValidationError):
                    normalize_umo(value)

    def test_rejects_bare_ids_bad_types_empty_and_controls(self) -> None:
        invalid = [
            "12345",
            "12345:GroupMessage",
            "aiocqhttp:GroupMessage:",
            ":GroupMessage:123",
            "aiocqhttp::123",
            "aiocqhttp:OtherMessage:123",
            "aiocqhttp:groupmessage:123",
            "aiocqhttp:GroupMessage:123\n",
            "aiocqhttp:GroupMessage:123\x00",
            "aiocqhttp!:GroupMessage:123",
            ":GroupMessage:123",
            "aiocqhttp:GroupMessage:  ",
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(TaskValidationError):
                    normalize_umo(value)
        with self.assertRaises(TaskValidationError):
            normalize_umos(["a:GroupMessage:1"] * 21)
        with self.assertRaises(TaskValidationError):
            normalize_umos("a:GroupMessage:1")
        with self.assertRaises(TaskValidationError):
            normalize_umos([1])

    def test_length_limits(self) -> None:
        with self.assertRaises(TaskValidationError):
            normalize_umo("a" * 129 + ":GroupMessage:x")
        with self.assertRaises(TaskValidationError):
            normalize_umo("a:GroupMessage:" + "x" * 385)
        with self.assertRaises(TaskValidationError):
            normalize_umo("a:GroupMessage:" + "x" * 500)


class TaskServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "plugin.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.site = await self.sites.create_site(
            SiteCreate(
                name="任务站点",
                base_url="http://example.test/proxy",
                enabled=False,
                timeout_seconds=5,
            ),
        )
        self.profile = await self.profiles.create_profile(
            ProfileCreate(
                name="任务组合",
                site_id=self.site.id,
                items=[item()],
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self.tmp.cleanup()

    async def _create(self, name="任务"):
        return await self.tasks.create_task(
            TaskCreate(
                name=name,
                profile_id=self.profile.id,
                output_format="image",
                umos=[
                    "aiocqhttp:GroupMessage:g:1",
                    "aiocqhttp:FriendMessage:u1",
                ],
            ),
        )

    async def test_create_read_update_delete_and_restart(self) -> None:
        task = await self._create()
        self.assertEqual(task.profile_id, self.profile.id)
        self.assertEqual(task.output_format, "image")
        self.assertEqual(task.umos, (
            "aiocqhttp:GroupMessage:g:1",
            "aiocqhttp:FriendMessage:u1",
        ))
        self.assertFalse(task.enabled)
        self.assertEqual([x.id for x in await self.tasks.list_tasks()], [task.id])
        self.assertEqual((await self.tasks.get_task(task.id)).name, "任务")

        updated = await self.tasks.update_task(
            task.id,
            TaskUpdate(
                name="改名任务",
                profile_id=self.profile.id,
                output_format="markdown",
                umos=[],
                expected_revision=task.revision,
            ),
        )
        self.assertEqual(updated.name, "改名任务")
        self.assertEqual(updated.umos, ())
        self.assertEqual(updated.output_format, "markdown")
        with self.assertRaises(TaskConflictError):
            await self.tasks.update_task(
                task.id,
                TaskUpdate(
                    name="冲突",
                    profile_id=self.profile.id,
                    output_format="text",
                    umos=[],
                    expected_revision=task.revision,
                ),
            )
        await self.sites.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            tasks = PushTaskService(
                ContentProfileService(reopened),
                reopened.store,
            )
            loaded = await tasks.get_task(task.id)
            self.assertEqual(loaded.name, "改名任务")
            self.assertEqual(loaded.output_format, "markdown")
            self.assertEqual(loaded.umos, ())
        finally:
            await reopened.close()

    async def test_profile_delete_protection_and_delete_task(self) -> None:
        task = await self._create()
        with self.assertRaises(ProfileReferencedError) as caught:
            await self.profiles.delete_profile(self.profile.id, self.profile.revision)
        self.assertEqual(caught.exception.task_names, ("任务",))
        await self.tasks.delete_task(task.id, task.revision)
        with self.assertRaises(TaskNotFoundError):
            await self.tasks.get_task(task.id)
        await self.profiles.delete_profile(self.profile.id, self.profile.revision)

    async def test_daily_schedule_persists_preserves_clears_and_migrates_v5(self) -> None:
        from core.schedule import DailySchedule
        from datetime import time as dt_time

        task = await self.tasks.create_task(TaskCreate(
            name="计划任务", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={
                "kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30",
            },
        ))
        self.assertEqual(task.schedule, DailySchedule("Asia/Shanghai", dt_time(8, 30)))
        updated = await self.tasks.update_task(task.id, TaskUpdate(
            name="计划保留", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            expected_revision=task.revision,
        ))
        self.assertEqual(updated.schedule, task.schedule)
        self.assertEqual(updated.revision, 2)
        cleared = await self.tasks.update_task(task.id, TaskUpdate(
            name="计划清除", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            expected_revision=updated.revision, schedule=None,
        ))
        self.assertIsNone(cleared.schedule)
        self.assertEqual(cleared.revision, 3)

        # Re-add, then simulate a schema-v5 database without schedule columns.
        scheduled = await self.tasks.update_task(task.id, TaskUpdate(
            name="重启后计划", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            expected_revision=cleared.revision,
            schedule={"kind": "daily", "timezone": "UTC", "time": "09:00"},
        ))
        await self.sites.store.create_delivery_execution(
            execution_id="migration-history", task_id=task.id,
            task_name=task.name, task_revision=task.revision,
            profile_id=self.profile.id, profile_revision=self.profile.revision,
            site_id=self.site.id, site_revision=self.site.revision,
            output_format="text", umos=(),
        )
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_kind")
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_timezone")
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_time")
            raw.execute("PRAGMA user_version = 5")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            tasks = PushTaskService(
                ContentProfileService(reopened), reopened.store,
            )
            loaded = await tasks.get_task(task.id)
            self.assertIsNone(loaded.schedule)
            self.assertEqual(loaded.revision, scheduled.revision)
            self.assertEqual(loaded.umos, scheduled.umos)
            self.assertIn(
                "migration-history",
                await reopened.store.list_delivery_execution_ids(limit=10, offset=0),
            )
            with closing(sqlite3.connect(self.db_path)) as check:
                self.assertEqual(
                    check.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_v7_schema_real_migration_preserves_runtime_and_history(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="v7真实迁移", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30"},
        ))
        await self.sites.store.set_task_enabled(
            task.id, enabled=True, expected_revision=1,
            next_run_at="2026-01-01T00:30:00+00:00",
            now="2026-01-01T00:00:00+00:00",
        )
        await self.sites.store.create_delivery_execution(
            execution_id="v7-history", task_id=task.id, task_name=task.name,
            task_revision=2, profile_id=self.profile.id,
            profile_revision=self.profile.revision, site_id=self.site.id,
            site_revision=self.site.revision, output_format="text", umos=(),
        )
        await self.sites.store.claim_schedule(
            task_id=task.id, scheduled_for="2026-01-01T00:30:00+00:00",
            execution_id="v7-claim", task_revision=2,
            profile_revision=self.profile.revision, site_revision=self.site.revision,
            next_run_at="2026-01-02T00:30:00+00:00",
            now="2026-01-01T00:30:00+00:00",
        )
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_weekdays")
            raw.execute("PRAGMA user_version = 7")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertTrue(loaded.enabled)
            self.assertEqual(loaded.revision, 2)
            self.assertEqual(loaded.schedule.time.strftime("%H:%M"), "08:30")
            self.assertEqual(loaded.umos, task.umos)
            self.assertEqual(
                (await reopened.store.get_schedule_state(task.id)).next_run_at,
                "2026-01-02T00:30:00+00:00",
            )
            claims = await reopened.store.list_schedule_claims(limit=10, offset=0)
            self.assertEqual(claims[0].execution_id, "v7-claim")
            self.assertIn(
                "v7-history",
                await reopened.store.list_delivery_execution_ids(limit=10, offset=0),
            )
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_v7_schema_weekday_migration_failure_rolls_back(self) -> None:
        from unittest.mock import patch
        import aiosqlite
        task = await self.tasks.create_task(TaskCreate(
            name="v7失败", profile_id=self.profile.id, output_format="text",
            umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "daily", "timezone": "UTC", "time": "08:30"},
        ))
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_weekdays")
            raw.execute("PRAGMA user_version = 7")
            raw.commit()
        finally:
            raw.close()
        original = aiosqlite.Connection.execute
        def failing(connection, sql, *args, **kwargs):
            if "ADD COLUMN schedule_weekdays" in sql:
                raise RuntimeError("forced v7 weekday failure")
            return original(connection, sql, *args, **kwargs)
        reopened = SiteService(self.db_path)
        with patch.object(aiosqlite.Connection, "execute", failing):
            with self.assertRaises(Exception):
                await reopened.open()
        await reopened.close()
        with closing(sqlite3.connect(self.db_path)) as check:
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertNotIn(
                "schedule_weekdays",
                {row[1] for row in check.execute("PRAGMA table_info(push_tasks)")},
            )
            self.assertEqual(
                check.execute("SELECT name FROM push_tasks WHERE id=?", (task.id,)).fetchone()[0],
                "v7失败",
            )
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def test_v7_schema_adds_weekdays_and_preserves_weekly(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="v7每周迁移", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "weekly", "timezone": "Asia/Shanghai",
                      "time": "08:30", "weekdays": [1, 3]},
        ))
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("PRAGMA user_version = 7")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertEqual(loaded.schedule.weekdays, (1, 3))
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_weekly_daily_null_switch_and_edit_disables(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="切换计划", profile_id=self.profile.id, output_format="text",
            umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "weekly", "timezone": "UTC", "time": "08:30",
                      "weekdays": [1, 3]},
        ))
        await self.sites.store.set_task_enabled(
            task.id, enabled=True, expected_revision=1,
            next_run_at="2026-01-05T08:30:00+00:00",
            now="2026-01-01T00:00:00+00:00",
        )
        updated = await self.tasks.update_task(task.id, TaskUpdate(
            name="切换每日", profile_id=self.profile.id,
            output_format="text", umos=list(task.umos),
            expected_revision=2,
            schedule={"kind": "daily", "timezone": "UTC", "time": "09:00"},
        ))
        self.assertFalse(updated.enabled)
        self.assertEqual(updated.schedule.kind, "daily")
        self.assertIsNone(await self.sites.store.get_schedule_state(task.id))
        cleared = await self.tasks.update_task(task.id, TaskUpdate(
            name="切换无计划", profile_id=self.profile.id,
            output_format="text", umos=list(task.umos),
            expected_revision=3, schedule=None,
        ))
        self.assertIsNone(cleared.schedule)

    async def test_corrupt_weekdays_are_reported_and_omitted_update_preserves(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="异常星期", profile_id=self.profile.id, output_format="text",
            umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "weekly", "timezone": "UTC", "time": "08:30",
                      "weekdays": [1, 3]},
        ))
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("UPDATE push_tasks SET schedule_weekdays='1,x,3' WHERE id=?", (task.id,))
            raw.commit()
        finally:
            raw.close()
        items = await self.tasks.list_tasks()
        loaded = next(item for item in items if item.id == task.id)
        self.assertIsNone(loaded.schedule)
        self.assertEqual(loaded.schedule_error, "schedule_invalid")
        self.assertEqual(loaded.schedule_raw["weekdays"], "1,x,3")
        updated = await self.tasks.update_task(task.id, TaskUpdate(
            name="异常保留", profile_id=self.profile.id,
            output_format="text", umos=list(task.umos),
            expected_revision=task.revision,
        ))
        self.assertEqual(updated.schedule_error, "schedule_invalid")
        with closing(sqlite3.connect(self.db_path)) as check:
            self.assertEqual(
                check.execute("SELECT schedule_weekdays FROM push_tasks WHERE id=?", (task.id,)).fetchone()[0],
                "1,x,3",
            )
        cleared = await self.tasks.update_task(task.id, TaskUpdate(
            name="异常清除", profile_id=self.profile.id,
            output_format="text", umos=list(task.umos),
            expected_revision=updated.revision, schedule=None,
        ))
        self.assertIsNone(cleared.schedule)
        self.assertIsNone(cleared.schedule_error)

    async def test_interval_schedule_persists_and_switches(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="间隔任务", profile_id=self.profile.id, output_format="text",
            umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "interval", "timezone": "Asia/Shanghai",
                      "start_at": "2026-11-04T08:30", "interval_minutes": 60},
        ))
        self.assertEqual(task.schedule.kind, "interval")
        self.assertEqual(task.schedule.interval_minutes, 60)
        await self.sites.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertEqual(loaded.schedule.start_at, "2026-11-04T08:30")
            self.assertEqual(loaded.schedule.interval_minutes, 60)
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_weekly_schedule_persists_and_restarts(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="每周任务", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={
                "kind": "weekly", "timezone": "Asia/Shanghai",
                "time": "08:30", "weekdays": [5, 1, 3],
            },
        ))
        self.assertEqual(task.schedule.kind, "weekly")
        self.assertEqual(task.schedule.weekdays, (1, 3, 5))
        await self.sites.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertEqual(loaded.schedule.weekdays, (1, 3, 5))
            self.assertEqual(loaded.schedule.time.strftime("%H:%M"), "08:30")
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_daily_schedule_survives_restart(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="重启计划", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "daily", "timezone": "UTC", "time": "10:00"},
        ))
        await self.sites.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertEqual(loaded.schedule, task.schedule)
            self.assertEqual(loaded.revision, 1)
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_v5_partial_schedule_columns_are_repaired(self) -> None:
        task = await self._create("部分迁移任务")
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_timezone")
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_time")
            raw.execute("PRAGMA user_version = 5")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            with closing(sqlite3.connect(self.db_path)) as check:
                self.assertEqual(
                    check.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
                self.assertEqual(
                    check.execute("SELECT COUNT(*) FROM push_tasks").fetchone()[0],
                    1,
                )
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertIsNone(loaded.schedule)
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_v5_schedule_migration_failure_rolls_back(self) -> None:
        from unittest.mock import patch
        import aiosqlite

        task = await self._create("v5迁移失败任务")
        await self.sites.store.create_delivery_execution(
            execution_id="v5-failure-history", task_id=task.id,
            task_name=task.name, task_revision=task.revision,
            profile_id=self.profile.id, profile_revision=self.profile.revision,
            site_id=self.site.id, site_revision=self.site.revision,
            output_format="text", umos=(),
        )
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_kind")
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_timezone")
            raw.execute("ALTER TABLE push_tasks DROP COLUMN schedule_time")
            raw.execute("PRAGMA user_version = 5")
            raw.commit()
        finally:
            raw.close()
        original_execute = aiosqlite.Connection.execute
        seen_sql: list[str] = []
        attempted_target = False

        def failing_execute(connection, sql, *args, **kwargs):
            nonlocal attempted_target
            seen_sql.append(sql)
            if "ADD COLUMN schedule_time " in sql:
                attempted_target = True
                raise RuntimeError("forced schedule migration failure")
            # Important: return the original awaitable/async-context object;
            # do not wrap it in a coroutine.
            return original_execute(connection, sql, *args, **kwargs)

        reopened = SiteService(self.db_path)
        with patch.object(aiosqlite.Connection, "execute", failing_execute):
            with self.assertRaises(SiteStorageError) as caught:
                await reopened.open()
        cause = caught.exception
        while cause is not None and "forced schedule migration failure" not in str(cause):
            cause = cause.__cause__
        self.assertIsNotNone(cause)
        self.assertTrue(attempted_target)
        self.assertTrue(any("ADD COLUMN schedule_kind" in sql for sql in seen_sql))
        self.assertTrue(any("ADD COLUMN schedule_timezone" in sql for sql in seen_sql))
        await reopened.close()
        with closing(sqlite3.connect(self.db_path)) as check:
            self.assertEqual(
                check.execute("PRAGMA user_version").fetchone()[0], 5,
            )
            columns = {
                row[1] for row in check.execute("PRAGMA table_info(push_tasks)")
            }
            self.assertNotIn("schedule_kind", columns)
            self.assertNotIn("schedule_timezone", columns)
            self.assertNotIn("schedule_time", columns)
            self.assertEqual(
                check.execute("SELECT COUNT(*) FROM push_tasks").fetchone()[0], 1,
            )
            self.assertEqual(
                check.execute(
                    "SELECT name FROM push_tasks WHERE id = ?", (task.id,),
                ).fetchone()[0],
                "v5迁移失败任务",
            )
            self.assertEqual(
                check.execute("SELECT COUNT(*) FROM push_task_umos").fetchone()[0],
                len(task.umos),
            )
            self.assertEqual(
                check.execute("SELECT COUNT(*) FROM content_profiles").fetchone()[0],
                1,
            )
            self.assertEqual(
                check.execute("SELECT COUNT(*) FROM delivery_executions").fetchone()[0],
                1,
            )
        # Removing the fault allows a clean retry.
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def test_unexpected_schedule_parse_error_is_not_silently_swallowed(self) -> None:
        from unittest.mock import patch
        await self.tasks.create_task(TaskCreate(
            name="异常计划任务", profile_id=self.profile.id,
            output_format="text", umos=[],
            schedule={"kind": "daily", "timezone": "UTC", "time": "08:00"},
        ))
        with patch("core.tasks.parse_schedule", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                await self.tasks.list_tasks()

    async def test_v6_schema_migrates_enabled_constraint_and_preserves_task(self) -> None:
        from core.site_store import SCHEMA_VERSION
        task = await self.tasks.create_task(TaskCreate(
            name="v6启用迁移", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30"},
        ))
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute("""
                CREATE TABLE push_tasks_v6 (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL,
                    profile_id TEXT NOT NULL REFERENCES content_profiles(id) ON DELETE RESTRICT,
                    output_format TEXT NOT NULL CHECK (
                        output_format IN ('text', 'markdown', 'image')
                    ),
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled = 0),
                    schedule_kind TEXT,
                    schedule_timezone TEXT,
                    schedule_time TEXT,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            raw.execute("""
                INSERT INTO push_tasks_v6
                SELECT id, name, profile_id, output_format, enabled,
                       schedule_kind, schedule_timezone, schedule_time,
                       revision, created_at, updated_at FROM push_tasks
            """)
            raw.execute("DROP TABLE push_tasks")
            raw.execute("ALTER TABLE push_tasks_v6 RENAME TO push_tasks")
            raw.execute("PRAGMA user_version = 6")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            loaded = await PushTaskService(
                ContentProfileService(reopened), reopened.store,
            ).get_task(task.id)
            self.assertFalse(loaded.enabled)
            self.assertEqual(loaded.schedule.time.strftime("%H:%M"), "08:30")
            with closing(sqlite3.connect(self.db_path)) as check:
                self.assertEqual(
                    check.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
                columns = {
                    row[1] for row in check.execute(
                        "PRAGMA table_info(push_tasks)",
                    )
                }
                self.assertIn("enabled", columns)
        finally:
            await reopened.close()
        self.sites = reopened

    async def test_v6_schema_migration_failure_rolls_back(self) -> None:
        from unittest.mock import patch
        import aiosqlite

        task = await self.tasks.create_task(TaskCreate(
            name="v6迁移失败", profile_id=self.profile.id,
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            schedule={"kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30"},
        ))
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("PRAGMA foreign_keys = OFF")
            raw.execute("""
                CREATE TABLE push_tasks_v6b (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL,
                    profile_id TEXT NOT NULL REFERENCES content_profiles(id) ON DELETE RESTRICT,
                    output_format TEXT NOT NULL CHECK (
                        output_format IN ('text', 'markdown', 'image')
                    ),
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled = 0),
                    schedule_kind TEXT,
                    schedule_timezone TEXT,
                    schedule_time TEXT,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            raw.execute("""
                INSERT INTO push_tasks_v6b
                SELECT id, name, profile_id, output_format, enabled,
                       schedule_kind, schedule_timezone, schedule_time,
                       revision, created_at, updated_at FROM push_tasks
            """)
            raw.execute("DROP TABLE push_tasks")
            raw.execute("ALTER TABLE push_tasks_v6b RENAME TO push_tasks")
            raw.execute("PRAGMA user_version = 6")
            raw.commit()
        finally:
            raw.close()
        original_execute = aiosqlite.Connection.execute

        def failing_execute(connection, sql, *args, **kwargs):
            if "CREATE TABLE IF NOT EXISTS push_schedule_state" in sql:
                raise RuntimeError("forced v6 schedule migration failure")
            return original_execute(connection, sql, *args, **kwargs)

        reopened = SiteService(self.db_path)
        with patch.object(aiosqlite.Connection, "execute", failing_execute):
            with self.assertRaises(Exception) as caught:
                await reopened.open()
        cause = caught.exception
        while cause is not None and "forced v6 schedule migration failure" not in str(cause):
            cause = cause.__cause__
        self.assertIsNotNone(cause)
        await reopened.close()
        with closing(sqlite3.connect(self.db_path)) as check:
            self.assertEqual(
                check.execute("PRAGMA user_version").fetchone()[0], 6,
            )
            sql = check.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='push_tasks'",
            ).fetchone()[0]
            self.assertIn("enabled = 0", sql)
            self.assertEqual(
                check.execute(
                    "SELECT name FROM push_tasks WHERE id = ?", (task.id,),
                ).fetchone()[0],
                "v6迁移失败",
            )
            self.assertEqual(
                check.execute("SELECT COUNT(*) FROM push_task_umos").fetchone()[0],
                1,
            )
        self.sites = SiteService(self.db_path)
        await self.sites.open()

    async def test_cancelled_create_does_not_persist(self) -> None:
        await self.sites.store._lock.acquire()
        try:
            task = asyncio.create_task(
                self.tasks.create_task(
                    TaskCreate(
                        name="取消任务",
                        profile_id=self.profile.id,
                        output_format="text",
                        umos=[],
                    ),
                ),
            )
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            self.sites.store._lock.release()
        self.assertEqual(await self.tasks.list_tasks(), [])

    async def test_invalid_expected_revision_is_task_validation_and_no_write(self) -> None:
        task = await self._create("revision任务")
        invalid = [None, True, False, 0, -1, 1.0, 1.5, "1", "", []]
        for value in invalid:
            with self.subTest(kind="update", value=value):
                with self.assertRaises(TaskValidationError) as caught:
                    await self.tasks.update_task(
                        task.id,
                        TaskUpdate(
                            name="不应写入",
                            profile_id=self.profile.id,
                            output_format="text",
                            umos=[],
                            expected_revision=value,
                        ),
                    )
                self.assertEqual(caught.exception.field, "expected_revision")
            with self.subTest(kind="delete", value=value):
                with self.assertRaises(TaskValidationError) as caught:
                    await self.tasks.delete_task(task.id, value)
                self.assertEqual(caught.exception.field, "expected_revision")
            self.assertEqual(
                (await self.tasks.get_task(task.id)).name,
                "revision任务",
            )
            self.assertEqual(len(await self.tasks.list_tasks()), 1)
        updated = await self.tasks.update_task(
            task.id,
            TaskUpdate(
                name="合法写入",
                profile_id=self.profile.id,
                output_format="text",
                umos=[],
                expected_revision=task.revision,
            ),
        )
        self.assertEqual(updated.name, "合法写入")
        with self.assertRaises(TaskConflictError):
            await self.tasks.update_task(
                task.id,
                TaskUpdate(
                    name="过期整数",
                    profile_id=self.profile.id,
                    output_format="text",
                    umos=[],
                    expected_revision=task.revision,
                ),
            )

    async def test_missing_profile_missing_task_and_validation(self) -> None:
        with self.assertRaises(Exception):
            await self.tasks.create_task(
                TaskCreate(
                    name="坏任务",
                    profile_id="missing",
                    output_format="text",
                    umos=[],
                ),
            )
        with self.assertRaises(TaskValidationError):
            await self.tasks.create_task(
                TaskCreate(
                    name="坏格式",
                    profile_id=self.profile.id,
                    output_format="pdf",
                    umos=[],
                ),
            )
        with self.assertRaises(TaskNotFoundError):
            await self.tasks.get_task("missing")

    async def test_v2_database_migrates_tasks_without_losing_data(self) -> None:
        await self._create("迁移任务")
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("DROP TABLE push_task_umos")
            raw.execute("DROP TABLE push_tasks")
            raw.execute("PRAGMA user_version = 2")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            with closing(sqlite3.connect(self.db_path)) as check:
                self.assertEqual(
                    check.execute("PRAGMA user_version").fetchone()[0],
                    SCHEMA_VERSION,
                )
                self.assertEqual(
                    check.execute("SELECT COUNT(*) FROM content_profiles").fetchone()[0],
                    1,
                )
            tasks = PushTaskService(
                ContentProfileService(reopened),
                reopened.store,
            )
            self.assertEqual(await tasks.list_tasks(), [])
        finally:
            await reopened.close()


    async def test_v2_task_migration_failure_rolls_back(self) -> None:
        import core.site_store as store_module

        await self._create("回滚前任务")
        await self.sites.close()
        raw = sqlite3.connect(self.db_path)
        try:
            raw.execute("DROP TABLE push_task_umos")
            raw.execute("DROP TABLE push_tasks")
            raw.execute("PRAGMA user_version = 2")
            raw.commit()
        finally:
            raw.close()
        original = store_module._CREATE_TASK_UMOS_TABLE
        store_module._CREATE_TASK_UMOS_TABLE = "CREATE TABLE broken ("
        reopened = SiteService(self.db_path)
        try:
            with self.assertRaises(Exception):
                await reopened.open()
        finally:
            store_module._CREATE_TASK_UMOS_TABLE = original
            await reopened.close()
        raw = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                raw.execute("PRAGMA user_version").fetchone()[0],
                2,
            )
            self.assertEqual(
                raw.execute("SELECT COUNT(*) FROM content_profiles").fetchone()[0],
                1,
            )
            self.assertEqual(
                raw.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='push_tasks'",
                ).fetchone()[0],
                0,
            )
        finally:
            raw.close()

class TaskConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_task_create_and_profile_delete_no_dangling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "plugin.sqlite3"
            sites = SiteService(db)
            await sites.open()
            profiles = ContentProfileService(sites)
            tasks = PushTaskService(profiles, sites.store)
            site = await sites.create_site(
                SiteCreate(
                    name="并发站点",
                    base_url="http://example.test/proxy",
                    enabled=True,
                    timeout_seconds=5,
                ),
            )
            profile = await profiles.create_profile(
                ProfileCreate(name="并发组合", site_id=site.id, items=[item()]),
            )
            async def create():
                try:
                    return await tasks.create_task(
                        TaskCreate(
                            name="并发任务",
                            profile_id=profile.id,
                            output_format="text",
                            umos=[],
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    return exc
            async def delete():
                try:
                    await profiles.delete_profile(profile.id, profile.revision)
                    return "deleted"
                except Exception as exc:  # noqa: BLE001
                    return exc
            created, deleted = await asyncio.gather(create(), delete())
            if isinstance(created, Exception):
                self.assertIsInstance(deleted, str)
            else:
                self.assertIsInstance(deleted, Exception)
            await sites.close()
