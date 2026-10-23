from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.site_api import SiteApi
from core.sites import SiteService


class SiteApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._root = Path(self._temporary_directory.name)
        self.db_path = self._root / "sites.sqlite3"
        self.service = SiteService(self.db_path)
        await self.service.open()
        self.api = SiteApi(self.service)

    async def asyncTearDown(self) -> None:
        await self.service.close()
        self._temporary_directory.cleanup()

    async def _create(self, **overrides: object) -> dict:
        payload: dict[str, object] = {
            "name": "测试站点",
            "base_url": "https://api.example.com/news",
        }
        payload.update(overrides)
        result = await self.api.create_site(payload)
        self.assertEqual(result.status_code, 201)
        self.assertEqual(result.body["status"], "ok")
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

    def assert_error(self, result, status_code: int, code: str) -> None:
        self.assertEqual(result.status_code, status_code)
        self.assertEqual(result.body["status"], "error")
        self.assertEqual(result.body["data"]["code"], code)

    async def test_list_empty(self) -> None:
        result = await self.api.list_sites()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, {"status": "ok", "data": {"sites": []}})

    async def test_create_list_get_and_public_fields(self) -> None:
        site = await self._create(
            name="  内网站点  ",
            base_url="http://10.0.0.5:8080/proxy/",
            enabled=True,
            timeout_seconds=25,
        )
        self.assertEqual(site["name"], "内网站点")
        self.assertEqual(site["base_url"], "http://10.0.0.5:8080/proxy")
        self.assertTrue(site["enabled"])
        self.assertEqual(site["timeout_seconds"], 25.0)
        self.assertFalse(site["has_bearer_token"])
        self.assertEqual(site["revision"], 1)
        self.assertEqual(
            set(site),
            {
                "id",
                "name",
                "base_url",
                "enabled",
                "timeout_seconds",
                "has_bearer_token",
                "revision",
                "created_at",
                "updated_at",
            },
        )

        listed = await self.api.list_sites()
        self.assertEqual(listed.body["data"]["sites"], [site])
        fetched = await self.api.get_site(site["id"])
        self.assertEqual(fetched.body["data"], site)

    async def test_create_defaults(self) -> None:
        site = await self._create()
        self.assertFalse(site["enabled"])
        self.assertEqual(site["timeout_seconds"], 10.0)
        self.assertEqual(site["revision"], 1)

    async def test_invalid_input_and_body(self) -> None:
        bad = await self.api.create_site(
            {"name": "站点", "base_url": "ftp://example.com"},
        )
        self.assert_error(bad, 400, "validation_error")
        self.assertEqual(bad.body["data"]["field"], "base_url")

        for payload in (None, [], "text", 1):
            with self.subTest(payload=payload):
                result = await self.api.create_site(payload)
                self.assert_error(result, 400, "invalid_body")

        missing_revision = await self.api.delete_site("a" * 32, {})
        self.assert_error(missing_revision, 400, "validation_error")
        self.assertEqual(
            missing_revision.body["data"]["field"],
            "expected_revision",
        )

    async def test_not_found(self) -> None:
        result = await self.api.get_site("f" * 32)
        self.assert_error(result, 404, "not_found")
        result = await self.api.delete_site("f" * 32, {"expected_revision": 1})
        self.assert_error(result, 404, "not_found")

    async def test_update_and_delete(self) -> None:
        site = await self._create()
        updated = await self.api.update_site(
            site["id"],
            self._update_payload(
                site,
                name="更新后",
                enabled=True,
                timeout_seconds=45,
            ),
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.body["data"]["name"], "更新后")
        self.assertEqual(updated.body["data"]["revision"], 2)

        deleted = await self.api.delete_site(
            site["id"],
            {"expected_revision": 2},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            deleted.body["data"],
            {"deleted_id": site["id"]},
        )
        self.assert_error(await self.api.get_site(site["id"]), 404, "not_found")

    async def test_update_conflict_does_not_overwrite(self) -> None:
        site = await self._create(name="原始名称")
        first = await self.api.update_site(
            site["id"],
            self._update_payload(site, name="第一次编辑"),
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.body["data"]["revision"], 2)

        second = await self.api.update_site(
            site["id"],
            self._update_payload(site, name="第二次编辑"),
        )
        self.assert_error(second, 409, "revision_conflict")
        self.assertEqual(second.body["data"]["expected_revision"], 1)
        self.assertEqual(second.body["data"]["actual_revision"], 2)

        current = await self.api.get_site(site["id"])
        self.assertEqual(current.body["data"]["name"], "第一次编辑")
        self.assertEqual(current.body["data"]["revision"], 2)

    async def test_delete_conflict_keeps_record(self) -> None:
        site = await self._create()
        first = await self.api.update_site(
            site["id"],
            self._update_payload(site, name="保留"),
        )
        self.assertEqual(first.status_code, 200)
        conflict = await self.api.delete_site(
            site["id"],
            {"expected_revision": 1},
        )
        self.assert_error(conflict, 409, "revision_conflict")
        self.assertEqual(conflict.body["data"]["actual_revision"], 2)
        self.assertEqual(
            (await self.api.get_site(site["id"])).body["data"]["name"],
            "保留",
        )

    def _read_token(self, site_id: str) -> str | None:
        with sqlite3.connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT bearer_token FROM sites WHERE id = ?",
                (site_id,),
            ).fetchone()
        return None if row is None else row[0]

    async def test_credentials_keep_set_clear_and_redaction(self) -> None:
        site = await self._create(bearer_token="alpha-secret")
        self.assertTrue(site["has_bearer_token"])
        self.assertNotIn("bearer_token", site)
        serialized = json.dumps(site, ensure_ascii=False)
        self.assertNotIn("alpha-secret", serialized)
        self.assertEqual(self._read_token(site["id"]), "alpha-secret")

        kept = await self.api.update_site(
            site["id"],
            self._update_payload(site),
        )
        self.assertTrue(kept.body["data"]["has_bearer_token"])
        self.assertEqual(self._read_token(site["id"]), "alpha-secret")

        replaced = await self.api.update_site(
            site["id"],
            self._update_payload(
                kept.body["data"],
                credential_action="set",
                bearer_token="beta-secret",
            ),
        )
        self.assertTrue(replaced.body["data"]["has_bearer_token"])
        self.assertNotIn(
            "beta-secret",
            json.dumps(replaced.body, ensure_ascii=False),
        )
        self.assertEqual(self._read_token(site["id"]), "beta-secret")

        cleared = await self.api.update_site(
            site["id"],
            self._update_payload(
                replaced.body["data"],
                credential_action="clear",
            ),
        )
        self.assertFalse(cleared.body["data"]["has_bearer_token"])
        self.assertIsNone(self._read_token(site["id"]))

        invalid = await self.api.update_site(
            site["id"],
            self._update_payload(
                cleared.body["data"],
                credential_action="set",
                bearer_token="",
            ),
        )
        self.assert_error(invalid, 400, "validation_error")
        self.assertIsNone(self._read_token(site["id"]))

    async def test_storage_unavailable_is_generic(self) -> None:
        await self.service.close()
        result = await self.api.list_sites()
        self.assert_error(result, 503, "storage_unavailable")
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn(str(self.db_path), serialized)
        self.assertNotIn("sites.sqlite3", serialized)

    async def test_unexpected_error_is_generic(self) -> None:
        class BrokenService:
            async def list_sites(self):
                raise RuntimeError("internal path /secret/db.sqlite3")

        result = await SiteApi(BrokenService()).list_sites()
        self.assert_error(result, 500, "internal_error")
        serialized = json.dumps(result.body, ensure_ascii=False)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("db.sqlite3", serialized)


if __name__ == "__main__":
    unittest.main()
