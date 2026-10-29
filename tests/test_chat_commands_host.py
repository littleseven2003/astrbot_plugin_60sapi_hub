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
import importlib
import os
import sys
import unittest
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace

from core.chat_commands import ChatCommandService

# Mirror test_astrbot_sender.py: only a genuinely missing host is skipped.
# If the host exists, import errors are real failures, not skips.
HAS_HOST = find_spec("astrbot") is not None
if HAS_HOST:
    from astrbot.core.message.components import Plain  # noqa: F401
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
