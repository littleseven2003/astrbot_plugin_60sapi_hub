from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiosqlite

from core.profiles import ContentProfileService, ProfileCreate
from core.site_store import SCHEMA_VERSION, SiteStore, SiteStoreError
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

# 夹具说明：先由**当前代码**建出完整结构并写入真实数据，再删除该历史
# 版本之后新增的列并把 user_version 调回去，得到“当前结构减列”的 v4
# 形状。它不是逐字节复制当年的 DDL，但表/索引/约束自洽，能真实走到 v4
# 的 ALTER TABLE 与 push_tasks 重建分支。
DROPS_V4 = (
    ("delivery_executions", "owner_token"),
    ("delivery_executions", "image_model"),
    ("delivery_pages", "image_generated"),
    ("sites", "source_kind"),
    ("push_tasks", "schedule_kind"),
    ("push_tasks", "schedule_timezone"),
    ("push_tasks", "schedule_time"),
    ("push_tasks", "schedule_weekdays"),
    ("push_tasks", "schedule_start_at"),
    ("push_tasks", "schedule_interval_minutes"),
    ("push_tasks", "schedule_expression"),
)

UMOS = ["aiocqhttp:GroupMessage:g:1", "aiocqhttp:FriendMessage:f:2"]


class _SignalingStore(SiteStore):
    """记录“已经进入迁移函数”，用于证明另一个实例确实在等写锁。"""

    def __init__(self, path):
        super().__init__(path)
        self.entered = asyncio.Event()

    async def _migrate_schema(self, connection, version):
        self.entered.set()
        return await super()._migrate_schema(connection, version)


