"""组合图片渲染的并发、排队、超时和生命周期管理。"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .profile_image import (
    ImagePage,
    ImageRenderCancelled,
    ImageRenderLimits,
    ProfileImageError,
    ProfileImageRenderer,
)

DEFAULT_MAX_CONCURRENT_RENDERS = 2
DEFAULT_MAX_QUEUED_RENDERS = 4
DEFAULT_RENDER_TIMEOUT_SECONDS = 20.0


class ImageServiceError(Exception):
    """图片服务错误基类。"""


class ImageServiceClosedError(ImageServiceError):
    """图片服务已关闭。"""


class ImageTimeoutError(ImageServiceError):
    """图片渲染超时（含排队时间）。"""


class ImageQueueTimeoutError(ImageTimeoutError):
    """请求在获得执行名额前排队超时。"""


class ImageRenderFailedError(ImageServiceError):
    """图片渲染失败。"""


class ProfileImageService:
    """在受限线程池中执行 Pillow 渲染，不阻塞事件循环。

    并发名额覆盖后台线程的实际生命周期，而不是请求的生命周期。请求超时或
    取消后，后台任务仍占用一个名额，直到线程真正结束；因此线程池队列不会
    因连续超时无限积压。
    """

    def __init__(
        self,
        *,
        font_path: str | None = None,
        limits: ImageRenderLimits | None = None,
        max_concurrent_renders: int = DEFAULT_MAX_CONCURRENT_RENDERS,
        max_queued_renders: int = DEFAULT_MAX_QUEUED_RENDERS,
        timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        if max_concurrent_renders < 1:
            raise ValueError("max_concurrent_renders must be positive")
        if max_queued_renders < 0:
            raise ValueError("max_queued_renders must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._font_path = font_path
        self._limits = limits or ImageRenderLimits()
        self._max_concurrent = max_concurrent_renders
        self._max_queued = max_queued_renders
        self._max_admitted = max_concurrent_renders + max_queued_renders
        self._timeout_seconds = timeout_seconds
        self._admission = asyncio.Semaphore(self._max_admitted)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent_renders,
            thread_name_prefix="60sapi-image",
        )
        self._active_events: set[threading.Event] = set()
        self._futures: set[Future[list[ImagePage]]] = set()
        self._closed = False
        self._closed_event = asyncio.Event()
        self._close_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def max_concurrent_renders(self) -> int:
        return self._max_concurrent

    @property
    def max_queued_renders(self) -> int:
        return self._max_queued

    @property
    def max_admitted_renders(self) -> int:
        return self._max_admitted

    @property
    def active_renders(self) -> int:
        return len(self._active_events)

    @property
    def is_open(self) -> bool:
        return not self._closed

    async def render(
        self,
        data: dict[str, Any],
        *,
        created_at: str = "",
    ) -> list[ImagePage]:
        if self._closed:
            raise ImageServiceClosedError("图片服务当前不可用。")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        await self._acquire_admission(deadline)
        if self._closed:
            self._admission.release()
            raise ImageServiceClosedError("图片服务当前不可用。")

        cancel_event = threading.Event()
        self._active_events.add(cancel_event)
        future: Future[list[ImagePage]] | None = None
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ImageQueueTimeoutError("图片渲染排队超时。")
            future = loop.run_in_executor(
                self._executor,
                self._render_sync,
                data,
                created_at,
                cancel_event,
            )
            self._futures.add(future)
            future.add_done_callback(
                lambda done: self._on_render_done(done, cancel_event),
            )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=remaining,
                )
            except asyncio.TimeoutError as exc:
                cancel_event.set()
                raise ImageTimeoutError("图片渲染超时。") from exc
            except asyncio.CancelledError:
                cancel_event.set()
                if self._closed:
                    raise ImageServiceClosedError(
                        "图片服务当前不可用。",
                    ) from None
                raise
        except Exception:
            if future is None:
                self._admission.release()
                self._active_events.discard(cancel_event)
            raise

    async def close(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._closed = True
                self._closed_event.set()
                for event in self._active_events:
                    event.set()
                self._close_task = asyncio.create_task(self._finish_close())
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _acquire_admission(self, deadline: float) -> None:
        if self._closed:
            raise ImageServiceClosedError("图片服务当前不可用。")
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ImageQueueTimeoutError("图片渲染排队超时。")
        admission_task = asyncio.create_task(self._admission.acquire())
        closed_task = asyncio.create_task(self._closed_event.wait())
        pending: set[asyncio.Task[Any]] = set()
        admitted = False
        handoff = False
        try:
            done, pending = await asyncio.wait(
                {admission_task, closed_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise ImageQueueTimeoutError("图片渲染排队超时。")
            if closed_task in done:
                raise ImageServiceClosedError("图片服务当前不可用。")
            await admission_task
            admitted = True
            if self._closed:
                raise ImageServiceClosedError("图片服务当前不可用。")
            handoff = True
        finally:
            for task in pending:
                task.cancel()
            closed_task.cancel()
            await asyncio.gather(*pending, closed_task, return_exceptions=True)
            if not handoff:
                if admission_task.done() and not admission_task.cancelled():
                    try:
                        if admission_task.result():
                            self._admission.release()
                    except Exception:
                        pass
                elif admitted:
                    self._admission.release()
    def _on_render_done(
        self,
        future: Future[list[ImagePage]],
        cancel_event: threading.Event,
    ) -> None:
        self._active_events.discard(cancel_event)
        self._futures.discard(future)
        try:
            self._admission.release()
        except Exception:
            pass
        _consume_future(future)

    async def _finish_close(self) -> None:
        try:
            await asyncio.to_thread(
                self._executor.shutdown,
                wait=True,
                cancel_futures=True,
            )
        finally:
            self._active_events.clear()
            self._futures.clear()
            async with self._lock:
                self._close_task = None

    def _render_sync(
        self,
        data: dict[str, Any],
        created_at: str,
        cancel_event: threading.Event,
    ) -> list[ImagePage]:
        if cancel_event.is_set():
            raise ImageRenderCancelled("图片渲染已取消。")
        renderer = ProfileImageRenderer(
            font_path=self._font_path,
            limits=self._limits,
        )
        try:
            return renderer.render(
                data,
                created_at=created_at,
                cancel_event=cancel_event,
            )
        except ImageRenderCancelled:
            raise
        except ProfileImageError:
            raise
        except Exception as exc:
            raise ImageRenderFailedError("图片渲染失败。") from exc


def _consume_future(future: Future[Any]) -> None:
    try:
        future.exception()
    except BaseException:
        # CancelledError derives from BaseException in Python 3.12; a
        # cancelled queued render must not escape from a done callback.
        pass


__all__ = [
    "DEFAULT_MAX_CONCURRENT_RENDERS",
    "DEFAULT_MAX_QUEUED_RENDERS",
    "DEFAULT_RENDER_TIMEOUT_SECONDS",
    "ImageQueueTimeoutError",
    "ImageRenderFailedError",
    "ImageServiceClosedError",
    "ImageServiceError",
    "ImageTimeoutError",
    "ProfileImageService",
]
