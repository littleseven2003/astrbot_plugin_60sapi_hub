from __future__ import annotations

import unittest

from core.sites import MAX_BASE_URL_LENGTH
from core.source_url import (
    MAX_SNAPSHOT_URL_LENGTH,
    MAX_SOURCE_URL_LENGTH,
    SOURCE_SELF_BUILT_TEXT,
    SOURCE_UNAVAILABLE_TEXT,
    sanitize_source_url,
    snapshot_source_url,
    source_display_text,
    source_domain_text,
    source_url_text,
)


class SanitizeSourceUrlTest(unittest.TestCase):
    def test_keeps_scheme_host_port_and_path_prefix(self) -> None:
        self.assertEqual(
            sanitize_source_url("http://example.test/proxy/v2/60s"),
            "http://example.test/proxy/v2/60s",
        )
        self.assertEqual(
            sanitize_source_url("https://Example.TEST:8443/a/b/v2/zhihu"),
            "https://example.test:8443/a/b/v2/zhihu",
        )
        self.assertEqual(
            sanitize_source_url("http://[::1]:8080/proxy/v2/bili"),
            "http://[::1]:8080/proxy/v2/bili",
        )

    def test_strips_credentials_query_and_fragment(self) -> None:
        self.assertEqual(
            sanitize_source_url(
                "https://user:pass@example.test/proxy/v2/60s"
                "?token=secret&x=1#frag",
            ),
            "https://example.test/proxy/v2/60s",
        )
        self.assertEqual(
            sanitize_source_url("https://example.test/p/v2/60s?"),
            "https://example.test/p/v2/60s",
        )

    def test_rejects_values_that_cannot_be_shown_safely(self) -> None:
        for value in (
            None,
            1,
            "",
            "   ",
            "ftp://example.test/v2/60s",
            "javascript:alert(1)",
            "//example.test/v2/60s",
            "http:///v2/60s",
            "http://example.test/a b/v2/60s",
            "http://example.test/a<b>",
            "http://example.test/a`b",
            "http://example.test/v2/60s\ninjected",
            "http://example.test/" + "a" * MAX_SOURCE_URL_LENGTH,
        ):
            with self.subTest(value=value):
                self.assertIsNone(sanitize_source_url(value))

    def test_display_text_marks_missing_source(self) -> None:
        self.assertEqual(source_url_text(None), SOURCE_UNAVAILABLE_TEXT)
        self.assertEqual(
            source_url_text("http://example.test/p/v2/bili"),
            "http://example.test/p/v2/bili",
        )


class SourceDisplayTest(unittest.TestCase):
    def test_public_shows_url_and_self_built_shows_label(self) -> None:
        url = "https://api.example.test/proxy/v2/60s"
        self.assertEqual(
            source_display_text(url, "public"), "api.example.test",
        )
        self.assertEqual(
            source_display_text(url, "self_built"),
            SOURCE_SELF_BUILT_TEXT,
        )
        # 自建站点即使没有地址也只写标签，不写“来源不可用”之外的猜测。
        self.assertEqual(
            source_display_text(None, "self_built"),
            SOURCE_SELF_BUILT_TEXT,
        )
        self.assertEqual(
            source_display_text(None, "public"),
            SOURCE_UNAVAILABLE_TEXT,
        )

    def test_unknown_kind_never_leaks_address(self) -> None:
        # 旧快照没有类型字段：有地址时按自建隐藏，没有地址时写不可用。
        self.assertEqual(
            source_display_text("http://10.0.0.5/proxy/v2/60s", None),
            SOURCE_SELF_BUILT_TEXT,
        )
        self.assertEqual(
            source_display_text(None, None),
            SOURCE_UNAVAILABLE_TEXT,
        )
        self.assertEqual(
            source_display_text("https://api.example.test/v2/60s", "unknown"),
            SOURCE_SELF_BUILT_TEXT,
        )


