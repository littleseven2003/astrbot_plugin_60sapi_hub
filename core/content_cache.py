"""统一内容预览的有界进程内缓存与同键请求合并。"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .news import NewsClientClosedError, NewsTimeoutError

DEFAULT_TTL_SECONDS = 300.0
DEFAULT_MAX_ENTRIES = 32
DEFAULT_WAIT_TIMEOUT_SECONDS = 130.0


class ContentCacheWaitTimeoutError(NewsTimeoutError):
    """等待同键共享获取超过上限。"""


class ContentCacheClosedError(NewsClientClosedError):
    """缓存层已关闭，不能继续读取或获取。"""


@dataclass(frozen=True, slots=True)
class CacheKey:
    """可公开诊断的缓存键；不包含凭据。"""

    site_id: str
    content_type: str
    params: tuple[tuple[str, str], ...] = ()
    revision: int = 0


@dataclass(frozen=True, slots=True)
class CacheResult:
    """一次获取的领域快照与独立缓存元信息。"""

    snapshot: Any
    hit: bool
    expires_at: str


@dataclass(slots=True)
class _CacheEntry:
    snapshot: Any
    expires_monotonic: float
    expires_at: str
    day_token: object | None = None


@dataclass(slots=True)
class _Inflight:
    task: asyncio.Task[CacheResult] | None
    waiters: int = 1
    completed: bool = False


class ContentCache:
    """TTL + 容量上限的 LRU 缓存；同键在途获取合并。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        wait_timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (
            lambda: datetime.now(timezone.utc)
        )
        self._wait_timeout_seconds = wait_timeout_seconds
        self._entries: OrderedDict[CacheKey, _CacheEntry] = OrderedDict()
        self._inflight: dict[CacheKey, _Inflight] = {}
        self._lock = asyncio.Lock()
        self._closed = True
        self._close_task: asyncio.Task[None] | None = None

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def is_open(self) -> bool:
        return not self._closed

    async def open(self) -> None:
        while True:
            async with self._lock:
                if not self._closed:
                    return
                close_task = self._close_task
            if close_task is not None and not close_task.done():
                await asyncio.shield(close_task)
                continue
            async with self._lock:
                if not self._closed:
                    return
                if self._close_task is not None and not self._close_task.done():
                    continue
                self._closed = False
                self._entries.clear()
                return

    async def close(self) -> None:
        async with self._lock:
            close_task = self._close_task
            if close_task is None:
                if self._closed and not self._inflight:
                    return
                self._closed = True
                tasks = tuple(
                    inflight.task
                    for inflight in self._inflight.values()
                    if inflight.task is not None
                )
                self._inflight.clear()
                self._entries.clear()
                close_task = asyncio.create_task(self._finish_close(tasks))
                self._close_task = close_task
        await asyncio.shield(close_task)

    async def _finish_close(
        self,
        tasks: tuple[asyncio.Task[CacheResult], ...],
    ) -> None:
        current = asyncio.current_task()
        try:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            async with self._lock:
                if self._close_task is current:
                    self._close_task = None

    async def get(
        self,
        key: CacheKey,
        fetch: Callable[[], Awaitable[Any]],
        *,
        force_refresh: bool = False,
        day_token: Callable[[], object] | None = None,
    ) -> CacheResult:
        """读取有效缓存或合并执行一次获取。

        ``force_refresh`` 跳过已完成缓存，但可以复用同一键当前在途的获取。
        等待者取消只减少等待计数；仍有等待者时共享任务继续，最后一个
        等待者取消时才取消共享任务。
        """
        async with self._lock:
            if self._closed:
                raise ContentCacheClosedError("内容缓存当前不可用。")
            now = self._monotonic()
            self._purge_expired(now)
            if not force_refresh:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_monotonic > now:
                    token = day_token() if day_token is not None else None
                    if day_token is None or entry.day_token == token:
                        self._entries.move_to_end(key)
                        return CacheResult(
                            snapshot=entry.snapshot,
                            hit=True,
                            expires_at=entry.expires_at,
                        )
                    self._entries.pop(key, None)

            inflight = self._inflight.get(key)
            if inflight is not None and inflight.completed:
                self._inflight.pop(key, None)
                inflight = None
            if inflight is None:
                inflight = _Inflight(task=None)
                task = asyncio.create_task(
                    self._run_fetch(key, fetch, day_token, inflight),
                )
                inflight.task = task
                self._inflight[key] = inflight
            else:
                inflight.waiters += 1
            task = inflight.task
            assert task is not None

        cancelled = False
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self._wait_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ContentCacheWaitTimeoutError(
                "等待共享内容获取超时。",
            ) from exc
        finally:
            async with self._lock:
                inflight.waiters -= 1
                if (
                    inflight.waiters <= 0
                    and not inflight.completed
                    and not task.done()
                ):
                    task.cancel()
                    cancelled = True
            if cancelled:
                task.add_done_callback(_consume_task_result)

    async def _run_fetch(
        self,
        key: CacheKey,
        fetch: Callable[[], Awaitable[Any]],
        day_token: Callable[[], object] | None,
        inflight: _Inflight,
    ) -> CacheResult:
        try:
            try:
                snapshot = await fetch()
            except asyncio.CancelledError:
                if self._closed:
                    raise ContentCacheClosedError(
                        "内容缓存当前不可用。",
                    ) from None
                raise

            now = self._monotonic()
            expires_at = (
                (
                    self._wall_clock()
                    + timedelta(seconds=self._ttl_seconds)
                )
                .astimezone(timezone.utc)
                .isoformat(timespec="seconds")
            )
            token = day_token() if day_token is not None else None
            entry = _CacheEntry(
                snapshot=snapshot,
                expires_monotonic=now + self._ttl_seconds,
                expires_at=expires_at,
                day_token=token,
            )
            async with self._lock:
                if self._closed:
                    raise ContentCacheClosedError(
                        "内容缓存当前不可用。",
                    )
                self._purge_expired(now)
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
                result = CacheResult(
                    snapshot=snapshot,
                    hit=False,
                    expires_at=expires_at,
                )
                inflight.completed = True
            return result
        finally:
            inflight.completed = True
            async with self._lock:
                if self._inflight.get(key) is inflight:
                    self._inflight.pop(key, None)

    def _purge_expired(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if entry.expires_monotonic <= now
        ]
        for key in expired:
            self._entries.pop(key, None)


def _consume_task_result(task: asyncio.Task[CacheResult]) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        return


__all__ = [
    "CacheKey",
    "CacheResult",
    "ContentCache",
    "ContentCacheClosedError",
    "ContentCacheWaitTimeoutError",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_WAIT_TIMEOUT_SECONDS",
]
