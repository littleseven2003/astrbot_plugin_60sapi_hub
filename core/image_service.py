"""组合图片渲染的并发、超时和生命周期管理。"""

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
DEFAULT_RENDER_TIMEOUT_SECONDS = 20.0


class ImageServiceError(Exception):
    """图片服务错误基类。"""


class ImageServiceClosedError(ImageServiceError):
    """图片服务已关闭。"""


class ImageTimeoutError(ImageServiceError):
    """图片渲染超时。"""


class ImageRenderFailedError(ImageServiceError):
    """图片渲染失败。"""


class ProfileImageService:
    """在受限线程池中执行 Pillow 渲染，不阻塞事件循环。"""

    def __init__(
        self,
        *,
        font_path: str | None = None,
        limits: ImageRenderLimits | None = None,
        max_concurrent_renders: int = DEFAULT_MAX_CONCURRENT_RENDERS,
        timeout_seconds: float = DEFAULT_RENDER_TIMEOUT_SECONDS,
    ) -> None:
        if max_concurrent_renders < 1:
            raise ValueError("max_concurrent_renders must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._font_path = font_path
        self._limits = limits or ImageRenderLimits()
        self._max_concurrent = max_concurrent_renders
        self._timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_renders)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent_renders,
            thread_name_prefix="60sapi-image",
        )
        self._active_events: set[threading.Event] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    @property
    def max_concurrent_renders(self) -> int:
        return self._max_concurrent

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
        async with self._semaphore:
            if self._closed:
                raise ImageServiceClosedError("图片服务当前不可用。")
            cancel_event = threading.Event()
            self._active_events.add(cancel_event)
            loop = asyncio.get_running_loop()
            future: Future[list[ImagePage]] | None = None
            try:
                future = loop.run_in_executor(
                    self._executor,
                    self._render_sync,
                    data,
                    created_at,
                    cancel_event,
                )
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(future),
                        timeout=self._timeout_seconds,
                    )
                except asyncio.TimeoutError as exc:
                    cancel_event.set()
                    raise ImageTimeoutError("图片渲染超时。") from exc
                except asyncio.CancelledError:
                    cancel_event.set()
                    raise
            finally:
                self._active_events.discard(cancel_event)
                if future is not None and not future.done():
                    future.add_done_callback(_consume_future)

    async def close(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._closed = True
                for event in self._active_events:
                    event.set()
                self._active_events.clear()
                self._close_task = asyncio.create_task(
                    self._finish_close(),
                )
            close_task = self._close_task
        await asyncio.shield(close_task)

    async def _finish_close(self) -> None:
        try:
            await asyncio.to_thread(
                self._executor.shutdown,
                wait=True,
                cancel_futures=True,
            )
        finally:
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
    except Exception:
        pass


__all__ = [
    "DEFAULT_MAX_CONCURRENT_RENDERS",
    "DEFAULT_RENDER_TIMEOUT_SECONDS",
    "ImageRenderFailedError",
    "ImageServiceClosedError",
    "ImageServiceError",
    "ImageTimeoutError",
    "ProfileImageService",
]
