"""聊天、手动投递与定时投递三条链路的真实出图样式回归。

上游用 httpx.MockTransport 模拟，发送器是假的，但图片由真实渲染器生成：
断言最终 PNG 的页眉底色来自任务所选样式，证明样式沿快照传到了每条链路。
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx
from PIL import Image

from core.chat_image import ChatImageQuery, ChatImageReply
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.delivery import (
    DeliveryLimits,
    DeliveryMessage,
    DeliverySendResult,
    DeliveryService,
    SendStatus,
)
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.image_styles import PRESETS
from core.plan_preview import PlanPreview
from core.profile_image import FontUnavailableError, find_cjk_font
from core.scheduler import SchedulerService
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


def header_pixel(png: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(png)).convert("RGB") as opened:
        return opened.getpixel((opened.width - 60, 30))


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[DeliveryMessage] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.messages.append(message)
        return DeliverySendResult(status=SendStatus.SUCCESS)

    def images(self) -> list[bytes]:
        return [
            message.image_png
            for message in self.messages
            if message.image_png is not None
        ]


@unittest.skipUnless(has_font(), "no CJK font available")
class ImageStyleChainTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "chain.sqlite3")
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.site = await self.sites.create_site(
            SiteCreate(
                name="链路站点",
                base_url="https://chain.example.test/api",
                timeout_seconds=5,
                source_kind="public",
            ),
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/v2/60s"):
                return httpx.Response(200, content=json.dumps({
                    "code": 200,
                    "data": {
                        "date": "2026-11-19",
                        "news": ["链路新闻一", "链路新闻二", "链路新闻三"],
                    },
                }).encode())
            return httpx.Response(200, content=json.dumps({
                "code": 200,
                "data": [{"title": "链路热榜一", "hot_value": 1}],
            }).encode())

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.sender = RecordingSender()
        self.delivery = DeliveryService(
            tasks=self.tasks,
            sites=self.sites,
            plan_preview=self.api,
            store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(
                max_execution_seconds=15, max_send_seconds=15,
            ),
        )
        self.clock_now = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.scheduler = SchedulerService(
            tasks=self.tasks,
            store=self.sites.store,
            delivery=self.delivery,
            clock=lambda: self.clock_now,
        )

    async def asyncTearDown(self) -> None:
        await self.scheduler.close()
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def _task(self, image_style: str, *, schedule=None):
        return await self.tasks.create_task(
            TaskCreate(
                name=f"链路任务-{image_style}",
                items=[{"content_type": "daily_news", "params": {}, "limit": 3}],
                output_format="image",
                umos=[UMO],
                sites=[self.site.id],
                image_style=image_style,
                schedule=schedule,
            ),
        )

    def _assert_style(self, pngs: list[bytes], style_id: str) -> None:
        self.assertTrue(pngs, "链路没有产出任何图片")
        expected = PRESETS[style_id].header_bg
        for png in pngs:
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(header_pixel(png), expected, style_id)

    async def test_chat_image_uses_task_style(self) -> None:
        task = await self._task("bold_studio")
        reply = await ChatImageQuery(self.tasks, self.api).query(UMO, task.id)
        self.assertIsInstance(reply, ChatImageReply)
        self._assert_style(list(reply.images), "bold_studio")

    async def test_manual_delivery_uses_task_style(self) -> None:
        task = await self._task("floral")
        record = await self.delivery.execute(task.id, "style-manual-1")
        self.assertNotEqual(record.status, "failed")
        self._assert_style(self.sender.images(), "floral")

    async def test_scheduled_delivery_uses_task_style(self) -> None:
        task = await self._task(
            "mist",
            schedule={"kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30"},
        )
        await self.scheduler.enable_task(task.id, 1)
        self.clock_now = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc)
        self.assertEqual(await self.scheduler.check_once(), 1)
        self._assert_style(self.sender.images(), "mist")


if __name__ == "__main__":
    unittest.main()
