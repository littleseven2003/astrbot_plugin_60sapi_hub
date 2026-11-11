from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

from core.chat_commands import ChatCommandService
from core.chat_content import ChatContentQuery
from core.chat_image import ChatImageQuery, ChatImageReply
from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.profile_api import ProfileApi
from core.profile_image import (
    FontUnavailableError,
    ProfileImageRenderer,
    find_cjk_font,
)
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

SECRET = "simulated-secret-abc123"
UMO = "bot:GroupMessage:g"
BASE = "https://api.example.test:8443/" + SECRET + "/reverse-proxy"


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


class ChatSourceOutputTest(unittest.IsolatedAsyncioTestCase):
    """聊天出口经过真实命令路径，检查对外来源展示。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self._tmp.name) / "chat.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)

        async def handler(request: httpx.Request) -> httpx.Response:
            body = {
                "code": 200, "message": "ok",
                "data": {"date": "2026-11-11", "news": ["第一条中文新闻"]},
            }
            return httpx.Response(200, content=json.dumps(body).encode())

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.snapshots = ImageSnapshotStore()
        self.image_service = ProfileImageService()
        self.api = ProfileApi(
            self.profiles, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.image_service,
        )
        self.service = ChatCommandService(
            self.tasks, self.profiles, self.sites,
            content_query=ChatContentQuery(self.tasks, self.api),
            image_query=ChatImageQuery(self.tasks, self.api),
        )

    async def asyncTearDown(self) -> None:
        await self.image_service.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _task(self, source_kind: str, output_format: str = "text"):
        site = await self.sites.create_site(
            SiteCreate(
                name="聊天来源站点",
                base_url=BASE,
                source_kind=source_kind,
                enabled=True,
                timeout_seconds=5,
            ),
        )
        profile = await self.profiles.create_profile(
            ProfileCreate(
                name="聊天来源组合",
                site_id=site.id,
                items=[{"content_type": "daily_news", "params": {}, "limit": 5}],
            ),
        )
        return await self.tasks.create_task(
            TaskCreate(
                name="聊天来源任务",
                profile_id=profile.id,
                output_format=output_format,
                umos=[UMO],
            ),
        )

    async def test_get_command_shows_domain_only(self) -> None:
        task = await self._task("public")
        text = await self.service.handle(UMO, "/60s get " + task.id)
        self.assertIn("数据来源：api.example.test", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(":8443", text)
        self.assertNotIn("/reverse-proxy", text)

    async def test_get_command_hides_self_built_address(self) -> None:
        task = await self._task("self_built")
        text = await self.service.handle(UMO, "/60s get " + task.id)
        self.assertIn("数据来源：自建服务", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("api.example.test", text)

    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_image_command_uses_domain_footer(self) -> None:
        task = await self._task("public", output_format="image")
        captured: list[str] = []
        original = ProfileImageRenderer._footer_lines

        def recording(renderer, section, created_at, width):
            lines = original(renderer, section, created_at, width)
            captured.append(
                "".join(part.text for line in lines for part in line.parts),
            )
            return lines

        with mock.patch.object(
            ProfileImageRenderer, "_footer_lines", recording,
        ):
            reply = await self.service.handle(UMO, "/60s image " + task.id)
        self.assertIsInstance(reply, ChatImageReply)
        self.assertEqual(reply.image_count, 1)
        self.assertTrue(reply.images[0].startswith(b"\x89PNG\r\n\x1a\n"))
        # 图片页脚由聊天命令实际渲染，来源只显示域名。
        self.assertTrue(captured)
        footer = captured[0]
        self.assertIn("数据来源：api.example.test", footer)
        self.assertNotIn(SECRET, footer)
        self.assertNotIn(":8443", footer)
        self.assertNotIn(SECRET, reply.text)

    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_image_command_hides_self_built_address(self) -> None:
        task = await self._task("self_built", output_format="image")
        captured: list[str] = []
        original = ProfileImageRenderer._footer_lines

        def recording(renderer, section, created_at, width):
            lines = original(renderer, section, created_at, width)
            captured.append(
                "".join(part.text for line in lines for part in line.parts),
            )
            return lines

        with mock.patch.object(
            ProfileImageRenderer, "_footer_lines", recording,
        ):
            reply = await self.service.handle(UMO, "/60s image " + task.id)
        self.assertIsInstance(reply, ChatImageReply)
        self.assertEqual(reply.image_count, 1)
        self.assertTrue(captured)
        self.assertIn("数据来源：自建服务", captured[0])
        self.assertNotIn(SECRET, captured[0])
        self.assertNotIn("api.example.test", captured[0])
