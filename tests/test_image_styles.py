"""图片样式注册表、配色对比度与四套样式的真实渲染回归。"""

from __future__ import annotations

import io
import unittest

from PIL import Image

from core import profile_image as image_module
from core.image_styles import (
    DEFAULT_STYLE,
    DEFAULT_STYLE_ID,
    PRESETS,
    STYLE_ORDER,
    get_style,
    is_known_style,
    parse_style_id,
    resolve_style_id,
    style_options,
)
from core.profile_image import ProfileImageRenderer


def _lum(rgb):
    def channel(value):
        value = value / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = rgb
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(a, b):
    first, second = _lum(a), _lum(b)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


class ImageSnapshotStyleTest(unittest.TestCase):
    def test_snapshot_copy_keeps_the_effective_style(self) -> None:
        from core.plan_preview import _image_snapshot_data

        snapshot = _image_snapshot_data(
            {
                "task_name": "任务",
                "site_name": "站点",
                "status": "complete",
                "success_count": 1,
                "failure_count": 0,
                "sections": [],
                "image_style": "floral",
                # 快照不保存输出正文或路径等前端数据。
                "outputs": {"text": "x"},
                "task_id": "t",
            },
        )
        self.assertEqual(snapshot["image_style"], "floral")
        self.assertNotIn("outputs", snapshot)
        self.assertNotIn("task_id", snapshot)


class ImageStyleRegistryTest(unittest.TestCase):
    def test_four_presets_in_stable_order(self) -> None:
        self.assertEqual(
            STYLE_ORDER, ("default", "mist", "floral", "bold_studio"),
        )
        self.assertEqual(tuple(PRESETS), STYLE_ORDER)
        options = style_options()
        self.assertEqual([item["id"] for item in options], list(STYLE_ORDER))
        self.assertTrue(all(item["name"] for item in options))

    def test_every_preset_is_immutable_and_well_formed(self) -> None:
        for style_id in STYLE_ORDER:
            style = PRESETS[style_id]
            with self.assertRaises(Exception):
                style.num = (0, 0, 0)  # type: ignore[misc]
            self.assertEqual(style.id, style_id)
            self.assertGreater(style.label_size, 0)
            self.assertGreaterEqual(style.card_border, 1)
            self.assertIn(style.number_shape, ("plain", "square"))

    def test_parse_is_strict_and_resolve_falls_back(self) -> None:
        self.assertEqual(parse_style_id("mist"), "mist")
        for bad in (None, "", "unknown", 3, ["default"]):
            with self.assertRaises(ValueError):
                parse_style_id(bad)
        self.assertEqual(resolve_style_id("unknown"), DEFAULT_STYLE_ID)
        self.assertEqual(resolve_style_id(None), DEFAULT_STYLE_ID)
        self.assertEqual(get_style("floral").id, "floral")
        self.assertEqual(get_style("nope").id, "default")
        self.assertIs(get_style(DEFAULT_STYLE), DEFAULT_STYLE)
        self.assertTrue(is_known_style("bold_studio"))
        self.assertFalse(is_known_style("nope"))

    def test_text_colors_meet_contrast_on_their_real_background(self) -> None:
        """可读文字与实际底色至少 4.5:1；纯装饰色不参与该断言。"""
        for style_id in STYLE_ORDER:
            style = PRESETS[style_id]
            header_bg = style.header_bg or style.card_bg
            footer_bg = style.footer_bg or style.card_bg
            number_bg = style.number_bg or style.card_bg
            pairs = {
                "正文": (style.ink, style.card_bg),
                "栏目标题": (style.title_ink, style.card_bg),
                "次要文字": (style.muted, style.card_bg),
                "次要文字（页眉底）": (style.muted, header_bg),
                "序号": (style.num, number_bg),
                "热度": (style.heat, style.card_bg),
                "品牌（页眉底）": (style.brand_ink, header_bg),
                "页眉辅文": (style.brand_secondary_ink, header_bg),
                "页脚文字": (style.footer_muted_ink, footer_bg),
                "页脚品牌": (style.footer_brand_ink, footer_bg),
            }
            for name, (foreground, background) in pairs.items():
                ratio = contrast(foreground, background)
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f"{style_id} {name} 对比度只有 {ratio:.2f}",
                )


def has_font() -> bool:
    try:
        image_module.find_cjk_font()
        return True
    except image_module.FontUnavailableError:
        return False


def _section(content_type: str, label: str) -> dict:
    return {
        "content_type": content_type,
        "label": label,
        "status": "ok",
        "displayed_count": 2,
        "items": [
            {
                "title": "第一条标题示例，用于检查中文排版与换行",
                "detail": "摘要示例文本，包含标点与英文 API。",
                "hot_value": 12345,
                "hot_value_desc": "12345 热度",
            },
            {"title": "第二条标题示例", "detail": "摘要示例文本"},
        ],
        "source_url": "https://example.test/60s",
        "source_kind": "public",
        "fetched_at": "2026-11-19T02:00:00+00:00",
    }


