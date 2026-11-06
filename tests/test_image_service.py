from __future__ import annotations

import asyncio
import threading
import time
import unittest
from unittest.mock import patch

from core.image_service import (
    ImageQueueTimeoutError,
    ImageRenderFailedError,
    ImageServiceClosedError,
    ImageServiceError,
    ImageTimeoutError,
    ProfileImageService,
)
from core.profile_image import ImageRenderCancelled


class RenderState:
    def __init__(self, *, respect_cancel: bool) -> None:
        self.respect_cancel = respect_cancel
        self.release = threading.Event()
        self.started = threading.Event()
        self.lock = threading.Lock()
        self.started_count = 0
        self.finished_count = 0
        self.active = 0
        self.max_active = 0


class BlockingRenderer:
    state: RenderState

    def __init__(self, **_kwargs) -> None:
        pass

    def render(self, _data, *, limits=None, created_at="", cancel_event=None):
        state = type(self).state
        with state.lock:
            state.started_count += 1
            state.active += 1
            state.max_active = max(state.max_active, state.active)
        state.started.set()
        try:
            while not state.release.is_set():
                if (
                    state.respect_cancel
                    and cancel_event is not None
                    and cancel_event.is_set()
                ):
                    raise ImageRenderCancelled("cancelled")
                time.sleep(0.005)
            return ()
        finally:
            with state.lock:
                state.active -= 1
                state.finished_count += 1


async def attempt(service: ProfileImageService):
    try:
        await service.render({})
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return type(exc)


async def wait_for(predicate, timeout=3.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


async def wait_until(predicate, timeout=2.0) -> None:
    """Yield to the event loop until predicate is true, with a hard timeout."""

    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(poll(), timeout)
    except asyncio.TimeoutError as exc:
        raise AssertionError("condition not reached") from exc


class ImageServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_keeps_admission_until_worker_exits(self) -> None:
        state = RenderState(respect_cancel=False)
        BlockingRenderer.state = state
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=1,
            timeout_seconds=0.08,
        )
        try:
            with patch("core.image_service.ProfileImageRenderer", BlockingRenderer):
                first = asyncio.create_task(attempt(service))
                await asyncio.to_thread(state.started.wait, 2)
                self.assertEqual(await first, ImageTimeoutError)
                self.assertEqual(service.active_renders, 1)

                second = asyncio.create_task(attempt(service))
                await wait_for(lambda: service.active_renders == 2)
                self.assertEqual(await second, ImageTimeoutError)
                self.assertEqual(service.active_renders, 2)
                self.assertEqual(state.max_active, 1)

                third = asyncio.create_task(attempt(service))
                self.assertEqual(await third, ImageQueueTimeoutError)
                # Third request must not be submitted while the two admitted
                # workers are still alive; actual work remains bounded.
                self.assertEqual(service.active_renders, 2)
                self.assertEqual(state.max_active, 1)

                state.release.set()
                await wait_for(lambda: service.active_renders == 0)
        finally:
            state.release.set()
            await service.close()

    async def test_queue_timeout_and_cancel_after_submit(self) -> None:
        state = RenderState(respect_cancel=False)
        BlockingRenderer.state = state
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=0.25,
        )
        try:
            with patch("core.image_service.ProfileImageRenderer", BlockingRenderer):
                first = asyncio.create_task(attempt(service))
                await asyncio.to_thread(state.started.wait, 2)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                self.assertEqual(service.active_renders, 1)
                queued = asyncio.create_task(attempt(service))
                self.assertEqual(await queued, ImageQueueTimeoutError)
                self.assertEqual(state.started_count, 1)
                # A cancelled request must not release the worker's slot.
                blocked = asyncio.create_task(attempt(service))
                self.assertEqual(await blocked, ImageQueueTimeoutError)
                self.assertEqual(state.started_count, 1)

                state.release.set()
                await wait_for(lambda: service.active_renders == 0)
                ok = await asyncio.wait_for(service.render({}), 2)
                self.assertEqual(ok, ())
        finally:
            state.release.set()
            await service.close()

    async def test_cancel_while_waiting_for_admission_does_not_leak_slot(self) -> None:
        state = RenderState(respect_cancel=False)
        BlockingRenderer.state = state
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=5,
        )
        try:
            with patch("core.image_service.ProfileImageRenderer", BlockingRenderer):
                running = asyncio.create_task(attempt(service))
                await asyncio.to_thread(state.started.wait, 2)
                waiting = asyncio.create_task(attempt(service))
                await asyncio.sleep(0.02)
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting
                state.release.set()
                await wait_for(lambda: service.active_renders == 0)
                self.assertEqual(await service.render({}), ())
                await running
        finally:
            state.release.set()
            await service.close()

    async def test_close_releases_queued_and_waiting_requests(self) -> None:
        state = RenderState(respect_cancel=True)
        BlockingRenderer.state = state
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=1,
            timeout_seconds=5,
        )
        try:
            with patch("core.image_service.ProfileImageRenderer", BlockingRenderer):
                running = asyncio.create_task(attempt(service))
                await asyncio.to_thread(state.started.wait, 2)
                queued = asyncio.create_task(attempt(service))
                await wait_for(lambda: service.active_renders == 2)
                waiting = asyncio.create_task(attempt(service))
                await asyncio.sleep(0.02)
                self.assertEqual(service.active_renders, 2)

                await asyncio.wait_for(service.close(), 3)
                self.assertEqual(await queued, ImageServiceClosedError)
                self.assertEqual(await waiting, ImageServiceClosedError)
                result = await running
                self.assertIn(
                    result,
                    (ImageServiceClosedError, ImageRenderCancelled),
                )
        finally:
            state.release.set()
            if service.is_open:
                await service.close()


