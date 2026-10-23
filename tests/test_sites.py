from __future__ import annotations

import asyncio
import dataclasses
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.site_store import SCHEMA_VERSION, StoredSite
from core.sites import (
    MAX_BASE_URL_LENGTH,
    MAX_BEARER_TOKEN_LENGTH,
    MAX_SITE_NAME_LENGTH,
    CredentialAction,
    SiteConflictError,
    SiteCreate,
    SiteNotFoundError,
    SiteService,
    SiteStorageError,
    SiteUpdate,
    SiteValidationError,
    normalize_base_url,
    normalize_bearer_token,
    normalize_enabled,
    normalize_revision,
    normalize_site_name,
    normalize_timeout_seconds,
)


def make_create(**overrides: object) -> SiteCreate:
    values: dict[str, object] = {
        "name": "测试站点",
        "base_url": "https://api.example.com/news",
    }
    values.update(overrides)
    return SiteCreate(**values)  # type: ignore[arg-type]


def make_update(site, **overrides: object) -> SiteUpdate:
    values: dict[str, object] = {
        "name": site.name,
        "base_url": site.base_url,
        "enabled": site.enabled,
        "timeout_seconds": site.timeout_seconds,
        "expected_revision": site.revision,
    }
    values.update(overrides)
    return SiteUpdate(**values)  # type: ignore[arg-type]


