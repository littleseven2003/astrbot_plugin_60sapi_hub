"""真实 AstrBot v4.29.1 命令过滤器/处理器回归。

这里验证宿主命令注册、CommandFilter、参数传递、处理器输出和停用后的
在途抑制；它不替代 PluginManager 的完整加载/停用/重载验收。

准备隔离宿主：
1. 准备 AstrBot v4.29.1 源码和 Python 3.12 依赖环境。
2. 将本插件复制到 <root>/data/plugins/astrbot_plugin_60sapi_hub。
3. 设置 ASTRBOT_HOST_ROOT=<root>（缺失时跳过；设置但目录无效时失败）。
   ASTRBOT_ROOT 仍是启动宿主的运行数据根目录；本测试优先读取
   ASTRBOT_HOST_ROOT，未设置时回退到 ASTRBOT_ROOT。
4. 运行：
   PYTHONPATH=<astrbot-src>:<venv-site-packages>:<root> \
   ASTRBOT_HOST_ROOT=<root> python -m unittest tests.test_chat_commands_host -v
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import os
import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

from core.chat_commands import ChatCommandService
from core.chat_content import ChatContentQuery

# Mirror test_astrbot_sender.py: only a genuinely missing host is skipped.
# If the host exists, import errors are real failures, not skips.
HAS_HOST = find_spec("astrbot") is not None
if HAS_HOST:
    from astrbot.core.message.components import Image, Plain  # noqa: F401
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.star.star_handler import star_handlers_registry


if HAS_HOST:
    class ConcreteEvent(AstrMessageEvent):
        async def send(self, message):
            await super().send(message)
else:
    class ConcreteEvent:  # pragma: no cover - import compatibility only
        pass


class FakeChat:
    def __init__(self, text="列表回复"):
        self.text = text
        self.calls = []

    async def handle(self, umo, message):
        self.calls.append((umo, message))
        return self.text


class ControlledChat:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    async def handle(self, umo, message):
        self.calls.append((umo, message))
        self.started.set()
        await self.release.wait()
        return "延迟回复"


class ListTasks:
    async def list_tasks(self):
        return [
            SimpleNamespace(
                id="visible", name="可见任务", profile_id="p1",
                output_format="text", umos=("bot1:GroupMessage:g1",),
                created_at="2026-01-01T00:00:00Z",
            ),
            SimpleNamespace(
                id="other", name="其他会话任务", profile_id="p1",
                output_format="image", umos=("bot1:GroupMessage:other",),
                created_at="2026-01-01T00:00:01Z",
            ),
        ]


class ListProfiles:
    async def list_profiles(self):
        return [SimpleNamespace(id="p1", site_id="s1")]


class ListSites:
    async def list_sites(self):
        return [SimpleNamespace(id="s1", enabled=True)]


def host_root() -> Path | None:
    raw = os.environ.get("ASTRBOT_HOST_ROOT") or os.environ.get("ASTRBOT_ROOT")
    if not raw:
        return None
    return Path(raw)


@unittest.skipUnless(HAS_HOST, "real AstrBot source is not on PYTHONPATH")
class ChatCommandHostTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = host_root()
        if root is None:
            self.skipTest("ASTRBOT_HOST_ROOT/ASTRBOT_ROOT is not set")
        if not root.is_dir():
            self.fail(f"host root is not a directory: {root}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        self.module = importlib.import_module(
            "data.plugins.astrbot_plugin_60sapi_hub.main",
        )

    def handler_metadata(self):
        from astrbot.core.star.star_handler import EventType
        handlers = [
            item for item in star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent,
                only_activated=False,
            )
            if item.handler_name == "sixty_seconds_command"
            and item.handler_module_path == self.module.__name__
        ]
        self.assertEqual(len(handlers), 1)
        return handlers[0]

    def event(self, message="60s list", umo="bot1:GroupMessage:g1"):
        platform, message_type, session_id = umo.split(":", 2)
        message_obj = AstrBotMessage()
        message_obj.type = MessageType(message_type)
        message_obj.session_id = session_id
        message_obj.message_str = message
        event = ConcreteEvent(
            message_str=message,
            message_obj=message_obj,
            platform_meta=PlatformMetadata(
                name="test", description="test", id=platform,
            ),
            session_id=session_id,
        )
        event.is_at_or_wake_command = True
        return event

    def filtered_event(self, handler, event):
        config = {"plugin_set": ["*"]}
        for event_filter in handler.event_filters:
            self.assertTrue(event_filter.filter(event, config))
        return event.get_extra("parsed_params", {})

    async def dispatch(self, handler, event, chat):
        params = self.filtered_event(handler, event)
        fake = SimpleNamespace(
            _active=True, _lifecycle_generation=1, chat_commands=chat,
        )
        results = [
            result async for result in handler.handler(fake, event, **params)
        ]
        return fake, results

    async def test_registration_filter_dispatch_to_real_service(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s list", "bot1:GroupMessage:g1")
        service = ChatCommandService(ListTasks(), ListProfiles(), ListSites())
        _, results = await self.dispatch(handler, event, service)
        self.assertEqual(len(results), 1)
        text = results[0].get_plain_text()
        self.assertIn("可见任务", text)
        self.assertIn("ID：visible", text)
        self.assertIn("输出：纯文字", text)
        self.assertNotIn("其他会话任务", text)
        self.assertNotIn("bot1:GroupMessage:g1", text)
        # Non-command messages and unrelated commands are not handled.
        for message in ("hello", "60sx list"):
            event = self.event(message)
            filters = [
                f.filter(event, {"plugin_set": ["*"]})
                for f in handler.event_filters
            ]
            self.assertFalse(any(filters))

    async def test_help_dispatch_and_reload_registry(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s help")
        chat = FakeChat("帮助回复")
        fake, results = await self.dispatch(handler, event, chat)
        self.assertEqual(fake.chat_commands.calls, [
            ("bot1:GroupMessage:g1", "60s help"),
        ])
        self.assertEqual(results[0].get_plain_text(), "帮助回复")

        # Module reload is registry hygiene only; it is not a full
        # PluginManager unload/load integration test.
        before = len([
            item for item in star_handlers_registry.get_handlers_by_event_type(
                __import__(
                    "astrbot.core.star.star_handler", fromlist=["EventType"],
                ).EventType.AdapterMessageEvent,
                only_activated=False,
            )
            if item.handler_name == "sixty_seconds_command"
        ])
        importlib.reload(self.module)
        after = len([
            item for item in star_handlers_registry.get_handlers_by_event_type(
                __import__(
                    "astrbot.core.star.star_handler", fromlist=["EventType"],
                ).EventType.AdapterMessageEvent,
                only_activated=False,
            )
            if item.handler_name == "sixty_seconds_command"
        ])
        self.assertEqual(before, 1)
        self.assertEqual(after, 1)

    async def test_inflight_disable_produces_zero_reply(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s list")
        chat = ControlledChat()
        params = self.filtered_event(handler, event)
        fake = SimpleNamespace(
            _active=True, _lifecycle_generation=3, chat_commands=chat,
        )
        stream = handler.handler(fake, event, **params)
        pending = asyncio.create_task(stream.__anext__())
        await chat.started.wait()
        fake._active = False
        chat.release.set()
        with self.assertRaises(StopAsyncIteration):
            await pending

    async def test_inflight_reinitialize_stale_generation_zero_reply(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s list")
        chat = ControlledChat()
        params = self.filtered_event(handler, event)
        fake = SimpleNamespace(
            _active=True, _lifecycle_generation=3, chat_commands=chat,
        )
        stream = handler.handler(fake, event, **params)
        pending = asyncio.create_task(stream.__anext__())
        await chat.started.wait()
        # Same instance re-initialized: active again, but generation advanced.
        fake._lifecycle_generation = 4
        chat.release.set()
        with self.assertRaises(StopAsyncIteration):
            await pending

    async def test_normal_inflight_reply_still_works(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s list")
        chat = ControlledChat()
        params = self.filtered_event(handler, event)
        fake = SimpleNamespace(
            _active=True, _lifecycle_generation=3, chat_commands=chat,
        )
        stream = handler.handler(fake, event, **params)
        pending = asyncio.create_task(stream.__anext__())
        await chat.started.wait()
        chat.release.set()
        result = await pending
        self.assertEqual(result.get_plain_text(), "延迟回复")

    async def test_get_command_through_actual_query_service(self) -> None:
        class GetFixture:
            def __init__(self):
                self.task = SimpleNamespace(
                    id="visible", name="可见任务", profile_id="p1",
                    output_format="image", umos=("bot1:GroupMessage:g1",),
                    revision=2, created_at="2026-01-01T00:00:00Z",
                )
                self.profile = SimpleNamespace(id="p1", site_id="s1", revision=3)
                self.site = SimpleNamespace(id="s1", site_revision=4, enabled=True, revision=4)

            async def get_task(self, task_id):
                from core.tasks import TaskNotFoundError
                if task_id != self.task.id:
                    raise TaskNotFoundError(task_id)
                return self.task

            async def get_config_snapshot(self, task_id):
                task = await self.get_task(task_id)
                return SimpleNamespace(
                    task=task, profile_id=self.profile.id,
                    profile_revision=self.profile.revision,
                    site_id=self.site.id, site_revision=self.site.revision,
                    site_enabled=self.site.enabled,
                )

            async def get_profile(self, profile_id):
                return self.profile

            async def get_site(self, site_id):
                return self.site

            async def preview_profile(self, payload):
                return SimpleNamespace(status_code=200, body={
                    "data": {"outputs": {"text": "组合：可见任务\n纯文字内容"}},
                })

        fixture = GetFixture()
        service = ChatCommandService(
            fixture, fixture, fixture,
            content_query=ChatContentQuery(fixture, fixture),
        )
        handler = self.handler_metadata()
        event = self.event("60s get visible", "bot1:GroupMessage:g1")
        params = self.filtered_event(handler, event)
        fake = SimpleNamespace(
            _active=True, _lifecycle_generation=1, chat_commands=service,
        )
        results = [
            result async for result in handler.handler(fake, event, **params)
        ]
        self.assertEqual(len(results), 1)
        self.assertIn("纯文字内容", results[0].get_plain_text())

    async def test_image_command_builds_one_ordered_chain(self) -> None:
        image_module = importlib.import_module(
            "data.plugins.astrbot_plugin_60sapi_hub.core.chat_image",
        )

        class Fixture:
            async def get_config_snapshot(self, task_id):
                task = SimpleNamespace(
                    id="visible", revision=2,
                    umos=("bot1:GroupMessage:g1",),
                    profile_id="p1",
                )
                return SimpleNamespace(
                    task=task, profile_id="p1", profile_revision=1,
                    site_id="s1", site_revision=1, site_enabled=True,
                )

            async def preview_profile(self, payload, *, owner=None):
                return SimpleNamespace(
                    status_code=200,
                    body={"data": {"image_snapshot": {"id": "chat-snapshot"}}},
                )

            async def generate_image(self, profile_id, payload, *, owner=None):
                png = b"\x89PNG\r\n\x1a\npage"
                encoded = base64.b64encode(png).decode()
                return SimpleNamespace(status_code=200, body={"data": {"pages": [
                    {"index": 2, "png_base64": encoded},
                    {"index": 1, "png_base64": encoded},
                ]}})

        fixture = Fixture()
        image_query = image_module.ChatImageQuery(fixture, fixture)
        service = ChatCommandService(
            SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
            image_query=image_query,
        )
        handler = self.handler_metadata()
        event = self.event("60s image visible", "bot1:GroupMessage:g1")
        params = self.filtered_event(handler, event)
        fake = SimpleNamespace(
            _active=True, _lifecycle_generation=1, chat_commands=service,
        )
        results = [
            result async for result in handler.handler(fake, event, **params)
        ]
        self.assertEqual(len(results), 1)
        chain = results[0].chain
        self.assertEqual([type(item) for item in chain], [Plain, Image, Image])
        self.assertEqual(chain[0].text, "共 2 页图片。\n可发送 /60s get visible 获取文字版。")
        self.assertTrue(chain[1].file.startswith("base64://"))
        self.assertTrue(chain[2].file.startswith("base64://"))

    async def test_actual_initialize_terminate_reinitialize_stale_query(self) -> None:
        class AsyncResource:
            def __init__(self):
                self.opened = 0
                self.closed = 0

            async def open(self):
                self.opened += 1

            async def close(self):
                self.closed += 1

        class Slot:
            def close(self):
                pass

        chat = ControlledChat()
        instance = object.__new__(self.module.SixtySAPIHub)
        instance._active = False
        instance._lifecycle_generation = 0
        instance.chat_commands = chat
        instance.sites = AsyncResource()
        instance.content = AsyncResource()
        instance.delivery = AsyncResource()
        instance.image_service = AsyncResource()
        instance.image_snapshots = Slot()

        await self.module.SixtySAPIHub.initialize(instance)
        self.assertTrue(instance._active)
        self.assertEqual(instance._lifecycle_generation, 1)

        event = SimpleNamespace(
            unified_msg_origin="bot1:GroupMessage:g1",
            message_str="60s list",
            plain_result=lambda text: SimpleNamespace(text=text),
        )
        stream = self.module.SixtySAPIHub.sixty_seconds_command(instance, event)
        old_pending = asyncio.create_task(stream.__anext__())
        await chat.started.wait()

        await self.module.SixtySAPIHub.terminate(instance)
        self.assertFalse(instance._active)
        self.assertEqual(instance._lifecycle_generation, 2)
        await self.module.SixtySAPIHub.initialize(instance)
        self.assertTrue(instance._active)
        self.assertEqual(instance._lifecycle_generation, 3)

        chat.release.set()
        with self.assertRaises(StopAsyncIteration):
            await old_pending

        new_event = SimpleNamespace(
            unified_msg_origin="bot1:GroupMessage:g1",
            message_str="60s list",
            plain_result=lambda text: SimpleNamespace(text=text),
        )
        new_stream = self.module.SixtySAPIHub.sixty_seconds_command(
            instance, new_event,
        )
        new_result = await new_stream.__anext__()
        self.assertEqual(new_result.text, "延迟回复")
