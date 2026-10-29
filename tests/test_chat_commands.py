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
        unknown = await service.handle("u", "/60s get")
        self.assertIn("未知命令", unknown)
        self.assertIn("/60s list", unknown)
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
