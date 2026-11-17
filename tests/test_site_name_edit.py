from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from contextlib import closing
import unittest
from pathlib import Path

from core.site_api import SiteApi
from core.sites import MAX_SITE_NAME_LENGTH, SiteService


class SiteNameEditTest(unittest.IsolatedAsyncioTestCase):
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

    @staticmethod
    def _update_payload(site: dict, **overrides: object) -> dict:
        payload: dict[str, object] = {
            "name": site["name"],
            "base_url": site["base_url"],
            "enabled": site["enabled"],
            "timeout_seconds": site["timeout_seconds"],
            "expected_revision": site["revision"],
        }
        payload.update(overrides)
        return payload

    async def test_clearing_name_on_edit_uses_normalized_base_url(self) -> None:
        site = await self._create("原名称")
        result = await self.api.update_site(
            site["id"],
            self._update_payload(
                site,
                name="   ",
                base_url="HTTPS://Proxy.Example.Test:8443/60s/api/",
            ),
        )
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(
            result.body["data"]["name"],
            "https://proxy.example.test:8443/60s/api",
        )

    async def _inject_legacy_duplicate(self, name: str) -> None:
        """模拟旧库：唯一性校验上线前已经存在两条同名记录。"""
        await self.service.close()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO sites (
                    id, name, base_url, enabled, timeout_seconds,
                    source_kind, bearer_token, revision, created_at, updated_at
                ) VALUES (
                    'legacy-duplicate', ?, 'https://legacy.example.test',
                    1, 10, 'public', NULL, 1,
                    '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00'
                )
                """,
                (name,),
            )
            connection.commit()
        self.service = SiteService(self.db_path)
        await self.service.open()
        self.api = SiteApi(self.service)

    async def test_legacy_duplicate_can_be_edited_without_renaming(self) -> None:
        site = await self._create("历史重名")
        await self._inject_legacy_duplicate("历史重名")

        current = (await self.api.get_site("legacy-duplicate")).body["data"]
        result = await self.api.update_site(
            "legacy-duplicate",
            self._update_payload(current, timeout_seconds=12),
        )
        self.assertEqual(result.status_code, 200, result.body)
        self.assertEqual(result.body["data"]["timeout_seconds"], 12)
        self.assertEqual(result.body["data"]["name"], "历史重名")

        renamed = await self.api.update_site(
            site["id"],
            self._update_payload(site, name="另一个名字"),
        )
        self.assertEqual(renamed.status_code, 200, renamed.body)

    async def test_legacy_duplicate_cannot_be_renamed_onto_another_name(self) -> None:
        await self._create("甲站点")
        await self._inject_legacy_duplicate("乙站点")
        second = (await self.api.create_site(
            {"name": "丙站点", "base_url": "https://c.example.test"},
        )).body["data"]
        current = (await self.api.get_site("legacy-duplicate")).body["data"]
        result = await self.api.update_site(
            "legacy-duplicate",
            self._update_payload(current, name="丙站点"),
        )
        self.assertEqual(result.status_code, 400, result.body)
        self.assertEqual(result.body["data"]["field"], "name")
        self.assertEqual(second["name"], "丙站点")

    async def test_concurrent_creates_with_same_name_allow_only_one(self) -> None:
        results = await asyncio.gather(
            *(
                self.api.create_site(
                    {"name": "并发站点", "base_url": f"https://c{index}.example.test"},
                )
                for index in range(4)
            ),
        )
        codes = sorted(item.status_code for item in results)
        self.assertEqual(codes, [201, 400, 400, 400], [item.body for item in results])
        sites = (await self.api.list_sites()).body["data"]["sites"]
        self.assertEqual(
            [item["name"] for item in sites].count("并发站点"), 1,
        )


class LongNamePrivacyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self._temporary_directory.name) / "sites.sqlite3"
        self.service = SiteService(self.db_path)
        await self.service.open()
        self.api = SiteApi(self.service)

    async def asyncTearDown(self) -> None:
        await self.service.close()
        self._temporary_directory.cleanup()

    async def test_long_url_and_proxy_path_become_the_default_name(self) -> None:
        base_url = (
            "https://gateway.example.test:9443/very/deep/proxy/path/for/60s/api"
        )
        result = await self.api.create_site({"name": "", "base_url": base_url})
        self.assertEqual(result.status_code, 201, result.body)
        name = result.body["data"]["name"]
        # 名称有 40 字符上限：兜底名按上限截断，保留反向代理路径的开头。
        self.assertLessEqual(len(name), MAX_SITE_NAME_LENGTH)
        self.assertEqual(name, base_url[: MAX_SITE_NAME_LENGTH - 1].rstrip() + "…")
        self.assertTrue(base_url.startswith(name[:-1]), name)

    async def test_name_over_the_limit_is_rejected(self) -> None:
        result = await self.api.create_site(
            {"name": "超" * (MAX_SITE_NAME_LENGTH + 1), "base_url": "https://a.example.test"},
        )
        self.assertEqual(result.status_code, 400, result.body)
        self.assertEqual(result.body["data"]["field"], "name")
        self.assertIn(
            f"长度不能超过 {MAX_SITE_NAME_LENGTH} 个字符",
            result.body["message"],
        )

    async def test_legacy_over_long_name_can_still_be_saved_unchanged(self) -> None:
        legacy_name = "旧库里的超长站点名称" * 6
        await self.service.close()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO sites (
                    id, name, base_url, enabled, timeout_seconds,
                    source_kind, bearer_token, revision, created_at, updated_at
                ) VALUES (
                    'legacy-long', ?, 'https://legacy.example.test',
                    1, 10, 'public', NULL, 1,
                    '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00'
                )
                """,
                (legacy_name,),
            )
            connection.commit()
        self.service = SiteService(self.db_path)
        await self.service.open()
        self.api = SiteApi(self.service)

        current = (await self.api.get_site("legacy-long")).body["data"]
        kept = await self.api.update_site(
            "legacy-long",
            {
                "name": legacy_name,
                "base_url": current["base_url"],
                "enabled": current["enabled"],
                "timeout_seconds": 12,
                "expected_revision": current["revision"],
            },
        )
        self.assertEqual(kept.status_code, 200, kept.body)
        self.assertEqual(kept.body["data"]["name"], legacy_name)

        renamed = await self.api.update_site(
            "legacy-long",
            {
                "name": "改短的名字",
                "base_url": current["base_url"],
                "enabled": current["enabled"],
                "timeout_seconds": 12,
                "expected_revision": kept.body["data"]["revision"],
            },
        )
        self.assertEqual(renamed.status_code, 200, renamed.body)
        self.assertEqual(renamed.body["data"]["name"], "改短的名字")

    async def test_default_name_of_public_site_keeps_no_credentials(self) -> None:
        result = await self.api.create_site(
            {"name": "", "base_url": "https://public.example.test/60s"},
        )
        data = result.body["data"]
        self.assertNotIn("bearer_token", data)
        self.assertFalse(data["has_bearer_token"])
        self.assertEqual(data["source_kind"], "public")


if __name__ == "__main__":
    unittest.main()
