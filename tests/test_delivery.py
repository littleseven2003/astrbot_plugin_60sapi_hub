from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path

from core.delivery import (
    DeliveryLimits,
    DeliveryMessage,
    DeliverySendResult,
    DeliveryService,
    DeliveryStorageError,
    SendStatus,
)
from core.profile_api import ProfileApiResult
from core.profiles import ContentProfileService, ProfileCreate
from core.site_store import SiteStoreError
from core.sites import SiteCreate, SiteService
from core.tasks import PushTaskService, TaskCreate


def item():
    return {"content_type": "daily_news", "params": {}, "limit": None}


class StubApi:
    def __init__(self) -> None:
        self.preview_calls = 0
        self.image_calls = 0
        self.preview_status = 200
        self.preview_code = "all_sections_failed"
        self.text = "共享正文"
        self.markdown = "# 共享正文"
        self.image_pages = [b"page-one", b"page-two"]
        self.image_status = 200
        self.partial = False

    async def preview_profile(self, payload, *, owner=None):
        self.preview_calls += 1
        if self.preview_status != 200:
            return ProfileApiResult(
                self.preview_status,
                {"status": "error", "data": {"code": self.preview_code}},
            )
        return ProfileApiResult(200, {"status": "ok", "data": {
            "status": "partial" if self.partial else "complete",
            "outputs": {"text": self.text, "markdown": self.markdown},
            "failed_sections": ([{"label": "失败栏目"}] if self.partial else []),
            "image_snapshot": {"id": "snap-1"},
        }})

    async def generate_image(self, profile_id, payload, *, owner=None):
        self.image_calls += 1
        if self.image_status != 200:
            return ProfileApiResult(self.image_status, {
                "status": "error", "data": {"code": "image_render_failed"},
            })
        return ProfileApiResult(200, {"status": "ok", "data": {
            "page_count": len(self.image_pages),
            "pages": [
                {"index": i, "png_base64": base64.b64encode(png).decode()}
                for i, png in enumerate(self.image_pages, 1)
            ],
        }})


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[DeliveryMessage] = []
        self.modes: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.sleep_seconds = 0.0
        self.hook = None

    async def send(self, message: DeliveryMessage) -> DeliverySendResult:
        self.calls.append(message)
        self.started.set()
        if self.hook is not None:
            await self.hook(message)
        if self.block:
            await self.release.wait()
        if self.sleep_seconds:
            await asyncio.sleep(self.sleep_seconds)
        mode = self.modes.pop(0) if self.modes else "success"
        if mode == "success":
            return DeliverySendResult(SendStatus.SUCCESS)
        if mode == "failed":
            return DeliverySendResult(SendStatus.FAILED, "explicit_failed")
        if mode == "unknown":
            return DeliverySendResult(SendStatus.UNKNOWN, "send_unknown")
        raise RuntimeError("boom")


class DeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "plugin.sqlite3"
        self.sites = SiteService(self.db)
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.tasks = PushTaskService(self.profiles, self.sites.store)
        self.site = await self.sites.create_site(
            SiteCreate(
                name="投递站点", base_url="http://example.test/proxy",
                enabled=True, timeout_seconds=5,
            ),
        )
        self.profile = await self.profiles.create_profile(
            ProfileCreate(name="投递组合", site_id=self.site.id, items=[item()]),
        )
        self.api = StubApi()
        self.sender = FakeSender()
        self.service = DeliveryService(
            tasks=self.tasks, profiles=self.profiles, sites=self.sites,
            profile_api=self.api, store=self.sites.store, sender=self.sender,
            limits=DeliveryLimits(max_execution_seconds=5, sender_timeout_seconds=1),
        )

    async def asyncTearDown(self) -> None:
        await self.service.close()
        await self.sites.close()
        self.tmp.cleanup()

    async def _task(self, umos, output="text"):
        return await self.tasks.create_task(TaskCreate(
            name="投递任务", profile_id=self.profile.id,
            output_format=output, umos=umos,
        ))

    async def test_empty_targets_no_content_and_no_send(self) -> None:
        task = await self._task([])
        result = await self.service.execute(task.id, "empty-1")
        self.assertEqual(result.status, "no_targets")
        self.assertEqual(self.api.preview_calls, 0)
        self.assertEqual(self.api.image_calls, 0)
        self.assertEqual(len(self.sender.calls), 0)
        self.assertEqual(result.targets, ())

    async def test_text_shared_once_and_all_success(self) -> None:
        task = await self._task([
            "aiocqhttp:GroupMessage:g:1",
            "aiocqhttp:FriendMessage:u1",
        ])
        result = await self.service.execute(task.id, "text-1")
        self.assertEqual(result.status, "success")
        self.assertEqual(self.api.preview_calls, 1)
        self.assertEqual(len(self.sender.calls), 2)
        self.assertEqual(
            [m.umo for m in self.sender.calls],
            ["aiocqhttp:GroupMessage:g:1", "aiocqhttp:FriendMessage:u1"],
        )
        self.assertEqual([m.text for m in self.sender.calls], ["共享正文"] * 2)
        self.assertEqual([m.page_index for m in self.sender.calls], [1, 1])
        self.assertEqual(
            [t.status for t in result.targets], ["success", "success"],
        )
        self.assertEqual(len(result.pages), 2)

    async def test_image_pages_shared_and_ordered(self) -> None:
        task = await self._task([
            "aiocqhttp:GroupMessage:g:1",
            "aiocqhttp:FriendMessage:u1",
        ], output="image")
        result = await self.service.execute(task.id, "image-1")
        self.assertEqual(result.status, "success")
        self.assertEqual(self.api.preview_calls, 1)
        self.assertEqual(self.api.image_calls, 1)
        self.assertEqual(len(self.sender.calls), 4)
        self.assertEqual(
            [(m.umo, m.page_index, m.image_png) for m in self.sender.calls],
            [
                ("aiocqhttp:GroupMessage:g:1", 1, b"page-one"),
                ("aiocqhttp:GroupMessage:g:1", 2, b"page-two"),
                ("aiocqhttp:FriendMessage:u1", 1, b"page-one"),
                ("aiocqhttp:FriendMessage:u1", 2, b"page-two"),
            ],
        )
        self.assertEqual(len(result.pages), 4)

    async def test_explicit_failure_and_unknown_stop_target(self) -> None:
        task = await self._task([
            "aiocqhttp:GroupMessage:g:1",
            "aiocqhttp:FriendMessage:u1",
        ])
        self.sender.modes = ["success", "failed", "success"]
        result = await self.service.execute(task.id, "fail-1")
        self.assertEqual(result.status, "partial")
        self.assertEqual([t.status for t in result.targets], ["success", "failed"])
        self.assertEqual([m.umo for m in self.sender.calls], [
            "aiocqhttp:GroupMessage:g:1",
            "aiocqhttp:FriendMessage:u1",
        ])

        self.sender.calls.clear()
        self.sender.modes = ["unknown"]
        task2 = await self._task(["aiocqhttp:GroupMessage:g2"], output="image")
        result2 = await self.service.execute(task2.id, "unknown-1")
        self.assertEqual(result2.status, "unknown")
        self.assertEqual(result2.targets[0].status, "unknown")
        self.assertEqual(result2.pages[-1].status, "skipped")

    async def test_timeout_is_unknown_not_failed(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        self.sender.sleep_seconds = 1.1
        result = await self.service.execute(task.id, "timeout-1")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.targets[0].status, "unknown")
        self.assertEqual(result.pages[0].status, "unknown")

    async def test_same_execution_id_idempotent_and_concurrent(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        first = await self.service.execute(task.id, "same-1")
        self.assertEqual(len(self.sender.calls), 1)
        second = await self.service.execute(task.id, "same-1")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual(len(second.pages), 1)

        task2 = await self._task(["aiocqhttp:GroupMessage:g2"])
        self.sender.calls.clear()
        results = await asyncio.gather(
            self.service.execute(task2.id, "concurrent-1"),
            self.service.execute(task2.id, "concurrent-1"),
        )
        self.assertEqual(len(self.sender.calls), 1)
        self.assertEqual({r.id for r in results}, {"concurrent-1"})

    async def test_write_before_send_failure_never_calls_sender(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        original = self.sites.store.set_delivery_page_status

        async def fail(*args, **kwargs):
            raise SiteStoreError("write failed")

        self.sites.store.set_delivery_page_status = fail
        try:
            with self.assertRaises(DeliveryStorageError):
                await self.service.execute(task.id, "write-before-1")
        finally:
            self.sites.store.set_delivery_page_status = original
        self.assertEqual(len(self.sender.calls), 0)

    async def test_success_then_record_failure_is_unknown_not_retry(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        original = self.sites.store.set_delivery_page_status

        async def fail_success(*args, **kwargs):
            if kwargs.get("status") == "success":
                raise SiteStoreError("write failed")
            return await original(*args, **kwargs)

        self.sites.store.set_delivery_page_status = fail_success
        try:
            with self.assertRaises(DeliveryStorageError):
                await self.service.execute(task.id, "write-after-1")
        finally:
            self.sites.store.set_delivery_page_status = original
        self.assertEqual(len(self.sender.calls), 1)
        result = await self.sites.store.get_delivery_execution("write-after-1")
        self.assertEqual(result.status, "unknown")
        # A repeated call returns the existing record and does not resend.
        again = await self.service.execute(task.id, "write-after-1")
        self.assertEqual(again.status, "unknown")
        self.assertEqual(len(self.sender.calls), 1)

    async def test_config_change_after_first_target_skips_remaining(self) -> None:
        task = await self._task([
            "aiocqhttp:GroupMessage:g1",
            "aiocqhttp:FriendMessage:u1",
        ])

        async def delete_task(message):
            if message.umo == "aiocqhttp:GroupMessage:g1":
                await self.tasks.delete_task(task.id, task.revision)

        self.sender.hook = delete_task
        result = await self.service.execute(task.id, "change-1")
        self.assertEqual(result.status, "partial")
        self.assertEqual([t.status for t in result.targets], ["success", "skipped"])
        self.assertEqual(len(self.sender.calls), 1)

    async def test_cancel_and_close_mark_unknown(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        self.sender.block = True
        running = asyncio.create_task(self.service.execute(task.id, "cancel-1"))
        await self.sender.started.wait()
        running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await running
        result = await self.sites.store.get_delivery_execution("cancel-1")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.pages[0].status, "unknown")
        self.assertEqual(len(self.sender.calls), 1)

    async def test_restart_marks_incomplete_unknown_without_sending(self) -> None:
        await self.sites.store.create_delivery_execution(
            execution_id="crash-1", task_id="task-x", task_name="崩溃任务",
            task_revision=1, profile_id="p", profile_revision=1,
            site_id="s", site_revision=1, output_format="text",
            umos=("aiocqhttp:GroupMessage:g1",),
        )
        await self.sites.store.add_delivery_pages("crash-1", position=0, page_count=1)
        await self.sites.close()
        reopened = SiteService(self.db)
        await reopened.open()
        try:
            result = await reopened.store.get_delivery_execution("crash-1")
            self.assertEqual(result.status, "unknown")
            self.assertEqual(result.pages[0].status, "unknown")
            self.assertEqual(result.targets[0].status, "unknown")
        finally:
            await reopened.close()
        self.assertEqual(len(self.sender.calls), 0)

    async def test_disabled_site_and_all_failed_preview_no_send(self) -> None:
        await self.sites.update_site(
            self.site.id,
            __import__("core.sites", fromlist=["SiteUpdate"]).SiteUpdate(
                name=self.site.name, base_url=self.site.base_url,
                enabled=False, timeout_seconds=5,
                expected_revision=self.site.revision,
            ),
        )
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        result = await self.service.execute(task.id, "disabled-1")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "site_disabled")
        self.assertEqual(self.api.preview_calls, 0)
        self.assertEqual(len(self.sender.calls), 0)

    async def test_partial_sections_recorded_but_delivered(self) -> None:
        self.api.partial = True
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        result = await self.service.execute(task.id, "partial-1")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.content_status, "partial")
        self.assertIn("失败栏目", result.failed_sections_json or "")
        self.assertEqual(len(self.sender.calls), 1)

    async def test_preview_and_image_failure_never_call_sender(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        self.api.preview_status = 502
        result = await self.service.execute(task.id, "preview-fail-1")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "all_sections_failed")
        self.assertEqual(self.api.preview_calls, 1)
        self.assertEqual(len(self.sender.calls), 0)

        self.api.preview_status = 200
        self.api.image_status = 422
        image_task = await self._task(["aiocqhttp:GroupMessage:g2"], output="image")
        image_result = await self.service.execute(image_task.id, "image-fail-1")
        self.assertEqual(image_result.status, "failed")
        self.assertEqual(image_result.error_code, "image_render_failed")
        self.assertEqual(len(self.sender.calls), 0)

    async def test_second_page_explicit_failure_is_partial(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"], output="image")
        self.sender.modes = ["success", "failed"]
        result = await self.service.execute(task.id, "page-fail-1")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.targets[0].status, "failed")
        self.assertEqual([p.status for p in result.pages], ["success", "failed"])

    async def test_close_cancels_own_execution_but_not_unrelated_task(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        self.sender.block = True
        running = asyncio.create_task(self.service.execute(task.id, "close-1"))
        await self.sender.started.wait()
        unrelated = asyncio.create_task(asyncio.sleep(0.2))
        await self.service.close()
        outcome = (await asyncio.gather(running, return_exceptions=True))[0]
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertFalse(unrelated.cancelled())
        await unrelated
        result = await self.sites.store.get_delivery_execution("close-1")
        self.assertEqual(result.status, "unknown")
        self.assertEqual(len(self.sender.calls), 1)
        with self.assertRaises(Exception):
            await self.service.execute(task.id, "close-2")

    async def test_execution_records_revisions_hash_and_no_content(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        result = await self.service.execute(task.id, "record-1")
        self.assertEqual(result.task_id, task.id)
        self.assertEqual(result.task_revision, task.revision)
        self.assertEqual(result.profile_revision, self.profile.revision)
        self.assertEqual(result.site_revision, self.site.revision)
        self.assertEqual(len(result.content_hash or ""), 64)
        self.assertEqual(result.content_status, "complete")
        self.assertEqual(result.output_format, "text")
        self.assertFalse(hasattr(result, "text"))

    async def test_target_removal_and_new_targets_are_not_sent(self) -> None:
        task = await self._task([
            "aiocqhttp:GroupMessage:g1",
            "aiocqhttp:FriendMessage:u1",
        ])

        async def remove_second(message):
            if message.umo == "aiocqhttp:GroupMessage:g1":
                await self.tasks.update_task(
                    task.id,
                    __import__("core.tasks", fromlist=["TaskUpdate"]).TaskUpdate(
                        name=task.name,
                        profile_id=task.profile_id,
                        output_format=task.output_format,
                        umos=[task.umos[0]],
                        expected_revision=task.revision,
                    ),
                )

        self.sender.hook = remove_second
        result = await self.service.execute(task.id, "remove-1")
        self.assertEqual(result.status, "partial")
        self.assertEqual([t.status for t in result.targets], ["success", "skipped"])
        self.assertEqual(len(self.sender.calls), 1)
        repeated = await self.service.execute(task.id, "remove-1")
        self.assertEqual(repeated.id, result.id)
        self.assertEqual(len(self.sender.calls), 1)

        self.sender.hook = None
        self.sender.calls.clear()
        task2 = await self._task(["aiocqhttp:GroupMessage:g2"])
        async def add_target(message):
            await self.tasks.update_task(
                task2.id,
                __import__("core.tasks", fromlist=["TaskUpdate"]).TaskUpdate(
                    name=task2.name,
                    profile_id=task2.profile_id,
                    output_format=task2.output_format,
                    umos=["aiocqhttp:GroupMessage:g2", "aiocqhttp:FriendMessage:new"],
                    expected_revision=task2.revision,
                ),
            )
        self.sender.hook = add_target
        result2 = await self.service.execute(task2.id, "add-1")
        self.assertEqual([m.umo for m in self.sender.calls], ["aiocqhttp:GroupMessage:g2"])
        self.assertEqual(result2.status, "success")
        self.assertEqual([t.status for t in result2.targets], ["success"])

    async def test_profile_and_site_change_after_first_send_skips(self) -> None:
        task = await self._task([
            "aiocqhttp:GroupMessage:g1",
            "aiocqhttp:FriendMessage:u1",
        ])

        async def change_profile(message):
            if message.umo == task.umos[0]:
                from core.profiles import ProfileUpdate
                await self.profiles.update_profile(
                    self.profile.id,
                    ProfileUpdate(
                        name="组合已修改", site_id=self.site.id,
                        items=[item()],
                        expected_revision=self.profile.revision,
                    ),
                )

        self.sender.hook = change_profile
        result = await self.service.execute(task.id, "profile-change-1")
        self.assertEqual([t.status for t in result.targets], ["success", "skipped"])
        self.assertEqual(result.status, "partial")

    async def test_v3_database_migrates_delivery_tables_and_preserves_data(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        await self.sites.close()
        import sqlite3
        raw = sqlite3.connect(self.db)
        try:
            raw.execute("DROP TABLE delivery_pages")
            raw.execute("DROP TABLE delivery_targets")
            raw.execute("DROP TABLE delivery_executions")
            raw.execute("PRAGMA user_version = 3")
            raw.commit()
        finally:
            raw.close()
        reopened = SiteService(self.db)
        await reopened.open()
        try:
            store = reopened.store
            with sqlite3.connect(self.db) as check:
                self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(check.execute("SELECT COUNT(*) FROM sites").fetchone()[0], 1)
                self.assertEqual(check.execute("SELECT COUNT(*) FROM push_tasks").fetchone()[0], 1)
                self.assertEqual(
                    check.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                        "AND name IN ('delivery_executions','delivery_targets','delivery_pages')"
                    ).fetchone()[0], 3,
                )
        finally:
            await reopened.close()
        self.assertTrue(task.id)

    async def test_v4_migration_failure_rolls_back(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        await self.sites.close()
        import sqlite3
        raw = sqlite3.connect(self.db)
        try:
            raw.execute("DROP TABLE delivery_pages")
            raw.execute("DROP TABLE delivery_targets")
            raw.execute("DROP TABLE delivery_executions")
            raw.execute("PRAGMA user_version = 3")
            raw.commit()
        finally:
            raw.close()
        import core.site_store as module
        original = module._CREATE_DELIVERY_PAGES_TABLE
        module._CREATE_DELIVERY_PAGES_TABLE = "CREATE TABLE broken ("
        reopened = SiteService(self.db)
        try:
            with self.assertRaises(Exception):
                await reopened.open()
        finally:
            module._CREATE_DELIVERY_PAGES_TABLE = original
            await reopened.close()
        raw = sqlite3.connect(self.db)
        try:
            self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM sites").fetchone()[0], 1)
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM push_tasks").fetchone()[0], 1)
            self.assertEqual(
                raw.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='delivery_executions'"
                ).fetchone()[0], 0,
            )
        finally:
            raw.close()

    async def test_config_change_between_image_pages_marks_target_partial(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"], output="image")

        async def change_task(message):
            if message.page_index == 1:
                from core.tasks import TaskUpdate
                await self.tasks.update_task(
                    task.id,
                    TaskUpdate(
                        name="页面变更", profile_id=task.profile_id,
                        output_format=task.output_format, umos=list(task.umos),
                        expected_revision=task.revision,
                    ),
                )

        self.sender.hook = change_task
        result = await self.service.execute(task.id, "page-change-1")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.targets[0].status, "partial")
        self.assertEqual([p.status for p in result.pages], ["success", "skipped"])
        self.assertEqual(len(self.sender.calls), 1)

    async def test_history_survives_task_deletion(self) -> None:
        task = await self._task(["aiocqhttp:GroupMessage:g1"])
        result = await self.service.execute(task.id, "history-1")
        await self.tasks.delete_task(task.id, task.revision)
        stored = await self.sites.store.get_delivery_execution("history-1")
        self.assertEqual(stored.id, result.id)
        self.assertEqual(stored.status, "success")
        self.assertEqual(stored.targets[0].umo, "aiocqhttp:GroupMessage:g1")
        repeated = await self.service.execute(task.id, "history-1")
        self.assertEqual(repeated.status, "success")
        self.assertEqual(len(self.sender.calls), 1)
