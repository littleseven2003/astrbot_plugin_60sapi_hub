"""复核退回项回归：缺栏提示、部分栏目保留、来源版本与候选上限口径。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx

from core.content_client import ContentHttpClient
from core.content_plan import PlanItem
from core.content_service import ContentService
from core.delivery import (
    DeliveryConfirmationStaleError,
    DeliveryLimits,
    DeliveryMessage,
    DeliverySendResult,
    DeliveryService,
    DeliveryStorageError,
    SendStatus,
    _Prepared,
)
from core.delivery_api import DeliveryApi
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview, PlanPreviewResult
from core.sites import (
    SiteCreate,
    SiteService,
    SiteStorageError,
    SiteUpdate,
)
from core.task_api import TaskApi
from core.tasks import (
    PushTaskService,
    TaskCreate,
    TaskUpdate,
    TaskValidationError,
)

UMO = "aiocqhttp:GroupMessage:g:1"


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


class ReviewFixTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "review.sqlite3"
        self.news = {"a.example.test": 2, "b.example.test": 0}
        self.hot = {"a.example.test": 3, "b.example.test": 3}
        self.hot_delay: dict[str, float] = {}
        self.on_request = None
        self.hosts: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            if self.on_request is not None:
                await self.on_request(request)
            if request.url.path.endswith("/v2/60s"):
                items = [
                    f"{host}新闻{index}"
                    for index in range(self.news.get(host, 0))
                ]
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-03-02", "news": items},
                }).encode())
            delay = self.hot_delay.get(host, 0.0)
            if delay:
                await asyncio.sleep(delay)
            items = [
                {"title": f"{host}热榜{index}", "hot_value": index}
                for index in range(self.hot.get(host, 0))
            ]
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok", "data": items,
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
        self.api = TaskApi(
            self.tasks, self.sites, content=self.content,
            plan_preview=self.preview,
        )
        self.a = await self.sites.create_site(SiteCreate(
            name="站点A", base_url="https://a.example.test/proxy",
            timeout_seconds=5, source_kind="self_built",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="站点B", base_url="https://b.example.test/proxy",
            timeout_seconds=5, source_kind="self_built",
        ))

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _task(
        self,
        *,
        news_limit: int = 2,
        hot_limit: int = 3,
        sites: tuple[str, ...] | None = None,
    ):
        return await self.tasks.create_task(TaskCreate(
            name="复核任务",
            items=[
                {"content_type": "daily_news", "params": {}, "limit": news_limit},
                {"content_type": "zhihu_hot", "params": {}, "limit": hot_limit},
            ],
            sites=list(sites or (self.a.id, self.b.id)),
            output_format="text",
            umos=[UMO],
        ))

    async def test_missing_required_items_sends_only_alert(self) -> None:
        # 两个站点各自最多 2 条，但任务要求 3 条：没有任何单站或拼合
        # 能满足整份内容，只能发一条异常提示并记采集失败。
        self.news = {"a.example.test": 2, "b.example.test": 2}
        self.hot = {"a.example.test": 3, "b.example.test": 3}
        task = await self._task(news_limit=3, hot_limit=3)
        result = await self.delivery.execute(task.id, "missing-items")
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        self.assertEqual(message.kind, "text")
        self.assertIn("60sAPI推送服务异常", message.text or "")
        self.assertNotIn("新闻", message.text or "")
        self.assertEqual(result.content_status, "failed")
        self.assertEqual(
            [target.status for target in result.targets], ["success"],
        )

    async def test_site_budget_timeout_keeps_finished_sections(self) -> None:
        # A 的新闻已取到，A 的热榜超出本次预览预算；B 的热榜可取。
        # 站级预算不能把 A 已成功的新闻一起丢掉：最终应按栏拼合成功。
        self.hot_delay["a.example.test"] = 2.0
        task = await self._task(news_limit=1, hot_limit=2)
        deadline = asyncio.get_running_loop().time() + 1.0
        result = await self.preview.preview_assembled(
            {"task_id": task.id},
            site_ids=[self.a.id, self.b.id],
            owner="budget",
            deadline=deadline,
        )
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertEqual(data["source_mode"], "multi")
        self.assertEqual(data["status"], "complete")
        self.assertEqual(
            [(item["content_type"], item["site_id"])
             for item in data["assignments"]],
            [("daily_news", self.a.id), ("zhihu_hot", self.b.id)],
        )
        self.assertIn("a.example.test新闻0", data["outputs"]["text"])
        self.assertIn("b.example.test热榜0", data["outputs"]["text"])

    async def test_preview_conflict_never_calls_sender(self) -> None:
        class ConflictPreview:
            async def preview_assembled(self, payload, **kwargs):
                return PlanPreviewResult(409, {
                    "status": "error",
                    "message": "站点配置在获取期间发生变化。",
                    "data": {"code": "preview_revision_changed"},
                })

        delivery = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=ConflictPreview(),
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(),
        )
        try:
            task = await self._task()
            result = await delivery.execute(task.id, "preview-conflict")
        finally:
            await delivery.close()
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "config_changed")
        self.assertIsNone(result.content_status)

    async def test_preview_rechecks_source_changed_during_assembly(self) -> None:
        state = {"done": False}

        async def change_a_during_b(request: httpx.Request) -> None:
            if request.url.host != "b.example.test" or state["done"]:
                return
            state["done"] = True
            current = await self.sites.get_site(self.a.id)
            await self.sites.update_site(self.a.id, SiteUpdate(
                name=current.name,
                base_url=current.base_url,
                timeout_seconds=current.timeout_seconds,
                expected_revision=current.revision,
            ))

        self.on_request = change_a_during_b
        self.hot["a.example.test"] = 1
        task = await self._task()
        result = await self.preview.preview_assembled(
            {"task_id": task.id},
            site_ids=[self.a.id, self.b.id],
            owner="changed",
        )
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(
            result.body["data"]["code"], "preview_revision_changed",
        )

    async def test_draft_preview_rechecks_source_changed_during_assembly(
        self,
    ) -> None:
        state = {"done": False}

        async def change_a_during_b(request: httpx.Request) -> None:
            if request.url.host != "b.example.test" or state["done"]:
                return
            state["done"] = True
            current = await self.sites.get_site(self.a.id)
            await self.sites.update_site(self.a.id, SiteUpdate(
                name=current.name,
                base_url=current.base_url,
                timeout_seconds=current.timeout_seconds,
                expected_revision=current.revision,
            ))

        self.on_request = change_a_during_b
        self.hot["a.example.test"] = 1
        result = await self.preview.preview_draft_assembled(
            (PlanItem("daily_news", 1), PlanItem("zhihu_hot", 2)),
            name="草稿",
            image_style="default",
            site_ids=[self.a.id, self.b.id],
            owner="draft-changed",
        )
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(
            result.body["data"]["code"], "preview_revision_changed",
        )

    async def test_delivery_refuses_when_source_changes_after_prepare(
        self,
    ) -> None:
        state = {"done": False}
        original = self.preview.preview_assembled

        async def change_after_preview(*args, **kwargs):
            response = await original(*args, **kwargs)
            if response.status_code == 200 and not state["done"]:
                state["done"] = True
                current = await self.sites.get_site(self.a.id)
                await self.sites.update_site(self.a.id, SiteUpdate(
                    name=current.name,
                    base_url=current.base_url,
                    timeout_seconds=current.timeout_seconds,
                    expected_revision=current.revision,
                ))
            return response

        self.preview.preview_assembled = change_after_preview  # type: ignore[method-assign]
        self.hot["a.example.test"] = 1
        task = await self._task()
        result = await self.delivery.execute(task.id, "source-changed")
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "config_changed")

    async def test_execution_details_record_each_column_source(self) -> None:
        self.hot["a.example.test"] = 1
        task = await self._task(news_limit=1, hot_limit=2)
        record = await self.delivery.execute(task.id, "sources-1")
        details = await self.delivery_api.get_execution(record.id)
        self.assertEqual(details.status_code, 200, details.body)
        data = details.body["data"]
        self.assertEqual(data["source_mode"], "multi")
        self.assertEqual(
            [(entry["content_type"], entry["site_id"])
             for entry in data["content_sources"]],
            [("daily_news", self.a.id), ("zhihu_hot", self.b.id)],
        )
        for entry in data["content_sources"]:
            self.assertEqual(entry["revision"], 1)
            self.assertTrue(entry["label"])

    async def test_capability_scans_legacy_six_candidates(self) -> None:
        extra_sites = []
        for index in range(5):
            extra_sites.append(await self.sites.create_site(SiteCreate(
                name=f"空站点{index}",
                base_url=f"https://empty{index}.example.test/proxy",
                timeout_seconds=5, source_kind="self_built",
            )))
        sixth = await self.sites.create_site(SiteCreate(
            name="第六站点", base_url="https://sixth.example.test/proxy",
            timeout_seconds=5, source_kind="self_built",
        ))
        self.news["sixth.example.test"] = 3
        self.hot["sixth.example.test"] = 3
        # 新选上限是 5：先按上限创建，再模拟历史任务直接补进第 6 个候选。
        task = await self._task(
            news_limit=2, hot_limit=2,
            sites=tuple(site.id for site in extra_sites),
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO push_task_sites (task_id, position, site_id) "
                "VALUES (?, ?, ?)",
                (task.id, 5, sixth.id),
            )
        result = await self.api.test_task(task.id)
        self.assertEqual(result.status_code, 200, result.body)
        data = result.body["data"]
        self.assertTrue(data["satisfied"], data)
        self.assertEqual(data["site_id"], sixth.id)
        self.assertEqual(data["checked_count"], 6)
        self.assertFalse(data["truncated"])

    async def test_new_task_rejects_sixth_candidate(self) -> None:
        sites = []
        for index in range(6):
            sites.append(await self.sites.create_site(SiteCreate(
                name=f"站点{index}",
                base_url=f"https://limit{index}.example.test/proxy",
                timeout_seconds=5, source_kind="self_built",
            )))
        with self.assertRaises(TaskValidationError):
            await self.tasks.create_task(TaskCreate(
                name="六个候选",
                items=[{"content_type": "daily_news", "params": {}, "limit": 1}],
                sites=[site.id for site in sites],
                output_format="text",
                umos=[UMO],
            ))

    async def test_legacy_six_candidates_update_keeps_unchanged_list(self) -> None:
        extra_sites = []
        for index in range(5):
            extra_sites.append(await self.sites.create_site(SiteCreate(
                name=f"旧站点{index}",
                base_url=f"https://legacy{index}.example.test/proxy",
                timeout_seconds=5, source_kind="self_built",
            )))
        task = await self._task(
            news_limit=1, hot_limit=1,
            sites=tuple(site.id for site in extra_sites),
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO push_task_sites (task_id, position, site_id) "
                "VALUES (?, ?, ?)",
                (task.id, 5, self.a.id),
            )
        current = await self.tasks.get_task(task.id)
        self.assertEqual(len(current.sites), 6)
        updated = await self.tasks.update_task(task.id, TaskUpdate(
            name="改名但保留候选",
            items=[
                {"content_type": "daily_news", "params": {}, "limit": 1},
                {"content_type": "zhihu_hot", "params": {}, "limit": 1},
            ],
            output_format=current.output_format,
            umos=list(current.umos),
            expected_revision=current.revision,
            sites=list(current.sites),
        ))
        self.assertEqual(tuple(updated.sites), tuple(current.sites))
        # 一旦改动候选（例如删到 5 个再按新顺序提交），就必须按新上限
        # 校验；把 6 个重新排序同样属于改动，应拒绝。
        with self.assertRaises(TaskValidationError):
            await self.tasks.update_task(task.id, TaskUpdate(
                name="重排候选",
                items=[
                    {"content_type": "daily_news", "params": {}, "limit": 1},
                    {"content_type": "zhihu_hot", "params": {}, "limit": 1},
                ],
                output_format=current.output_format,
                umos=list(current.umos),
                expected_revision=updated.revision,
                sites=list(reversed(current.sites)),
            ))

    async def test_probe_uses_actual_fetch_revision(self) -> None:
        state = {"done": False}
        original = self.preview.preview_site

        async def change_after_probe(*args, **kwargs):
            response = await original(*args, **kwargs)
            if (
                kwargs.get("site_id") == self.a.id
                and not state["done"]
                and getattr(response, "status_code", None) == 200
            ):
                state["done"] = True
                current = await self.sites.get_site(self.a.id)
                await self.sites.update_site(self.a.id, SiteUpdate(
                    name="探测后改名",
                    base_url="https://changed.example.test/proxy",
                    timeout_seconds=current.timeout_seconds,
                    expected_revision=current.revision,
                ))
            return response

        self.preview.preview_site = change_after_probe  # type: ignore[method-assign]
        task = await self._task(news_limit=2, hot_limit=3)
        preview = await self.preview.preview_assembled(
            {"task_id": task.id},
            site_ids=[self.a.id],
            owner="probe-revision",
        )
        # 探测返回后再改站点：旧栏目必须按探测时的版本复核并取消，
        # 不能把旧内容标成改名后的新 revision。
        self.assertEqual(preview.status_code, 409, preview.body)
        self.assertEqual(
            preview.body["data"]["code"], "preview_revision_changed",
        )

    async def test_delivery_refuses_old_content_when_probe_revision_changed(
        self,
    ) -> None:
        state = {"done": False}
        original = self.preview.preview_site

        async def rename_after_probe(*args, **kwargs):
            response = await original(*args, **kwargs)
            if (
                kwargs.get("site_id") == self.a.id
                and not state["done"]
                and getattr(response, "status_code", None) == 200
            ):
                state["done"] = True
                current = await self.sites.get_site(self.a.id)
                await self.sites.update_site(self.a.id, SiteUpdate(
                    name="探测后改名",
                    base_url=current.base_url,
                    timeout_seconds=current.timeout_seconds,
                    expected_revision=current.revision,
                ))
            return response

        self.preview.preview_site = rename_after_probe  # type: ignore[method-assign]
        task = await self._task(news_limit=2, hot_limit=3)
        result = await self.delivery.execute(task.id, "probe-revision-send")
        # 内容准备阶段已发现探测版本与当前版本不一致：不得发送旧正文，
        # 也不能发“采集失败”告警。
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "config_changed")

    async def test_column_fetch_storage_error_aborts_without_alert(
        self,
    ) -> None:
        async def broken_preview(*args, **kwargs):
            raise SiteStorageError("injected storage failure")

        self.content.preview_cached = broken_preview  # type: ignore[assignment]
        task = await self._task()
        preview = await self.preview.preview_assembled(
            {"task_id": task.id},
            site_ids=[self.a.id, self.b.id],
            owner="column-storage",
        )
        # 栏目取数内部的存储故障必须保留为 503，而不是降级成缺栏。
        self.assertEqual(preview.status_code, 503, preview.body)
        self.assertEqual(
            preview.body["data"]["code"], "storage_unavailable",
        )
        result = await self.delivery.execute(task.id, "column-storage")
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "storage_unavailable")

    async def test_source_checks_use_one_snapshot_read(self) -> None:
        class AtomicSites:
            def __init__(self, real, name: str) -> None:
                self._real = real
                self.name = name
                self.calls: list[tuple[str, ...]] = []

            async def get_revisions(self, site_ids):
                self.calls.append(tuple(site_ids))
                return await self._real.get_revisions(list(site_ids))

            async def get_site(self, site_id):
                raise AssertionError(
                    f"{self.name} 不应逐站读取来源版本：{site_id}",
                )

        revision_a = (await self.sites.get_site(self.a.id)).revision
        revision_b = (await self.sites.get_site(self.b.id)).revision
        atomic = AtomicSites(self.sites, "preview")
        original_preview_sites = self.preview._sites
        self.preview._sites = atomic
        try:
            verified = await self.preview._verify_sources((
                (self.a.id, revision_a), (self.b.id, revision_b),
            ))
            self.assertIsNone(verified)
            # 渲染后校验同一个方法，也必须一次读完。
            snapshot = SimpleNamespace(
                sites=((self.a.id, revision_a), (self.b.id, revision_b)),
            )
            self.assertIsNone(
                await self.preview._check_snapshot_sites(snapshot),
            )
        finally:
            self.preview._sites = original_preview_sites
        self.assertEqual(len(atomic.calls), 2)
        for call in atomic.calls:
            self.assertEqual(call, (self.a.id, self.b.id))

        # 发送校验不再单独读站点：任务版本、白名单和来源 revision 必须
        # 来自同一次 get_config_snapshot，不能再有“读完任务再读来源”的间隙。
        class RecordingTasks:
            def __init__(self, real) -> None:
                self._real = real
                self.calls = 0

            async def get_config_snapshot(self, task_id):
                self.calls += 1
                return await self._real.get_config_snapshot(task_id)

        task = await self._task(news_limit=1, hot_limit=2)
        prepared = _Prepared(
            slots=((1, "正文"),),
            content_hash="hash",
            content_status="complete",
            failed_sections=[],
            sites=((self.a.id, revision_a), (self.b.id, revision_b)),
        )
        recording = RecordingTasks(self.tasks)
        original_delivery_tasks = self.delivery._tasks
        original_delivery_sites = self.delivery._sites
        self.delivery._tasks = recording
        self.delivery._sites = AtomicSites(self.sites, "delivery")
        try:
            self.assertTrue(
                await self.delivery._config_unchanged(
                    task, prepared, UMO,
                ),
            )
        finally:
            self.delivery._tasks = original_delivery_tasks
            self.delivery._sites = original_delivery_sites
        self.assertEqual(recording.calls, 1)

        # 清空白名单后同一个方法必须失败：白名单与来源版本来自同一快照。
        current = await self.tasks.get_task(task.id)
        await self.tasks.update_task(task.id, TaskUpdate(
            name=current.name,
            items=[
                {"content_type": item.content_type, "params": {},
                 "limit": item.limit}
                for item in current.items
            ],
            output_format=current.output_format,
            umos=[],
            expected_revision=current.revision,
        ))
        self.assertFalse(
            await self.delivery._config_unchanged(task, prepared, UMO),
        )

    async def test_many_items_in_first_column_do_not_shrink_later_budget(
        self,
    ) -> None:
        # 首栏返回 50 条、第二栏固定 1.85 秒：预算必须按“还剩几个栏目”
        # 等分，不能被首栏条数覆盖成 1 秒级超时。
        self.news = {"a.example.test": 50}
        self.hot = {"a.example.test": 3}
        self.hot_delay["a.example.test"] = 1.85
        task = await self._task(
            news_limit=2, hot_limit=2, sites=(self.a.id,),
        )
        result = await self.delivery.execute(task.id, "many-first-column")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("新闻", text)
        self.assertIn("热榜", text)
        self.assertNotIn("60sAPI推送服务异常", text)
        self.assertEqual(result.content_status, "complete")

    async def test_confirmation_reads_report_storage_failure(self) -> None:
        task = await self._task()
        preview = await self.delivery_api.preview(task.id)
        self.assertEqual(preview.status_code, 200, preview.body)
        data = preview.body["data"]

        async def broken_get_site(site_id):
            raise SiteStorageError("injected confirmation read failure")

        self.sites.get_site = broken_get_site  # type: ignore[assignment]
        page = await self.delivery_api.preview(task.id)
        self.assertEqual(page.status_code, 503, page.body)
        self.assertEqual(
            page.body["data"]["code"], "storage_unavailable",
        )
        payload = {
            "execution_id": "confirm-storage",
            "expected_task_revision": data["task_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": data["alert_only"],
            "confirmation_version": 2,
        }
        submitted = await self.delivery_api.execute(task.id, payload)
        self.assertEqual(submitted.status_code, 503, submitted.body)
        self.assertEqual(
            submitted.body["data"]["code"], "storage_unavailable",
        )
        self.assertEqual(self.sender.messages, [])

    async def test_candidate_storage_failure_aborts_without_alert(self) -> None:
        async def broken_get_site(site_id):
            raise SiteStorageError("injected candidate read failure")

        self.sites.get_site = broken_get_site  # type: ignore[assignment]
        task = await self._task()
        with self.assertRaises(DeliveryStorageError):
            await self.delivery.execute(task.id, "candidate-storage")
        self.assertEqual(self.sender.messages, [])

    async def test_confirmation_candidates_are_rechecked_before_send(
        self,
    ) -> None:
        task = await self._task(news_limit=1, hot_limit=2)
        revision_a = (await self.sites.get_site(self.a.id)).revision
        revision_b = (await self.sites.get_site(self.b.id)).revision
        confirmed = [
            {"id": self.a.id, "revision": revision_a},
            {"id": self.b.id, "revision": revision_b},
        ]
        state = {"done": False}
        original = self.preview.preview_assembled

        async def change_after_confirm(*args, **kwargs):
            if not state["done"]:
                state["done"] = True
                current = await self.sites.get_site(self.a.id)
                await self.sites.update_site(self.a.id, SiteUpdate(
                    name="确认后改名",
                    base_url=current.base_url,
                    timeout_seconds=current.timeout_seconds,
                    expected_revision=current.revision,
                ))
            return await original(*args, **kwargs)

        self.preview.preview_assembled = change_after_confirm  # type: ignore[method-assign]
        with self.assertRaises(DeliveryConfirmationStaleError):
            await self.delivery.execute(
                task.id, "confirm-replaced",
                expected_task_revision=task.revision,
                expected_candidates=confirmed,
            )
        self.assertEqual(self.sender.messages, [])
        # 同一执行 ID 已完成后仍优先回读原记录，不重新确认、不重发。
        existing = await self.delivery.execute(
            task.id, "confirm-replaced",
            expected_task_revision=task.revision,
            expected_candidates=confirmed,
        )
        self.assertEqual(existing.id, "confirm-replaced")
        self.assertEqual(existing.status, "failed")
        self.assertEqual(self.sender.messages, [])

    async def test_storage_error_aborts_without_alert(self) -> None:
        class StoragePreview:
            async def preview_assembled(self, payload, **kwargs):
                return PlanPreviewResult(503, {
                    "status": "error",
                    "message": "配置存储暂不可用，请稍后重试。",
                    "data": {"code": "storage_unavailable"},
                })

        delivery = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=StoragePreview(),
            store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(),
        )
        try:
            task = await self._task()
            result = await delivery.execute(task.id, "storage-error")
        finally:
            await delivery.close()
        self.assertEqual(self.sender.messages, [])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "storage_unavailable")
        self.assertIsNone(result.content_status)

    async def test_single_site_two_columns_is_not_multi(self) -> None:
        task = await self._task(
            news_limit=1, hot_limit=2, sites=(self.a.id,),
        )
        record = await self.delivery.execute(task.id, "single-two-columns")
        details = await self.delivery_api.get_execution(record.id)
        self.assertEqual(details.status_code, 200, details.body)
        data = details.body["data"]
        self.assertEqual(data["source_mode"], "single")
        self.assertEqual(data["source_count"], 1)
        self.assertEqual(len(data["content_sources"]), 2)
        self.assertEqual(
            {entry["site_id"] for entry in data["content_sources"]},
            {self.a.id},
        )

    async def test_draft_test_allows_unchanged_legacy_list_only(self) -> None:
        extra_sites = []
        for index in range(5):
            extra_sites.append(await self.sites.create_site(SiteCreate(
                name=f"草稿旧站点{index}",
                base_url=f"https://draftlegacy{index}.example.test/proxy",
                timeout_seconds=5, source_kind="self_built",
            )))
        task = await self._task(
            news_limit=1, hot_limit=1,
            sites=tuple(site.id for site in extra_sites),
        )
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "INSERT INTO push_task_sites (task_id, position, site_id) "
                "VALUES (?, ?, ?)",
                (task.id, 5, self.a.id),
            )
        current = await self.tasks.get_task(task.id)
        payload = {
            "name": current.name,
            "items": [
                {"content_type": "daily_news", "params": {}, "limit": 1},
                {"content_type": "zhihu_hot", "params": {}, "limit": 1},
            ],
            "sites": list(current.sites),
            "output_format": current.output_format,
            "image_style": current.image_style,
        }
        # 新任务/无任务上下文：第 6 个候选仍按新上限拒绝。
        rejected = await self.api.test_draft(dict(payload))
        self.assertEqual(rejected.status_code, 400, rejected.body)
        # 编辑这条历史任务、候选完全未改动：允许测试完整 6 个候选。
        accepted = await self.api.test_draft({**payload, "task_id": task.id})
        self.assertEqual(accepted.status_code, 200, accepted.body)
        self.assertEqual(accepted.body["data"]["checked_count"], 6)
        # 同一任务但候选顺序被改过：不能被 task_id 绕过新上限。
        changed = await self.api.test_draft({
            **payload,
            "task_id": task.id,
            "sites": list(reversed(current.sites)),
        })
        self.assertEqual(changed.status_code, 400, changed.body)


if __name__ == "__main__":
    unittest.main()
