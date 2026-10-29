from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HAS_HOST = False
try:
    from astrbot.core.message.components import Plain
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata
    from astrbot.core.star.star_handler import star_handlers_registry
    HAS_HOST = True
except Exception:
    HAS_HOST = False


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


@unittest.skipUnless(HAS_HOST, "real AstrBot source is not on PYTHONPATH")
class ChatCommandHostTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("ASTRBOT_HOST_ROOT", ""))
        if not self.root.is_dir():
            self.skipTest("ASTRBOT_HOST_ROOT is not set")
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        self.module = importlib.import_module(
            "data.plugins.astrbot_plugin_60sapi_hub.main",
        )

    def handler_metadata(self):
        handlers = [
            item for item in star_handlers_registry.get_handlers_by_event_type(
                __import__(
                    "astrbot.core.star.star_handler", fromlist=["EventType"],
                ).EventType.AdapterMessageEvent,
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

    async def dispatch(self, handler, event):
        config = {"plugin_set": ["*"]}
        for event_filter in handler.event_filters:
            self.assertTrue(event_filter.filter(event, config))
        params = event.get_extra("parsed_params", {})
        fake = SimpleNamespace(_active=True, chat_commands=FakeChat())
        results = [
            result async for result in handler.handler(fake, event, **params)
        ]
        return fake, results

    async def test_registration_filter_dispatch_and_response(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s help", "bot1:GroupMessage:g1")
        fake, results = await self.dispatch(handler, event)
        self.assertEqual(fake.chat_commands.calls, [
            ("bot1:GroupMessage:g1", "60s help"),
        ])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get_plain_text(), "列表回复")
        # Non-command messages and unrelated commands are not handled.
        for text in ("hello", "60sx list"):
            event = self.event(text)
            filters = [f.filter(event, {"plugin_set": ["*"]}) for f in handler.event_filters]
            self.assertFalse(any(filters))

    async def test_inactive_instance_and_reload_do_not_duplicate(self) -> None:
        handler = self.handler_metadata()
        event = self.event("60s list")
        for event_filter in handler.event_filters:
            event_filter.filter(event, {"plugin_set": ["*"]})
        fake = SimpleNamespace(_active=False, chat_commands=FakeChat())
        results = [
            result async for result in handler.handler(fake, event)
        ]
        self.assertEqual(results, [])
        self.assertEqual(fake.chat_commands.calls, [])
        before = len([
            item for item in star_handlers_registry.get_handlers_by_event_type(
                __import__("astrbot.core.star.star_handler", fromlist=["EventType"]).EventType.AdapterMessageEvent,
                only_activated=False,
            )
            if item.handler_name == "sixty_seconds_command"
        ])
        importlib.reload(self.module)
        after = len([
            item for item in star_handlers_registry.get_handlers_by_event_type(
                __import__("astrbot.core.star.star_handler", fromlist=["EventType"]).EventType.AdapterMessageEvent,
                only_activated=False,
            )
            if item.handler_name == "sixty_seconds_command"
        ])
        self.assertEqual(before, 1)
        self.assertEqual(after, 1)
