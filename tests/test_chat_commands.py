from __future__ import annotations

import unittest
from types import SimpleNamespace

from core.chat_commands import (
    EMPTY_TEXT,
    HELP_TEXT,
    PAGE_NOTICE,
    PAGE_OUT_OF_RANGE,
    ChatCommandService,
    parse_command,
)


def task(task_id, umos, profile_id="profile-1", output="text", created=0):
    return SimpleNamespace(
        id=task_id, name=f"任务-{task_id}", profile_id=profile_id,
        output_format=output, umos=tuple(umos), created_at=f"2026-01-01T00:00:{created:02d}Z",
    )


class FakeTasks:
    def __init__(self, tasks):
        self.items = list(tasks)
        self.list_calls = 0

    async def list_tasks(self):
        self.list_calls += 1
        return list(self.items)


class FakeProfiles:
    def __init__(self, profiles):
        self.items = list(profiles)
        self.list_calls = 0

    async def list_profiles(self):
        self.list_calls += 1
        return list(self.items)


class FakeSites:
    def __init__(self, sites):
        self.items = list(sites)
        self.list_calls = 0

    async def list_sites(self):
        self.list_calls += 1
        return list(self.items)


def profile(profile_id="profile-1", site_id="site-1"):
    return SimpleNamespace(id=profile_id, site_id=site_id)


def site(site_id="site-1", enabled=True):
    return SimpleNamespace(id=site_id, enabled=enabled)


class ParseCommandTest(unittest.TestCase):
    def test_help_and_list_forms(self) -> None:
        self.assertEqual(parse_command("/60s"), ("help", []))
        self.assertEqual(parse_command("60s help"), ("help", []))
        self.assertEqual(parse_command("/60s list"), ("list", []))
        self.assertEqual(parse_command("/60s list 2"), ("list", ["2"]))
        self.assertEqual(parse_command("60s list 2 extra"), ("list", ["2", "extra"]))

    def test_non_command_is_ignored(self) -> None:
        self.assertIsNone(parse_command("hello"))
        self.assertIsNone(parse_command("/60sx list"))
        self.assertIsNone(parse_command(""))


