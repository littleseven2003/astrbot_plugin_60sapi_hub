from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.profiles import ContentProfileService, ProfileCreate
from core.site_store import SCHEMA_VERSION, SiteStore
from core.sites import SiteCreate, SiteReferencedProfilesError, SiteService
from core.tasks import PushTaskService, TaskCreate, TaskNotFoundError
from core.tasks import TaskUpdate, TaskValidationError

ITEMS = [{"content_type": "daily_news", "params": {}, "limit": 2}]


class TaskSitesTest(unittest.IsolatedAsyncioTestCase):
    """任务候选站点的顺序、迁移与引用保护。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_sites.sqlite3"
        await self._open()

    async def asyncTearDown(self) -> None:
        await self._close()
        self._tmp.cleanup()

    async def _open(self) -> None:
        self.sites = SiteService(self.path)
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)

    async def _close(self) -> None:
        await self.sites.close()

    async def _site(self, name: str, *, kind: str = "public"):
        return await self.sites.create_site(
            SiteCreate(
                name=name,
                base_url=f"https://{name}.example.test/proxy",
                enabled=True,
                timeout_seconds=5,
                source_kind=kind,
            ),
        )

    async def _profile(self, site_id: str):
        return await self.profiles.create_profile(
            ProfileCreate(name="组合", site_id=site_id, items=ITEMS),
        )

    async def _task(self, profile_id: str, sites=None):
        return await self.tasks.create_task(
            TaskCreate(
                name="任务",
                profile_id=profile_id,
                output_format="text",
                umos=["aiocqhttp:GroupMessage:g:1"],
                sites=sites,
            ),
        )

    async def test_candidate_order_is_preserved(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        third = await self._site("c")
        profile = await self._profile(first.id)
        task = await self._task(profile.id, sites=[third.id, first.id, second.id])
        self.assertEqual(task.sites, (third.id, first.id, second.id))
        loaded = await self.tasks.get_task(task.id)
        self.assertEqual(loaded.sites, (third.id, first.id, second.id))

    async def test_update_reorders_and_persists_after_reopen(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        profile = await self._profile(first.id)
        task = await self._task(profile.id, sites=[first.id, second.id])
        updated = await self.tasks.update_task(
            task.id,
            TaskUpdate(
                name=task.name,
                profile_id=profile.id,
                output_format="text",
                umos=list(task.umos),
                expected_revision=task.revision,
                sites=[second.id, first.id],
            ),
        )
        self.assertEqual(updated.sites, (second.id, first.id))
        await self._close()
        await self._open()
        reloaded = await self.tasks.get_task(task.id)
        self.assertEqual(reloaded.sites, (second.id, first.id))
        self.assertEqual(reloaded.revision, updated.revision)

    async def test_default_candidates_follow_profile_site(self) -> None:
        site = await self._site("a", kind="self_built")
        profile = await self._profile(site.id)
        task = await self._task(profile.id)
        self.assertEqual(task.sites, (site.id,))
        # 明确选择的自建站点不应被改成公共站点。
        loaded = await self.sites.get_site(site.id)
        self.assertEqual(loaded.source_kind, "self_built")

    async def test_invalid_candidates_are_rejected(self) -> None:
        site = await self._site("a")
        profile = await self._profile(site.id)
        with self.assertRaises(TaskValidationError):
            await self._task(profile.id, sites=[])
        with self.assertRaises(TaskValidationError):
            await self._task(profile.id, sites=[""])
        with self.assertRaises(TaskNotFoundError):
            await self._task(profile.id, sites=["missing-site"])

    async def test_site_delete_blocked_by_task_reference(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        profile = await self._profile(first.id)
        await self._task(profile.id, sites=[first.id, second.id])
        with self.assertRaises(SiteReferencedProfilesError):
            await self.sites.delete_site(second.id, second.revision)

    async def test_legacy_single_site_task_is_backfilled(self) -> None:
        site = await self._site("a")
        profile = await self._profile(site.id)
        task = await self._task(profile.id, sites=[site.id])
        await self._close()
        # 降级到 v13：删掉候选站点表，模拟迁移前的库。
        raw = sqlite3.connect(self.path)
        try:
            raw.execute("DROP TABLE push_task_sites")
            raw.execute("PRAGMA user_version = 13")
            raw.commit()
        finally:
            raw.close()
        await self._open()
        reloaded = await self.tasks.get_task(task.id)
        self.assertEqual(reloaded.sites, (site.id,))
        raw = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                raw.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
        finally:
            raw.close()


if __name__ == "__main__":
    unittest.main()
