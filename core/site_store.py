"""站点配置的 SQLite 持久化。

本模块只负责存储与 revision 并发控制，字段语义和输入校验由 sites 模块负责。
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 19

_CREATE_SITES_TABLE = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    timeout_seconds REAL NOT NULL CHECK (timeout_seconds >= 1 AND timeout_seconds <= 120),
    source_kind TEXT NOT NULL DEFAULT 'public'
        CHECK (source_kind IN ('public', 'self_built')),
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

_CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS push_tasks (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    profile_id TEXT NOT NULL REFERENCES content_profiles(id) ON DELETE RESTRICT,
    output_format TEXT NOT NULL CHECK (
        output_format IN ('text', 'markdown', 'image')
    ),
    image_style TEXT NOT NULL DEFAULT 'default',
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    schedule_kind TEXT,
    schedule_timezone TEXT,
    schedule_time TEXT,
    schedule_weekdays TEXT,
    schedule_start_at TEXT,
    schedule_interval_minutes INTEGER,
    schedule_expression TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_TASK_UMOS_TABLE = """
CREATE TABLE IF NOT EXISTS push_task_umos (
    task_id TEXT NOT NULL REFERENCES push_tasks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    umo TEXT NOT NULL,
    PRIMARY KEY (task_id, position),
    UNIQUE (task_id, umo)
)
"""

_CREATE_TASK_PROFILE_INDEX = """
CREATE INDEX IF NOT EXISTS push_tasks_profile_id
ON push_tasks (profile_id)
"""

_CREATE_TASK_SITES_TABLE = """
CREATE TABLE IF NOT EXISTS push_task_sites (
    task_id TEXT NOT NULL REFERENCES push_tasks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE RESTRICT,
    PRIMARY KEY (task_id, position),
    UNIQUE (task_id, site_id)
)
"""

_CREATE_TASK_SITES_INDEX = """
CREATE INDEX IF NOT EXISTS push_task_sites_site_id
ON push_task_sites (site_id)
"""

_CREATE_TASKS_TABLE_V7 = _CREATE_TASKS_TABLE.replace(
    "CREATE TABLE IF NOT EXISTS push_tasks",
    "CREATE TABLE IF NOT EXISTS push_tasks_v7",
)

_CREATE_SCHEDULE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS push_schedule_state (
    task_id TEXT PRIMARY KEY NOT NULL
        REFERENCES push_tasks(id) ON DELETE CASCADE,
    next_run_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_SCHEDULE_CLAIMS_TABLE = """
CREATE TABLE IF NOT EXISTS push_schedule_claims (
    task_id TEXT NOT NULL REFERENCES push_tasks(id) ON DELETE CASCADE,
    scheduled_for TEXT NOT NULL,
    execution_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('claimed', 'succeeded', 'failed', 'unknown', 'skipped')
    ),
    task_revision INTEGER NOT NULL,
    profile_revision INTEGER NOT NULL,
    site_revision INTEGER NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, scheduled_for)
)
"""

_CREATE_SCHEDULE_SKIPS_TABLE = """
CREATE TABLE IF NOT EXISTS push_schedule_skips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES push_tasks(id) ON DELETE CASCADE,
    scheduled_for TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, scheduled_for, reason)
)
"""

_CREATE_SCHEDULE_STATE_INDEX = """
CREATE INDEX IF NOT EXISTS push_schedule_state_next
ON push_schedule_state (next_run_at)
"""

_CREATE_DELIVERY_EXECUTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS delivery_executions (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL,
    task_name TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    profile_revision INTEGER NOT NULL,
    site_id TEXT NOT NULL,
    site_revision INTEGER NOT NULL,
    output_format TEXT NOT NULL CHECK (
        output_format IN ('text', 'markdown', 'image')
    ),
    owner_token TEXT,
    content_hash TEXT,
    content_status TEXT,
    failed_sections_json TEXT,
    content_sources_json TEXT,
    image_model TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('preparing', 'running', 'success', 'partial',
                   'failed', 'unknown', 'no_targets', 'stopped')
    ),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_DELIVERY_TARGETS_TABLE = """
CREATE TABLE IF NOT EXISTS delivery_targets (
    execution_id TEXT NOT NULL REFERENCES delivery_executions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL CHECK (position >= 0),
    umo TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'sending', 'success', 'partial', 'failed',
                   'unknown', 'skipped')
    ),
    error_code TEXT,
    PRIMARY KEY (execution_id, position),
    UNIQUE (execution_id, umo)
)
"""

_CREATE_DELIVERY_PAGES_TABLE = """
CREATE TABLE IF NOT EXISTS delivery_pages (
    execution_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    page_index INTEGER NOT NULL CHECK (page_index >= 1),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'sending', 'success', 'partial', 'failed',
                   'unknown', 'skipped')
    ),
    error_code TEXT,
    image_generated INTEGER,
    PRIMARY KEY (execution_id, position, page_index),
    FOREIGN KEY (execution_id, position)
        REFERENCES delivery_targets(execution_id, position)
        ON DELETE CASCADE
)
"""

_CREATE_DELIVERY_EXECUTION_INDEX = """
CREATE INDEX IF NOT EXISTS delivery_executions_task_id
ON delivery_executions (task_id)
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


class SiteNameConflictError(SiteStoreError):
    """已有同名站点（去首尾空白、忽略大小写比较）。"""


class SiteRevisionConflictError(SiteStoreError):
    """写入时的 revision 与数据库不一致。"""

    def __init__(self, actual_revision: int | None) -> None:
        self.actual_revision = actual_revision
        super().__init__("站点 revision 冲突")


class SiteReferencedError(SiteStoreError):
    """站点仍是任务候选，不能删除。"""

    def __init__(self, task_names: tuple[str, ...]) -> None:
        self.task_names = task_names
        self.profile_names = task_names
        super().__init__("站点仍是任务的候选站点，不能删除")


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


class ProfileReferencedError(SiteStoreError):
    """内容组合仍被推送任务引用，不能删除。"""

    def __init__(self, task_names: tuple[str, ...]) -> None:
        self.task_names = task_names
        super().__init__("内容组合仍被推送任务引用")


