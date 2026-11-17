"""聊天查询按任务候选顺序取第一份可用内容，失败才换下一个站点。"""

from __future__ import annotations

import base64
import unittest
from types import SimpleNamespace

from core.chat_commands import ChatCommandService
from core.chat_content import (
    CHANGED_TEXT,
    CONFIG_TEXT,
    FAILED_TEXT,
    ChatContentQuery,
)
from core.chat_image import IMAGE_FAILED_TEXT, ChatImageQuery
from core.content_plan import PlanItem

UMO = "bot:GroupMessage:g"
PNG_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00").decode()


def _snapshot(task, candidates):
    return SimpleNamespace(
        task=task,
        profile_id=task.id,
        plan_revision=3,
        candidates=tuple(
            SimpleNamespace(id=site_id, revision=revision, enabled=enabled)
            for site_id, revision, enabled in candidates
        ),
    )


class _Tasks:
    """可变配置快照：候选顺序、启用状态与站点版本都可在查询中途变化。"""

    def __init__(self, candidates, revision: int = 2) -> None:
        self.task = SimpleNamespace(
            id="task-1",
            name="任务",
            items=(PlanItem("daily_news", None),),
            output_format="text",
            umos=(UMO,),
            sites=tuple(item[0] for item in candidates),
            revision=revision,
            created_at="2026-01-01T00:00:00Z",
        )
        self.candidates = list(candidates)

    async def get_config_snapshot(self, task_id):
        from core.tasks import TaskNotFoundError

        if task_id != self.task.id:
            raise TaskNotFoundError(task_id)
        return _snapshot(self.task, self.candidates)


class _TextPreviews:
    def __init__(self, failing=(), on_call=None) -> None:
        self.failing = set(failing)
        self.on_call = on_call
        self.calls: list[str | None] = []

    async def preview_site(self, payload, *, owner=None, site_id=None):
        # 聊天查询按约定把本次站点放进载荷，而不是单独的关键字参数。
        requested = payload.get("site_id") if isinstance(payload, dict) else None
        self.calls.append(requested)
        if self.on_call is not None:
            self.on_call(requested)
        if requested in self.failing:
            return SimpleNamespace(
                status_code=502, body={"data": {"status": "failed"}},
            )
        return SimpleNamespace(
            status_code=200,
            body={"data": {"outputs": {"text": f"来自 {requested} 的内容"}}},
        )


class _ImagePreviews:
    def __init__(self, snapshots: dict[str, str], images: dict[str, list]) -> None:
        self.snapshots = snapshots
        self.images = images
        self.preview_calls: list[str | None] = []
        self.generate_calls: list[str] = []
        self.generate_plan_ids: list[str] = []

    async def preview_site(self, payload, *, owner=None, site_id=None):
        requested = payload.get("site_id") if isinstance(payload, dict) else None
        self.preview_calls.append(requested)
        snapshot_id = self.snapshots.get(requested)
        data = {} if snapshot_id is None else {"image_snapshot": {"id": snapshot_id}}
        return SimpleNamespace(status_code=200, body={"data": data})

    async def generate_image(self, task_id, payload, *, owner=None, site_id=None):
        snapshot_id = payload.get("snapshot_id")
        self.generate_calls.append(snapshot_id)
        self.generate_plan_ids.append(task_id)
        return SimpleNamespace(
            status_code=200,
            body={"data": {"images": self.images.get(snapshot_id, [])}},
        )


def _page(index: int) -> dict:
    return {
        "index": index,
        "content_type": "daily_news",
        "label": "每日新闻",
        "status": "success",
        "width": 900,
        "height": 1200,
        "png_base64": PNG_BASE64,
    }


