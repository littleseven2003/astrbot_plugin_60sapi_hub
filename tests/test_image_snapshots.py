from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from core.image_snapshots import (
    ImageSnapshotClosedError,
    ImageSnapshotStore,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.wall = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)

    def monotonic(self) -> float:
        return self.value

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.value += seconds
        self.wall += timedelta(seconds=seconds)


def put(store: ImageSnapshotStore, name="组合", owner="alice"):
    return store.put(
        owner=owner,
        profile_id="profile-1",
        profile_revision=2,
        site_id="site-1",
        site_revision=3,
        data={
            "profile_name": name,
            "site_name": "站点",
            "status": "complete",
            "sections": [{"label": "每日新闻", "items": []}],
        },
    )


class ImageSnapshotStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.store = ImageSnapshotStore(
            ttl_seconds=10,
            max_snapshots=2,
            max_total_bytes=3000,
            max_snapshot_bytes=2000,
            monotonic=self.clock.monotonic,
            wall_clock=self.clock.now,
        )

    def test_put_get_owner_and_ttl(self) -> None:
        snapshot = put(self.store)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.owner, "alice")
        self.assertEqual(snapshot.profile_id, "profile-1")
        self.assertEqual(snapshot.profile_revision, 2)
        self.assertEqual(snapshot.site_revision, 3)
        self.assertTrue(snapshot.expires_at)
        self.assertIs(self.store.get(snapshot.id), snapshot)
        self.clock.advance(9.999)
        self.assertIsNotNone(self.store.get(snapshot.id))
        self.clock.advance(0.001)
        self.assertIsNone(self.store.get(snapshot.id))
        self.assertEqual(self.store.size, 0)
        self.assertEqual(self.store.total_bytes, 0)

    def test_oversize_and_capacity_eviction(self) -> None:
        huge = self.store.put(
            owner="alice",
            profile_id="p",
            profile_revision=1,
            site_id="s",
            site_revision=1,
            data={"sections": [{"text": "x" * 5000}]},
        )
        self.assertIsNone(huge)
        first = put(self.store, "one")
        second = put(self.store, "two")
        assert first is not None and second is not None
        self.assertEqual(self.store.size, 2)
        # Touch first so second is least recently used.
        self.store.get(first.id)
        third = put(self.store, "three")
        assert third is not None
        self.assertEqual(self.store.size, 2)
        self.assertIsNotNone(self.store.get(first.id))
        self.assertIsNone(self.store.get(second.id))
        self.assertIsNotNone(self.store.get(third.id))

    def test_total_byte_limit_and_lru(self) -> None:
        store = ImageSnapshotStore(
            ttl_seconds=10,
            max_snapshots=32,
            max_total_bytes=1200,
            max_snapshot_bytes=2000,
            monotonic=self.clock.monotonic,
            wall_clock=self.clock.now,
        )
        snapshots = [put(store, "x" * 300) for _ in range(3)]
        self.assertTrue(any(s is None for s in snapshots) or store.total_bytes <= 1200)
        self.assertLessEqual(store.total_bytes, 1200)
        self.assertLessEqual(store.size, 32)

    def test_close_rejects_get_and_put_and_clears(self) -> None:
        snapshot = put(self.store)
        assert snapshot is not None
        self.store.close()
        self.assertFalse(self.store.is_open)
        self.assertEqual(self.store.size, 0)
        with self.assertRaises(ImageSnapshotClosedError):
            self.store.get(snapshot.id)
        with self.assertRaises(ImageSnapshotClosedError):
            put(self.store)
