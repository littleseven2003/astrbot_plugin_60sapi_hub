from __future__ import annotations

import io
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.profile_image import (
    FontUnavailableError,
    ImageLimitError,
    ImageRenderCancelled,
    ImageRenderLimits,
    ProfileImageRenderer,
    find_cjk_font,
)


def sample_data(items: int = 3) -> dict:
    return {
        "profile_name": "中文长标题组合",
        "site_name": "本地模拟站点",
        "status": "partial",
        "success_count": 3,
        "failure_count": 1,
        "sections": [
            {
                "label": "每日新闻",
                "status": "ok",
                "fetched_at": "2026-10-26T05:00:00+00:00",
                "content_date": "2026-10-26",
                "displayed_count": items,
                "total_item_count": items,
                "limit": None,
                "warnings": ["内容日期未知提示"],
                "items": [
                    {
                        "title": (
                            "这是一个很长的中文标题，用来验证自动换行不会出现缺字方框、"
                            "裁切、重叠或越界，内容需要持续换行直到结束。"
                        ),
                        "detail": "摘要内容包含中文、English、数字 12345 和标点。" * 5,
                        "hot_value": 0,
                        "link": "https://example.test/a_(b)?x=1&y=2",
                    }
                    for _ in range(items)
                ],
            },
            {
                "label": "知乎热榜",
                "status": "ok",
                "fetched_at": "2026-10-26T05:01:00+00:00",
                "displayed_count": 1,
                "total_item_count": 1,
                "limit": 1,
                "items": [
                    {
                        "title": "知乎问题",
                        "detail": "",
                        "hot_value_desc": "1670 万热度",
                    },
                ],
            },
            {
                "label": "抖音热搜",
                "status": "ok",
                "fetched_at": "2026-10-26T05:02:00+00:00",
                "displayed_count": 0,
                "total_item_count": 0,
                "limit": None,
                "items": [],
            },
            {
                "label": "Bilibili 热搜",
                "status": "error",
                "code": "upstream_http_error",
                "message": "上游站点返回 HTTP 500。",
            },
        ],
    }


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


@unittest.skipUnless(has_font(), "no CJK font available")
class ProfileImageRendererTest(unittest.TestCase):
    def test_renders_valid_png_and_supports_long_pagination(self) -> None:
        limits = ImageRenderLimits(
            page_width=900,
            page_height=1200,
            max_pages=12,
            max_total_pixels=12_000_000,
        )
        renderer = ProfileImageRenderer(limits=limits)
        pages = renderer.render(
            sample_data(items=20),
            created_at="2026-10-26T05:03:00+00:00",
        )
        self.assertGreaterEqual(len(pages), 2)
        self.assertLessEqual(len(pages), 12)
        for page in pages:
            self.assertEqual((page.width, page.height), (900, 1200))
            self.assertTrue(page.png.startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(io.BytesIO(page.png)) as image:
                self.assertEqual(image.size, (900, 1200))
                self.assertEqual(image.mode, "RGB")

    def test_same_snapshot_renders_identical_bytes(self) -> None:
        renderer = ProfileImageRenderer()
        first = renderer.render(sample_data(items=2), created_at="t")
        second = ProfileImageRenderer().render(sample_data(items=2), created_at="t")
        self.assertEqual(
            [page.png for page in first],
            [page.png for page in second],
        )

    def test_content_signals_and_empty_sections_render(self) -> None:
        data = sample_data(items=1)
        data["sections"][2]["items"] = []
        data["sections"][0]["items"][0]["hot_value"] = 0
        pages = ProfileImageRenderer().render(data, created_at="t")
        self.assertGreaterEqual(len(pages), 1)

    def test_page_and_byte_limits_raise(self) -> None:
        many = sample_data(items=80)
        with self.assertRaises(ImageLimitError):
            ProfileImageRenderer(
                limits=ImageRenderLimits(max_pages=1),
            ).render(many)
        with self.assertRaises(ImageLimitError):
            ProfileImageRenderer(
                limits=ImageRenderLimits(max_page_bytes=100),
            ).render(sample_data(items=2))

    def test_long_latin_name_is_split_without_loss(self) -> None:
        renderer = ProfileImageRenderer()
        width = 792
        name = "W" * 80
        lines = renderer._wrap(name, 34, width)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), name)
        font = renderer._font(34)
        self.assertTrue(all(font.getlength(line) <= width for line in lines))
        data = sample_data(items=1)
        data["profile_name"] = name
        pages = renderer.render(data, created_at="t")
        self.assertGreaterEqual(len(pages), 1)
        with Image.open(io.BytesIO(pages[0].png)) as image:
            self.assertEqual(image.size, (900, 1200))

    def test_long_ascii_token_splits_without_loss(self) -> None:
        renderer = ProfileImageRenderer()
        width = 300
        token = "https://example.test/" + ("a" * 500)
        lines = renderer._wrap(token, 17, width)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), token)
        font = renderer._font(17)
        self.assertTrue(all(font.getlength(line) <= width for line in lines))

    def test_non_cjk_font_is_rejected(self) -> None:
        arial = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
        if not arial.is_file():
            self.skipTest("Arial font not available")
        with self.assertRaises(FontUnavailableError):
            ProfileImageRenderer(font_path=arial).render(sample_data(items=1))
        with patch.dict(os.environ, {"ASTRBOT_60SAPI_FONT": str(arial)}):
            selected = find_cjk_font()
        self.assertNotEqual(selected, arial)
        self.assertTrue(selected.is_file())

    def test_no_cjk_font_reports_clear_error(self) -> None:
        import core.profile_image as image_module

        with patch.object(image_module, "_FONT_CANDIDATES", ()):
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(FontUnavailableError):
                    find_cjk_font()

    def test_missing_font_and_cancellation_raise(self) -> None:
        with self.assertRaises(FontUnavailableError):
            ProfileImageRenderer(font_path=Path("/definitely/missing/font.ttf")).render(
                sample_data(items=1),
            )
        event = threading.Event()
        event.set()
        with self.assertRaises(ImageRenderCancelled):
            ProfileImageRenderer().render(sample_data(items=1), cancel_event=event)
    def test_single_long_item_continues_across_pages_without_error(self) -> None:
        data = sample_data(items=1)
        data["sections"][0]["items"][0]["detail"] = "超长单条摘要。" * 1200
        data["sections"][0]["date_is_unknown"] = True
        pages = ProfileImageRenderer(
            limits=ImageRenderLimits(max_pages=12, max_total_pixels=20_000_000),
        ).render(data, created_at="t")
        self.assertGreaterEqual(len(pages), 2)
        self.assertTrue(all(page.png.startswith(b"\x89PNG") for page in pages))
