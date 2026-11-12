from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import site_store
from core.site_store import SCHEMA_VERSION, SiteStore, SiteStoreError

SITE_ID = "a" * 32

# 由当前 schema 降级得到的历史形状：v4 还没有 owner_token、image_model、
# image_generated、source_kind 和计划列；v12 只缺 v13 新增的 source_kind。
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
DROPS_V12 = (("sites", "source_kind"),)


class _BarrierStore(SiteStore):
    """在进入迁移前wait一次，让两个实例都已完成版本读取。"""

    barrier: asyncio.Barrier | None = None

    async def _migrate_schema(self, connection, version):
        if self.barrier is not None:
            await self.barrier.wait()
        return await super()._migrate_schema(connection, version)


class ConcurrentMigrationTest(unittest.IsolatedAsyncioTestCase):
    """两个独立实例同时打开同一个旧库时的迁移竞态。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _path(self, name: str) -> Path:
        return Path(self._tmp.name) / f"{name}.sqlite3"

    async def _make_legacy(self, version: int, name: str) -> Path:
        path = self._path(name)
        store = SiteStore(path)
        await store.open()
        try:
            await store.create_site(
                site_id=SITE_ID,
                name="旧站点",
                base_url="https://old.example.test/proxy",
                enabled=True,
                timeout_seconds=5,
                source_kind="public",
                bearer_token="legacy-token",
            )
        finally:
            await store.close()
        drops = DROPS_V4 if version <= 4 else DROPS_V12
        raw = sqlite3.connect(path)
        try:
            for table, column in drops:
                raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            raw.execute(f"PRAGMA user_version = {version}")
            raw.commit()
        finally:
            raw.close()
        return path

    def _version(self, path: Path) -> int:
        raw = sqlite3.connect(path)
        try:
            return int(raw.execute("PRAGMA user_version").fetchone()[0])
        finally:
            raw.close()

    async def _race(self, version: int, name: str):
        path = await self._make_legacy(version, name)
        barrier = asyncio.Barrier(2)
        first = _BarrierStore(path)
        second = _BarrierStore(path)
        first.barrier = barrier
        second.barrier = barrier
        # 两个实例都读到旧版本后同时进入迁移；写锁串行化，后进入者必须
        # 识别前一个实例已完成迁移，不能重复执行 DDL。
        await asyncio.gather(first.open(), second.open())
        self.assertTrue(first.is_open)
        self.assertTrue(second.is_open)
        return path, first, second

    async def _assert_migrated(self, path: Path, first, second) -> None:
        self.assertEqual(self._version(path), SCHEMA_VERSION)
        for store in (first, second):
            sites = await store.list_sites()
            self.assertEqual(len(sites), 1)
            site = sites[0]
            self.assertEqual(site.id, SITE_ID)
            self.assertEqual(site.name, "旧站点")
            self.assertEqual(site.base_url, "https://old.example.test/proxy")
            self.assertTrue(site.enabled)
            self.assertEqual(site.timeout_seconds, 5)
            # 无 source_kind 的旧站点迁移为 public。
            self.assertEqual(site.source_kind, "public")
            self.assertEqual(site.bearer_token, "legacy-token")

    async def test_v4_two_instances_migrate_concurrently(self) -> None:
        path, first, second = await self._race(4, "v4-race")
        try:
            await self._assert_migrated(path, first, second)
        finally:
            await first.close()
            await second.close()

    async def test_v12_two_instances_migrate_concurrently(self) -> None:
        path, first, second = await self._race(12, "v12-race")
        try:
            await self._assert_migrated(path, first, second)
        finally:
            await first.close()
            await second.close()

    async def test_second_instance_skips_ddl_already_applied(self) -> None:
        """后进入者不应重复执行 ALTER：库中列必须只加一次。"""
        path, first, second = await self._race(4, "v4-columns")
        try:
            await self._assert_migrated(path, first, second)
        finally:
            await first.close()
            await second.close()
        raw = sqlite3.connect(path)
        try:
            columns = [
                row[1] for row in raw.execute(
                    "PRAGMA table_info(delivery_executions)",
                )
            ]
        finally:
            raw.close()
        self.assertEqual(columns.count("owner_token"), 1, columns)

    async def test_failed_migration_rolls_back_without_half_schema(self) -> None:
        path = await self._make_legacy(4, "v4-rollback")
        broken = await self._break_migration(path)
        self.assertTrue(broken)
        # 失败后版本与数据保持旧状态，没有半迁移。
        self.assertEqual(self._version(path), 4)
        raw = sqlite3.connect(path)
        try:
            rows = raw.execute("SELECT id, name FROM sites").fetchall()
            executions = raw.execute(
                "PRAGMA table_info(delivery_executions)",
            ).fetchall()
        finally:
            raw.close()
        self.assertEqual(rows, [(SITE_ID, "旧站点")])
        self.assertNotIn("owner_token", [row[1] for row in executions])
        # 后续重试仍能完整迁移。
        store = SiteStore(path)
        await store.open()
        try:
            await self._assert_migrated(path, store, store)
        finally:
            await store.close()

    async def _break_migration(self, path: Path) -> bool:
        with mock.patch.object(
            site_store,
            "_CREATE_SCHEDULE_CLAIMS_TABLE",
            "CREATE TABLE broken (",
        ):
            store = SiteStore(path)
            with self.assertRaises(SiteStoreError):
                await store.open()
        return True


if __name__ == "__main__":
    unittest.main()
