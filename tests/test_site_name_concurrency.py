from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from core.sites import (
    SiteCreate,
    SiteService,
    SiteUpdate,
    SiteValidationError,
)

CONFLICT_TEXT = "已存在同名站点，请换一个名称"


def make_create(name: str, base_url: str = "https://api.example.com/news") -> SiteCreate:
    return SiteCreate(name=name, base_url=base_url)


def make_update(site, **overrides: object) -> SiteUpdate:
    values: dict[str, object] = {
        "name": site.name,
        "base_url": site.base_url,
        "timeout_seconds": site.timeout_seconds,
        "expected_revision": site.revision,
    }
    values.update(overrides)
    return SiteUpdate(**values)  # type: ignore[arg-type]


class SiteNameConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    """两个独立连接争用同一数据库时，名称检查必须与写入互为原子。"""

    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.first = SiteService(self.db_path)
        self.second = SiteService(self.db_path)
        await self.first.open()
        await self.second.open()

    async def asyncTearDown(self) -> None:
        await self.first.close()
        await self.second.close()
        self._temporary_directory.cleanup()

    def _names(self) -> list[str]:
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute("SELECT name FROM sites").fetchall()
        return [row[0] for row in rows]

    async def _race(self, first, second) -> list[object]:
        """让两个连接的写操作在同一时刻出发，暴露检查与写入之间的空档。"""
        start = asyncio.Event()

        async def go(coro):
            await start.wait()
            return await coro

        tasks = [
            asyncio.create_task(go(first)),
            asyncio.create_task(go(second)),
        ]
        start.set()
        return await asyncio.gather(*tasks, return_exceptions=True)

    async def test_concurrent_create_keeps_single_row(self) -> None:
        results = await self._race(
            self.first.create_site(make_create("并发站点")),
            self.second.create_site(make_create("并发站点")),
        )

        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, BaseException)]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(failures), 1, results)
        failure = failures[0]
        self.assertIsInstance(failure, SiteValidationError)
        assert isinstance(failure, SiteValidationError)
        self.assertEqual(failure.field, "name")
        self.assertIn(CONFLICT_TEXT, str(failure))
        self.assertEqual(self._names(), ["并发站点"])

    async def test_losing_connection_stays_usable_after_conflict(self) -> None:
        results = await self._race(
            self.first.create_site(make_create("重复名称")),
            self.second.create_site(make_create("重复名称")),
        )
        loser = self.first if isinstance(results[0], BaseException) else self.second
        self.assertEqual(sum(isinstance(item, BaseException) for item in results), 1)

        created = await loser.create_site(make_create("另一个名称"))
        self.assertEqual(created.name, "另一个名称")
        names = sorted(site.name for site in await loser.list_sites())
        self.assertEqual(names, ["另一个名称", "重复名称"])

    async def test_concurrent_rename_keeps_names_distinct(self) -> None:
        left = await self.first.create_site(make_create("站点甲"))
        right = await self.second.create_site(make_create("站点乙"))
        results = await self._race(
            self.first.update_site(left.id, make_update(left, name="统一名称")),
            self.second.update_site(right.id, make_update(right, name="统一名称")),
        )

        successes = [item for item in results if not isinstance(item, Exception)]
        failures = [item for item in results if isinstance(item, BaseException)]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(failures), 1, results)
        failure = failures[0]
        self.assertIsInstance(failure, SiteValidationError)
        assert isinstance(failure, SiteValidationError)
        self.assertEqual(failure.field, "name")
        self.assertIn(CONFLICT_TEXT, str(failure))
        names = self._names()
        self.assertEqual(len(set(names)), 2, names)
        self.assertIn("统一名称", names)

    async def test_rename_loser_can_still_edit_other_fields(self) -> None:
        left = await self.first.create_site(make_create("站点甲"))
        right = await self.second.create_site(make_create("站点乙"))
        results = await self._race(
            self.first.update_site(left.id, make_update(left, name="统一名称")),
            self.second.update_site(right.id, make_update(right, name="统一名称")),
        )
        loser_service, loser_site = (
            (self.second, right)
            if isinstance(results[1], BaseException)
            else (self.first, left)
        )
        self.assertEqual(sum(isinstance(item, BaseException) for item in results), 1)

        updated = await loser_service.update_site(
            loser_site.id,
            make_update(
                loser_site,
                base_url="https://other.example.com/news",
            ),
        )
        self.assertEqual(updated.base_url, "https://other.example.com/news")
        self.assertEqual(len(self._names()), 2)


class LegacyDuplicateNameTest(unittest.IsolatedAsyncioTestCase):
    """旧库里已有的同名记录不删除、不改名，但不能因此再引入新冲突。"""

    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.service = SiteService(self.db_path)
        await self.service.open()

    async def asyncTearDown(self) -> None:
        await self.service.close()
        self._temporary_directory.cleanup()

    async def _legacy_duplicate(self) -> tuple[object, object]:
        first = await self.service.create_site(make_create("旧库站点"))
        second = await self.service.create_site(make_create("待改重名"))
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE sites SET name = ? WHERE id = ?",
                ("旧库站点", second.id),
            )
            connection.commit()
        return first, second

    async def test_legacy_rows_stay_editable_under_their_own_name(self) -> None:
        first, _second = await self._legacy_duplicate()

        updated = await self.service.update_site(
            first.id,
            make_update(first, base_url="https://changed.example.com/news"),
        )
        self.assertEqual(updated.base_url, "https://changed.example.com/news")
        self.assertEqual(updated.name, "旧库站点")

    async def test_new_records_cannot_add_conflicts(self) -> None:
        await self._legacy_duplicate()

        with self.assertRaises(SiteValidationError) as created:
            await self.service.create_site(make_create("旧库站点"))
        self.assertEqual(created.exception.field, "name")
        self.assertIn(CONFLICT_TEXT, str(created.exception))

        other = await self.service.create_site(make_create("无关站点"))
        with self.assertRaises(SiteValidationError) as renamed:
            await self.service.update_site(
                other.id,
                make_update(other, name="旧库站点"),
            )
        self.assertEqual(renamed.exception.field, "name")
        self.assertIn(CONFLICT_TEXT, str(renamed.exception))

        names = sorted(site.name for site in await self.service.list_sites())
        self.assertEqual(names, ["无关站点", "旧库站点", "旧库站点"])


if __name__ == "__main__":
    unittest.main()
