from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.site_store import SCHEMA_VERSION
from core.profiles import (
    ContentProfileService,
    ProfileConflictError,
    ProfileCreate,
    ProfileNotFoundError,
    ProfileUpdate,
    ProfileValidationError,
    normalize_profile_items,
)
from core.sites import (
    CredentialAction,
    SiteCreate,
    SiteReferencedProfilesError,
    SiteService,
    SiteUpdate,
    SiteValidationError,
)


def item(content_type: str, limit=None) -> dict:
    return {
        "content_type": content_type,
        "params": {},
        "limit": limit,
    }


class ProfileValidationTest(unittest.TestCase):
    def test_items_require_one_to_five_unique_supported_types(self) -> None:
        with self.assertRaises(ProfileValidationError):
            normalize_profile_items([])
        with self.assertRaises(ProfileValidationError):
            normalize_profile_items([item("daily_news")] * 6)
        with self.assertRaises(ProfileValidationError):
            normalize_profile_items(
                [item("daily_news"), item("daily_news")],
            )
        with self.assertRaises(ProfileValidationError):
            normalize_profile_items([item("unknown")])
        with self.assertRaises(ProfileValidationError):
            normalize_profile_items([{"content_type": "daily_news", "params": {"a": 1}}])
        with self.assertRaises(ProfileValidationError):
            normalize_profile_items([{"content_type": "daily_news", "params": []}])
        for limit in (True, False, 0, -1, 1.5, "10", 1001):
            with self.subTest(limit=limit):
                with self.assertRaises(ProfileValidationError):
                    normalize_profile_items([item("daily_news", limit)])
        parsed = normalize_profile_items(
            [
                item("daily_news", None),
                item("zhihu_hot", 1),
                item("bilibili_hot", 1000),
                {"content_type": "douyin_hot"},
            ],
        )
        self.assertEqual(
            [entry.content_type for entry in parsed],
            ["daily_news", "zhihu_hot", "bilibili_hot", "douyin_hot"],
        )
        self.assertEqual(parsed[1].limit, 1)
        self.assertEqual(parsed[2].limit, 1000)


class ProfileServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "plugin.sqlite3"
        self.sites = SiteService(self.db_path)
        await self.sites.open()
        self.profiles = ContentProfileService(self.sites)
        self.site = await self.sites.create_site(
            SiteCreate(
                name="组合站点",
                base_url="http://example.test/proxy",
                enabled=True,
                timeout_seconds=5,
                bearer_token="profile-token",
            ),
        )
        self.disabled = await self.sites.create_site(
            SiteCreate(
                name="停用站点",
                base_url="http://disabled.test/proxy",
                enabled=False,
                timeout_seconds=5,
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.sites.close()
        self.tmp.cleanup()

    async def _create(self, name="组合", site_id=None, items=None):
        return await self.profiles.create_profile(
            ProfileCreate(
                name=name,
                site_id=site_id or self.site.id,
                items=items
                if items is not None
                else [
                    item("daily_news", 2),
                    item("zhihu_hot", None),
                ],
            ),
        )

    async def test_crud_revision_and_persistence(self) -> None:
        profile = await self._create()
        self.assertEqual(profile.name, "组合")
        self.assertEqual(profile.site_id, self.site.id)
        self.assertEqual(
            [entry.content_type for entry in profile.items],
            ["daily_news", "zhihu_hot"],
        )
        self.assertEqual(profile.items[0].limit, 2)
        listed = await self.profiles.list_profiles()
        self.assertEqual([entry.id for entry in listed], [profile.id])

        updated = await self.profiles.update_profile(
            profile.id,
            ProfileUpdate(
                name="改名组合",
                site_id=self.disabled.id,
                items=[item("toutiao_hot", 3)],
                expected_revision=profile.revision,
            ),
        )
        self.assertEqual(updated.name, "改名组合")
        self.assertEqual(updated.site_id, self.disabled.id)
        self.assertEqual(updated.revision, profile.revision + 1)
        with self.assertRaises(ProfileConflictError):
            await self.profiles.update_profile(
                profile.id,
                ProfileUpdate(
                    name="冲突",
                    site_id=self.site.id,
                    items=[item("daily_news")],
                    expected_revision=profile.revision,
                ),
            )
        await self.sites.close()
        reopened = SiteService(self.db_path)
        await reopened.open()
        try:
            profiles = ContentProfileService(reopened)
            reloaded = await profiles.get_profile(profile.id)
            self.assertEqual(reloaded.name, "改名组合")
            self.assertEqual(reloaded.items[0].content_type, "toutiao_hot")
            self.assertEqual(reloaded.items[0].limit, 3)
        finally:
            await reopened.close()

    async def test_site_reference_blocks_delete_until_profile_removed(self) -> None:
        profile = await self._create()
        with self.assertRaises(SiteReferencedProfilesError) as raised:
            await self.sites.delete_site(self.site.id, self.site.revision)
        self.assertIn(profile.name, raised.exception.profile_names)
        self.assertEqual(
            (await self.sites.get_site(self.site.id)).id,
            self.site.id,
        )

        await self.profiles.delete_profile(profile.id, profile.revision)
        with self.assertRaises(ProfileNotFoundError):
            await self.profiles.get_profile(profile.id)
        await self.sites.delete_site(self.site.id, self.site.revision)

    async def test_concurrent_site_delete_and_profile_create_leave_no_dangling_reference(self) -> None:
        create_result, delete_result = await asyncio.gather(
            self.profiles.create_profile(
                ProfileCreate(
                    name="并发组合",
                    site_id=self.site.id,
                    items=[item("daily_news")],
                ),
            ),
            self.sites.delete_site(self.site.id, self.site.revision),
            return_exceptions=True,
        )
        site_exists = any(
            site.id == self.site.id for site in await self.sites.list_sites()
        )
        profiles = await self.profiles.list_profiles()
        if site_exists:
            self.assertIsInstance(delete_result, SiteReferencedProfilesError)
            self.assertEqual(len(profiles), 1)
        else:
            self.assertIsInstance(create_result, Exception)
            self.assertEqual(profiles, [])

    async def test_missing_site_and_validation_are_rejected(self) -> None:
        with self.assertRaises(Exception):
            await self._create(site_id="missing")
        with self.assertRaises(ProfileValidationError):
            await self._create(items=[])
        profile = await self._create()
        with self.assertRaises(ProfileValidationError):
            await self.profiles.update_profile(
                profile.id,
                ProfileUpdate(
                    name="x",
                    site_id=self.site.id,
                    items=[item("daily_news"), item("daily_news")],
                    expected_revision=profile.revision,
                ),
            )
        with self.assertRaises(SiteValidationError):
            await self.profiles.delete_profile(profile.id, True)


class ProfileMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_v1_database_migrates_without_losing_sites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v1.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE sites (
                    id TEXT PRIMARY KEY NOT NULL,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    timeout_seconds REAL NOT NULL CHECK (timeout_seconds >= 1 AND timeout_seconds <= 120),
                    bearer_token TEXT,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO sites VALUES
                    ('site-v1', '旧站点', 'http://old.test/proxy', 1, 5,
                     'old-token', 7, '2026-01-01T00:00:00+00:00',
                     '2026-01-01T00:00:00+00:00');
                PRAGMA user_version = 1;
                """,
            )
            connection.commit()
            connection.close()

            sites = SiteService(path)
            await sites.open()
            try:
                stored = await sites.get_site("site-v1")
                self.assertEqual(stored.name, "旧站点")
                self.assertEqual(stored.revision, 7)
                self.assertTrue(stored.has_bearer_token)
                profiles = ContentProfileService(sites)
                profile = await profiles.create_profile(
                    ProfileCreate(
                        name="迁移后组合",
                        site_id="site-v1",
                        items=[item("daily_news", 1)],
                    ),
                )
                self.assertEqual(profile.site_id, "site-v1")
                async with sites.store._require_connection().execute(
                    "PRAGMA user_version",
                ) as cursor:
                    version = (await cursor.fetchone())[0]
                self.assertEqual(version, SCHEMA_VERSION)
            finally:
                await sites.close()

    async def test_fresh_database_has_profile_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fresh.sqlite3"
            sites = SiteService(path)
            await sites.open()
            try:
                async with sites.store._require_connection().execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name IN
                    ('sites', 'content_profiles', 'content_profile_items')
                    ORDER BY name
                    """,
                ) as cursor:
                    rows = await cursor.fetchall()
                self.assertEqual(
                    [row[0] for row in rows],
                    ["content_profile_items", "content_profiles", "sites"],
                )
            finally:
                await sites.close()
