from __future__ import annotations

import unittest

from core.source_url import (
    MAX_SOURCE_URL_LENGTH,
    SOURCE_UNAVAILABLE_TEXT,
    sanitize_source_url,
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


if __name__ == "__main__":
    unittest.main()
