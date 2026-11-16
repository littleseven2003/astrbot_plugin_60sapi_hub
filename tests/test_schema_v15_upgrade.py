"""v14 数据库升级到 v15：组合去掉站点后，旧任务的候选必须原样保留。

夹具做法与 tests/test_site_store_migration_rollback.py 一致：先用当前代码
建出完整结构并写入真实形状的数据，再加回 site_id 列、删掉需要的候选行、
把 user_version 调回 14，得到“v15 之前”的形状，然后正常打开触发迁移。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import core.site_store as site_store_module
from core.profiles import ContentProfileService, ProfileCreate
from core.site_store import SiteStore
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMOS = ["bot:GroupMessage:g", "bot:FriendMessage:f"]


class SchemaV15UpgradeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "legacy.sqlite3"

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _rewind_to_v14(
        self,
        legacy_sites: dict[str, str],
        drop_candidates: tuple[str, ...] = (),
    ) -> None:
        """给组合表加回 site_id、写回绑定、删除指定任务的候选，再把版本调回 14。"""
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "ALTER TABLE content_profiles ADD COLUMN site_id TEXT",
            )
            for profile_id, site_id in legacy_sites.items():
                connection.execute(
                    "UPDATE content_profiles SET site_id = ? WHERE id = ?",
                    (site_id, profile_id),
                )
            for task_id in drop_candidates:
                connection.execute(
                    "DELETE FROM push_task_sites WHERE task_id = ?",
                    (task_id,),
                )
            connection.execute("PRAGMA user_version = 14")

    def _columns(self, table: str) -> set[str]:
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                f"PRAGMA table_info({table})",
            ).fetchall()
        return {row[1] for row in rows}

    def _user_version(self) -> int:
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    async def _seed(self) -> dict[str, object]:
        sites = SiteService(self.path)
        await sites.open()
        profiles = ContentProfileService(sites)
        tasks = PushTaskService(profiles, sites.store)
        try:
            site_a = await sites.create_site(SiteCreate(
                name="旧站 A", base_url="http://a.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            site_b = await sites.create_site(SiteCreate(
                name="旧站 B", base_url="http://b.example.test/60s",
                enabled=True, timeout_seconds=5,
            ))
            single = await profiles.create_profile(ProfileCreate(
                name="旧单栏目组合",
                items=[{"content_type": "daily_news", "params": {}, "limit": 3}],
            ))
            double = await profiles.create_profile(ProfileCreate(
                name="旧双栏目组合",
                items=[
                    {"content_type": "daily_news", "params": {}, "limit": 3},
                    {"content_type": "bilibili_hot", "params": {}, "limit": 5},
                ],
            ))
            ordered = await profiles.create_profile(ProfileCreate(
                name="旧多候选组合",
                items=[{"content_type": "zhihu_hot", "params": {}, "limit": 2}],
            ))
            backfilled = await tasks.create_task(TaskCreate(
                name="旧单候选任务", profile_id=single.id,
                sites=[site_a.id], output_format="text", umos=list(UMOS),
            ))
            preserved = await tasks.create_task(TaskCreate(
                name="旧多候选任务", profile_id=ordered.id,
                sites=[site_b.id, site_a.id], output_format="image",
                umos=[UMOS[0]],
            ))
            dropped = await tasks.create_task(TaskCreate(
                name="待回填任务", profile_id=double.id,
                sites=[site_a.id], output_format="markdown", umos=list(UMOS),
            ))
            return {
                "site_a": site_a, "site_b": site_b,
                "single": single, "double": double, "ordered": ordered,
                "backfilled": backfilled, "preserved": preserved,
                "dropped": dropped,
            }
        finally:
            await sites.close()

    async def test_upgrade_backfills_candidates_and_keeps_configuration(
        self,
    ) -> None:
        seeded = await self._seed()
        single = seeded["single"]
        double = seeded["double"]
        ordered = seeded["ordered"]
        backfilled = seeded["backfilled"]
        preserved = seeded["preserved"]
        dropped = seeded["dropped"]
        site_a = seeded["site_a"]
        site_b = seeded["site_b"]
        self._rewind_to_v14(
            {
                single.id: site_a.id,
                double.id: site_b.id,
                ordered.id: site_a.id,
            },
            drop_candidates=(dropped.id,),
        )
        # v14 形状：组合表带 site_id，待回填任务没有候选行。
        self.assertIn("site_id", self._columns("content_profiles"))
        self.assertEqual(self._user_version(), 14)

        sites = SiteService(self.path)
        await sites.open()
        try:
            profiles = ContentProfileService(sites)
            tasks = PushTaskService(profiles, sites.store)
            self.assertNotIn("site_id", self._columns("content_profiles"))
            self.assertEqual(self._user_version(), 15)

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
            # 没有候选行的旧任务按组合曾经的绑定回填到第一位。
            migrated = await tasks.get_config_snapshot(dropped.id)
            self.assertEqual(
                [item.id for item in migrated.candidates], [site_b.id],
            )
            self.assertIsNotNone(migrated.candidates[0].revision)
            self.assertTrue(migrated.candidates[0].enabled)

            # 栏目、条数、名称、版本、白名单与输出格式都在迁移后保留。
            profile = await profiles.get_profile(double.id)
            self.assertEqual(profile.name, "旧双栏目组合")
            self.assertEqual(
                [item.content_type for item in profile.items],
                ["daily_news", "bilibili_hot"],
            )
            self.assertEqual(
                [item.limit for item in profile.items], [3, 5],
            )
            self.assertEqual(profile.revision, double.revision)
            task = await tasks.get_task(dropped.id)
            self.assertEqual(list(task.umos), UMOS)
            self.assertEqual(task.output_format, "markdown")
            self.assertEqual(task.revision, dropped.revision)
            # 两个站点的凭据与配置都还在（列表顺序由名称/时间决定，不作断言）。
            self.assertEqual(
                {item.id for item in await sites.list_sites()},
                {site_a.id, site_b.id},
            )
            # 迁移用的临时表必须清理干净。
            with closing(sqlite3.connect(self.path)) as connection:
                names = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'",
                    )
                }
            self.assertNotIn("_profile_site_backup", names)
            self.assertNotIn("content_profiles_v15", names)
        finally:
            await sites.close()

    async def test_upgrade_ignores_profile_site_for_existing_candidates(
        self,
    ) -> None:
        seeded = await self._seed()
        ordered = seeded["ordered"]
        preserved = seeded["preserved"]
        site_a = seeded["site_a"]
        site_b = seeded["site_b"]
        self._rewind_to_v14({ordered.id: site_a.id})

        sites = SiteService(self.path)
        await sites.open()
        try:
            tasks = PushTaskService(
                ContentProfileService(sites), sites.store,
            )
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
        double = seeded["double"]
        dropped = seeded["dropped"]
        site_b = seeded["site_b"]
        self._rewind_to_v14({double.id: site_b.id}, drop_candidates=(dropped.id,))

        with mock.patch.object(
            site_store_module, "_CREATE_SCHEDULE_STATE_TABLE", "NOT VALID SQL",
        ):
            sites = SiteService(self.path)
            with self.assertRaises(Exception):
                await sites.open()
            await sites.close()

        # 提交前失败：旧库结构、版本、候选行都保持原样。
        self.assertIn("site_id", self._columns("content_profiles"))
        self.assertEqual(self._user_version(), 14)
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT COUNT(*) FROM push_task_sites WHERE task_id = ?",
                (dropped.id,),
            ).fetchone()
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'",
                )
            }
        self.assertEqual(rows[0], 0)
        self.assertNotIn("_profile_site_backup", tables)
        self.assertNotIn("content_profiles_v15", tables)


if __name__ == "__main__":
    unittest.main()
