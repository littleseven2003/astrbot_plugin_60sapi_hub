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
        self.service = DeliveryService(
            tasks=self.tasks, profiles=self.profiles, sites=self.sites,
            profile_api=StubApi(), store=self.sites.store,
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
        detail = await self.api.get_execution(execution_id)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.body["data"]["targets"][0]["pages"][0]["status"], "success")
        again = await self.api.execute(task.id, {
            "execution_id": execution_id,
            "expected_task_revision": task.revision,
            "expected_profile_revision": self.profile.revision,
            "expected_site_revision": self.site.revision,
        })
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.sender.calls), 2)

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
