from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.site_api import SiteApi
from core.sites import SiteService


class SiteNameConflictTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.service = SiteService(self.db_path)
        await self.service.open()
        self.api = SiteApi(self.service)

    async def asyncTearDown(self) -> None:
        await self.service.close()
        self._temporary_directory.cleanup()

    async def _create(self, name: str, base_url: str = "https://a.example.test") -> dict:
        result = await self.api.create_site({"name": name, "base_url": base_url})
        self.assertEqual(result.status_code, 201, result.body)
        return result.body["data"]

    def _assert_conflict(self, result) -> None:
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.body["status"], "error")
        self.assertEqual(result.body["data"]["code"], "validation_error")
        self.assertEqual(result.body["data"]["field"], "name")
        self.assertIn("已存在同名站点", result.body["message"])

    async def test_create_rejects_duplicate_name(self) -> None:
        await self._create("60s 站点")
        result = await self.api.create_site(
            {"name": "60s 站点", "base_url": "https://b.example.test"},
        )
        self._assert_conflict(result)
        sites = (await self.api.list_sites()).body["data"]["sites"]
        self.assertEqual(len(sites), 1)

    async def test_duplicate_detection_ignores_case_and_padding(self) -> None:
        await self._create("Main Site")
        result = await self.api.create_site(
            {"name": "  main site  ", "base_url": "https://b.example.test"},
        )
        self._assert_conflict(result)

    async def test_same_base_url_with_distinct_names_is_allowed(self) -> None:
        await self._create("主站", "https://a.example.test")
        await self._create("备用", "https://a.example.test")
        sites = (await self.api.list_sites()).body["data"]["sites"]
        self.assertEqual(len(sites), 2)

    async def test_rename_onto_existing_name_is_rejected(self) -> None:
        await self._create("主站")
        second = await self._create("备用", "https://b.example.test")
        result = await self.api.update_site(
            second["id"],
            {
                "name": "主站",
                "base_url": second["base_url"],
                "timeout_seconds": second["timeout_seconds"],
                "expected_revision": second["revision"],
            },
        )
        self._assert_conflict(result)
        unchanged = await self.api.get_site(second["id"])
        self.assertEqual(unchanged.body["data"]["name"], "备用")

    async def test_rename_keeping_own_name_with_new_case_is_allowed(self) -> None:
        site = await self._create("Main")
        result = await self.api.update_site(
            site["id"],
            {
                "name": "main",
                "base_url": site["base_url"],
                "timeout_seconds": site["timeout_seconds"],
                "expected_revision": site["revision"],
            },
        )
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["name"], "main")

    async def test_empty_name_falls_back_to_base_url(self) -> None:
        site = await self._create("", "https://a.example.test")
        self.assertEqual(site["name"], "https://a.example.test")
        result = await self.api.create_site(
            {"name": "   ", "base_url": "https://a.example.test"},
        )
        self._assert_conflict(result)


if __name__ == "__main__":
    unittest.main()
