from __future__ import annotations

import io
import os
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import core.profile_image as image_module
from core.profile_image import (
    FontUnavailableError,
    ImageLimitError,
    ImageRenderCancelled,
    ImageRenderLimits,
    ProfileImageError,
    ProfileImageRenderer,
    SectionImage,
    find_cjk_font,
)

LONG_TITLE = (
    "这是一个很长的中文标题，用来验证自动换行不会出现缺字方框、裁切、重叠或"
    "越界，内容需要持续换行直到结束，并保持可读。"
)
LONG_DETAIL = "摘要内容包含中文、English、数字 12345 和标点。" * 120


def item(title: str = "中文标题", **extra: object) -> dict:
    return {"title": title, **extra}


def section(
    content_type: str = "daily_news",
    label: str = "每日新闻",
    items: list | None = None,
    **extra: object,
) -> dict:
    data: dict = {
        "content_type": content_type,
        "label": label,
        "status": "ok",
        "site_name": "本地模拟站点",
        "fetched_at": "2026-10-26T05:00:00+00:00",
    }
    if items is not None:
        data["items"] = items
    data.update(extra)
    return data


def profile(sections: list, **extra: object) -> dict:
    data = {
        "profile_name": "中文长标题组合",
        "site_name": "本地模拟站点",
        "status": "partial",
        "success_count": 0,
        "failure_count": 0,
        "sections": sections,
    }
    data.update(extra)
    return data


def png_size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as image:
        return image.size

def same_color(a, b, tol: int = 3) -> bool:
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


def is_ink(color) -> bool:
    """把深色文字像素与浅蓝底、白底和浅色分隔线区分开。"""
    return max(color) < 210


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


def text_of(lines: list) -> str:
    parts: list[str] = []
    for line in lines:
        for part in line.parts:
            parts.append(part.text)
    return "".join(parts)


