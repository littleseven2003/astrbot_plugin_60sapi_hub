"""多站点按栏目拼合：预览、能力测试、投递与确认格式版本。"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.delivery import DeliveryLimits, DeliveryMessage, DeliverySendResult, DeliveryService, SendStatus
from core.delivery_api import DeliveryApi
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview
from core.profile_image import FontUnavailableError, find_cjk_font
from core.sites import SiteCreate, SiteService
from core.task_api import TaskApi
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[DeliveryMessage] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.messages.append(message)
        return DeliverySendResult(status=SendStatus.SUCCESS)


class _Inspector:
    def inspect_target(self, umo: str) -> dict:
        return {
            "umo": umo, "platform_id": "aiocqhttp",
            "message_type": "GroupMessage", "target": "g:1",
            "isolated": True, "valid": True, "reason": None,
        }


class MultiSiteAssemblyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "multi.sqlite3"
        self.hosts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            path = request.url.path
            if path.endswith("/v2/60s"):
                news = (
                    ["A新闻一", "A新闻二"]
                    if host.startswith("a.") else []
                )
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-03-02", "news": news},
                }).encode())
            hot = (
                [{"title": "B热榜一", "hot_value": 1},
                 {"title": "B热榜二", "hot_value": 2}]
                if host.startswith("b.") else []
            )
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok", "data": hot,
            }).encode())

        self.sites = SiteService(self.path)
        await self.sites.open()
        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.tasks = PushTaskService(self.sites.store)
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.preview = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.sender = RecordingSender()
        self.delivery = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=self.preview,
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(),
        )
        self.delivery_api = DeliveryApi(
            delivery=self.delivery, tasks=self.tasks, sites=self.sites,
            store=self.sites.store, inspector=_Inspector(),
        )
        self.a = await self.sites.create_site(SiteCreate(
            name="站点A", base_url="https://a.example.test/proxy",
            timeout_seconds=5, source_kind="self_built",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="站点B", base_url="https://b.example.test/proxy",
            timeout_seconds=5, source_kind="self_built",
        ))
        self.task = await self.tasks.create_task(TaskCreate(
            name="拼合任务",
            items=[
                {"content_type": "daily_news", "params": {}, "limit": 2},
                {"content_type": "zhihu_hot", "params": {}, "limit": 2},
            ],
            sites=[self.a.id, self.b.id],
            output_format="text",
            umos=[UMO],
        ))

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def test_preview_combines_one_column_per_site(self) -> None:
        result = await self.preview.preview_assembled(
            {"task_id": self.task.id}, site_ids=[self.a.id, self.b.id],
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["source_mode"], "multi")
        self.assertEqual(
            [(item["content_type"], item["site_id"]) for item in data["assignments"]],
            [("daily_news", self.a.id), ("zhihu_hot", self.b.id)],
        )
        self.assertEqual(data["status"], "complete")
        self.assertIn("A新闻一", data["outputs"]["text"])
        self.assertIn("B热榜一", data["outputs"]["text"])
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertEqual(
            {site_id for site_id, _ in snapshot.sites},
            {self.a.id, self.b.id},
        )

    async def test_task_preview_uses_assembly(self) -> None:
        api = TaskApi(
            self.tasks, self.sites,
            content=self.content, plan_preview=self.preview,
        )
        result = await api.preview_task(self.task.id, {"force_refresh": True})
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["source_mode"], "multi")
        self.assertEqual(data["site_name"], "多站点")
        self.assertEqual(len(data["assignments"]), 2)

    async def test_capability_reports_multi_site_plan(self) -> None:
        api = TaskApi(
            self.tasks, self.sites,
            content=self.content, plan_preview=self.preview,
        )
        result = await api.test_task(self.task.id)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertTrue(data["satisfied"])
        self.assertEqual(data["source_mode"], "multi")
        self.assertEqual(
            [(item["content_type"], item["site_id"]) for item in data["assignments"]],
            [("daily_news", self.a.id), ("zhihu_hot", self.b.id)],
        )
        self.assertIn("需要多站点拼合", data["message"])

    async def test_delivery_sends_combined_content_and_records_empty_site(self) -> None:
        record = await self.delivery.execute(self.task.id, "multi-1")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("A新闻一", text)
        self.assertIn("B热榜一", text)
        self.assertEqual(record.site_id, "")
        self.assertEqual(record.content_status, "complete")

    async def test_old_confirmation_format_is_rejected(self) -> None:
        preview = await self.delivery_api.preview(self.task.id)
        data = preview.body["data"]
        self.assertEqual(data["confirmation_version"], 2)
        payload = {
            "execution_id": "old-confirm",
            "expected_task_revision": data["task_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": False,
        }
        rejected = await self.delivery_api.execute(self.task.id, payload)
        self.assertEqual(rejected.status_code, 409, rejected.body)
        self.assertEqual(rejected.body["data"]["code"], "confirmation_stale")
        self.assertEqual(
            rejected.body["data"]["reason"], "confirmation_version_changed",
        )
        self.assertEqual(self.sender.messages, [])
        accepted = await self.delivery_api.execute(self.task.id, {
            **payload, "confirmation_version": 2,
        })
        self.assertEqual(accepted.status_code, 200, accepted.body)
        self.assertEqual(accepted.body["data"]["site_name"], "多站点")
        # 同一执行 ID 已完成后再次查询：即使再次缺格式版本也取回原记录。
        again = await self.delivery_api.execute(self.task.id, {
            "execution_id": "old-confirm",
            "expected_task_revision": data["task_revision"],
            "expected_candidates": data["candidate_snapshot"],
        })
        self.assertEqual(again.status_code, 200, again.body)
        self.assertEqual(again.body["data"]["id"], "old-confirm")

    @unittest.skipUnless(has_font(), "no CJK font available")
    async def test_multi_site_image_snapshot_renders(self) -> None:
        result = await self.preview.preview_assembled(
            {"task_id": self.task.id}, site_ids=[self.a.id, self.b.id],
        )
        snapshot_id = result.body["data"]["image_snapshot"]["id"]
        image = await self.preview.generate_image(
            self.task.id, {"snapshot_id": snapshot_id},
        )
        self.assertEqual(image.status_code, 200, image.body)
        images = image.body["data"]["images"]
        self.assertEqual(len(images), 2)
        for entry in images:
            self.assertTrue(
                base64.b64decode(entry["png_base64"]).startswith(b"\x89PNG"),
            )


if __name__ == "__main__":
    unittest.main()