class ChatCandidateFailoverTest(unittest.IsolatedAsyncioTestCase):
    """任务候选就是尝试顺序：第一个可用即用，全部不可用才失败。"""

    def build_text(self, candidates, failing=(), on_call=None):
        tasks = _Tasks(candidates)
        previews = _TextPreviews(failing, on_call)
        service = ChatCommandService(
            tasks, tasks,
            content_query=ChatContentQuery(tasks, previews),
        )
        return service, tasks, previews

    async def test_first_available_candidate_in_order(self) -> None:
        service, _tasks, previews = self.build_text(
            (("s1", 4, True), ("s2", 5, True)), failing={"s1"},
        )
        reply = await service.handle(UMO, "/60s get task-1")
        self.assertIn("来自 s2 的内容", reply)
        self.assertNotIn("s1", reply)
        self.assertEqual(previews.calls, ["s1", "s2"])

    async def test_disabled_and_deleted_candidates_are_skipped(self) -> None:
        service, _tasks, previews = self.build_text(
            (("s1", 4, False), ("s2", None, True), ("s3", 6, True)),
        )
        reply = await service.handle(UMO, "/60s get task-1")
        self.assertIn("来自 s3 的内容", reply)
        self.assertEqual(previews.calls, ["s3"])

    async def test_no_usable_candidate_reports_config_unavailable(self) -> None:
        service, _tasks, previews = self.build_text(
            (("s1", 4, False), ("s2", None, True)),
        )
        self.assertEqual(
            await service.handle(UMO, "/60s get task-1"),
            CONFIG_TEXT,
        )
        self.assertEqual(previews.calls, [])

    async def test_all_candidates_failing_returns_failed_text(self) -> None:
        service, _tasks, previews = self.build_text(
            (("s1", 4, True), ("s2", 5, True)), failing={"s1", "s2"},
        )
        self.assertEqual(
            await service.handle(UMO, "/60s get task-1"),
            FAILED_TEXT,
        )
        self.assertEqual(previews.calls, ["s1", "s2"])

    async def test_candidate_order_change_suppresses_content(self) -> None:
        def reorder(site_id) -> None:
            if site_id == "s1":
                tasks.candidates = [("s2", 5, True), ("s1", 4, True)]

        tasks = _Tasks((("s1", 4, True), ("s2", 5, True)))
        previews = _TextPreviews(failing={"s1"}, on_call=reorder)
        service = ChatCommandService(
            tasks, tasks,
            content_query=ChatContentQuery(tasks, previews),
        )
        self.assertEqual(
            await service.handle(UMO, "/60s get task-1"),
            CHANGED_TEXT,
        )

    async def test_image_uses_first_candidate_that_renders(self) -> None:
        tasks = _Tasks((("s1", 4, True), ("s2", 5, True)))
        previews = _ImagePreviews(
            {"s1": None, "s2": "snap-2"},
            {"snap-2": [_page(1)]},
        )
        service = ChatCommandService(
            tasks, tasks,
            image_query=ChatImageQuery(tasks, previews),
        )
        reply = await service.handle(UMO, "/60s image task-1")
        self.assertEqual(reply.images, (b"\x89PNG\r\n\x1a\n\x00",))
        self.assertIn("共 1 张栏目长图", reply.text)
        self.assertEqual(previews.preview_calls, ["s1", "s2"])
        # 只有第二份快照被渲染，没有拿第一个站点的空结果冒充图片。
        self.assertEqual(previews.generate_calls, ["snap-2"])
        # 图片 rerender 用任务 id（组合已并入任务）。
        self.assertEqual(previews.generate_plan_ids, ["task-1"])

    async def test_image_all_candidates_empty_returns_failed_text(self) -> None:
        tasks = _Tasks((("s1", 4, True), ("s2", 5, True)))
        empty = [{"index": 1, "content_type": "daily_news", "label": "每日新闻",
                  "status": "failed"}]
        previews = _ImagePreviews(
            {"s1": "snap-1", "s2": "snap-2"},
            {"snap-1": empty, "snap-2": empty},
        )
        service = ChatCommandService(
            tasks, tasks,
            image_query=ChatImageQuery(tasks, previews),
        )
        self.assertEqual(
            await service.handle(UMO, "/60s image task-1"),
            IMAGE_FAILED_TEXT,
        )
        self.assertEqual(previews.preview_calls, ["s1", "s2"])