class ConcurrentMigrationRollbackTest(unittest.IsolatedAsyncioTestCase):
    """并发迁移的关联数据保留、写锁等待与提交前回滚。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _path(self, name: str) -> Path:
        return Path(self._tmp.name) / f"{name}.sqlite3"

    async def _rich_legacy(self, name: str) -> tuple[Path, dict]:
        """建出带站点、凭据、组合、任务、白名单与投递历史的 v4 库。"""
        path = self._path(name)
        sites = SiteService(path)
        await sites.open()
        try:
            site = await sites.create_site(
                SiteCreate(
                    name="关联旧站点",
                    base_url="https://old.example.test/proxy",
                    enabled=True,
                    timeout_seconds=7,
                    source_kind="public",
                    bearer_token="legacy-token",
                ),
            )
            profiles = ContentProfileService(sites)
            profile = await profiles.create_profile(
                ProfileCreate(
                    name="关联旧组合",
                    site_id=site.id,
                    items=[
                        {"content_type": "daily_news", "params": {}, "limit": 3},
                        {"content_type": "zhihu_hot", "params": {}, "limit": 2},
                    ],
                ),
            )
            tasks = PushTaskService(profiles, sites.store)
            task = await tasks.create_task(
                TaskCreate(
                    name="关联旧任务",
                    profile_id=profile.id,
                    output_format="text",
                    umos=list(UMOS),
                ),
            )
            await sites.store.create_delivery_execution(
                execution_id="legacy-execution",
                task_id=task.id,
                task_name=task.name,
                task_revision=task.revision,
                profile_id=profile.id,
                profile_revision=profile.revision,
                site_id=site.id,
                site_revision=site.revision,
                output_format="text",
                umos=(),
            )
            ids = {"site": site.id, "profile": profile.id, "task": task.id}
        finally:
            await sites.close()

        raw = sqlite3.connect(path)
        try:
            for table, column in DROPS_V4:
                raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            raw.execute("PRAGMA user_version = 4")
            raw.commit()
        finally:
            raw.close()
        return path, ids

    def _version(self, path: Path) -> int:
        raw = sqlite3.connect(path)
        try:
            return int(raw.execute("PRAGMA user_version").fetchone()[0])
        finally:
            raw.close()

    def _columns(self, path: Path, table: str) -> list[str]:
        raw = sqlite3.connect(path)
        try:
            return [row[1] for row in raw.execute(f"PRAGMA table_info({table})")]
        finally:
            raw.close()

    def _tables(self, path: Path) -> set[str]:
        raw = sqlite3.connect(path)
        try:
            rows = raw.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            ).fetchall()
            return {row[0] for row in rows}
        finally:
            raw.close()

    def _fk_violations(self, path: Path) -> list:
        raw = sqlite3.connect(path)
        try:
            return raw.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            raw.close()

    async def _assert_related_rows(self, path: Path, ids: dict) -> None:
        sites = SiteService(path)
        await sites.open()
        try:
            site = await sites.get_site(ids["site"])
            self.assertEqual(site.name, "关联旧站点")
            self.assertEqual(site.base_url, "https://old.example.test/proxy")
            self.assertTrue(site.enabled)
            self.assertEqual(site.timeout_seconds, 7)
            # 无 source_kind 的旧站点迁移为 public。
            self.assertEqual(site.source_kind, "public")
            self.assertTrue(site.has_bearer_token)
            fetch = await sites.get_fetch_config(ids["site"])
            self.assertEqual(fetch.bearer_token, "legacy-token")

            profiles = ContentProfileService(sites)
            profile = await profiles.get_profile(ids["profile"])
            self.assertEqual(profile.name, "关联旧组合")
            self.assertEqual(profile.site_id, ids["site"])
            self.assertEqual(
                [item.content_type for item in profile.items],
                ["daily_news", "zhihu_hot"],
            )
            self.assertEqual([item.limit for item in profile.items], [3, 2])

            tasks = PushTaskService(profiles, sites.store)
            task = await tasks.get_task(ids["task"])
            self.assertEqual(task.name, "关联旧任务")
            self.assertEqual(list(task.umos), UMOS)
            self.assertEqual(task.profile_id, ids["profile"])

            self.assertIn(
                "legacy-execution",
                await sites.store.list_delivery_execution_ids(limit=10, offset=0),
            )
            self.assertEqual(self._fk_violations(path), [])
        finally:
            await sites.close()

    async def _foreign_keys_on(self, store: SiteStore) -> int:
        # 外键开关是连接级状态，raw sqlite3 连接看不到；这里问被打开的连接。
        async with store._connection.execute("PRAGMA foreign_keys") as cursor:
            row = await cursor.fetchone()
        return int(row[0])

    async def _close_quietly(self, *stores: SiteStore) -> None:
        for store in stores:
            try:
                await asyncio.wait_for(store.close(), timeout=10)
            except Exception:
                pass

    async def test_concurrent_migration_keeps_related_rows(self) -> None:
        path, ids = await self._rich_legacy("rich-race")
        first = _SignalingStore(path)
        second = _SignalingStore(path)
        try:
            await asyncio.wait_for(
                asyncio.gather(first.open(), second.open()), timeout=30,
            )
            self.assertTrue(first.is_open)
            self.assertTrue(second.is_open)
            self.assertEqual(self._version(path), SCHEMA_VERSION)
            self.assertEqual(self._columns(path, "delivery_executions").count(
                "owner_token"), 1,
            )
            self.assertNotIn("push_tasks_v7", self._tables(path))
            # 两个连接上的外键都已恢复启用。
            self.assertEqual(await self._foreign_keys_on(first), 1)
            self.assertEqual(await self._foreign_keys_on(second), 1)
        finally:
            await self._close_quietly(first, second)
        await self._assert_related_rows(path, ids)

    async def test_second_instance_waits_for_lock_then_skips_ddl(self) -> None:
        path, ids = await self._rich_legacy("lock-wait")
        first = _SignalingStore(path)
        second = _SignalingStore(path)
        release = asyncio.Event()
        holding = asyncio.Event()
        real_commit = aiosqlite.Connection.commit

        async def gated_commit(connection) -> None:
            # 迁移事务的提交点：此时写锁仍被持有。
            if not release.is_set():
                holding.set()
                await release.wait()
            await real_commit(connection)

        first_task = None
        second_task = None
        try:
            with mock.patch.object(
                aiosqlite.Connection, "commit", gated_commit,
            ):
                first_task = asyncio.create_task(first.open())
                # 第一个实例拿到写锁并停在提交点。
                await asyncio.wait_for(holding.wait(), timeout=20)
                second_task = asyncio.create_task(second.open())
                # 第二个实例已进入迁移函数，但第一个仍持有写锁。
                await asyncio.wait_for(second.entered.wait(), timeout=20)
                self.assertFalse(first_task.done())
                self.assertFalse(second_task.done())
                release.set()
                await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=30,
                )
            self.assertTrue(first.is_open)
            self.assertTrue(second.is_open)
            self.assertEqual(self._version(path), SCHEMA_VERSION)
            # 后进入者没有重复执行 DDL。
            self.assertEqual(self._columns(path, "delivery_executions").count(
                "owner_token"), 1,
            )
        finally:
            release.set()
            for task in (first_task, second_task):
                if task is not None and not task.done():
                    task.cancel()
            pending = [t for t in (first_task, second_task) if t is not None]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await self._close_quietly(first, second)
        await self._assert_related_rows(path, ids)

    async def test_failure_after_alter_rolls_back_all(self) -> None:
        path, ids = await self._rich_legacy("rollback-after-alter")
        real_commit = aiosqlite.Connection.commit
        added: list[str] = []

        async def failing_commit(connection) -> None:
            # 放在提交点：此前所有 ALTER TABLE 与 push_tasks 重建都已执行。
            raise sqlite3.OperationalError("injected before commit")

        store = SiteStore(path)
        try:
            with mock.patch.object(
                aiosqlite.Connection, "commit", failing_commit,
            ):
                with self.assertRaises(SiteStoreError):
                    await asyncio.wait_for(store.open(), timeout=30)
        finally:
            await self._close_quietly(store)

        # 版本、已加的列与重建的表都回到旧状态。
        self.assertEqual(self._version(path), 4)
        self.assertNotIn("owner_token", self._columns(path, "delivery_executions"))
        self.assertNotIn("image_generated", self._columns(path, "delivery_pages"))
        self.assertNotIn("source_kind", self._columns(path, "sites"))
        self.assertNotIn("schedule_kind", self._columns(path, "push_tasks"))
        self.assertNotIn("push_tasks_v7", self._tables(path))
        # 关联数据完好。
        raw = sqlite3.connect(path)
        try:
            self.assertEqual(
                raw.execute("SELECT id, name FROM sites").fetchall(),
                [(ids["site"], "关联旧站点")],
            )
            self.assertEqual(
                raw.execute("SELECT COUNT(*) FROM push_task_umos").fetchone()[0],
                2,
            )
        finally:
            raw.close()
        self.assertEqual(self._fk_violations(path), [])

        # 后续实例仍能完整迁移。
        retry = SiteStore(path)
        try:
            await asyncio.wait_for(retry.open(), timeout=30)
            self.assertEqual(self._version(path), SCHEMA_VERSION)
            self.assertEqual(
                self._columns(path, "delivery_executions").count("owner_token"), 1,
            )
            self.assertEqual(await self._foreign_keys_on(retry), 1)
        finally:
            await self._close_quietly(retry)
        await self._assert_related_rows(path, ids)


if __name__ == "__main__":
    unittest.main()
