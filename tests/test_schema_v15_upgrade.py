"""课程（内容组合）并入任务后的库结构迁移测试。

v14→v15：组合去掉站点绑定，旧任务的候选按原绑定回填。
v15→v16：每个任务补建自己的方案行（id 等于任务 id），旧共享组合的栏目复制过来，
push_tasks.profile_id 指向任务自己；旧共享行保留，重复启动不重复复制。

夹具做法与 tests/test_site_store_migration_rollback.py 一致：先用当前代码建出完整
结构并写入真实数据，再改回旧版本的形状（列、profile_id、user_version），然后正常
打开触发迁移。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import core.site_store as site_store_module
from core.site_store import SCHEMA_VERSION, SiteStore, SiteStoreError
from core.sites import SiteCreate, SiteService, SiteStorageError
from core.tasks import PushTaskService, TaskCreate, TaskUpdate

UMOS = ["bot:GroupMessage:g", "bot:FriendMessage:f"]


def _item(content_type: str, limit: int | None = None) -> dict:
    return {"content_type": content_type, "params": {}, "limit": limit}


class _MigrationCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "legacy.sqlite3"

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _columns(self, table: str) -> set[str]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                f"PRAGMA table_info({table})",
            ).fetchall()
        return {row[1] for row in rows}

    def _user_version(self) -> int:
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(sql, params).fetchall()

    def _tables(self) -> set[str]:
        return {
            row[0] for row in self._query(
                "SELECT name FROM sqlite_master WHERE type = 'table'",
            )
        }

    _SNAPSHOT_TABLES = (
        "content_profiles",
        "content_profile_items",
        "push_tasks",
        "push_task_umos",
        "push_task_sites",
        "delivery_executions",
        "delivery_targets",
        "delivery_pages",
    )

    def _snapshot(self) -> dict[str, list[tuple]]:
        """整表行集合快照；回滚必须逐行复原，而不只是版本号变回去。"""
        return {
            table: sorted(self._query(f"SELECT * FROM {table}"), key=repr)
            for table in self._SNAPSHOT_TABLES
        }

    def _plan_items(self, plan_id: str) -> list[tuple]:
        return self._query(
            "SELECT position, content_type, limit_count "
            "FROM content_profile_items WHERE profile_id = ? ORDER BY position",
            (plan_id,),
        )


class SchemaV15UpgradeTest(_MigrationCase):
    """v14 库里的旧组合站点绑定要回填成任务候选，不被后来的 v16 破坏。"""

    def _rewind_to_v14(
        self,
        legacy_sites: dict[str, str],
        drop_candidates: tuple[str, ...] = (),
    ) -> None:
        """给方案表加回 site_id、写回绑定、删除指定任务的候选，再把版本调回 14。"""
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "ALTER TABLE content_profiles ADD COLUMN site_id TEXT",
            )
            for task_id, site_id in legacy_sites.items():
                connection.execute(
                    "UPDATE content_profiles SET site_id = ? WHERE id = ?",
                    (site_id, task_id),
                )
            for task_id in drop_candidates:
                connection.execute(
                    "DELETE FROM push_task_sites WHERE task_id = ?",
                    (task_id,),
                )
            connection.execute("PRAGMA user_version = 14")

    async def _seed(self) -> dict[str, object]:
        sites = SiteService(self.path)
        await sites.open()
        tasks = PushTaskService(sites.store)
        try:
            site_a = await sites.create_site(SiteCreate(
                name="旧站 A", base_url="http://a.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            site_b = await sites.create_site(SiteCreate(
                name="旧站 B", base_url="http://b.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            backfilled = await tasks.create_task(TaskCreate(
                name="旧单候选任务", items=[_item("daily_news", 3)],
                sites=[site_a.id], output_format="text", umos=list(UMOS),
            ))
            preserved = await tasks.create_task(TaskCreate(
                name="旧多候选任务", items=[_item("zhihu_hot", 2)],
                sites=[site_b.id, site_a.id], output_format="image",
                umos=[UMOS[0]],
            ))
            dropped = await tasks.create_task(TaskCreate(
                name="待回填任务",
                items=[_item("daily_news", 3), _item("bilibili_hot", 5)],
                sites=[site_a.id], output_format="markdown", umos=list(UMOS),
            ))
            return {
                "site_a": site_a, "site_b": site_b,
                "backfilled": backfilled, "preserved": preserved,
                "dropped": dropped,
            }
        finally:
            await sites.close()

    async def test_upgrade_backfills_candidates_and_keeps_configuration(
        self,
    ) -> None:
        seeded = await self._seed()
        backfilled = seeded["backfilled"]
        preserved = seeded["preserved"]
        dropped = seeded["dropped"]
        site_a = seeded["site_a"]
        site_b = seeded["site_b"]
        self._rewind_to_v14(
            {
                backfilled.id: site_a.id,
                preserved.id: site_a.id,
                dropped.id: site_b.id,
            },
            drop_candidates=(dropped.id,),
        )
        # v14 形状：方案表带 site_id，待回填任务没有候选行。
        self.assertIn("site_id", self._columns("content_profiles"))
        self.assertEqual(self._user_version(), 14)
        before = self._plan_items(dropped.id)
        self.assertEqual(
            [(row[1], row[2]) for row in before],
            [("daily_news", 3), ("bilibili_hot", 5)],
        )

        sites = SiteService(self.path)
        await sites.open()
        try:
            tasks = PushTaskService(sites.store)
            self.assertNotIn("site_id", self._columns("content_profiles"))
            self.assertEqual(self._user_version(), SCHEMA_VERSION)

            # 已有候选的任务保持原有顺序与内容。
            kept = await tasks.get_config_snapshot(preserved.id)
            self.assertEqual(
                [item.id for item in kept.candidates],
                [site_b.id, site_a.id],
            )
            untouched = await tasks.get_config_snapshot(backfilled.id)
            self.assertEqual(
                [item.id for item in untouched.candidates], [site_a.id],
            )
            # 没有候选行的旧任务按旧组合的站点绑定回填到第一位。
            migrated = await tasks.get_config_snapshot(dropped.id)
            self.assertEqual(
                [item.id for item in migrated.candidates], [site_b.id],
            )
            self.assertIsNotNone(migrated.candidates[0].revision)
            self.assertTrue(migrated.candidates[0].enabled)

            # 栏目、条数、名称、版本、白名单与输出格式都在迁移后保留。
            task = await tasks.get_task(dropped.id)
            self.assertEqual(
                [item.content_type for item in task.items],
                ["daily_news", "bilibili_hot"],
            )
            self.assertEqual([item.limit for item in task.items], [3, 5])
            self.assertEqual(task.name, "待回填任务")
            self.assertEqual(task.revision, dropped.revision)
            self.assertEqual(list(task.umos), UMOS)
            self.assertEqual(task.output_format, "markdown")
            # 两个站点的凭据与配置都还在（列表顺序由名称/时间决定，不作断言）。
            self.assertEqual(
                {item.id for item in await sites.list_sites()},
                {site_a.id, site_b.id},
            )
            # 迁移用的临时表必须清理干净。
            names = self._tables()
            self.assertNotIn("_profile_site_backup", names)
            self.assertNotIn("content_profiles_v15", names)
        finally:
            await sites.close()

    async def test_upgrade_ignores_legacy_site_for_existing_candidates(
        self,
    ) -> None:
        seeded = await self._seed()
        preserved = seeded["preserved"]
        site_a = seeded["site_a"]
        site_b = seeded["site_b"]
        self._rewind_to_v14({preserved.id: site_a.id})

        sites = SiteService(self.path)
        await sites.open()
        try:
            tasks = PushTaskService(sites.store)
            snapshot = await tasks.get_config_snapshot(preserved.id)
            # 第一位已被占用，回填被忽略，绝不重排用户显式配置的候选。
            self.assertEqual(
                [item.id for item in snapshot.candidates],
                [site_b.id, site_a.id],
            )
        finally:
            await sites.close()

    async def test_upgrade_failure_keeps_v14_database(self) -> None:
        seeded = await self._seed()
        dropped = seeded["dropped"]
        site_b = seeded["site_b"]
        self._rewind_to_v14({dropped.id: site_b.id}, drop_candidates=(dropped.id,))
        before = self._plan_items(dropped.id)
        before_snapshot = self._snapshot()

        with mock.patch.object(
            site_store_module, "_CREATE_SCHEDULE_STATE_TABLE", "NOT VALID SQL",
        ):
            sites = SiteService(self.path)
            with self.assertRaises(SiteStorageError) as caught:
                await sites.open()
            await sites.close()

        # 异常链指向注入的 DDL，而不是夹具或无关语句。
        self.assertIsInstance(caught.exception.__cause__, SiteStoreError)
        cause = caught.exception.__cause__.__cause__
        self.assertIsInstance(cause, sqlite3.OperationalError, repr(cause))
        self.assertIn("syntax error", str(cause), repr(cause))
        self.assertEqual(self._snapshot(), before_snapshot)

        # 提交前失败：旧库结构、版本、候选行都保持原样。
        self.assertIn("site_id", self._columns("content_profiles"))
        self.assertEqual(self._user_version(), 14)
        rows = self._query(
            "SELECT COUNT(*) FROM push_task_sites WHERE task_id = ?",
            (dropped.id,),
        )
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(self._plan_items(dropped.id), before)
        tables = self._tables()
        self.assertNotIn("_profile_site_backup", tables)
        self.assertNotIn("content_profiles_v15", tables)


class SchemaV16UpgradeTest(_MigrationCase):
    """v15 的共享组合要复制成任务私有方案行，并且可重复打开。"""

    def _rewind_to_v15(self, shared: dict[str, str]) -> None:
        """把任务私有方案行改成 v15 的共享组合形状，版本调回 15。

        同一个共享 id 被多个任务引用时只保留第一个任务的栏目，这正是 v15
        「一份组合被多个任务共用」的真实形状。
        """
        created: set[str] = set()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for task_id, shared_id in shared.items():
                if shared_id not in created:
                    connection.execute(
                        "INSERT INTO content_profiles "
                        "(id, name, revision, created_at, updated_at) "
                        "SELECT ?, name, revision, created_at, updated_at "
                        "FROM content_profiles WHERE id = ?",
                        (shared_id, task_id),
                    )
                    connection.execute(
                        "UPDATE content_profile_items SET profile_id = ? "
                        "WHERE profile_id = ?",
                        (shared_id, task_id),
                    )
                    created.add(shared_id)
                else:
                    connection.execute(
                        "DELETE FROM content_profile_items WHERE profile_id = ?",
                        (task_id,),
                    )
                connection.execute(
                    "DELETE FROM content_profiles WHERE id = ?", (task_id,),
                )
                connection.execute(
                    "UPDATE push_tasks SET profile_id = ? WHERE id = ?",
                    (shared_id, task_id),
                )
            connection.execute("PRAGMA user_version = 15")

    async def _seed(
        self,
        first_items,
        second_items,
        *,
        shared: bool,
        with_history: bool = False,
    ) -> dict:
        sites = SiteService(self.path)
        await sites.open()
        tasks = PushTaskService(sites.store)
        try:
            site = await sites.create_site(SiteCreate(
                name="旧站", base_url="http://old.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            first = await tasks.create_task(TaskCreate(
                name="旧任务一", items=first_items, sites=[site.id],
                output_format="text", umos=list(UMOS),
            ))
            second = await tasks.create_task(TaskCreate(
                name="旧任务二", items=second_items, sites=[site.id],
                output_format="markdown", umos=[UMOS[0]],
            ))
            if with_history:
                # 历史投递记录在 v15 里按共享组合 id 与当时的版本保存；迁移
                # 不得改写这两列（执行记录是审计数据，不是活配置）。
                for task, revision in ((first, 3), (second, 5)):
                    await sites.store.create_delivery_execution(
                        execution_id=f"exec-{task.id}",
                        task_id=task.id,
                        task_name=task.name,
                        task_revision=task.revision,
                        profile_id="legacy-shared",
                        profile_revision=revision,
                        site_id=site.id,
                        site_revision=site.revision,
                        output_format=task.output_format,
                        umos=tuple(task.umos),
                    )
            mapping = (
                {first.id: "legacy-shared", second.id: "legacy-shared"}
                if shared
                else {first.id: "legacy-shared-1", second.id: "legacy-shared-2"}
            )
            self._rewind_to_v15(mapping)
            return {
                "site": site, "first": first, "second": second,
                "mapping": mapping,
            }
        finally:
            await sites.close()

    async def test_shared_plan_is_copied_to_one_row_per_task(self) -> None:
        seeded = await self._seed(
            [_item("daily_news", 2), _item("zhihu_hot", None)],
            [_item("bilibili_hot", 4)],
            shared=False,
        )
        first = seeded["first"]
        second = seeded["second"]
        mapping = seeded["mapping"]
        self.assertEqual(self._user_version(), 15)
        # v15 形状：任务指向别人的组合行，自身没有方案行。
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM content_profiles WHERE id IN (?, ?)",
                (first.id, second.id),
            )[0][0],
            0,
        )

        sites = SiteService(self.path)
        await sites.open()
        try:
            self.assertEqual(self._user_version(), SCHEMA_VERSION)
            tasks = PushTaskService(sites.store)
            plan_ids = {
                row[0]
                for row in self._query("SELECT profile_id FROM push_tasks")
            }
            # profile_id 指向任务自己，任务 id 就是私有方案行 id。
            self.assertEqual(plan_ids, {first.id, second.id})
            # 旧共享行仍在库里，只是不再被运行路径读取。
            self.assertEqual(
                self._query(
                    "SELECT COUNT(*) FROM content_profiles WHERE id IN (?, ?)",
                    (mapping[first.id], mapping[second.id]),
                )[0][0],
                2,
            )
            self.assertEqual(
                [(row[1], row[2]) for row in self._plan_items(first.id)],
                [("daily_news", 2), ("zhihu_hot", None)],
            )
            self.assertEqual(
                [(row[1], row[2]) for row in self._plan_items(second.id)],
                [("bilibili_hot", 4)],
            )
            migrated = await tasks.get_task(first.id)
            self.assertEqual(
                [item.content_type for item in migrated.items],
                ["daily_news", "zhihu_hot"],
            )
            self.assertEqual([item.limit for item in migrated.items], [2, None])
            self.assertEqual(migrated.profile_id, first.id)
            self.assertEqual(
                (await tasks.get_config_snapshot(first.id)).plan_revision, 1,
            )
        finally:
            await sites.close()

        # 第二次启动不重复复制栏目，也不改版本：栏目行总数保持 3。
        again = SiteService(self.path)
        await again.open()
        try:
            self.assertEqual(self._user_version(), SCHEMA_VERSION)
            self.assertEqual(len(self._plan_items(first.id)), 2)
            self.assertEqual(len(self._plan_items(second.id)), 1)
            self.assertEqual(
                self._query(
                    "SELECT COUNT(*) FROM content_profile_items "
                    "WHERE profile_id IN (?, ?)",
                    (first.id, second.id),
                )[0][0],
                3,
            )
            # 旧共享行连同栏目都保留，只是不再被任务读取。
            self.assertEqual(
                self._query("SELECT COUNT(*) FROM content_profile_items")[0][0],
                6,
            )
            self.assertEqual(
                self._query("SELECT COUNT(*) FROM content_profiles")[0][0], 4,
            )
        finally:
            await again.close()

    async def test_tasks_that_shared_one_plan_get_independent_copies(self) -> None:
        seeded = await self._seed(
            [_item("daily_news", 2)],
            [_item("zhihu_hot", 3)],
            shared=True,
        )
        first = seeded["first"]
        second = seeded["second"]
        shared_id = seeded["mapping"][first.id]

        sites = SiteService(self.path)
        await sites.open()
        try:
            tasks = PushTaskService(sites.store)
            # 两个任务从同一份共享组合各复制一份自己的栏目。
            for task_id in (first.id, second.id):
                self.assertEqual(
                    [(row[1], row[2]) for row in self._plan_items(task_id)],
                    [("daily_news", 2)],
                )
            shared_items = self._plan_items(shared_id)
            edited = await tasks.update_task(first.id, TaskUpdate(
                name="旧任务一",
                items=[_item("toutiao_hot", 7)],
                output_format="text",
                umos=list(UMOS),
                expected_revision=first.revision,
            ))
            self.assertEqual(
                [item.content_type for item in edited.items], ["toutiao_hot"],
            )
            other = await tasks.get_task(second.id)
            self.assertEqual(
                [item.content_type for item in other.items], ["daily_news"],
            )
            self.assertEqual((await tasks.get_config_snapshot(second.id)).plan_revision, 1)
            # 编辑任务只提升自己那份方案，旧共享行一个字节都不动。
            self.assertEqual(self._plan_items(shared_id), shared_items)
        finally:
            await sites.close()

    async def test_v16_failure_rolls_back_to_v15_data(self) -> None:
        """v16 复制到第二个任务时失败：来源可证、整体回滚、排除故障后可重迁。

        故障由触发器 RAISE(FAIL) 注入：FAIL 不撤销同一语句已写入的行，所以
        回滚前的同连接观测能区分「复制到一半」与「还没开始复制」。
        """
        seeded = await self._seed(
            [_item("daily_news", 2), _item("douyin_hot", 1)],
            [_item("zhihu_hot", 3)],
            shared=True,
            with_history=True,
        )
        first = seeded["first"]
        second = seeded["second"]
        shared_id = seeded["mapping"][first.id]
        self.assertEqual(seeded["mapping"][second.id], shared_id)
        names = {first.id: first.name, second.id: second.name}
        # 迁移按 t.id 升序复制，所以 id 较小的任务先写完自己的栏目。
        earlier, later = sorted((first.id, second.id))
        before = self._snapshot()
        shared_items = self._plan_items(shared_id)
        self.assertEqual(
            [(row[1], row[2]) for row in shared_items],
            [("daily_news", 2), ("douyin_hot", 1)],
        )
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM content_profile_items "
                "WHERE profile_id IN (?, ?)",
                (first.id, second.id),
            )[0][0],
            0,
            "v15 夹具里两个任务都不该已有私有栏目",
        )

        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER injected_v16_copy_failure "
                "BEFORE INSERT ON content_profile_items "
                f"WHEN NEW.profile_id = '{later}' "
                "AND (SELECT COUNT(*) FROM content_profile_items "
                f"WHERE profile_id = '{earlier}') = {len(shared_items)} "
                "BEGIN SELECT RAISE(FAIL, "
                "'injected v16 failure after first task copy'); END",
            )

        observed: list[dict] = []
        real_rollback = SiteStore._rollback_quietly

        async def capture_rollback(connection) -> None:
            # 迁移的 except 分支在回滚前调用这里，用同一连接读同一事务里
            # 已经写入、尚未提交的状态。
            if not observed:
                async def fetch(sql: str, params: tuple = ()) -> list[tuple]:
                    cursor = await connection.execute(sql, params)
                    rows = await cursor.fetchall()
                    await cursor.close()
                    return [tuple(row) for row in rows]

                observed.append({
                    "version": (await fetch("PRAGMA user_version"))[0][0],
                    "plans": await fetch(
                        "SELECT id, name FROM content_profiles "
                        "WHERE id IN (?, ?) ORDER BY id",
                        (first.id, second.id),
                    ),
                    "items": await fetch(
                        "SELECT profile_id, position, content_type, limit_count "
                        "FROM content_profile_items "
                        "WHERE profile_id IN (?, ?) "
                        "ORDER BY profile_id, position",
                        (first.id, second.id),
                    ),
                })
            await real_rollback(connection)

        sites = SiteService(self.path)
        with mock.patch.object(
            SiteStore, "_rollback_quietly", staticmethod(capture_rollback),
        ):
            with self.assertRaises(SiteStorageError) as caught:
                await sites.open()
        await sites.close()

        # 1) 失败来自注入的迁移故障，而不是夹具或无关 SQL。
        self.assertIsInstance(caught.exception.__cause__, SiteStoreError)
        cause = caught.exception.__cause__.__cause__
        self.assertIsInstance(cause, sqlite3.IntegrityError, repr(cause))
        self.assertIn(
            "injected v16 failure after first task copy", str(cause), repr(cause),
        )
        self.assertEqual(len(observed), 1, "迁移失败时必须走回滚钩子")
        seen = observed[0]
        # 2) 失败发生在已有迁移写入之后：两个私有方案行都建好，先复制的任务
        #    栏目已齐，后一个任务一行都没写，版本号仍停在上一个版本。
        self.assertEqual(seen["version"], 15)
        self.assertEqual(
            seen["plans"],
            sorted((task_id, names[task_id])
                   for task_id in (first.id, second.id)),
        )
        self.assertEqual(
            seen["items"],
            [
                (earlier, 0, "daily_news", 2),
                (earlier, 1, "douyin_hot", 1),
            ],
        )

        # 3) 回滚后版本、任务行、共享栏目、UMO/候选顺序、投递历史逐项不变。
        self.assertEqual(self._user_version(), 15)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(
            [row[0] for row in self._query("SELECT id FROM content_profiles")],
            [shared_id],
        )
        self.assertEqual(self._plan_items(shared_id), shared_items)
        task_rows = {
            row[0]: row
            for row in self._query(
                "SELECT id, profile_id, name, output_format, enabled, revision "
                "FROM push_tasks ORDER BY id",
            )
        }
        self.assertEqual(set(task_rows), {first.id, second.id})
        for task, output_format in ((first, "text"), (second, "markdown")):
            self.assertEqual(
                task_rows[task.id],
                (
                    task.id, shared_id, task.name, output_format,
                    int(task.enabled), task.revision,
                ),
            )
        self.assertEqual(
            self._query(
                "SELECT task_id, position, umo FROM push_task_umos "
                "ORDER BY task_id, position",
            ),
            sorted(
                (task.id, position, umo)
                for task, umos in ((first, UMOS), (second, [UMOS[0]]))
                for position, umo in enumerate(umos)
            ),
        )
        self.assertEqual(
            self._query(
                "SELECT task_id, position, site_id FROM push_task_sites "
                "ORDER BY task_id, position",
            ),
            sorted((task.id, 0, seeded["site"].id) for task in (first, second)),
        )
        executions = {
            row[0]: row
            for row in self._query(
                "SELECT id, task_id, task_name, profile_id, profile_revision, "
                "site_id, site_revision, output_format, status, error_code "
                "FROM delivery_executions ORDER BY id",
            )
        }
        self.assertEqual(
            set(executions), {f"exec-{first.id}", f"exec-{second.id}"},
        )
        for task, revision in ((first, 3), (second, 5)):
            row = executions[f"exec-{task.id}"]
            self.assertEqual(row[1], task.id)
            self.assertEqual(row[2], task.name)
            self.assertEqual(row[3], shared_id)
            self.assertEqual(row[4], revision)
            self.assertEqual(row[5], seeded["site"].id)
            self.assertEqual(row[7], task.output_format)
            # 回滚发生在本轮恢复投递历史之前：仍是创建时的状态。
            self.assertEqual(row[8], "preparing")
            self.assertIsNone(row[9])
        self.assertEqual(
            self._query("SELECT COUNT(*) FROM delivery_targets")[0][0],
            len(UMOS) + 1,
        )
        self.assertEqual(
            self._query("SELECT COUNT(*) FROM delivery_pages")[0][0], 0,
        )

        # 4) 排除故障后同一个文件能正常迁移到 16，且栏目按任务复制。
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("DROP TRIGGER injected_v16_copy_failure")
        self.assertEqual(
            self._query(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' AND name = 'injected_v16_copy_failure'",
            )[0][0],
            0,
        )
        sites = SiteService(self.path)
        await sites.open()
        try:
            self.assertEqual(self._user_version(), SCHEMA_VERSION)
            tasks = PushTaskService(sites.store)
            for task in (first, second):
                migrated = await tasks.get_task(task.id)
                self.assertEqual(migrated.profile_id, task.id)
                self.assertEqual(
                    [(row[1], row[2]) for row in self._plan_items(task.id)],
                    [("daily_news", 2), ("douyin_hot", 1)],
                )
            self.assertEqual(self._plan_items(shared_id), shared_items)
            for task, revision in ((first, 3), (second, 5)):
                history = await sites.store.get_delivery_execution(
                    f"exec-{task.id}",
                )
                self.assertEqual(history.profile_id, shared_id)
                self.assertEqual(history.profile_revision, revision)
                self.assertEqual(history.status, "stopped")
                self.assertEqual(history.error_code, "never_started")
            counts = self._query(
                "SELECT profile_id, COUNT(*) FROM content_profile_items "
                "GROUP BY profile_id ORDER BY profile_id",
            )
            self.assertEqual([row[1] for row in counts], [2, 2, 2], counts)
        finally:
            await sites.close()

        # 5) 再次启动不重复复制栏目。
        again = SiteService(self.path)
        await again.open()
        try:
            self.assertEqual(self._user_version(), SCHEMA_VERSION)
            self.assertEqual(
                self._query(
                    "SELECT profile_id, COUNT(*) FROM content_profile_items "
                    "GROUP BY profile_id ORDER BY profile_id",
                ),
                counts,
            )
        finally:
            await again.close()

    async def test_shared_plan_tasks_keep_own_schedule_umos_sites_and_history(
        self,
    ) -> None:
        """多任务共享旧组合：迁移后各自独立，计划/白名单/候选/历史语义不变。"""
        schedules = (
            {"kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30"},
            {
                "kind": "weekly", "timezone": "Asia/Shanghai",
                "time": "21:05", "weekdays": [2, 5],
            },
        )
        sites = SiteService(self.path)
        await sites.open()
        try:
            site_a = await sites.create_site(SiteCreate(
                name="站点 A", base_url="http://a.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            site_b = await sites.create_site(SiteCreate(
                name="站点 B", base_url="http://b.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            tasks = PushTaskService(sites.store)
            first = await tasks.create_task(TaskCreate(
                name="共享任务一",
                items=[_item("daily_news", 2)],
                sites=[site_a.id, site_b.id],
                output_format="text",
                umos=list(UMOS),
                schedule=schedules[0],
            ))
            second = await tasks.create_task(TaskCreate(
                name="共享任务二",
                items=[_item("bilibili_hot", 2)],
                sites=[site_b.id],
                output_format="markdown",
                umos=[UMOS[0]],
                schedule=schedules[1],
            ))
            for task, revision in ((first, 3), (second, 5)):
                await sites.store.create_delivery_execution(
                    execution_id=f"exec-{task.id}",
                    task_id=task.id,
                    task_name=task.name,
                    task_revision=task.revision,
                    profile_id="legacy-shared",
                    profile_revision=revision,
                    site_id=site_b.id,
                    site_revision=site_b.revision,
                    output_format=task.output_format,
                    umos=tuple(task.umos),
                )
        finally:
            await sites.close()

        # 退回 v15 形态：两个任务共用一个旧组合。
        self._rewind_to_v15({
            first.id: "legacy-shared",
            second.id: "legacy-shared",
        })
        self.assertEqual(self._user_version(), 15)
        shared_items = self._plan_items("legacy-shared")
        self.assertEqual([row[1] for row in shared_items], ["daily_news"])

        sites = SiteService(self.path)
        await sites.open()
        try:
            self.assertEqual(self._user_version(), SCHEMA_VERSION)
            tasks = PushTaskService(sites.store)
            # 各自拥有独立栏目；旧共享行保留但不再被任务读取。
            for task in (first, second):
                self.assertEqual(
                    [(row[1], row[2]) for row in self._plan_items(task.id)],
                    [("daily_news", 2)],
                )
            self.assertEqual(self._plan_items("legacy-shared"), shared_items)
            migrated_first = await tasks.get_task(first.id)
            migrated_second = await tasks.get_task(second.id)
            self.assertEqual(migrated_first.profile_id, first.id)
            self.assertEqual(migrated_second.profile_id, second.id)
            # 计划、UMO 顺序、候选顺序都保留原语义。
            self.assertEqual(
                migrated_first.schedule.to_dict(), first.schedule.to_dict(),
            )
            self.assertEqual(
                migrated_second.schedule.to_dict(), second.schedule.to_dict(),
            )
            self.assertEqual(migrated_first.schedule_raw, first.schedule_raw)
            self.assertEqual(migrated_second.schedule_raw, second.schedule_raw)
            self.assertEqual(list(migrated_first.umos), list(first.umos))
            self.assertEqual(list(migrated_second.umos), [UMOS[0]])
            self.assertEqual(list(migrated_first.sites), [site_a.id, site_b.id])
            self.assertEqual(list(migrated_second.sites), [site_b.id])
            # 历史投递记录仍记着共享组合 id 与当时的版本。
            for task, revision in ((first, 3), (second, 5)):
                history = await sites.store.get_delivery_execution(
                    f"exec-{task.id}",
                )
                self.assertEqual(history.task_id, task.id)
                self.assertEqual(history.profile_id, "legacy-shared")
                self.assertEqual(history.profile_revision, revision)
                self.assertEqual(history.site_id, site_b.id)
                self.assertEqual(history.status, "stopped")
                self.assertEqual(history.error_code, "never_started")
            self.assertEqual(
                self._query(
                    "SELECT task_id, profile_id, profile_revision "
                    "FROM delivery_executions ORDER BY task_id",
                ),
                sorted(
                    (task.id, "legacy-shared", revision)
                    for task, revision in ((first, 3), (second, 5))
                ),
            )
            # 独立编辑只影响自己那份组合。
            edited = await tasks.update_task(first.id, TaskUpdate(
                name="共享任务一",
                items=[_item("toutiao_hot", 7)],
                output_format="text",
                umos=[UMOS[1]],
                expected_revision=first.revision,
            ))
            self.assertEqual(
                [item.content_type for item in edited.items], ["toutiao_hot"],
            )
            other = await tasks.get_task(second.id)
            self.assertEqual(
                [item.content_type for item in other.items], ["daily_news"],
            )
            self.assertEqual(
                (await tasks.get_config_snapshot(second.id)).plan_revision,
                second.revision,
            )
            self.assertEqual(self._plan_items("legacy-shared"), shared_items)
            counts = dict(self._query(
                "SELECT profile_id, COUNT(*) FROM content_profile_items "
                "GROUP BY profile_id",
            ))
            self.assertEqual(
                counts,
                {first.id: 1, second.id: 1, "legacy-shared": 1},
            )
        finally:
            await sites.close()

        # 二次启动不重复复制。
        again = SiteService(self.path)
        await again.open()
        try:
            self.assertEqual(self._user_version(), SCHEMA_VERSION)
            self.assertEqual(
                dict(self._query(
                    "SELECT profile_id, COUNT(*) FROM content_profile_items "
                    "GROUP BY profile_id",
                )),
                counts,
            )
        finally:
            await again.close()
    async def test_migration_keeps_schedule_umos_candidate_order_and_history(
        self,
    ) -> None:
        """v16 只改方案归属，不碰计划、白名单、候选顺序和投递历史。"""
        sites = SiteService(self.path)
        await sites.open()
        try:
            site_a = await sites.create_site(SiteCreate(
                name="站点 A", base_url="http://a.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            site_b = await sites.create_site(SiteCreate(
                name="站点 B", base_url="http://b.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            tasks = PushTaskService(sites.store)
            # 候选顺序 B→A 与 UMO 顺序都是任务语义的一部分，迁移必须原样保留。
            task = await tasks.create_task(TaskCreate(
                name="带计划的任务",
                items=[_item("daily_news", 2), _item("zhihu_hot", None)],
                sites=[site_b.id, site_a.id],
                output_format="markdown",
                umos=[UMOS[1], UMOS[0]],
                schedule={
                    "kind": "daily", "timezone": "Asia/Shanghai",
                    "time": "08:30",
                },
            ))
            await sites.store.create_delivery_execution(
                execution_id="exec-legacy",
                task_id=task.id,
                task_name=task.name,
                task_revision=task.revision,
                profile_id="legacy-shared",
                profile_revision=3,
                site_id=site_b.id,
                site_revision=site_b.revision,
                output_format="markdown",
                umos=(UMOS[0],),
            )
            self._rewind_to_v15({task.id: "legacy-shared"})
        finally:
            await sites.close()
        self.assertEqual(self._user_version(), 15)

        for _ in range(2):
            sites = SiteService(self.path)
            await sites.open()
            try:
                self.assertEqual(self._user_version(), SCHEMA_VERSION)
                tasks = PushTaskService(sites.store)
                migrated = await tasks.get_task(task.id)
                self.assertEqual(
                    [item.content_type for item in migrated.items],
                    ["daily_news", "zhihu_hot"],
                )
                self.assertEqual([item.limit for item in migrated.items], [2, None])
                self.assertEqual(migrated.schedule.to_dict(), task.schedule.to_dict())
                self.assertEqual(migrated.umos, (UMOS[1], UMOS[0]))
                self.assertEqual(migrated.sites, (site_b.id, site_a.id))
                self.assertEqual(migrated.output_format, "markdown")
                self.assertEqual(migrated.revision, task.revision)
                snapshot = await tasks.get_config_snapshot(task.id)
                self.assertEqual(
                    [(item.id, item.enabled) for item in snapshot.candidates],
                    [(site_b.id, True), (site_a.id, True)],
                )
                # 历史投递记录仍指向当时的方案行与站点，不被迁移改写；
                # 未完成的执行按既有恢复规则标记为 stopped，不当作已投递。
                history = await sites.store.get_delivery_execution("exec-legacy")
                self.assertEqual(history.task_id, task.id)
                self.assertEqual(history.profile_id, "legacy-shared")
                self.assertEqual(history.profile_revision, 3)
                self.assertEqual(history.site_id, site_b.id)
                self.assertEqual(history.status, "stopped")
                self.assertEqual(history.error_code, "never_started")
            finally:
                await sites.close()

class FreshDatabaseSchemaTest(_MigrationCase):
    """新库直接是 v16：方案表存在，且方案不再带 site_id 列。"""

    async def test_fresh_database_has_plan_tables(self) -> None:
        store = SiteStore(self.path)
        await store.open()
        await store.close()

        tables = self._tables()
        self.assertIn("content_profiles", tables)
        self.assertIn("content_profile_items", tables)
        self.assertIn("sites", tables)
        self.assertNotIn("core_profiles", tables)
        columns = self._columns("content_profiles")
        self.assertEqual(
            columns, {"id", "name", "revision", "created_at", "updated_at"},
        )
        self.assertNotIn("site_id", columns)
        self.assertEqual(self._user_version(), SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
