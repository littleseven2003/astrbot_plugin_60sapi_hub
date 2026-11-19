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
        # 页脚来源按 source_url + source_kind 计算：公开实例只显示域名。
        "source_url": "https://news.example.test/proxy?token=secret",
        "source_kind": "public",
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
    """把深色文字像素与白底、浅色分隔线和浅灰页面底色区分开。"""
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
            displayed_count=1,
            total_item_count=1,
        )
        self.assertIn(
            "内容日期 2026 年 10 月 26 日",
            image_module._content_date_text(daily, "daily_news"),
        )
        self.assertEqual(image_module._count_text(daily), "1 条")
        header_text = text_of(
            self.renderer._header_lines(
                daily, "每日新闻", "daily_news", self.renderer.limits.width,
            ),
        )
        self.assertIn("每日新闻", header_text)
        self.assertIn("1 条", header_text)
        self.assertIn("内容日期 2026 年 10 月 26 日", header_text)

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

    def test_widow_adjust_keeps_closing_punct_at_line_end(self) -> None:
        # 修复前：末行孤字调整会把上一行末尾的「。」搬到末行行首，渲染成「。末」。
        text = "字" * 11 + "。末"
        lines = self.renderer._wrap(text, image_module._TITLE_SIZE, 300)
        self.assertEqual(lines, ["字" * 11 + "。", "末"])
        self.assertEqual("".join(lines), text)
        for line in lines[1:]:
            self.assertNotIn(line[0], image_module._CLOSING_PUNCT)

    def test_widow_adjust_does_not_split_ascii_word(self) -> None:
        # 修复前：末行只剩「末」时把 "Preview" 的末字母带下去，得到 "Previe" + "w末"。
        text = "字" * 4 + "Preview末"
        lines = self.renderer._wrap(text, image_module._TITLE_SIZE, 200)
        self.assertEqual(lines, ["字" * 4, "Preview末"])
        self.assertEqual("".join(lines), text)
        self.assertIn("Preview", lines[-1])

    def test_closing_punct_stays_out_of_inline_hot_column(self) -> None:
        # 修复前：首行写满 25 字（625px）后「。」被并回首行，右边缘 650px 越过
        # 并排热度列前可用的 630px，压到右侧热度文字上。
        text = "字" * 25 + "。"
        width = 770
        first_width = 630
        lines = self.renderer._wrap(
            text,
            image_module._TITLE_SIZE,
            width,
            first_width=first_width,
        )
        font = self.renderer._font(image_module._TITLE_SIZE)
        self.assertLessEqual(font.getlength(lines[0]), first_width)
        self.assertEqual("".join(lines), text)
        for line in lines[1:]:
            self.assertNotIn(line[0], image_module._CLOSING_PUNCT)

    def test_lone_closing_punct_last_line_merges_into_previous_line(self) -> None:
        font = self.renderer._font(image_module._TITLE_SIZE)

        def fits(_index: int) -> int:
            return 770

        lines = ["标题内容说明文字", "。"]
        self.renderer._tidy_tail(lines, font.getlength, fits)
        self.assertEqual(lines, ["标题内容说明文字。"])

        # 上一行放不下时把行尾的字带下来，标点仍留在行尾、不落到下一行行首。
        def narrow(_index: int) -> int:
            return 630

        lines = ["字" * 25, "。"]
        self.renderer._tidy_tail(lines, font.getlength, narrow)
        self.assertEqual(lines, ["字" * 24, "字。"])

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

    def test_header_rule_keeps_clear_gap_above_first_item(self) -> None:
        """标题分隔线必须与正文首行留出间距，不能压字或贴线。"""
        image = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item("第一条新闻")])]),
        )[0]
        assert image.png is not None
        with Image.open(io.BytesIO(image.png)).convert("RGB") as opened:
            width, height = opened.size
            pixels = opened.load()
            rule_y = None
            for y in range(1, height // 2):
                if (
                    same_color(pixels[width // 2, y], image_module._BORDER)
                    and same_color(pixels[60, y], image_module._BORDER)
                ):
                    rule_y = y
            ink_top = None
            for y in range((rule_y or 0) + 1, height):
                if any(
                    is_ink(pixels[x, y])
                    for x in range(60, width - 60, 3)
                ):
                    ink_top = y
                    break
        self.assertIsNotNone(rule_y)
        self.assertIsNotNone(ink_top)
        gap = ink_top - (rule_y or 0)
        self.assertGreaterEqual(gap, 14)
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
                "width", "height", "png", "error", "note",
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
        self.assertIn("数据来源：news.example.test", footer)
        self.assertNotIn("T05:00:00", footer)
        # 公开实例绝不能泄露路径、查询串或凭据。
        self.assertNotIn("proxy", footer)
        self.assertNotIn("token", footer)

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
        missing = image_module._content_date_text(
            section("daily_news", "每日新闻", []), "daily_news",
        )
        self.assertEqual(missing, "")
        unknown = image_module._content_date_text(
            section("daily_news", "每日新闻", [], date_is_unknown=True),
            "daily_news",
        )
        self.assertEqual(unknown, "内容日期 未知")
        hot_without_date = image_module._content_date_text(
            section("douyin_hot", "抖音热搜", []), "douyin_hot",
        )
        self.assertEqual(hot_without_date, "")

    def test_footer_uses_two_columns_without_overlap(self) -> None:
        target = section(
            "daily_news",
            "每日新闻",
            [item("新闻一")],
            site_name="一个非常长的站点名称用于验证右栏换行不会与左栏文字重叠或越界",
            source_url=(
                "https://a-very-long-subdomain-for-footer-wrapping."
                "example.test/proxy"
            ),
            source_kind="public",
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
        font = self.renderer._font(image_module._FOOTER_SIZE)
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
        self.assertEqual(hot_parts[0].color, image_module._HEAT)
        self.assertIsNotNone(hot_parts[0].right)
        index_parts = [
            part
            for line in lines
            for part in line.parts
            if part.text == "01"
        ]
        self.assertEqual(len(index_parts), 1)
        self.assertEqual(index_parts[0].color, image_module._NUM)
        title_parts = [
            part
            for line in lines
            for part in line.parts
            if part.text == "抖音热点"
        ]
        self.assertEqual(len(title_parts), 1)
        self.assertEqual(title_parts[0].color, image_module._INK)
        self.assertFalse(title_parts[0].bold)

    def test_default_limits_match_frozen_contract(self) -> None:
        limits = ImageRenderLimits()
        self.assertEqual(limits.width, 900)
        self.assertEqual(limits.max_height, 8000)
        self.assertEqual(limits.max_image_bytes, 2 * 1024 * 1024)
        self.assertEqual(limits.max_total_bytes, 8 * 1024 * 1024)
        self.assertEqual(limits.max_sections, 5)
        self.assertEqual(self.renderer.limits, limits)

    def test_white_card_palette_matches_poster_spec(self) -> None:
        """白底卡片、1px 外框、圆角外页面底色、顶部莓红强调条。"""
        image = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item("新闻一")])]),
        )[0]
        assert image.png is not None
        with Image.open(io.BytesIO(image.png)).convert("RGB") as opened:
            width, height = opened.size
            pixels = opened.load()
            self.assertEqual(pixels[0, 0], image_module._PAGE_BG)
            self.assertEqual(pixels[0, height // 2], image_module._BORDER)
            self.assertEqual(pixels[20, 20], image_module._WHITE)
            # 强调条是页眉的第一行，落在上内边距之后，不再贴着 1px 外框。
            bar_top = image_module._CARD_BORDER + image_module._HEADER_TOP_PAD
            bar_bottom = bar_top + image_module._ACCENT_BAR_H - 1
            self.assertGreaterEqual(bar_top - image_module._CARD_BORDER, 8)
            self.assertEqual(pixels[60, bar_top - 1], image_module._WHITE)
            for y in range(bar_top, bar_bottom + 1):
                self.assertEqual(pixels[60, y], image_module._ACCENT)
            self.assertEqual(pixels[60, bar_bottom + 1], image_module._WHITE)
            self.assertEqual(pixels[width // 2, 120], image_module._WHITE)

    def test_section_height_counts_every_header_line(self) -> None:
        """页眉强调条必须计入高度：高度只由 _Line 相加，不再有绝对坐标外挂。"""
        data = section("daily_news", "每日新闻", [item("第一条新闻")])
        captured: dict[str, object] = {}
        original = self.renderer._draw

        def spy(header, body, footer, height, width):
            captured["lines"] = (header, body, footer)
            captured["height"] = height
            return original(header, body, footer, height, width)

        with patch.object(self.renderer, "_draw", side_effect=spy):
            image = self.renderer.render(profile([data]))[0]
        assert image.png is not None
        header, body, footer = captured["lines"]
        self.assertTrue(header[0].bar)
        self.assertEqual(
            self.renderer._line_height(header[0]),
            image_module._HEADER_TOP_PAD
            + image_module._ACCENT_BAR_H
            + image_module._ACCENT_BAR_GAP,
        )
        expected = (
            image_module._CARD_BORDER
            + self.renderer._lines_height(header)
            + self.renderer._lines_height(body)
            + self.renderer._lines_height(footer)
            + image_module._FOOTER_BOTTOM_PAD
            + image_module._CARD_BORDER
        )
        self.assertEqual(captured["height"], expected)
        self.assertEqual(image.height, expected)

    def test_index_and_title_share_one_baseline(self) -> None:
        """序号与标题首行共用一条基线，不能一个居中一个基线。"""
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(
            image_module._items(section("daily_news", "每日新闻", [item("第一条")])),
            width,
        )
        first = lines[0]
        index = [part for part in first.parts if part.size == image_module._NUM_SIZE]
        title = [part for part in first.parts if part.size == image_module._TITLE_SIZE]
        self.assertEqual(len(index), 1)
        self.assertEqual(len(title), 1)
        self.assertTrue(index[0].baseline)
        line_h = self.renderer._line_text_height(first)
        half_leading = (line_h - self.renderer._line_content_height(first)) // 2
        max_ascent = max(self.renderer._ascent(part.size) for part in first.parts)
        baselines = {
            half_leading + max_ascent
            if part.baseline
            else (line_h - self.renderer._text_height(part.size)) // 2
            + self.renderer._ascent(part.size)
            for part in first.parts
        }
        self.assertEqual(baselines, {half_leading + max_ascent})

    def test_inline_hot_narrows_only_the_first_title_line(self) -> None:
        """并排热度只挤压标题首行；续行回到整行宽度，右侧不留大片空白。"""
        width = self.renderer.limits.width
        data = section(
            "zhihu_hot", "知乎热榜", [item("超长标题" * 24, hot_value=12345)],
        )
        lines = self.renderer._body_lines(image_module._items(data), width)
        full_text_width = (
            self.renderer._content_width(width)
            - image_module._NUM_COL
            - image_module._NUM_GAP
        )
        first_width = full_text_width - image_module._HEAT_COL - image_module._HEAT_GAP
        widths = [
            self.renderer._text_width(part.text, image_module._TITLE_SIZE)
            for line in lines
            for part in line.parts
            if part.size == image_module._TITLE_SIZE
        ]
        self.assertGreater(len(widths), 2)
        self.assertLessEqual(widths[0], first_width)
        self.assertLessEqual(max(widths[1:]), full_text_width)
        self.assertGreater(max(widths[1:]), first_width)

    def test_row_rhythm_is_compact_and_uniform(self) -> None:
        """单行条目固定 57px（10 上留白 + 36 行高 + 10 下留白 + 1 分隔线）。"""
        row_height = (
            image_module._TITLE_LEADING
            + 2 * image_module._ROW_PAD_Y
            + 1
        )
        self.assertEqual(row_height, 57)
        one = self.renderer.render(
            profile([section("daily_news", "每日新闻", [item("第一条")])]),
        )[0]
        two = self.renderer.render(
            profile([
                section("daily_news", "每日新闻", [item("第一条"), item("第二条")]),
            ]),
        )[0]
        self.assertEqual(two.height - one.height, row_height)
        hot20 = self.renderer.render(
            profile([
                section(
                    "bilibili_hot", "Bilibili 热榜",
                    [item(f"热点{index}", hot_value=index) for index in range(1, 21)],
                ),
            ]),
        )[0]
        hot50 = self.renderer.render(
            profile([
                section(
                    "bilibili_hot", "Bilibili 热榜",
                    [item(f"热点{index}", hot_value=index) for index in range(1, 51)],
                ),
            ]),
        )[0]
        self.assertEqual(hot50.height - hot20.height, 30 * row_height)

    def test_hot_value_sits_inline_when_it_fits(self) -> None:
        data = section(
            "douyin_hot", "抖音热搜", [item("抖音热点", hot_value=12345)],
        )
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(image_module._items(data), width)
        first = lines[0]
        self.assertEqual(len(first.parts), 3)
        number, title, heat = first.parts
        content_width = self.renderer._content_width(width)
        self.assertIsNotNone(heat.right)
        self.assertEqual(heat.right, content_width)
        self.assertEqual(heat.size, image_module._HEAT_SIZE)
        self.assertFalse(title.bold)
        title_end = title.x + self.renderer._text_width(title.text, title.size)
        heat_start = content_width - self.renderer._text_width(
            heat.text, heat.size,
        )
        self.assertLessEqual(title_end + image_module._HEAT_GAP, heat_start + 1)
        self.assertEqual(
            [part.text for part in first.parts],
            ["01", "抖音热点", "热度：12345"],
        )

    def test_heat_column_fits_three_digit_decimal_values(self) -> None:
        # 热度列宽度要覆盖「热度：128.4 万」这类常见小数热度，
        # 否则同一张图里部分行内联、部分落到标题下方，行高忽高忽低。
        hot = "热度：128.4 万"
        self.assertLessEqual(
            self.renderer._text_width(hot, image_module._HEAT_SIZE),
            image_module._HEAT_COL,
        )
        data = section(
            "bilibili_hot", "Bilibili 热榜", [item("标题", hot_value_desc="128.4 万")],
        )
        lines = self.renderer._body_lines(
            image_module._items(data), self.renderer.limits.width,
        )
        self.assertEqual(len(lines[0].parts), 3)
        self.assertEqual(lines[0].parts[2].text, hot)
        self.assertEqual(
            lines[0].parts[2].right,
            self.renderer._content_width(self.renderer.limits.width),
        )

    def test_empty_state_placeholder_hangs_full_width_parens(self) -> None:
        # 全角括号自带左侧空白，空状态首行需要悬挂该空白才与正文左对齐。
        width = self.renderer.limits.width
        lines = self.renderer._body_lines([], width)
        placeholder, note = lines[0].parts[0], lines[1].parts[0]
        inset = self.renderer._ink_inset(placeholder.text, placeholder.size)
        self.assertGreater(inset, 0)
        self.assertEqual(placeholder.x, image_module._NUM_COL + image_module._NUM_GAP - inset)
        self.assertEqual(placeholder.x + inset, note.x)

    def test_single_character_tail_line_is_avoided(self) -> None:
        # 长标题在热度列让位后可能只剩一个字落到末行，需要回带一个字符。
        width = self.renderer.limits.width
        content_width = self.renderer._content_width(width)
        text_x = image_module._NUM_COL + image_module._NUM_GAP
        first_width = content_width - text_x - image_module._HEAT_COL - image_module._HEAT_GAP
        title = (
            "20261117-2026Q4-REVISION-8821-9981-DELIVERY-PIPELINE-"
            "CHECKLIST-AND-ROLLBACK-NOTES 发布"
        )
        wrapped = self.renderer._wrap(
            title, image_module._TITLE_SIZE, content_width - text_x,
            first_width=first_width,
        )
        self.assertGreater(len(wrapped), 1)
        self.assertGreaterEqual(len(wrapped[-1]), 2)
        self.assertEqual("".join(wrapped).replace(" ", ""), title.replace(" ", ""))

    def test_long_hot_value_moves_below_the_title(self) -> None:
        long_hot = "热度：1.2 亿（较昨日上升 3 位，来自站内实时榜单）"
        data = section(
            "bilibili_hot", "Bilibili 热榜",
            [
                item("标题", hot_value_desc=long_hot),
                item("第二条", hot_value=1),
            ],
        )
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(image_module._items(data), width)
        first = lines[0]
        self.assertEqual([part.text for part in first.parts], ["01", "标题"])
        hot_lines = [
            line for line in lines
            if any(part.text == long_hot for part in line.parts)
        ]
        self.assertEqual(len(hot_lines), 1)
        hot_part = hot_lines[0].parts[0]
        self.assertIsNotNone(hot_part.right)
        self.assertEqual(hot_part.right, self.renderer._content_width(width))
        self.assertGreater(lines.index(hot_lines[0]), 0)
        self.assertNotIn(
            long_hot,
            [part.text for part in first.parts],
        )

    def test_hot_value_wider_than_content_is_one_ellipsised_line(self) -> None:
        # 极长热度不再换行：固定单行 + 省略号，避免把该条行高撑高。
        huge = "热度：" + "超长热度描述" * 30
        data = section(
            "douyin_hot", "抖音热搜", [item("热点", hot_value_desc=huge)],
        )
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(image_module._items(data), width)
        hot_parts = [
            part for line in lines for part in line.parts
            if part.size == image_module._HEAT_SIZE
        ]
        self.assertEqual(len(hot_parts), 1)
        self.assertTrue(hot_parts[0].text.startswith("热度："))
        self.assertTrue(hot_parts[0].text.endswith("…"))
        self.assertNotEqual(hot_parts[0].text, huge)
        limit = self.renderer._content_width(width) - (
            image_module._NUM_COL + image_module._NUM_GAP
        )
        self.assertLessEqual(
            self.renderer._text_width(hot_parts[0].text, hot_parts[0].size),
            limit + 1,
        )

    def test_heat_collection_without_any_heat_uses_full_title_width(self) -> None:
        # 整栏都没有热度：标题按整行宽度排版，不预留空白热度列。
        data = section("daily_news", "每日新闻", [item(LONG_TITLE), item("短标题")])
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(image_module._items(data), width)
        text_x = image_module._NUM_COL + image_module._NUM_GAP
        full = self.renderer._content_width(width) - text_x
        expected = len(self.renderer._wrap(LONG_TITLE, image_module._TITLE_SIZE, full))
        title_lines = [line for line in lines if any(p.number for p in line.parts)]
        self.assertEqual(len(title_lines), expected)
        # 没有热度行，所有行都不应出现热度字号。
        self.assertFalse(any(
            part.size == image_module._HEAT_SIZE
            for line in lines for part in line.parts
        ))

    def test_huge_heat_text_is_truncated_quickly_and_cancellable(self) -> None:
        import threading
        import time

        huge = "热度：" + "超长热度描述" * 400
        data = profile([section("douyin_hot", "抖音热搜", [item("热点", hot_value_desc=huge)])])
        started = time.monotonic()
        image = self.renderer.render(data)[0]
        elapsed = time.monotonic() - started
        self.assertEqual(image.status, "success")
        self.assertLess(elapsed, 2.0, f"超长热度截断过慢：{elapsed:.2f}s")

        self.renderer._cancel_event = threading.Event()
        try:
            self.renderer._cancel_event.set()
            with self.assertRaises(image_module.ImageRenderCancelled):
                self.renderer._fit_single_line(huge, image_module._HEAT_SIZE, 400)
        finally:
            self.renderer._cancel_event = None

    def test_long_title_in_body_is_not_truncated(self) -> None:
        data = section("daily_news", "每日新闻", [item(LONG_TITLE)])
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(image_module._items(data), width)
        title_parts = [
            part for line in lines for part in line.parts
            if part.size == image_module._TITLE_SIZE
        ]
        self.assertGreater(len(title_parts), 1)
        self.assertEqual("".join(part.text for part in title_parts), LONG_TITLE)
        self.assertTrue(all(not part.bold for part in title_parts))
        self.assertEqual(
            sum(
                1 for line in lines for part in line.parts if part.text == "01"
            ),
            1,
        )

    def test_long_english_word_wraps_inside_text_column(self) -> None:
        token = "Supercalifragilisticexpialidocious" * 8
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(
            image_module._items(section("daily_news", "每日新闻", [item(token)])),
            width,
        )
        title_parts = [
            part for line in lines for part in line.parts
            if part.size == image_module._TITLE_SIZE
        ]
        self.assertGreater(len(title_parts), 1)
        self.assertEqual("".join(part.text for part in title_parts), token)
        limit = self.renderer._content_width(width) - (
            image_module._NUM_COL + image_module._NUM_GAP
        )
        for part in title_parts:
            self.assertLessEqual(
                self.renderer._text_width(part.text, part.size), limit + 1,
            )

    def test_missing_hot_values_render_without_heat_text(self) -> None:
        data = section(
            "douyin_hot", "抖音热搜",
            [
                item("只有标题"),
                item("空值", hot_value_desc="   ", hot_value=None),
                item("负数", hot_value=-5),
            ],
        )
        width = self.renderer.limits.width
        lines = self.renderer._body_lines(image_module._items(data), width)
        self.assertFalse(
            any(
                part.size == image_module._HEAT_SIZE
                for line in lines for part in line.parts
            ),
        )
        image = self.renderer.render(profile([data]))[0]
        self.assertEqual(image.status, "success")
        self.assertIsNotNone(image.png)

    def test_all_section_numbers_use_the_same_gray(self) -> None:
        """五类栏目共用同一序号颜色：深中性灰 #6b7280，不再按类型分色。"""
        self.assertEqual(image_module._NUM, (107, 114, 128))
        width = self.renderer.limits.width
        news = self.renderer._body_lines(
            image_module._items(
                section("daily_news", "每日新闻", [item("新闻一")]),
            ),
            width,
        )
        hot = self.renderer._body_lines(
            image_module._items(
                section("douyin_hot", "抖音热搜", [item("热点", hot_value=1)]),
            ),
            width,
        )
        news_index = [
            part for line in news for part in line.parts if part.text == "01"
        ]
        hot_index = [
            part for line in hot for part in line.parts if part.text == "01"
        ]
        self.assertEqual(news_index[0].color, image_module._NUM)
        self.assertEqual(hot_index[0].color, image_module._NUM)
        self.assertEqual(news_index[0].color, hot_index[0].color)

    def test_no_ink_outside_content_margins(self) -> None:
        image = self.renderer.render(
            profile([
                section(
                    "zhihu_hot", "知乎热榜",
                    [item(LONG_TITLE, detail=LONG_DETAIL, hot_value_desc="1670 万热度")],
                ),
            ]),
        )[0]
        assert image.png is not None
        with Image.open(io.BytesIO(image.png)).convert("L") as opened:
            width, height = opened.size
            left = opened.crop((1, 1, 40, height - 1)).getextrema()[0]
            right = opened.crop((861, 1, width - 1, height - 1)).getextrema()[0]
        self.assertGreater(left, 200)
        self.assertGreater(right, 200)

    def test_long_label_wraps_and_count_stays_inside(self) -> None:
        label = "很长的栏目名称" * 20
        width = self.renderer.limits.width
        data = section(
            "daily_news", label, [item("新闻一")], displayed_count=1,
        )
        lines = self.renderer._header_lines(data, label, "daily_news", width)
        label_parts = [
            part for line in lines for part in line.parts
            if part.size == image_module._LABEL_SIZE
        ]
        self.assertGreater(len(label_parts), 1)
        self.assertEqual("".join(part.text for part in label_parts), label)
        count_parts = [
            part for line in lines for part in line.parts
            if part.size == image_module._COUNT_SIZE and part.text == "1 条"
        ]
        self.assertEqual(len(count_parts), 1)
        content_width = self.renderer._content_width(width)
        self.assertLessEqual(
            count_parts[0].x
            + self.renderer._text_width(
                count_parts[0].text, image_module._COUNT_SIZE,
            ),
            content_width + 0.5,
        )

    def test_hot_numbers_keep_unified_gray_without_heat_values(self) -> None:
        """Bilibili 适配器没有热度字段，序号仍是统一的深中性灰。"""
        width = self.renderer.limits.width
        data = section(
            "bilibili_hot", "Bilibili 热搜",
            [item("热搜一"), item("热搜二")],
        )
        lines = self.renderer._body_lines(
            image_module._items(data), width,
        )
        index_parts = [
            part for line in lines for part in line.parts
            if part.text in {"01", "02"}
        ]
        self.assertEqual(len(index_parts), 2)
        for part in index_parts:
            self.assertEqual(part.color, image_module._NUM)
        self.assertFalse(
            any(part.size == image_module._HEAT_SIZE for line in lines for part in line.parts),
        )

    def test_daily_news_numbers_stay_neutral_gray(self) -> None:
        width = self.renderer.limits.width
        data = section("daily_news", "每日新闻", [item("新闻一")])
        lines = self.renderer._body_lines(
            image_module._items(data), width,
        )
        index_parts = [
            part for line in lines for part in line.parts if part.text == "01"
        ]
        self.assertEqual(index_parts[0].color, image_module._NUM)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(has_font(), "no CJK font available")
class OverflowReflowAndHeatTest(unittest.TestCase):
    """超限摘要有界缩略、热度按整栏统一布局。"""

    def setUp(self) -> None:
        self.renderer = ProfileImageRenderer()

    def _zhihu(self, detail_repeat: int, heat: int, count: int = 50) -> dict:
        items = [
            {
                "title": f"知乎问题第{index}条：如何评价当前的行业趋势",
                "detail": "一段较长的摘要内容，" * detail_repeat,
                "hot_value": heat if index % 2 else heat + 1,
            }
            for index in range(1, count + 1)
        ]
        return profile([section("zhihu_hot", "知乎热榜", items)])

    def test_long_summaries_are_reflowed_not_dropped(self) -> None:
        data = self._zhihu(detail_repeat=18, heat=12_345_678)
        for style in ("default", "mist", "floral", "bold_studio"):
            with self.subTest(style=style):
                image = self.renderer.render({**data, "image_style": style})[0]
                self.assertEqual(image.status, "success")
                self.assertIsNotNone(image.png)
                self.assertLessEqual(image.height, 8000)
                self.assertLessEqual(len(image.png), 2 * 1024 * 1024)
                # 标题、条数与顺序都保留，只缩略摘要。
                self.assertEqual(len(image_module._items(
                    data["sections"][0],
                )), 50)
                self.assertEqual(image.note, "部分摘要已缩略")

    def test_unshrinkable_overflow_still_fails_clearly(self) -> None:
        items = [item(f"标题 {index}") for index in range(1, 201)]
        data = profile([section("bilibili_hot", "Bilibili 热搜", items)])
        image = self.renderer.render(data)[0]
        self.assertEqual(image.status, "failed")
        self.assertEqual(image.error, "section_too_tall")
        self.assertIsNone(image.note)