class SourceDomainTest(unittest.TestCase):
    """公开来源对外只展示域名。"""

    def test_public_shows_domain_only(self) -> None:
        for url in (
            "https://api.example.com/secret-path/v2/60s",
            "http://example.test/proxy/v2/zhihu",
            "https://Example.TEST:8443/a/b/v2/bili",
            "https://user:pass@example.test/proxy/v2/60s?token=secret#frag",
            "http://example.test/proxy/v2/60s?",
        ):
            with self.subTest(url=url):
                display = source_display_text(url, "public")
                self.assertEqual(
                    display, source_domain_text(url),
                )
                # 域名之外的东西一律不出现。
                for leak in ("secret", "token", "proxy", "v2", "user", ":8443"):
                    self.assertNotIn(leak, display)
                self.assertNotIn("/", display)

    def test_public_domain_keeps_ipv6_brackets_and_drops_port(self) -> None:
        self.assertEqual(
            source_domain_text("http://[::1]:8080/proxy/v2/bili"), "[::1]",
        )
        self.assertEqual(
            source_display_text("http://[2001:db8::1]/p/v2/60s", "public"),
            "[2001:db8::1]",
        )
        # 端口不是域名的一部分，对外不展示。
        self.assertEqual(
            source_display_text("http://10.0.0.5:8080/proxy", "public"),
            "10.0.0.5",
        )

    def test_secret_in_path_never_reaches_display(self) -> None:
        secret = "simulated-secret-abc123"
        url = f"https://api.example.com/{secret}/v2/60s"
        for kind in ("public", "self_built", None, "unknown"):
            with self.subTest(kind=kind):
                self.assertNotIn(
                    secret, source_display_text(url, kind),
                )
        self.assertEqual(
            source_display_text(url, "public"), "api.example.com",
        )

    def test_long_base_url_keeps_port_path_and_endpoint(self) -> None:
        # 合法基础地址接近上限，拼接 endpoint 后超过 MAX_SOURCE_URL_LENGTH。
        prefix = "https://api.example.com:8443/reverse-proxy/"
        base = prefix + "p" * (MAX_BASE_URL_LENGTH - len(prefix))
        self.assertEqual(len(base), MAX_BASE_URL_LENGTH)
        url = base + "/v2/zhihu"
        self.assertGreater(len(url), MAX_SOURCE_URL_LENGTH)
        self.assertIsNone(sanitize_source_url(url))
        # 快照必须完整表达本次请求：端口、反代前缀和 endpoint 都不能丢。
        snapshot = snapshot_source_url(url)
        self.assertEqual(snapshot, url)
        self.assertIn(":8443", snapshot)
        self.assertIn("/reverse-proxy/", snapshot)
        self.assertTrue(snapshot.endswith("/v2/zhihu"))
        self.assertGreater(len(snapshot), MAX_SOURCE_URL_LENGTH)
        self.assertLessEqual(len(snapshot), MAX_SNAPSHOT_URL_LENGTH)
        # 对外展示仍然只有域名。
        self.assertEqual(source_display_text(url, "public"), "api.example.com")

    def test_special_path_characters_do_not_hide_domain(self) -> None:
        for url in (
            "https://a.test/x%20y/z;v=1",
            "https://a.test/a~b/c_d/e-f/g.h",
            "https://a.test/" + "%" * 64,
        ):
            with self.subTest(url=url):
                self.assertEqual(source_display_text(url, "public"), "a.test")

    def test_snapshot_keeps_full_url_when_it_fits(self) -> None:
        url = "http://example.test/proxy/v2/60s"
        self.assertEqual(snapshot_source_url(url), url)
        self.assertIsNone(snapshot_source_url(None))
        self.assertIsNone(snapshot_source_url("ftp://example.test/v2/60s"))
        self.assertIsNone(snapshot_source_url("javascript:alert(1)"))

    def test_old_snapshot_without_source_stays_explicit(self) -> None:
        # 旧快照没有来源字段：不拿站点名称或 endpoint 补造。
        self.assertEqual(source_display_text(None, "public"), SOURCE_UNAVAILABLE_TEXT)
        self.assertEqual(source_display_text(None, None), SOURCE_UNAVAILABLE_TEXT)
        self.assertEqual(source_display_text("", "public"), SOURCE_UNAVAILABLE_TEXT)


if __name__ == "__main__":
    unittest.main()
