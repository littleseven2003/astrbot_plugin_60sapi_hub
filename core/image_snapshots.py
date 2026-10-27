"""组合图片生成的短期内存快照。

快照只保存已经由组合预览服务验证并截取的数据，不保存 HTML、URL、文件路径
或任意前端内容。快照有过期时间、数量上限和总字节上限；图片生成不读取后来
的缓存或重新请求上游。
"""

from __future__ import annotations

import json
import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_SNAPSHOT_TTL_SECONDS = 300.0
DEFAULT_MAX_SNAPSHOTS = 32
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


class ImageSnapshotError(Exception):
    """快照存储错误基类。"""


class ImageSnapshotClosedError(ImageSnapshotError):
    """快照存储已关闭。"""


@dataclass(frozen=True, slots=True)
class ImageSnapshot:
    id: str
    owner: str
    profile_id: str
    profile_revision: int
    site_id: str
    site_revision: int
    data: dict[str, Any]
    created_at: str
    expires_at: str
    size_bytes: int
    expires_monotonic: float


class ImageSnapshotStore:
    """带 TTL/LRU 的进程内快照存储。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_SNAPSHOT_TTL_SECONDS,
        max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_snapshots < 1:
            raise ValueError("max_snapshots must be at least 1")
        if max_total_bytes < 1 or max_snapshot_bytes < 1:
            raise ValueError("byte limits must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_snapshots = max_snapshots
        self._max_total_bytes = max_total_bytes
        self._max_snapshot_bytes = max_snapshot_bytes
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (
            lambda: datetime.now(timezone.utc)
        )
        self._snapshots: OrderedDict[str, ImageSnapshot] = OrderedDict()
        self._total_bytes = 0
        self._closed = False

    @property
    def is_open(self) -> bool:
        return not self._closed

    @property
    def size(self) -> int:
        return len(self._snapshots)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def put(
        self,
        *,
        owner: str,
        profile_id: str,
        profile_revision: int,
        site_id: str,
        site_revision: int,
        data: dict[str, Any],
    ) -> ImageSnapshot | None:
        if self._closed:
            raise ImageSnapshotClosedError("图片快照存储当前不可用。")
        try:
            serialized = json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        size = len(serialized)
        if size > self._max_snapshot_bytes:
            return None
        now = self._monotonic()
        self._prune_expired(now)
        while (
            self._snapshots
            and (
                len(self._snapshots) >= self._max_snapshots
                or self._total_bytes + size > self._max_total_bytes
            )
        ):
            self._evict_oldest()
        if size > self._max_total_bytes:
            return None

        snapshot_id = secrets.token_urlsafe(24)
        created_at = self._wall_clock().astimezone(timezone.utc).isoformat(
            timespec="seconds",
        )
        expires_at = (
            self._wall_clock() + timedelta(seconds=self._ttl_seconds)
        ).astimezone(timezone.utc).isoformat(timespec="seconds")
        snapshot = ImageSnapshot(
            id=snapshot_id,
            owner=owner,
            profile_id=profile_id,
            profile_revision=profile_revision,
            site_id=site_id,
            site_revision=site_revision,
            data=json.loads(serialized),
            created_at=created_at,
            expires_at=expires_at,
            size_bytes=size,
            expires_monotonic=now + self._ttl_seconds,
        )
        self._snapshots[snapshot_id] = snapshot
        self._total_bytes += size
        return snapshot

    def get(self, snapshot_id: str) -> ImageSnapshot | None:
        if self._closed:
            raise ImageSnapshotClosedError("图片快照存储当前不可用。")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            return None
        now = self._monotonic()
        self._prune_expired(now)
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            return None
        self._snapshots.move_to_end(snapshot_id)
        return snapshot

    def close(self) -> None:
        self._closed = True
        self._snapshots.clear()
        self._total_bytes = 0

    def _prune_expired(self, now: float) -> None:
        expired = [
            snapshot_id
            for snapshot_id, snapshot in self._snapshots.items()
            if snapshot.expires_monotonic <= now
        ]
        for snapshot_id in expired:
            snapshot = self._snapshots.pop(snapshot_id, None)
            if snapshot is not None:
                self._total_bytes -= snapshot.size_bytes

    def _evict_oldest(self) -> None:
        _snapshot_id, snapshot = self._snapshots.popitem(last=False)
        self._total_bytes -= snapshot.size_bytes


__all__ = [
    "DEFAULT_MAX_SNAPSHOT_BYTES",
    "DEFAULT_MAX_SNAPSHOTS",
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_SNAPSHOT_TTL_SECONDS",
    "ImageSnapshot",
    "ImageSnapshotClosedError",
    "ImageSnapshotError",
    "ImageSnapshotStore",
]
