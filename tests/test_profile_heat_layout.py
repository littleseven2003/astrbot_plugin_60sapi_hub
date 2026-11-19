"""热榜热度排版：整栏统一布局，缺值与超长文本不改变行高。"""

from __future__ import annotations

import unittest

from core.profile_image import FontUnavailableError, ProfileImageRenderer, find_cjk_font


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


@unittest.skipUnless(has_font(), "no CJK font available")
class HeatLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = ProfileImageRenderer()

    def test_mixed_digits_missing_and_long_heat_keep_one_row_height(self) -> None:
        # 8 位、7 位、缺热度、超长描述与超大数字混排：整栏统一单独一行。
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
          "相邻条目高度应一致：" + str(sorted(set(groups))))

        lines = self.renderer._body_lines(items, self.renderer.limits.width)
        long_lines = [
            line for line in lines
            if line.parts and line.parts[0].text.startswith("热度：九九")
        ]
        self.assertEqual(len(long_lines), 1)
        self.assertTrue(long_lines[0].parts[0].text.endswith("…"),
          "极长热度应单行省略：" + long_lines[0].parts[0].text)

    def test_short_heat_with_missing_value_keeps_inline_column(self) -> None:
        items = [
            {"title": f"标题{index}", "hot_value": 100 + index}
            for index in range(1, 10)
        ] + [{"title": "没有热度"}]
        groups = entry_heights(self.renderer, items)
        self.assertEqual(len(groups), len(items))
        self.assertLessEqual(max(groups) - min(groups), 1,
          "缺热度条目也要占同样的首行热度列：" + str(sorted(set(groups))))
        title_lines = [
            line for line in self.renderer._body_lines(
                items, self.renderer.limits.width,
            )
            if any(part.number for part in line.parts)
        ]
        self.assertEqual(len(title_lines), 10)


if __name__ == "__main__":
    unittest.main()
