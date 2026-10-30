from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.astrbot_sender import AstrBotAiocqhttpSender
from core.delivery import DeliveryMessage, DeliverySendResult, SendStatus
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate
from core.delivery import DeliveryLimits, DeliveryService
from tests.test_delivery import StubApi, item

from io import BytesIO

from PIL import Image as PILImage

HAS_HOST = False
AiocqhttpAdapter = None  # type: ignore[assignment]
Context = None  # type: ignore[assignment]

# Only skip when the real astrbot package cannot be found at all. Once the
# package is present, any dependency/import failure is a real test failure.
from importlib.util import find_spec

if find_spec("astrbot") is not None:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
        AiocqhttpAdapter,
    )
    from astrbot.core.star.context import Context
    from astrbot.core.message.components import Image, Plain
    from astrbot.core.message.message_event_result import MessageChain
    from astrbot.core.platform.message_session import MessageSession
    HAS_HOST = True


_png_buffer = BytesIO()
PILImage.new("RGB", (1, 1), (255, 0, 0)).save(_png_buffer, format="PNG")
PNG_1X1 = _png_buffer.getvalue()


class FakeBot:
    def __init__(self, mode="ok") -> None:
        self.mode = mode
        self.calls: list[tuple[str, dict]] = []

    async def send_group_msg(self, **kwargs):
        self.calls.append(("group", kwargs))
        if self.mode == "raise":
            raise RuntimeError("fake network failure")
        if self.mode == "cancel":
            raise asyncio.CancelledError
        return {"status": "ok", "data": {"message_id": 1}}

    async def send_private_msg(self, **kwargs):
        self.calls.append(("private", kwargs))
        if self.mode == "raise":
            raise RuntimeError("fake network failure")
        if self.mode == "cancel":
            raise asyncio.CancelledError
        return {"status": "ok", "data": {"message_id": 1}}


