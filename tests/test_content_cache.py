from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from core.content_cache import (
    CacheKey,
    ContentCache,
    ContentCacheClosedError,
    ContentCacheWaitTimeoutError,
)


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_value = 0.0
        self.wall = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.monotonic_value

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall += timedelta(seconds=seconds)


class ContentCacheTest(unittest.IsolatedAsyncioTestCase):
    def test_default_limits(self) -> None:
        cache = ContentCache()
        self.assertEqual(cache.ttl_seconds, 300)
        self.assertEqual(cache.max_entries, 32)

    async def asyncSetUp(self) -> None:
        self.clock = FakeClock()
        self.cache = ContentCache(
            ttl_seconds=10,
            max_entries=2,
            monotonic=self.clock.monotonic,
            wall_clock=self.clock.now,
            wait_timeout_seconds=2,
        )
        await self.cache.open()
        self.addAsyncCleanup(self.cache.close)

    async def test_miss_hit_and_ttl_boundary(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return {"value": calls}

        first = await self.cache.get(key, fetch)
        second = await self.cache.get(key, fetch)
        self.assertFalse(first.hit)
        self.assertTrue(second.hit)
        self.assertEqual(calls, 1)
        self.assertEqual(first.snapshot, second.snapshot)
        self.assertEqual(first.expires_at, "2026-10-26T00:00:10+00:00")

        self.clock.advance(9.999)
        self.assertTrue((await self.cache.get(key, fetch)).hit)
        self.clock.advance(0.001)
        expired = await self.cache.get(key, fetch)
        self.assertFalse(expired.hit)
        self.assertEqual(calls, 2)

    async def test_force_refresh_bypasses_completed_cache(self) -> None:
        key = CacheKey("site", "bilibili_hot", (), 1)
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return calls

        await self.cache.get(key, fetch)
        refreshed = await self.cache.get(key, fetch, force_refresh=True)
        self.assertFalse(refreshed.hit)
        self.assertEqual(refreshed.snapshot, 2)
        self.assertEqual(calls, 2)
        hit = await self.cache.get(key, fetch)
        self.assertTrue(hit.hit)
        self.assertEqual(hit.snapshot, 2)
        self.assertEqual(calls, 2)

    async def test_empty_values_are_cached_and_errors_are_not(self) -> None:
        key = CacheKey("site", "zhihu_hot", (), 1)
        calls = 0

        async def empty_fetch():
            nonlocal calls
            calls += 1
            return ()

        await self.cache.get(key, empty_fetch)
        self.assertTrue((await self.cache.get(key, empty_fetch)).hit)
        self.assertEqual(calls, 1)

        error_key = CacheKey("site", "douyin_hot", (), 1)
        async def failing():
            raise RuntimeError("upstream")
        with self.assertRaises(RuntimeError):
            await self.cache.get(error_key, failing)
        self.assertEqual(self.cache.size, 1)

    async def test_failed_refresh_keeps_previous_cache(self) -> None:
        key = CacheKey("site", "toutiao_hot", (), 1)
        calls = 0

        async def good():
            nonlocal calls
            calls += 1
            return "old"

        await self.cache.get(key, good)
        async def failing():
            raise RuntimeError("refresh failed")
        with self.assertRaises(RuntimeError):
            await self.cache.get(key, failing, force_refresh=True)
        old = await self.cache.get(key, good)
        self.assertTrue(old.hit)
        self.assertEqual(old.snapshot, "old")

        self.clock.advance(11)
        async def new():
            return "new"
        refreshed = await self.cache.get(key, new)
        self.assertFalse(refreshed.hit)
        self.assertEqual(refreshed.snapshot, "new")

    async def test_key_isolates_site_type_params_and_revision(self) -> None:
        calls = 0
        async def fetch():
            nonlocal calls
            calls += 1
            return calls

        keys = [
            CacheKey("s1", "daily_news", (), 1),
            CacheKey("s2", "daily_news", (), 1),
            CacheKey("s1", "bilibili_hot", (), 1),
            CacheKey("s1", "daily_news", (("x", "1"),), 1),
            CacheKey("s1", "daily_news", (), 2),
        ]
        for key in keys:
            await self.cache.get(key, fetch)
        self.assertEqual(calls, 5)
        self.assertNotIn("fictional-token", repr(keys[0]))

    async def test_lru_capacity_and_expired_cleanup(self) -> None:
        async def fetch(value):
            return value

        k1 = CacheKey("s", "t", (), 1)
        k2 = CacheKey("s", "t", (("p", "2"),), 1)
        k3 = CacheKey("s", "t", (("p", "3"),), 1)
        await self.cache.get(k1, lambda: fetch("one"))
        await self.cache.get(k2, lambda: fetch("two"))
        # Refresh k1 so k2 is the least recently used entry.
        await self.cache.get(k1, lambda: fetch("one"))
        await self.cache.get(k3, lambda: fetch("three"))
        self.assertEqual(self.cache.size, 2)
        calls = 0
        async def count():
            nonlocal calls
            calls += 1
            return "again"
        await self.cache.get(k2, count)
        self.assertEqual(calls, 1)

        self.clock.advance(11)
        await self.cache.get(CacheKey("s", "t", (("p", "4"),), 1), count)
        self.assertLessEqual(self.cache.size, 2)

    async def test_same_key_inflight_is_merged(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared"

        first = asyncio.create_task(self.cache.get(key, fetch))
        second = asyncio.create_task(self.cache.get(key, fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        release.set()
        one, two = await asyncio.gather(first, second)
        self.assertEqual(calls, 1)
        self.assertFalse(one.hit)
        self.assertFalse(two.hit)
        self.assertEqual(one.snapshot, "shared")
        self.assertEqual(two.snapshot, "shared")

    async def test_force_refresh_joins_existing_flight_without_old_hit(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared"

        normal = asyncio.create_task(self.cache.get(key, fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        forced = asyncio.create_task(
            self.cache.get(key, fetch, force_refresh=True),
        )
        release.set()
        first, second = await asyncio.gather(normal, forced)
        self.assertFalse(first.hit)
        self.assertFalse(second.hit)
        self.assertEqual(calls, 1)
        self.assertEqual(first.snapshot, "shared")

    async def test_cancelled_cleanup_cannot_be_joined_by_new_request(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        old_done = asyncio.Event()

        async def old_fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await cleanup_release.wait()
                return "old"
            finally:
                old_done.set()

        waiter = asyncio.create_task(self.cache.get(key, old_fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        old_task = next(iter(self.cache._tasks))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)

        new_started = asyncio.Event()
        new_release = asyncio.Event()

        async def new_fetch():
            new_started.set()
            await new_release.wait()
            return "new"

        new_waiter = asyncio.create_task(self.cache.get(key, new_fetch))
        await asyncio.wait_for(new_started.wait(), timeout=1)
        new_inflight = self.cache._inflight[key]
        self.assertIsNot(new_inflight.task, old_task)

        # Releasing the old cleanup must not remove the new in-flight record.
        cleanup_release.set()
        await asyncio.wait_for(old_done.wait(), timeout=1)
        await asyncio.gather(old_task, return_exceptions=True)
        self.assertIs(self.cache._inflight[key], new_inflight)
        self.assertFalse(new_waiter.done())

        new_release.set()
        result = await asyncio.wait_for(new_waiter, timeout=1)
        self.assertFalse(result.hit)
        self.assertEqual(result.snapshot, "new")
        hit = await self.cache.get(key, new_fetch)
        self.assertTrue(hit.hit)
        self.assertEqual(hit.snapshot, "new")

    async def test_cancelled_cleanup_cannot_overwrite_new_cache(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()

        async def old_fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await cleanup_release.wait()
                return "old"

        waiter = asyncio.create_task(self.cache.get(key, old_fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        old_task = next(iter(self.cache._tasks))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)

        async def new_fetch():
            return "new"

        new_result = await self.cache.get(key, new_fetch)
        self.assertEqual(new_result.snapshot, "new")
        cleanup_release.set()
        await asyncio.gather(old_task, return_exceptions=True)
        hit = await self.cache.get(key, new_fetch)
        self.assertTrue(hit.hit)
        self.assertEqual(hit.snapshot, "new")

    async def test_expired_last_waiter_uses_same_new_fetch_path(self) -> None:
        clock = FakeClock()
        cache = ContentCache(
            ttl_seconds=10,
            max_entries=2,
            monotonic=clock.monotonic,
            wall_clock=clock.now,
            wait_timeout_seconds=0.01,
        )
        await cache.open()
        self.addAsyncCleanup(cache.close)
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()

        async def old_fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await cleanup_release.wait()
                return "old"

        with self.assertRaises(ContentCacheWaitTimeoutError):
            await cache.get(key, old_fetch)
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        old_task = next(iter(cache._tasks))

        async def new_fetch():
            return "new"

        result = await cache.get(key, new_fetch)
        self.assertEqual(result.snapshot, "new")
        self.assertTrue((await cache.get(key, new_fetch)).hit)
        cleanup_release.set()
        await asyncio.gather(old_task, return_exceptions=True)

    async def test_close_waits_for_cancelled_cleanup_task(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()

        async def old_fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await cleanup_release.wait()
                return "old"

        waiter = asyncio.create_task(self.cache.get(key, old_fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        old_task = next(iter(self.cache._tasks))

        close_task = asyncio.create_task(self.cache.close())
        await asyncio.sleep(0)
        self.assertFalse(close_task.done())
        cleanup_release.set()
        await asyncio.wait_for(close_task, timeout=1)
        await asyncio.gather(old_task, return_exceptions=True)
        self.assertFalse(self.cache.is_open)

    async def test_day_token_is_captured_before_fetch(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        day = {"value": "2026-10-26"}
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return calls

        task = asyncio.create_task(
            self.cache.get(key, fetch, day_token=lambda: day["value"]),
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        day["value"] = "2026-10-27"
        release.set()
        first = await task
        self.assertFalse(first.hit)
        second = await self.cache.get(
            key,
            fetch,
            day_token=lambda: day["value"],
        )
        self.assertFalse(second.hit)
        self.assertEqual(calls, 2)

    async def test_waiting_cancel_does_not_cancel_shared_fetch(self) -> None:
        key = CacheKey("site", "zhihu_hot", (), 1)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared"

        first = asyncio.create_task(self.cache.get(key, fetch))
        second = asyncio.create_task(self.cache.get(key, fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        result = await asyncio.wait_for(second, timeout=1)
        self.assertFalse(result.hit)
        self.assertEqual(calls, 1)

    async def test_last_waiter_cancel_cleans_up_flight(self) -> None:
        key = CacheKey("site", "douyin_hot", (), 1)
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "shared"

        waiter = asyncio.create_task(self.cache.get(key, fetch))
        await asyncio.wait_for(started.wait(), timeout=1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0)
        self.assertEqual(self.cache.size, 0)
        release.set()

        async def fresh():
            nonlocal calls
            calls += 1
            return "fresh"
        self.assertEqual((await self.cache.get(key, fresh)).snapshot, "fresh")
        self.assertEqual(calls, 2)

    async def test_wait_timeout_is_bounded(self) -> None:
        clock = FakeClock()
        cache = ContentCache(
            ttl_seconds=10,
            max_entries=2,
            monotonic=clock.monotonic,
            wall_clock=clock.now,
            wait_timeout_seconds=0.01,
        )
        await cache.open()
        self.addAsyncCleanup(cache.close)
        started = asyncio.Event()

        async def fetch():
            started.set()
            await asyncio.Event().wait()

        with self.assertRaises(ContentCacheWaitTimeoutError):
            await cache.get(CacheKey("site", "toutiao_hot", (), 1), fetch)
        self.assertTrue(started.is_set())

    async def test_shared_failure_is_not_cached_and_can_retry(self) -> None:
        key = CacheKey("site", "bilibili_hot", (), 1)
        calls = 0

        async def failing():
            nonlocal calls
            calls += 1
            raise RuntimeError("shared failure")

        with self.assertRaises(RuntimeError):
            await self.cache.get(key, failing)
        async def success():
            nonlocal calls
            calls += 1
            return "ok"
        result = await self.cache.get(key, success)
        self.assertFalse(result.hit)
        self.assertEqual(result.snapshot, "ok")
        self.assertEqual(calls, 2)

    async def test_close_cancels_waits_clears_and_reopen_is_clean(self) -> None:
        key = CacheKey("site", "daily_news", (), 1)
        started = asyncio.Event()

        async def blocked():
            started.set()
            await asyncio.Event().wait()

        waiter = asyncio.create_task(self.cache.get(key, blocked))
        await asyncio.wait_for(started.wait(), timeout=1)
        await self.cache.close()
        with self.assertRaises(ContentCacheClosedError):
            await waiter
        with self.assertRaises(ContentCacheClosedError):
            await self.cache.get(key, blocked)
        self.assertFalse(self.cache.is_open)
        self.assertEqual(self.cache.size, 0)
        await self.cache.close()
        await self.cache.open()
        async def fresh():
            return "fresh"
        result = await self.cache.get(key, fresh)
        self.assertEqual(result.snapshot, "fresh")
