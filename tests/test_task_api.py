from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from core.plan_preview import PlanPreviewResult
from core.sites import SiteCreate, SiteService, SiteUpdate
from core.task_api import TaskApi
from core.tasks import PushTaskService


def item():
    return {"content_type": "daily_news", "params": {}, "limit": None}


class TaskApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "plugin.sqlite3"
        self.sites = SiteService(self.db)
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.site = await self.sites.create_site(
            SiteCreate(
                name="禁用站点",
                base_url="http://example.test/proxy",
                enabled=False,
                timeout_seconds=5,
            ),
        )
        self.api = TaskApi(self.tasks, self.sites)
        self.payload = {
            "name": "API任务",
            "items": [item()],
            "sites": [self.site.id],
            "output_format": "text",
            "umos": ["aiocqhttp:GroupMessage:g:1"],
        }

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self.tmp.cleanup()

    async def test_crud_and_revision_conflict(self) -> None:
        created = await self.api.create_task(self.payload)
        self.assertEqual(created.status_code, 201, created.body)
        task = created.body["data"]
        self.assertFalse(task["enabled"])
        self.assertFalse(task["can_send"])
        self.assertEqual(task["target_count"], 1)
        self.assertEqual(
            task["items"],
            [{"content_type": "daily_news", "label": "每日新闻", "limit": None}],
        )
        self.assertNotIn("profile_id", task)
        self.assertNotIn("profile_name", task)
        self.assertFalse(task["site_enabled"])
        listed = await self.api.list_tasks()
        self.assertEqual(len(listed.body["data"]["tasks"]), 1)
        got = await self.api.get_task(task["id"])
        self.assertEqual(got.status_code, 200)

        updated_payload = {
            **self.payload,
            "name": "改名",
            "output_format": "image",
            "umos": [],
            "expected_revision": task["revision"],
        }
        updated = await self.api.update_task(task["id"], updated_payload)
        self.assertEqual(updated.status_code, 200, updated.body)
        self.assertEqual(updated.body["data"]["name"], "改名")
        self.assertEqual(updated.body["data"]["target_count"], 0)
        conflict = await self.api.update_task(task["id"], updated_payload)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.body["data"]["code"], "revision_conflict")
        deleted = await self.api.delete_task(
            task["id"],
            {"expected_revision": updated.body["data"]["revision"]},
        )
        self.assertEqual(deleted.status_code, 200)

    async def test_reject_enable_and_scheduled_fields(self) -> None:
        for extra in (
            {"enabled": True}, {"enabled": False}, {"next_run": "x"},
            {"next_run_at": "x"}, {"schedule_preview": {}}, {"running": True},
        ):
            with self.subTest(extra=extra):
                result = await self.api.create_task({**self.payload, **extra})
                self.assertEqual(result.status_code, 400)
                self.assertEqual(
                    result.body["data"]["code"],
                    "unsupported_enable_request",
                )
        for items in ([], [item(), item()], [{"content_type": "unknown"}]):
            with self.subTest(items=items):
                bad_items = await self.api.create_task({**self.payload, "items": items})
                self.assertEqual(bad_items.status_code, 400, bad_items.body)
                self.assertEqual(bad_items.body["data"]["code"], "validation_error")
                self.assertTrue(
                    bad_items.body["data"]["field"].startswith("items"),
                    bad_items.body,
                )
        missing = await self.api.get_task("missing")
        self.assertEqual(missing.status_code, 404)

    async def test_cron_no_candidate_preview_error(self) -> None:
        clock = lambda: datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.api = TaskApi(self.tasks, self.sites, clock=clock)
        created = await self.api.create_task({**self.payload, "schedule": {
            "kind": "cron", "timezone": "UTC", "expression": "0 0 31 2 *",
        }})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            created.body["data"]["schedule_preview"]["error"],
            "schedule_calculation_error",
        )
        self.assertIsNone(created.body["data"]["schedule_error"])
        fixed = await self.api.update_task(created.body["data"]["id"], {
            **self.payload, "expected_revision": 1,
            "schedule": {"kind": "cron", "timezone": "UTC",
                         "expression": "0 0 * * *"},
        })
        self.assertEqual(fixed.status_code, 200)
        self.assertIsNotNone(
            fixed.body["data"]["schedule_preview"]["next_run_at"],
        )

    async def test_schedule_create_update_preserve_clear_and_preview(self) -> None:
        clock = lambda: datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.api = TaskApi(self.tasks, self.sites, clock=clock)
        payload = {**self.payload, "schedule": {
            "kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30",
        }}
        created = await self.api.create_task(payload)
        self.assertEqual(created.status_code, 201, created.body)
        task = created.body["data"]
        self.assertEqual(task["schedule"], payload["schedule"])
        self.assertEqual(task["schedule_preview"]["evaluated_at"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(task["schedule_preview"]["next_run_at"], "2026-01-01T00:30:00+00:00")

        # Dashboard-style edit omits schedule: preserve it and increment once.
        updated = await self.api.update_task(task["id"], {
            **self.payload, "name": "保留计划",
            "expected_revision": task["revision"],
        })
        self.assertEqual(updated.status_code, 200, updated.body)
        self.assertEqual(updated.body["data"]["schedule"], payload["schedule"])
        self.assertEqual(updated.body["data"]["revision"], task["revision"] + 1)
        self.assertEqual(
            updated.body["data"]["schedule_preview"]["evaluated_at"],
            task["schedule_preview"]["evaluated_at"],
        )

        listed = await self.api.list_tasks()
        detail = await self.api.get_task(task["id"])
        self.assertEqual(
            listed.body["data"]["tasks"][0]["schedule_preview"]["evaluated_at"],
            detail.body["data"]["schedule_preview"]["evaluated_at"],
        )

        cleared = await self.api.update_task(task["id"], {
            **self.payload, "schedule": None,
            "expected_revision": updated.body["data"]["revision"],
        })
        self.assertEqual(cleared.status_code, 200, cleared.body)
        self.assertIsNone(cleared.body["data"]["schedule"])
        self.assertIsNone(cleared.body["data"]["schedule_preview"])

    async def test_timezone_unavailable_preserves_plan_on_omitted_update(self) -> None:
        clock = lambda: datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.api = TaskApi(self.tasks, self.sites, clock=clock)
        created = await self.api.create_task({**self.payload, "schedule": {
            "kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30",
        }})
        task = created.body["data"]
        original = __import__("core.schedule", fromlist=["ZoneInfo"]).ZoneInfo

        def unavailable(name):
            if name == "Asia/Shanghai":
                raise ZoneInfoNotFoundError(name)
            return original(name)

        with patch("core.schedule.ZoneInfo", side_effect=unavailable):
            listed = await self.api.list_tasks()
            item = listed.body["data"]["tasks"][0]
            self.assertIsNone(item["schedule_preview"]["next_run_at"])
            self.assertEqual(item["schedule_error"], "schedule_timezone_unavailable")
            self.assertEqual(item["schedule"]["timezone"], "Asia/Shanghai")
            detail = await self.api.get_task(task["id"])
            self.assertEqual(
                detail.body["data"]["schedule_error"],
                "schedule_timezone_unavailable",
            )
            snapshot = await self.tasks.get_config_snapshot(task["id"])
            self.assertEqual(snapshot.task.schedule, None)
            self.assertEqual(
                snapshot.task.schedule_error, "schedule_timezone_unavailable",
            )
            updated = await self.api.update_task(task["id"], {
                **self.payload, "name": "时区不可用时改名",
                "expected_revision": task["revision"],
            })
            self.assertEqual(updated.status_code, 200, updated.body)
            self.assertEqual(
                updated.body["data"]["schedule"]["timezone"], "Asia/Shanghai",
            )
            self.assertEqual(
                updated.body["data"]["revision"], task["revision"] + 1,
            )
        restored = await self.api.get_task(task["id"])
        self.assertEqual(restored.body["data"]["schedule_error"], None)
        self.assertEqual(
            restored.body["data"]["schedule"],
            {"kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30"},
        )
        self.assertIsNotNone(restored.body["data"]["schedule_preview"]["next_run_at"])
        cleared = await self.api.update_task(task["id"], {
            **self.payload, "schedule": None,
            "expected_revision": restored.body["data"]["revision"],
        })
        self.assertEqual(cleared.status_code, 200, cleared.body)
        self.assertIsNone(cleared.body["data"]["schedule"])
        self.assertIsNone(cleared.body["data"]["schedule_error"])

    async def test_schedule_validation_and_runtime_fields(self) -> None:
        invalid = [
            {"kind": "weekly", "timezone": "UTC", "time": "08:00"},
            {"kind": "daily", "timezone": "Not/AZone", "time": "08:00"},
            {"kind": "daily", "timezone": "UTC", "time": "8:00"},
            {"kind": "daily", "timezone": "UTC", "time": "08:00", "x": 1},
            "daily",
        ]
        for schedule in invalid:
            with self.subTest(schedule=schedule):
                result = await self.api.create_task({**self.payload, "schedule": schedule})
                self.assertEqual(result.status_code, 400, result.body)
                self.assertIn(
                    result.body["data"]["code"],
                    ("validation_error", "schedule_timezone_unavailable"),
                )
        result = await self.api.create_task({**self.payload, "schedule": {
            "kind": "daily", "timezone": "UTC", "time": "08:00",
        }})
        self.assertEqual(result.status_code, 201, result.body)
        task = result.body["data"]
        for extra in ({"next_run": "x"}, {"next_run_at": "x"}, {"enabled": True}):
            with self.subTest(extra=extra):
                rejected = await self.api.update_task(task["id"], {
                    **self.payload, "expected_revision": task["revision"], **extra,
                })
                self.assertEqual(rejected.status_code, 400)
                self.assertEqual(rejected.body["data"]["code"], "unsupported_enable_request")

    async def test_invalid_expected_revision_returns_400_and_no_write(self) -> None:
        created = await self.api.create_task(self.payload)
        task = created.body["data"]
        invalid = [None, True, False, 0, -1, 1.0, 1.5, "1", "", []]
        for value in invalid:
            with self.subTest(kind="update", value=value):
                body = {**self.payload, "name": "不能写入", "expected_revision": value}
                result = await self.api.update_task(task["id"], body)
                self.assertEqual(result.status_code, 400, result.body)
                self.assertEqual(result.body["data"]["code"], "validation_error")
                self.assertEqual(result.body["data"]["field"], "expected_revision")
            with self.subTest(kind="delete", value=value):
                result = await self.api.delete_task(
                    task["id"],
                    {"expected_revision": value},
                )
                self.assertEqual(result.status_code, 400, result.body)
                self.assertEqual(result.body["data"]["code"], "validation_error")
                self.assertEqual(result.body["data"]["field"], "expected_revision")
            current = await self.api.get_task(task["id"])
            self.assertEqual(current.body["data"]["name"], "API任务")
        missing = await self.api.delete_task(task["id"], {})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.body["data"]["field"], "expected_revision")
        legal = await self.api.update_task(
            task["id"],
            {
                **self.payload,
                "name": "合法修改",
                "expected_revision": task["revision"],
            },
        )
        self.assertEqual(legal.status_code, 200)
        stale = await self.api.update_task(
            task["id"],
            {
                **self.payload,
                "name": "过期",
                "expected_revision": task["revision"],
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.body["data"]["code"], "revision_conflict")

    async def test_validation_and_not_found(self) -> None:
        for payload in (
            {**self.payload, "output_format": "pdf"},
            {**self.payload, "umos": ["12345"]},
            {**self.payload, "name": ""},
        ):
            result = await self.api.create_task(payload)
            self.assertEqual(result.status_code, 400)
            self.assertEqual(result.body["data"]["code"], "validation_error")
        missing = await self.api.get_task("missing")
        self.assertEqual(missing.status_code, 404)

    async def test_item_edits_are_task_private_and_visible(self) -> None:
        created = await self.api.create_task(self.payload)
        task = created.body["data"]
        other = await self.api.create_task({**self.payload, "name": "另一个任务"})
        other_task = other.body["data"]
        updated = await self.api.update_task(
            task["id"],
            {
                **self.payload,
                "items": [
                    {"content_type": "zhihu_hot", "limit": 3},
                    {"content_type": "douyin_hot"},
                ],
                "expected_revision": task["revision"],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.body)
        self.assertEqual(
            [entry["content_type"] for entry in updated.body["data"]["items"]],
            ["zhihu_hot", "douyin_hot"],
        )
        self.assertEqual(updated.body["data"]["items"][0]["limit"], 3)
        untouched = await self.api.get_task(other_task["id"])
        self.assertEqual(
            [entry["content_type"] for entry in untouched.body["data"]["items"]],
            ["daily_news"],
        )

    async def test_preview_needs_plan_preview_and_candidate_site(self) -> None:
        created = await self.api.create_task(self.payload)
        task = created.body["data"]
        unavailable = await self.api.preview_task(task["id"], {})
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.body["data"]["code"], "preview_unavailable")
        self.image_unavailable = await self.api.generate_task_image(task["id"], {})
        self.assertEqual(self.image_unavailable.status_code, 503)

        previews = _StubPlanPreview(
            sections=[{"content_type": "daily_news", "status": "ok", "items": ["a"]}],
        )
        self.api = TaskApi(self.tasks, self.sites, plan_preview=previews)
        # 站点停用：候选为空，不请求上游。
        disabled = await self.api.preview_task(task["id"], {})
        self.assertEqual(disabled.status_code, 502)
        self.assertEqual(disabled.body["data"]["code"], "content_unavailable")
        self.assertEqual(previews.calls, [])

        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name=self.site.name,
                base_url=self.site.base_url,
                enabled=True,
                timeout_seconds=self.site.timeout_seconds,
                expected_revision=self.site.revision,
            ),
        )
        ok = await self.api.preview_task(task["id"], {"force_refresh": True})
        self.assertEqual(ok.status_code, 200, ok.body)
        data = ok.body["data"]
        self.assertEqual(data["task_id"], task["id"])
        self.assertEqual(data["task_name"], "API任务")
        self.assertEqual(
            [entry["content_type"] for entry in data["items"]],
            ["daily_news"],
        )
        self.assertEqual(previews.calls, [(task["id"], self.site.id)])
        self.assertEqual(previews.payloads[0]["force_refresh"], True)

    async def test_saving_preview_and_capability_test_create_no_delivery(self) -> None:
        """保存、预览、能力测试都只读配置，不写入任何投递记录。"""
        created = await self.api.create_task(self.payload)
        self.assertEqual(created.status_code, 201, created.body)
        task = created.body["data"]
        updated = await self.api.update_task(
            task["id"],
            {
                **self.payload,
                "items": [item(), {"content_type": "zhihu_hot", "params": {}, "limit": 3}],
                "expected_revision": task["revision"],
            },
        )
        self.assertEqual(updated.status_code, 200, updated.body)

        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name=self.site.name,
                base_url=self.site.base_url,
                enabled=True,
                timeout_seconds=self.site.timeout_seconds,
                expected_revision=self.site.revision,
            ),
        )
        previews = _StubPlanPreview(
            sections=[
                {"content_type": "daily_news", "status": "ok", "items": ["a"]},
                {"content_type": "zhihu_hot", "status": "ok", "items": ["a", "b", "c"]},
            ],
        )
        self.api = TaskApi(
            self.tasks,
            self.sites,
            content=_StubContent(),
            plan_preview=previews,
        )
        preview = await self.api.preview_task(task["id"], {})
        self.assertEqual(preview.status_code, 200, preview.body)
        capability = await self.api.test_task(task["id"])
        self.assertEqual(capability.status_code, 200, capability.body)
        self.assertEqual(previews.calls[0][0], task["id"])

        with sqlite3.connect(self.db) as connection:
            executions = connection.execute(
                "SELECT COUNT(*) FROM delivery_executions",
            ).fetchone()[0]
            pages = connection.execute(
                "SELECT COUNT(*) FROM delivery_pages",
            ).fetchone()[0]
        self.assertEqual(executions, 0)
        self.assertEqual(pages, 0)


class _StubContent:
    async def preview_cached(self, content_type, site_id, *args, **kwargs):
        cached = type(
            "Cached",
            (),
            {"snapshot": {"items": [f"{content_type}-1"], "title": content_type}},
        )
        return cached()


class _StubPlanPreview:
    def __init__(self, *, sections) -> None:
        self.sections = sections
        self.calls: list[tuple[str, str]] = []
        self.payloads: list[dict] = []

    async def preview_site(self, payload, *, owner=None, site_id=None):
        self.payloads.append(dict(payload))
        self.calls.append((str(payload.get("task_id")), site_id))
        return PlanPreviewResult(
            200,
            {
                "status": "ok",
                "data": {
                    "task_id": payload.get("task_id"),
                    "sections": self.sections,
                    "outputs": {"text": "内容"},
                },
            },
        )
