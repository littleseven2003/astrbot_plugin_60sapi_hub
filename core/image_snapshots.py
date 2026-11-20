"""组合图片生成的短期内存快照。

快照只保存已经由组合预览服务验证并截取的数据，不保存 HTML、URL、文件路径
或任意前端内容。快照有过期时间、数量上限和总字节上限；图片生成不读取后来
的缓存或重新请求上游。

栏目长图按「每个栏目一张」生成，图片字节不进入快照：快照字节只衡量预览数据
JSON，渲染结果合计大小由 DEFAULT_MAX_RENDER_OUTPUT_BYTES 约束，单图高度、单图
字节和栏目数由渲染器的 ImageRenderLimits 约束。
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
# 一次图片渲染返回的全部栏目长图合计字节上限（PNG 原始字节，不含 base64）。
DEFAULT_MAX_RENDER_OUTPUT_BYTES = 8 * 1024 * 1024


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
    # 单站或拼合内容用到的全部站点及版本；顺序即栏目分配顺序。
    sites: tuple[tuple[str, int], ...]
    data: dict[str, Any]
    created_at: str
    expires_at: str
    size_bytes: int
    expires_monotonic: float

    @property
    def site_id(self) -> str:
        """兼容旧调用：返回第一个来源站点，无来源时为空串。"""
        return self.sites[0][0] if self.sites else ""

    @property
    def site_revision(self) -> int:
        """兼容旧调用：返回第一个来源站点的版本。"""
        return self.sites[0][1] if self.sites else 0


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
        # 快照用途：已保存任务（task）与表单草稿（draft）。草稿淘汰只清理
        # 草稿自己，绝不挤掉已保存任务的快照；总量上限仍由同一个存储约束。
        self._scopes: dict[str, str] = {}
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
        site_id: str = "",
        site_revision: int = 0,
        data: dict[str, Any] | None = None,
        scope: str = "task",
        sites: tuple[tuple[str, int], ...] | None = None,
    ) -> ImageSnapshot | None:
        if data is None:
            raise ValueError("data")
        if sites is None:
            sites = ((site_id, site_revision),) if site_id else ()
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
        normalized_scope = "draft" if scope == "draft" else "task"
        while self._snapshots and (
            len(self._snapshots) >= self._max_snapshots
            or self._total_bytes + size > self._max_total_bytes
        ):
            victim = self._oldest_evictable(normalized_scope)
            if victim is None:
                # 草稿不得淘汰已保存任务的快照；存储已满时放弃本次草稿快照，
                # 预览内容仍可用，只是暂时不能直接出图。
                return None
            self._remove(victim)
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
            sites=tuple(sites),
            data=json.loads(serialized),
            created_at=created_at,
            expires_at=expires_at,
            size_bytes=size,
            expires_monotonic=now + self._ttl_seconds,
        )
        self._snapshots[snapshot_id] = snapshot
        self._scopes[snapshot_id] = normalized_scope
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
        self._scopes.clear()
        self._total_bytes = 0

    def _prune_expired(self, now: float) -> None:
        expired = [
            snapshot_id
            for snapshot_id, snapshot in self._snapshots.items()
            if snapshot.expires_monotonic <= now
        ]
        for snapshot_id in expired:
            self._remove(snapshot_id)

    def _oldest_evictable(self, scope: str) -> str | None:
        """草稿只能淘汰草稿；任务快照可以按最旧顺序淘汰任意用途。"""
        for snapshot_id in self._snapshots:
            if scope == "task" or self._scopes.get(snapshot_id) == "draft":
                return snapshot_id
        return None

    def _remove(self, snapshot_id: str) -> None:
        snapshot = self._snapshots.pop(snapshot_id, None)
        self._scopes.pop(snapshot_id, None)
        if snapshot is not None:
            self._total_bytes -= snapshot.size_bytes


__all__ = [
    "DEFAULT_MAX_RENDER_OUTPUT_BYTES",
    "DEFAULT_MAX_SNAPSHOT_BYTES",
    "DEFAULT_MAX_SNAPSHOTS",
    "DEFAULT_MAX_TOTAL_BYTES",
    "DEFAULT_SNAPSHOT_TTL_SECONDS",
    "ImageSnapshot",
    "ImageSnapshotClosedError",
    "ImageSnapshotError",
    "ImageSnapshotStore",
]
