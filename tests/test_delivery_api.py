from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.delivery import DeliveryLimits, DeliveryService
from core.delivery_api import DeliveryApi
from core.profiles import ContentProfileService, ProfileCreate
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate
from tests.test_delivery import FakeSender, StubApi, item


class Inspector:
    def inspect_target(self, umo):
        return {
            "valid": True, "reason": None, "umo": umo,
            "platform_id": umo.split(":", 1)[0],
            "message_type": umo.split(":", 2)[1],
            "target": umo.rsplit(":", 1)[-1].rsplit("_", 1)[-1],
            "isolated": "_" in umo,
        }


class DeliveryApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.sites = SiteService(Path(self.tmp.name) / "plugin.sqlite3")
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.site = await self.sites.create_site(SiteCreate(
            name="站点", base_url="http://example.test/proxy",
            enabled=True, timeout_seconds=5,
        ))
        self.profile = await self.profiles.create_profile(ProfileCreate(
            name="组合", site_id=self.site.id, items=[item()],
        ))
        self.sender = FakeSender()
        self.stub = StubApi()
        self.service = DeliveryService(
            tasks=self.tasks, profiles=self.profiles, sites=self.sites,
            profile_api=self.stub, store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(max_execution_seconds=5, sender_timeout_seconds=5),
        )
        self.api = DeliveryApi(
            delivery=self.service, tasks=self.tasks, profiles=self.profiles,
            sites=self.sites, store=self.sites.store, inspector=Inspector(),
        )

    async def asyncTearDown(self) -> None:
        await self.service.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def _task(self, umos):
        return await self.tasks.create_task(TaskCreate(
            name="任务", profile_id=self.profile.id,
            output_format="text", umos=umos,
        ))

    async def _image_task(self, umos):
        return await self.tasks.create_task(TaskCreate(
            name="图片任务", profile_id=self.profile.id,
            output_format="image", umos=umos,
        ))

    async def test_preview_execute_list_detail_and_projection(self) -> None:
        task = await self._task([
            "demo_qq_01:GroupMessage:123",
            "demo_qq_01:GroupMessage:456_789",
        ])
        preview = await self.api.preview(task.id)
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.body["data"]["can_deliver"])
        self.assertEqual(
            [t["target"] for t in preview.body["data"]["targets"]],
            ["123", "789"],
        )
        execution_id = "manual-1"
        result = await self.api.execute(task.id, {
            "execution_id": execution_id,
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["targets"]), 2)
        self.assertNotIn("owner_token", data)
        self.assertNotIn("owner_token", repr(data))
        self.assertEqual(len(self.sender.calls), 2)

        listed = await self.api.list_executions(limit=5, offset=0)
        self.assertEqual(listed.body["data"]["count"], 1)
        # 投影语义：一个栏目一张图，对外用 image_count / image_index。
        summary = listed.body["data"]["executions"][0]
        # 汇总的 image_count = 该执行下所有目标的槽位行数（2 目标 × 1 张）。
        self.assertEqual(summary["image_count"], 2)
        self.assertNotIn("page_count", summary)
        detail = await self.api.get_execution(execution_id)
        self.assertEqual(detail.status_code, 200)
        target_payload = detail.body["data"]["targets"][0]
        self.assertNotIn("pages", target_payload)
        item = target_payload["images"][0]
        self.assertEqual(item["status"], "success")
        self.assertEqual(item["image_index"], 1)
        self.assertNotIn("page_index", item)
        again = await self.api.execute(task.id, {
            "execution_id": execution_id,
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.sender.calls), 2)

    async def test_partial_image_failure_projection(self) -> None:
        self.stub.image_sections[1]["status"] = "failed"
        task = await self._image_task(["demo_qq_01:GroupMessage:123"])
        result = await self.api.execute(task.id, {
            "execution_id": "image-partial-1",
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["status"], "partial")
        self.assertEqual(
            result.body["data"]["error_code"], "section_image_failed",
        )

        detail = await self.api.get_execution("image-partial-1")
        data = detail.body["data"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["error_code"], "section_image_failed")
        self.assertIs(data["image_partial"], True)
        self.assertEqual(data["delivered_image_count"], 1)
        self.assertEqual(data["skipped_image_count"], 1)
        self.assertEqual(data["distinct_skipped_section_count"], 1)
        self.assertEqual(data["failed_section_count"], 1)
        self.assertEqual(data["failed_sections"], [{
            "label": "知乎热榜",
            "reason": "image_failed",
            "image_index": 2,
        }])
        target = data["targets"][0]
        self.assertEqual(target["status"], "partial")
        self.assertEqual(target["error_code"], "section_image_failed")
        self.assertEqual(target["delivered_image_count"], 1)
        self.assertEqual(target["skipped_image_count"], 1)
        self.assertEqual(target["skipped_section_count"], 1)
        self.assertEqual(
            [image["status"] for image in target["images"]],
            ["success", "skipped"],
        )
        self.assertNotIn("label", target["images"][0])
        self.assertEqual(target["images"][1]["label"], "知乎热榜")
        self.assertEqual(
            target["images"][1]["error_code"], "section_image_failed",
        )

        listed = await self.api.list_executions(limit=5, offset=0)
        summary = listed.body["data"]["executions"][0]
        self.assertEqual(summary["status"], "partial")
        self.assertIs(summary["image_partial"], True)
        self.assertEqual(summary["delivered_image_count"], 1)
        self.assertEqual(summary["skipped_image_count"], 1)
        self.assertEqual(summary["distinct_skipped_section_count"], 1)
        self.assertEqual(summary["failed_section_count"], 1)

    async def test_multi_target_counts_are_records_not_sections(self) -> None:
        self.stub.image_sections[1]["status"] = "failed"
        task = await self._image_task([
            "demo_qq_01:GroupMessage:123",
            "demo_qq_01:GroupMessage:456",
        ])
        result = await self.api.execute(task.id, {
            "execution_id": "image-partial-multi",
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200, result.body)
        detail = await self.api.get_execution("image-partial-multi")
        data = detail.body["data"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["image_count"], 4)
        self.assertEqual(data["delivered_image_count"], 2)
        # 记录数：两个目标各有一个未送达槽位。
        self.assertEqual(data["skipped_image_count"], 2)
        # 栏目数：同一栏目跨目标只算一次。
        self.assertEqual(data["distinct_skipped_section_count"], 1)
        self.assertEqual(data["failed_section_count"], 1)
        self.assertEqual(
            [target["status"] for target in data["targets"]],
            ["partial", "partial"],
        )
        for target in data["targets"]:
            self.assertEqual(target["skipped_image_count"], 1)
            self.assertEqual(target["skipped_section_count"], 1)

        listed = await self.api.list_executions(limit=5, offset=0)
        summary = listed.body["data"]["executions"][0]
        self.assertEqual(summary["skipped_image_count"], 2)
        self.assertEqual(summary["distinct_skipped_section_count"], 1)
        self.assertEqual(summary["failed_section_count"], 1)

    async def test_target_statuses_differ_and_sources_stay_distinct(
        self,
    ) -> None:
        self.stub.image_sections[1]["status"] = "failed"
        task = await self._image_task([
            "demo_qq_01:GroupMessage:123",
            "demo_qq_01:GroupMessage:456",
        ])
        self.sender.modes = ["success", "failed"]
        result = await self.api.execute(task.id, {
            "execution_id": "image-partial-failed",
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200, result.body)
        detail = await self.api.get_execution("image-partial-failed")
        data = detail.body["data"]
        self.assertEqual(data["status"], "partial")
        self.assertIsNone(data["error_code"])
        self.assertEqual(
            [target["status"] for target in data["targets"]],
            ["partial", "failed"],
        )
        self.assertEqual(data["delivered_image_count"], 1)
        self.assertEqual(data["skipped_image_count"], 2)
        self.assertEqual(data["distinct_skipped_section_count"], 1)
        self.assertEqual(data["failed_section_count"], 1)
        first, second = data["targets"]
        self.assertEqual(first["images"][0]["status"], "success")
        self.assertEqual(first["images"][1]["status"], "skipped")
        self.assertEqual(
            first["images"][1]["error_code"], "section_image_failed",
        )
        self.assertEqual(second["images"][0]["status"], "failed")
        self.assertEqual(second["images"][0]["error_code"], "send_failed")
        self.assertEqual(second["images"][1]["status"], "skipped")
        self.assertEqual(
            second["images"][1]["error_code"], "section_image_failed",
        )

    async def test_legacy_failed_sections_json_is_sanitized(self) -> None:
        task = await self._task(["demo_qq_01:GroupMessage:123"])
        result = await self.api.execute(task.id, {
            "execution_id": "legacy-1",
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200, result.body)
        await self.sites.store.set_delivery_content(
            "legacy-1",
            content_hash="legacy",
            content_status="partial",
            failed_sections_json=(
                '[{"label": "旧栏目", "reason": "image_failed"}, '
                '"字符串栏目", '
                '{"label": "危险栏目", "code": "Bad Code", '
                '"message": "secret 细节"}, '
                '{"label": "", "reason": "empty"}, '
                '{"label": "旧栏目", "reason": "image_failed"}]'
            ),
            status="success",
        )
        detail = await self.api.get_execution("legacy-1")
        data = detail.body["data"]
        self.assertEqual(data["failed_sections"], [
            {"label": "旧栏目", "reason": "image_failed"},
            {"label": "字符串栏目"},
            {"label": "危险栏目"},
        ])
        self.assertNotIn("secret 细节", repr(data))
        self.assertIs(data["image_partial"], False)
        self.assertEqual(data["delivered_image_count"], 1)
        self.assertEqual(data["skipped_image_count"], 0)
        self.assertEqual(data["distinct_skipped_section_count"], 0)
        listed = await self.api.list_executions(limit=5, offset=0)
        summary = listed.body["data"]["executions"][0]
        self.assertEqual(summary["failed_section_count"], 3)

        await self.sites.store.set_delivery_content(
            "legacy-1",
            content_hash="legacy",
            content_status="partial",
            failed_sections_json="not-json",
            status="success",
        )
        broken = await self.api.get_execution("legacy-1")
        self.assertEqual(broken.status_code, 200)
        self.assertEqual(broken.body["data"]["failed_sections"], [])
        listed = await self.api.list_executions(limit=5, offset=0)
        self.assertEqual(
            listed.body["data"]["executions"][0]["failed_section_count"], 0,
        )

    async def test_revision_conflict_and_empty_targets_no_send(self) -> None:
        task = await self._task(["demo_qq_01:GroupMessage:123"])
        result = await self.api.execute(task.id, {
            "execution_id": "stale-1",
            "expected_task_revision": task.revision + 1,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.body["data"]["code"], "revision_conflict")
        self.assertEqual(len(self.sender.calls), 0)
        empty = await self._task([])
        preview = await self.api.preview(empty.id)
        self.assertFalse(preview.body["data"]["can_deliver"])
        self.assertEqual(preview.body["data"]["reason"], "empty_targets")
        result = await self.api.execute(empty.id, {
            "execution_id": "empty-1",
            "expected_task_revision": empty.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body["data"]["status"], "no_targets")
        self.assertEqual(len(self.sender.calls), 0)

    async def test_invalid_payload_and_pagination_bounds(self) -> None:
        task = await self._task(["demo_qq_01:GroupMessage:123"])
        for payload in (
            {}, {"execution_id": ""},
            {"execution_id": "x", "expected_task_revision": True,
             "expected_profile_revision": 1, "expected_site_revision": 1},
        ):
            result = await self.api.execute(task.id, payload)
            self.assertEqual(result.status_code, 400, result.body)
        for kwargs in (
            {"limit": 0}, {"limit": 51}, {"offset": -1}, {"task_id": 1},
        ):
            result = await self.api.list_executions(**kwargs)
            self.assertEqual(result.status_code, 400)
        self.assertEqual(len(self.sender.calls), 0)

    async def test_history_survives_task_deletion(self) -> None:
        task = await self._task(["demo_qq_01:GroupMessage:123"])
        result = await self.api.execute(task.id, {
            "execution_id": "history-1",
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(result.status_code, 200)
        await self.tasks.delete_task(task.id, task.revision)
        listed = await self.api.list_executions(task_id=task.id)
        self.assertEqual(listed.body["data"]["count"], 1)
        detail = await self.api.get_execution("history-1")
        self.assertEqual(detail.status_code, 200)

    async def test_execution_list_paginates_beyond_twenty(self) -> None:
        for index in range(21):
            await self.sites.store.create_delivery_execution(
                execution_id=f"page-{index:02d}", task_id="task-pages",
                task_name="翻页任务", task_revision=1,
                profile_id="profile-pages", profile_revision=1,
                site_id="site-pages", site_revision=1,
                output_format="text", umos=(),
            )
        first = await self.api.list_executions(limit=20, offset=0)
        self.assertEqual(first.body["data"]["count"], 20)
        self.assertTrue(first.body["data"]["has_more"])
        second = await self.api.list_executions(limit=20, offset=20)
        self.assertEqual(second.body["data"]["count"], 1)
        self.assertFalse(second.body["data"]["has_more"])
        self.assertNotEqual(
            first.body["data"]["executions"][0]["id"],
            second.body["data"]["executions"][0]["id"],
        )
