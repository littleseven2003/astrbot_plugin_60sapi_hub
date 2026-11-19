"""表单草稿的能力测试、预览与出图：不写任务、与已保存快照隔离。"""

from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from PIL import Image

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview
from core.profile_image import FontUnavailableError, find_cjk_font
from core.sites import SiteCreate, SiteService
from core.task_api import TaskApi
from core.tasks import PushTaskService, TaskCreate


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


def payload_for(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200,
            "data": {"date": "2026-11-19", "news": ["新闻一", "新闻二", "新闻三"]},
        }
    return {"code": 200, "data": [{"title": "热榜一", "hot_value": 1}]}


@unittest.skipUnless(has_font(), "no CJK font available")
class TaskDraftApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "draft.sqlite3")
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.calls: list[str] = []
        self.fail = False

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            if self.fail:
                return httpx.Response(500, content=b"upstream down")
            return httpx.Response(
                200, content=json.dumps(payload_for(request.url.path)).encode(),
            )

        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.snapshots = ImageSnapshotStore()
        self.images = ProfileImageService()
        self.preview = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.api = TaskApi(
            self.tasks, self.sites,
            content=self.content, plan_preview=self.preview,
        )
        self.first = await self.sites.create_site(SiteCreate(
            name="首选站点", base_url="https://first.example.test/api",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.backup = await self.sites.create_site(SiteCreate(
            name="备用站点", base_url="https://backup.example.test/api",
            enabled=True, timeout_seconds=5, source_kind="public",
        ))
        self.disabled = await self.sites.create_site(SiteCreate(
            name="停用站点", base_url="https://off.example.test/api",
            enabled=False, timeout_seconds=5, source_kind="public",
        ))

    async def asyncTearDown(self) -> None:
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    def draft(self, **overrides) -> dict:
        data = {
            "name": "草稿任务",
            "items": [
                {"content_type": "daily_news", "params": {}, "limit": 2},
            ],
            "sites": [self.first.id],
            "output_format": "image",
            "image_style": "floral",
        }
        data.update(overrides)
        return data

    async def test_invalid_drafts_return_field_errors(self) -> None:
        missing_items = await self.api.test_draft(self.draft(items=[]))
        self.assertEqual(missing_items.status_code, 400)
        self.assertEqual(missing_items.body["data"]["field"], "items")

        bad_style = await self.api.test_draft(self.draft(image_style="unknown"))
        self.assertEqual(bad_style.status_code, 400)
        self.assertEqual(bad_style.body["data"]["field"], "image_style")

        bad_site = await self.api.test_draft(self.draft(sites=["missing-site"]))
        self.assertEqual(bad_site.status_code, 400)
        self.assertEqual(bad_site.body["data"]["code"], "invalid_sites")

        bad_format = await self.api.test_draft(self.draft(output_format="html"))
        self.assertEqual(bad_format.status_code, 400)
        self.assertEqual(bad_format.body["data"]["field"], "output_format")

    async def test_capability_skips_disabled_and_keeps_no_task(self) -> None:
        result = await self.api.test_draft(
            self.draft(sites=[self.disabled.id, self.backup.id]),
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertIs(data["draft"], True)
        self.assertIs(data["satisfied"], True)
        self.assertEqual(data["site_id"], self.backup.id)
        self.assertEqual([entry["reason"] for entry in data["checked"]],
                         ["site_disabled", None])
        # 草稿不落库：任务列表仍为空。
        self.assertEqual(await self.tasks.list_tasks(), [])

    async def test_preview_and_draft_image_are_isolated(self) -> None:
        result = await self.api.preview_draft(self.draft(), owner="alice")
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertIs(data["draft"], True)
        self.assertEqual(data["image_style"], "floral")
        self.assertEqual(data["task_name"], "草稿任务")
        snapshot_id = data["image_snapshot"]["id"]
        snapshot = self.snapshots.get(snapshot_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.profile_id, "")
        self.assertIs(snapshot.data.get("draft"), True)

        rendered = await self.api.generate_draft_image(
            {"snapshot_id": snapshot_id}, owner="alice",
        )
        self.assertEqual(rendered.status_code, 200, rendered.body)
        images = rendered.body["data"]["images"]
        self.assertEqual(len(images), 1)
        raw = base64.b64decode(images[0]["png_base64"], validate=True)
        with Image.open(io.BytesIO(raw)) as opened:
            self.assertEqual(opened.width, 900)

        # 跨身份、跨入口都不可用。
        wrong_owner = await self.api.generate_draft_image(
            {"snapshot_id": snapshot_id}, owner="bob",
        )
        self.assertEqual(wrong_owner.status_code, 403)

        task = await self.tasks.create_task(TaskCreate(
            name="已保存任务",
            items=[{"content_type": "daily_news", "params": {}, "limit": 1}],
            output_format="image", umos=[], sites=[self.first.id],
        ))
        cross = await self.api.generate_task_image(
            task.id, {"snapshot_id": snapshot_id}, owner="alice",
        )
        self.assertEqual(cross.status_code, 404)
        self.assertEqual(cross.body["data"]["code"], "snapshot_not_found")

        saved = await self.api.preview_task(
            task.id, {}, owner="alice",
        )
        self.assertEqual(saved.status_code, 200, saved.body)
        saved_snapshot = saved.body["data"]["image_snapshot"]["id"]
        reverse = await self.api.generate_draft_image(
            {"snapshot_id": saved_snapshot}, owner="alice",
        )
        self.assertEqual(reverse.status_code, 404)

    async def test_preview_does_not_touch_saved_task_or_delivery(self) -> None:
        task = await self.tasks.create_task(TaskCreate(
            name="已保存任务",
            items=[{"content_type": "daily_news", "params": {}, "limit": 1}],
            output_format="text", umos=["aiocqhttp:GroupMessage:g:1"],
            sites=[self.first.id],
        ))
        before = await self.tasks.get_task(task.id)
        result = await self.api.preview_draft(
            self.draft(items=[
                {"content_type": "daily_news", "params": {}, "limit": 3},
                {"content_type": "zhihu_hot", "params": {}, "limit": 1},
            ]),
            owner="alice",
        )
        self.assertEqual(result.status_code, 200, result.body)
        after = await self.tasks.get_task(task.id)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.items, before.items)
        records = await self.sites.store.list_delivery_execution_ids(limit=10, offset=0)
        self.assertEqual(tuple(records), ())

    async def test_preview_draft_reports_all_failed_content(self) -> None:
        self.fail = True
        result = await self.api.preview_draft(self.draft(), owner="alice")
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.body["data"]["code"], "content_unavailable")

    async def test_preview_with_unsaved_style_and_sites(self) -> None:
        result = await self.api.preview_draft(
            self.draft(
                sites=[self.backup.id],
                image_style="bold_studio",
                items=[
                    {"content_type": "daily_news", "params": {}, "limit": 1},
                    {"content_type": "zhihu_hot", "params": {}, "limit": 1},
                ],
            ),
            owner="alice",
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(len(data["sections"]), 2)
        self.assertEqual(data["site_id"], self.backup.id)
        self.assertEqual(data["image_style"], "bold_studio")
        snapshot = self.snapshots.get(data["image_snapshot"]["id"])
        self.assertEqual(snapshot.site_id, self.backup.id)
        self.assertEqual(snapshot.data["image_style"], "bold_studio")

    async def test_preview_draft_rejects_bad_body(self) -> None:
        result = await self.api.preview_draft([], owner="alice")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.body["data"]["code"], "invalid_body")
        bad_force = await self.api.preview_draft(
            {**self.draft(), "force_refresh": "yes"}, owner="alice",
        )
        self.assertEqual(bad_force.status_code, 400)
        self.assertEqual(bad_force.body["data"]["code"], "invalid_force_refresh")


if __name__ == "__main__":
    unittest.main()