class ImmediateRenderer:
    def __init__(self, **_kwargs) -> None:
        pass

    def render(self, _data, *, limits=None, created_at="", cancel_event=None):
        return ()


class AdmissionCancellationTest(unittest.IsolatedAsyncioTestCase):
    async def _run_cancel_during_cleanup(self, service, second_cancel=False):
        started = asyncio.Event()
        second_stage = asyncio.Event()
        original_gather = asyncio.gather

        async def controlled_gather(*aws, **kwargs):
            started.set()
            try:
                return await original_gather(*aws, **kwargs)
            except asyncio.CancelledError:
                if second_cancel:
                    second_stage.set()
                    await asyncio.sleep(0.05)
                raise

        with patch("core.image_service.ProfileImageRenderer", ImmediateRenderer):
            with patch("core.image_service.asyncio.gather", controlled_gather):
                task = asyncio.create_task(service.render({}))
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                if second_cancel:
                    await asyncio.wait_for(second_stage.wait(), 1)
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

    async def test_cancel_during_cleanup_releases_admission(self) -> None:
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=2,
        )
        try:
            await self._run_cancel_during_cleanup(service)
            self.assertEqual(service._admission._value, 1)
            with patch("core.image_service.ProfileImageRenderer", ImmediateRenderer):
                self.assertEqual(await service.render({}), ())
        finally:
            await service.close()

    async def test_cancel_during_cleanup_without_renderer_patch(self) -> None:
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=2,
        )
        started = asyncio.Event()
        original_gather = asyncio.gather

        async def controlled_gather(*aws, **kwargs):
            started.set()
            return await original_gather(*aws, **kwargs)

        try:
            with patch("core.image_service.asyncio.gather", controlled_gather):
                task = asyncio.create_task(service.render({}))
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(service._admission._value, 1)
        finally:
            await service.close()

    async def test_second_cancel_during_cleanup_releases_once(self) -> None:
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=2,
        )
        try:
            await self._run_cancel_during_cleanup(service, second_cancel=True)
            self.assertEqual(service._admission._value, 1)
            with patch("core.image_service.ProfileImageRenderer", ImmediateRenderer):
                self.assertEqual(await service.render({}), ())
        finally:
            await service.close()

    async def test_multi_round_cancel_does_not_change_capacity(self) -> None:
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=2,
        )
        initial_capacity = service._admission._value
        try:
            for _ in range(3):
                await self._run_cancel_during_cleanup(service)
                self.assertEqual(service._admission._value, initial_capacity)
            with patch("core.image_service.ProfileImageRenderer", ImmediateRenderer):
                for _ in range(3):
                    self.assertEqual(await service.render({}), ())
            await wait_until(
                lambda: (
                    service.active_renders == 0
                    and len(service._futures) == 0
                    and service._admission._value == initial_capacity
                ),
            )
            self.assertEqual(service._admission._value, initial_capacity)
            self.assertEqual(service.active_renders, 0)
            self.assertEqual(len(service._futures), 0)
        finally:
            await service.close()

    async def test_close_and_cancel_do_not_leak(self) -> None:
        service = ProfileImageService(
            max_concurrent_renders=1,
            max_queued_renders=0,
            timeout_seconds=2,
        )
        started = asyncio.Event()
        original_gather = asyncio.gather

        async def controlled_gather(*aws, **kwargs):
            started.set()
            return await original_gather(*aws, **kwargs)

        with patch("core.image_service.ProfileImageRenderer", ImmediateRenderer):
            with patch("core.image_service.asyncio.gather", controlled_gather):
                task = asyncio.create_task(service.render({}))
                await asyncio.wait_for(started.wait(), 1)
                close_task = asyncio.create_task(service.close())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(close_task, 2)
        self.assertFalse(service.is_open)


