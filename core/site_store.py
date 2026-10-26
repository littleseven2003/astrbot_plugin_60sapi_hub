"""站点配置的 SQLite 持久化。

本模块只负责存储与 revision 并发控制，字段语义和输入校验由 sites 模块负责。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 2

_CREATE_SITES_TABLE = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    timeout_seconds REAL NOT NULL CHECK (timeout_seconds >= 1 AND timeout_seconds <= 120),
    bearer_token TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_PROFILES_TABLE = """
CREATE TABLE IF NOT EXISTS content_profiles (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_PROFILE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS content_profile_items (
    profile_id TEXT NOT NULL REFERENCES content_profiles(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    content_type TEXT NOT NULL CHECK (
        content_type IN (
            'daily_news', 'bilibili_hot', 'zhihu_hot',
            'douyin_hot', 'toutiao_hot'
        )
    ),
    params_json TEXT NOT NULL,
    limit_count INTEGER CHECK (
        limit_count IS NULL
        OR (limit_count >= 1 AND limit_count <= 1000)
    ),
    PRIMARY KEY (profile_id, position),
    UNIQUE (profile_id, content_type)
)
"""

_CREATE_PROFILE_SITE_INDEX = """
CREATE INDEX IF NOT EXISTS content_profiles_site_id
ON content_profiles (site_id)
"""


class SiteStoreError(Exception):
    """站点存储错误基类。"""


class SiteStoreClosedError(SiteStoreError):
    """存储尚未打开时执行操作。"""


class SiteRecordNotFoundError(SiteStoreError):
    """按 ID 查找站点失败。"""

    def __init__(self, site_id: str) -> None:
        self.site_id = site_id
        super().__init__(f"站点 {site_id} 不存在")


class SiteRevisionConflictError(SiteStoreError):
    """写入时的 revision 与数据库不一致。"""

    def __init__(self, actual_revision: int | None) -> None:
        self.actual_revision = actual_revision
        super().__init__("站点 revision 冲突")


class SiteReferencedError(SiteStoreError):
    """站点仍被内容组合引用，不能删除。"""

    def __init__(self, profile_names: tuple[str, ...]) -> None:
        self.profile_names = profile_names
        super().__init__("站点仍被内容组合引用")


class ProfileRecordNotFoundError(SiteStoreError):
    """按 ID 查找内容组合失败。"""

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        super().__init__(f"内容组合 {profile_id} 不存在")


class ProfileRevisionConflictError(SiteStoreError):
    """内容组合写入 revision 冲突。"""

    def __init__(self, actual_revision: int | None) -> None:
        self.actual_revision = actual_revision
        super().__init__("内容组合 revision 冲突")


@dataclass(frozen=True, slots=True)
class StoredSite:
    """存储层的站点记录；bearer_token 不出现在调试输出中。"""

    id: str
    name: str
    base_url: str
    enabled: bool
    timeout_seconds: float
    bearer_token: str | None = field(default=None, repr=False)
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class StoredProfileItem:
    content_type: str
    params_json: str = "{}"
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class StoredProfile:
    id: str
    name: str
    site_id: str
    items: tuple[StoredProfileItem, ...]
    revision: int
    created_at: str
    updated_at: str


class SiteStore:
    """单个 SQLite 数据库中的站点表。

    所有操作都在同一个 asyncio.Lock 下执行，避免同一插件实例内并发关闭连接
    或交错写入。数据库访问通过 aiosqlite 的工作线程完成。
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._connection: aiosqlite.Connection | None = None
        self._connection_broken = False
        self._lock = asyncio.Lock()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    async def open(self) -> None:
        """创建父目录、初始化首次 schema，并打开连接。"""
        async with self._lock:
            if self._connection_broken:
                raise SiteStoreError(
                    "站点数据库连接状态不确定，请重新创建存储实例",
                )
            if self._connection is not None:
                return

            connection: aiosqlite.Connection | None = None
            try:
                await asyncio.to_thread(
                    self._database_path.parent.mkdir,
                    parents=True,
                    exist_ok=True,
                )
                connection = await aiosqlite.connect(self._database_path)
                connection.row_factory = aiosqlite.Row
                async with connection.execute("PRAGMA busy_timeout = 5000"):
                    pass
                await connection.execute("PRAGMA foreign_keys = ON")

                async with connection.execute("PRAGMA user_version") as cursor:
                    row = await cursor.fetchone()
                version = int(row[0]) if row is not None else 0

                if version in (0, 1):
                    await self._migrate_schema(connection, version)
                elif version != SCHEMA_VERSION:
                    raise SiteStoreError(
                        f"不支持的站点数据库 schema 版本：{version}",
                    )

                self._connection = connection
                connection = None
            except asyncio.CancelledError:
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass
                self._connection = None
                raise
            except Exception as exc:
                if connection is not None:
                    try:
                        await connection.close()
                    except Exception:
                        pass
                self._connection = None
                if isinstance(exc, SiteStoreError):
                    raise
                raise SiteStoreError("无法初始化站点数据库") from exc

    async def _migrate_schema(
        self,
        connection: aiosqlite.Connection,
        version: int,
    ) -> None:
        """在同一事务中创建/升级 schema，失败保留旧版本数据。"""
        try:
            await connection.execute("BEGIN IMMEDIATE")
            if version == 0:
                await connection.execute(_CREATE_SITES_TABLE)
            await connection.execute(_CREATE_PROFILES_TABLE)
            await connection.execute(_CREATE_PROFILE_ITEMS_TABLE)
            await connection.execute(_CREATE_PROFILE_SITE_INDEX)
            await connection.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION}",
            )
            await connection.commit()
        except BaseException:
            await self._rollback_quietly(connection)
            raise

    async def close(self) -> None:
        """关闭连接；可重复调用。"""
        async with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await connection.close()

    async def create_site(
        self,
        *,
        site_id: str,
        name: str,
        base_url: str,
        enabled: bool,
        timeout_seconds: float,
        bearer_token: str | None,
    ) -> StoredSite:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                await connection.execute(
                    """
                    INSERT INTO sites (
                        id, name, base_url, enabled, timeout_seconds,
                        bearer_token, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        site_id,
                        name,
                        base_url,
                        int(enabled),
                        timeout_seconds,
                        bearer_token,
                        now,
                        now,
                    ),
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="保存站点失败",
            )
            return StoredSite(
                id=site_id,
                name=name,
                base_url=base_url,
                enabled=enabled,
                timeout_seconds=timeout_seconds,
                bearer_token=bearer_token,
                revision=1,
                created_at=now,
                updated_at=now,
            )

    async def list_sites(self) -> list[StoredSite]:
        async with self._lock:
            connection = self._require_connection()
            try:
                async with connection.execute(
                    """
                    SELECT id, name, base_url, enabled, timeout_seconds,
                           bearer_token, revision, created_at, updated_at
                    FROM sites
                    ORDER BY created_at, id
                    """,
                ) as cursor:
                    rows = await cursor.fetchall()
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取站点列表失败") from exc
            return [_row_to_site(row) for row in rows]

    async def get_site(self, site_id: str) -> StoredSite:
        async with self._lock:
            try:
                row = await self._fetchone_unlocked(
                    """
                    SELECT id, name, base_url, enabled, timeout_seconds,
                           bearer_token, revision, created_at, updated_at
                    FROM sites
                    WHERE id = ?
                    """,
                    (site_id,),
                )
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取站点失败") from exc
            if row is None:
                raise SiteRecordNotFoundError(site_id)
            return _row_to_site(row)

    async def update_site(
        self,
        site_id: str,
        *,
        name: str,
        base_url: str,
        enabled: bool,
        timeout_seconds: float,
        bearer_token: str | None,
        expected_revision: int,
    ) -> StoredSite:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                cursor = await connection.execute(
                    """
                    UPDATE sites
                    SET name = ?, base_url = ?, enabled = ?, timeout_seconds = ?,
                        bearer_token = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        name,
                        base_url,
                        int(enabled),
                        timeout_seconds,
                        bearer_token,
                        now,
                        site_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM sites WHERE id = ?",
                        (site_id,),
                    )
                    if row is None:
                        raise SiteRecordNotFoundError(site_id)
                    raise SiteRevisionConflictError(int(row["revision"]))

            await self._commit_write(
                connection,
                perform,
                failure_message="更新站点失败",
            )

            try:
                row = await self._fetchone_unlocked(
                    """
                    SELECT id, name, base_url, enabled, timeout_seconds,
                           bearer_token, revision, created_at, updated_at
                    FROM sites
                    WHERE id = ?
                    """,
                    (site_id,),
                )
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("更新站点失败") from exc
            if row is None:
                raise SiteRecordNotFoundError(site_id)
            return _row_to_site(row)

    async def delete_site(self, site_id: str, *, expected_revision: int) -> None:
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                cursor = await connection.execute(
                    "SELECT name FROM content_profiles WHERE site_id = ? "
                    "ORDER BY created_at, id",
                    (site_id,),
                )
                names = tuple(row["name"] for row in await cursor.fetchall())
                if names:
                    raise SiteReferencedError(names)
                cursor = await connection.execute(
                    "DELETE FROM sites WHERE id = ? AND revision = ?",
                    (site_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM sites WHERE id = ?",
                        (site_id,),
                    )
                    if row is None:
                        raise SiteRecordNotFoundError(site_id)
                    raise SiteRevisionConflictError(int(row["revision"]))

            await self._commit_write(
                connection,
                perform,
                failure_message="删除站点失败",
            )

    async def create_profile(
        self,
        *,
        profile_id: str,
        name: str,
        site_id: str,
        items: tuple[StoredProfileItem, ...],
    ) -> StoredProfile:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                await connection.execute(
                    """
                    INSERT INTO content_profiles (
                        id, name, site_id, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (profile_id, name, site_id, now, now),
                )
                await self._insert_profile_items(
                    connection,
                    profile_id,
                    items,
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="保存内容组合失败",
            )
            return StoredProfile(
                id=profile_id,
                name=name,
                site_id=site_id,
                items=items,
                revision=1,
                created_at=now,
                updated_at=now,
            )

    async def list_profiles(self) -> list[StoredProfile]:
        async with self._lock:
            connection = self._require_connection()
            try:
                async with connection.execute(
                    """
                    SELECT id, name, site_id, revision, created_at, updated_at
                    FROM content_profiles
                    ORDER BY created_at, id
                    """,
                ) as cursor:
                    rows = await cursor.fetchall()
                items_by_profile = await self._load_profile_items(
                    tuple(row["id"] for row in rows),
                )
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取内容组合列表失败") from exc
            return [
                _row_to_profile(
                    row,
                    items_by_profile.get(row["id"], ()),
                )
                for row in rows
            ]

    async def get_profile(self, profile_id: str) -> StoredProfile:
        async with self._lock:
            try:
                row = await self._fetchone_unlocked(
                    """
                    SELECT id, name, site_id, revision, created_at, updated_at
                    FROM content_profiles
                    WHERE id = ?
                    """,
                    (profile_id,),
                )
                if row is None:
                    raise ProfileRecordNotFoundError(profile_id)
                items = await self._load_profile_items((profile_id,))
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取内容组合失败") from exc
            return _row_to_profile(row, items.get(profile_id, ()))

    async def update_profile(
        self,
        profile_id: str,
        *,
        name: str,
        site_id: str,
        items: tuple[StoredProfileItem, ...],
        expected_revision: int,
    ) -> StoredProfile:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                cursor = await connection.execute(
                    """
                    UPDATE content_profiles
                    SET name = ?, site_id = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (name, site_id, now, profile_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM content_profiles WHERE id = ?",
                        (profile_id,),
                    )
                    if row is None:
                        raise ProfileRecordNotFoundError(profile_id)
                    raise ProfileRevisionConflictError(int(row["revision"]))
                await connection.execute(
                    "DELETE FROM content_profile_items WHERE profile_id = ?",
                    (profile_id,),
                )
                await self._insert_profile_items(
                    connection,
                    profile_id,
                    items,
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="更新内容组合失败",
            )
            try:
                row = await self._fetchone_unlocked(
                    """
                    SELECT id, name, site_id, revision, created_at, updated_at
                    FROM content_profiles
                    WHERE id = ?
                    """,
                    (profile_id,),
                )
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("更新内容组合失败") from exc
            if row is None:
                raise ProfileRecordNotFoundError(profile_id)
            return _row_to_profile(row, items)

    async def delete_profile(
        self,
        profile_id: str,
        *,
        expected_revision: int,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                cursor = await connection.execute(
                    """
                    DELETE FROM content_profiles
                    WHERE id = ? AND revision = ?
                    """,
                    (profile_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM content_profiles WHERE id = ?",
                        (profile_id,),
                    )
                    if row is None:
                        raise ProfileRecordNotFoundError(profile_id)
                    raise ProfileRevisionConflictError(int(row["revision"]))

            await self._commit_write(
                connection,
                perform,
                failure_message="删除内容组合失败",
            )

    async def _insert_profile_items(
        self,
        connection: aiosqlite.Connection,
        profile_id: str,
        items: tuple[StoredProfileItem, ...],
    ) -> None:
        for position, item in enumerate(items):
            await connection.execute(
                """
                INSERT INTO content_profile_items (
                    profile_id, position, content_type,
                    params_json, limit_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    profile_id,
                    position,
                    item.content_type,
                    item.params_json,
                    item.limit,
                ),
            )

    async def _load_profile_items(
        self,
        profile_ids: tuple[str, ...],
    ) -> dict[str, tuple[StoredProfileItem, ...]]:
        if not profile_ids:
            return {}
        placeholders = ",".join("?" for _ in profile_ids)
        async with self._require_connection().execute(
            f"""
            SELECT profile_id, content_type, params_json, limit_count
            FROM content_profile_items
            WHERE profile_id IN ({placeholders})
            ORDER BY profile_id, position
            """,
            profile_ids,
        ) as cursor:
            rows = await cursor.fetchall()
        grouped: dict[str, list[StoredProfileItem]] = {}
        for row in rows:
            grouped.setdefault(row["profile_id"], []).append(
                _row_to_profile_item(row),
            )
        return {
            profile_id: tuple(items)
            for profile_id, items in grouped.items()
        }

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise SiteStoreClosedError("站点数据库尚未打开")
        return self._connection

    async def _fetchone_unlocked(
        self,
        sql: str,
        params: tuple[object, ...],
    ) -> aiosqlite.Row | None:
        connection = self._require_connection()
        async with connection.execute(sql, params) as cursor:
            return await cursor.fetchone()

    @staticmethod
    async def _rollback_quietly(connection: aiosqlite.Connection) -> None:
        try:
            await connection.rollback()
        except Exception:
            pass

    async def _commit_write(
        self,
        connection: aiosqlite.Connection,
        operation: Callable[[], Awaitable[None]],
        *,
        failure_message: str,
    ) -> None:
        commit_task: asyncio.Task[None] | None = None
        try:
            await operation()
            commit_task = asyncio.create_task(connection.commit())
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            await self._cleanup_cancelled_write(connection, commit_task)
            raise
        except (
            SiteRecordNotFoundError,
            SiteRevisionConflictError,
            SiteReferencedError,
            ProfileRecordNotFoundError,
            ProfileRevisionConflictError,
        ):
            await self._rollback_for_error(connection)
            raise
        except SiteStoreClosedError:
            raise
        except Exception as exc:
            await self._rollback_for_error(connection)
            raise SiteStoreError(failure_message) from exc

    async def _cleanup_cancelled_write(
        self,
        connection: aiosqlite.Connection,
        commit_task: asyncio.Task[None] | None,
    ) -> None:
        if commit_task is not None:
            try:
                await self._wait_for_cleanup(commit_task)
            except asyncio.CancelledError:
                await self._discard_connection(connection)
                return
            except Exception:
                await self._rollback_after_cancel(connection)
                return
            # COMMIT completed. A cancelled caller does not imply rollback;
            # the transaction must not be reported as undone.
            return
        await self._rollback_after_cancel(connection)

    async def _rollback_after_cancel(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        rollback_task = asyncio.create_task(connection.rollback())
        try:
            await self._wait_for_cleanup(rollback_task)
        except asyncio.CancelledError:
            await self._discard_connection(connection)
        except Exception:
            await self._discard_connection(connection)

    async def _rollback_for_error(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        rollback_task = asyncio.create_task(connection.rollback())
        try:
            cancelled = await self._wait_for_cleanup(rollback_task)
        except asyncio.CancelledError:
            await self._discard_connection(connection)
            raise
        except Exception:
            await self._discard_connection(connection)
            return
        if cancelled:
            raise asyncio.CancelledError

    async def _wait_for_cleanup(self, task: asyncio.Task[None]) -> bool:
        """Wait for a commit/rollback/close task without losing cancellation."""
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                return cancelled
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True

    async def _discard_connection(
        self,
        connection: aiosqlite.Connection,
    ) -> None:
        self._connection_broken = True
        if self._connection is connection:
            self._connection = None
        try:
            close_task = asyncio.create_task(connection.close())
        except Exception:
            return
        try:
            await self._wait_for_cleanup(close_task)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_profile(
    row: aiosqlite.Row,
    items: tuple[StoredProfileItem, ...],
) -> StoredProfile:
    return StoredProfile(
        id=row["id"],
        name=row["name"],
        site_id=row["site_id"],
        items=items,
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_profile_item(row: aiosqlite.Row) -> StoredProfileItem:
    limit = row["limit_count"]
    return StoredProfileItem(
        content_type=row["content_type"],
        params_json=row["params_json"],
        limit=None if limit is None else int(limit),
    )


def _row_to_site(row: aiosqlite.Row) -> StoredSite:
    return StoredSite(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        enabled=bool(row["enabled"]),
        timeout_seconds=float(row["timeout_seconds"]),
        bearer_token=row["bearer_token"],
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
