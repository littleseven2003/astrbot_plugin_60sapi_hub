"""热榜热度排版：无摘要栏目热度同行，带摘要栏目保留展开布局。"""

from __future__ import annotations

import unittest

from core.profile_image import (
    FontUnavailableError,
    ProfileImageRenderer,
    find_cjk_font,
    get_style,
)


def has_font() -> bool:
    try:
        find_cjk_font()
        return True
    except FontUnavailableError:
        return False


def entry_heights(renderer: ProfileImageRenderer, items: list[dict]) -> list[int]:
    """按序号行分组，返回每条条目的实际像素高度（含分隔与留白）。"""
    lines = renderer._body_lines(items, renderer.limits.width)
    groups: list[int] = []
    current = 0
    for line in lines:
        if any(part.number for part in line.parts) and current:
            groups.append(current)
            current = 0
        current += renderer._line_height(line)
    if current:
        groups.append(current)
    return groups


def title_lines(renderer: ProfileImageRenderer, items: list[dict]):
    return [
        line
        for line in renderer._body_lines(items, renderer.limits.width)
        if any(part.number for part in line.parts)
    ]


@unittest.skipUnless(has_font(), "no CJK font available")
class HeatLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = ProfileImageRenderer()

    def test_no_detail_items_keep_heat_inline_with_one_row_each(self) -> None:
        # 抖音/头条没有摘要：七八位原始整数热度也必须与标题同行，
        # 每条只占一行，不能因为超过固定列宽把整栏切成两行。
        items: list[dict] = []
        for index in range(1, 21):
            items.append({"title": f"标题{index}", "hot_value": 99_999_999})
        for index in range(21, 41):
            items.append({"title": f"标题{index}", "hot_value": 999_999})
        items.append({"title": "缺热度"})
        items.append({"title": "很长的热度描述", "hot_value_desc": "热度：" + "九" * 60})
        items.append({"title": "超大数字", "hot_value": 10**15})
        groups = entry_heights(self.renderer, items)
        self.assertEqual(len(groups), len(items))
        self.assertLessEqual(max(groups) - min(groups), 1,
          "无摘要栏目的条目高度应一致：" + str(sorted(set(groups))))

        lines = self.renderer._body_lines(items, self.renderer.limits.width)
        # 每条只有一个标题行，热度不会再单独占行。
        self.assertEqual(len(title_lines(self.renderer, items)), len(items))
        self.assertFalse([
            line for line in lines
            if line.parts and not line.parts[0].number
            and "热度" in line.parts[0].text
        ], "无摘要栏目不应出现独立热度行")
        # 七八位热度完整显示，极长热度限宽单行省略且仍在标题行内。
        first = title_lines(self.renderer, items)[0]
        self.assertEqual(first.parts[-1].text, "热度：99999999")
        long_line = [
            line for line in lines
            if line.parts and any(
                part.text.startswith("热度：九") for part in line.parts
            )
        ]
        self.assertEqual(len(long_line), 1)
        long_part = [
            part for part in long_line[0].parts
            if part.text.startswith("热度：九")
        ][0]
        self.assertTrue(long_part.text.endswith("…"),
          "极长热度应单行省略：" + long_part.text)
        self.assertEqual(len(long_line[0].parts), 3, "省略后仍应在标题行内")
        self.assertLessEqual(
            self.renderer._text_width(long_part.text, long_part.size), 180,
        )

    def test_heat_column_is_bounded_by_measured_width(self) -> None:
        content_width = self.renderer._content_width(self.renderer.limits.width)
        items = [
            {"title": f"标题{index}", "hot_value": 12345678}
            for index in range(1, 6)
        ]
        line = title_lines(self.renderer, items)[0]
        heat = line.parts[-1]
        self.assertEqual(heat.text, "热度：12345678")
        title = line.parts[1]
        gap = image_module_heat_gap()
        self.assertLessEqual(
            title.x + self.renderer._text_width(title.text, title.size) + gap,
            heat.right - self.renderer._text_width(heat.text, heat.size) + 1,
        )
        # 列宽本身不超过 180px，也不超过正文宽度四分之一。
        self.assertLessEqual(
            self.renderer._text_width(heat.text, heat.size), 180,
        )
        self.assertLessEqual(180, content_width // 4 + 1)

    def test_first_line_never_overlaps_heat_with_trailing_punct(self) -> None:
        # 首行右侧有热度列时，收尾标点不能悬挂进热度列；放不下的标题内容
        # 续到下一行，且不丢字、不让收尾标点落到次行行首。
        titles = [
            "202611201234567890123456789012345678？？？",
            "字" * 24 + "（续）？？？",
        ]
        for style_name in ("default", "mist", "floral", "bold_studio"):
            self.renderer._style = get_style(style_name)
            for title in titles:
                items = [{"title": title, "hot_value": 87654321}]
                lines = self.renderer._body_lines(items, self.renderer.limits.width)
                first = [
                    line for line in lines
                    if any(part.number for part in line.parts)
                ][0]
                heat = first.parts[-1]
                title_part = first.parts[1]
                content_width = self.renderer._content_width(
                    self.renderer.limits.width,
                )
                heat_start = content_width - self.renderer._text_width(
                    heat.text, heat.size,
                )
                title_end = title_part.x + self.renderer._text_width(
                    title_part.text, title_part.size,
                )
                self.assertLessEqual(
                    title_end, heat_start,
                    f"{style_name} 首行标题压到热度列：{title_end} > {heat_start}",
                )
                # 用真实字形墨迹再确认一次：标题最后一笔必须在热度第一笔左侧。
                title_box = self.renderer._font(
                    title_part.size,
                ).getmask(title_part.text).getbbox()
                heat_box = self.renderer._font(
                    heat.size,
                ).getmask(heat.text).getbbox()
                self.assertIsNotNone(title_box)
                self.assertIsNotNone(heat_box)
                title_ink_right = title_part.x + title_box[2]
                heat_left = heat_start + heat_box[0]
                self.assertLessEqual(
                    title_ink_right, heat_left,
                    f"{style_name} 标题字形与热度重叠：{title_ink_right} > {heat_left}",
                )
                joined = "".join(
                    part.text
                    for line in lines
                    for part in line.parts
                    if part.size == self.renderer._style.title_size
                )
                self.assertEqual(joined, title, f"{style_name} 标题文字被截断")
                self.assertNotIn(
                    lines[1].parts[0].text[0],
                    "，。、；：！？）》】”’…",
                    f"{style_name} 收尾标点落到次行行首",
                )

    def test_common_single_line_title_stays_compact(self) -> None:
        for style_name in ("default", "mist", "floral", "bold_studio"):
            self.renderer._style = get_style(style_name)
            items = [{"title": "普通标题", "hot_value": 87654321}]
            lines = self.renderer._body_lines(items, self.renderer.limits.width)
            numbered = [
                line for line in lines
                if any(part.number for part in line.parts)
            ]
            self.assertEqual(len(numbered), 1)
            self.assertEqual(numbered[0].parts[1].text, "普通标题")
            self.assertEqual(numbered[0].parts[-1].text, "热度：87654321")

    def test_detail_sections_keep_expanded_heat_row(self) -> None:
        # 知乎等带摘要栏目沿用原布局：热度超过 124px 时整栏独立一行。
        items = [
            {"title": f"话题{index}", "detail": "摘要文本", "hot_value": 99_999_999}
            for index in range(1, 6)
        ]
        lines = self.renderer._body_lines(items, self.renderer.limits.width)
        heat_only = [
            line for line in lines
            if line.parts and "热度" in line.parts[0].text
            and not line.parts[0].number
        ]
        self.assertEqual(len(heat_only), len(items))
        for line in heat_only:
            self.assertEqual(len(line.parts), 1)
            self.assertEqual(line.parts[0].right, self.renderer._content_width(
                self.renderer.limits.width,
            ))
        self.assertEqual(
            [line.parts[-1].text for line in title_lines(self.renderer, items)],
            [f"话题{index}" for index in range(1, 6)],
        )

    def test_short_heat_with_missing_value_keeps_inline_column(self) -> None:
        items = [
            {"title": f"标题{index}", "hot_value": 100 + index}
            for index in range(1, 10)
        ] + [{"title": "没有热度"}]
        groups = entry_heights(self.renderer, items)
        self.assertEqual(len(groups), len(items))
        self.assertLessEqual(max(groups) - min(groups), 1,
          "缺热度条目也要占同样的首行热度列：" + str(sorted(set(groups))))
        self.assertEqual(len(title_lines(self.renderer, items)), 10)

    def test_no_detail_long_title_wraps_but_heat_stays_on_first_line(self) -> None:
        items = [{"title": "超长标题" * 20, "hot_value": 987654321}]
        lines = self.renderer._body_lines(items, self.renderer.limits.width)
        numbered = [line for line in lines if any(part.number for part in line.parts)]
        self.assertEqual(len(numbered), 1)
        title_only = [
            line for line in lines
            if line.parts and all(not part.number for part in line.parts)
            and any(part.size == image_title_size() for part in line.parts)
        ]
        self.assertGreaterEqual(len(title_only), 2)
        self.assertTrue(any("热度" in part.text for part in numbered[0].parts))
        for line in title_only:
            self.assertFalse(any("热度" in part.text for part in line.parts))

    def test_section_without_any_heat_uses_full_title_width(self) -> None:
        width = self.renderer.limits.width
        full = (
            self.renderer._content_width(width)
            - num_col() - num_gap()
        )
        items = [{"title": "字" * 40}]
        first = title_lines(self.renderer, items)[0]
        title = first.parts[1]
        self.assertGreater(self.renderer._text_width(title.text, title.size), full - 40)
        self.assertEqual(len(first.parts), 2)


def image_module_heat_gap() -> int:
    from core import profile_image

    return profile_image._HEAT_GAP


def num_col() -> int:
    from core import profile_image

    return profile_image._NUM_COL


def image_title_size() -> int:
    from core import profile_image

    return profile_image._TITLE_SIZE


def num_gap() -> int:
    from core import profile_image

    return profile_image._NUM_GAP


if __name__ == "__main__":
    unittest.main()