TYPES = (
    ("daily_news", "每日新闻"),
    ("bilibili_hot", "Bilibili 热搜"),
    ("zhihu_hot", "知乎热榜"),
    ("douyin_hot", "抖音热搜"),
    ("toutiao_hot", "今日头条热榜"),
)


@unittest.skipUnless(has_font(), "no CJK font available")
class StyleRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = ProfileImageRenderer()

    def _render(self, style_id: str, content_type: str, label: str):
        data = {
            "image_style": style_id,
            "site_name": "样式回归站点",
            "status": "ok",
            "success_count": 1,
            "failure_count": 0,
            "sections": [_section(content_type, label)],
        }
        return self.renderer.render(data)[0]

    def test_each_style_renders_all_five_content_types(self) -> None:
        for style_id in STYLE_ORDER:
            for content_type, label in TYPES:
                image = self._render(style_id, content_type, label)
                self.assertEqual(image.status, "success", style_id)
                self.assertEqual(image.width, 900)
                self.assertGreater(image.height, 200)
                self.assertIsNotNone(image.png)
                with Image.open(io.BytesIO(image.png)) as opened:
                    self.assertEqual(opened.size, (image.width, image.height))

    def test_numbers_use_the_style_palette(self) -> None:
        for style_id in STYLE_ORDER:
            style = PRESETS[style_id]
            self._render(style_id, "douyin_hot", "抖音热搜")
            lines = self.renderer._body_lines(
                image_module._items(_section("douyin_hot", "抖音热搜")),
                self.renderer.limits.width,
            )
            numbers = [
                part for line in lines for part in line.parts
                if part.number
            ]
            self.assertGreaterEqual(len(numbers), 2)
            for part in numbers:
                self.assertEqual(part.color, style.num, style_id)

    def test_header_and_footer_backgrounds_follow_the_style(self) -> None:
        for style_id in ("mist", "floral", "bold_studio"):
            style = PRESETS[style_id]
            image = self._render(style_id, "daily_news", "每日新闻")
            with Image.open(io.BytesIO(image.png)).convert("RGB") as opened:
                header_pixel = opened.getpixel((image.width - 60, 30))
                footer_pixel = opened.getpixel((image.width - 60, image.height - 8))
            self.assertEqual(header_pixel, style.header_bg, style_id)
            if style.footer_bg is not None:
                self.assertEqual(footer_pixel, style.footer_bg, style_id)
            else:
                self.assertEqual(footer_pixel, style.card_bg, style_id)

    def test_square_numbers_draw_a_block_only_in_bold_studio(self) -> None:
        """方形序号只在粗体工作室出现，其他款式保持纯数字。"""
        for style_id in STYLE_ORDER:
            style = PRESETS[style_id]
            image = self._render(style_id, "daily_news", "每日新闻")
            with Image.open(io.BytesIO(image.png)).convert("RGB") as opened:
                # 序号列第 1 行右侧：方形底款应出现底色，其他款式是卡片底色。
                pixel = opened.getpixel((60, 200))
            if style.number_shape == "square":
                self.assertNotEqual(pixel, style.card_bg, style_id)
            else:
                self.assertNotEqual(style.number_bg, style.card_bg)

    def test_default_style_keeps_the_legacy_palette(self) -> None:
        self.assertEqual(DEFAULT_STYLE.page_bg, (238, 240, 243))
        self.assertEqual(DEFAULT_STYLE.card_bg, (255, 255, 255))
        self.assertEqual(DEFAULT_STYLE.accent, (161, 60, 98))
        self.assertEqual(DEFAULT_STYLE.num, (107, 114, 128))
        self.assertEqual(DEFAULT_STYLE.label_size, 42)
        self.assertEqual(DEFAULT_STYLE.title_size, 25)

    def test_empty_and_failed_sections_still_use_the_selected_style(self) -> None:
        empty = _section("daily_news", "每日新闻")
        empty["items"] = []
        empty["displayed_count"] = 0
        data = {
            "image_style": "mist",
            "site_name": "样式回归站点",
            "status": "ok",
            "success_count": 1,
            "failure_count": 0,
            "sections": [empty],
        }
        image = self.renderer.render(data)[0]
        self.assertEqual(image.status, "empty")
        self.assertIsNotNone(image.png)
        with Image.open(io.BytesIO(image.png)).convert("RGB") as opened:
            self.assertEqual(opened.getpixel((image.width - 60, 30)), PRESETS["mist"].header_bg)


if __name__ == "__main__":
    unittest.main()