class FakeSectionImage:
    """测试用的栏目长图替身：与渲染器冻结的 SectionImage 同形。"""

    def __init__(self, index, content_type, label, status, png=None, error=None):
        self.index = index
        self.content_type = content_type
        self.label = label
        self.status = status
        self.width = 900
        self.height = 640
        self.png = png
        self.error = error


class FrozenRendererContractTest(unittest.IsolatedAsyncioTestCase):
    """渲染器接口已冻结为 render(data, *, limits)。"""

    async def test_render_passes_data_limits_time_and_cancel_event(self) -> None:
        received = {}

        class RecordingRenderer:
            def __init__(self, **kwargs) -> None:
                received['font_path'] = kwargs.get('font_path')

            def render(self, data, *, limits, created_at='', cancel_event=None):
                received['data'] = data
                received['limits'] = limits
                received['created_at'] = created_at
                received['cancel_event'] = cancel_event
                return (
                    FakeSectionImage(1, 'daily_news', '每日新闻', 'success', png=b'a'),
                    FakeSectionImage(2, 'zhihu_hot', '知乎热榜', 'failed', error='too_tall'),
                )

        service = ProfileImageService(max_concurrent_renders=1)
        try:
            with patch('core.image_service.ProfileImageRenderer', RecordingRenderer):
                result = await service.render(
                    {'profile_name': 'p'},
                    created_at='2026-11-05T00:00:00+00:00',
                )
        finally:
            await service.close()
        self.assertEqual(received['data'], {'profile_name': 'p'})
        self.assertIs(received['limits'], service._limits)
        self.assertEqual(received['created_at'], '2026-11-05T00:00:00+00:00')
        self.assertTrue(callable(received['cancel_event'].is_set))
        self.assertIsInstance(result, tuple)
        self.assertEqual([item.index for item in result], [1, 2])
        self.assertEqual(result[1].status, 'failed')

    async def test_list_result_is_normalised_to_tuple(self) -> None:
        class ListRenderer:
            def __init__(self, **_kwargs) -> None:
                pass

            def render(self, _data, *, limits, created_at='', cancel_event=None):
                return [
                    FakeSectionImage(1, 'daily_news', '每日新闻', 'empty', png=b'a'),
                ]

        service = ProfileImageService(max_concurrent_renders=1)
        try:
            with patch('core.image_service.ProfileImageRenderer', ListRenderer):
                result = await service.render({})
        finally:
            await service.close()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 1)

    async def test_invalid_result_shape_raises_render_failed(self) -> None:
        class BadRenderer:
            def __init__(self, **_kwargs) -> None:
                pass

            def render(self, _data, *, limits, created_at='', cancel_event=None):
                return 'not-a-sequence'

        service = ProfileImageService(max_concurrent_renders=1)
        try:
            with patch('core.image_service.ProfileImageRenderer', BadRenderer):
                with self.assertRaises(ImageServiceError):
                    await service.render({})
        finally:
            await service.close()
