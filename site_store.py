"""站点配置的 SQLite 持久化。

本模块只负责存储与 revision 并发控制，字段语义和输入校验由 sites 模块负责。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1

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


class SiteStore:
    """单个 SQLite 数据库中的站点表。

    所有操作都在同一个 asyncio.Lock 下执行，避免同一插件实例内并发关闭连接
    或交错写入。数据库访问通过 aiosqlite 的工作线程完成。
    """

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._connection: aiosqlite.Connection | None = None
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

                async with connection.execute("PRAGMA user_version") as cursor:
                    row = await cursor.fetchone()
                version = int(row[0]) if row is not None else 0

                if version == 0:
                    try:
                        await connection.execute(_CREATE_SITES_TABLE)
                        await connection.execute(
                            f"PRAGMA user_version = {SCHEMA_VERSION}",
                        )
                        await connection.commit()
                    except Exception:
                        await self._rollback_quietly(connection)
                        raise
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
            try:
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
                await connection.commit()
            except Exception as exc:
                await self._rollback_quietly(connection)
                raise SiteStoreError("保存站点失败") from exc

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
            try:
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
                    await self._rollback_quietly(connection)
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM sites WHERE id = ?",
                        (site_id,),
                    )
                    if row is None:
                        raise SiteRecordNotFoundError(site_id)
                    raise SiteRevisionConflictError(int(row["revision"]))
                await connection.commit()
            except (SiteRecordNotFoundError, SiteRevisionConflictError):
                raise
            except Exception as exc:
                await self._rollback_quietly(connection)
                raise SiteStoreError("更新站点失败") from exc

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
            try:
                cursor = await connection.execute(
                    "DELETE FROM sites WHERE id = ? AND revision = ?",
                    (site_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    await self._rollback_quietly(connection)
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM sites WHERE id = ?",
                        (site_id,),
                    )
                    if row is None:
                        raise SiteRecordNotFoundError(site_id)
                    raise SiteRevisionConflictError(int(row["revision"]))
                await connection.commit()
            except (SiteRecordNotFoundError, SiteRevisionConflictError):
                raise
            except Exception as exc:
                await self._rollback_quietly(connection)
                raise SiteStoreError("删除站点失败") from exc

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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
