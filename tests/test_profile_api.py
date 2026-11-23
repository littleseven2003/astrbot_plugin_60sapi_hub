"""任务内容方案预览（core/plan_preview.py）的后端专项测试。

内容组合已并入推送任务：每个任务自带栏目方案，预览按任务 id 取方案，
按预览时临时选定的站点抓取；本文件覆盖顺序与条数、缓存与强制刷新、
部分/全部失败、站点停用、多轮复核冲突，以及图片快照归属与过期判定。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from contextlib import closing
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview
from core.sites import SiteCreate, SiteService, SiteUpdate
from core.tasks import PushTaskService, TaskCreate, TaskUpdate


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall = datetime(2026, 10, 26, 5, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def now(self) -> datetime:
        return self.wall


class FakeImageService:
    """只返回预置栏目图，不执行真实渲染。"""

    def __init__(self, images=(), error: Exception | None = None) -> None:
        self.images = tuple(images)
        self.error = error
        self.calls = 0

    async def render(self, _data, *, created_at: str = ""):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.images


def profile_item(content_type, limit=None):
    return {"content_type": content_type, "params": {}, "limit": limit}


class PlanPreviewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "plugin.sqlite3"
        self.secret = "{{SECRET_cvmigipi2}}"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.site = await self.sites.create_site(
            SiteCreate(
                name="自建预览站点",
                base_url="http://example.test/proxy",
                timeout_seconds=5,
                source_kind="self_built",
                bearer_token=self.secret,
            ),
        )
        self.public_site = await self.sites.create_site(
            SiteCreate(
                name="公开实例站点",
                base_url="http://example.test/proxy",
                timeout_seconds=5,
                source_kind="public",
                bearer_token="public-token",
            ),
        )
        self.calls: list[str] = []
        self.mode = "normal"
        self.seen_auth: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request.url.path)
            self.seen_auth.append(request.headers.get("authorization", ""))
            if self.mode == "all_error":
                raise httpx.ConnectError("cannot connect")
            if self.mode == "empty":
                return httpx.Response(
                    200,
                    content=json.dumps(_empty_payload(request.url.path)).encode(),
                )
            if self.mode == "zhihu_error" and request.url.path.endswith(
                "/v2/zhihu",
            ):
                return httpx.Response(
                    500,
                    content=f"echo {self.secret}".encode(),
                )
            return httpx.Response(
                200,
                content=json.dumps(_payload(request.url.path)).encode(),
            )

        self.client = ContentHttpClient(transport=httpx.MockTransport(handler))
        self.content = ContentService(self.sites, client=self.client)
        await self.content.open()
        self.tasks = PushTaskService(self.sites.store)
        self.clock = FakeClock()
        self.snapshots = ImageSnapshotStore(
            ttl_seconds=10,
            max_snapshots=4,
            monotonic=self.clock.monotonic,
            wall_clock=self.clock.now,
        )
        self.image_service = FakeImageService()
        self.api = PlanPreview(
            self.tasks,
            self.content,
            self.sites,
            image_snapshots=self.snapshots,
            image_service=self.image_service,
        )

    async def asyncTearDown(self) -> None:
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def _task(self, items=None, *, sites=None, name="任务"):
        if items is None:
            items = [
                profile_item("daily_news", 2),
                profile_item("zhihu_hot", 1),
                profile_item("douyin_hot"),
            ]
        return await self.tasks.create_task(
            TaskCreate(
                name=name,
                items=items,
                output_format="text",
                umos=[],
                sites=list(sites if sites is not None else [self.site.id]),
            ),
        )

    async def _preview(self, task, *, site_id=None, **extra):
        payload = {"task_id": task.id, "site_id": site_id or self.site.id}
        payload.update(extra)
        return await self.api.preview_site(payload)

    async def test_preview_reports_task_plan_order_limits_and_cache(self) -> None:
        task = await self._task()
        snapshot = await self.tasks.get_config_snapshot(task.id)
        result = await self._preview(task)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["task_id"], task.id)
        self.assertEqual(data["task_name"], "任务")
        self.assertEqual(data["task_revision"], task.revision)
        self.assertEqual(data["plan_revision"], snapshot.plan_revision)
        self.assertEqual(
            data["items"],
            [
                {"content_type": "daily_news", "label": "每日新闻", "limit": 2},
                {"content_type": "zhihu_hot", "label": "知乎热榜", "limit": 1},
                {"content_type": "douyin_hot", "label": "抖音热搜", "limit": None},
            ],
        )
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["success_count"], 3)
        self.assertEqual(
            [section["content_type"] for section in data["sections"]],
            ["daily_news", "zhihu_hot", "douyin_hot"],
        )
        self.assertEqual(
            [section["displayed_count"] for section in data["sections"]],
            [2, 1, 3],
        )
        self.assertEqual(
            [section["total_item_count"] for section in data["sections"]],
            [3, 3, 3],
        )
        self.assertEqual(
            sorted(set(self.calls)),
            ["/proxy/v2/60s", "/proxy/v2/douyin", "/proxy/v2/zhihu"],
        )
        outputs = data["outputs"]
        self.assertEqual(set(outputs), {"text", "markdown"})
        # 预览标题必须用任务名：渲染器优先读 task_name，回落到旧键。
        self.assertIn("任务：任务", outputs["text"])
        self.assertTrue(outputs["markdown"].startswith("# 任务"))
        self.assertIn("【每日新闻】", outputs["text"])
        self.assertIn("展示条数：2 / 3（最多 2 条）", outputs["text"])
        self.assertIn("热度：1", outputs["text"])
        self.assertIn("## 知乎热榜", outputs["markdown"])
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(
            all(not section["cache"]["hit"] for section in data["sections"]),
        )

        cached = await self._preview(task)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(
            all(
                section["cache"]["hit"]
                for section in cached.body["data"]["sections"]
            ),
        )
        self.assertEqual(
            [
                section["total_item_count"]
                for section in cached.body["data"]["sections"]
            ],
            [3, 3, 3],
        )

        refreshed = await self._preview(task, force_refresh=True)
        self.assertFalse(
            any(
                section["cache"]["hit"]
                for section in refreshed.body["data"]["sections"]
            ),
        )
        self.assertEqual(len(self.calls), 6)

        # 任务条数截断不得改写单栏目的完整缓存。
        full = await self.content.preview_cached("daily_news", self.site.id)
        self.assertEqual(len(full.snapshot.items), 3)
        self.assertTrue(full.hit)

    async def test_legacy_profile_id_payload_still_resolves_the_task(self) -> None:
        task = await self._task(items=[profile_item("daily_news")])
        result = await self.api.preview_site(
            {"profile_id": task.id, "site_id": self.site.id},
        )
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["task_id"], task.id)
        self.assertEqual(result.body["data"]["plan_revision"], 1)

    async def test_sections_and_outputs_show_each_section_source_url(self) -> None:
        task = await self._task(sites=[self.public_site.id])
        result = await self._preview(task, site_id=self.public_site.id)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        sections = {item["content_type"]: item for item in data["sections"]}
        self.assertEqual(
            sections["daily_news"]["source_url"],
            "http://example.test/proxy/v2/60s",
        )
        self.assertEqual(
            sections["zhihu_hot"]["source_url"],
            "http://example.test/proxy/v2/zhihu",
        )
        self.assertEqual(
            sections["douyin_hot"]["source_url"],
            "http://example.test/proxy/v2/douyin",
        )
        self.assertEqual(sections["daily_news"]["source_kind"], "public")
        # 后台投影保留完整地址，对外来源展示只给域名。
        self.assertEqual(
            sections["daily_news"]["source_display"], "example.test",
        )
        text = data["outputs"]["text"]
        markdown = data["outputs"]["markdown"]
        self.assertIn("数据来源：example.test", text)
        self.assertIn("数据来源：example.test", markdown)
        self.assertNotIn("proxy/v2", text)
        self.assertNotIn("proxy/v2", markdown)
        # 凭据只用于请求头，不出现在来源地址或任何输出里。
        self.assertNotIn(self.secret, json.dumps(data, ensure_ascii=False))
        self.assertNotIn("public-token", json.dumps(data, ensure_ascii=False))

    async def test_self_built_sections_hide_address_in_outputs(self) -> None:
        task = await self._task()
        result = await self._preview(task)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["sections"][0]["source_kind"], "self_built")
        self.assertEqual(data["sections"][0]["source_display"], "自建服务")
        text = data["outputs"]["text"]
        markdown = data["outputs"]["markdown"]
        self.assertIn("数据来源：自建服务", text)
        self.assertIn("- 数据来源：自建服务", markdown)
        self.assertNotIn("example.test/proxy", text)
        self.assertNotIn("example.test/proxy", markdown)

    async def test_existing_outputs_keep_old_source_after_site_change(self) -> None:
        task = await self._task(
            items=[profile_item("daily_news")],
            sites=[self.public_site.id],
        )
        first = await self._preview(task, site_id=self.public_site.id)
        self.assertEqual(first.status_code, 200, first.body)
        data = first.body["data"]
        section = data["sections"][0]
        self.assertEqual(
            section["source_url"], "http://example.test/proxy/v2/60s",
        )
        text_before = data["outputs"]["text"]
        markdown_before = data["outputs"]["markdown"]

        updated = await self.sites.update_site(
            self.public_site.id,
            SiteUpdate(
                name="改名后的站点",
                base_url="http://new.example.test/other-prefix",
                timeout_seconds=5,
                expected_revision=self.public_site.revision,
            ),
        )
        self.assertEqual(updated.revision, self.public_site.revision + 1)
        self.assertEqual(updated.source_kind, "public")

        # 已生成的内容快照和文字/Markdown 保持原地址，不被新配置覆盖。
        self.assertEqual(section["source_url"], "http://example.test/proxy/v2/60s")
        self.assertEqual(data["outputs"]["text"], text_before)
        self.assertEqual(data["outputs"]["markdown"], markdown_before)
        self.assertIn("数据来源：example.test", text_before)
        self.assertNotIn("proxy/v2", text_before)
        self.assertNotIn("new.example.test", text_before)

        # 新配置下的新获取才使用新地址。
        refreshed = await self.content.preview_cached(
            "daily_news", self.public_site.id, force_refresh=True,
        )
        self.assertEqual(
            refreshed.snapshot.source_url,
            "http://new.example.test/other-prefix/v2/60s",
        )

    async def test_task_update_uses_new_order_and_site_revision(self) -> None:
        task = await self._task(
            items=[profile_item("daily_news", 1), profile_item("zhihu_hot", 1)],
        )
        await self._preview(task)
        self.assertEqual(len(self.calls), 2)
        updated = await self.tasks.update_task(
            task.id,
            TaskUpdate(
                name="新顺序",
                items=[
                    profile_item("zhihu_hot", 2),
                    profile_item("daily_news", 1),
                ],
                output_format="text",
                umos=[],
                expected_revision=task.revision,
            ),
        )
        self.assertEqual(
            [item.content_type for item in updated.items],
            ["zhihu_hot", "daily_news"],
        )
        second = await self._preview(task)
        self.assertEqual(
            [section["content_type"] for section in second.body["data"]["sections"]],
            ["zhihu_hot", "daily_news"],
        )
        self.assertEqual(
            [section["displayed_count"] for section in second.body["data"]["sections"]],
            [2, 1],
        )
        self.assertEqual(len(self.calls), 2)

        await self.sites.update_site(
            self.site.id,
            SiteUpdate(
                name="站点改名",
                base_url=self.site.base_url,
                timeout_seconds=self.site.timeout_seconds,
                expected_revision=self.site.revision,
            ),
        )
        revised = await self._preview(task)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(
            all(
                not section["cache"]["hit"]
                for section in revised.body["data"]["sections"]
            ),
        )

    async def test_partial_failure_and_all_failure(self) -> None:
        task = await self._task(
            items=[
                profile_item("daily_news"),
                profile_item("zhihu_hot"),
                profile_item("douyin_hot"),
            ],
        )
        self.mode = "zhihu_error"
        partial = await self._preview(task)
        self.assertEqual(partial.status_code, 200)
        data = partial.body["data"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["success_count"], 2)
        self.assertEqual(data["failure_count"], 1)
        failed = [section for section in data["sections"] if section["status"] == "error"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["content_type"], "zhihu_hot")
        self.assertEqual(failed[0]["code"], "upstream_http_error")
        self.assertEqual(
            data["failed_sections"],
            [
                {
                    "content_type": "zhihu_hot",
                    "label": "知乎热榜",
                    "message": data["failed_sections"][0]["message"],
                },
            ],
        )
        outputs = data["outputs"]
        self.assertIn("获取失败", outputs["text"])
        self.assertIn("知乎热榜", outputs["text"])
        self.assertIn("## 知乎热榜", outputs["markdown"])
        serialized = json.dumps(partial.body, ensure_ascii=False)
        self.assertNotIn(self.secret, serialized)
        self.assertNotIn(f"echo {self.secret}", serialized)
        self.assertEqual(self.seen_auth[0], f"Bearer {self.secret}")

        self.mode = "all_error"
        all_failed = await self._preview(task, force_refresh=True)
        self.assertEqual(all_failed.status_code, 502)
        self.assertEqual(all_failed.body["status"], "error")
        self.assertEqual(
            all_failed.body["data"]["code"], "all_sections_failed",
        )
        self.assertEqual(all_failed.body["data"]["status"], "failed")
        self.assertEqual(all_failed.body["data"]["success_count"], 0)
        self.assertEqual(len(all_failed.body["data"]["sections"]), 3)
        self.assertTrue(
            all(
                section["status"] == "error"
                for section in all_failed.body["data"]["sections"]
            ),
        )
        self.assertNotIn("outputs", all_failed.body["data"])
        self.assertNotIn("image_snapshot", all_failed.body["data"])
        self.assertIn("所有栏目获取失败", all_failed.body["message"])

    async def test_all_five_types_and_legitimate_empty_section(self) -> None:
        task = await self._task(
            items=[
                profile_item("daily_news", 1),
                profile_item("bilibili_hot", 1),
                profile_item("zhihu_hot", 1),
                profile_item("douyin_hot", 1),
                profile_item("toutiao_hot", 1),
            ],
        )
        result = await self._preview(task)
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["status"], "complete")
        self.assertEqual(
            [section["content_type"] for section in result.body["data"]["sections"]],
            [
                "daily_news",
                "bilibili_hot",
                "zhihu_hot",
                "douyin_hot",
                "toutiao_hot",
            ],
        )
        self.mode = "empty"
        empty = await self._preview(task, force_refresh=True)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.body["data"]["status"], "complete")
        self.assertTrue(
            all(
                section["displayed_count"] == 0
                for section in empty.body["data"]["sections"]
            ),
        )
        self.assertIn("（空列表，0 条）", empty.body["data"]["outputs"]["text"])

    async def test_legacy_disabled_site_is_not_rejected(self) -> None:
        # 站点级启用已移除：旧库 enabled=0 只供追溯，不再阻止预览取数。
        task = await self._task()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                "UPDATE sites SET enabled = 0 WHERE id = ?", (self.site.id,),
            )
        result = await self._preview(task)
        self.assertEqual(result.status_code, 200, result.body)
        self.assertTrue(self.calls)

    async def test_invalid_force_refresh_does_not_fetch(self) -> None:
        task = await self._task()
        for value in ("true", 1, 0, None, []):
            with self.subTest(value=value):
                result = await self._preview(task, force_refresh=value)
                self.assertEqual(result.status_code, 400)
                self.assertEqual(
                    result.body["data"]["code"],
                    "invalid_force_refresh",
                )
        self.assertEqual(self.calls, [])

    async def test_plan_change_during_preview_cancels_the_response(self) -> None:
        task = await self._task(items=[profile_item("daily_news", 1)])
        original = self.content.preview_cached
        revisions: list[int] = []

        async def hooked(content_type, site_id, *, force_refresh=False, use_cache=True):
            current = await self.tasks.get_task(task.id)
            alternative = (
                profile_item("bilibili_hot", 1)
                if current.items[0].content_type != "bilibili_hot"
                else profile_item("toutiao_hot", 1)
            )
            await self.tasks.update_task(
                task.id,
                TaskUpdate(
                    name=current.name,
                    items=[alternative],
                    output_format=current.output_format,
                    umos=list(current.umos),
                    expected_revision=current.revision,
                ),
            )
            revisions.append(
                (await self.tasks.get_config_snapshot(task.id)).plan_revision,
            )
            return await original(
                content_type, site_id,
                force_refresh=force_refresh, use_cache=use_cache,
            )

        self.content.preview_cached = hooked
        try:
            result = await self._preview(task)
        finally:
            self.content.preview_cached = original
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(
            result.body["data"]["code"], "preview_revision_changed",
        )
        # 两轮复核都拿到新方案，才允许判定为冲突。
        self.assertEqual(len(revisions), 2)
        self.assertEqual(len(set(revisions)), 2)

    async def test_generate_image_rejects_snapshot_of_another_task(self) -> None:
        first = await self._task(items=[profile_item("daily_news", 1)], name="任务一")
        second = await self._task(items=[profile_item("daily_news", 2)], name="任务二")
        preview = await self.api.preview_site(
            {"task_id": first.id, "site_id": self.site.id}, owner="alice",
        )
        self.assertEqual(preview.status_code, 200, preview.body)
        snapshot_id = preview.body["data"]["image_snapshot"]["id"]

        await self.api.generate_image(
            first.id, {"snapshot_id": snapshot_id}, owner="alice",
        )
        foreign = await self.api.generate_image(
            second.id, {"snapshot_id": snapshot_id}, owner="alice",
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.body["data"]["code"], "snapshot_not_found")
        self.assertEqual(foreign.body["message"], "图片快照不属于该任务。")
        self.assertEqual(self.image_service.calls, 1)

    async def test_generate_image_rejects_stale_plan_revision(self) -> None:
        task = await self._task(items=[profile_item("daily_news", 1)])
        preview = await self.api.preview_site(
            {"task_id": task.id, "site_id": self.site.id}, owner="alice",
        )
        self.assertEqual(preview.status_code, 200, preview.body)
        snapshot_id = preview.body["data"]["image_snapshot"]["id"]

        current = await self.tasks.get_task(task.id)
        await self.tasks.update_task(
            task.id,
            TaskUpdate(
                name=current.name,
                items=[profile_item("zhihu_hot", 2)],
                output_format=current.output_format,
                umos=list(current.umos),
                expected_revision=current.revision,
            ),
        )
        stale = await self.api.generate_image(
            task.id, {"snapshot_id": snapshot_id}, owner="alice",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.body["data"]["code"], "snapshot_stale")
        self.assertEqual(self.image_service.calls, 0)


def _empty_payload(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {"code": 200, "data": {"date": "2026-10-26", "news": []}}
    return {"code": 200, "data": []}


def _payload(path: str) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200,
            "data": {"date": "2026-10-26", "news": ["一", "二", "三"]},
        }
    if path.endswith("/v2/zhihu"):
        return {
            "code": 200,
            "data": [
                {"title": "知乎一", "hot_value_desc": "10 万热度"},
                {"title": "知乎二", "hot_value_desc": "9 万热度"},
                {"title": "知乎三", "hot_value_desc": "8 万热度"},
            ],
        }
    if path.endswith("/v2/douyin"):
        return {
            "code": 200,
            "data": [
                {"title": "抖音一", "hot_value": 1},
                {"title": "抖音二", "hot_value": 2},
                {"title": "抖音三", "hot_value": 3},
            ],
        }
    if path.endswith("/v2/bili"):
        return {
            "code": 200,
            "data": [
                {"title": "B站一", "link": "https://www.bilibili.com/video/1"},
                {"title": "B站二"},
                {"title": "B站三"},
            ],
        }
    if path.endswith("/v2/toutiao"):
        return {
            "code": 200,
            "data": [
                {"title": "头条一", "hot_value": 11},
                {"title": "头条二", "hot_value": 12},
                {"title": "头条三", "hot_value": 13},
            ],
        }
    return {"code": 500, "data": None}


if __name__ == "__main__":
    unittest.main()