class BaseUrlValidationTest(unittest.TestCase):
    def test_valid_urls_are_normalized(self) -> None:
        cases = {
            "https://api.example.com": "https://api.example.com",
            "HTTPS://API.Example.COM/News/": "https://api.example.com/News",
            "http://localhost:8080/": "http://localhost:8080",
            "http://127.0.0.1:9000/60s": "http://127.0.0.1:9000/60s",
            "http://192.168.1.10:9000/": "http://192.168.1.10:9000",
            "http://10.0.0.5:3000/docker/prefix/": (
                "http://10.0.0.5:3000/docker/prefix"
            ),
            "http://[2001:db8::1]:8080/api/": "http://[2001:db8::1]:8080/api",
            "http://[::1]/": "http://[::1]",
            "http://my_service:8080/": "http://my_service:8080",
            "http://host.docker.internal:1234/": (
                "http://host.docker.internal:1234"
            ),
            "https://api.example.com:443/proxy/60s/": (
                "https://api.example.com:443/proxy/60s"
            ),
            "https://api.example.com/api/v2/news": (
                "https://api.example.com/api/v2/news"
            ),
            "https://api.example.com/v2x": "https://api.example.com/v2x",
            "https://api.example.com/news//": "https://api.example.com/news",
            "  https://api.example.com/news/  ": "https://api.example.com/news",
            "\thttps://api.example.com/news/\n": "https://api.example.com/news",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_base_url(raw), expected)

    def test_invalid_url_errors_do_not_echo_input(self) -> None:
        secret = "super-secret-token"
        with self.assertRaises(SiteValidationError) as raised:
            normalize_base_url(f"https://user:{secret}@example.com")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("user:", str(raised.exception))

    def test_invalid_urls_are_rejected(self) -> None:
        cases = [
            "",
            "   ",
            "api.example.com",
            "ftp://example.com",
            "file:///tmp/site",
            "http://",
            "http:///path",
            "http://:8080",
            "http://user@example.com",
            "http://user:pass@example.com",
            "http://example.com/path?x=1",
            "http://example.com/path#fragment",
            "http://example.com/path?",
            "http://example.com/path#",
            "http://example.com:abc",
            "http://example.com:0",
            "http://example.com:65536",
            "http://example.com:",
            "http://-bad.example.com",
            "http://bad-.example.com",
            "http://bad..example.com",
            "http://exa mple.com",
            "http://example.com/a b",
            "http://exa\nmple.com",
            "http://exa\rmple.com",
            "http://example.com/a\tb",
            "http://example.com/a\x00b",
            "http://example.com/a\rb",
            "http://999.1.1.1",
            "http://[gggg::1]",
            "http://::1",
            "http://[fe80::1%25en0]",
            "http://example.com/v2",
            "http://example.com/v2/",
            "http://example.com/api/v2/",
            "http://example.com\\@evil.com",
            "https://api.example.com/" + "a" * MAX_BASE_URL_LENGTH,
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(SiteValidationError):
                    normalize_base_url(raw)


class FieldValidationTest(unittest.TestCase):
    def test_name_normalization_and_limits(self) -> None:
        self.assertEqual(normalize_site_name("  测试站点  "), "测试站点")
        self.assertEqual(
            normalize_site_name("a" * MAX_SITE_NAME_LENGTH),
            "a" * MAX_SITE_NAME_LENGTH,
        )
        for value in [
            None,
            1,
            "",
            "   ",
            "a" * (MAX_SITE_NAME_LENGTH + 1),
            "bad\nname",
            "bad\x7fname",
        ]:
            with self.subTest(value=value):
                with self.assertRaises(SiteValidationError):
                    normalize_site_name(value)

    def test_timeout_rejects_bool_and_invalid_numbers(self) -> None:
        self.assertEqual(normalize_timeout_seconds(1), 1.0)
        self.assertEqual(normalize_timeout_seconds(120), 120.0)
        self.assertEqual(normalize_timeout_seconds(1.5), 1.5)
        for value in [
            True,
            False,
            0,
            0.9,
            120.1,
            math.nan,
            math.inf,
            "10",
            None,
        ]:
            with self.subTest(value=value):
                with self.assertRaises(SiteValidationError):
                    normalize_timeout_seconds(value)

    def test_enabled_must_be_bool(self) -> None:
        self.assertTrue(normalize_enabled(True))
        self.assertFalse(normalize_enabled(False))
        for value in [1, 0, "true", None]:
            with self.subTest(value=value):
                with self.assertRaises(SiteValidationError):
                    normalize_enabled(value)

    def test_bearer_token_limits_and_characters(self) -> None:
        self.assertIsNone(normalize_bearer_token(None))
        self.assertIsNone(normalize_bearer_token("   "))
        self.assertEqual(normalize_bearer_token("  token-123  "), "token-123")
        self.assertEqual(
            normalize_bearer_token("a" * MAX_BEARER_TOKEN_LENGTH),
            "a" * MAX_BEARER_TOKEN_LENGTH,
        )
        for value in [
            123,
            "a" * (MAX_BEARER_TOKEN_LENGTH + 1),
            "has space",
            "has\nnewline",
            "非ASCII",
            "Bearer token",
        ]:
            with self.subTest(value=value):
                with self.assertRaises(SiteValidationError):
                    normalize_bearer_token(value)

    def test_revision_must_be_positive_int(self) -> None:
        self.assertEqual(normalize_revision(1), 1)
        for value in [True, False, 0, -1, 1.0, "1", None]:
            with self.subTest(value=value):
                with self.assertRaises(SiteValidationError):
                    normalize_revision(value)

    def test_internal_record_repr_hides_token(self) -> None:
        stored = StoredSite(
            id="a" * 32,
            name="测试",
            base_url="https://api.example.com",
            enabled=False,
            timeout_seconds=10.0,
            bearer_token="top-secret-token",
        )
        self.assertNotIn("top-secret-token", repr(stored))

    def test_input_repr_and_errors_hide_token(self) -> None:
        create = SiteCreate(
            name="测试",
            base_url="https://api.example.com",
            bearer_token="top-secret-token",
        )
        update = SiteUpdate(
            name="测试",
            base_url="https://api.example.com",
            enabled=False,
            timeout_seconds=10,
            expected_revision=1,
            credential_action=CredentialAction.SET,
            bearer_token="top-secret-token",
        )
        self.assertNotIn("top-secret-token", repr(create))
        self.assertNotIn("top-secret-token", repr(update))

        with self.assertRaises(SiteValidationError) as raised:
            normalize_bearer_token("secret token")
        self.assertNotIn("secret token", str(raised.exception))


class SiteServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._root = Path(self._temporary_directory.name)
        self.db_path = self._root / "sites.sqlite3"
        self.service = SiteService(self.db_path)
        await self.service.open()

    async def asyncTearDown(self) -> None:
        await self.service.close()
        self._temporary_directory.cleanup()

    def _read_token(self, site_id: str) -> str | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT bearer_token FROM sites WHERE id = ?",
                (site_id,),
            ).fetchone()
        return None if row is None else row[0]

    async def _cancel_blocked_write(self, operation) -> None:
        """Hold an external write lock, cancel a queued write, then release it."""
        external = sqlite3.connect(self.db_path, isolation_level=None, timeout=5)
        external.execute("BEGIN IMMEDIATE")
        connection = self.service._store._connection
        self.assertIsNotNone(connection)
        assert connection is not None
        write_queued = asyncio.Event()
        original_execute = connection._execute

        async def tracked_execute(fn, *args, **kwargs):
            sql = args[0] if args else ""
            if isinstance(sql, str) and sql.lstrip().upper().startswith(
                ("INSERT", "UPDATE", "DELETE"),
            ):
                write_queued.set()
            return await original_execute(fn, *args, **kwargs)

        connection._execute = tracked_execute
        task = asyncio.create_task(operation)
        try:
            await asyncio.wait_for(write_queued.wait(), timeout=2)
            task.cancel()
            if external.in_transaction:
                external.rollback()
            external.close()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass
            try:
                if external.in_transaction:
                    external.rollback()
            except sqlite3.ProgrammingError:
                pass
            try:
                external.close()
            except sqlite3.ProgrammingError:
                pass
            connection._execute = original_execute

    async def test_cancelled_create_does_not_commit_with_next_write(self) -> None:
        await self._cancel_blocked_write(
            self.service.create_site(make_create(name="被取消的站点")),
        )
        await self.service.create_site(make_create(name="正常站点"))
        await self.service.close()
        self.service = SiteService(self.db_path)
        await self.service.open()
        names = {site.name for site in await self.service.list_sites()}
        self.assertEqual(names, {"正常站点"})

    async def test_cancelled_update_does_not_commit_with_next_write(self) -> None:
        site = await self.service.create_site(make_create(name="原始名称"))
        await self._cancel_blocked_write(
            self.service.update_site(
                site.id,
                make_update(site, name="被取消的编辑"),
            ),
        )
        await self.service.create_site(make_create(name="正常站点"))
        await self.service.close()
        self.service = SiteService(self.db_path)
        await self.service.open()
        current = await self.service.get_site(site.id)
        self.assertEqual(current.name, "原始名称")
        self.assertEqual(current.revision, 1)

    async def test_cancel_cleanup_failure_marks_store_unusable(self) -> None:
        await self.service.create_site(make_create(name="原始站点"))
        connection = self.service._store._connection
        self.assertIsNotNone(connection)
        assert connection is not None
        original_rollback = connection.rollback

        async def failing_rollback() -> None:
            raise RuntimeError("forced rollback failure")

        connection.rollback = failing_rollback
        try:
            await self._cancel_blocked_write(
                self.service.create_site(make_create(name="取消的站点")),
            )
        finally:
            connection.rollback = original_rollback

        self.assertFalse(self.service.is_open)
        with self.assertRaises(SiteStorageError):
            await self.service.list_sites()
        with self.assertRaises(SiteStorageError):
            await self.service.open()

    async def test_cancelled_delete_does_not_commit_with_next_write(self) -> None:
        site = await self.service.create_site(make_create(name="应保留的站点"))
        await self._cancel_blocked_write(
            self.service.delete_site(site.id, site.revision),
        )
        await self.service.create_site(make_create(name="正常站点"))
        await self.service.close()
        self.service = SiteService(self.db_path)
        await self.service.open()
        current = await self.service.get_site(site.id)
        self.assertEqual(current.name, "应保留的站点")

    async def test_create_defaults_and_get(self) -> None:
        site = await self.service.create_site(
            make_create(
                name="  测试站点  ",
                base_url="https://api.example.com/news/",
            ),
        )
        self.assertTrue(site.id)
        self.assertEqual(site.name, "测试站点")
        self.assertEqual(site.base_url, "https://api.example.com/news")
        self.assertFalse(site.enabled)
        self.assertEqual(site.timeout_seconds, 10.0)
        self.assertFalse(site.has_bearer_token)
        self.assertEqual(site.revision, 1)
        self.assertFalse(hasattr(site, "bearer_token"))
        self.assertEqual(await self.service.get_site(site.id), site)
        self.assertEqual(await self.service.list_sites(), [site])

    async def test_crud_persists_across_reopen(self) -> None:
        site = await self.service.create_site(
            make_create(bearer_token="original-token"),
        )
        updated = await self.service.update_site(
            site.id,
            make_update(
                site,
                name="更新后的站点",
                base_url="http://10.0.0.5:8080/proxy/",
                enabled=True,
                timeout_seconds=45,
            ),
        )
        self.assertEqual(updated.revision, 2)
        self.assertTrue(updated.has_bearer_token)
        self.assertEqual(
            updated.base_url,
            "http://10.0.0.5:8080/proxy",
        )

        await self.service.close()
        self.service = SiteService(self.db_path)
        await self.service.open()
        self.assertEqual(await self.service.get_site(site.id), updated)

        await self.service.delete_site(site.id, updated.revision)
        await self.service.close()
        await self.service.open()
        self.assertEqual(await self.service.list_sites(), [])
        with self.assertRaises(SiteNotFoundError):
            await self.service.get_site(site.id)

    async def test_update_revision_conflict_does_not_overwrite(self) -> None:
        site = await self.service.create_site(make_create())
        first = await self.service.update_site(
            site.id,
            make_update(site, name="第一次编辑"),
        )
        self.assertEqual(first.revision, 2)

        with self.assertRaises(SiteConflictError) as raised:
            await self.service.update_site(
                site.id,
                make_update(site, name="第二次编辑"),
            )
        self.assertEqual(raised.exception.expected_revision, 1)
        self.assertEqual(raised.exception.actual_revision, 2)
        current = await self.service.get_site(site.id)
        self.assertEqual(current.name, "第一次编辑")
        self.assertEqual(current.revision, 2)

    async def test_delete_revision_conflict_keeps_record(self) -> None:
        site = await self.service.create_site(make_create())
        updated = await self.service.update_site(
            site.id,
            make_update(site, name="保留"),
        )
        with self.assertRaises(SiteConflictError) as raised:
            await self.service.delete_site(site.id, site.revision)
        self.assertEqual(raised.exception.actual_revision, updated.revision)
        self.assertEqual(await self.service.get_site(site.id), updated)

        await self.service.delete_site(site.id, updated.revision)
        with self.assertRaises(SiteNotFoundError):
            await self.service.get_site(site.id)

    async def test_credentials_keep_set_clear(self) -> None:
        site = await self.service.create_site(
            make_create(bearer_token="alpha-token"),
        )
        self.assertTrue(site.has_bearer_token)
        self.assertEqual(self._read_token(site.id), "alpha-token")
        self.assertNotIn("alpha-token", repr(site))
        self.assertNotIn("alpha-token", str(dataclasses.asdict(site)))

        kept = await self.service.update_site(
            site.id,
            make_update(site, credential_action=CredentialAction.KEEP),
        )
        self.assertTrue(kept.has_bearer_token)
        self.assertEqual(self._read_token(site.id), "alpha-token")

        replaced = await self.service.update_site(
            site.id,
            make_update(
                kept,
                credential_action=CredentialAction.SET,
                bearer_token="beta-token",
            ),
        )
        self.assertTrue(replaced.has_bearer_token)
        self.assertEqual(self._read_token(site.id), "beta-token")

        with self.assertRaises(SiteValidationError):
            await self.service.update_site(
                site.id,
                make_update(
                    replaced,
                    credential_action=CredentialAction.SET,
                    bearer_token="",
                ),
            )
        self.assertEqual(self._read_token(site.id), "beta-token")

        cleared = await self.service.update_site(
            site.id,
            make_update(replaced, credential_action=CredentialAction.CLEAR),
        )
        self.assertFalse(cleared.has_bearer_token)
        self.assertIsNone(self._read_token(site.id))

        with self.assertRaises(SiteValidationError):
            await self.service.update_site(
                site.id,
                make_update(
                    cleared,
                    credential_action=CredentialAction.KEEP,
                    bearer_token="should-not-be-used",
                ),
            )
        with self.assertRaises(SiteValidationError):
            await self.service.update_site(
                site.id,
                make_update(
                    cleared,
                    credential_action=CredentialAction.CLEAR,
                    bearer_token="should-not-be-used",
                ),
            )

    async def test_validation_failure_keeps_existing_data(self) -> None:
        site = await self.service.create_site(
            make_create(bearer_token="alpha-token"),
        )
        with self.assertRaises(SiteValidationError):
            await self.service.update_site(
                site.id,
                make_update(site, base_url="ftp://example.com"),
            )
        self.assertEqual(await self.service.get_site(site.id), site)
        self.assertEqual(self._read_token(site.id), "alpha-token")

        with self.assertRaises(SiteConflictError):
            await self.service.update_site(
                site.id,
                make_update(site, expected_revision=99, name="不应写入"),
            )
        self.assertEqual(await self.service.get_site(site.id), site)

        with self.assertRaises(SiteValidationError):
            await self.service.create_site(make_create(base_url="http://x/v2"))
        self.assertEqual(await self.service.list_sites(), [site])

    async def test_sql_write_failure_rolls_back(self) -> None:
        site = await self.service.create_site(
            make_create(name="原始名称", bearer_token="alpha-token"),
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_update BEFORE UPDATE ON sites "
                "BEGIN SELECT RAISE(ABORT, 'forced failure'); END",
            )

        with self.assertRaises(SiteStorageError):
            await self.service.update_site(
                site.id,
                make_update(site, name="不应写入"),
            )
        current = await self.service.get_site(site.id)
        self.assertEqual(current, site)
        self.assertEqual(self._read_token(site.id), "alpha-token")

        with sqlite3.connect(self.db_path) as connection:
            connection.execute("DROP TRIGGER fail_update")
            connection.execute(
                "CREATE TRIGGER fail_insert BEFORE INSERT ON sites "
                "BEGIN SELECT RAISE(ABORT, 'forced failure'); END",
            )
        with self.assertRaises(SiteStorageError):
            await self.service.create_site(make_create(name="也不应写入"))
        self.assertEqual(await self.service.list_sites(), [site])

    async def test_storage_path_and_schema_version(self) -> None:
        self.assertTrue(self.db_path.exists())
        with sqlite3.connect(self.db_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            table = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'sites'",
            ).fetchone()
        self.assertEqual(version, SCHEMA_VERSION)
        self.assertIsNotNone(table)

    async def test_open_creates_nested_directory(self) -> None:
        nested = self._root / "nested" / "data" / "sites.sqlite3"
        service = SiteService(nested)
        await service.open()
        try:
            self.assertTrue(nested.exists())
            self.assertTrue(service.is_open)
        finally:
            await service.close()

    async def test_open_is_idempotent_and_reopen_keeps_data(self) -> None:
        await self.service.open()
        site = await self.service.create_site(make_create())
        await self.service.close()
        await self.service.open()
        self.assertEqual(await self.service.get_site(site.id), site)

    async def test_closed_storage_raises_storage_error(self) -> None:
        await self.service.close()
        self.assertFalse(self.service.is_open)
        with self.assertRaises(SiteStorageError):
            await self.service.list_sites()
        with self.assertRaises(SiteStorageError):
            await self.service.create_site(make_create())

    async def test_missing_site_raises_not_found(self) -> None:
        with self.assertRaises(SiteNotFoundError):
            await self.service.get_site("f" * 32)
        with self.assertRaises(SiteNotFoundError):
            await self.service.delete_site("f" * 32, 1)
        with self.assertRaises(SiteValidationError):
            await self.service.get_site("")

    async def test_unsupported_schema_version_is_rejected(self) -> None:
        future_path = self._root / "future.sqlite3"
        with sqlite3.connect(future_path) as connection:
            connection.execute("PRAGMA user_version = 999")
        service = SiteService(future_path)
        with self.assertRaises(SiteStorageError):
            await service.open()
        self.assertFalse(service.is_open)
        await service.close()

    async def test_open_failure_releases_state(self) -> None:
        blocker = self._root / "not-a-directory"
        blocker.write_text("x", encoding="utf-8")
        service = SiteService(blocker / "sites.sqlite3")
        with self.assertRaises(SiteStorageError):
            await service.open()
        self.assertFalse(service.is_open)
        await service.close()

    async def test_update_and_delete_revision_input_is_validated(self) -> None:
        site = await self.service.create_site(make_create())
        for bad in [True, False, 0, -1, 1.5, "1", None]:
            with self.subTest(value=bad):
                with self.assertRaises(SiteValidationError):
                    await self.service.update_site(
                        site.id,
                        make_update(site, expected_revision=bad),
                    )
                with self.assertRaises(SiteValidationError):
                    await self.service.delete_site(site.id, bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
