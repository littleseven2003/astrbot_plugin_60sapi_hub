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

# 夹具说明：**不是**真实历史数据库样本，也不是逐字节复制当年的 DDL。
# 做法是先由当前代码建出完整结构并写入真实形状的假数据，再删除该历史
# 版本之后新增的列并把 user_version 调回去，得到“当前结构减列”的 v4
# 形状。表/索引/约束自洽，能真实走到 v4 的 ALTER TABLE 与 push_tasks
# 重建分支；当年的数据内容与中间版本改写过的行状态无法复现。
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
EXECUTION_ID = "legacy-execution"


class _InjectedMigrationFailure(RuntimeError):
    """提交前注入的特定失败，用来证明回滚由它触发，而不是更早的错误。"""


class _BarrierTimeout(AssertionError):
    """屏障等待超时；单独区分，避免被当成普通断言失败。"""


class ConcurrentMigrationRollbackTest(unittest.IsolatedAsyncioTestCase):
    """并发迁移的写锁等待、提交前回滚与关联数据保留。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _path(self, name: str) -> Path:
        return Path(self._tmp.name) / f"{name}.sqlite3"

    async def _rich_legacy(self, name: str) -> tuple[Path, dict]:
        """带站点、凭据、组合、任务、白名单、投递执行与 target/page 子记录。"""
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
                    sites=[site.id],
                    output_format="text",
                    umos=list(UMOS),
                ),
            )
            await sites.store.create_delivery_execution(
                execution_id=EXECUTION_ID,
                task_id=task.id,
                task_name=task.name,
                task_revision=task.revision,
                profile_id=profile.id,
                profile_revision=profile.revision,
                site_id=site.id,
                site_revision=site.revision,
                output_format="text",
                umos=(UMOS[0],),
            )
            await sites.store.add_delivery_pages(
                EXECUTION_ID, position=0, page_count=2, image_generated=(1, 0),
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

    # ---- 事务外检查 ----

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

    def _fk_targets(self, path: Path, table: str) -> list[str]:
        raw = sqlite3.connect(path)
        try:
            rows = raw.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            return sorted({row[2] for row in rows})
        finally:
            raw.close()

    def _count(self, path: Path, table: str) -> int:
        raw = sqlite3.connect(path)
        try:
            return int(raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            raw.close()

    # ---- 连接与清理 ----

    async def _foreign_keys_on(self, store: SiteStore) -> int:
        # 外键开关是连接级状态，raw sqlite3 连接看不到；这里问被打开的连接。
        async with store._connection.execute("PRAGMA foreign_keys") as cursor:
            row = await cursor.fetchone()
        return int(row[0])

    async def _close_all(self, *stores: SiteStore) -> list[str]:
        """关闭所有连接并返回问题列表；调用方断言为空，不静默吞异常。"""
        problems: list[str] = []
        for index, store in enumerate(stores):
            try:
                await asyncio.wait_for(store.close(), timeout=10)
            except Exception as exc:
                problems.append(f"close[{index}]: {exc!r}")
        return problems

    async def _drain(self, *tasks) -> None:
        pending = [task for task in tasks if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ---- 关联数据校验 ----

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
            async with sites.store._require_connection().execute(
                "PRAGMA table_info(content_profiles)",
            ) as cursor:
                columns = {row[1] for row in await cursor.fetchall()}
            self.assertNotIn("site_id", columns)
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
                EXECUTION_ID,
                await sites.store.list_delivery_execution_ids(limit=10, offset=0),
            )
            self.assertEqual(self._count(path, "delivery_targets"), 1)
            self.assertEqual(self._count(path, "delivery_pages"), 2)
            self.assertEqual(self._fk_violations(path), [])
        finally:
            await sites.close()

    async def _assert_fk_definitions(self, path: Path) -> None:
        self.assertEqual(
            self._fk_targets(path, "delivery_targets"), ["delivery_executions"],
        )
        self.assertEqual(
            self._fk_targets(path, "delivery_pages"), ["delivery_targets"],
        )

    async def _assert_fk_enforced(self, store: SiteStore, ids: dict) -> None:
        """用合法/非法引用操作验证约束仍然生效（不仅仅是声明）。"""
        connection = store._connection
        await connection.execute("SAVEPOINT fk_probe")
        try:
            # 合法引用：指向已存在的 target 行。
            await connection.execute(
                "INSERT INTO delivery_pages "
                "(execution_id, position, page_index, status) "
                "VALUES (?, 0, 3, 'pending')",
                (EXECUTION_ID,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                await connection.execute(
                    "INSERT INTO delivery_pages "
                    "(execution_id, position, page_index, status) "
                    "VALUES ('missing-execution', 0, 1, 'pending')",
                )
            # 非法引用：target 指向不存在的执行记录。
            with self.assertRaises(sqlite3.IntegrityError):
                await connection.execute(
                    "INSERT INTO delivery_targets "
                    "(execution_id, position, umo, status) "
                    "VALUES ('missing-execution', 0, 'x', 'pending')",
                )
        finally:
            await connection.execute("ROLLBACK TO fk_probe")
            await connection.execute("RELEASE fk_probe")

    async def _wait(self, event: asyncio.Event, label: str) -> None:
        try:
            await asyncio.wait_for(event.wait(), timeout=20)
        except asyncio.TimeoutError as exc:
            raise _BarrierTimeout(f"等待超时：{label}") from exc

    async def test_concurrent_migration_keeps_related_rows(self) -> None:
        path, ids = await self._rich_legacy("rich-race")
        first = SiteStore(path)
        second = SiteStore(path)
        first_task = second_task = None
        try:
            first_task = asyncio.create_task(first.open())
            second_task = asyncio.create_task(second.open())
            await asyncio.wait_for(
                asyncio.gather(first_task, second_task), timeout=30,
            )
            self.assertTrue(first.is_open)
            self.assertTrue(second.is_open)
            self.assertEqual(self._version(path), SCHEMA_VERSION)
            self.assertEqual(
                self._columns(path, "delivery_executions").count("owner_token"), 1,
            )
            self.assertNotIn("push_tasks_v7", self._tables(path))
            # 两个连接上的外键都已恢复启用。
            self.assertEqual(await self._foreign_keys_on(first), 1)
            self.assertEqual(await self._foreign_keys_on(second), 1)
            await self._assert_fk_definitions(path)
            await self._assert_fk_enforced(first, ids)
        finally:
            await self._drain(first_task, second_task)
            self.assertEqual(await self._close_all(first, second), [])
        await self._assert_related_rows(path, ids)

    async def test_second_connection_waits_for_write_lock(self) -> None:
        """第一个连接持锁时，第二个连接已发起 BEGIN IMMEDIATE 且停在锁上。"""
        path, ids = await self._rich_legacy("lock-wait")
        first = SiteStore(path)
        second = SiteStore(path)
        release = asyncio.Event()
        holding = asyncio.Event()
        begin_issued = asyncio.Event()
        loop = asyncio.get_running_loop()
        opened: list = []
        statements: list[str] = []
        begin_index: list[int] = []
        real_connect = aiosqlite.connect
        real_commit = aiosqlite.Connection.commit

        def on_second_sql(sql: str) -> None:
            # trace 回调在语句**执行前**触发（在连接线程里）。
            statements.append(sql)
            if sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                begin_index.append(len(statements) - 1)
                loop.call_soon_threadsafe(begin_issued.set)

        async def tracking_connect(*args, **kwargs):
            connection = await real_connect(*args, **kwargs)
            opened.append(connection)
            if len(opened) == 2:
                await connection.set_trace_callback(on_second_sql)
            return connection

        async def gated_commit(connection) -> None:
            # 迁移事务的提交点：此时第一个连接仍持有写锁。
            if not release.is_set():
                holding.set()
                await release.wait()
            await real_commit(connection)

        first_task = second_task = None
        try:
            with mock.patch.object(aiosqlite, "connect", tracking_connect), \
                    mock.patch.object(
                        aiosqlite.Connection, "commit", gated_commit,
                    ):
                first_task = asyncio.create_task(first.open())
                await self._wait(holding, "第一个连接到达迁移提交点")
                second_task = asyncio.create_task(second.open())
                await self._wait(begin_issued, "第二个连接发起 BEGIN IMMEDIATE")
                # 第一个连接仍在事务内（未提交），第二个连接已发出取锁语句，
                # 且该语句之后没有任何语句被执行过 —— 说明它正阻塞在锁上。
                self.assertFalse(first_task.done())
                self.assertEqual(
                    len(statements), begin_index[0] + 1,
                    f"BEGIN IMMEDIATE 之后不应有新语句：{statements!r}",
                )
                release.set()
                await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=30,
                )
            self.assertTrue(first.is_open)
            self.assertTrue(second.is_open)
            self.assertEqual(self._version(path), SCHEMA_VERSION)
            # 后进入者读到已提交版本，跳过重复 DDL。
            self.assertEqual(
                self._columns(path, "delivery_executions").count("owner_token"), 1,
            )
            self.assertEqual(await self._foreign_keys_on(second), 1)
            # 第二个连接在拿到锁后确实继续执行了迁移（语句序列增长）。
            self.assertGreater(len(statements), begin_index[0] + 1)
        finally:
            release.set()
            await self._drain(first_task, second_task)
            self.assertEqual(await self._close_all(first, second), [])
        self.assertGreaterEqual(len(opened), 2, "夹具未建立两个独立连接")
        await self._assert_related_rows(path, ids)

    async def test_failure_after_alter_rolls_back_all(self) -> None:
        """在 ALTER 与 push_tasks 重建都已执行后注入失败，验证完整回滚。"""
        path, ids = await self._rich_legacy("rollback-after-alter")
        real_commit = aiosqlite.Connection.commit
        observed: dict = {}

        async def hook_commit(connection) -> None:
            if observed:
                await real_commit(connection)
                return
            # 这里是迁移事务内、所有 ALTER 与重建之后、提交之前：
            # 用同一个连接读同一事务里的状态，然后注入可识别的失败。
            async def columns(table: str) -> set[str]:
                cursor = await connection.execute(f"PRAGMA table_info({table})")
                rows = await cursor.fetchall()
                await cursor.close()
                return {row[1] for row in rows}

            async def count(table: str) -> int:
                cursor = await connection.execute(f"SELECT COUNT(*) FROM {table}")
                row = await cursor.fetchone()
                await cursor.close()
                return int(row[0])

            async def has_table(table: str) -> bool:
                cursor = await connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = ?",
                    (table,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                return row is not None

            observed["owner_token"] = "owner_token" in await columns(
                "delivery_executions",
            )
            observed["image_generated"] = "image_generated" in await columns(
                "delivery_pages",
            )
            observed["schedule_kind"] = "schedule_kind" in await columns(
                "push_tasks",
            )
            observed["push_tasks_v7"] = await has_table("push_tasks_v7")
            observed["sites"] = await count("sites")
            observed["umos"] = await count("push_task_umos")
            observed["targets"] = await count("delivery_targets")
            observed["pages"] = await count("delivery_pages")
            raise _InjectedMigrationFailure("injected before commit")

        store = SiteStore(path)
        try:
            with mock.patch.object(
                aiosqlite.Connection, "commit", hook_commit,
            ):
                with self.assertRaises(SiteStoreError) as captured:
                    await asyncio.wait_for(store.open(), timeout=30)
        finally:
            problems = await self._close_all(store)
            self.assertEqual(problems, [])

        # 失败确实由注入的异常引起，而不是更早的任意错误。
        self.assertIsInstance(
            captured.exception.__cause__, _InjectedMigrationFailure,
            repr(captured.exception.__cause__),
        )
        # 钩子确实执行，且执行时 ALTER 与重建都已完成。
        self.assertTrue(observed, "注入钩子没有执行")
        self.assertTrue(observed["owner_token"], "注入时 owner_token 列应已存在")
        self.assertTrue(
            observed["image_generated"], "注入时 image_generated 列应已存在",
        )
        self.assertTrue(observed["schedule_kind"], "注入时 push_tasks 应已重建")
        self.assertFalse(observed["push_tasks_v7"], "重建后不应残留中间表")
        self.assertEqual(observed["sites"], 1)
        self.assertEqual(observed["umos"], 2)
        self.assertEqual(observed["targets"], 1)
        self.assertEqual(observed["pages"], 2)

        # 事务外：版本、列、表与关联数据全部回滚。
        self.assertEqual(self._version(path), 4)
        self.assertNotIn("owner_token", self._columns(path, "delivery_executions"))
        self.assertNotIn("image_generated", self._columns(path, "delivery_pages"))
        self.assertNotIn("source_kind", self._columns(path, "sites"))
        self.assertNotIn("schedule_kind", self._columns(path, "push_tasks"))
        self.assertNotIn("push_tasks_v7", self._tables(path))
        self.assertEqual(self._count(path, "sites"), 1)
        self.assertEqual(self._count(path, "push_task_umos"), 2)
        self.assertEqual(self._count(path, "delivery_targets"), 1)
        self.assertEqual(self._count(path, "delivery_pages"), 2)
        self.assertEqual(self._fk_violations(path), [])

        # 后续实例仍能完整迁移。
        retry = SiteStore(path)
        retry_task = None
        try:
            retry_task = asyncio.create_task(retry.open())
            await asyncio.wait_for(retry_task, timeout=30)
            self.assertEqual(self._version(path), SCHEMA_VERSION)
            self.assertEqual(
                self._columns(path, "delivery_executions").count("owner_token"), 1,
            )
            self.assertEqual(await self._foreign_keys_on(retry), 1)
            await self._assert_fk_definitions(path)
            await self._assert_fk_enforced(retry, ids)
        finally:
            await self._drain(retry_task)
            self.assertEqual(await self._close_all(retry), [])
        await self._assert_related_rows(path, ids)

if __name__ == "__main__":
    unittest.main()
