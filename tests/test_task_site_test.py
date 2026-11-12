from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.profiles import ContentProfileService, ProfileCreate
from core.site_requirements import Requirement, evaluate_sections
from core.sites import SiteCreate, SiteService
from core.task_api import TaskApi
from core.tasks import PushTaskService, TaskCreate

SECRET = "simulated-secret-abc123"


def payload(path: str, news: int, hot: int) -> dict:
    if path.endswith("/v2/60s"):
        return {
            "code": 200, "message": "ok",
            "data": {"date": "2026-11-20", "news": [f"新闻{i}" for i in range(news)]},
        }
    return {
        "code": 200, "message": "ok",
        "data": [{"title": f"热榜{i}", "hot_value": i} for i in range(hot)],
    }


class RequirementTest(unittest.TestCase):
    def test_empty_list_is_insufficient_when_positive_required(self) -> None:
        sections = [{"content_type": "daily_news", "status": "ok", "items": []}]
        ok, shortfalls = evaluate_sections(
            sections, [Requirement("daily_news", 1)],
        )
        self.assertFalse(ok)
        self.assertEqual(shortfalls[0].reason, "insufficient")

    def test_empty_list_is_acceptable_when_no_limit(self) -> None:
        sections = [{"content_type": "daily_news", "status": "ok", "items": []}]
        ok, _ = evaluate_sections(
            sections, [Requirement("daily_news", None)],
        )
        self.assertTrue(ok)

    def test_failed_section_never_satisfies(self) -> None:
        sections = [{"content_type": "zhihu_hot", "status": "error", "items": []}]
        ok, shortfalls = evaluate_sections(
            sections, [Requirement("zhihu_hot", 1)],
        )
        self.assertFalse(ok)
        self.assertEqual(shortfalls[0].reason, "unavailable")

    def test_all_requirements_must_hold_together(self) -> None:
        sections = [
            {"content_type": "daily_news", "status": "ok", "items": [1, 2]},
            {"content_type": "zhihu_hot", "status": "ok", "items": [1]},
        ]
        ok, _ = evaluate_sections(
            sections,
            [Requirement("daily_news", 2), Requirement("zhihu_hot", 2)],
        )
        self.assertFalse(ok)


class TaskSiteTestApiTest(unittest.IsolatedAsyncioTestCase):
    """任务测试：单个站点必须同时满足全部类别与条数。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_test.sqlite3"
        self.news = {"a.example.test": 2, "b.example.test": 5}
        self.hot = {"a.example.test": 1, "b.example.test": 3}

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            news = self.news.get(host, 0)
            hot = self.hot.get(host, 0)
            return httpx.Response(
                200, content=json.dumps(payload(request.url.path, news, hot)).encode(),
            )

        self.sites = SiteService(self.path)
        await self.sites.open()
        self.content = ContentService(
            self.sites,
            client=ContentHttpClient(transport=httpx.MockTransport(handler)),
        )
        await self.content.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.api = TaskApi(
            self.tasks, self.profiles, self.sites, content=self.content,
        )

    async def asyncTearDown(self) -> None:
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _site(self, name: str):
        return await self.sites.create_site(
            SiteCreate(
                name=name,
                base_url=f"https://{name}/{SECRET}",
                enabled=True,
                timeout_seconds=5,
                source_kind="public",
            ),
        )

    async def _task(self, site_ids):
        first = site_ids[0]
        profile = await self.profiles.create_profile(
            ProfileCreate(
                name="组合",
                site_id=first,
                items=[
                    {"content_type": "daily_news", "params": {}, "limit": 3},
                    {"content_type": "zhihu_hot", "params": {}, "limit": 2},
                ],
            ),
        )
        return await self.tasks.create_task(
            TaskCreate(
                name="任务",
                profile_id=profile.id,
                output_format="text",
                umos=["aiocqhttp:GroupMessage:g:1"],
                sites=list(site_ids),
            ),
        )

    async def test_first_satisfying_site_wins(self) -> None:
        first = await self._site("a.example.test")
        second = await self._site("b.example.test")
        task = await self._task([first.id, second.id])
        result = await self.api.test_task(task.id)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertTrue(data["satisfied"])
        self.assertEqual(data["site_id"], second.id)
        self.assertEqual(len(data["checked"]), 2)
        self.assertFalse(data["checked"][0]["satisfied"])
        self.assertEqual(
            data["checked"][0]["missing"][0]["content_type"], "daily_news",
        )

    async def test_no_site_satisfies_all_requirements(self) -> None:
        first = await self._site("a.example.test")
        task = await self._task([first.id])
        result = await self.api.test_task(task.id)
        data = result.body["data"]
        self.assertFalse(data["satisfied"])
        self.assertIsNone(data["site_id"])
        self.assertIn("无法满足", data["message"])

    async def test_disabled_site_is_skipped_with_reason(self) -> None:
        first = await self._site("a.example.test")
        second = await self._site("b.example.test")
        task = await self._task([first.id, second.id])
        await self.sites.update_site(
            first.id,
            __import__("core.sites", fromlist=["SiteUpdate"]).SiteUpdate(
                name=first.name,
                base_url=first.base_url,
                enabled=False,
                timeout_seconds=5,
                source_kind="public",
                expected_revision=first.revision,
            ),
        )
        result = await self.api.test_task(task.id)
        data = result.body["data"]
        self.assertTrue(data["satisfied"])
        self.assertEqual(data["checked"][0]["reason"], "site_disabled")
        self.assertEqual(data["site_id"], second.id)


    async def test_result_reports_freshness_and_cache(self) -> None:
        site = await self._site("a.example.test")
        task = await self._task([site.id])
        result = await self.api.test_task(task.id)
        data = result.body["data"]
        self.assertIn("checked_at", data)
        self.assertEqual(data["cache"]["used"], True)
        self.assertIn("ttl_seconds", data["cache"])
        self.assertFalse(data["truncated"])

    async def test_config_change_during_test_is_reported(self) -> None:
        first = await self._site("a.example.test")
        second = await self._site("b.example.test")
        task = await self._task([first.id, second.id])
        original = self.content.preview_cached
        state = {"done": False}

        async def flaky(content_type, site_id, **kwargs):
            result = await original(content_type, site_id, **kwargs)
            if not state["done"]:
                state["done"] = True
                current = await self.tasks.get_task(task.id)
                await self.tasks.update_task(
                    task.id,
                    __import__("core.tasks", fromlist=["TaskUpdate"]).TaskUpdate(
                        name="改名",
                        profile_id=current.profile_id,
                        output_format=current.output_format,
                        umos=list(current.umos),
                        expected_revision=current.revision,
                    ),
                )
            return result

        self.content.preview_cached = flaky  # type: ignore[assignment]
        result = await self.api.test_task(task.id)
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(result.body["data"]["code"], "config_changed")


if __name__ == "__main__":
    unittest.main()