class ChatCommandServiceTest(unittest.IsolatedAsyncioTestCase):
    def build(self, tasks=None, profiles=None, sites=None):
        self.tasks = FakeTasks(tasks or [])
        self.profiles = FakeProfiles(profiles or [profile()])
        self.sites = FakeSites(sites or [site()])
        return ChatCommandService(self.tasks, self.profiles, self.sites)

    async def test_exact_umo_across_group_private_platform_and_isolation(self) -> None:
        source = "bot1:GroupMessage:group:1"
        service = self.build(tasks=[
            task("exact", [source]),
            task("member", ["bot1:GroupMessage:group:1_member"]),
            task("prefix", ["bot1:GroupMessage:group:10"]),
            task("platform", ["bot2:GroupMessage:group:1"]),
            task("private", ["bot1:FriendMessage:user1"]),
            task("bare", ["12345"]),
        ])
        text = await service.handle(source, "/60s list")
        self.assertIn("exact", text)
        for hidden in ("member", "prefix", "platform", "private", "bare"):
            self.assertNotIn(f"任务-{hidden}", text)
            self.assertNotIn(f"ID：{hidden}", text)

    async def test_empty_unauthorized_disabled_and_removed_targets_do_not_leak(
        self,
    ) -> None:
        service = self.build(
            tasks=[
                task("empty", []),
                task("removed", ["bot1:FriendMessage:other"]),
                task("disabled", ["bot1:FriendMessage:me"], profile_id="p-disabled"),
                task("missing-profile", ["bot1:FriendMessage:me"], profile_id="missing"),
            ],
            profiles=[profile(), profile("p-disabled", "site-disabled")],
            sites=[site(), site("site-disabled", enabled=False)],
        )
        text = await service.handle("bot1:FriendMessage:me", "/60s list")
        self.assertEqual(text, EMPTY_TEXT)
        for name in ("empty", "removed", "disabled", "missing-profile"):
            self.assertNotIn(f"任务-{name}", text)
        self.assertEqual(
            (self.tasks.list_calls, self.profiles.list_calls, self.sites.list_calls),
            (1, 1, 1),
        )

    async def test_visible_tasks_include_disabled_task_but_require_enabled_site(
        self,
    ) -> None:
        service = self.build(tasks=[
            task("visible", ["bot1:GroupMessage:g"], output="image"),
        ])
        text = await service.handle("bot1:GroupMessage:g", "/60s list")
        self.assertIn("ID：visible", text)
        self.assertIn("输出：图片", text)
        self.assertNotIn("bot1:GroupMessage:g", text)
        self.assertNotIn("site-1", text)

    async def test_pagination_after_filtering_and_stable_order(self) -> None:
        items = [
            task(f"task-{i:02d}", ["bot1:GroupMessage:g"], created=i)
            for i in range(21)
        ]
        # Unauthorized items must not consume page slots.
        items.insert(0, task("hidden", ["bot1:GroupMessage:other"], created=-1))
        service = self.build(tasks=items)
        first = await service.handle("bot1:GroupMessage:g", "/60s list")
        self.assertIn("task-00", first)
        self.assertIn("task-09", first)
        self.assertNotIn("task-10", first)
        self.assertNotIn("hidden", first)
        self.assertIn("第 1/3 页，共 21 项", first)
        second = await service.handle("bot1:GroupMessage:g", "/60s list 2")
        self.assertIn("task-10", second)
        self.assertNotIn("task-00", second)
        third = await service.handle("bot1:GroupMessage:g", "/60s list 3")
        self.assertIn("task-20", third)
        self.assertIn("第 3/3 页", third)

    async def test_page_errors(self) -> None:
        service = self.build(tasks=[task("only", ["bot1:GroupMessage:g"])])
        self.assertEqual(
            await service.handle("bot1:GroupMessage:g", "/60s list 0"),
            PAGE_NOTICE,
        )
        self.assertEqual(
            await service.handle("bot1:GroupMessage:g", "/60s list abc"),
            PAGE_NOTICE,
        )
        self.assertEqual(
            await service.handle("bot1:GroupMessage:g", "/60s list -1"),
            PAGE_NOTICE,
        )
        out = await service.handle("bot1:GroupMessage:g", "/60s list 2")
        self.assertTrue(out.startswith(PAGE_OUT_OF_RANGE))
        self.assertIn("共 1 页", out)

    async def test_help_unknown_and_empty(self) -> None:
        service = self.build()
        self.assertEqual(await service.handle("u", "/60s"), HELP_TEXT)
        self.assertEqual(await service.handle("u", "/60s help"), HELP_TEXT)
        unknown = await service.handle("u", "/60s foo")
        self.assertIn("未知命令", unknown)
        self.assertIn("/60s list", unknown)
        self.assertEqual(
            await service.handle("u", "/60s get"),
            "用法：/60s get <任务ID>。",
        )
        self.assertEqual(await service.handle("u", "/60s list"), EMPTY_TEXT)
        self.assertEqual(await service.handle("u", "/60s help extra"), HELP_TEXT)

    async def test_list_does_not_call_content_delivery_or_write(self) -> None:
        service = self.build(tasks=[task("t", ["u"])])
        await service.handle("u", "/60s list")
        # The service has no delivery/content/write dependency at all; the
        # three read-only calls below are the complete observable work.
        self.assertEqual(self.tasks.list_calls, 1)
        self.assertEqual(self.profiles.list_calls, 1)
        self.assertEqual(self.sites.list_calls, 1)
        self.assertFalse(hasattr(self.tasks, "create_task"))
        self.assertFalse(hasattr(self.tasks, "execute"))


class ChatCommandSqliteTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import tempfile
        from pathlib import Path
        from core.profiles import ContentProfileService, ProfileCreate
        from core.sites import SiteCreate, SiteService
        from core.tasks import PushTaskService, TaskCreate

        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "chat.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.site = await self.sites.create_site(SiteCreate(
            name="聊天站点", base_url="http://example.test", enabled=True,
            timeout_seconds=5,
        ))
        self.disabled_site = await self.sites.create_site(SiteCreate(
            name="停用站点", base_url="http://disabled.test", enabled=False,
            timeout_seconds=5,
        ))
        self.profile = await self.profiles.create_profile(ProfileCreate(
            name="聊天组合", site_id=self.site.id,
            items=[{"content_type": "daily_news", "params": {}, "limit": None}],
        ))
        self.disabled_profile = await self.profiles.create_profile(ProfileCreate(
            name="停用组合", site_id=self.disabled_site.id,
            items=[{"content_type": "daily_news", "params": {}, "limit": None}],
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="可见任务", profile_id=self.profile.id,
            output_format="markdown", umos=["bot:GroupMessage:g"],
        ))
        await self.tasks.create_task(TaskCreate(
            name="停用站点任务", profile_id=self.disabled_profile.id,
            output_format="text", umos=["bot:GroupMessage:g"],
        ))
        self.service = ChatCommandService(self.tasks, self.profiles, self.sites)

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self.tmp.cleanup()

    async def test_sqlite_visibility_and_read_only(self) -> None:
        before = await self.tasks.list_tasks()
        text = await self.service.handle("bot:GroupMessage:g", "/60s list")
        self.assertIn(self.task.id, text)
        self.assertIn("可见任务", text)
        self.assertIn("输出：Markdown", text)
        self.assertNotIn("停用站点任务", text)
        self.assertEqual(len(await self.tasks.list_tasks()), len(before))