@unittest.skipUnless(HAS_HOST, "real AstrBot source is not on PYTHONPATH")
class AstrBotAiocqhttpSenderTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._metric_patch = patch(
            "astrbot.core.platform.platform.Metric.upload",
            new=self._noop_metric,
        )
        self._metric_patch.start()
        self.queue = asyncio.Queue()
        self.bot1 = FakeBot()
        self.bot2 = FakeBot()
        self.adapter1 = AiocqhttpAdapter(
            {
                "id": "demo_qq_01", "ws_reverse_host": "127.0.0.1",
                "ws_reverse_port": 0, "ws_reverse_token": "",
            },
            {}, self.queue,
        )
        self.adapter2 = AiocqhttpAdapter(
            {
                "id": "demo_qq_02", "ws_reverse_host": "127.0.0.1",
                "ws_reverse_port": 0, "ws_reverse_token": "",
            },
            {}, self.queue,
        )
        self.adapter1.bot = self.bot1
        self.adapter2.bot = self.bot2
        self.unsupported = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="telegram_01", name="telegram"),
        )
        self.context = object.__new__(Context)
        self.context.platform_manager = SimpleNamespace(
            platform_insts=[self.adapter1, self.adapter2, self.unsupported],
        )
        self.context.get_config = lambda umo=None: {
            "provider_ltm_settings": {},
        }
        self.sender = AstrBotAiocqhttpSender(self.context)

    async def asyncTearDown(self) -> None:
        self._metric_patch.stop()

    @staticmethod
    async def _noop_metric(**kwargs) -> None:
        return None

    async def test_exact_instance_group_and_private_routes(self) -> None:
        group = DeliveryMessage(
            umo="demo_qq_01:GroupMessage:123",
            kind="text", page_index=1, page_count=1, text="hello",
        )
        private = DeliveryMessage(
            umo="demo_qq_02:FriendMessage:456",
            kind="text", page_index=1, page_count=1, text="hi",
        )
        self.assertEqual((await self.sender.send(group)).status, SendStatus.SUCCESS)
        self.assertEqual((await self.sender.send(private)).status, SendStatus.SUCCESS)
        self.assertEqual(len(self.bot1.calls), 1)
        self.assertEqual(self.bot1.calls[0][0], "group")
        self.assertEqual(self.bot1.calls[0][1]["group_id"], 123)
        self.assertEqual(self.bot1.calls[0][1]["message"][0], {
            "type": "text", "data": {"text": "hello"},
        })
        self.assertEqual(len(self.bot2.calls), 1)
        self.assertEqual(self.bot2.calls[0][0], "private")
        self.assertEqual(self.bot2.calls[0][1]["user_id"], 456)
        self.assertEqual(group.umo, "demo_qq_01:GroupMessage:123")

    async def test_member_isolated_group_and_markdown_source(self) -> None:
        message = DeliveryMessage(
            umo="demo_qq_01:GroupMessage:123456_789",
            kind="markdown", page_index=1, page_count=1,
            text="# title\n<script>alert(1)</script> [CQ:at,qq=1]",
        )
        result = await self.sender.send(message)
        self.assertEqual(result.status, SendStatus.SUCCESS)
        call = self.bot1.calls[0][1]
        self.assertEqual(call["group_id"], 789)
        self.assertEqual(call["message"][0]["type"], "text")
        self.assertEqual(
            call["message"][0]["data"]["text"],
            "# title\n<script>alert(1)</script> [CQ:at,qq=1]",
        )
        self.assertNotIn("at", [seg["type"] for seg in call["message"]])

    async def test_valid_and_invalid_png(self) -> None:
        image = DeliveryMessage(
            umo="demo_qq_01:GroupMessage:123",
            kind="image", page_index=1, page_count=1, image_png=PNG_1X1,
        )
        self.assertEqual((await self.sender.send(image)).status, SendStatus.SUCCESS)
        call = self.bot1.calls[0][1]
        self.assertEqual(call["message"][0]["type"], "image")
        self.assertTrue(
            call["message"][0]["data"]["file"].startswith("base64://"),
        )
        for bad in (b"", b"not-png", b"\x89PNG\r\n\x1a\n" + b"x" * 10):
            result = await self.sender.send(DeliveryMessage(
                umo="demo_qq_01:GroupMessage:123",
                kind="image", page_index=1, page_count=1, image_png=bad,
            ))
            self.assertEqual(result.status, SendStatus.FAILED)
        self.assertEqual(len(self.bot1.calls), 1)

    async def test_invalid_routes_never_call_bot(self) -> None:
        cases = [
            ("demo_qq_01:GroupMessage:abc", "sender_invalid_payload"),
            ("demo_qq_01:GroupMessage:bad_123", "sender_invalid_payload"),
            ("demo_qq_01:GroupMessage:_123", "sender_invalid_payload"),
            ("demo_qq_01:GroupMessage:123_", "sender_invalid_payload"),
            ("demo_qq_01:GroupMessage:sender_123_456", "sender_invalid_payload"),
            ("demo_qq_01:GroupMessage:123__456", "sender_invalid_payload"),
            ("demo_qq_01:GroupMessage:not:a:session_456", "sender_invalid_payload"),
            ("missing:GroupMessage:123", "sender_not_found"),
            ("telegram_01:GroupMessage:123", "sender_not_supported"),
            ("demo_qq_01:OtherMessage:123", "sender_invalid_payload"),
            ("demo_qq_01:FriendMessage:abc", "sender_invalid_payload"),
            ("demo_qq_01:FriendMessage:123_456", "sender_invalid_payload"),
            ("demo_qq_01:FriendMessage:123:456", "sender_invalid_payload"),
        ]
        for umo, code in cases:
            result = await self.sender.send(DeliveryMessage(
                umo=umo, kind="text", page_index=1, page_count=1, text="x",
            ))
            self.assertEqual(result.status, SendStatus.FAILED, umo)
            self.assertEqual(result.code, code, umo)
        self.assertEqual(self.bot1.calls, [])
        self.assertEqual(self.bot2.calls, [])

    async def test_one_chat_like_chain_with_two_images_through_fake_bot(self) -> None:
        session = MessageSession.from_str("demo_qq_01:GroupMessage:123")
        chain = MessageChain([
            Plain("共 2 页图片。"),
            Image.fromBytes(PNG_1X1),
            Image.fromBytes(PNG_1X1),
        ])
        found = await self.context.send_message(session, chain)
        self.assertTrue(found)
        self.assertEqual(len(self.bot1.calls), 1)
        call = self.bot1.calls[0]
        self.assertEqual(call[0], "group")
        self.assertEqual(call[1]["group_id"], 123)
        segments = call[1]["message"]
        self.assertEqual([item["type"] for item in segments], ["text", "image", "image"])
        self.assertEqual(segments[0]["data"]["text"], "共 2 页图片。")
        self.assertTrue(segments[1]["data"]["file"].startswith("base64://"))
        self.assertTrue(segments[2]["data"]["file"].startswith("base64://"))
        self.assertEqual(len(self.bot2.calls), 0)

    async def test_network_exception_is_unknown_and_cancel_propagates(self) -> None:
        self.bot1.mode = "raise"
        result = await self.sender.send(DeliveryMessage(
            umo="demo_qq_01:GroupMessage:123",
            kind="text", page_index=1, page_count=1, text="x",
        ))
        self.assertEqual(result.status, SendStatus.UNKNOWN)
        self.assertEqual(result.code, "sender_exception")
        self.bot1.mode = "cancel"
        with self.assertRaises(asyncio.CancelledError):
            await self.sender.send(DeliveryMessage(
                umo="demo_qq_01:GroupMessage:123",
                kind="text", page_index=1, page_count=1, text="x",
            ))

    async def test_malformed_context_result_is_unknown(self) -> None:
        async def malformed(session, chain):
            return "weird"
        self.context.send_message = malformed
        result = await self.sender.send(DeliveryMessage(
            umo="demo_qq_01:GroupMessage:123",
            kind="text", page_index=1, page_count=1, text="x",
        ))
        self.assertEqual(result.status, SendStatus.UNKNOWN)

    async def test_delivery_core_to_real_router_to_fake_bot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sites = SiteService(Path(tmp) / "plugin.sqlite3")
            await sites.open()
            try:
                profiles = ContentProfileService(sites)
                tasks = PushTaskService(profiles, sites.store)
                site = await sites.create_site(SiteCreate(
                    name="发送站点", base_url="http://example.test/proxy",
                    enabled=True, timeout_seconds=5,
                ))
                profile = await profiles.create_profile(ProfileCreate(
                    name="发送组合", site_id=site.id, items=[item()],
                ))
                task = await tasks.create_task(TaskCreate(
                    name="发送任务", profile_id=profile.id,
                    output_format="text",
                    umos=["demo_qq_01:GroupMessage:123"],
                ))
                service = DeliveryService(
                    tasks=tasks, profiles=profiles, sites=sites,
                    profile_api=StubApi(), store=sites.store,
                    sender=self.sender,
                    limits=DeliveryLimits(
                        max_execution_seconds=5, sender_timeout_seconds=5,
                    ),
                )
                try:
                    result = await service.execute(task.id, "e2e-host-1")
                finally:
                    await service.close()
                self.assertEqual(result.status, "success")
                self.assertIsNone(result.error_code)
                self.assertEqual(len(self.bot1.calls), 1)
                self.assertEqual(self.bot1.calls[0][1]["group_id"], 123)
                self.assertEqual(
                    self.bot1.calls[0][1]["message"][0]["data"]["text"],
                    "共享正文",
                )
            finally:
                await sites.close()
