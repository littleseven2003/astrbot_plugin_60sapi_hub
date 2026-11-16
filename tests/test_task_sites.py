from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

from core.profiles import ContentProfileService, ProfileCreate
from core.site_store import SCHEMA_VERSION
from core.sites import SiteCreate, SiteReferencedProfilesError, SiteService
from core.tasks import (
    PushTaskService,
    TaskCreate,
    TaskNotFoundError,
    TaskUpdate,
    TaskValidationError,
)

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

    async def _profile(self):
        return await self.profiles.create_profile(
            ProfileCreate(name="组合", items=ITEMS),
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
        profile = await self._profile()
        task = await self._task(profile.id, sites=[third.id, first.id, second.id])
        self.assertEqual(task.sites, (third.id, first.id, second.id))
        loaded = await self.tasks.get_task(task.id)
        self.assertEqual(loaded.sites, (third.id, first.id, second.id))

    async def test_update_reorders_and_persists_after_reopen(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        profile = await self._profile()
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

    async def test_task_requires_explicit_candidates(self) -> None:
        """组合不再绑定站点，任务创建必须显式给出候选站点。"""
        await self._site("a", kind="self_built")
        profile = await self._profile()
        with self.assertRaises(TaskValidationError) as raised:
            await self._task(profile.id)
        self.assertEqual(raised.exception.field, "sites")

    async def test_update_without_sites_keeps_order(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        profile = await self._profile()
        task = await self._task(profile.id, sites=[second.id, first.id])
        updated = await self.tasks.update_task(
            task.id,
            TaskUpdate(
                name="改名",
                profile_id=profile.id,
                output_format="text",
                umos=list(task.umos),
                expected_revision=task.revision,
            ),
        )
        self.assertEqual(updated.sites, (second.id, first.id))
        reloaded = await self.tasks.get_task(task.id)
        self.assertEqual(reloaded.sites, (second.id, first.id))

    async def test_update_explicit_sites_replaces_order(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        profile = await self._profile()
        task = await self._task(profile.id, sites=[first.id])
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

    async def test_invalid_candidates_are_rejected(self) -> None:
        site = await self._site("a")
        profile = await self._profile()
        with self.assertRaises(TaskValidationError):
            await self._task(profile.id, sites=[])
        with self.assertRaises(TaskValidationError):
            await self._task(profile.id, sites=[""])
        with self.assertRaises(TaskNotFoundError):
            await self._task(profile.id, sites=["missing-site"])

    async def test_site_delete_blocked_by_task_reference(self) -> None:
        first = await self._site("a")
        second = await self._site("b")
        profile = await self._profile()
        await self._task(profile.id, sites=[first.id, second.id])
        with self.assertRaises(SiteReferencedProfilesError):
            await self.sites.delete_site(second.id, second.revision)

    async def test_legacy_single_site_task_is_backfilled(self) -> None:
        site = await self._site("a")
        profile = await self._profile()
        task = await self._task(profile.id, sites=[site.id])
        await self._close()
        raw = sqlite3.connect(self.path)
        try:
            # 还原 v14 之前的旧库形状：组合自带 site_id，任务没有候选表。
            raw.execute("ALTER TABLE content_profiles ADD COLUMN site_id TEXT")
            raw.execute(
                "UPDATE content_profiles SET site_id = ? WHERE id = ?",
                (site.id, profile.id),
            )
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


class TaskWebRouteTest(unittest.TestCase):
    """候选站点测试与手动投递必须使用不同路径。"""

    def _collect(self) -> list[str]:
        root = Path(__file__).resolve().parent.parent
        package_name = "hubroutepkg"

        @contextmanager
        def fake_astrbot():
            names = ("astrbot", "astrbot.api", "astrbot.api.star", "astrbot.api.web")
            saved = {name: sys.modules.get(name) for name in names}
            astrbot = types.ModuleType("astrbot")
            api = types.ModuleType("astrbot.api")
            star = types.ModuleType("astrbot.api.star")
            web = types.ModuleType("astrbot.api.web")
            star.Context = type("Context", (), {})
            web.request = types.SimpleNamespace()
            web.error_response = lambda *a, **k: None
            web.json_response = lambda *a, **k: None
            api.star = star
            api.web = web
            astrbot.api = api
            sys.modules.update({
                "astrbot": astrbot,
                "astrbot.api": api,
                "astrbot.api.star": star,
                "astrbot.api.web": web,
            })
            try:
                yield
            finally:
                for name, module in saved.items():
                    if module is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module

        def load(name: str):
            spec = importlib.util.spec_from_file_location(
                f"{package_name}.{name}", root / f"{name}.py",
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module

        captured: list[str] = []

        class _Ctx:
            def register_web_api(self, path, handler, methods, desc):
                captured.append(path)

        package = types.ModuleType(package_name)
        package.__path__ = [str(root)]
        sys.modules[package_name] = package
        try:
            with fake_astrbot():
                task_web = load("task_web")
                delivery_web = load("delivery_web")
                task_web.TaskWeb(
                    None, plugin_name="p", is_active=lambda: True,
                ).register(_Ctx())
                delivery_web.DeliveryWeb(
                    None, plugin_name="p", is_active=lambda: True,
                ).register(_Ctx())
        finally:
            for name in list(sys.modules):
                if name == package_name or name.startswith(f"{package_name}."):
                    sys.modules.pop(name, None)
        return captured

    def test_capability_test_does_not_collide_with_manual_delivery(self) -> None:
        routes = self._collect()
        self.assertIn("/p/tasks/<task_id>/capability-test", routes)
        self.assertIn("/p/tasks/<task_id>/test", routes)
        self.assertIn("/p/tasks/<task_id>/test-preview", routes)
        # 同一路径允许注册不同方法（如 /tasks 的 GET 与 POST）；
        # 关键是两条测试路由各被注册一次，且互不覆盖。
        self.assertEqual(routes.count("/p/tasks/<task_id>/capability-test"), 1)
        self.assertEqual(routes.count("/p/tasks/<task_id>/test"), 1)
        self.assertEqual(
            len([r for r in routes if r != "/p/tasks"]),
            len({r for r in routes if r != "/p/tasks"}),
            routes,
        )


if __name__ == "__main__":
    unittest.main()