class ContentFixture:
    def __init__(self):
        self.task = SimpleNamespace(
            id="task-get", name="查询任务", profile_id="p1",
            output_format="image", umos=("bot:GroupMessage:g",),
            revision=2, created_at="2026-01-01T00:00:00Z",
        )
        self.profile = SimpleNamespace(
            id="p1", site_id="s1", revision=3,
        )
        self.site = SimpleNamespace(id="s1", enabled=True, revision=4)
        self.task_reads = 0
        self.profile_reads = 0
        self.site_reads = 0
        self.preview_calls = []

    async def get_task(self, task_id):
        from core.tasks import TaskNotFoundError
        self.task_reads += 1
        if task_id != self.task.id:
            raise TaskNotFoundError(task_id)
        return self.task

    async def get_profile(self, profile_id):
        return self.profile

    async def get_site(self, site_id):
        return self.site

    async def preview_profile(self, payload):
        self.preview_calls.append(payload)
        return SimpleNamespace(
            status_code=200,
            body={"data": {"outputs": {"text": "组合：查询任务\n内容"}}},
        )


class ChatContentQueryTest(unittest.IsolatedAsyncioTestCase):
    def build(self):
        from core.chat_commands import ChatCommandService
        from core.chat_content import ChatContentQuery
        self.f = ContentFixture()
        query = ChatContentQuery(self.f, self.f, self.f, self.f)
        service = ChatCommandService(
            self.f, self.f, self.f, content_query=query,
        )
        return service

    async def test_exact_authorization_and_same_denial(self) -> None:
        service = self.build()
        ok = await service.handle("bot:GroupMessage:g", "/60s get task-get")
        self.assertIn("内容", ok)
        self.assertEqual(self.f.preview_calls, [{
            "profile_id": "p1", "force_refresh": False,
        }])
        missing = await service.handle("bot:GroupMessage:g", "/60s get nope")
        denied = await service.handle("bot:GroupMessage:other", "/60s get task-get")
        self.assertEqual(missing, denied)
        self.assertEqual(missing, "任务不存在或当前会话无权查询。")
        self.assertEqual(len(self.f.preview_calls), 1)

    async def test_usage_site_disabled_and_all_failed_no_upstream(self) -> None:
        service = self.build()
        self.assertEqual(
            await service.handle("bot:GroupMessage:g", "/60s get"),
            "用法：/60s get <任务ID>。",
        )
        self.f.site.enabled = False
        self.assertEqual(
            await service.handle("bot:GroupMessage:g", "/60s get task-get"),
            "任务配置不可用，请检查组合或站点状态。",
        )
        self.assertEqual(self.f.preview_calls, [])

        self.f.site.enabled = True
        async def failed(payload):
            self.f.preview_calls.append(payload)
            return SimpleNamespace(status_code=502, body={"data": {"status": "failed"}})
        self.f.preview_profile = failed
        self.assertEqual(
            await service.handle("bot:GroupMessage:g", "/60s get task-get"),
            "内容暂时无法获取，请稍后重试。",
        )

    async def test_config_change_during_query_suppresses_old_content(self) -> None:
        service = self.build()
        original = self.f.preview_profile
        async def changed(payload):
            result = await original(payload)
            self.f.task.revision += 1
            return result
        self.f.preview_profile = changed
        self.assertEqual(
            await service.handle("bot:GroupMessage:g", "/60s get task-get"),
            "任务或配置已变化，请重新查询。",
        )

    async def test_target_removed_during_query(self) -> None:
        service = self.build()
        original = self.f.preview_profile
        async def removed(payload):
            result = await original(payload)
            self.f.task.umos = ("bot:GroupMessage:other",)
            return result
        self.f.preview_profile = removed
        self.assertEqual(
            await service.handle("bot:GroupMessage:g", "/60s get task-get"),
            "任务或配置已变化，请重新查询。",
        )

    async def test_partial_empty_and_truncation(self) -> None:
        service = self.build()
        async def partial(payload):
            return SimpleNamespace(status_code=200, body={"data": {"outputs": {"text": "状态：部分成功\n空列表，0 条"}}})
        self.f.preview_profile = partial
        text = await service.handle("bot:GroupMessage:g", "/60s get task-get")
        self.assertIn("部分成功", text)
        self.assertIn("空列表", text)

        long_text = "长" * 2500
        async def long(payload):
            return SimpleNamespace(status_code=200, body={"data": {"outputs": {"text": long_text}}})
        self.f.preview_profile = long
        text = await service.handle("bot:GroupMessage:g", "/60s get task-get")
        self.assertLessEqual(len(text), 2000)
        self.assertIn("已截断", text)

    async def test_list_and_help_do_not_use_query(self) -> None:
        service = self.build()
        self.assertEqual(await service.handle("bot:GroupMessage:g", "/60s help"), HELP_TEXT)
        await service.handle("bot:GroupMessage:g", "/60s list")
        self.assertEqual(self.f.preview_calls, [])
        self.assertEqual(self.f.task_reads, 0)