@unittest.skipUnless(has_font(), "no CJK font available")
class ProfileImageRendererTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = ProfileImageRenderer()

    def test_each_content_type_renders_exactly_one_long_image(self) -> None:
        cases = (
            (
                "daily_news",
                "每日新闻",
                section(
                    "daily_news",
                    "每日新闻",
                    [item("新闻一"), item("新闻二", link="https://example.test/a")],
                    content_date="2026-10-26",
                    displayed_count=2,
                    total_item_count=2,
                ),
            ),
            (
                "bilibili_hot",
                "Bilibili 热搜",
                section(
                    "bilibili_hot",
                    "Bilibili 热搜",
                    [item("热搜一"), item("热搜二")],
                ),
            ),
            (
                "zhihu_hot",
                "知乎热榜",
                section(
                    "zhihu_hot",
                    "知乎热榜",
                    [
                        item(
                            "知乎问题",
                            detail="知乎摘要",
                            hot_value_desc="1670 万热度",
                        ),
                    ],
                ),
            ),
            (
                "douyin_hot",
                "抖音热搜",
                section(
                    "douyin_hot",
                    "抖音热搜",
                    [item("抖音热点", hot_value=12345)],
                ),
            ),
            (
                "toutiao_hot",
                "今日头条热榜",
                section(
                    "toutiao_hot",
                    "今日头条热榜",
                    [item("头条热点", hot_value=9876)],
                ),
            ),
        )
        for content_type, label, single in cases:
            with self.subTest(content_type=content_type):
                images = self.renderer.render(profile([single]))
                self.assertEqual(len(images), 1)
                image = images[0]
                self.assertIsInstance(image, SectionImage)
                self.assertEqual(image.index, 1)
                self.assertEqual(image.content_type, content_type)
                self.assertEqual(image.label, label)
                self.assertEqual(image.status, "success")
                self.assertEqual(image.error, None)
                self.assertEqual(image.width, 900)
                self.assertGreater(image.height, 0)
                self.assertLessEqual(image.height, 8000)
                self.assertIsNotNone(image.png)
                assert image.png is not None
                self.assertTrue(image.png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(png_size(image.png), (900, image.height))
                with Image.open(io.BytesIO(image.png)) as decoded:
                    self.assertEqual(decoded.mode, "RGB")

    def test_multi_section_order_and_indexes_follow_profile(self) -> None:
        sections = [
            section("douyin_hot", "抖音热搜", [item("抖音一")]),
            section("daily_news", "每日新闻", [item("新闻一")]),
            section("zhihu_hot", "知乎热榜", [item("知乎一", detail="摘要")]),
        ]
        images = self.renderer.render(profile(sections))
        self.assertEqual(len(images), 3)
        self.assertEqual([image.index for image in images], [1, 2, 3])
        self.assertEqual(
            [image.content_type for image in images],
            ["douyin_hot", "daily_news", "zhihu_hot"],
        )
        self.assertEqual(
            [image.label for image in images],
            ["抖音热搜", "每日新闻", "知乎热榜"],
        )
        self.assertTrue(all(image.status == "success" for image in images))
        self.assertEqual(len({image.png for image in images}), 3)

    def test_empty_section_renders_explicit_empty_state(self) -> None:
        empty = section("douyin_hot", "抖音热搜", [])
        images = self.renderer.render(profile([empty]))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].status, "empty")
        self.assertEqual(images[0].error, None)
        self.assertIsNotNone(images[0].png)
        assert images[0].png is not None
        self.assertEqual(png_size(images[0].png), (900, images[0].height))
        lines = self.renderer._body_lines(image_module._items(empty), self.renderer.limits.width)
        rendered = text_of(lines)
        self.assertIn("没有返回内容", rendered)
        self.assertIn("空栏目", rendered)

        missing_items = section("douyin_hot", "抖音热搜")
        missing = self.renderer.render(profile([missing_items]))
        self.assertEqual(missing[0].status, "empty")

        success = self.renderer.render(
            profile([
                section(
                    "douyin_hot", "抖音热搜",
                    [item(f"抖音热点{index}") for index in range(1, 6)],
                ),
            ]),
        )
        self.assertGreater(success[0].height, images[0].height)

    def test_failed_section_returns_safe_code_without_png(self) -> None:
        failed = {
            "content_type": "bilibili_hot",
            "label": "Bilibili 热搜",
            "status": "error",
            "code": "upstream_timeout",
            "message": "上游请求超时，内部细节不应出现在 error。",
        }
        images = self.renderer.render(profile([failed]))
        self.assertEqual(len(images), 1)
        image = images[0]
        self.assertEqual(image.status, "failed")
        self.assertIsNone(image.png)
        self.assertEqual(image.error, "upstream_timeout")
        self.assertEqual(image.height, 0)
        self.assertNotIn("内部细节", image.error or "")

        unsafe = {
            "content_type": "bilibili_hot",
            "label": "Bilibili 热搜",
            "status": "error",
            "code": "Bad Code With Spaces",
        }
        fallback = self.renderer.render(profile([unsafe]))[0]
        self.assertEqual(fallback.status, "failed")
        self.assertEqual(fallback.error, "section_failed")
        self.assertIsNone(fallback.png)

    def test_daily_news_and_hot_lists_render_their_own_fields(self) -> None:
        daily = section(
            "daily_news",
            "每日新闻",
            [item("新闻一")],
            content_date="2026-10-26",
        )
        meta = self.renderer._meta_text(daily, "daily_news")
        self.assertIn("内容日期：2026 年 10 月 26 日", meta)
        self.assertIn("显示 1 条", meta)

        zhihu = section(
            "zhihu_hot",
            "知乎热榜",
            [
                item(
                    "知乎问题",
                    detail="知乎摘要",
                    hot_value_desc="1670 万热度",
                ),
            ],
        )
        zhihu_text = text_of(
            self.renderer._body_lines(image_module._items(zhihu), self.renderer.limits.width),
        )
        self.assertIn("知乎摘要", zhihu_text)
        self.assertIn("1670 万热度", zhihu_text)
        self.assertNotIn("热度：1670", zhihu_text)

        douyin = section(
            "douyin_hot",
            "抖音热搜",
            [item("抖音热点", hot_value=12345)],
        )
        douyin_text = text_of(
            self.renderer._body_lines(image_module._items(douyin), self.renderer.limits.width),
        )
        self.assertIn("热度：12345", douyin_text)

        bilibili = section("bilibili_hot", "Bilibili 热搜", [item("热搜一")])
        bilibili_text = text_of(
            self.renderer._body_lines(image_module._items(bilibili), self.renderer.limits.width),
        )
        self.assertIn("热搜一", bilibili_text)
        self.assertNotIn("摘要", bilibili_text)
        self.assertNotIn("热度", bilibili_text)

    def test_long_title_wraps_without_loss_or_leading_punctuation(self) -> None:
        lines = self.renderer._wrap(LONG_TITLE, 24, 500, bold=True)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines).replace(" ", ""), LONG_TITLE.replace(" ", ""))
        for line in lines[1:]:
            self.assertNotIn(line[0], image_module._CLOSING_PUNCT)

        short = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item("短标题")])]),
        )[0]
        long = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item(LONG_TITLE)])]),
        )[0]
        self.assertEqual(long.status, "success")
        self.assertGreater(long.height, short.height)
        self.assertIsNotNone(long.png)
        assert long.png is not None
        self.assertEqual(png_size(long.png), (900, long.height))

    def test_long_latin_token_is_split_without_loss(self) -> None:
        width = 300
        token = "httpsexampletest" + "a" * 400
        lines = self.renderer._wrap(token, 17, width)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), token)
        font = self.renderer._font(17)
        for line in lines:
            self.assertLessEqual(font.getlength(line), width)

    def test_long_summary_stays_in_one_image(self) -> None:
        short = self.renderer.render(
            profile([
                section(
                    "zhihu_hot", "知乎热榜",
                    [item("知乎问题", detail="短摘要", hot_value_desc="1 万热度")],
                ),
            ]),
        )[0]
        long = self.renderer.render(
            profile([
                section(
                    "zhihu_hot", "知乎热榜",
                    [item("知乎问题", detail=LONG_DETAIL, hot_value_desc="1 万热度")],
                ),
            ]),
        )[0]
        self.assertEqual(long.status, "success")
        self.assertIsNotNone(long.png)
        self.assertGreater(long.height, short.height)
        self.assertLessEqual(long.height, 8000)

    def test_fifty_items_fit_in_a_single_image(self) -> None:
        items = [
            item(f"第{index}条新闻标题，包含一些用于换行的中文内容")
            for index in range(1, 51)
        ]
        images = self.renderer.render(
            profile([
                section(
                    "daily_news", "每日新闻", items,
                    displayed_count=50, total_item_count=50,
                ),
            ]),
        )
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].status, "success")
        self.assertGreater(images[0].height, 3000)
        self.assertLessEqual(images[0].height, 8000)
        self.assertIsNotNone(images[0].png)

    def test_invalid_or_missing_fields_are_rejected_or_tolerated(self) -> None:
        with self.assertRaises(ProfileImageError):
            self.renderer.render([])
        with self.assertRaises(ProfileImageError):
            self.renderer.render({"sections": "bad"})
        with self.assertRaises(ProfileImageError):
            self.renderer.render({"profile_name": "x"})
        with self.assertRaises(ProfileImageError):
            self.renderer.render({"sections": ["bad"]})
        with self.assertRaises(ProfileImageError):
            self.renderer.render(profile([section()]), limits={"width": 900})

        tolerant = self.renderer.render(
            profile([
                {
                    "status": "ok",
                    "items": [
                        "not-a-dict",
                        {"title": ""},
                        {"title": "有效标题"},
                    ],
                },
            ]),
        )[0]
        self.assertEqual(tolerant.content_type, "")
        self.assertEqual(tolerant.label, "栏目")
        self.assertEqual(tolerant.status, "success")

        not_list = self.renderer.render(
            profile([section("douyin_hot", "抖音热搜", items=[])]),
        )[0]
        self.assertEqual(not_list.status, "empty")
        bad_items = self.renderer.render(
            profile([{**section("douyin_hot", "抖音热搜"), "items": "bad"}]),
        )[0]
        self.assertEqual(bad_items.status, "empty")

    def test_section_over_height_returns_failed_without_png(self) -> None:
        with patch.object(image_module.Image, "new") as new:
            images = self.renderer.render(
                profile([
                    section("daily_news", "每日新闻", [item("新闻一")]),
                    section(
                        "zhihu_hot", "知乎热榜",
                        [item("问题", detail=LONG_DETAIL)],
                    ),
                ]),
                limits=ImageRenderLimits(max_height=200),
            )
            new.assert_not_called()
        self.assertEqual(len(images), 2)
        for image in images:
            self.assertEqual(image.status, "failed")
            self.assertEqual(image.error, "section_too_tall")
            self.assertIsNone(image.png)
            self.assertEqual(image.height, 0)

    def test_section_over_bytes_returns_failed_without_png(self) -> None:
        image = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item("新闻一")])]),
            limits=ImageRenderLimits(max_image_bytes=64),
        )[0]
        self.assertEqual(image.status, "failed")
        self.assertEqual(image.error, "section_too_large")
        self.assertIsNone(image.png)

    def test_total_bytes_limit_marks_remaining_sections_failed(self) -> None:
        content = section("daily_news", "每日新闻", [item("新闻一")])
        measured = self.renderer.render(profile([content]))[0]
        assert measured.png is not None
        exact = len(measured.png)
        images = self.renderer.render(
            profile([content, content]),
            limits=ImageRenderLimits(max_total_bytes=exact),
        )
        self.assertEqual(images[0].status, "success")
        self.assertIsNotNone(images[0].png)
        self.assertEqual(images[1].status, "failed")
        self.assertEqual(images[1].error, "total_bytes_exceeded")
        self.assertIsNone(images[1].png)

    def test_header_band_keeps_clear_gap_above_first_item(self) -> None:
        """标题区浅蓝底的平直下边缘必须与正文首行留出间距，不能压字。"""
        image = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item("第一条新闻")])]),
        )[0]
        assert image.png is not None
        with Image.open(io.BytesIO(image.png)).convert("RGB") as opened:
            width, height = opened.size
            pixels = opened.load()
            band_bottom = 0
            for y in range(height // 2):
                if same_color(pixels[width // 2, y], image_module._HEADER_BG):
                    band_bottom = y
            ink_top = None
            for y in range(band_bottom + 1, height):
                if any(
                    is_ink(pixels[x, y])
                    for x in range(60, width - 60, 3)
                ):
                    ink_top = y
                    break
        self.assertGreater(band_bottom, 0)
        self.assertIsNotNone(ink_top)
        gap = ink_top - band_bottom
        self.assertGreaterEqual(gap, 20)
        self.assertLessEqual(gap, 40)

    def test_mixed_statuses_are_distinguishable_in_one_render(self) -> None:
        """成功 / 空 / 失败三种栏目在同一次渲染里必须可区分且互不影响。"""
        images = self.renderer.render(
            profile([
                section("daily_news", "每日新闻", [item("正常新闻")]),
                section("douyin_hot", "抖音热搜", items=[]),
                {
                    "content_type": "toutiao_hot", "label": "今日头条热榜",
                    "status": "error", "code": "upstream_timeout",
                    "items": [],
                },
            ]),
        )
        self.assertEqual([i.index for i in images], [1, 2, 3])
        self.assertEqual(
            [i.status for i in images], ["success", "empty", "failed"],
        )
        self.assertIsNotNone(images[0].png)
        self.assertIsNotNone(images[1].png)
        self.assertIsNone(images[2].png)
        self.assertEqual(images[2].error, "upstream_timeout")
        self.assertEqual(images[2].height, 0)
        self.assertEqual(len(images), 3)

    def test_total_bytes_exhaustion_keeps_all_sections_in_result(self) -> None:
        """合计字节耗尽时后续栏目逐项标记失败，不裁切、不丢项、不拆图。"""
        content = section("daily_news", "每日新闻", [item("新闻一")])
        measured = self.renderer.render(profile([content]))[0]
        assert measured.png is not None
        exact = len(measured.png)
        with patch.object(image_module.Image, "new", wraps=image_module.Image.new) as new:
            images = self.renderer.render(
                profile([content, content, content]),
                limits=ImageRenderLimits(max_total_bytes=exact),
            )
        self.assertEqual(len(images), 3)
        self.assertEqual(
            [i.status for i in images],
            ["success", "failed", "failed"],
        )
        self.assertEqual(images[1].error, "total_bytes_exceeded")
        self.assertEqual(images[2].error, "total_bytes_exceeded")
        # 超出的那个栏目为了量出字节需要先渲染一次，之后的栏目直接短路不分配。
        self.assertEqual(new.call_count, 2)

    def test_too_many_sections_raise_limit_error(self) -> None:
        sections = [
            section("daily_news", f"栏目{index}", [item("新闻")])
            for index in range(1, 7)
        ]
        with self.assertRaises(ImageLimitError):
            self.renderer.render(profile(sections))

    def test_links_and_urls_are_not_rendered(self) -> None:
        target = section(
            "zhihu_hot",
            "知乎热榜",
            [
                item(
                    "标题",
                    detail="详见 https://example.test/a?x=1 内容",
                    hot_value_desc="1 万热度",
                    link="https://example.com/b",
                ),
            ],
        )
        label = image_module._section_label(target)
        content_type = image_module._clean(target.get("content_type"))
        rendered = ""
        rendered += text_of(
            self.renderer._header_lines(target, label, content_type, self.renderer.limits.width),
        )
        rendered += text_of(
            self.renderer._body_lines(image_module._items(target), self.renderer.limits.width),
        )
        rendered += text_of(
            self.renderer._footer_lines(target, "本地模拟站点", "", self.renderer.limits.width),
        )
        self.assertNotIn("http", rendered)
        self.assertNotIn("链接", rendered)
        self.assertIn("详见", rendered)
        self.assertIn("内容", rendered)

    def test_cancel_before_measure_allocates_no_image(self) -> None:
        event = threading.Event()
        event.set()
        with patch.object(image_module.Image, "new") as new:
            with self.assertRaises(ImageRenderCancelled):
                self.renderer.render(
                    profile([section("daily_news", "每日新闻", [item("新闻")])]),
                    cancel_event=event,
                )
            new.assert_not_called()

    def test_cancel_during_measurement_allocates_no_image(self) -> None:
        class CancelAfter:
            def __init__(self, after: int) -> None:
                self.calls = 0
                self.after = after

            def is_set(self) -> bool:
                self.calls += 1
                return self.calls > self.after

        with patch.object(image_module.Image, "new") as new:
            with self.assertRaises(ImageRenderCancelled):
                self.renderer.render(
                    profile([section("daily_news", "每日新闻", [item(LONG_TITLE)])]),
                    cancel_event=CancelAfter(4),
                )
            new.assert_not_called()

    def test_missing_font_and_non_cjk_font_are_rejected(self) -> None:
        with self.assertRaises(FontUnavailableError):
            ProfileImageRenderer(
                font_path=Path("/definitely/missing/font.ttf"),
            ).render(profile([section("daily_news", "每日新闻", [item("新闻")])]))

        arial = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
        if arial.is_file():
            with self.assertRaises(FontUnavailableError):
                ProfileImageRenderer(font_path=arial).render(
                    profile([section("daily_news", "每日新闻", [item("新闻")])]),
                )
            with patch.dict(os.environ, {"ASTRBOT_60SAPI_FONT": str(arial)}):
                selected = find_cjk_font()
            self.assertNotEqual(selected, arial)
            self.assertTrue(selected.is_file())

    def test_same_input_renders_identical_png_and_input_is_not_mutated(
        self,
    ) -> None:
        data = profile(
            [
                section(
                    "daily_news", "每日新闻",
                    [item("新闻一", link="https://example.test/a")],
                    content_date="2026-10-26",
                ),
                section("douyin_hot", "抖音热搜", [item("热点", hot_value=1)]),
            ],
        )
        snapshot = deepcopy(data)
        first = self.renderer.render(data, created_at="2026-10-26T05:03:00+00:00")
        second = ProfileImageRenderer().render(
            data, created_at="2026-10-26T05:03:00+00:00",
        )
        self.assertEqual(
            [image.png for image in first],
            [image.png for image in second],
        )
        self.assertEqual(data, snapshot)

    def test_renderer_contract_replaces_pagination(self) -> None:
        import dataclasses

        self.assertFalse(hasattr(image_module, "ImagePage"))
        fields = [field.name for field in dataclasses.fields(SectionImage)]
        self.assertEqual(
            fields,
            [
                "index", "content_type", "label", "status",
                "width", "height", "png", "error",
            ],
        )

    def test_fetched_time_is_localized_and_readable(self) -> None:
        self.assertEqual(
            image_module._format_fetched_time("2026-10-26T05:00:00+00:00"),
            "2026-10-26 13:00",
        )
        self.assertEqual(
            image_module._format_fetched_time("2026-10-26T05:00:00"),
            "2026-10-26 05:00",
        )
        self.assertEqual(
            image_module._format_fetched_time("2026-10-26T05:00:00Z"),
            "2026-10-26 13:00",
        )
        self.assertEqual(
            image_module._format_fetched_time("昨天 下午"),
            "昨天 下午",
        )
        raw = image_module._format_fetched_time("not-a-time-Tvalue")
        self.assertNotIn("T", raw)
        self.assertIn("not-a-time", raw)

        section_data = section("daily_news", "每日新闻", [item("新闻一")])
        footer = text_of(
            self.renderer._footer_lines(
                section_data, "本地模拟站点", "", self.renderer.limits.width,
            ),
        )
        self.assertIn("获取时间：2026-10-26 13:00", footer)
        self.assertIn("数据来源：本地模拟站点", footer)
        self.assertNotIn("T05:00:00", footer)

    def test_content_date_is_chinese_and_weekday_is_optional(self) -> None:
        self.assertEqual(
            image_module._format_content_date("2026-10-26", {}),
            "2026 年 10 月 26 日",
        )
        self.assertEqual(
            image_module._format_content_date(
                "2026/10/26", {"weekday": "星期一"},
            ),
            "2026 年 10 月 26 日（星期一）",
        )
        self.assertEqual(
            image_module._format_content_date(
                "2026 年 10 月 26 日", {"weekday": 3},
            ),
            "2026 年 10 月 26 日（星期三）",
        )
        self.assertEqual(
            image_module._format_content_date("2026-10-26 周三", {}),
            "2026 年 10 月 26 日（周三）",
        )
        self.assertEqual(
            image_module._format_content_date("未知日期", {}),
            "未知日期",
        )
        self.assertEqual(
            image_module._format_content_date("2026-13-40", {}),
            "2026-13-40",
        )
        missing = self.renderer._meta_text(section("daily_news", "每日新闻", []), "daily_news")
        self.assertNotIn("内容日期", missing)

    def test_footer_uses_two_columns_without_overlap(self) -> None:
        target = section(
            "daily_news",
            "每日新闻",
            [item("新闻一")],
            site_name="一个非常长的站点名称用于验证右栏换行不会与左栏文字重叠或越界",
        )
        width = self.renderer.limits.width
        content_width = self.renderer._content_width(width)
        lines = self.renderer._footer_lines(
            target, "本地模拟站点", "", width,
        )
        self.assertGreaterEqual(len(lines), 4)
        both = [
            line for line in lines
            if len(line.parts) == 2
        ]
        self.assertGreaterEqual(len(both), 2)
        for line in both:
            left, right = line.parts
            self.assertEqual(left.x, 0)
            self.assertIsNotNone(right.right)
            self.assertEqual(right.right, content_width)
        right_parts = [
            part
            for line in lines
            for part in line.parts
            if part.right is not None
        ]
        self.assertGreaterEqual(len(right_parts), 3)
        font = self.renderer._font(15)
        for part in right_parts:
            text_width = font.getlength(part.text)
            start = content_width - text_width
            self.assertGreaterEqual(start, 0)
            self.assertLessEqual(start + text_width, content_width + 0.5)
        left_parts = [
            part
            for line in lines
            for part in line.parts
            if part.right is None
        ]
        self.assertTrue(any(part.text == "60sAPI Hub" for part in left_parts))
        self.assertTrue(
            any(part.text == "由插件根据接口数据生成" for part in left_parts),
        )
        for part in left_parts:
            self.assertLessEqual(
                font.getlength(part.text), content_width // 2,
            )

    def test_hot_value_text_uses_secondary_color(self) -> None:
        data = section(
            "douyin_hot", "抖音热搜",
            [item("抖音热点", hot_value=12345)],
        )
        lines = self.renderer._body_lines(
            image_module._items(data), self.renderer.limits.width,
        )
        hot_parts = [
            part
            for line in lines
            for part in line.parts
            if "热度" in part.text
        ]
        self.assertEqual(len(hot_parts), 1)
        self.assertEqual(hot_parts[0].color, image_module._MUTED)
        index_parts = [
            part
            for line in lines
            for part in line.parts
            if part.text == "01"
        ]
        self.assertEqual(len(index_parts), 1)
        self.assertEqual(index_parts[0].color, image_module._ACCENT)
        title_parts = [
            part
            for line in lines
            for part in line.parts
            if part.text == "抖音热点"
        ]
        self.assertEqual(len(title_parts), 1)
        self.assertEqual(title_parts[0].color, image_module._INK)

    def test_default_limits_match_frozen_contract(self) -> None:
        limits = ImageRenderLimits()
        self.assertEqual(limits.width, 900)
        self.assertEqual(limits.max_height, 8000)
        self.assertEqual(limits.max_image_bytes, 2 * 1024 * 1024)
        self.assertEqual(limits.max_total_bytes, 8 * 1024 * 1024)
        self.assertEqual(limits.max_sections, 5)
        self.assertEqual(self.renderer.limits, limits)


if __name__ == "__main__":
    unittest.main()
