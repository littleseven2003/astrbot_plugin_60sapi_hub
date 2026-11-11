from __future__ import annotations

import unittest

from core.source_url import (
    MAX_SOURCE_URL_LENGTH,
    SOURCE_SELF_BUILT_TEXT,
    SOURCE_UNAVAILABLE_TEXT,
    sanitize_source_url,
    source_display_text,
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
        self.assertEqual(source_display_text(url, "public"), url)
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


if __name__ == "__main__":
    unittest.main()