class TaskRecordNotFoundError(SiteStoreError):
    """按 ID 查找推送任务失败。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"推送任务 {task_id} 不存在")


class TaskOperationStaleError(SiteStoreError):
    """操作所属运行周期已失效。"""


class TaskRevisionConflictError(SiteStoreError):
    """推送任务写入 revision 冲突。"""

    def __init__(self, actual_revision: int | None) -> None:
        self.actual_revision = actual_revision
        super().__init__("推送任务 revision 冲突")


class DeliveryExecutionExistsError(SiteStoreError):
    """执行标识已存在。"""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__("投递执行标识已存在")


class DeliveryRecordNotFoundError(SiteStoreError):
    """投递记录不存在。"""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__("投递记录不存在")


@dataclass(frozen=True, slots=True)
class StoredSite:
    """存储层的站点记录；bearer_token 不出现在调试输出中。"""

    id: str
    name: str
    base_url: str
    timeout_seconds: float
    source_kind: str = "public"
    bearer_token: str | None = field(default=None, repr=False)
    revision: int = 1
    created_at: str = ""
    updated_at: str = ""
    # 历史列：站点级启用已移除，保留供旧库追溯与迁移读取，运行时不再过滤。
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class StoredProfileItem:
    content_type: str
    params_json: str = "{}"
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class StoredDeliveryTarget:
    execution_id: str
    position: int
    umo: str
    status: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class StoredDeliveryPage:
    execution_id: str
    position: int
    page_index: int
    status: str
    error_code: str | None
    image_generated: int | None = None


@dataclass(frozen=True, slots=True)
class StoredDeliveryExecution:
    id: str
    task_id: str
    task_name: str
    task_revision: int
    profile_id: str
    profile_revision: int
    site_id: str
    site_revision: int
    output_format: str
    owner_token: str | None
    content_hash: str | None
    content_status: str | None
    failed_sections_json: str | None
    status: str
    error_code: str | None
    created_at: str
    updated_at: str
    image_model: str | None = None
    content_sources_json: str | None = None
    targets: tuple[StoredDeliveryTarget, ...] = ()
    pages: tuple[StoredDeliveryPage, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredTask:
    id: str
    name: str
    profile_id: str
    output_format: str
    umos: tuple[str, ...]
    enabled: bool
    revision: int
    created_at: str
    updated_at: str
    schedule_kind: str | None = None
    schedule_timezone: str | None = None
    schedule_time: str | None = None
    schedule_weekdays: str | None = None
    schedule_start_at: str | None = None
    schedule_interval_minutes: int | str | None = None
    schedule_expression: str | None = None
    image_style: str = "default"
    sites: tuple[str, ...] = ()
    # 任务自己的栏目配置（顺序即输出顺序），随任务一起读写。
    items: tuple[StoredProfileItem, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredTaskCandidateSite:
    id: str
    revision: int | None


@dataclass(frozen=True, slots=True)
class StoredTaskConfigSnapshot:
    task: StoredTask
    profile_id: str | None
    profile_revision: int | None
    candidates: tuple[StoredTaskCandidateSite, ...] = ()


@dataclass(frozen=True, slots=True)
class ScheduleState:
    task_id: str
    next_run_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ScheduleClaim:
    task_id: str
    scheduled_for: str
    execution_id: str
    status: str
    task_revision: int
    profile_revision: int
    site_revision: int
    error_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ScheduleSkip:
    task_id: str
    scheduled_for: str
    reason: str
    created_at: str


@dataclass(frozen=True, slots=True)
class StoredProfile:
    id: str
    name: str
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

                if 0 <= version < SCHEMA_VERSION:
                    await self._migrate_schema(connection, version)
                elif version != SCHEMA_VERSION:
                    raise SiteStoreError(
                        f"不支持的站点数据库 schema 版本：{version}",
                    )

                await self._recover_schedule_claims(connection)
                await self._recover_delivery(connection)
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
            # 迁移期间统一关闭外键校验：重建 push_tasks 时不能触发子表约束，
            # 其它版本只是加列/建表，不受影响；结束后统一恢复。
            await connection.execute("PRAGMA foreign_keys = OFF")
            # 先取得写锁，再读取版本。只读 PRAGMA 拿到的是锁外的快照，
            # 另一个实例可能刚完成迁移；按旧版本重复执行 DDL 会 duplicate
            # column（例如再次 ALTER TABLE ... ADD COLUMN owner_token）。
            await connection.execute("BEGIN IMMEDIATE")
            async with connection.execute("PRAGMA user_version") as cursor:
                row = await cursor.fetchone()
            locked_version = int(row[0]) if row is not None else 0
            if locked_version > SCHEMA_VERSION:
                raise SiteStoreError(
                    f"不支持的站点数据库 schema 版本：{locked_version}",
                )
            if locked_version >= SCHEMA_VERSION:
                # 另一个实例已经迁到当前版本：本轮不需要任何 DDL。
                await connection.commit()
                return
            version = locked_version
            if version == 0:
                await connection.execute(_CREATE_SITES_TABLE)
            await connection.execute(_CREATE_PROFILES_TABLE)
            await connection.execute(_CREATE_PROFILE_ITEMS_TABLE)
            if version in (3, 4, 5, 6):
                async with connection.execute(
                    "PRAGMA table_info(push_tasks)",
                ) as cursor:
                    task_columns = {row[1] for row in await cursor.fetchall()}
                for column in (
                    "schedule_kind", "schedule_timezone", "schedule_time",
                    "schedule_weekdays", "schedule_start_at",
                    "schedule_interval_minutes", "schedule_expression",
                ):
                    if column not in task_columns:
                        await connection.execute(
                            f"ALTER TABLE push_tasks ADD COLUMN {column} TEXT",
                        )
                await connection.execute(_CREATE_TASKS_TABLE_V7)
                await connection.execute(
                    """
                    INSERT INTO push_tasks_v7 (
                        id, name, profile_id, output_format, enabled,
                        schedule_kind, schedule_timezone, schedule_time,
                        schedule_weekdays, schedule_start_at,
                        schedule_interval_minutes, schedule_expression,
                        revision, created_at, updated_at
                    )
                    SELECT id, name, profile_id, output_format, enabled,
                           schedule_kind, schedule_timezone, schedule_time, schedule_weekdays, schedule_start_at, schedule_interval_minutes, schedule_expression,
                           revision, created_at, updated_at
                    FROM push_tasks
                    """,
                )
                await connection.execute("DROP TABLE push_tasks")
                await connection.execute(
                    "ALTER TABLE push_tasks_v7 RENAME TO push_tasks",
                )
            else:
                await connection.execute(_CREATE_TASKS_TABLE)
            await connection.execute(_CREATE_TASK_UMOS_TABLE)
            await connection.execute(_CREATE_TASK_PROFILE_INDEX)
            await connection.execute(_CREATE_TASK_SITES_TABLE)
            await connection.execute(_CREATE_TASK_SITES_INDEX)
            # v15：组合只定义栏目、顺序和数量，不再绑定站点。重建组合表去掉
            # site_id 与旧索引；旧绑定关系暂存一份，用于回填旧任务的候选。
            async with connection.execute(
                "PRAGMA table_info(content_profiles)",
            ) as cursor:
                profile_columns = {row[1] for row in await cursor.fetchall()}
            legacy_profile_sites = "site_id" in profile_columns
            if legacy_profile_sites:
                await connection.execute(
                    "DROP TABLE IF EXISTS _profile_site_backup",
                )
                await connection.execute(
                    "CREATE TABLE _profile_site_backup AS "
                    "SELECT id, site_id FROM content_profiles",
                )
                await connection.execute(
                    "DROP TABLE IF EXISTS content_profiles_v15",
                )
                await connection.execute(
                    """
                    CREATE TABLE content_profiles_v15 (
                        id TEXT PRIMARY KEY NOT NULL,
                        name TEXT NOT NULL,
                        revision INTEGER NOT NULL CHECK (revision >= 1),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """,
                )
                await connection.execute(
                    """
                    INSERT INTO content_profiles_v15
                        (id, name, revision, created_at, updated_at)
                    SELECT id, name, revision, created_at, updated_at
                    FROM content_profiles
                    """,
                )
                await connection.execute("DROP TABLE content_profiles")
                await connection.execute(
                    "ALTER TABLE content_profiles_v15 "
                    "RENAME TO content_profiles",
                )
            # v14：为旧任务回填候选站点，顺序沿用原组合绑定的站点。
            # 旧单站点任务因此保持原有执行顺序与可见行为。
            if legacy_profile_sites:
                await connection.execute(
                    """
                    INSERT OR IGNORE INTO push_task_sites
                        (task_id, position, site_id)
                    SELECT t.id, 0, b.site_id
                    FROM push_tasks AS t
                    JOIN _profile_site_backup AS b ON b.id = t.profile_id
                    WHERE b.site_id IS NOT NULL
                    """,
                )
                await connection.execute("DROP TABLE _profile_site_backup")
            await connection.execute(_CREATE_SCHEDULE_STATE_TABLE)
            await connection.execute(_CREATE_SCHEDULE_CLAIMS_TABLE)
            await connection.execute(_CREATE_SCHEDULE_SKIPS_TABLE)
            await connection.execute(_CREATE_SCHEDULE_STATE_INDEX)
            await connection.execute(_CREATE_DELIVERY_EXECUTIONS_TABLE)
            await connection.execute(_CREATE_DELIVERY_TARGETS_TABLE)
            await connection.execute(_CREATE_DELIVERY_PAGES_TABLE)
            await connection.execute(_CREATE_DELIVERY_EXECUTION_INDEX)
            if version == 4:
                # v4 已有投递表，仅补执行所有者标记。
                await connection.execute(
                    "ALTER TABLE delivery_executions "
                    "ADD COLUMN owner_token TEXT",
                )
            # 兼容 version 10 及更早的库：增加可空 image_model，只加列不改写
            # 历史行；已存在列时跳过，保证迁移幂等。
            async with connection.execute(
                "PRAGMA table_info(delivery_executions)",
            ) as cursor:
                delivery_columns = {
                    row[1] for row in await cursor.fetchall()
                }
            if "image_model" not in delivery_columns:
                await connection.execute(
                    "ALTER TABLE delivery_executions "
                    "ADD COLUMN image_model TEXT",
                )
            async with connection.execute(
                "PRAGMA table_info(delivery_pages)",
            ) as cursor:
                page_columns = {row[1] for row in await cursor.fetchall()}
            if "image_generated" not in page_columns:
                await connection.execute(
                    "ALTER TABLE delivery_pages "
                    "ADD COLUMN image_generated INTEGER",
                )
            # v13：站点来源类型（公开实例 / 自建），旧站点按公开处理。
            async with connection.execute(
                "PRAGMA table_info(sites)",
            ) as cursor:
                site_columns = {row[1] for row in await cursor.fetchall()}
            if "source_kind" not in site_columns:
                await connection.execute(
                    "ALTER TABLE sites ADD COLUMN source_kind TEXT NOT NULL "
                    "DEFAULT 'public' "
                    "CHECK (source_kind IN ('public', 'self_built'))",
                )
            # Older push_tasks tables may lack schedule columns. Inspect the
            # actual table so partially migrated/test fixtures are safe too.
            async with connection.execute(
                "PRAGMA table_info(push_tasks)",
            ) as cursor:
                task_columns = {row[1] for row in await cursor.fetchall()}
            for column in (
                "schedule_kind", "schedule_timezone", "schedule_time",
                "schedule_weekdays", "schedule_start_at",
                "schedule_expression",
            ):
                if column not in task_columns:
                    await connection.execute(
                        f"ALTER TABLE push_tasks ADD COLUMN {column} TEXT",
                    )
            if "schedule_interval_minutes" not in task_columns:
                await connection.execute(
                    "ALTER TABLE push_tasks "
                    "ADD COLUMN schedule_interval_minutes INTEGER",
                )
            # v16：内容组合并入推送任务。每个任务保存自己的一份栏目配置，
            # 组合行的 id 等于任务 id（历史执行记录里的 profile_id 因此继续有效）。
            # 三步都按「该任务是否已有自己的方案行 / 栏目」判断，重复启动不会
            # 二次复制栏目，也不会改动已迁移的任务。
            await connection.execute(
                """
                INSERT INTO content_profiles
                    (id, name, revision, created_at, updated_at)
                SELECT t.id, t.name, 1, t.created_at, t.updated_at
                FROM push_tasks AS t
                WHERE NOT EXISTS (
                    SELECT 1 FROM content_profiles AS p WHERE p.id = t.id
                )
                """
            )
            await connection.execute(
                """
                INSERT INTO content_profile_items
                    (profile_id, position, content_type, params_json, limit_count)
                SELECT t.id, i.position, i.content_type,
                       i.params_json, i.limit_count
                FROM push_tasks AS t
                JOIN content_profile_items AS i ON i.profile_id = t.profile_id
                WHERE t.profile_id <> t.id
                  AND NOT EXISTS (
                      SELECT 1 FROM content_profile_items AS x
                      WHERE x.profile_id = t.id
                  )
                ORDER BY t.id, i.position
                """
            )
            await connection.execute(
                "UPDATE push_tasks SET profile_id = id WHERE profile_id <> id"
            )
            # v17：任务级图片样式。旧行与旧客户端省略字段时保留默认款；
            # 同一事务内加列，失败回滚不影响旧库。
            async with connection.execute(
                "PRAGMA table_info(push_tasks)",
            ) as cursor:
                task_columns = {row[1] for row in await cursor.fetchall()}
            if "image_style" not in task_columns:
                await connection.execute(
                    "ALTER TABLE push_tasks ADD COLUMN "
                    "image_style TEXT NOT NULL DEFAULT 'default'",
                )
            # v18：站点级启用开关移除。历史停用站点保留供追溯，运行时不
            # 再按它过滤；但升级前已启用的任务若引用了停用站点，升级后不
            # 应在管理员核对候选顺序前自动取数，因此只暂停这些任务，保留
            # 计划、白名单、revision 与历史，管理员确认后可重新启用。
            if version < 18:
                async with connection.execute(
                    """
                    SELECT DISTINCT ps.task_id
                    FROM push_task_sites AS ps
                    JOIN push_tasks AS t ON t.id = ps.task_id
                    JOIN sites AS s ON s.id = ps.site_id
                    WHERE t.enabled = 1 AND s.enabled = 0
                    """
                ) as cursor:
                    paused_task_ids = [
                        str(row[0]) for row in await cursor.fetchall()
                    ]
                if paused_task_ids:
                    now = _utc_now()
                    placeholders = ",".join("?" for _ in paused_task_ids)
                    await connection.execute(
                        "UPDATE push_tasks SET enabled = 0, "
                        "revision = revision + 1, updated_at = ? "
                        f"WHERE id IN ({placeholders})",
                        (now, *paused_task_ids),
                    )
                    await connection.execute(
                        "DELETE FROM push_schedule_state "
                        f"WHERE task_id IN ({placeholders})",
                        tuple(paused_task_ids),
                    )
            # v19：拼合内容的逐栏来源。历史行保持 NULL，详情接口据此显示
            # “未记录来源”，不根据空站点 ID 反推具体栏目来源。
            async with connection.execute(
                "PRAGMA table_info(delivery_executions)",
            ) as cursor:
                delivery_columns = {
                    row[1] for row in await cursor.fetchall()
                }
            if "content_sources_json" not in delivery_columns:
                await connection.execute(
                    "ALTER TABLE delivery_executions "
                    "ADD COLUMN content_sources_json TEXT",
                )
            await connection.execute(
                f"PRAGMA user_version = {SCHEMA_VERSION}",
            )
            await connection.commit()
        except BaseException:
            await self._rollback_quietly(connection)
            raise
        finally:
            try:
                await connection.execute("PRAGMA foreign_keys = ON")
            except Exception:
                pass

    async def close(self) -> None:
        """关闭连接；可重复调用。"""
        async with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await connection.close()

    async def _name_taken(
        self, connection, name: str, exclude_id: str | None = None,
    ) -> bool:
        """名称按去首尾空白、忽略大小写比较；旧库重名留给用户处理。"""
        sql = "SELECT id FROM sites WHERE lower(trim(name)) = lower(trim(?))"
        params: list[object] = [name]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        cursor = await connection.execute(sql, params)
        return await cursor.fetchone() is not None

    async def _name_conflicts(
        self, connection, site_id: str, name: str,
    ) -> bool:
        """编辑时保留本行既有名称不受影响，旧库已有的重名站点仍可继续保存。"""
        async with connection.execute(
            "SELECT name FROM sites WHERE id = ?", (site_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None and row["name"].strip().lower() == name.strip().lower():
            return False
        return await self._name_taken(connection, name, exclude_id=site_id)

    async def create_site(
        self,
        *,
        site_id: str,
        name: str,
        base_url: str,
        timeout_seconds: float,
        source_kind: str,
        bearer_token: str | None,
    ) -> StoredSite:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                if await self._name_taken(connection, name):
                    raise SiteNameConflictError(name)
                await connection.execute(
                    """
                    INSERT INTO sites (
                        id, name, base_url, enabled, timeout_seconds,
                        source_kind, bearer_token, revision, created_at,
                        updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        site_id,
                        name,
                        base_url,
                        1,
                        timeout_seconds,
                        source_kind,
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
                enabled=True,
                timeout_seconds=timeout_seconds,
                source_kind=source_kind,
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
                           source_kind, bearer_token, revision, created_at,
                           updated_at
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
                           source_kind, bearer_token, revision, created_at,
                           updated_at
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
        timeout_seconds: float,
        source_kind: str,
        bearer_token: str | None,
        expected_revision: int,
    ) -> StoredSite:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                if await self._name_conflicts(connection, site_id, name):
                    raise SiteNameConflictError(name)
                cursor = await connection.execute(
                    """
                    UPDATE sites
                    SET name = ?, base_url = ?, timeout_seconds = ?,
                        source_kind = ?, bearer_token = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        name,
                        base_url,
                        timeout_seconds,
                        source_kind,
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
                           source_kind, bearer_token, revision, created_at,
                           updated_at
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
                # 组合不再绑定站点；只有任务候选构成引用，删除会让任务失去候选。
                cursor = await connection.execute(
                    "SELECT t.name FROM push_task_sites AS s "
                    "JOIN push_tasks AS t ON t.id = s.task_id "
                    "WHERE s.site_id = ? ORDER BY t.created_at, t.id",
                    (site_id,),
                )
                task_names = tuple(row["name"] for row in await cursor.fetchall())
                if task_names:
                    raise SiteReferencedError(task_names)
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
        items: tuple[StoredProfileItem, ...],
    ) -> StoredProfile:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                await connection.execute(
                    """
                    INSERT INTO content_profiles (
                        id, name, revision, created_at, updated_at
                    ) VALUES (?, ?, 1, ?, ?)
                    """,
                    (profile_id, name, now, now),
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
                    SELECT id, name, revision, created_at, updated_at
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
                    SELECT id, name, revision, created_at, updated_at
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
                    SET name = ?, revision = revision + 1,
                        updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (name, now, profile_id, expected_revision),
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
                    SELECT id, name, revision, created_at, updated_at
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
                    "SELECT name FROM push_tasks WHERE profile_id = ? "
                    "ORDER BY created_at, id",
                    (profile_id,),
                )
                task_names = tuple(row["name"] for row in await cursor.fetchall())
                if task_names:
                    raise ProfileReferencedError(task_names)
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

    async def create_task(
        self,
        *,
        task_id: str,
        name: str,
        items: tuple[StoredProfileItem, ...],
        output_format: str,
        umos: tuple[str, ...],
        image_style: str = "default",
        site_ids: tuple[str, ...] = (),
        schedule_kind: str | None = None,
        schedule_timezone: str | None = None,
        schedule_time: str | None = None,
        schedule_weekdays: str | None = None,
        schedule_start_at: str | None = None,
        schedule_interval_minutes: int | str | None = None,
        schedule_expression: str | None = None,
    ) -> StoredTask:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                # 任务自带一份内容方案（行 id 与任务 id 相同），和任务同一事务落库。
                await self._insert_plan(connection, task_id, name, items, now)
                await connection.execute(
                    """
                    INSERT INTO push_tasks (
                        id, name, profile_id, output_format, image_style, enabled,
                        schedule_kind, schedule_timezone, schedule_time,
                        schedule_weekdays, schedule_start_at,
                        schedule_interval_minutes, schedule_expression,
                        revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        task_id, name, task_id, output_format, image_style,
                        schedule_kind, schedule_timezone, schedule_time,
                        schedule_weekdays, schedule_start_at,
                        schedule_interval_minutes, schedule_expression,
                        now, now,
                    ),
                )
                await self._insert_task_umos(connection, task_id, umos)
                await self._insert_task_sites(connection, task_id, site_ids)

            await self._commit_write(
                connection,
                perform,
                failure_message="保存推送任务失败",
            )
            return StoredTask(
                id=task_id,
                name=name,
                profile_id=task_id,
                items=items,
                output_format=output_format,
                image_style=image_style,
                umos=umos,
                sites=site_ids,
                enabled=False,
                revision=1,
                created_at=now,
                updated_at=now,
                schedule_kind=schedule_kind,
                schedule_timezone=schedule_timezone,
                schedule_time=schedule_time,
                schedule_weekdays=schedule_weekdays,
                schedule_start_at=schedule_start_at,
                schedule_interval_minutes=schedule_interval_minutes,
                schedule_expression=schedule_expression,
            )

    async def list_tasks(self) -> list[StoredTask]:
        async with self._lock:
            connection = self._require_connection()
            try:
                async with connection.execute(
                    """
                    SELECT id, name, profile_id, output_format, image_style, enabled,
                           schedule_kind, schedule_timezone, schedule_time, schedule_weekdays, schedule_start_at, schedule_interval_minutes, schedule_expression,
                           revision, created_at, updated_at
                    FROM push_tasks
                    ORDER BY created_at, id
                    """,
                ) as cursor:
                    rows = await cursor.fetchall()
                ids = tuple(row["id"] for row in rows)
                umos = await self._load_task_umos(ids)
                sites = await self._load_task_sites(ids)
                items = await self._load_profile_items(ids)
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取推送任务列表失败") from exc
            return [
                _row_to_task(
                    row,
                    umos.get(row["id"], ()),
                    sites.get(row["id"], ()),
                    items.get(row["id"], ()),
                )
                for row in rows
            ]

    async def get_task(self, task_id: str) -> StoredTask:
        async with self._lock:
            try:
                row = await self._fetchone_unlocked(
                    """
                    SELECT id, name, profile_id, output_format, image_style, enabled,
                           schedule_kind, schedule_timezone, schedule_time, schedule_weekdays, schedule_start_at, schedule_interval_minutes, schedule_expression,
                           revision, created_at, updated_at
                    FROM push_tasks
                    WHERE id = ?
                    """,
                    (task_id,),
                )
                if row is None:
                    raise TaskRecordNotFoundError(task_id)
                umos = await self._load_task_umos((task_id,))
                sites = await self._load_task_sites((task_id,))
                items = await self._load_profile_items((task_id,))
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取推送任务失败") from exc
            return _row_to_task(
                row, umos.get(task_id, ()), sites.get(task_id, ()),
                items.get(task_id, ()),
            )

    async def get_task_config_snapshot(
        self,
        task_id: str,
    ) -> StoredTaskConfigSnapshot:
        """在一个锁内读取任务、栏目方案和候选站点的配置快照。

        任务 UMO、任务的候选站点、任务的内容方案以及各自 revision 必须来自
        同一次一致的存储读取，避免分多次 await 拼装旧状态。
        """
        async with self._lock:
            try:
                row = await self._fetchone_unlocked(
                    """
                    SELECT id, name, profile_id, output_format, image_style, enabled,
                           schedule_kind, schedule_timezone, schedule_time, schedule_weekdays, schedule_start_at, schedule_interval_minutes, schedule_expression,
                           revision, created_at, updated_at
                    FROM push_tasks
                    WHERE id = ?
                    """,
                    (task_id,),
                )
                if row is None:
                    raise TaskRecordNotFoundError(task_id)
                umos = await self._load_task_umos((task_id,))
                sites = await self._load_task_sites((task_id,))
                items = await self._load_profile_items((task_id,))
                task = _row_to_task(
                    row, umos.get(task_id, ()), sites.get(task_id, ()),
                    items.get(task_id, ()),
                )
                async with self._require_connection().execute(
                    """
                    SELECT ps.site_id AS site_id, s.revision AS revision
                    FROM push_task_sites AS ps
                    LEFT JOIN sites AS s ON s.id = ps.site_id
                    WHERE ps.task_id = ?
                    ORDER BY ps.position
                    """,
                    (task_id,),
                ) as cursor:
                    candidate_rows = await cursor.fetchall()
                # 候选顺序即尝试顺序；已删除候选同样带出，供确认与告警判定。
                candidates = tuple(
                    StoredTaskCandidateSite(
                        id=str(item["site_id"]),
                        revision=(
                            int(item["revision"])
                            if item["revision"] is not None else None
                        ),
                    )
                    for item in candidate_rows
                )
                profile_row = await self._fetchone_unlocked(
                    """
                    SELECT id, revision
                    FROM content_profiles
                    WHERE id = ?
                    """,
                    (task.profile_id,),
                )
                if profile_row is None:
                    return StoredTaskConfigSnapshot(
                        task=task, profile_id=None, profile_revision=None,
                        candidates=candidates,
                    )
                return StoredTaskConfigSnapshot(
                    task=task,
                    profile_id=str(profile_row["id"]),
                    profile_revision=int(profile_row["revision"]),
                    candidates=candidates,
                )
            except TaskRecordNotFoundError:
                raise
            except SiteStoreError:
                raise
            except Exception as exc:
                raise SiteStoreError("读取任务配置快照失败") from exc

    async def update_task(
        self,
        task_id: str,
        *,
        name: str,
        items: tuple[StoredProfileItem, ...],
        output_format: str,
        umos: tuple[str, ...],
        expected_revision: int,
        image_style: str | None = None,
        site_ids: tuple[str, ...] | None = None,
        schedule_unchanged: bool = False,
        schedule_kind: str | None = None,
        schedule_timezone: str | None = None,
        schedule_time: str | None = None,
        schedule_weekdays: str | None = None,
        schedule_start_at: str | None = None,
        schedule_interval_minutes: int | str | None = None,
        schedule_expression: str | None = None,
    ) -> StoredTask:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                # 旧库或异常中断可能缺方案行；先补建，后续外键与栏目写入才成立。
                await self._ensure_plan_row(
                    connection, task_id, name, now,
                )
                if schedule_unchanged:
                    cursor = await connection.execute(
                        """
                        UPDATE push_tasks
                        SET name = ?, profile_id = ?, output_format = ?,
                            image_style = COALESCE(?, image_style),
                            enabled = 0, revision = revision + 1,
                            updated_at = ?
                        WHERE id = ? AND revision = ?
                        """,
                        (
                            name, task_id, output_format, image_style, now,
                            task_id, expected_revision,
                        ),
                    )
                else:
                    cursor = await connection.execute(
                        """
                        UPDATE push_tasks
                        SET name = ?, profile_id = ?, output_format = ?,
                            image_style = COALESCE(?, image_style),
                            enabled = 0, schedule_kind = ?,
                            schedule_timezone = ?, schedule_time = ?,
                            schedule_weekdays = ?, schedule_start_at = ?,
                            schedule_interval_minutes = ?, schedule_expression = ?,
                            revision = revision + 1, updated_at = ?
                        WHERE id = ? AND revision = ?
                        """,
                        (
                            name, task_id, output_format, image_style,
                            schedule_kind, schedule_timezone, schedule_time,
                            schedule_weekdays, schedule_start_at,
                            schedule_interval_minutes, schedule_expression,
                            now, task_id,
                            expected_revision,
                        ),
                    )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM push_tasks WHERE id = ?",
                        (task_id,),
                    )
                    if row is None:
                        raise TaskRecordNotFoundError(task_id)
                    raise TaskRevisionConflictError(int(row["revision"]))
                await connection.execute(
                    "DELETE FROM push_task_umos WHERE task_id = ?",
                    (task_id,),
                )
                await connection.execute(
                    "DELETE FROM push_schedule_state WHERE task_id = ?",
                    (task_id,),
                )
                await self._insert_task_umos(connection, task_id, umos)
                if site_ids is not None:
                    await connection.execute(
                        "DELETE FROM push_task_sites WHERE task_id = ?",
                        (task_id,),
                    )
                    await self._insert_task_sites(connection, task_id, site_ids)

                # 栏目配置属于这个任务自己：整体替换并提升方案版本，
                # 历史执行记录里的 profile_revision 仍可读。
                await self._replace_plan_items(
                    connection, task_id, name, items, now,
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="更新推送任务失败",
            )
            row = await self._fetchone_unlocked(
                """
                SELECT id, name, profile_id, output_format, image_style, enabled,
                       schedule_kind, schedule_timezone, schedule_time, schedule_weekdays, schedule_start_at, schedule_interval_minutes, schedule_expression,
                       revision, created_at, updated_at
                FROM push_tasks WHERE id = ?
                """,
                (task_id,),
            )
            if row is None:
                raise TaskRecordNotFoundError(task_id)
            stored_sites = await self._load_task_sites((task_id,))
            stored_items = await self._load_profile_items((task_id,))
            return _row_to_task(
                row, umos, stored_sites.get(task_id, ()),
                stored_items.get(task_id, ()),
            )

    async def delete_task(self, task_id: str, *, expected_revision: int) -> None:
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                cursor = await connection.execute(
                    "DELETE FROM push_tasks WHERE id = ? AND revision = ?",
                    (task_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM push_tasks WHERE id = ?",
                        (task_id,),
                    )
                    if row is None:
                        raise TaskRecordNotFoundError(task_id)
                    raise TaskRevisionConflictError(int(row["revision"]))
                # 任务自带的方案随任务删除；共享组合是旧模型的遗留数据，
                # 可能仍被历史执行记录引用，这里不动它们。
                await connection.execute(
                    "DELETE FROM content_profiles WHERE id = ?",
                    (task_id,),
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="删除推送任务失败",
            )

    async def set_task_enabled(
        self,
        task_id: str,
        *,
        enabled: bool,
        expected_revision: int,
        next_run_at: str | None,
        now: str,
        is_valid: Callable[[], bool] | None = None,
    ) -> StoredTask:
        """显式启停；与 revision、运行计划同一事务。"""
        async with self._lock:
            if is_valid is not None and not is_valid():
                raise TaskOperationStaleError()
            connection = self._require_connection()

            async def perform() -> None:
                if is_valid is not None and not is_valid():
                    raise TaskOperationStaleError()
                cursor = await connection.execute(
                    """
                    UPDATE push_tasks
                    SET enabled = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (int(enabled), now, task_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    row = await self._fetchone_unlocked(
                        "SELECT revision FROM push_tasks WHERE id = ?",
                        (task_id,),
                    )
                    if row is None:
                        raise TaskRecordNotFoundError(task_id)
                    raise TaskRevisionConflictError(int(row["revision"]))
                if enabled:
                    if next_run_at is None:
                        raise ValueError("启用必须提供 next_run_at")
                    await connection.execute(
                        """
                        INSERT INTO push_schedule_state
                            (task_id, next_run_at, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(task_id) DO UPDATE SET
                            next_run_at = excluded.next_run_at,
                            updated_at = excluded.updated_at
                        """,
                        (task_id, next_run_at, now),
                    )
                else:
                    await connection.execute(
                        "DELETE FROM push_schedule_state WHERE task_id = ?",
                        (task_id,),
                    )

            await self._commit_write(
                connection,
                perform,
                failure_message="更新任务启停状态失败",
            )
            row = await self._fetchone_unlocked(
                """
                SELECT id, name, profile_id, output_format, image_style, enabled,
                       schedule_kind, schedule_timezone, schedule_time, schedule_weekdays, schedule_start_at, schedule_interval_minutes, schedule_expression,
                       revision, created_at, updated_at
                FROM push_tasks WHERE id = ?
                """,
                (task_id,),
            )
            if row is None:
                raise TaskRecordNotFoundError(task_id)
            umos = await self._load_task_umos((task_id,))
            sites = await self._load_task_sites((task_id,))
            items = await self._load_profile_items((task_id,))
            return _row_to_task(
                row, umos.get(task_id, ()), sites.get(task_id, ()),
                items.get(task_id, ()),
            )

    async def get_schedule_state(self, task_id: str) -> ScheduleState | None:
        async with self._lock:
            row = await self._fetchone_unlocked(
                "SELECT task_id, next_run_at, updated_at "
                "FROM push_schedule_state WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                return None
            return ScheduleState(
                task_id=str(row["task_id"]),
                next_run_at=str(row["next_run_at"]),
                updated_at=str(row["updated_at"]),
            )

    async def list_schedule_states(self) -> list[ScheduleState]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                "SELECT task_id, next_run_at, updated_at "
                "FROM push_schedule_state ORDER BY next_run_at, task_id",
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                ScheduleState(
                    task_id=str(row["task_id"]),
                    next_run_at=str(row["next_run_at"]),
                    updated_at=str(row["updated_at"]),
                )
                for row in rows
            ]

    async def list_due_schedule_states(self, now: str) -> list[ScheduleState]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT s.task_id, s.next_run_at, s.updated_at
                FROM push_schedule_state s
                JOIN push_tasks t ON t.id = s.task_id
                WHERE t.enabled = 1 AND s.next_run_at <= ?
                ORDER BY s.next_run_at, s.task_id
                """,
                (now,),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                ScheduleState(
                    task_id=str(row["task_id"]),
                    next_run_at=str(row["next_run_at"]),
                    updated_at=str(row["updated_at"]),
                )
                for row in rows
            ]

    async def advance_schedule(
        self,
        task_id: str,
        *,
        scheduled_for: str,
        next_run_at: str,
        now: str,
    ) -> bool:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                UPDATE push_schedule_state
                SET next_run_at = ?, updated_at = ?
                WHERE task_id = ? AND next_run_at = ?
                """,
                (next_run_at, now, task_id, scheduled_for),
            )
            await connection.commit()
            return cursor.rowcount == 1

    async def claim_schedule(
        self,
        *,
        task_id: str,
        scheduled_for: str,
        execution_id: str,
        task_revision: int,
        profile_revision: int,
        site_revision: int,
        next_run_at: str,
        now: str,
    ) -> bool:
        """唯一认领并推进下一次计划；返回 False 表示已被别人处理。"""
        async with self._lock:
            connection = self._require_connection()
            claimed = False

            async def perform() -> None:
                nonlocal claimed
                row = await self._fetchone_unlocked(
                    """
                    SELECT t.enabled, s.next_run_at
                    FROM push_tasks t
                    JOIN push_schedule_state s ON s.task_id = t.id
                    WHERE t.id = ? AND t.revision = ?
                    """,
                    (task_id, task_revision),
                )
                if (
                    row is None or not bool(row["enabled"])
                    or str(row["next_run_at"]) != scheduled_for
                ):
                    return
                # The scan may be stale after a backward clock adjustment.
                # Never claim a slot that is still in the future.
                if _iso_time(scheduled_for) > _iso_time(now):
                    return
                cursor = await connection.execute(
                    """
                    INSERT INTO push_schedule_claims (
                        task_id, scheduled_for, execution_id, status,
                        task_revision, profile_revision, site_revision,
                        error_code, created_at, updated_at
                   ) VALUES (?, ?, ?, 'claimed', ?, ?, ?, NULL, ?, ?)
                    ON CONFLICT (task_id, scheduled_for) DO NOTHING
                    """,
                    (
                        task_id, scheduled_for, execution_id, task_revision,
                        profile_revision, site_revision, now, now,
                    ),
                )
                if cursor.rowcount != 1:
                    # The slot was already handled. If the persisted state is
                    # still stuck on that slot, advance it under the same
                    # version/state guard; never overwrite a newer plan.
                    exists = await self._fetchone_unlocked(
                        "SELECT 1 FROM push_schedule_claims "
                        "WHERE task_id = ? AND scheduled_for = ?",
                        (task_id, scheduled_for),
                    )
                    if exists is not None:
                        await connection.execute(
                            """
                            UPDATE push_schedule_state
                            SET next_run_at = ?, updated_at = ?
                            WHERE task_id = ? AND next_run_at = ?
                            """,
                            (next_run_at, now, task_id, scheduled_for),
                        )
                    return
                cursor = await connection.execute(
                    """
                    UPDATE push_schedule_state
                    SET next_run_at = ?, updated_at = ?
                    WHERE task_id = ? AND next_run_at = ?
                    """,
                    (next_run_at, now, task_id, scheduled_for),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("运行计划已变化")
                claimed = True

            await self._commit_write(
                connection,
                perform,
                failure_message="认领调度执行失败",
            )
            return claimed

    async def record_schedule_skip(
        self,
        *,
        task_id: str,
        scheduled_for: str,
        next_run_at: str,
        reason: str,
        now: str,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                await connection.execute(
                    """
                    INSERT INTO push_schedule_skips
                        (task_id, scheduled_for, reason, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (task_id, scheduled_for, reason) DO NOTHING
                    """,
                    (task_id, scheduled_for, reason, now),
                )
                await connection.execute(
                    """
                    UPDATE push_schedule_state
                    SET next_run_at = ?, updated_at = ?
                    WHERE task_id = ? AND next_run_at = ?
                    """,
                    (next_run_at, now, task_id, scheduled_for),
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="记录调度跳过失败",
            )

    async def set_schedule_claim_status(
        self,
        execution_id: str,
        *,
        status: str,
        error_code: str | None,
        now: str,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            await connection.execute(
                """
                UPDATE push_schedule_claims
                SET status = ?, error_code = ?, updated_at = ?
                WHERE execution_id = ?
                """,
                (status, error_code, now, execution_id),
            )
            await connection.commit()

    async def list_schedule_claims(
        self, *, limit: int, offset: int,
    ) -> list[ScheduleClaim]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT task_id, scheduled_for, execution_id, status,
                       task_revision, profile_revision, site_revision,
                       error_code, created_at, updated_at
                FROM push_schedule_claims
                ORDER BY scheduled_for DESC, task_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                ScheduleClaim(
                    task_id=str(row["task_id"]),
                    scheduled_for=str(row["scheduled_for"]),
                    execution_id=str(row["execution_id"]),
                    status=str(row["status"]),
                    task_revision=int(row["task_revision"]),
                    profile_revision=int(row["profile_revision"]),
                    site_revision=int(row["site_revision"]),
                    error_code=row["error_code"],
                    created_at=str(row["created_at"]),
                    updated_at=str(row["updated_at"]),
                )
                for row in rows
            ]

    async def list_schedule_skips(
        self, *, limit: int, offset: int,
    ) -> list[ScheduleSkip]:
        async with self._lock:
            connection = self._require_connection()
            async with connection.execute(
                """
                SELECT task_id, scheduled_for, reason, created_at
                FROM push_schedule_skips
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
            return [
                ScheduleSkip(
                    task_id=str(row["task_id"]),
                    scheduled_for=str(row["scheduled_for"]),
                    reason=str(row["reason"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            ]

    async def _recover_schedule_claims(self, connection: aiosqlite.Connection) -> None:
        now = _utc_now()
        await connection.execute(
            """
            UPDATE push_schedule_claims
            SET status = 'unknown', error_code = 'interrupted', updated_at = ?
            WHERE status = 'claimed'
            """,
            (now,),
        )
        await connection.commit()

    async def create_delivery_execution(
        self,
        *,
        execution_id: str,
        task_id: str,
        task_name: str,
        task_revision: int,
        profile_id: str,
        profile_revision: int,
        site_id: str,
        site_revision: int,
        output_format: str,
        umos: tuple[str, ...],
        owner_token: str | None = None,
        image_model: str | None = None,
    ) -> None:
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                try:
                    await connection.execute(
                        """
                        INSERT INTO delivery_executions (
                            id, task_id, task_name, task_revision,
                            profile_id, profile_revision, site_id, site_revision,
                            output_format, owner_token, content_hash,
                            content_status, failed_sections_json, image_model,
                            status, error_code, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                                  NULL, ?, 'preparing', NULL, ?, ?)
                        """,
                        (
                            execution_id, task_id, task_name, task_revision,
                            profile_id, profile_revision, site_id, site_revision,
                            output_format, owner_token, image_model, now, now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DeliveryExecutionExistsError(execution_id) from exc
                for position, umo in enumerate(umos):
                    await connection.execute(
                        """
                        INSERT INTO delivery_targets (
                            execution_id, position, umo, status, error_code
                       ) VALUES (?, ?, ?, 'pending', NULL)
                        """,
                        (execution_id, position, umo),
                    )

            await self._commit_write(
                connection,
                perform,
                failure_message="保存投递执行失败",
            )

    async def get_delivery_execution(
        self,
        execution_id: str,
    ) -> StoredDeliveryExecution:
        async with self._lock:
            row = await self._fetchone_unlocked(
                "SELECT * FROM delivery_executions WHERE id = ?",
                (execution_id,),
            )
            if row is None:
                raise DeliveryRecordNotFoundError(execution_id)
            async with self._require_connection().execute(
                "SELECT * FROM delivery_targets WHERE execution_id = ? "
                "ORDER BY position",
                (execution_id,),
            ) as cursor:
                target_rows = await cursor.fetchall()
            async with self._require_connection().execute(
                "SELECT * FROM delivery_pages WHERE execution_id = ? "
                "ORDER BY position, page_index",
                (execution_id,),
            ) as cursor:
                page_rows = await cursor.fetchall()
            return _row_to_delivery(row, target_rows, page_rows)

    async def list_delivery_execution_ids(
        self,
        *,
        limit: int,
        offset: int,
        task_id: str | None = None,
    ) -> tuple[str, ...]:
        async with self._lock:
            if task_id is None:
                sql = (
                    "SELECT id FROM delivery_executions "
                    "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
                )
                params: tuple[object, ...] = (limit, offset)
            else:
                sql = (
                    "SELECT id FROM delivery_executions WHERE task_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
                )
                params = (task_id, limit, offset)
            async with self._require_connection().execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            return tuple(row["id"] for row in rows)

    async def update_delivery_site(
        self,
        execution_id: str,
        *,
        site_id: str,
        site_revision: int,
    ) -> None:
        """把执行记录改成本次实际使用的候选站点。"""
        async with self._lock:
            connection = self._require_connection()
            now = _utc_now()

            async def perform() -> None:
                await connection.execute(
                    """
                    UPDATE delivery_executions
                    SET site_id = ?, site_revision = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (site_id, site_revision, now, execution_id),
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="记录投递站点失败",
            )

    async def set_delivery_content(
        self,
        execution_id: str,
        *,
        content_hash: str,
        content_status: str,
        failed_sections_json: str | None,
        status: str,
        sources_json: str | None = None,
    ) -> None:
        await self._update_delivery_row(
            "delivery_executions",
            execution_id,
            {
                "content_hash": content_hash,
                "content_status": content_status,
                "failed_sections_json": failed_sections_json,
                "content_sources_json": sources_json,
                "status": status,
            },
        )

    async def set_delivery_execution_status(
        self,
        execution_id: str,
        *,
        status: str,
        error_code: str | None = None,
    ) -> None:
        await self._update_delivery_row(
            "delivery_executions",
            execution_id,
            {"status": status, "error_code": error_code},
        )

    async def set_delivery_target_status(
        self,
        execution_id: str,
        position: int,
        *,
        status: str,
        error_code: str | None = None,
    ) -> None:
        await self._update_delivery_row(
            "delivery_targets",
            execution_id,
            {"status": status, "error_code": error_code},
            extra_where=" AND position = ?",
            extra_params=(position,),
            id_column="execution_id",
        )

    async def set_delivery_page_status(
        self,
        execution_id: str,
        position: int,
        page_index: int,
        *,
        status: str,
        error_code: str | None = None,
    ) -> None:
        await self._update_delivery_row(
            "delivery_pages",
            execution_id,
            {"status": status, "error_code": error_code},
            extra_where=" AND position = ? AND page_index = ?",
            extra_params=(position, page_index),
            id_column="execution_id",
        )

    async def add_delivery_pages(
        self,
        execution_id: str,
        *,
        position: int,
        page_count: int,
        image_generated: tuple[int | None, ...] | None = None,
    ) -> None:
        if image_generated is not None:
            if len(image_generated) != page_count:
                raise SiteStoreError("投递页生成标记数量不匹配")
            for value in image_generated:
                if value not in (0, 1, None):
                    raise SiteStoreError("投递页生成标记不合法")
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                for offset in range(page_count):
                    page_index = offset + 1
                    generated = (
                        image_generated[offset]
                        if image_generated is not None else None
                    )
                    await connection.execute(
                        """
                        INSERT INTO delivery_pages (
                            execution_id, position, page_index, status,
                            error_code, image_generated
                       ) VALUES (?, ?, ?, 'pending', NULL, ?)
                        """,
                        (execution_id, position, page_index, generated),
                    )

            await self._commit_write(
                connection,
                perform,
                failure_message="保存投递页失败",
            )

    async def _update_delivery_row(
        self,
        table: str,
        execution_id: str,
        values: dict[str, object],
        *,
        extra_where: str = "",
        extra_params: tuple[object, ...] = (),
        id_column: str = "id",
    ) -> None:
        allowed = {
            "delivery_executions": {
                "content_hash", "content_status", "failed_sections_json",
                "content_sources_json", "status", "error_code",
            },
            "delivery_targets": {"status", "error_code"},
            "delivery_pages": {"status", "error_code"},
        }
        if table not in allowed or not set(values).issubset(allowed[table]):
            raise SiteStoreError("投递记录更新字段不合法")
        assignments = ", ".join(f"{name} = ?" for name in values)
        params = (*values.values(), execution_id, *extra_params)
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                cursor = await connection.execute(
                    f"UPDATE {table} SET {assignments} WHERE {id_column} = ?"
                    + extra_where,
                    params,
                )
                if cursor.rowcount != 1:
                    raise DeliveryRecordNotFoundError(execution_id)

            await self._commit_write(
                connection,
                perform,
                failure_message="更新投递记录失败",
            )

    async def mark_delivery_incomplete(self, execution_id: str) -> None:
        """取消/超时/关闭时保守归类：未开始=skipped，已调用=unknown。"""
        async with self._lock:
            connection = self._require_connection()

            async def perform() -> None:
                await _mark_incomplete_rows(
                    connection, "execution_id = ?", (execution_id,),
                    execution_where="id = ?",
                )

            await self._commit_write(
                connection,
                perform,
                failure_message="标记投递中断失败",
            )

    async def _recover_delivery(self, connection: aiosqlite.Connection) -> None:
        async with connection.execute("BEGIN IMMEDIATE"):
            pass
        try:
            await _mark_incomplete_rows(connection, "1 = 1", ())
            await connection.commit()
        except BaseException:
            await self._rollback_quietly(connection)
            raise

    async def _insert_task_sites(
        self,
        connection: aiosqlite.Connection,
        task_id: str,
        site_ids: tuple[str, ...],
    ) -> None:
        for position, site_id in enumerate(site_ids):
            await connection.execute(
                """
                INSERT INTO push_task_sites (task_id, position, site_id)
                VALUES (?, ?, ?)
                """,
                (task_id, position, site_id),
            )

    async def _load_task_sites(
        self,
        task_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        if not task_ids:
            return {}
        placeholders = ",".join("?" for _ in task_ids)
        async with self._require_connection().execute(
            f"""
            SELECT task_id, site_id
            FROM push_task_sites
            WHERE task_id IN ({placeholders})
            ORDER BY task_id, position
            """,
            task_ids,
        ) as cursor:
            rows = await cursor.fetchall()
        collected: dict[str, list[str]] = {}
        for row in rows:
            collected.setdefault(row["task_id"], []).append(row["site_id"])
        return {key: tuple(value) for key, value in collected.items()}

    async def _insert_task_umos(
        self,
        connection: aiosqlite.Connection,
        task_id: str,
        umos: tuple[str, ...],
    ) -> None:
        for position, umo in enumerate(umos):
            await connection.execute(
                """
                INSERT INTO push_task_umos (task_id, position, umo)
                VALUES (?, ?, ?)
                """,
                (task_id, position, umo),
            )

    async def _load_task_umos(
        self,
        task_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        if not task_ids:
            return {}
        placeholders = ",".join("?" for _ in task_ids)
        async with self._require_connection().execute(
            f"""
            SELECT task_id, umo
            FROM push_task_umos
            WHERE task_id IN ({placeholders})
            ORDER BY task_id, position
            """,
            task_ids,
        ) as cursor:
            rows = await cursor.fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["task_id"], []).append(row["umo"])
        return {
            task_id: tuple(umos)
            for task_id, umos in grouped.items()
        }

    async def _insert_plan(
        self,
        connection: aiosqlite.Connection,
        task_id: str,
        name: str,
        items: tuple[StoredProfileItem, ...],
        now: str,
    ) -> None:
        """为任务创建私有内容方案行：行 id 与任务 id 相同。"""
        await connection.execute(
            """
            INSERT INTO content_profiles
                (id, name, revision, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            """,
            (task_id, name, now, now),
        )
        await self._insert_profile_items(connection, task_id, items)

    async def _ensure_plan_row(
        self,
        connection: aiosqlite.Connection,
        task_id: str,
        name: str,
        now: str,
    ) -> None:
        await connection.execute(
            """
            INSERT INTO content_profiles
                (id, name, revision, created_at, updated_at)
            SELECT ?, ?, 1, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM content_profiles WHERE id = ?
            )
            """,
            (task_id, name, now, now, task_id),
        )

    async def _replace_plan_items(
        self,
        connection: aiosqlite.Connection,
        task_id: str,
        name: str,
        items: tuple[StoredProfileItem, ...],
        now: str,
    ) -> None:
        await self._ensure_plan_row(connection, task_id, name, now)
        await connection.execute(
            """
            UPDATE content_profiles
            SET name = ?, revision = revision + 1, updated_at = ?
            WHERE id = ?
            """,
            (name, now, task_id),
        )
        await connection.execute(
            "DELETE FROM content_profile_items WHERE profile_id = ?",
            (task_id,),
        )
        await self._insert_profile_items(connection, task_id, items)

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

    @staticmethod
    async def _begin_write(connection: aiosqlite.Connection) -> None:
        """写锁在手才做写前检查，跨连接的查重与写入才是原子的。"""
        if connection.in_transaction:
            await connection.rollback()
        await connection.execute("BEGIN IMMEDIATE")

    async def _commit_write(
        self,
        connection: aiosqlite.Connection,
        operation: Callable[[], Awaitable[None]],
        *,
        failure_message: str,
    ) -> None:
        commit_task: asyncio.Task[None] | None = None
        try:
            await self._begin_write(connection)
            await operation()
            commit_task = asyncio.create_task(connection.commit())
            await asyncio.shield(commit_task)
        except asyncio.CancelledError:
            await self._cleanup_cancelled_write(connection, commit_task)
            raise
        except (
            SiteNameConflictError,
            SiteRecordNotFoundError,
            SiteRevisionConflictError,
            SiteReferencedError,
            ProfileRecordNotFoundError,
            ProfileRevisionConflictError,
            ProfileReferencedError,
            TaskRecordNotFoundError,
            TaskOperationStaleError,
            TaskRevisionConflictError,
            DeliveryExecutionExistsError,
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
        items=items,
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _mark_incomplete_rows(
    connection: aiosqlite.Connection,
    where: str,
    params: tuple[object, ...],
    *,
    execution_where: str | None = None,
) -> None:
    execution_filter = execution_where or where
    await connection.execute(
        f"""
        UPDATE delivery_pages SET status = 'skipped', error_code = 'never_started'
        WHERE {where} AND status = 'pending'
        """,
        params,
    )
    await connection.execute(
        f"""
        UPDATE delivery_pages SET status = 'unknown', error_code = 'interrupted'
        WHERE {where} AND status = 'sending'
        """,
        params,
    )
    await connection.execute(
        f"""
        UPDATE delivery_targets AS t SET
            status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position AND p.status = 'unknown'
                ) THEN 'unknown'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position AND p.status = 'failed'
                ) THEN 'failed'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position AND p.status = 'success'
                ) AND EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position
                      AND p.status IN ('skipped', 'failed')
                ) THEN 'partial'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position AND p.status = 'success'
                ) THEN 'success'
                ELSE 'skipped'
            END,
            error_code = CASE
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position AND p.status = 'unknown'
                ) THEN 'interrupted'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = t.execution_id
                      AND p.position = t.position
                      AND p.status IN ('failed', 'skipped')
                ) THEN 'never_started'
                ELSE NULL
            END
        WHERE {where} AND t.status IN ('pending', 'sending')
        """,
        params,
    )
    await connection.execute(
        f"""
        UPDATE delivery_executions SET
            status = CASE
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status = 'unknown'
                ) THEN 'unknown'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status = 'success'
                ) AND EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status IN ('failed', 'skipped')
                ) THEN 'partial'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status = 'success'
                ) THEN 'success'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status = 'failed'
                ) THEN 'failed'
                ELSE 'stopped'
            END,
            error_code = CASE
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status = 'unknown'
                ) THEN 'interrupted'
                WHEN EXISTS (
                    SELECT 1 FROM delivery_pages p
                    WHERE p.execution_id = delivery_executions.id
                      AND p.status IN ('success', 'failed')
                ) THEN 'interrupted'
                ELSE 'never_started'
            END,
            updated_at = ?
        WHERE {execution_filter}
          AND status IN ('preparing', 'running')
        """,
        (_utc_now(), *params),
    )


def _row_to_delivery(
    row: aiosqlite.Row,
    target_rows: list[aiosqlite.Row],
    page_rows: list[aiosqlite.Row],
) -> StoredDeliveryExecution:
    return StoredDeliveryExecution(
        id=row["id"], task_id=row["task_id"], task_name=row["task_name"],
        task_revision=int(row["task_revision"]),
        profile_id=row["profile_id"],
        profile_revision=int(row["profile_revision"]),
        site_id=row["site_id"], site_revision=int(row["site_revision"]),
        output_format=row["output_format"], owner_token=row["owner_token"],
        content_hash=row["content_hash"],
        content_status=row["content_status"],
        failed_sections_json=row["failed_sections_json"],
        content_sources_json=(
            row["content_sources_json"]
            if "content_sources_json" in row.keys() else None
        ),
        image_model=(
            row["image_model"]
            if "image_model" in row.keys() else None
        ),
        status=row["status"], error_code=row["error_code"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        targets=tuple(
            StoredDeliveryTarget(
                execution_id=r["execution_id"], position=int(r["position"]),
                umo=r["umo"], status=r["status"], error_code=r["error_code"],
            ) for r in target_rows
        ),
        pages=tuple(
            StoredDeliveryPage(
                execution_id=r["execution_id"], position=int(r["position"]),
                page_index=int(r["page_index"]), status=r["status"],
                error_code=r["error_code"],
                image_generated=(
                    r["image_generated"]
                    if "image_generated" in r.keys() else None
                ),
            ) for r in page_rows
        ),
    )


def _iso_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_to_task(
    row: aiosqlite.Row,
    umos: tuple[str, ...],
    sites: tuple[str, ...] = (),
    items: tuple[StoredProfileItem, ...] = (),
) -> StoredTask:
    return StoredTask(
        id=row["id"],
        name=row["name"],
        profile_id=row["profile_id"],
        items=items,
        output_format=row["output_format"],
        image_style=str(row["image_style"] or "default"),
        umos=umos,
        sites=sites,
        enabled=bool(row["enabled"]),
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        schedule_kind=row["schedule_kind"],
        schedule_timezone=row["schedule_timezone"],
        schedule_time=row["schedule_time"],
        schedule_weekdays=row["schedule_weekdays"],
        schedule_start_at=row["schedule_start_at"],
        schedule_interval_minutes=row["schedule_interval_minutes"],
        schedule_expression=row["schedule_expression"],
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
        source_kind=str(row["source_kind"] or "public"),
        bearer_token=row["bearer_token"],
        revision=int(row["revision"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
