from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from core.content_client import ContentHttpClient
from core.content_service import ContentService
from core.delivery import (
    SAFE_ERROR_CODES,
    DeliveryLimits,
    DeliveryMessage,
    DeliverySendResult,
    DeliveryService,
    SendStatus,
)
from core.image_snapshots import ImageSnapshotStore
from core.plan_preview import PlanPreview
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate

UMO = "aiocqhttp:GroupMessage:g:1"
UMO_SECOND = "aiocqhttp:GroupMessage:g:2"
PNG = b"\x89PNG\r\n\x1a\n" + b"error-code-fake"


class ScriptedSender:
    """按调用序号脚本化的假发送器：成功、明确失败、挂起（超时）或抛异常。"""

    def __init__(self, script: list[tuple]) -> None:
        self.script = list(script)
        self.calls = 0
        self.messages: list[DeliveryMessage] = []

    def supports_umo(self, umo: object) -> bool:
        return True

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.calls += 1
        self.messages.append(message)
        step = self.script.pop(0) if self.script else ("success",)
        kind = step[0]
        if kind == "sleep":
            # 比单次发送上限更长：由调用方按预算取消。
            await asyncio.sleep(step[1])
            return DeliverySendResult(status=SendStatus.SUCCESS)
        if kind == "failed":
            return DeliverySendResult(status=SendStatus.FAILED, code=step[1])
        if kind == "unknown":
            return DeliverySendResult(status=SendStatus.UNKNOWN, code=step[1])
        if kind == "raise":
            raise step[1]
        return DeliverySendResult(status=SendStatus.SUCCESS)


class FakeImageRenderer:
    """零耗时的图片渲染器：内容正确性不是本模块的关注点。"""

    def __init__(self) -> None:
        self.calls = 0

    async def render(self, data, *, created_at=None):
        self.calls += 1
        return [
            SimpleNamespace(
                index=position,
                content_type=section.get("content_type", "daily_news"),
                label=section.get("label", "栏目"),
                status="success",
                width=600,
                height=800,
                png=PNG,
                error=None,
            )
            for position, section in enumerate(data.get("sections", []), start=1)
        ]


