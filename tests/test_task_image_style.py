"""任务级图片样式：默认值、严格校验、缺省保留、迁移与预览返回。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from core.image_styles import DEFAULT_STYLE_ID, PRESETS
from core.sites import SiteCreate, SiteService
from core.task_api import TaskApi
from core.tasks import PushTaskService


def item():
    return {"content_type": "daily_news", "params": {}, "limit": None}


class TaskImageStyleApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "plugin.sqlite3"
        self.sites = SiteService(self.db)
        await self.sites.open()
        self.tasks = PushTaskService(self.sites.store)
        self.site = await self.sites.create_site(
            SiteCreate(
                name="样式测试站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=5,
            ),
        )
        self.api = TaskApi(self.tasks, self.sites)
        self.payload = {
            "name": "样式任务",
            "items": [item()],
            "sites": [self.site.id],
            "output_format": "image",
            "umos": [],
        }

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self.tmp.cleanup()

    async def test_create_defaults_to_default_style(self) -> None:
        created = await self.api.create_task(self.payload)
        self.assertEqual(created.status_code, 201, created.body)
        self.assertEqual(
            created.body["data"]["image_style"], DEFAULT_STYLE_ID,
        )

    async def test_list_and_detail_expose_registry_and_effective_style(self) -> None:
        created = await self.api.create_task({**self.payload, "image_style": "mist"})
        task = created.body["data"]
        self.assertEqual(task["image_style"], "mist")
        listed = await self.api.list_tasks()
        data = listed.body["data"]
        self.assertEqual(
            [style["id"] for style in data["image_styles"]],
            ["default", "mist", "floral", "bold_studio"],
        )
        self.assertEqual(data["default_image_style"], "default")
        got = await self.api.get_task(task["id"])
        self.assertEqual(got.body["data"]["image_style"], "mist")

    async def test_omitted_style_is_preserved_and_explicit_value_changes(self) -> None:
        created = await self.api.create_task({**self.payload, "image_style": "floral"})
        task = created.body["data"]
        # 旧客户端更新时省略字段：保留原样式。
        updated = await self.api.update_task(
            task["id"],
            {**self.payload, "name": "改名", "expected_revision": task["revision"]},
        )
        self.assertEqual(updated.status_code, 200, updated.body)
        self.assertEqual(updated.body["data"]["image_style"], "floral")
        # 显式 reset 为默认款。
        reset = await self.api.update_task(
            task["id"],
            {
                **self.payload,
                "name": "改名",
                "image_style": "default",
                "expected_revision": updated.body["data"]["revision"],
            },
        )
        self.assertEqual(reset.status_code, 200, reset.body)
        self.assertEqual(reset.body["data"]["image_style"], "default")

    async def test_invalid_null_and_unknown_styles_are_field_errors(self) -> None:
        created = await self.api.create_task(self.payload)
        task = created.body["data"]
        for bad in (None, "unknown", 3, "", ["mist"]):
            result = await self.api.create_task({**self.payload, "image_style": bad})
            self.assertEqual(result.status_code, 400, (bad, result.body))
            self.assertEqual(
                result.body["data"]["field"], "image_style", result.body,
            )
            updated = await self.api.update_task(
                task["id"],
                {
                    **self.payload,
                    "image_style": bad,
                    "expected_revision": task["revision"],
                },
            )
            self.assertEqual(updated.status_code, 400, (bad, updated.body))
        # 失败请求不得写入或推进 revision。
        current = await self.api.get_task(task["id"])
        self.assertEqual(current.body["data"]["revision"], task["revision"])

    async def test_preview_reports_the_saved_style(self) -> None:
        created = await self.api.create_task({**self.payload, "image_style": "bold_studio"})
        task = created.body["data"]
        # 预览需要注入一个可用的预览服务；本测试只关心样式字段透传。
        api = TaskApi(self.tasks, self.sites, plan_preview=object())

        async def fake_preview(*args, **kwargs):
            return (
                self.site.id,
                {
                    "task_id": task["id"],
                    "task_name": task["name"],
                    "status": "complete",
                    "sections": [],
                    "success_count": 1,
                    "failure_count": 0,
                },
            )

        with patch("core.task_api.first_satisfying_preview", fake_preview):
            result = await api.preview_task(task["id"], {})
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["image_style"], "bold_studio")
        self.assertEqual(
            [style["id"] for style in result.body["data"]["image_styles"]],
            ["default", "mist", "floral", "bold_studio"],
        )

    async def test_old_database_gains_style_column_and_defaults(self) -> None:
        created = await self.api.create_task(self.payload)
        task_id = created.body["data"]["id"]
        await self.sites.close()
        # 模拟 v16 旧库：删掉新列并把版本改回 16，任务数据保留。
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("ALTER TABLE push_tasks DROP COLUMN image_style")
            connection.execute("PRAGMA user_version = 16")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 16)

        reopened = SiteService(self.db)
        await reopened.open()
        try:
            task = await PushTaskService(reopened.store).get_task(task_id)
            self.assertEqual(task.image_style, DEFAULT_STYLE_ID)
            with closing(sqlite3.connect(self.db)) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1] for row in
                    connection.execute("PRAGMA table_info(push_tasks)")
                }
            self.assertEqual(version, 17)
            self.assertIn("image_style", columns)
        finally:
            await reopened.close()

    def test_every_registry_style_is_a_frozen_preset(self) -> None:
        self.assertEqual(set(PRESETS), {"default", "mist", "floral", "bold_studio"})


if __name__ == "__main__":
    unittest.main()
