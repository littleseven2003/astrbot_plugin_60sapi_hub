from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.delivery import (
    DeliveryLimits,
    DeliveryMessage,
    DeliverySendResult,
    DeliveryService,
    SendStatus,
)
from core.image_service import ProfileImageService
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview
from core.sites import SiteCreate, SiteUpdate, SiteService
from core.tasks import PushTaskService, TaskCreate

SECRET = "simulated-secret-abc123"
UMO = "aiocqhttp:GroupMessage:g:1"


class RecordingSender:
    def __init__(self) -> None:
        self.messages: list[DeliveryMessage] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.messages.append(message)
        return DeliverySendResult(status=SendStatus.SUCCESS)


class TaskFailoverEndToEndTest(unittest.IsolatedAsyncioTestCase):
    """真实 DeliveryService + 模拟上游 + 记录型发送器。"""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "e2e.sqlite3"
        self.hosts: list[str] = []
        # 站点级启用已移除；用上游失败模拟“该候选本次不可用”。
        self.fail_hosts: set[str] = set()

        async def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            self.hosts.append(host)
            if host in self.fail_hosts:
                return httpx.Response(500, content=b"upstream down")
            rich = host.startswith("b.")
            if request.url.path.endswith("/v2/60s"):
                news = (
                    ["B新闻一", "B新闻二", "B新闻三"] if rich else ["A新闻一"]
                )
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-11-26", "news": news},
                }).encode())
            hot = (
                [{"title": "B热榜一", "hot_value": 1},
                 {"title": "B热榜二", "hot_value": 2}]
                if rich else [{"title": "A热榜一", "hot_value": 1}]
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
        self.api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.sender = RecordingSender()
        self.delivery = DeliveryService(
            tasks=self.tasks,
            sites=self.sites,
            plan_preview=self.api,
            store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(),
        )
        self.a = await self.sites.create_site(SiteCreate(
            name="站点A", base_url=f"https://a.example.test/{SECRET}",
            timeout_seconds=5, source_kind="public",
        ))
        self.b = await self.sites.create_site(SiteCreate(
            name="站点B", base_url=f"https://b.example.test/{SECRET}",
            timeout_seconds=5, source_kind="public",
        ))

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.images.close()
        self.snapshots.close()
        await self.content.close()
        await self.sites.close()
        self._tmp.cleanup()

    async def _set_enabled(self, site, enabled: bool) -> None:
        """兼容旧用例名：enabled=False 表示该候选上游失败，而不是站点停用。"""
        host = (await self.sites.get_site(site.id)).base_url.split("/")[2]
        if enabled:
            self.fail_hosts.discard(host)
        else:
            self.fail_hosts.add(host)

    async def _clear_candidates(self, task) -> None:
        """清空任务候选行：模拟“没有单站可用且没有提示目标”的告警路径。"""
        with sqlite3.connect(self.path) as connection, connection:
            connection.execute(
                "DELETE FROM push_task_sites WHERE task_id = ?", (task.id,),
            )

    async def _restore_candidate(self, task, site_id: str) -> None:
        with sqlite3.connect(self.path) as connection, connection:
            connection.execute(
                "INSERT INTO push_task_sites (task_id, site_id, position) "
                "VALUES (?, ?, 0)",
                (task.id, site_id),
            )

    @staticmethod
    def _items(limit: int = 2):
        return [
            {"content_type": "daily_news", "params": {}, "limit": limit},
            {"content_type": "zhihu_hot", "params": {}, "limit": limit},
        ]

    async def _task(self, sites, *, items=None, fmt: str = "text",
                    umos=(UMO,)):
        return await self.tasks.create_task(TaskCreate(
            name="任务", items=items if items is not None else self._items(),
            output_format=fmt, umos=list(umos), sites=list(sites),
        ))

    async def test_insufficient_first_site_falls_back_to_second(self) -> None:
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        self.hosts.clear()
        record = await self.delivery.execute(task.id, "exec-1")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertIn("B新闻", text)
        self.assertIn("B热榜", text)
        self.assertNotIn("A新闻", text)
        # 记录反映实际使用的站点。
        self.assertEqual(record.site_id, self.b.id)

    async def test_limit_is_a_required_count_not_only_a_display_cap(self) -> None:
        # 站点 B 有 3 条新闻、2 条热榜：条数 2 满足，条数 4 就一个站点都不满足。
        items = self._items(2)
        task = await self._task([self.b.id], items=items)
        record = await self.delivery.execute(task.id, "exec-limit-ok")
        self.assertIn("B新闻", self.sender.messages[0].text or "")
        self.assertNotEqual(record.status, "failed")

        strict_items = self._items(4)
        strict_task = await self._task([self.b.id], items=strict_items)
        self.sender.messages.clear()
        blocked = await self.delivery.execute(strict_task.id, "exec-limit-strict")
        text = self.sender.messages[0].text or ""
        self.assertTrue(text.startswith("60sAPI推送服务异常："), text)
        self.assertEqual(blocked.content_status, "failed")

    async def test_only_candidate_b_is_used(self) -> None:
        items = self._items(2)
        task = await self._task([self.b.id], items=items)
        record = await self.delivery.execute(task.id, "exec-2")
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("B新闻", self.sender.messages[0].text or "")
        self.assertEqual(record.site_id, self.b.id)
        self.assertNotEqual(record.status, "failed")

    async def test_all_candidates_failing_sends_safe_alert(self) -> None:
        items = self._items(5)
        task = await self._task([self.a.id, self.b.id], items=items)
        record = await self.delivery.execute(task.id, "exec-3")
        self.assertEqual(len(self.sender.messages), 1)
        text = self.sender.messages[0].text or ""
        self.assertTrue(text.startswith("60sAPI推送服务异常："), text)
        self.assertIsNone(self.sender.messages[0].image_png)
        # 记录能区分“内容采集失败”。
        self.assertEqual(record.content_status, "failed")

    async def test_image_task_alert_is_sent_as_text(self) -> None:
        items = self._items(9)
        task = await self._task([self.a.id], fmt="image", items=items)
        await self.delivery.execute(task.id, "exec-4")
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        self.assertIsNone(message.image_png)
        self.assertTrue((message.text or "").startswith("60sAPI推送服务异常："))

    async def test_empty_whitelist_sends_nothing(self) -> None:
        items = self._items(2)
        task = await self._task([self.b.id], umos=(), items=items)
        await self.delivery.execute(task.id, "exec-5")
        self.assertEqual(self.sender.messages, [])

    async def test_disabled_candidate_is_skipped(self) -> None:
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        current = await self.sites.get_site(self.a.id)
        await self.sites.update_site(self.a.id, SiteUpdate(
            name=current.name, base_url=current.base_url, timeout_seconds=5, source_kind=current.source_kind,
            expected_revision=current.revision,
        ))
        record = await self.delivery.execute(task.id, "exec-6")
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("B新闻", self.sender.messages[0].text or "")
        self.assertEqual(record.site_id, self.b.id)


    async def test_alert_message_kind_is_text_for_image_tasks(self) -> None:
        items = self._items(9)
        task = await self._task([self.a.id], fmt="image", items=items)
        record = await self.delivery.execute(task.id, "exec-kind")
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        # 真实适配边界要求：文字提示必须是 text，不能伪装成 image。
        self.assertEqual(message.kind, "text")
        self.assertIsNone(message.image_png)
        self.assertEqual(record.content_status, "failed")

    async def test_unknown_send_result_is_preserved(self) -> None:
        class UnknownSender(RecordingSender):
            async def send(self, message):
                self.messages.append(message)
                return DeliverySendResult(status=SendStatus.UNKNOWN)

        unknown = UnknownSender()
        self.delivery = DeliveryService(
            tasks=self.tasks, sites=self.sites, plan_preview=self.api,
            store=self.sites.store, sender=unknown, limits=DeliveryLimits(),
        )
        items = self._items(9)
        task = await self._task([self.a.id], items=items)
        record = await self.delivery.execute(task.id, "exec-unknown")
        self.assertEqual(len(unknown.messages), 1)
        # 采集失败与投递结果分别可见，unknown 不能被改写成明确失败。
        self.assertEqual(record.status, "unknown")
        self.assertEqual(record.content_status, "failed")
        again = await self.delivery.execute(task.id, "exec-unknown")
        self.assertEqual(len(unknown.messages), 1)
        self.assertEqual(again.status, "unknown")


class _Inspector:
    def inspect_target(self, umo: str) -> dict:
        return {
            "umo": umo, "platform_id": "aiocqhttp", "message_type": "GroupMessage",
            "target": "g:1", "isolated": True, "valid": True, "reason": None,
        }


class ConfirmIntentTest(TaskFailoverEndToEndTest):
    """确认页锁定意图：候选状态在提交前变化时不得发送。"""

    def _api(self):
        from core.delivery_api import DeliveryApi

        return DeliveryApi(
            delivery=self.delivery, tasks=self.tasks,
            sites=self.sites, store=self.sites.store, inspector=_Inspector(),
        )



    async def test_confirmed_alert_only_is_delivered(self) -> None:
        """A/B 均停用、配置未变化：确认过的文字提示必须发出。"""
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        await self._clear_candidates(task)
        data = (await api.preview(task.id)).body["data"]
        self.assertTrue(data["alert_only"])
        result = await api.execute(task.id, {
            "execution_id": "ok-alert",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": True,
        })
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertEqual(self.sender.messages[0].kind, "text")
        self.assertTrue(
            (self.sender.messages[0].text or "").startswith("60sAPI推送服务异常："),
        )
        record = result.body["data"]
        self.assertEqual(record["content_status"], "failed")

    async def test_confirmed_content_from_backup_is_delivered(self) -> None:
        """A 停用、B 可用、配置未变化：确认过的 B 内容必须发出。"""
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        await self._set_enabled(self.a, False)
        data = (await api.preview(task.id)).body["data"]
        self.assertFalse(data["alert_only"])
        result = await api.execute(task.id, {
            "execution_id": "ok-content",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": False,
        })
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertIn("B新闻", self.sender.messages[0].text or "")
        self.assertEqual(result.body["data"]["site_id"], self.b.id)

    async def test_stale_confirmation_leaves_terminal_record(self) -> None:
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        await self._clear_candidates(task)
        data = (await api.preview(task.id)).body["data"]
        await self._restore_candidate(task, self.b.id)
        result = await api.execute(task.id, {
            "execution_id": "stale-terminal",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": True,
        })
        self.assertEqual(result.status_code, 409, result.body)
        # 校验阶段就拒绝：不创建执行记录，页面不会锁定到不存在的执行。
        from core.site_store import DeliveryRecordNotFoundError

        with self.assertRaises(DeliveryRecordNotFoundError):
            await self.sites.store.get_delivery_execution("stale-terminal")
        self.assertEqual(self.sender.messages, [])



    async def test_content_failure_after_confirmation_sends_alert(self) -> None:
        """A/B 均启用、配置未变，但条数不足：发送已说明的异常提示。"""
        items = self._items(5)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        data = (await api.preview(task.id)).body["data"]
        self.assertFalse(data["alert_only"])
        result = await api.execute(task.id, {
            "execution_id": "content-fail",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": False,
        })
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(len(self.sender.messages), 1)
        message = self.sender.messages[0]
        self.assertEqual(message.kind, "text")
        self.assertTrue((message.text or "").startswith("60sAPI推送服务异常："))
        record = result.body["data"]
        self.assertEqual(record["content_status"], "failed")

    async def test_upstream_error_after_confirmation_sends_alert(self) -> None:
        """上游全部失败同样按已说明规则发提示，而不是报配置变化。"""
        items = self._items(9)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        data = (await api.preview(task.id)).body["data"]
        # 让两个站点在确认后都无法提供足够内容。
        await self.content.close()
        result = await api.execute(task.id, {
            "execution_id": "upstream-fail",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": False,
        })
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(len(self.sender.messages), 1)
        self.assertEqual(self.sender.messages[0].kind, "text")

    async def test_same_execution_id_returns_original_record(self) -> None:
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        first = await self.delivery.execute(task.id, "idem-1")
        sent = len(self.sender.messages)
        self.assertGreaterEqual(sent, 1)
        # 完成后再改候选，重复查询同一 execution_id 仍应取回原记录。
        await self._set_enabled(self.b, False)
        api = self._api()
        again = await api.execute(task.id, {
            "execution_id": "idem-1",
            "expected_task_revision": task.revision,
            "expected_profile_revision": task.revision,
            "expected_site_revision": first.site_revision,
        })
        self.assertEqual(again.status_code, 200, again.body)
        self.assertEqual(again.body["data"]["id"], "idem-1")
        self.assertEqual(len(self.sender.messages), sent)

    async def test_alert_only_confirmation_cannot_become_content(self) -> None:
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        await self._clear_candidates(task)
        preview = await api.preview(task.id)
        data = preview.body["data"]
        self.assertTrue(data["alert_only"])
        # 提交前 B 恢复为候选：确认的意图（只发提示）已失效。
        await self._restore_candidate(task, self.b.id)
        result = await api.execute(task.id, {
            "execution_id": "confirm-1",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": True,
        })
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(result.body["data"]["code"], "confirmation_stale")
        self.assertEqual(self.sender.messages, [])

    async def test_content_confirmation_cannot_become_alert(self) -> None:
        items = self._items(2)
        task = await self._task([self.a.id, self.b.id], items=items)
        api = self._api()
        preview = await api.preview(task.id)
        data = preview.body["data"]
        self.assertFalse(data["alert_only"])
        # 提交前候选全部清空：实际会变成提示，与确认不符。
        await self._clear_candidates(task)
        result = await api.execute(task.id, {
            "execution_id": "confirm-2",
            "expected_task_revision": data["task_revision"],
            "expected_profile_revision": data["plan_revision"],
            "expected_site_revision": data["site_revision"],
            "expected_candidates": data["candidate_snapshot"],
            "expected_alert_only": False,
        })
        self.assertEqual(result.status_code, 409, result.body)
        self.assertEqual(self.sender.messages, [])



if __name__ == "__main__":
    unittest.main()