class DeliveryErrorCodeTest(unittest.IsolatedAsyncioTestCase):
    """中断后的记录：页面、目标、执行各自保留安全且可读的错误码。

    多目标多图片，发送器按脚本产出超时、明确失败与异常，核对实际调用
    次数与三层记录，不依赖任何真实消息平台。
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "error-codes.sqlite3"

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/v2/60s"):
                return httpx.Response(200, content=json.dumps({
                    "code": 200, "message": "ok",
                    "data": {"date": "2026-11-26",
                             "news": ["快新闻一", "快新闻二"]},
                }).encode())
            return httpx.Response(200, content=json.dumps({
                "code": 200, "message": "ok",
                "data": [{"title": "快热榜一", "hot_value": 1},
                         {"title": "快热榜二", "hot_value": 2}],
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
        self.images = FakeImageRenderer()
        self.api = PlanPreview(
            self.tasks, self.content, self.sites,
            image_snapshots=self.snapshots, image_service=self.images,
        )
        self.sender = ScriptedSender([])
        self.site = await self.sites.create_site(SiteCreate(
            name="本地站点", base_url="https://fast.example.test/api",
            timeout_seconds=5, source_kind="public",
        ))
        self.delivery = self._delivery()

    async def asyncTearDown(self) -> None:
        await self.delivery.close()
        await self.content.close()
        self.snapshots.close()
        await self.sites.close()
        self._tmp.cleanup()

    def _delivery(
        self, *, send_budget: float = 5.0, sender_timeout: float = 1.0,
        budget: float = 5.0,
    ) -> DeliveryService:
        return DeliveryService(
            tasks=self.tasks,
            sites=self.sites,
            plan_preview=self.api,
            store=self.sites.store,
            sender=self.sender,
            limits=DeliveryLimits(
                max_images=2,
                max_execution_seconds=budget,
                max_send_seconds=send_budget,
                sender_timeout_seconds=sender_timeout,
            ),
        )

    async def _image_task(self, *, umos=(UMO, UMO_SECOND)):
        # 两个栏目各一张长图：同一目标内既有“第一张”，也有“剩余图片”。
        return await self.tasks.create_task(TaskCreate(
            name="错误码任务",
            items=[
                {"content_type": content_type, "params": {}, "limit": 2}
                for content_type in ("daily_news", "zhihu_hot")
            ],
            output_format="image",
            umos=list(umos), sites=[self.site.id],
        ))

    @staticmethod
    def _slots(record) -> dict[tuple[int, int], object]:
        return {
            (page.position, page.page_index): page for page in record.pages
        }

    def _assert_codes_are_safe(self, record) -> None:
        for page in record.pages:
            if page.error_code is not None:
                self.assertIn(page.error_code, SAFE_ERROR_CODES)
                self.assertNotEqual(page.error_code, "internal_error")
        for target in record.targets:
            if target.error_code is not None:
                self.assertIn(target.error_code, SAFE_ERROR_CODES)
        if record.error_code is not None:
            self.assertIn(record.error_code, SAFE_ERROR_CODES)

    async def test_send_timeout_keeps_codes_on_pages_and_targets(self) -> None:
        # 发送预算 0.2 秒、单次上限 0.5 秒、假发送器挂 1 秒：
        # 第一张按单次上限超时（已调用），第二个目标预算已耗尽（未调用）。
        self.sender = ScriptedSender([("sleep", 1.0)])
        self.delivery = self._delivery(send_budget=0.2, sender_timeout=0.5)
        task = await self._image_task()
        record = await self.delivery.execute(task.id, "err-timeout")

        self.assertEqual(self.sender.calls, 1, "预算耗尽后不得调用发送器")
        slots = self._slots(record)
        self.assertEqual(sorted(slots), [(0, 1), (0, 2), (1, 1), (1, 2)])
        self.assertEqual(slots[(0, 1)].status, "unknown")
        self.assertEqual(slots[(0, 1)].error_code, "sender_timeout")
        self.assertEqual(slots[(0, 2)].status, "skipped")
        self.assertEqual(
            slots[(0, 2)].error_code, "skipped_after_send_unknown",
        )
        self.assertEqual(slots[(1, 1)].status, "unknown")
        self.assertEqual(
            slots[(1, 1)].error_code, "sender_budget_exhausted",
        )
        self.assertEqual(slots[(1, 2)].status, "skipped")
        self.assertEqual(
            slots[(1, 2)].error_code, "skipped_after_send_unknown",
        )

        # 目标记录要能区分“已调用后超时”和“预算耗尽、没有调用”。
        targets = {target.position: target for target in record.targets}
        self.assertEqual(targets[0].status, "unknown")
        self.assertEqual(targets[0].error_code, "sender_timeout")
        self.assertEqual(targets[1].status, "unknown")
        self.assertEqual(targets[1].error_code, "sender_budget_exhausted")

        # 执行级仍是结果未知，符合既有汇总语义。
        self.assertEqual(record.status, "unknown")
        self.assertIsNone(record.error_code)
        self._assert_codes_are_safe(record)

        again = await self.delivery.execute(task.id, "err-timeout")
        self.assertEqual(self.sender.calls, 1, "结果未知的投递不得自动重发")
        self.assertEqual(again.status, "unknown")

    async def test_explicit_send_failure_keeps_reason_and_skips_remainder(
        self,
    ) -> None:
        # 第一个目标的第一张明确失败：后续图片与目标继续按既有语义推进，
        # 失败原因保留在页面与目标上，执行级仍是部分成功。
        self.sender = ScriptedSender([("failed", "sender_route_rejected")])
        self.delivery = self._delivery()
        task = await self._image_task()
        record = await self.delivery.execute(task.id, "err-failed")

        self.assertEqual(self.sender.calls, 3)
        slots = self._slots(record)
        self.assertEqual(slots[(0, 1)].status, "failed")
        self.assertEqual(slots[(0, 1)].error_code, "sender_route_rejected")
        self.assertEqual(slots[(0, 2)].status, "skipped")
        self.assertEqual(
            slots[(0, 2)].error_code, "skipped_after_send_failed",
        )
        self.assertEqual(slots[(1, 1)].status, "success")
        self.assertIsNone(slots[(1, 1)].error_code)
        self.assertEqual(slots[(1, 2)].status, "success")

        targets = {target.position: target for target in record.targets}
        self.assertEqual(targets[0].status, "failed")
        self.assertEqual(targets[0].error_code, "sender_route_rejected")
        self.assertEqual(targets[1].status, "success")
        self.assertIsNone(targets[1].error_code)

        self.assertEqual(record.status, "partial")
        self._assert_codes_are_safe(record)

    async def test_sender_exception_does_not_leak_into_records(self) -> None:
        # 发送器抛出的原始异常（可能含凭据）不得进入任何记录字段。
        self.sender = ScriptedSender([
            ("raise", RuntimeError("上游错误 token=abc123 不可写入记录")),
        ])
        self.delivery = self._delivery()
        task = await self._image_task()
        record = await self.delivery.execute(task.id, "err-exception")

        slots = self._slots(record)
        self.assertEqual(slots[(0, 1)].status, "unknown")
        self.assertEqual(slots[(0, 1)].error_code, "sender_exception")
        self.assertEqual(
            slots[(0, 2)].error_code, "skipped_after_send_unknown",
        )
        targets = {target.position: target for target in record.targets}
        self.assertEqual(targets[0].error_code, "sender_exception")
        self.assertEqual(record.status, "unknown")

        codes = [page.error_code for page in record.pages] + [
            target.error_code for target in record.targets
        ] + [record.error_code]
        for code in codes:
            self.assertNotIn("abc123", code or "")
            self.assertNotIn("不可写入记录", code or "")
        self._assert_codes_are_safe(record)

    async def test_successful_multi_target_image_delivery_has_no_codes(self) -> None:
        # 全部送达时不写错误码，避免读记录的人误以为发生过中断。
        self.delivery = self._delivery()
        task = await self._image_task()
        record = await self.delivery.execute(task.id, "err-success")

        self.assertEqual(self.sender.calls, 4)
        self.assertEqual(record.status, "success")
        self.assertIsNone(record.error_code)
        for page in record.pages:
            self.assertEqual(page.status, "success")
            self.assertIsNone(page.error_code)
        for target in record.targets:
            self.assertEqual(target.status, "success")
            self.assertIsNone(target.error_code)
